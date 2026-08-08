"""aiopnsense package to manage OPNsense."""

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
]
