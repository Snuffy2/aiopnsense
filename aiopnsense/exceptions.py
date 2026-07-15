"""Custom exceptions and exception mapping for aiopnsense."""

import re

import aiohttp


class OPNsenseError(Exception):
    """Base exception for aiopnsense errors."""


class OPNsenseConnectionError(OPNsenseError):
    """Base exception for OPNsense connection failures."""

    def __init__(self, message: str = "", *, status: int | None = None) -> None:
        """Initialize an OPNsense connection failure.

        Args:
            message (str): Human-readable failure detail.
            status (int | None): HTTP status associated with the failure, when available.
        """
        super().__init__(message)
        self.status = status


class OPNsenseTimeoutError(OPNsenseConnectionError):
    """Raised when a request to OPNsense times out."""


class OPNsenseSSLError(OPNsenseConnectionError):
    """Raised when an SSL error occurs during communication with OPNsense."""


class OPNsenseInvalidURL(OPNsenseConnectionError):
    """Raised when an OPNsense URL is invalid."""


class OPNsenseInvalidAuth(OPNsenseConnectionError):
    """Raised when OPNsense authentication fails."""


class OPNsensePrivilegeMissing(OPNsenseConnectionError):
    """Raised when the authenticated user lacks required privileges."""


class OPNsenseBelowMinFirmware(OPNsenseError):
    """Raised when the detected firmware is below the supported minimum."""


class OPNsenseMissingDeviceUniqueID(OPNsenseError):
    """Raised when no device unique ID can be derived from OPNsense."""


class OPNsenseVoucherServerError(OPNsenseError):
    """Error from Voucher Server."""


class OPNsenseUnknownFirmware(OPNsenseError):
    """Unknown current firmware version."""


class OPNsenseInvalidArgument(OPNsenseError, TypeError):
    """Raised when an aiopnsense argument has an invalid type or value."""


_URL_SCHEME_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://")


def _redact_url_userinfo(message: str) -> str:
    """Redact credentials in URL userinfo while preserving non-secret context.

    The strategy is conservative and line-based: for each URL scheme on a line,
    redact everything from the start of authority up to the last ``@`` before path
    delimiters. This avoids malformed parse behavior while ensuring raw userinfo
    never appears in logs or mapped exceptions.

    Args:
        message (str): Message that may contain one or more URL-like values.

    Returns:
        str: Message with all matching URL userinfo values redacted.
    """

    def _redact_line(line: str) -> str:
        cursor = 0
        parts: list[str] = []
        while True:
            scheme_match = _URL_SCHEME_PATTERN.search(line, cursor)
            if not scheme_match:
                parts.append(line[cursor:])
                return "".join(parts)

            parts.append(line[cursor : scheme_match.start()])

            search_start = scheme_match.end()
            search_end = len(line)
            for separator in ("/", "?", "#"):
                separator_pos = line.find(separator, search_start)
                if separator_pos != -1:
                    search_end = min(search_end, separator_pos)

            authority = line[search_start:search_end]
            at_sign = authority.rfind("@")
            if at_sign < 0:
                parts.append(line[scheme_match.start() : search_end])
                cursor = search_end
                continue

            userinfo = authority[:at_sign]
            if not userinfo:
                parts.append(line[scheme_match.start() : search_end])
                cursor = search_end
                continue

            redacted_userinfo = "<redacted>:<redacted>" if ":" in userinfo else "<redacted>"
            parts.append(line[scheme_match.start() : search_start])
            parts.append(redacted_userinfo)
            cursor = search_start + at_sign

    return "".join(_redact_line(line) for line in message.splitlines(keepends=True))


def _map_opnsense_exception(error: Exception) -> OPNsenseError:
    """Map an arbitrary library failure to the public OPNsense exception hierarchy.

    Args:
        error (Exception): Exception raised by an aiopnsense operation.

    Returns:
        OPNsenseError: Existing or newly mapped public exception.
    """
    if isinstance(error, OPNsenseError):
        return error
    if isinstance(error, (aiohttp.ClientConnectorDNSError, aiohttp.InvalidURL)):
        return OPNsenseInvalidURL(_redact_url_userinfo(str(error)))
    if isinstance(error, aiohttp.ClientSSLError):
        return OPNsenseSSLError(str(error))
    if isinstance(error, (TimeoutError, aiohttp.ServerTimeoutError)):
        return OPNsenseTimeoutError(str(error))
    if isinstance(error, aiohttp.ClientResponseError):
        return _opnsense_http_error(error.status, error.message)
    if isinstance(error, aiohttp.ClientError):
        return OPNsenseConnectionError(str(error))
    return OPNsenseError(str(error))


def _opnsense_http_error(status: int, reason: str | None = None) -> OPNsenseConnectionError:
    """Build a public OPNsense exception for an HTTP response failure.

    Args:
        status (int): HTTP response status.
        reason (str | None): Optional HTTP response reason.

    Returns:
        OPNsenseConnectionError: Status-specific public connection exception.
    """
    message = f"HTTP Status Error: {status}"
    if reason:
        message = f"{message} {reason}"
    if status == 401:
        return OPNsenseInvalidAuth(message, status=status)
    if status == 403:
        return OPNsensePrivilegeMissing(message, status=status)
    return OPNsenseConnectionError(message, status=status)
