"""aiopnsense package to manage OPNsense."""

from .client import OPNsenseClient
from ._typing import CategoryResult, CategoryState
from .exceptions import (
    OPNsenseBelowMinFirmware,
    OPNsenseConnectionError,
    OPNsenseError,
    OPNsenseInvalidAuth,
    OPNsenseInvalidArgument,
    OPNsenseInvalidURL,
    OPNsenseMissingDeviceUniqueID,
    OPNsensePrivilegeMissing,
    OPNsenseSSLError,
    OPNsenseTimeoutError,
    OPNsenseUnknownFirmware,
    OPNsenseVoucherServerError,
)

__all__ = [
    "CategoryResult",
    "CategoryState",
    "OPNsenseBelowMinFirmware",
    "OPNsenseClient",
    "OPNsenseConnectionError",
    "OPNsenseError",
    "OPNsenseInvalidAuth",
    "OPNsenseInvalidArgument",
    "OPNsenseInvalidURL",
    "OPNsenseMissingDeviceUniqueID",
    "OPNsensePrivilegeMissing",
    "OPNsenseSSLError",
    "OPNsenseTimeoutError",
    "OPNsenseUnknownFirmware",
    "OPNsenseVoucherServerError",
]
