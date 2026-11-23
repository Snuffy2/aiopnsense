"""Raw HTTP transport and response coercion helpers for OPNsenseClient."""

import asyncio
import codecs
from collections.abc import AsyncGenerator, Awaitable, Callable, MutableMapping
from dataclasses import dataclass, replace
import json
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal

import aiohttp
from yarl import URL

from ._endpoint_outcome import _EndpointOutcome
from ._error_policy import _raise_if_throwing
from ._request_failure import (
    _REQUEST_PHASE,
    _STREAM_PHASE,
    _report_http_failure,
    _report_transport_failure,
)
from .const import DEFAULT_REQUEST_TIMEOUT_SECONDS
from .exceptions import _map_opnsense_exception
from .helpers import (
    _LOGGER,
    _log_request_recovery,
    _redact_url_credentials,
    _redacted_exception_message,
)

_STREAM_JSON_EVENT_RESET_KEY = "__aiopnsense_internal_stream_json_reset__"

type _ResponseStatusValidator = Callable[[aiohttp.ClientResponse], Awaitable[None]]
type _ValidatorFailure = (
    aiohttp.ClientError | TimeoutError | UnicodeDecodeError | json.JSONDecodeError
)
type _ValidatorErrors = list[tuple[_ValidatorFailure, aiohttp.ClientResponse]]


@dataclass(slots=True)
class _ObservedResponse:
    """Record the response handed to aiohttp's status validator."""

    response: aiohttp.ClientResponse | None = None
    observation_id: int = 0


def _outcome_raise_for_status(
    session: aiohttp.ClientSession,
    next_observation_id: Callable[[], int],
) -> tuple[_ResponseStatusValidator, _ObservedResponse, _ValidatorErrors]:
    """Wrap a custom session validator for endpoint outcome requests.

    Args:
        session (aiohttp.ClientSession): Session whose validator applies to requests.
        next_observation_id (Callable[[], int]): Assign the response's arrival order.

    Returns:
        tuple[_ResponseStatusValidator, _ObservedResponse, _ValidatorErrors]:
            Per-request validator, its observed response, and exceptions it raises.
    """
    validator = session.raise_for_status
    observed = _ObservedResponse()
    validator_errors: _ValidatorErrors = []

    async def validate_response(response: aiohttp.ClientResponse) -> None:
        """Run the session validator for every delivered response.

        Args:
            response (aiohttp.ClientResponse): HTTP response returned by aiohttp.

        Raises:
            aiohttp.ClientError: Propagated from the session validator or aiohttp response.
            TimeoutError: Propagated from the session validator.
            UnicodeDecodeError: Propagated from the session validator.
            json.JSONDecodeError: Propagated from the session validator.
        """
        observed.response = response
        observed.observation_id = next_observation_id()
        try:
            if callable(validator):
                await validator(response)
            elif validator:
                response.raise_for_status()
        except (
            aiohttp.ClientError,
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            validator_errors.append((error, response))
            raise

    return validate_response, observed, validator_errors


def _classify_outcome_response_error(
    error: aiohttp.ClientResponseError,
    *,
    response_entered: bool,
    observed_response: aiohttp.ClientResponse | None,
    method: str,
    url: str,
    validator_errors: _ValidatorErrors,
) -> _EndpointOutcome | None:
    """Classify middleware or validator errors that carry an HTTP status.

    Args:
        error (aiohttp.ClientResponseError): Raised HTTP response error.
        response_entered (bool): Whether transport received the response context.
        observed_response (aiohttp.ClientResponse | None): Response delivered to the validator.
        method (str): HTTP method of the endpoint request.
        url (str): Raw endpoint request URL passed to aiohttp.
        validator_errors (_ValidatorErrors):
            Errors raised by the session callback and the response being validated.

    Returns:
        _EndpointOutcome | None: Classified status outcome, or ``None`` when the
            error came from response processing or does not match its response.
    """
    if isinstance(error, aiohttp.ClientHttpProxyError) or not 400 <= error.status <= 599:
        return None

    for validator_error, response in validator_errors:
        if validator_error is error:
            if error.status != response.status or not _request_info_matches_response(
                error.request_info, response
            ):
                return None
            outcome = _EndpointOutcome.from_http_status(error.status, error.message)
            if outcome.error is not None:
                outcome.error.__cause__ = error
            return outcome

    if (
        response_entered
        or observed_response is not None
        or _raised_by_aiohttp_trace(error.__traceback__)
        or not _response_error_matches_request(error, method, url)
    ):
        return None

    outcome = _EndpointOutcome.from_http_status(error.status, error.message)
    if outcome.error is not None:
        outcome.error.__cause__ = error
    return outcome


def _raised_by_aiohttp_trace(traceback: TracebackType | None) -> bool:
    """Identify status errors raised by an aiohttp trace callback.

    Trace callbacks can run before dispatch or after a response. A status error
    raised by a trace callback does not establish that an HTTP response was
    delivered or that its status is cacheable.

    Args:
        traceback (TracebackType | None): Traceback attached to the HTTP error.

    Returns:
        bool: Whether an aiohttp trace dispatch frame raised the error.
    """
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") == "aiohttp.tracing" and frame.f_code.co_name.startswith(
            "send_"
        ):
            return True
        traceback = traceback.tb_next
    return False


