"""Classification and logging of failed HTTP responses and transport errors.

The immediate GET and POST, endpoint availability probe and both streaming reads report failures
here. Firmware installation requests use their own polling and error policy. This module maps
reported failures to public exceptions, logs through outage suppression and marks raised
exceptions as already logged. It never decides whether the caller raises or returns an empty value.
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

REQUEST_PHASE = "request"
"""Request phase of immediate GET and POST data calls."""

PROBE_PHASE = "probe"
"""Request phase of endpoint availability probes, which log transient failures as warnings."""

STREAM_PHASE = "stream"
"""Request phase of the streaming reads."""

_PERMISSION_GUIDANCE = (
    "Ensure the OPNsense user connected to HA has appropriate access. Recommend full admin access"
)


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
        phase == STREAM_PHASE
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
        phase (str): Request phase used for outage suppression, such as ``request``,
            ``probe`` or ``stream``.
        operation (str): Name of the request operation used in the log message.
        url (str): Full requested URL used in the log message.
        status (int): HTTP response status.
        reason (str | None): HTTP response reason phrase.
        caller (str | None): Caller name used in the log message, when known.

    Returns:
        OPNsenseConnectionError: Public exception for the response status. The caller
            decides whether to raise it.
    """
    error = _opnsense_http_error(status, reason)
    logged_error = _error_if_throwing(client, error)
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
            url,
            error=logged_error,
            failure_status=_stream_auth_status(phase, error),
        )
    elif phase == PROBE_PHASE:
        _log_request_failure(
            client,
            method,
            path,
            phase,
            logging.WARNING,
            "Transient %s endpoint check failure for %s. Response %s: %s. Not caching result.",
            method.upper(),
            path,
            status,
            reason,
            error=logged_error,
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
            url,
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
        phase (str): Request phase used for outage suppression, such as ``request``,
            ``probe`` or ``stream``.
        operation (str): Name of the request operation used in the log message.
        exc (Exception): Caught aiohttp client error or timeout.
        caller (str | None): Caller name used in the log message, when known.

    Returns:
        OPNsenseError: Public exception mapped from ``exc``. The caller decides whether
            to raise it.
    """
    error = _map_opnsense_exception(exc)
    logged_error = _error_if_throwing(client, error)
    if phase == PROBE_PHASE:
        _log_request_failure(
            client,
            method,
            path,
            phase,
            logging.WARNING,
            "%s endpoint availability check failed for %s. %s: %s. Not caching result.",
            method.upper(),
            path,
            type(exc).__name__,
            _redacted_exception_message(exc),
            error=logged_error,
        )
    else:
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
