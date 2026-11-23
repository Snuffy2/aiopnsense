"""Raw HTTP transport and response coercion helpers for OPNsenseClient."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, MutableMapping
from contextlib import aclosing
from enum import StrEnum
import json
import sys
from types import FrameType
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
from ._sse_decoder import _SseDecoder, _SseItem, _SseMessage, _SseRejection, _SseRejectionReason
from .const import DEFAULT_REQUEST_TIMEOUT_SECONDS
from .exceptions import OPNsenseError
from .helpers import _LOGGER, _log_request_recovery, _mark_request_error_logged

_STREAM_JSON_EVENT_RESET_KEY = "__aiopnsense_internal_stream_json_reset__"


async def _sse_items(chunks: AsyncIterator[bytes]) -> AsyncGenerator[_SseItem]:
    """Decode a bytes stream into server-sent-event items.

    Args:
        chunks (AsyncIterator[bytes]): Bytes chunks of the response body.

    Yields:
        _SseItem: Each item completed by a chunk, then those from the end-of-stream flush.
    """
    decoder = _SseDecoder()
    async for chunk in chunks:
        for item in decoder.feed(chunk):
            yield item
    for item in decoder.finish():
        yield item


class _RequestKind(StrEnum):
    """How a queued request's result is delivered.

    The HTTP method and response format are separate ``_QueueEntry`` fields.
    ``PLAIN`` returns the payload (or raises or returns ``None`` under the
    ``throw_errors`` policy), ``OUTCOME`` returns an ``_EndpointOutcome`` and
    ``STREAM`` returns a buffered stream read.
    """

    PLAIN = "plain"
    OUTCOME = "outcome"
    STREAM = "stream"


def _is_skipped_when_cancelled(kind: _RequestKind | str, method: str) -> bool:
    """Return whether the queue worker skips a request cancelled before dispatch.

    ``OUTCOME`` requests and plain POSTs are skipped. Plain GETs and buffered
    stream reads are still sent. An unknown kind is not skipped, so dispatch
    reports it.

    Args:
        kind (_RequestKind | str): Request kind of the queue entry.
        method (str): HTTP method of the queue entry.

    Returns:
        bool: True when the worker must not dispatch the cancelled request.
    """
    return kind == _RequestKind.OUTCOME or (kind == _RequestKind.PLAIN and method == "post")


def _public_caller_name() -> str:
    """Return the public client method that triggered the current request.

    Walks up the call stack from the caller of this function while the frames
    belong to the aiopnsense package, and returns the nearest frame that is a
    public method: its name has no leading underscore and its qualified name has
    no ``<locals>``. That skips private helpers and the error-policy wrappers, and
    when a public method calls another public method the nearest one is named. It
    does not depend on call depth. The walk ends without a name when the stack
    leaves the package.

    Returns:
        str: Name of the nearest public method, or ``"Unknown"`` when the call
            stack leaves the package without reaching one, or frame inspection is
            unavailable.
    """
    package = __name__.partition(".")[0]
    try:
        frame: FrameType | None = sys._getframe(1)
    except ValueError, AttributeError:
        return "Unknown"
    while frame is not None and frame.f_globals.get("__name__", "").partition(".")[0] == package:
        code = frame.f_code
        if not code.co_name.startswith("_") and "<locals>" not in code.co_qualname:
            return code.co_name
        frame = frame.f_back
    return "Unknown"


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

    async def _dispatch_request(
        self,
        kind: _RequestKind,
        path: str,
        payload: MutableMapping[str, Any] | None,
        caller: str,
        method: Literal["get", "post"] = "get",
        response_format: Literal["json", "text"] = "json",
    ) -> Any:
        """Send one dequeued request through the transport method for its kind.

        Args:
            kind (_RequestKind): How the result is delivered.
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None): Request body for POST requests.
            caller (str): Calling method name used for diagnostics.
            method (Literal["get", "post"]): HTTP method of the request.
            response_format (Literal["json", "text"]): Body format of an outcome GET.

        Returns:
            Any: Result of the matching transport method.

        Raises:
            error: An ``OPNsenseError``, already logged, if ``kind`` is not a known request kind.
        """
        match kind, method:
            case _RequestKind.STREAM, _:
                return await self._do_get_from_stream(path, caller)
            case _RequestKind.PLAIN, "get":
                return await self._do_get(path, caller)
            case _RequestKind.PLAIN, "post":
                return await self._do_post(path, payload, caller)
            case _RequestKind.OUTCOME, "get" if response_format == "text":
                return await self._do_get_outcome(path, caller, response_format="text")
            case _RequestKind.OUTCOME, "get":
                return await self._do_get_outcome(path, caller)
            case _RequestKind.OUTCOME, "post":
                return await self._do_post_outcome(path, payload, caller)
        _LOGGER.error("Unknown method to add to Queue: %s", kind)
        error = OPNsenseError(f"Unknown method to add to Queue: {kind}")
        _mark_request_error_logged(error)
        raise error

    def _open_request(
        self,
        method: Literal["get", "post"],
        path: str,
        *,
        timeout: aiohttp.ClientTimeout,
        payload: MutableMapping[str, Any] | None = None,
    ) -> Any:
        """Count and open one HTTP request to the Router.

        This is the only place that sends a request, supplying its URL, authentication
        and TLS setting and passing it through the ADR 0002 guard. The count is taken before the
        guard, so a blocked request is counted as an attempted one. Callers open the
        returned context inside their own ``try`` so a blocked request reaches their
        existing error handling.

        Args:
            method (Literal["get", "post"]): Lowercase HTTP method.
            path (str): API endpoint path to request.
            timeout (aiohttp.ClientTimeout): Timeout for this request.
            payload (MutableMapping[str, Any] | None): JSON body for a POST. ``None``
                sends an empty body. A GET never sends a body.

        Returns:
            Any: The session's request context manager. A request the ADR 0002 guard
                forbids sends nothing and raises ``_BlockedFirmwareRequest``.
        """
        self._rest_api_query_count += 1
        request_kwargs: dict[str, Any] = {
            "auth": aiohttp.BasicAuth(self._username, self._password),
            "timeout": timeout,
            "ssl": self._verify_ssl,
        }
        if method == "post":
            request_kwargs["json"] = payload
        return _send_request(self._session, method, f"{self._url}{path}", **request_kwargs)

    async def _do_get_from_stream(self, path: str, caller: str = "Unknown") -> dict[str, Any]:
        """Execute a streaming GET request immediately.

        The stream is decoded like ``_stream_json_events`` decodes it: UTF-8 across chunks,
        CRLF, CR and LF line endings, and ``data:`` lines joined into one message per event.
        Malformed JSON and non-mapping JSON count as messages. Invalid or truncated UTF-8
        before the second message, or a second message that is not valid JSON, fails the read.
        Only one space after ``data:`` is removed, so other whitespace around the JSON is no
        longer stripped: a second message whose value starts with a non-breaking space fails
        with a JSON decode error.

        Args:
            path (str): API endpoint path to request.
            caller (str): Caller name used for diagnostics and logging.

        Returns:
            dict[str, Any]: The second message of the stream, or an empty mapping when the
                stream ends first, the second message is valid JSON but not a mapping, or
                after a logged failure. When ``throw_errors`` is enabled, an HTTP, client or
                timeout failure is raised as the mapped public ``OPNsenseError`` instead.

        Raises:
            decode_error: The ``UnicodeDecodeError`` for invalid or truncated UTF-8 before the
                second message.
            json_error: The ``json.JSONDecodeError`` for a second message that is not valid
                JSON.
        """
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[get_from_stream] path: %s", path)
        try:
            async with self._open_request(
                "get",
                path,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
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

                message_count = 0
                async with aclosing(_sse_items(response.content.iter_chunked(1024))) as items:
                    async for item in items:
                        if isinstance(item, _SseRejection) and isinstance(
                            item.error, UnicodeDecodeError
                        ):
                            decode_error = item.error
                            raise decode_error
                        message_count += 1
                        if message_count != 2:
                            continue
                        match item:
                            case _SseMessage(payload=payload):
                                _log_request_recovery(self, "get", path, _STREAM_PHASE)
                                return payload
                            case _SseRejection(error=json.JSONDecodeError() as json_error):
                                raise json_error
                            case _:
                                return {}
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
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[stream_json_events] path: %s", path)
        try:
            async with self._open_request(
                "get",
                path,
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    sock_connect=DEFAULT_REQUEST_TIMEOUT_SECONDS,
                    sock_read=self._normalize_timeout_seconds(sock_read_timeout_seconds),
                ),
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

                async with aclosing(_sse_items(response.content.iter_chunked(1024))) as items:
                    async for item in items:
                        match item:
                            case _SseMessage(payload=payload):
                                if not recovered:
                                    _log_request_recovery(self, "get", path, _STREAM_PHASE)
                                    recovered = True
                                yield payload
                            case _SseRejection(reason=_SseRejectionReason.TRUNCATED_UTF8):
                                _LOGGER.debug(
                                    "Dropping incomplete UTF-8 trailing bytes in "
                                    "_stream_json_events: %s",
                                    item.error,
                                )
                            case _SseRejection(reason=_SseRejectionReason.INVALID_UTF8):
                                _LOGGER.debug(
                                    "Dropping incomplete UTF-8 chunk in _stream_json_events: %s",
                                    item.error,
                                )
                                if yield_reset_events:
                                    yield {_STREAM_JSON_EVENT_RESET_KEY: True}
                            case _SseRejection(reason=_SseRejectionReason.MALFORMED_JSON):
                                _LOGGER.debug("Skipping malformed stream JSON event: %s", item.text)
                                if yield_reset_events:
                                    yield {_STREAM_JSON_EVENT_RESET_KEY: True}
                            case _SseRejection():
                                _LOGGER.debug(
                                    "Skipping non-mapping stream JSON event: %s (%s)",
                                    item.text,
                                    item.json_type,
                                )
                                if yield_reset_events:
                                    yield {_STREAM_JSON_EVENT_RESET_KEY: True}
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
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[get] path: %s", path)
        timeout_total: float = self._normalize_timeout_seconds(timeout_seconds)
        try:
            async with self._open_request(
                "get", path, timeout=aiohttp.ClientTimeout(total=timeout_total)
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
        url: str = f"{self._url}{path}"
        method_name: Literal["get", "post"] = "get" if method == "GET" else "post"
        operation = f"do_{method_name}_outcome"
        _LOGGER.debug("[%s] path: %s (called by %s)", method_name, path, caller)
        try:
            async with self._open_request(
                method_name,
                path,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                payload=payload,
            ) as response:
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
            caller=_public_caller_name(),
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
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[post] path: %s", path)
        try:
            async with self._open_request(
                "post",
                path,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                payload=payload,
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
