"""Custom exceptions for aiopnsense."""


class OPNsenseError(Exception):
    """Base exception for aiopnsense errors."""


class OPNsenseConnectionError(OPNsenseError):
    """Base exception for OPNsense connection failures."""


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


class OPNsenseVoucherServerError(OPNsenseError):
    """Error from Voucher Server."""


class OPNsenseUnknownFirmware(OPNsenseError):
    """Unknown current firmware version."""
