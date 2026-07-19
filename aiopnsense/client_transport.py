"""Raw HTTP transport and response coercion helpers for OPNsenseClient."""

import codecs
from collections.abc import AsyncGenerator, MutableMapping
import json
from typing import TYPE_CHECKING, Any, Literal

import aiohttp

from .const import DEFAULT_REQUEST_TIMEOUT_SECONDS
from .exceptions import _map_opnsense_exception, _opnsense_http_error
from .helpers import _LOGGER

_STREAM_JSON_EVENT_RESET_KEY = "__aiopnsense_internal_stream_json_reset__"


class ClientTransportMixin:
    """Immediate request execution and safe response methods for OPNsenseClient."""

    if TYPE_CHECKING:
        _password: str
        _rest_api_query_count: int
        _session: aiohttp.ClientSession
        _throw_errors: bool
        _url: str
        _username: str
        _verify_ssl: bool

        async def _get(self, path: str) -> MutableMapping[str, Any] | list | None:
            """Queue a GET request and return the decoded payload."""
            ...

        async def _post(
            self, path: str, payload: MutableMapping[str, Any] | None = None
        ) -> MutableMapping[str, Any] | list | None:
            """Queue a POST request and return the decoded payload."""
            ...

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
                        while "\n\n" in buffer:
                            message, buffer = buffer.split("\n\n", 1)
                            lines = message.splitlines()
                            for line in lines:
                                if line.startswith("data:"):
                                    message_count += 1
                                    if message_count == 2:
                                        response_str: str = line[len("data:") :].strip()
                                        response_json = json.loads(response_str)
                                        return (
                                            dict(response_json)
                                            if isinstance(response_json, MutableMapping)
                                            else {}
                                        )
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
                        raise _opnsense_http_error(response.status, response.reason)
        except (aiohttp.ClientError, TimeoutError) as e:
            _LOGGER.error("Client error. %s: %s", type(e).__name__, e)
            if self._throw_errors:
                raise _map_opnsense_exception(e) from e

        return {}

    async def _stream_json_events(
        self,
        path: str,
        *,
        yield_reset_events: bool = False,
        sock_read_timeout_seconds: float | None = None,
    ) -> AsyncGenerator[dict[str, Any]]:
        """Yield decoded JSON objects from a server-sent event stream.

        Args:
            path (str): API endpoint path to request.
            yield_reset_events (bool): Yield internal reset events when UTF-8
                decoding fails. Default is disabled for backwards compatibility.
            sock_read_timeout_seconds (float | None): Optional per-read timeout
                for streamed responses.

        Yields:
            dict[str, Any]: Decoded JSON object from each valid ``data:`` event.
        """
        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[stream_json_events] url: %s", url)
        try:
            async with self._session.get(
                url,
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    sock_connect=DEFAULT_REQUEST_TIMEOUT_SECONDS,
                    sock_read=self._normalize_timeout_seconds(sock_read_timeout_seconds),
                ),
                ssl=self._verify_ssl,
            ) as response:
                if not response.ok:
                    if response.status == 403:
                        _LOGGER.error(
                            "Permission Error in stream_json_events. Path: %s. Ensure the OPNsense user connected to HA has appropriate access. Recommend full admin access",
                            url,
                        )
                    else:
                        _LOGGER.error(
                            "Error in stream_json_events. Path: %s. Response %s: %s",
                            url,
                            response.status,
                            response.reason,
                        )
                    if self._throw_errors:
                        raise _opnsense_http_error(response.status, response.reason)
                    return

                decoder = codecs.getincrementaldecoder("utf-8")()
                buffer = ""
                pending_cr = False

                def _drain_buffer(
                    source_buffer: str,
                ) -> tuple[str, list[dict[str, Any]]]:
                    """Drain complete JSON SSE events from a text buffer.

                    Args:
                        source_buffer (str): Buffered stream text to parse.

                    Returns:
                        tuple[str, list[dict[str, Any]]]: Remaining unparsed buffer
                            text and decoded JSON events.
                    """
                    events: list[dict[str, Any]] = []
                    drain_buffer = source_buffer
                    while "\n\n" in drain_buffer:
                        message, drain_buffer = drain_buffer.split("\n\n", 1)
                        data_lines: list[str] = []
                        for line in message.splitlines():
                            if not line.startswith("data:"):
                                continue
                            value = line[len("data:") :]
                            if value.startswith(" "):
                                value = value[1:]
                            data_lines.append(value)

                        if not data_lines:
                            continue

                        response_str = "\n".join(data_lines)
                        try:
                            response_json = json.loads(response_str)
                        except json.JSONDecodeError:
                            _LOGGER.debug("Skipping malformed stream JSON event: %s", response_str)
                            if yield_reset_events:
                                events.append({_STREAM_JSON_EVENT_RESET_KEY: True})
                            continue
                        if not isinstance(response_json, MutableMapping):
                            _LOGGER.debug(
                                "Skipping non-mapping stream JSON event: %s (%s)",
                                response_str,
                                type(response_json).__name__,
                            )
                            if yield_reset_events:
                                events.append({_STREAM_JSON_EVENT_RESET_KEY: True})
                            continue
                        events.append(dict(response_json))

                    return drain_buffer, events

                async for chunk in response.content.iter_chunked(1024):
                    try:
                        chunk_text = decoder.decode(chunk)
                    except UnicodeDecodeError as err:
                        _LOGGER.debug(
                            "Dropping incomplete UTF-8 chunk in _stream_json_events: %s",
                            err,
                        )
                        if yield_reset_events:
                            yield {_STREAM_JSON_EVENT_RESET_KEY: True}
                        decoder = codecs.getincrementaldecoder("utf-8")()
                        buffer = ""
                        pending_cr = False
                        continue
                    if pending_cr:
                        chunk_text = f"\r{chunk_text}"
                        pending_cr = False

                    if chunk_text.endswith("\r"):
                        chunk_text = chunk_text[:-1]
                        pending_cr = True

                    buffer += chunk_text.replace("\r\n", "\n").replace("\r", "\n")
                    buffer, events = _drain_buffer(buffer)
                    for event in events:
                        yield event
                try:
                    tail_text = decoder.decode(b"", final=True)
                except UnicodeDecodeError as err:
                    _LOGGER.debug(
                        "Dropping incomplete UTF-8 trailing bytes in _stream_json_events: %s",
                        err,
                    )
                    tail_text = ""
                if pending_cr:
                    tail_text = f"\r{tail_text}"
                if tail_text:
                    if tail_text.endswith("\r"):
                        tail_text = tail_text[:-1]
                        pending_cr = True
                    if tail_text:
                        buffer += tail_text.replace("\r\n", "\n").replace("\r", "\n")
                if pending_cr:
                    buffer += "\n"

                buffer, events = _drain_buffer(buffer)
                for event in events:
                    yield event
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.error("Client error in stream_json_events. %s: %s", type(err).__name__, err)
            if self._throw_errors:
                raise _map_opnsense_exception(err) from err

    async def _do_get(
        self,
        path: str,
        caller: str = "Unknown",
        timeout_seconds: float | None = None,
        *,
        response_format: Literal["json", "text"] = "json",
    ) -> MutableMapping[str, Any] | list | str | None:
        """Execute a GET request immediately without queueing.

        Args:
            path (str): API endpoint path to request.
            caller (str): Caller name used for diagnostics and logging.
            timeout_seconds (float | None, optional): Request timeout in seconds for this call.
            response_format (Literal["json", "text"], optional): Response decoding mode.

        Returns:
            MutableMapping[str, Any] | list | str | None: Decoded response payload
                returned by the GET request.
        """
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
                    if response_format == "text":
                        return await response.text()
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
                    raise _opnsense_http_error(response.status, response.reason)
        except (aiohttp.ClientError, TimeoutError) as e:
            _LOGGER.error("Client error. %s: %s", type(e).__name__, e)
            if self._throw_errors:
                raise _map_opnsense_exception(e) from e

        return None

    async def _do_optional_get(
        self, path: str, caller: str = "Unknown"
    ) -> tuple[Literal["available", "malformed", "missing", "unavailable"], object]:
        """Execute an optional GET request immediately.

        Args:
            path (str): API endpoint path to request.
            caller (str): Caller name used for diagnostics and logging.

        Returns:
            tuple[Literal["available", "malformed", "missing", "unavailable"], object]:
                Availability state and parsed response payload.
        """
        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[optional_get] url: %s", url)
        try:
            async with self._session.get(
                url,
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                ssl=self._verify_ssl,
            ) as response:
                _LOGGER.debug("[optional_get] Response %s: %s", response.status, response.reason)
                if response.ok:
                    try:
                        return "available", await response.json(content_type=None)
                    except (ValueError, UnicodeDecodeError) as err:
                        _LOGGER.debug(
                            "Optional GET endpoint returned malformed JSON for %s: %s",
                            path,
                            err,
                        )
                        return "malformed", {}
                if response.status == 404:
                    _LOGGER.debug(
                        "Optional GET endpoint unavailable (HTTP 404). Path: %s (called by %s)",
                        path,
                        caller,
                    )
                    return "missing", {}
                if response.status == 403:
                    _LOGGER.error(
                        "Permission Error in optional_get (called by %s). Path: %s. Ensure the OPNsense user connected to HA has appropriate access. Recommend full admin access",
                        caller,
                        url,
                    )
                else:
                    _LOGGER.warning(
                        "Transient optional GET endpoint failure for %s. Response %s: %s",
                        path,
                        response.status,
                        response.reason,
                    )
                if self._throw_errors:
                    raise _opnsense_http_error(response.status, response.reason)
        except (aiohttp.ClientError, TimeoutError) as e:
            _LOGGER.warning(
                "Optional GET endpoint availability check failed for %s. %s: %s.",
                path,
                type(e).__name__,
                e,
            )
            if self._throw_errors:
                raise _map_opnsense_exception(e) from e

        return "unavailable", {}

    async def _do_optional_post(
        self,
        path: str,
        payload: MutableMapping[str, Any] | None = None,
        caller: str = "Unknown",
    ) -> tuple[Literal["available", "malformed", "missing", "unavailable"], object]:
        """Execute an explicitly read-only optional POST immediately.

        Args:
            path: API endpoint path to request.
            payload: Optional JSON request payload.
            caller: Caller name used for diagnostics and logging.

        Returns:
            Availability state and decoded response payload.
        """
        self._rest_api_query_count += 1
        url = f"{self._url}{path}"
        _LOGGER.debug("[optional_post] url: %s", url)
        try:
            async with self._session.post(
                url,
                auth=aiohttp.BasicAuth(self._username, self._password),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                ssl=self._verify_ssl,
            ) as response:
                _LOGGER.debug("[optional_post] Response %s: %s", response.status, response.reason)
                if response.ok:
                    try:
                        return "available", await response.json(content_type=None)
                    except (ValueError, UnicodeDecodeError) as err:
                        _LOGGER.debug(
                            "Optional POST endpoint returned malformed JSON for %s: %s",
                            path,
                            err,
                        )
                        return "malformed", {}
                if response.status == 404:
                    _LOGGER.debug(
                        "Optional POST endpoint unavailable (HTTP 404). Path: %s (called by %s)",
                        path,
                        caller,
                    )
                    return "missing", {}
                if response.status == 403:
                    _LOGGER.error(
                        "Permission Error in optional_post (called by %s). Path: %s. Ensure the OPNsense user connected to HA has appropriate access. Recommend full admin access",
                        caller,
                        url,
                    )
                else:
                    _LOGGER.warning(
                        "Transient optional POST endpoint failure for %s. Response %s: %s",
                        path,
                        response.status,
                        response.reason,
                    )
                if self._throw_errors:
                    raise _opnsense_http_error(response.status, response.reason)
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning(
                "Optional POST endpoint availability check failed for %s. %s: %s.",
                path,
                type(err).__name__,
                err,
            )
            if self._throw_errors:
                raise _map_opnsense_exception(err) from err

        return "unavailable", {}

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
                    raise _opnsense_http_error(response.status, response.reason)
        except (aiohttp.ClientError, TimeoutError) as e:
            _LOGGER.error("Client error. %s: %s", type(e).__name__, e)
            if self._throw_errors:
                raise _map_opnsense_exception(e) from e

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
