"""Classification and logging of failed HTTP responses and transport errors.

The immediate GET and POST and both streaming reads report failures here. Endpoint outcomes
apply their own status and cache policy using the same outage logging helpers. Firmware
installation requests use their own polling and error policy. This module maps reported
failures to public exceptions, suppresses repeated logs and marks raised exceptions as
already logged. Callers decide whether to raise or return an empty value.
"""

import logging
from typing import Protocol

from ._error_policy import _error_if_throwing, _ThrowErrorsClient
from .exceptions import (
    OPNsenseConnectionError,
    OPNsenseError,
    _map_opnsense_exception,
    _opnsense_http_error,
)
from .helpers import _log_request_failure, _redacted_exception_message, _RequestLoggingClient

_REQUEST_PHASE = "request"
"""Request phase of immediate GET and POST data calls."""

_PROBE_PHASE = "probe"
"""Request phase of endpoint outcome reads and status checks."""

_STREAM_PHASE = "stream"
"""Request phase of the streaming reads."""

_PERMISSION_GUIDANCE = (
    "Ensure the OPNsense user connected to HA has appropriate access. Recommend full admin access"
)


def _request_log_url(url: str) -> str:
    """Remove URL user information before including a request URL in a log message.

    Args:
        url (str): Request URL that may contain credentials.

    Returns:
        str: URL with any user information omitted.
    """
    scheme, separator, location = url.partition("://")
    authority, slash, endpoint = location.partition("/")
    host = authority.rsplit("@", 1)[-1]
    return f"{scheme}{separator}{host}{slash}{endpoint}"


class _RequestFailureClient(_RequestLoggingClient, _ThrowErrorsClient, Protocol):
    """Client state used by the request failure reporter."""


def _stream_auth_status(phase: str, error: OPNsenseError) -> int | None:
    """Distinguish stream authentication and privilege failures from other failures.

    Args:
        phase (str): Request phase used for outage suppression.
        error (OPNsenseError): Classified request failure.

    Returns:
        int | None: The authentication or privilege status for a stream failure,
            or ``None`` for the standard outage classification.
    """
    if (
        phase == _STREAM_PHASE
        and isinstance(error, OPNsenseConnectionError)
        and error.status in (401, 403)
    ):
        return error.status
    return None


def _report_http_failure(
    client: _RequestFailureClient,
    *,
    method: str,
    path: str,
    phase: str,
    operation: str,
    url: str,
    status: int,
    reason: str | None,
    caller: str | None = None,
) -> OPNsenseConnectionError:
    """Classify and log a non-successful HTTP response.

    Args:
        client (_RequestFailureClient): Client holding the outage state and error policy setting.
        method (str): HTTP method used by the request.
        path (str): Requested API path.
        phase (str): Request phase used for outage suppression, ``request`` or ``stream``.
        operation (str): Name of the request operation used in the log message.
        url (str): Requested URL whose credentials are omitted from the log.
        status (int): HTTP response status.
        reason (str | None): HTTP response reason phrase.
        caller (str | None): Caller name used in the log message, when known.

    Returns:
        OPNsenseConnectionError: Public exception for the response status. The caller
            decides whether to raise it.
    """
    error = _opnsense_http_error(status, reason)
    logged_error = _error_if_throwing(client, error)
    logged_url = _request_log_url(url)
    origin = "" if caller is None else " (called by %s)"
    origin_args: tuple[str, ...] = () if caller is None else (caller,)
    if status == 403:
        _log_request_failure(
            client,
            method,
            path,
            phase,
            logging.ERROR,
            "Permission Error in " + operation + origin + ". Path: %s. " + _PERMISSION_GUIDANCE,
            *origin_args,
            logged_url,
            error=logged_error,
            failure_status=_stream_auth_status(phase, error),
        )
    else:
        _log_request_failure(
            client,
            method,
            path,
            phase,
            logging.ERROR,
            "Error in " + operation + origin + ". Path: %s. Response %s: %s",
            *origin_args,
            logged_url,
            status,
            reason,
            error=logged_error,
            failure_status=_stream_auth_status(phase, error),
        )
    return error


def _report_transport_failure(
    client: _RequestFailureClient,
    *,
    method: str,
    path: str,
    phase: str,
    operation: str,
    exc: Exception,
    caller: str | None = None,
) -> OPNsenseError:
    """Classify and log an aiohttp client error or timeout.

    Args:
        client (_RequestFailureClient): Client holding the outage state and error policy setting.
        method (str): HTTP method used by the request.
        path (str): Requested API path.
        phase (str): Request phase used for outage suppression, ``request`` or ``stream``.
        operation (str): Name of the request operation used in the log message.
        exc (Exception): Caught aiohttp client error or timeout.
        caller (str | None): Caller name used in the log message, when known.

    Returns:
        OPNsenseError: Public exception mapped from ``exc``. The caller decides whether
            to raise it.
    """
    error = _map_opnsense_exception(exc)
    logged_error = _error_if_throwing(client, error)
    origin = "" if caller is None else " (called by %s)"
    origin_args: tuple[str, ...] = () if caller is None else (caller,)
    _log_request_failure(
        client,
        method,
        path,
        phase,
        logging.ERROR,
        "Client error in " + operation + origin + ". Path: %s. %s: %s",
        *origin_args,
        path,
        type(exc).__name__,
        _redacted_exception_message(exc),
        error=logged_error,
        failure_status=_stream_auth_status(phase, error),
    )
    return error
