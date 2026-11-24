"""aiopnsense package to manage OPNsense."""

from .access import Access, AccessDict, AccessList, result_access
from .client import OPNsenseClient
from .exceptions import (
    OPNsenseBelowMinFirmware,
    OPNsenseConnectionError,
    OPNsenseError,
    OPNsenseInvalidArgument,
    OPNsenseInvalidAuth,
    OPNsenseInvalidURL,
    OPNsenseMissingDeviceUniqueID,
    OPNsensePrivilegeMissing,
    OPNsenseSSLError,
    OPNsenseTimeoutError,
    OPNsenseUnknownFirmware,
    OPNsenseVoucherServerError,
)

__all__ = [
    "Access",
    "AccessDict",
    "AccessList",
    "OPNsenseBelowMinFirmware",
    "OPNsenseClient",
    "OPNsenseConnectionError",
    "OPNsenseError",
    "OPNsenseInvalidArgument",
    "OPNsenseInvalidAuth",
    "OPNsenseInvalidURL",
    "OPNsenseMissingDeviceUniqueID",
    "OPNsensePrivilegeMissing",
    "OPNsenseSSLError",
    "OPNsenseTimeoutError",
    "OPNsenseUnknownFirmware",
    "OPNsenseVoucherServerError",
    "result_access",
]
