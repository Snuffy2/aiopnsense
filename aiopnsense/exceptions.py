"""Custom exceptions and exception mapping for aiopnsense."""

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


def _invalid_url_error_message() -> str:
    """Return a constant-safe invalid URL message."""
    return "Invalid OPNsense URL"


def _map_opnsense_exception(error: Exception) -> OPNsenseError:
    """Map an arbitrary library failure to the public OPNsense exception hierarchy.

    Args:
        error (Exception): Exception raised by an aiopnsense operation.

    Returns:
        OPNsenseError: Existing or newly mapped public exception.
    """
    if isinstance(error, OPNsenseError):
        return error
    if isinstance(error, aiohttp.InvalidURL):
        return OPNsenseInvalidURL(_invalid_url_error_message())
    if isinstance(error, aiohttp.ClientConnectorDNSError):
        return OPNsenseInvalidURL(str(error))
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
