"""Request queue helpers for OPNsenseClient."""

import asyncio
from collections.abc import MutableMapping
import inspect
from typing import TYPE_CHECKING, Any, cast

from .helpers import _LOGGER


class ClientQueueMixin:
    """Request queue and background task methods for OPNsenseClient."""

    if TYPE_CHECKING:
        _loop: asyncio.AbstractEventLoop | None
        _max_workers: int
        _queue_monitor: asyncio.Task[Any] | None
        _request_queue: asyncio.Queue[Any]
        _workers: list[asyncio.Task[Any]]

        async def _do_get(
            self,
            path: str,
            caller: str = "Unknown",
            timeout_seconds: float | None = None,
        ) -> MutableMapping[str, Any] | list | None:
            """Execute a queued GET request."""
            ...

        async def _do_get_from_stream(
            self,
            path: str,
            caller: str = "Unknown",
        ) -> dict[str, Any]:
            """Execute a queued streaming GET request."""
            ...

        async def _do_post(
            self,
            path: str,
            payload: MutableMapping[str, Any] | None = None,
            caller: str = "Unknown",
        ) -> MutableMapping[str, Any] | list | None:
            """Execute a queued POST request."""
            ...

    async def _ensure_workers_started(self) -> None:
        """Ensure queue workers are running on the active event loop."""
        self._loop = asyncio.get_running_loop()

        if self._queue_monitor is None or self._queue_monitor.done():
            self._queue_monitor = asyncio.create_task(self._monitor_queue())

        self._workers = [worker for worker in self._workers if not worker.done()]
        while len(self._workers) < self._max_workers:
            self._workers.append(asyncio.create_task(self._process_queue()))

    async def _get_active_loop(self) -> asyncio.AbstractEventLoop:
        """Ensure workers are started and return the active event loop.

        Returns:
            asyncio.AbstractEventLoop: Value produced by this method.
        """
        await self._ensure_workers_started()
        if self._loop is None:
            raise RuntimeError("Event loop is not initialized")
        return self._loop

    @staticmethod
    def _get_caller_name() -> str:
        """Return the public caller above the queue wrapper.

        Returns:
            str: Function name used for diagnostics, or ``"Unknown"`` when stack
            inspection is unavailable.
        """
        try:
            return inspect.stack()[3].function
        except IndexError, AttributeError:
            return "Unknown"

    async def _queue_request(
        self,
        method: str,
        path: str,
        payload: MutableMapping[str, Any] | None = None,
    ) -> Any:
        """Queue one API request and return its future result.

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
        """Continuously process queued API requests and resolve waiting futures."""
        while True:
            method: str | None = None
            path: str | None = None
            payload: dict[str, Any] | None = None
            future: asyncio.Future[Any] | None = None
            caller = "Unknown"
            try:
                method, path, payload, future, caller = await self._request_queue.get()
                if method == "get_from_stream":
                    result: Any = await self._do_get_from_stream(path, caller)
                    if future is not None and not future.done():
                        future.set_result(result)
                elif method == "get":
                    result = await self._do_get(path, caller)
                    if future is not None and not future.done():
                        future.set_result(result)
                elif method == "post":
                    result = await self._do_post(path, payload, caller)
                    if future is not None and not future.done():
                        future.set_result(result)
                else:
                    _LOGGER.error("Unknown method to add to Queue: %s", method)
                    if future is not None and not future.done():
                        future.set_exception(
                            RuntimeError(f"Unknown method to add to Queue: {method}")
                        )
            except asyncio.CancelledError:
                _LOGGER.debug("Request queue processor cancelled (called by %s)", caller)
                if future is not None and not future.done():
                    future.cancel()
                raise
            except Exception as e:  # noqa: BLE001
                _LOGGER.error(
                    "Exception in request queue processor (called by %s). %s: %s",
                    caller,
                    type(e).__name__,
                    e,
                )
                if future is not None and not future.done():
                    future.set_exception(e)
            await asyncio.sleep(0.3)

    async def _monitor_queue(self) -> None:
        """Periodically log request queue backlog size for diagnostics."""
        while True:
            try:
                queue_size = self._request_queue.qsize()
                if queue_size > 0:
                    _LOGGER.debug("OPNsense API queue backlog: %d tasks", queue_size)
            except Exception as e:  # noqa: BLE001
                _LOGGER.error("Error monitoring queue size. %s: %s", type(e).__name__, e)
            await asyncio.sleep(10)

    async def async_close(self) -> None:
        """Cancel all running background tasks and clear the request queue."""
        _LOGGER.debug("Closing OPNsenseClient and cancelling background tasks")

        tasks_to_cancel = []

        if self._queue_monitor and not self._queue_monitor.done():
            self._queue_monitor.cancel()
            tasks_to_cancel.append(self._queue_monitor)

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

        while not self._request_queue.empty():
            try:
                _method, _path, _payload, future, _caller = self._request_queue.get_nowait()
                if future is not None and not future.done():
                    future.set_exception(asyncio.CancelledError("OPNsenseClient is closing"))
            except asyncio.QueueEmpty:
                break
        self._queue_monitor = None
        self._workers = []
        self._loop = None
        _LOGGER.debug("Request queue cleared")
