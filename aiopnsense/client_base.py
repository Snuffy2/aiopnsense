"""Core transport and queue plumbing for OPNsenseClient."""

import asyncio
from collections.abc import MutableMapping
from datetime import datetime
import inspect
import json
from typing import Any, overload
from urllib.parse import urlparse
import warnings

import aiohttp

from .const import DEFAULT_CACHE_TTL_SECONDS, DEFAULT_REQUEST_TIMEOUT_SECONDS
from .helpers import _LOGGER

_UNSET: object = object()


class ClientBaseMixin:
    """ClientBase methods for OPNsenseClient."""

    @staticmethod
    def _validate_throw_error_flag(
        value: bool | object,
        parameter_name: str,
    ) -> bool:
        """Validate required ``throw_errors``-style arguments.

        Args:
            value (bool | object): Candidate value to validate.
            parameter_name (str): Parameter name used in the error message.

        Returns:
            bool: Validated value.

        Raises:
            TypeError: Raised when ``value`` is not an allowed type.
        """
        if isinstance(value, bool):
            return value
        raise TypeError(f"`{parameter_name}` must be a bool.")

    @staticmethod
    @overload
    def _validate_optional_throw_error_flag(
        value: None,
        parameter_name: str,
    ) -> None:
        """Type overload for optional ``throw_errors``-style arguments set to ``None``."""
        ...

    @staticmethod
    @overload
    def _validate_optional_throw_error_flag(
        value: bool,
        parameter_name: str,
    ) -> bool:
        """Type overload for optional ``throw_errors``-style arguments set to ``bool``."""
        ...

    @staticmethod
    def _validate_optional_throw_error_flag(
        value: bool | None,
        parameter_name: str,
    ) -> bool | None:
        """Validate optional ``throw_errors``-style arguments.

        Args:
            value (bool | None): Candidate value to validate.
            parameter_name (str): Parameter name used in the error message.

        Returns:
            bool | None: Validated value.

        Raises:
            TypeError: Raised when ``value`` is not an allowed type.
        """
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        raise TypeError(f"`{parameter_name}` must be a bool or None.")

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        opts: MutableMapping[str, Any] | None = None,
        initial: bool | object = _UNSET,
        name: str = "OPNsense",
        *,
        throw_errors: bool | object = _UNSET,
    ) -> None:
        """Initialize the OPNsense client.

        Args:
            url (str): Base URL of the OPNsense instance.
            username (str): Username for API authentication.
            password (str): Password for API authentication.
            session (aiohttp.ClientSession): HTTP client session used for API requests.
            opts (MutableMapping[str, Any] | None, optional): Optional client configuration values.
            initial (bool | object): Deprecated alias for ``throw_errors``. When provided,
                a ``DeprecationWarning`` is emitted. Ignored when ``throw_errors`` is also set.
            throw_errors (bool | object): Whether request and decorator errors should be
                re-raised instead of logged and suppressed. Defaults to ``False``.
            name (str): Display name for the client instance.

        Raises:
            TypeError: Raised when ``initial`` or ``throw_errors`` is not a ``bool``.
        """

        self._username: str = username
        self._password: str = password
        self._name: str = name

        self._opts: dict[str, Any] = dict(opts or {})
        self._verify_ssl: bool = self._opts.get("verify_ssl", True)
        parts = urlparse(url.rstrip("/"))
        self._url: str = f"{parts.scheme}://{parts.netloc}"
        self._session: aiohttp.ClientSession = session
        self._throw_errors: bool = False
        if throw_errors is not _UNSET:
            validated_throw_errors = self._validate_throw_error_flag(
                throw_errors,
                "throw_errors",
            )
            self._throw_errors = validated_throw_errors
        if initial is not _UNSET:
            validated_initial = self._validate_throw_error_flag(initial, "initial")
            warnings.warn(
                "In OPNsenseClient, `initial` is deprecated and will be removed in a future release. "
                "Use `throw_errors` instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            if throw_errors is _UNSET:
                self._throw_errors = validated_initial
        self._firmware_version: str | None = None
        self._endpoint_availability: dict[str, bool] = {}
        self._endpoint_checked_at: dict[str, datetime] = {}
        self._endpoint_cache_ttl_seconds = DEFAULT_CACHE_TTL_SECONDS
        self._rest_api_query_count = 0
        self._request_queue: asyncio.Queue = asyncio.Queue()
        self._queue_monitor: asyncio.Task[Any] | None = None
        self._workers: list[asyncio.Task[Any]] = []
        # Number of parallel workers to process the queue
        self._max_workers = 2
        # Don't use directly. Use await self._get_active_loop() instead
        self._loop: asyncio.AbstractEventLoop | None = None

    def toggle_throwing_errors(self, throw_errors: bool | None = None) -> bool:
        """Set or toggle request error propagation.

        Args:
            throw_errors (bool | None): Explicit error propagation state. When ``None``,
                the current state is inverted.

        Returns:
            bool: Updated error propagation state.

        Raises:
            TypeError: Raised when ``throw_errors`` is not a ``bool`` or ``None``.
        """
        if throw_errors is None:
            self._throw_errors = not self._throw_errors
        else:
            validated_throw_errors = self._validate_optional_throw_error_flag(
                throw_errors,
                "throw_errors",
            )
            self._throw_errors = validated_throw_errors
        return self._throw_errors

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

    @property
    def name(self) -> str:
        """Return the name of the client.

        Returns:
            str: The name of the client.
        """
        return self._name

    async def reset_query_counts(self) -> None:
        """Reset API query counters to zero."""
        self._rest_api_query_count = 0

    async def get_query_counts(self) -> int:
        """Return current API query counts.

        Returns:
            int: Current number of API queries performed by the client.
        """
        return self._rest_api_query_count

    async def _get_from_stream(self, path: str) -> dict[str, Any]:
        """Queue a streaming GET request and return the parsed payload.

        Args:
            path (str): API endpoint path to request.

        Returns:
            dict[str, Any]: Decoded payload extracted from the streaming API response.
        """
        loop = await self._get_active_loop()
        try:
            caller = inspect.stack()[1].function
        except IndexError, AttributeError:
            caller = "Unknown"
        future = loop.create_future()
        await self._request_queue.put(("get_from_stream", path, None, future, caller))
        return await future

    async def _get(self, path: str) -> MutableMapping[str, Any] | list | None:
        """Queue a GET request and return the result.

        Args:
            path (str): API endpoint path to request.

        Returns:
            MutableMapping[str, Any] | list | None: Decoded response payload returned by the GET request.
        """
        loop = await self._get_active_loop()
        try:
            caller = inspect.stack()[1].function
        except IndexError, AttributeError:
            caller = "Unknown"
        future = loop.create_future()
        await self._request_queue.put(("get", path, None, future, caller))
        return await future

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
        loop = await self._get_active_loop()
        try:
            caller = inspect.stack()[1].function
        except IndexError, AttributeError:
            caller = "Unknown"
        future = loop.create_future()
        await self._request_queue.put(("post", path, payload, future, caller))
        return await future

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

    async def _do_get_from_stream(self, path: str, caller: str = "Unknown") -> dict[str, Any]:
        """Execute a streaming GET request immediately.

        Args:
            path (str): API endpoint path to request.
            caller (str): Caller name used for diagnostics and logging.

        Returns:
            dict[str, Any]: Decoded payload extracted from the streaming API response.
        """
        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[get_from_stream] url: %s", url)
        try:
            async with self._session.get(
                url,
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                ssl=self._verify_ssl,
            ) as response:
                _LOGGER.debug(
                    "[get_from_stream] Response %s: %s",
                    response.status,
                    response.reason,
                )

                if response.ok:
                    buffer = ""
                    message_count = 0

                    async for chunk in response.content.iter_chunked(1024):
                        buffer += chunk.decode("utf-8")
                        # _LOGGER.debug("[get_from_stream] buffer: %s", buffer)

                        while "\n\n" in buffer:
                            message, buffer = buffer.split("\n\n", 1)
                            lines = message.splitlines()
                            for line in lines:
                                if line.startswith("data:"):
                                    message_count += 1
                                    if message_count == 2:
                                        response_str: str = line[len("data:") :].strip()
                                        response_json = json.loads(response_str)
                                        # _LOGGER.debug(
                                        #     "[get_from_stream] response_json (%s): %s",
                                        #     type(response_json).__name__,
                                        #     response_json,
                                        # )
                                        return (
                                            dict(response_json)
                                            if isinstance(response_json, MutableMapping)
                                            else {}
                                        )  # Exit after processing the second message
                                    # _LOGGER.debug(
                                    #     "[get_from_stream] Ignored message %s: %s",
                                    #     message_count,
                                    #     line,
                                    # )
                                # else:
                                #     _LOGGER.debug("[get_from_stream] Unparsed: %s", line)
                else:
                    if response.status == 403:
                        _LOGGER.error(
                            "Permission Error in do_get_from_stream (called by %s). Path: %s. Ensure the OPNsense user connected to HA has appropriate access. Recommend full admin access",
                            caller,
                            url,
                        )
                    else:
                        _LOGGER.error(
                            "Error in do_get_from_stream (called by %s). Path: %s. Response %s: %s",
                            caller,
                            url,
                            response.status,
                            response.reason,
                        )
                    if self._throw_errors:
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info,
                            history=response.history,
                            status=response.status,
                            message=f"HTTP Status Error: {response.status} {response.reason}",
                            headers=response.headers,
                        )
        except (aiohttp.ClientError, TimeoutError) as e:
            _LOGGER.error("Client error. %s: %s", type(e).__name__, e)
            if self._throw_errors:
                raise

        return {}

    async def _do_get(
        self,
        path: str,
        caller: str = "Unknown",
        timeout_seconds: float | None = None,
    ) -> MutableMapping[str, Any] | list | None:
        """Execute a GET request immediately without queueing.

        Args:
            path (str): API endpoint path to request.
            caller (str): Caller name used for diagnostics and logging.
            timeout_seconds (float | None, optional): Request timeout in seconds for this call.

        Returns:
            MutableMapping[str, Any] | list | None: Decoded response payload returned by the GET request.
        """
        # /api/<module>/<controller>/<command>/[<param1>/[<param2>/...]]
        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[get] url: %s", url)
        timeout_total: float = self._normalize_timeout_seconds(timeout_seconds)
        try:
            async with self._session.get(
                url,
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=aiohttp.ClientTimeout(total=timeout_total),
                ssl=self._verify_ssl,
            ) as response:
                _LOGGER.debug("[get] Response %s: %s", response.status, response.reason)
                if response.ok:
                    return await response.json(content_type=None)
                if response.status == 403:
                    _LOGGER.error(
                        "Permission Error in do_get (called by %s). Path: %s. Ensure the OPNsense user connected to HA has appropriate access. Recommend full admin access",
                        caller,
                        url,
                    )
                else:
                    _LOGGER.error(
                        "Error in do_get (called by %s). Path: %s. Response %s: %s",
                        caller,
                        url,
                        response.status,
                        response.reason,
                    )
                if self._throw_errors:
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=f"HTTP Status Error: {response.status} {response.reason}",
                        headers=response.headers,
                    )
        except (aiohttp.ClientError, TimeoutError) as e:
            _LOGGER.error("Client error. %s: %s", type(e).__name__, e)
            if self._throw_errors:
                raise

        return None

    def _normalize_timeout_seconds(self, timeout_seconds: float | None) -> float:
        """Normalize per-call timeout values to a positive float in seconds.

        Args:
            timeout_seconds (float | None): Request timeout in seconds for this call.

        Returns:
            float: Normalized value ready for downstream processing.
        """
        if timeout_seconds is None:
            return float(DEFAULT_REQUEST_TIMEOUT_SECONDS)
        try:
            timeout_total = float(timeout_seconds)
        except TypeError, ValueError:
            return float(DEFAULT_REQUEST_TIMEOUT_SECONDS)
        if timeout_total <= 0:
            return float(DEFAULT_REQUEST_TIMEOUT_SECONDS)
        return timeout_total

    async def _safe_dict_get(self, path: str) -> dict[str, Any]:
        """Fetch data from the given path, ensuring the result is a dict.

        Args:
            path (str): API endpoint path to request.

        Returns:
            dict[str, Any]: Response payload coerced to a dictionary.
        """
        result = await self._get(path=path)
        return dict(result) if isinstance(result, MutableMapping) else {}

    async def _safe_dict_get_with_timeout(
        self, path: str, timeout_seconds: float
    ) -> dict[str, Any]:
        """Fetch a GET payload with a custom timeout and coerce to a dictionary.

        Args:
            path (str): API endpoint path to request.
            timeout_seconds (float): Request timeout in seconds for this call.

        Returns:
            dict[str, Any]: Response payload coerced to a dictionary.
        """
        result = await self._do_get(
            path=path,
            caller="_safe_dict_get_with_timeout",
            timeout_seconds=timeout_seconds,
        )
        return dict(result) if isinstance(result, MutableMapping) else {}

    async def _safe_list_get(self, path: str) -> list:
        """Fetch data from the given path, ensuring the result is a list.

        Args:
            path (str): API endpoint path to request.

        Returns:
            list: Response payload coerced to a list.
        """
        result = await self._get(path=path)
        return result if isinstance(result, list) else []

    async def _do_post(
        self, path: str, payload: MutableMapping[str, Any] | None = None, caller: str = "Unknown"
    ) -> MutableMapping[str, Any] | list | None:
        """Execute a POST request immediately without queueing.

        Args:
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None, optional): Request payload sent to the API endpoint.
            caller (str): Caller name used for diagnostics and logging.

        Returns:
            MutableMapping[str, Any] | list | None: Decoded response payload returned by the POST request.
        """
        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[post] url: %s", url)
        # _LOGGER.debug("[post] payload: %s", payload)
        try:
            async with self._session.post(
                url,
                json=payload,
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                ssl=self._verify_ssl,
            ) as response:
                _LOGGER.debug("[post] Response %s: %s", response.status, response.reason)
                if response.ok:
                    response_json: dict[str, Any] | list = await response.json(content_type=None)
                    return response_json
                if response.status == 403:
                    _LOGGER.error(
                        "Permission Error in do_post (called by %s). Path: %s. Ensure the OPNsense user connected to HA has appropriate access. Recommend full admin access",
                        caller,
                        url,
                    )
                else:
                    _LOGGER.error(
                        "Error in do_post (called by %s). Path: %s. Response %s: %s",
                        caller,
                        url,
                        response.status,
                        response.reason,
                    )
                if self._throw_errors:
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=f"HTTP Status Error: {response.status} {response.reason}",
                        headers=response.headers,
                    )
        except (aiohttp.ClientError, TimeoutError) as e:
            _LOGGER.error("Client error. %s: %s", type(e).__name__, e)
            if self._throw_errors:
                raise

        return None

    async def _safe_dict_post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fetch data from the given path, ensuring the result is a dict.

        Args:
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None, optional): Request payload sent to the API endpoint.

        Returns:
            dict[str, Any]: Response payload coerced to a dictionary.
        """
        result = await self._post(path=path, payload=payload)
        return dict(result) if isinstance(result, MutableMapping) else {}

    async def _safe_list_post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> list:
        """Fetch data from the given path, ensuring the result is a list.

        Args:
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None, optional): Request payload sent to the API endpoint.

        Returns:
            list: Response payload coerced to a list.
        """
        result = await self._post(path=path, payload=payload)
        return result if isinstance(result, list) else []

    async def is_endpoint_available(self, path: str, force_refresh: bool = False) -> bool:
        """Return whether a specific API endpoint appears to be available.

        Args:
            path (str): API endpoint path to request.
            force_refresh (bool): Whether to bypass cached endpoint availability.

        Returns:
            bool: True if a specific api endpoint appears to be available; otherwise, False.
        """
        if not isinstance(path, str) or not path:
            return False

        now = datetime.now().astimezone()
        cache_is_fresh = (
            path in self._endpoint_checked_at
            and (now - self._endpoint_checked_at[path]).total_seconds()
            < self._endpoint_cache_ttl_seconds
        )

        if not force_refresh and cache_is_fresh and path in self._endpoint_availability:
            return self._endpoint_availability[path]

        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[is_endpoint_available] url: %s", url)

        try:
            async with self._session.get(
                url,
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                ssl=self._verify_ssl,
            ) as response:
                if response.ok:
                    self._endpoint_availability[path] = True
                    self._endpoint_checked_at[path] = now
                    return True
                if response.status == 404:
                    self._endpoint_availability[path] = False
                    self._endpoint_checked_at[path] = now
                    return False

                self._endpoint_availability.pop(path, None)
                self._endpoint_checked_at.pop(path, None)
                if response.status == 403:
                    _LOGGER.error(
                        "Permission Error in is_endpoint_available. Path: %s. Ensure the OPNsense user connected to HA has appropriate access. Recommend full admin access",
                        url,
                    )
                else:
                    _LOGGER.warning(
                        "Transient endpoint check failure for %s. Response %s: %s. Not caching result.",
                        path,
                        response.status,
                        response.reason,
                    )
                return False
        except (aiohttp.ClientError, TimeoutError) as e:
            self._endpoint_availability.pop(path, None)
            self._endpoint_checked_at.pop(path, None)
            _LOGGER.warning(
                "Endpoint availability check failed for %s. %s: %s. Not caching result.",
                path,
                type(e).__name__,
                e,
            )
            return False

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