def _classify_outcome_payload_error(
    error: (
        aiohttp.ClientPayloadError
        | aiohttp.ContentTypeError
        | UnicodeDecodeError
        | json.JSONDecodeError
        | TimeoutError
    ),
    validator_errors: _ValidatorErrors,
) -> _EndpointOutcome | None:
    """Classify validator failures using the response delivered to that validator.

    Args:
        error (aiohttp.ClientPayloadError | aiohttp.ContentTypeError | UnicodeDecodeError | json.JSONDecodeError | TimeoutError):
            Raised response-body, decoding or timeout error.
        validator_errors (_ValidatorErrors): Errors raised while validating a response.

    Returns:
        _EndpointOutcome | None: The HTTP status outcome for a delivered error
            response, or None when the failure is unrelated or the response was successful.
    """
    for validator_error, response in validator_errors:
        if validator_error is error:
            if 400 <= response.status <= 599:
                outcome = _EndpointOutcome.from_http_status(response.status, response.reason)
                if outcome.error is not None:
                    outcome.error.__cause__ = error
                return outcome
            return None
    return None


def _request_info_matches_request(
    request_info: aiohttp.RequestInfo | None, method: str, url: str
) -> bool:
    """Check whether aiohttp request metadata identifies the endpoint request.

    Args:
        request_info (aiohttp.RequestInfo | None): Metadata attached to a response error.
        method (str): Expected HTTP method.
        url (str): Expected URL, including its query string.

    Returns:
        bool: Whether both the method and URL match the expected request.
    """
    if request_info is None:
        return False
    try:
        expected_url = URL(url).with_fragment(None)
    except ValueError:
        return False
    return request_info.method == method and request_info.url.with_fragment(None) == expected_url


def _request_info_matches_response(
    request_info: aiohttp.RequestInfo | None, response: aiohttp.ClientResponse
) -> bool:
    """Check whether validator error metadata identifies its response.

    Args:
        request_info (aiohttp.RequestInfo | None): Metadata attached to the validator error.
        response (aiohttp.ClientResponse): Response passed to the session validator.

    Returns:
        bool: Whether the validator error refers to this response's request.
    """
    response_info = response.request_info
    return (
        request_info is not None
        and response_info is not None
        and request_info.method == response_info.method
        and request_info.url == response_info.url
    )


