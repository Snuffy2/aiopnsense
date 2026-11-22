"""Classification and logging of failed HTTP responses and transport errors.

Every HTTP request path (immediate GET and POST, the endpoint availability probe and both
streaming reads) reports a failure here. The module maps the failure to the public exception,
logs it through outage suppression and marks that exception as already logged. It never decides
whether the caller raises the exception or returns an empty value.
"""

import logging

from .exceptions import (
    OPNsenseConnectionError,
    OPNsenseError,
    _map_opnsense_exception,
    _opnsense_http_error,
)
from .helpers import _log_request_failure, _RequestLoggingClient

REQUEST_PHASE = "request"
"""Request phase of immediate GET and POST data calls."""

PROBE_PHASE = "probe"
"""Request phase of endpoint availability probes, which log transient failures as warnings."""

STREAM_PHASE = "stream"
"""Request phase of the streaming reads."""

_PERMISSION_GUIDANCE = (
    "Ensure the OPNsense user connected to HA has appropriate access. Recommend full admin access"
)


def _report_http_failure(
    client: _RequestLoggingClient,
    *,
    method: str,
    path: str,
    phase: str,
    operation: str,
    url: str,
    status: int,
    reason: str | None,
    will_raise: bool,
    caller: str | None = None,
) -> OPNsenseConnectionError:
    """Classify and log a non-successful HTTP response.

    Args:
        client (_RequestLoggingClient): Client holding request outage state.
        method (str): HTTP method used by the request.
        path (str): Requested API path.
        phase (str): Request phase used for outage suppression, such as ``request``,
            ``probe`` or ``stream``.
        operation (str): Name of the request operation used in the log message.
        url (str): Full requested URL used in the log message.
        status (int): HTTP response status.
        reason (str | None): HTTP response reason phrase.
        will_raise (bool): Whether the caller raises the returned exception, so it is
            marked as already logged.
        caller (str | None): Caller name used in the log message, when known.

    Returns:
        OPNsenseConnectionError: Public exception for the response status. The caller
            decides whether to raise it.
    """
    error = _opnsense_http_error(status, reason)
    logged_error = error if will_raise else None
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
        )
    return error


def _report_transport_failure(
    client: _RequestLoggingClient,
    *,
    method: str,
    path: str,
    phase: str,
    operation: str,
    url: str,
    exc: Exception,
    will_raise: bool,
    caller: str | None = None,
) -> OPNsenseError:
    """Classify and log an aiohttp client error or timeout.

    Args:
        client (_RequestLoggingClient): Client holding request outage state.
        method (str): HTTP method used by the request.
        path (str): Requested API path.
        phase (str): Request phase used for outage suppression, such as ``request``,
            ``probe`` or ``stream``.
        operation (str): Name of the request operation used in the log message.
        url (str): Full requested URL used in the log message.
        exc (Exception): Caught aiohttp client error or timeout.
        will_raise (bool): Whether the caller raises the returned exception, so it is
            marked as already logged.
        caller (str | None): Caller name used in the log message, when known.

    Returns:
        OPNsenseError: Public exception mapped from ``exc``. The caller decides whether
            to raise it.
    """
    error = _map_opnsense_exception(exc)
    logged_error = error if will_raise else None
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
            exc,
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
            url,
            type(exc).__name__,
            exc,
            error=logged_error,
        )
    return error
