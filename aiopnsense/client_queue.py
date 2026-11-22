"""Request queue helpers for OPNsenseClient."""

import asyncio
from collections.abc import MutableMapping
from dataclasses import dataclass
import sys
from typing import TYPE_CHECKING, Any, Literal, cast

from ._endpoint_outcome import EndpointOutcome
from ._error_policy import _attempt
from .const import QUEUE_REQUEST_INTERVAL_SECONDS
from .exceptions import OPNsenseError, _map_opnsense_exception
from .helpers import _LOGGER, _is_request_error_logged, _mark_request_error_logged


@dataclass(slots=True)
class _CancellablePostState:
    """Track whether an opted-in queued POST has started dispatching."""

    waiter: asyncio.Future[Any]
    started: bool = False


class ClientQueueMixin:
    """Request queue and background task methods for OPNsenseClient."""

    if TYPE_CHECKING:
        _loop: asyncio.AbstractEventLoop | None
        _max_workers: int
        _request_queue: asyncio.Queue[Any]
        _workers: list[asyncio.Task[Any]]
        _active_close_count: int

        def _signal_toggle_shutdown(self) -> None:
            """Invalidate in-flight VPN Toggles because the client is closing."""
            ...

        async def _do_get(
            self,
            path: str,
            caller: str = "Unknown",
            timeout_seconds: float | None = None,
        ) -> MutableMapping[str, Any] | list | None:
            """Execute an immediate GET transport request after it is dequeued.

            Args:
                path (str): API endpoint path to request.
                caller (str): Calling method name used for diagnostics.
                timeout_seconds (float | None): Optional transport request timeout.

            Returns:
                MutableMapping[str, Any] | list | None: Decoded transport response.
            """
            ...

        async def _do_get_outcome(
            self,
            path: str,
            caller: str = "Unknown",
            *,
            read_body: bool = True,
            response_format: Literal["json", "text"] = "json",
        ) -> EndpointOutcome:
            """Execute an immediate GET request and classify the response.

            Args:
                path (str): API endpoint path to request.
                caller (str): Calling method name used for diagnostics.
                read_body (bool): Whether to decode a successful response body.
                response_format (Literal["json", "text"], optional): Decoding mode for
                    a successful response body.

            Returns:
                EndpointOutcome: Classified transport response.
            """
            ...

        async def _do_post_outcome(
            self,
            path: str,
            payload: MutableMapping[str, Any] | None = None,
            caller: str = "Unknown",
        ) -> EndpointOutcome:
            """Execute an immediate POST request and classify the response.

            Args:
                path (str): API endpoint path to request.
                payload (MutableMapping[str, Any] | None): Request body.
                caller (str): Calling method name used for diagnostics.

            Returns:
                EndpointOutcome: Classified transport response.
            """
            ...

        async def _do_get_from_stream(
            self,
            path: str,
            caller: str = "Unknown",
        ) -> dict[str, Any]:
            """Execute an immediate streaming GET transport request after dequeuing it.

            Args:
                path (str): Streaming API endpoint path to request.
                caller (str): Calling method name used for diagnostics.

            Returns:
                dict[str, Any]: Decoded payload extracted from the stream response.
            """
            ...

        async def _do_post(
            self,
            path: str,
            payload: MutableMapping[str, Any] | None = None,
            caller: str = "Unknown",
        ) -> MutableMapping[str, Any] | list | None:
            """Execute an immediate POST transport request after it is dequeued.

            Args:
                path (str): API endpoint path to request.
                payload (MutableMapping[str, Any] | None): Optional request body.
                caller (str): Calling method name used for diagnostics.

            Returns:
                MutableMapping[str, Any] | list | None: Decoded transport response,
                    if available.
            """
            ...

    async def _ensure_workers_started(self) -> None:
        """Ensure queue workers are running on the active event loop.

        Raises:
            asyncio.CancelledError: If the client is closing.
        """
        if self._active_close_count > 0:
            raise asyncio.CancelledError("OPNsenseClient is closing")
        self._loop = asyncio.get_running_loop()

        self._workers = [worker for worker in self._workers if not worker.done()]
        while len(self._workers) < self._max_workers:
            self._workers.append(asyncio.create_task(self._process_queue()))

    async def _get_active_loop(self) -> asyncio.AbstractEventLoop:
        """Ensure workers are started and return the active event loop.

        Returns:
            asyncio.AbstractEventLoop: Running event loop used to create
                queued request futures.

        Raises:
            OPNsenseError: Raised when worker startup does not initialize an event
                loop.
        """
        await self._ensure_workers_started()
        if self._loop is None:
            raise OPNsenseError("Event loop is not initialized")
        return self._loop

    @staticmethod
    def _get_caller_name() -> str:
        """Return the public caller above the queue wrapper.

        Returns:
            str: Function name used for diagnostics, or ``"Unknown"`` when frame
            inspection is unavailable.
        """
        try:
            frame = sys._getframe(3)
            if frame.f_code is _attempt.__code__:
                frame = frame.f_back or frame
            return frame.f_code.co_name
        except ValueError, AttributeError:
            return "Unknown"

    async def _queue_request(
        self,
        method: str,
        path: str,
        payload: MutableMapping[str, Any] | None = None,
    ) -> Any:
        """Queue one API request and return its future result.

        Cancelling the caller before dispatch causes the worker to skip a queued POST.

        Args:
            method (str): Internal request method handled by ``_process_queue``.
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None, optional): Request payload
                sent with POST requests.

        Returns:
            Any: Result set by the queue processor.
        """
        loop = await self._get_active_loop()
        future = loop.create_future()
        await self._request_queue.put((method, path, payload, future, self._get_caller_name()))
        return await future

    async def _queue_post_with_cancellation(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> tuple[MutableMapping[str, Any] | list | None, asyncio.CancelledError | None]:
        """Queue a POST while retaining its result if cancellation follows dispatch.

        Args:
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None): Optional request body.

        Returns:
            tuple[MutableMapping[str, Any] | list | None, asyncio.CancelledError | None]:
                The response and any original caller cancellation received after
                dispatch began. Callers process the response before propagating
                the returned cancellation.

        Raises:
            asyncio.CancelledError: Raised when cancellation occurs before the
                queued POST begins dispatching.
            cancelled: The original ``asyncio.CancelledError`` from caller
                cancellation, re-raised if the dispatched POST fails or its
                future is cancelled before a response is available.
        """
        loop = await self._get_active_loop()
        future = loop.create_future()
        waiter = asyncio.shield(future)
        cancellation_state = _CancellablePostState(waiter)
        await self._request_queue.put(
            (
                "post",
                path,
                payload,
                future,
                self._get_caller_name(),
                cancellation_state,
            )
        )
        try:
            return await waiter, None
        except asyncio.CancelledError as cancelled:
            if not cancellation_state.started:
                future.cancel()
                raise
            while True:
                try:
                    return await asyncio.shield(future), cancelled
                except asyncio.CancelledError:
                    if future.cancelled():
                        raise cancelled
                except OPNsenseError:
                    raise cancelled from None

    async def _get_from_stream(self, path: str) -> dict[str, Any]:
        """Queue a streaming GET request and return the parsed payload.

        Args:
            path (str): API endpoint path to request.

        Returns:
            dict[str, Any]: Decoded payload extracted from the streaming API response.
        """
        return cast(dict[str, Any], await self._queue_request("get_from_stream", path))

    async def _get(self, path: str) -> MutableMapping[str, Any] | list | None:
        """Queue a GET request and return the result.

        Args:
            path (str): API endpoint path to request.

        Returns:
            MutableMapping[str, Any] | list | None: Decoded response payload returned by the GET request.
        """
        return await self._queue_request("get", path)

    async def _post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> MutableMapping[str, Any] | list | None:
        """Queue a POST request and return the result.

        Args:
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None, optional): Request payload sent to the API endpoint.

        Returns:
            MutableMapping[str, Any] | list | None: Decoded response payload returned by the POST request.
        """
        return await self._queue_request("post", path, payload)

    async def _process_queue(self) -> None:
        """Continuously process queued API requests and resolve waiting futures.

        Raises:
            asyncio.CancelledError: Raised when the queue processor is cancelled.
        """
        while True:
            method: str | None = None
            path: str | None = None
            payload: dict[str, Any] | None = None
            future: asyncio.Future[Any] | None = None
            cancellation_state: _CancellablePostState | None = None
            caller = "Unknown"
            try:
                entry = await self._request_queue.get()
                if len(entry) == 6:
                    method, path, payload, future, caller, cancellation_state = entry
                else:
                    method, path, payload, future, caller = entry
                if method in {"post", "get_outcome", "get_text_outcome", "post_outcome"} and (
                    future is not None and future.cancelled()
                ):
                    continue
                if cancellation_state is not None:
                    waiter = cancellation_state.waiter
                    if future is not None and waiter.cancelled():
                        if not future.done():
                            future.cancel()
                        continue
                    cancellation_state.started = True
                if method == "get_from_stream":
                    result: Any = await self._do_get_from_stream(path, caller)
                    if future is not None and not future.done():
                        future.set_result(result)
                elif method == "get":
                    result = await self._do_get(path, caller)
                    if future is not None and not future.done():
                        future.set_result(result)
                elif method == "get_outcome":
                    result = await self._do_get_outcome(path, caller)
                    if future is not None and not future.done():
                        future.set_result(result)
                elif method == "get_text_outcome":
                    result = await self._do_get_outcome(path, caller, response_format="text")
                    if future is not None and not future.done():
                        future.set_result(result)
                elif method == "post":
                    result = await self._do_post(path, payload, caller)
                    if future is not None and not future.done():
                        future.set_result(result)
                elif method == "post_outcome":
                    result = await self._do_post_outcome(path, payload, caller)
                    if future is not None and not future.done():
                        future.set_result(result)
                else:
                    _LOGGER.error("Unknown method to add to Queue: %s", method)
                    if future is not None and not future.done():
                        future.set_exception(
                            OPNsenseError(f"Unknown method to add to Queue: {method}")
                        )
            except asyncio.CancelledError:
                _LOGGER.debug("Request queue processor cancelled (called by %s)", caller)
                if future is not None and not future.done():
                    future.cancel()
                raise
            except Exception as e:  # noqa: BLE001
                if not _is_request_error_logged(e):
                    _LOGGER.error(
                        "Exception in request queue processor (called by %s). %s: %s",
                        caller,
                        type(e).__name__,
                        e,
                    )
                if future is not None and not future.done():
                    mapped_error = _map_opnsense_exception(e)
                    if mapped_error is not e:
                        mapped_error.__cause__ = e
                    if _is_request_error_logged(e):
                        _mark_request_error_logged(mapped_error)
                    future.set_exception(mapped_error)
            await asyncio.sleep(QUEUE_REQUEST_INTERVAL_SECONDS)

    async def async_close(self) -> None:
        """Cancel background work, invalidate VPN waiters, and clear the queue.

        Queue calls are cancelled while the client is closing. Closing also
        invalidates in-progress VPN calls so they cannot send follow-up writes.
        """
        _LOGGER.debug("Closing OPNsenseClient and cancelling background tasks")
        self._signal_toggle_shutdown()
        self._active_close_count += 1
        try:
            tasks_to_cancel = []

            if self._workers:
                for worker in self._workers:
                    if not worker.done():
                        worker.cancel()
                    tasks_to_cancel.append(worker)

            if tasks_to_cancel:
                try:
                    await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
                    _LOGGER.debug("All background tasks cancelled successfully")
                except Exception as e:  # noqa: BLE001
                    _LOGGER.warning(
                        "Error during background task cancellation. %s: %s", type(e).__name__, e
                    )

        finally:
            try:
                while not self._request_queue.empty():
                    try:
                        entry = self._request_queue.get_nowait()
                        future = entry[3]
                        if future is not None and not future.done():
                            future.set_exception(
                                asyncio.CancelledError("OPNsenseClient is closing")
                            )
                    except asyncio.QueueEmpty:
                        break
                self._workers = []
                self._loop = None
                _LOGGER.debug("Request queue cleared")
            finally:
                self._active_close_count -= 1
