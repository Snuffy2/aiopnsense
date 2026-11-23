"""Raw HTTP transport and response coercion helpers for OPNsenseClient."""

import asyncio
import codecs
from collections.abc import AsyncGenerator, MutableMapping
import json
from typing import TYPE_CHECKING, Any, Literal

import aiohttp

from ._endpoint_outcome import _EndpointOutcome
from ._error_policy import _raise_if_throwing
from ._firmware_request_guard import _send_request
from ._request_failure import (
    _REQUEST_PHASE,
    _STREAM_PHASE,
    _report_http_failure,
    _report_transport_failure,
)
from .const import DEFAULT_REQUEST_TIMEOUT_SECONDS
from .helpers import _LOGGER, _log_request_recovery

_STREAM_JSON_EVENT_RESET_KEY = "__aiopnsense_internal_stream_json_reset__"


class ClientTransportMixin:
    """Immediate request execution and safe response methods for OPNsenseClient."""

    if TYPE_CHECKING:
        _password: str
        _request_outages: dict[tuple[str, str, str], int | None]
        _rest_api_query_count: int
        _session: aiohttp.ClientSession
        _throw_errors: bool
        _url: str
        _username: str
        _verify_ssl: bool

        async def _get(self, path: str) -> MutableMapping[str, Any] | list | None:
            """Queue a GET request and return the decoded payload.

            Args:
                path (str): API path to request.

            Returns:
                MutableMapping[str, Any] | list | None: Decoded response payload.
            """
            ...

        async def _post(
            self, path: str, payload: MutableMapping[str, Any] | None = None
        ) -> MutableMapping[str, Any] | list | None:
            """Queue a POST request and return the decoded payload.

            Args:
                path (str): API path to request.
                payload (MutableMapping[str, Any] | None): JSON object to send with the request.

            Returns:
                MutableMapping[str, Any] | list | None: Decoded response payload.
            """
            ...

        async def _queue_post_with_cancellation(
            self, path: str, payload: MutableMapping[str, Any] | None = None
        ) -> tuple[MutableMapping[str, Any] | list | None, asyncio.CancelledError | None]:
            """Queue a POST and retain its response when cancellation follows dispatch.

            Args:
                path (str): API endpoint path to request.
                payload (MutableMapping[str, Any] | None): Optional request body.

            Returns:
                tuple[MutableMapping[str, Any] | list | None, asyncio.CancelledError | None]:
                    Response and any cancellation received after dispatch began.
            """
            ...

    async def _do_get_from_stream(self, path: str, caller: str = "Unknown") -> dict[str, Any]:
        """Execute a streaming GET request immediately.

        Args:
            path (str): API endpoint path to request.
            caller (str): Caller name used for diagnostics and logging.

        Returns:
            dict[str, Any]: Decoded payload extracted from the streaming API response,
                or an empty mapping after a logged failure. When ``throw_errors`` is
                enabled, an HTTP, client or timeout failure is raised as the mapped
                public ``OPNsenseError`` instead.
        """
        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[get_from_stream] path: %s", path)
        try:
            async with _send_request(
                self._session,
                "get",
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

                if not response.ok:
                    http_error = _report_http_failure(
                        self,
                        method="get",
                        path=path,
                        phase=_STREAM_PHASE,
                        operation="do_get_from_stream",
                        url=url,
                        status=response.status,
                        reason=response.reason,
                        caller=caller,
                    )
                    _raise_if_throwing(self, http_error)
                    return {}

                buffer = ""
                message_count = 0
                async for chunk in response.content.iter_chunked(1024):
                    buffer += chunk.decode("utf-8")
                    while "\n\n" in buffer:
                        message, buffer = buffer.split("\n\n", 1)
                        for line in message.splitlines():
                            if not line.startswith("data:"):
                                continue
                            message_count += 1
                            if message_count != 2:
                                continue
                            response_str: str = line[len("data:") :].strip()
                            response_json = json.loads(response_str)
                            if not isinstance(response_json, MutableMapping):
                                return {}
                            _log_request_recovery(self, "get", path, _STREAM_PHASE)
                            return dict(response_json)
        except (aiohttp.ClientError, TimeoutError) as e:
            transport_error = _report_transport_failure(
                self,
                method="get",
                path=path,
                phase=_STREAM_PHASE,
                operation="do_get_from_stream",
                exc=e,
                caller=caller,
            )
            _raise_if_throwing(self, transport_error, e)

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
            dict[str, Any]: Decoded JSON object from each valid ``data:`` event. The
                stream ends after a logged failure. When ``throw_errors`` is enabled,
                an HTTP, client or timeout failure is raised as the mapped public
                ``OPNsenseError`` instead.
        """
        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[stream_json_events] path: %s", path)
        try:
            async with _send_request(
                self._session,
                "get",
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
                    http_error = _report_http_failure(
                        self,
                        method="get",
                        path=path,
                        phase=_STREAM_PHASE,
                        operation="stream_json_events",
                        url=url,
                        status=response.status,
                        reason=response.reason,
                    )
                    _raise_if_throwing(self, http_error)
                    return

                recovered = False
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
                    nonlocal recovered
                    events: list[dict[str, Any]] = []
                    drain_buffer = source_buffer
                    while "\n\n" in drain_buffer:
                        message, drain_buffer = drain_buffer.split("\n\n", 1)
                        data_lines: list[str] = []
                        for line in message.splitlines():
                            if not line.startswith("data:"):
                                continue
                            value = line[len("data:") :]
                            value = value.removeprefix(" ")
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
                        if not recovered:
                            _log_request_recovery(self, "get", path, _STREAM_PHASE)
                            recovered = True
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
            transport_error = _report_transport_failure(
                self,
                method="get",
                path=path,
                phase=_STREAM_PHASE,
                operation="stream_json_events",
                exc=err,
            )
            _raise_if_throwing(self, transport_error, err)

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
            MutableMapping[str, Any] | list | None: Decoded response payload
                returned by the GET request, or ``None`` after a logged failure.
                When ``throw_errors`` is enabled, an HTTP or transport failure is
                raised as the mapped public ``OPNsenseError`` instead.
        """
        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[get] path: %s", path)
        timeout_total: float = self._normalize_timeout_seconds(timeout_seconds)
        try:
            async with _send_request(
                self._session,
                "get",
                url,
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=aiohttp.ClientTimeout(total=timeout_total),
                ssl=self._verify_ssl,
            ) as response:
                _LOGGER.debug("[get] Response %s: %s", response.status, response.reason)
                if response.ok:
                    result = await response.json(content_type=None)
                    _log_request_recovery(self, "get", path, _REQUEST_PHASE)
                    return result
                http_error = _report_http_failure(
                    self,
                    method="get",
                    path=path,
                    phase=_REQUEST_PHASE,
                    operation="do_get",
                    url=url,
                    status=response.status,
                    reason=response.reason,
                    caller=caller,
                )
                _raise_if_throwing(self, http_error)
        except (aiohttp.ClientError, TimeoutError) as e:
            transport_error = _report_transport_failure(
                self,
                method="get",
                path=path,
                phase=_REQUEST_PHASE,
                operation="do_get",
                exc=e,
                caller=caller,
            )
            _raise_if_throwing(self, transport_error, e)

        return None

    async def _request_outcome(
        self,
        method: Literal["GET", "POST"],
        path: str,
        caller: str,
        payload: MutableMapping[str, Any] | None = None,
        *,
        phase: str = _REQUEST_PHASE,
        read_body: bool = True,
        response_format: Literal["json", "text"] = "json",
    ) -> _EndpointOutcome:
        """Execute an endpoint request immediately and classify the response.

        Shared body of ``_do_get_outcome`` and ``_do_post_outcome``. A 404 is returned
        as an absent outcome. Any other failed HTTP response or transport error is
        logged by the shared request reporter and returned as an outcome instead of
        being raised. Undecodable response bodies propagate to the queue worker.

        Args:
            method (Literal["GET", "POST"]): HTTP method to send.
            path (str): API endpoint path to request.
            caller (str): Caller name used for diagnostics.
            payload (MutableMapping[str, Any] | None, optional): Request payload sent
                with a POST request; ignored for GET.
            phase (str): Request phase for outage logging, ``probe`` for status-only
                checks or ``request`` for data reads.
            read_body (bool): Whether to decode the body of a successful response.
                ``False`` makes a status-only check: the body is not read and a
                successful response carries no payload.
            response_format (Literal["json", "text"], optional): Decoding mode for a
                successful response body.

        Returns:
            _EndpointOutcome: ``DATA`` with the decoded payload or body text (``None`` when
                ``read_body`` is ``False``) for a successful response, ``ABSENT`` for a
                404 or 403 response, and ``TRANSIENT`` for any other HTTP status, timeout
                or client error.
        """
        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        method_name = method.lower()
        operation = f"do_{method_name}_outcome"
        _LOGGER.debug("[%s] path: %s (called by %s)", method_name, path, caller)
        request_kwargs: dict[str, Any] = {
            "auth": aiohttp.BasicAuth(self._username, self._password),
            "timeout": aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
            "ssl": self._verify_ssl,
        }
        try:
            if method != "GET":
                request_kwargs["json"] = payload
            async with _send_request(self._session, method_name, url, **request_kwargs) as response:
                _LOGGER.debug("[%s] Response %s: %s", method_name, response.status, response.reason)
                if response.status == 404:
                    return _EndpointOutcome.absent()
                if not response.ok:
                    http_error = _report_http_failure(
                        self,
                        method=method_name,
                        path=path,
                        phase=phase,
                        operation=operation,
                        url=url,
                        status=response.status,
                        reason=response.reason,
                        caller=caller,
                    )
                    return _EndpointOutcome.from_http_error(response.status, http_error)
                if not read_body:
                    body = None
                elif response_format == "text":
                    body = await response.text()
                else:
                    body = await response.json(content_type=None)
                _log_request_recovery(self, method_name, path, phase)
                return _EndpointOutcome.data(body)
        except (aiohttp.ClientError, TimeoutError) as e:
            transport_error = _report_transport_failure(
                self,
                method=method_name,
                path=path,
                phase=phase,
                operation=operation,
                exc=e,
                caller=caller,
            )
            transport_error.__cause__ = e
            return _EndpointOutcome.transient(transport_error)

    async def _do_get_outcome(
        self,
        path: str,
        caller: str = "Unknown",
        *,
        read_body: bool = True,
        response_format: Literal["json", "text"] = "json",
        phase: str = _REQUEST_PHASE,
    ) -> _EndpointOutcome:
        """Execute a GET request immediately and classify the response.

        Unlike ``_do_get``, an HTTP or transport failure is returned as an
        outcome instead of being raised, so the endpoint module can tell a missing
        endpoint from a transient failure and apply the ``throw_errors`` policy itself.

        Args:
            path (str): API endpoint path to request.
            caller (str): Caller name used for diagnostics.
            read_body (bool): Whether to decode the body of a successful response.
                ``False`` makes a status-only check: the body is not read and a
                successful response carries no payload.
            response_format (Literal["json", "text"], optional): Decoding mode for a
                successful response body.
            phase (str): Request phase for outage logging, ``probe`` for status-only
                checks or ``request`` for data reads.

        Returns:
            _EndpointOutcome: The outcome described by ``_request_outcome``.
        """
        return await self._request_outcome(
            "GET",
            path,
            caller,
            phase=phase,
            read_body=read_body,
            response_format=response_format,
        )

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

    async def _do_post(
        self, path: str, payload: MutableMapping[str, Any] | None = None, caller: str = "Unknown"
    ) -> MutableMapping[str, Any] | list | None:
        """Execute a POST request immediately without queueing.

        Args:
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None, optional): Request payload sent to the API endpoint.
            caller (str): Caller name used for diagnostics and logging.

        Returns:
            MutableMapping[str, Any] | list | None: Decoded response payload returned by the POST request,
                or ``None`` after a logged failure. When ``throw_errors`` is enabled,
                an HTTP or transport failure is raised as the mapped public
                ``OPNsenseError`` instead.
        """
        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[post] path: %s", path)
        try:
            async with _send_request(
                self._session,
                "post",
                url,
                json=payload,
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                ssl=self._verify_ssl,
            ) as response:
                _LOGGER.debug("[post] Response %s: %s", response.status, response.reason)
                if response.ok:
                    response_json: dict[str, Any] | list = await response.json(content_type=None)
                    _log_request_recovery(self, "post", path, _REQUEST_PHASE)
                    return response_json
                http_error = _report_http_failure(
                    self,
                    method="post",
                    path=path,
                    phase=_REQUEST_PHASE,
                    operation="do_post",
                    url=url,
                    status=response.status,
                    reason=response.reason,
                    caller=caller,
                )
                _raise_if_throwing(self, http_error)
        except (aiohttp.ClientError, TimeoutError) as e:
            transport_error = _report_transport_failure(
                self,
                method="post",
                path=path,
                phase=_REQUEST_PHASE,
                operation="do_post",
                exc=e,
                caller=caller,
            )
            _raise_if_throwing(self, transport_error, e)

        return None

    async def _do_post_outcome(
        self,
        path: str,
        payload: MutableMapping[str, Any] | None = None,
        caller: str = "Unknown",
    ) -> _EndpointOutcome:
        """Execute a POST request immediately and classify the response.

        Unlike ``_do_post``, an HTTP or transport failure is returned as an
        outcome instead of being raised, so the endpoint module can tell a missing
        endpoint from a transient failure and apply the ``throw_errors`` policy itself.
        The request always carries the caller's payload.

        Args:
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None): Request payload sent to the API endpoint.
            caller (str): Caller name used for diagnostics.

        Returns:
            _EndpointOutcome: The outcome described by ``_request_outcome``.
        """
        return await self._request_outcome("POST", path, caller, payload)

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

    async def _safe_dict_post_with_cancellation(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> tuple[dict[str, Any], asyncio.CancelledError | None]:
        """Post through the queue while preserving dispatched responses on cancellation.

        Args:
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None): Optional request body.

        Returns:
            tuple[dict[str, Any], asyncio.CancelledError | None]: Dictionary
                response and any cancellation received after dispatch began.
        """
        result, cancelled = await self._queue_post_with_cancellation(path, payload)
        return (dict(result) if isinstance(result, MutableMapping) else {}, cancelled)

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
