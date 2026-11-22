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
from .firmware import FirmwareRequirements, get_firmware_requirements

__all__ = [
    "FirmwareRequirements",
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
    "get_firmware_requirements",
]
