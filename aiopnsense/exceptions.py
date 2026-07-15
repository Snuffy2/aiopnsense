"""Custom exceptions and exception mapping for aiopnsense."""

import re
from urllib.parse import urlsplit

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


_URL_AUTHORITY_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s\"'<>`]+")


def _fallback_redact_userinfo(url: str) -> str:
    """Redact userinfo from a URL-like value by authority boundary parsing.

    This fallback path is used when ``urlsplit`` cannot parse a malformed
    authority reliably.

    Args:
        url (str): URL-like string that may contain userinfo in authority.

    Returns:
        str: URL with userinfo redacted if present; otherwise the input unchanged.
    """
    scheme_sep = url.find("://")
    if scheme_sep < 0:
        return url

    authority_start = scheme_sep + 3
    authority_and_rest = url[authority_start:]
    if not authority_and_rest:
        return url

    delimiters = ("?", "#")
    authority_end = len(authority_and_rest)
    for delimiter in delimiters:
        delimiter_pos = authority_and_rest.find(delimiter)
        if 0 <= delimiter_pos < authority_end:
            authority_end = delimiter_pos
    path_sep = authority_and_rest.find("/")
    if path_sep != -1 and path_sep < authority_end:
        authority_end = path_sep

    authority = authority_and_rest[:authority_end]
    rest = authority_and_rest[authority_end:]

    _, _, hostpart = authority.rpartition("@")
    if not hostpart:
        return url

    userinfo = authority[: -len(hostpart) - 1]
    if not userinfo:
        return url

    redacted_userinfo = "<redacted>:<redacted>" if ":" in userinfo else "<redacted>"
    redacted_authority = f"{redacted_userinfo}@{hostpart}"
    return url[:authority_start] + redacted_authority + rest


def _redact_url_userinfo(message: str) -> str:
    """Redact credentials in URL userinfo while preserving non-secret context.

    Args:
        message (str): Message that may contain one or more URL-like values.

    Returns:
        str: Message with all matching URL userinfo values redacted.
    """

    def _redact(match: re.Match[str]) -> str:
        url = match.group(0)
        try:
            parsed = urlsplit(url)
        except ValueError:
            return _fallback_redact_userinfo(url)

        if parsed.username is None:
            return url

        _, _, hostpart = parsed.netloc.rpartition("@")
        if not hostpart:
            return url

        redacted_userinfo = "<redacted>" if parsed.password is None else "<redacted>:<redacted>"
        return parsed._replace(netloc=f"{redacted_userinfo}@{hostpart}").geturl()

    return _URL_AUTHORITY_PATTERN.sub(_redact, message)


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