def _response_error_matches_request(
    error: aiohttp.ClientResponseError, method: str, url: str
) -> bool:
    """Match an HTTP error to the endpoint request that produced it.

    Args:
        error (aiohttp.ClientResponseError): Raised HTTP status error.
        method (str): Expected HTTP method.
        url (str): Expected URL, including its query string.

    Returns:
        bool: Whether the error metadata or initial redirect response identifies the request.
    """
    if error.history:
        return _request_info_matches_request(error.history[0].request_info, method, url)
    return _request_info_matches_request(error.request_info, method, url)


class ClientTransportMixin:
    """Immediate request execution and safe response methods for OPNsenseClient."""

    if TYPE_CHECKING:
        _endpoint_observation_id: int
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
            async with self._session.get(
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

    def _next_endpoint_observation_id(self) -> int:
        """Assign an ordering number shared by endpoint dispatch and response delivery.

        Returns:
            int: Next observation ID for this client.
        """
        self._endpoint_observation_id += 1
        return self._endpoint_observation_id

    async def _do_get_outcome(
        self,
        path: str,
        caller: str = "Unknown",
        *,
        read_body: bool = True,
        response_format: Literal["json", "text"] = "json",
    ) -> _EndpointOutcome:
        """Execute a GET request immediately and classify the response.

        Unlike ``_do_get``, an HTTP or transport failure is returned as an
        outcome instead of being logged and raised, so the endpoint module can
        tell a missing endpoint from a transient failure and apply the
        ``throw_errors`` policy itself. A callable session validator runs through
        aiohttp before request completion for each response. The outcome layer
        classifies an attributable 404 as absent.

        Args:
            path (str): API endpoint path to request.
            caller (str): Caller name used for diagnostics.
            read_body (bool): Whether to decode the body of a successful response.
                ``False`` makes a status-only check: the body is not read and a
                successful response carries no payload.
            response_format (Literal["json", "text"], optional): Decoding mode for a
                successful response body.

        Returns:
            _EndpointOutcome: ``DATA`` with the decoded payload or body text (``None`` when
                ``read_body`` is ``False``) for a successful response,
                ``ABSENT`` for a 404 or 403 response, and ``TRANSIENT`` for any
                other HTTP status, timeout or client error. A successful response
                that fails decoding carries the original decoding error.

        Raises:
            UnicodeDecodeError: For invalid text encoding without an attributable response.
            json.JSONDecodeError: For undecodable JSON without an attributable response.
        """
        dispatch_id = self._next_endpoint_observation_id()
        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        _LOGGER.debug(
            "[get_outcome] path: %s (called by %s)", _redact_url_credentials(path), caller
        )
        raise_for_status, observed, validator_errors = _outcome_raise_for_status(
            self._session, self._next_endpoint_observation_id
        )
        response_entered = False
        successful_response = False
        outcome: _EndpointOutcome | None
        try:
            async with self._session.get(
                url,
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                ssl=self._verify_ssl,
                raise_for_status=raise_for_status,
            ) as response:
                response_entered = True
                successful_response = response.ok
                _LOGGER.debug(
                    "[get_outcome] Response %s: %s",
                    response.status,
                    _redact_url_credentials(str(response.reason)),
                )
                if not response.ok:
                    outcome = _EndpointOutcome.from_http_status(response.status, response.reason)
                elif not read_body:
                    outcome = _EndpointOutcome.data(None)
                elif response_format == "text":
                    outcome = _EndpointOutcome.data(await response.text())
                else:
                    outcome = _EndpointOutcome.data(await response.json(content_type=None))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            outcome = _classify_outcome_payload_error(e, validator_errors)
            if outcome is None:
                if not successful_response and not any(
                    e is validator_error and response.ok
                    for validator_error, response in validator_errors
                ):
                    raise
                outcome = replace(_EndpointOutcome.data(None), decode_error=e)
        except (aiohttp.ClientError, TimeoutError) as e:
            outcome = None
            if isinstance(e, aiohttp.ClientResponseError) and not isinstance(
                e, aiohttp.ContentTypeError
            ):
                outcome = _classify_outcome_response_error(
                    e,
                    response_entered=response_entered,
                    observed_response=observed.response,
                    method="GET",
                    url=url,
                    validator_errors=validator_errors,
                )
            elif isinstance(
                e, aiohttp.ClientPayloadError | aiohttp.ContentTypeError | TimeoutError
            ):
                outcome = _classify_outcome_payload_error(e, validator_errors)
            if outcome is None:
                error = _map_opnsense_exception(e)
                error.__cause__ = e
                outcome = _EndpointOutcome.transient(
                    error, f"{type(e).__name__}: {_redacted_exception_message(e)}"
                )
        finally:
            if not response_entered and observed.response is not None:
                observed.response.close()
        return replace(outcome, observation_id=observed.observation_id or dispatch_id)

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
        outcome instead of being logged and raised, so the endpoint module can
        tell a missing endpoint from a transient failure and apply the
        ``throw_errors`` policy itself. A callable session validator runs through
        aiohttp before request completion for each response. The outcome layer
        classifies an attributable 404 as absent. The request always carries the
        caller's payload; no payload-less probe is sent.

        Args:
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None): Request payload sent to the API endpoint.
            caller (str): Caller name used for diagnostics.

        Returns:
            _EndpointOutcome: ``DATA`` with the decoded payload for a successful response,
                ``ABSENT`` for a 404 or 403 response, and ``TRANSIENT`` for any other
                HTTP status, timeout or client error. A successful response that
                fails decoding carries the original decoding error.

        Raises:
            UnicodeDecodeError: For invalid text encoding without an attributable response.
            json.JSONDecodeError: For undecodable JSON without an attributable response.
        """
        dispatch_id = self._next_endpoint_observation_id()
        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        _LOGGER.debug(
            "[post_outcome] path: %s (called by %s)", _redact_url_credentials(path), caller
        )
        raise_for_status, observed, validator_errors = _outcome_raise_for_status(
            self._session, self._next_endpoint_observation_id
        )
        response_entered = False
        successful_response = False
        outcome: _EndpointOutcome | None
        try:
            async with self._session.post(
                url,
                json=payload,
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                ssl=self._verify_ssl,
                raise_for_status=raise_for_status,
            ) as response:
                response_entered = True
                successful_response = response.ok
                _LOGGER.debug(
                    "[post_outcome] Response %s: %s",
                    response.status,
                    _redact_url_credentials(str(response.reason)),
                )
                if not response.ok:
                    outcome = _EndpointOutcome.from_http_status(response.status, response.reason)
                else:
                    outcome = _EndpointOutcome.data(await response.json(content_type=None))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            outcome = _classify_outcome_payload_error(e, validator_errors)
            if outcome is None:
                if not successful_response and not any(
                    e is validator_error and response.ok
                    for validator_error, response in validator_errors
                ):
                    raise
                outcome = replace(_EndpointOutcome.data(None), decode_error=e)
        except (aiohttp.ClientError, TimeoutError) as e:
            outcome = None
            if isinstance(e, aiohttp.ClientResponseError) and not isinstance(
                e, aiohttp.ContentTypeError
            ):
                outcome = _classify_outcome_response_error(
                    e,
                    response_entered=response_entered,
                    observed_response=observed.response,
                    method="POST",
                    url=url,
                    validator_errors=validator_errors,
                )
            elif isinstance(
                e, aiohttp.ClientPayloadError | aiohttp.ContentTypeError | TimeoutError
            ):
                outcome = _classify_outcome_payload_error(e, validator_errors)
            if outcome is None:
                error = _map_opnsense_exception(e)
                error.__cause__ = e
                outcome = _EndpointOutcome.transient(
                    error, f"{type(e).__name__}: {_redacted_exception_message(e)}"
                )
        finally:
            if not response_entered and observed.response is not None:
                observed.response.close()
        return replace(outcome, observation_id=observed.observation_id or dispatch_id)

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
