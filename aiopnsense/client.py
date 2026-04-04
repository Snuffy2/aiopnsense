"""Public OPNsense client class composed from domain mixins."""

from types import TracebackType
from typing import Self

import aiohttp
import awesomeversion

from .client_base import ClientBaseMixin
from .const import OPNSENSE_LTD_FIRMWARE, OPNSENSE_MIN_FIRMWARE
from .dhcp import DHCPMixin
from .exceptions import (
    OPNsenseBelowMinFirmware,
    OPNsenseConnectionError,
    OPNsenseInvalidAuth,
    OPNsenseInvalidURL,
    OPNsensePrivilegeMissing,
    OPNsenseSSLError,
    OPNsenseTimeoutError,
    OPNsenseUnknownFirmware,
)
from .firewall import FirewallMixin
from .firmware import FirmwareMixin
from .helpers import _LOGGER
from .services import ServicesMixin
from .speedtest import SpeedtestMixin
from .system import SystemMixin
from .telemetry import TelemetryMixin
from .unbound import UnboundMixin
from .vnstat import VnstatMixin
from .vouchers import VouchersMixin
from .vpn import VPNMixin


class OPNsenseClient(
    ClientBaseMixin,
    FirmwareMixin,
    FirewallMixin,
    DHCPMixin,
    ServicesMixin,
    SpeedtestMixin,
    SystemMixin,
    UnboundMixin,
    VouchersMixin,
    TelemetryMixin,
    VnstatMixin,
    VPNMixin,
):
    """Async client for supported OPNsense REST endpoints."""

    async def __aenter__(self) -> Self:
        """Validate the client before entering an async context manager.

        Returns:
            Self: Validated client instance.
        """
        await self.validate()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close background resources when leaving an async context manager.

        Args:
            exc_type (type[BaseException] | None): Exception type raised in the context block.
            exc (BaseException | None): Exception instance raised in the context block.
            traceback (TracebackType | None): Traceback for an exception raised in the context block.
        """
        del exc_type, exc, traceback
        await self.async_close()

    async def validate(self) -> None:
        """Validate connectivity, authentication, and minimum firmware support.

        Raises:
            OPNsenseInvalidURL: Raised when the configured URL is invalid.
            OPNsenseSSLError: Raised when the TLS handshake fails.
            OPNsenseTimeoutError: Raised when validation requests time out.
            OPNsenseInvalidAuth: Raised when API authentication fails.
            OPNsensePrivilegeMissing: Raised when the API user lacks privileges.
            OPNsenseConnectionError: Raised when another client connection error occurs.
            OPNsenseUnknownFirmware: Raised when firmware detection returns no version.
            OPNsenseBelowMinFirmware: Raised when the detected firmware is unsupported.
        """
        orig_throw_errors = self._throw_errors
        self._throw_errors = True
        try:
            try:
                fw_ver = await self.get_host_firmware_version()
            except (aiohttp.ClientConnectorDNSError, aiohttp.InvalidURL) as e:
                raise OPNsenseInvalidURL from e
            except aiohttp.ClientSSLError as e:
                raise OPNsenseSSLError from e
            except (TimeoutError, aiohttp.ServerTimeoutError) as e:
                raise OPNsenseTimeoutError from e
            except aiohttp.ClientResponseError as e:
                if e.status == 401:
                    raise OPNsenseInvalidAuth from e
                if e.status == 403:
                    raise OPNsensePrivilegeMissing from e
                raise OPNsenseConnectionError from e
            except aiohttp.ClientError as e:
                raise OPNsenseConnectionError from e

            if fw_ver is None:
                raise OPNsenseUnknownFirmware

            try:
                if awesomeversion.AwesomeVersion(fw_ver) < awesomeversion.AwesomeVersion(
                    OPNSENSE_MIN_FIRMWARE
                ):
                    msg = (
                        f"OPNsense Firmware {fw_ver} detected. "
                        f"aiopnsense requires OPNsense Firmware >= {OPNSENSE_MIN_FIRMWARE}"
                    )
                    raise OPNsenseBelowMinFirmware(msg)

                if awesomeversion.AwesomeVersion(fw_ver) < awesomeversion.AwesomeVersion(
                    OPNSENSE_LTD_FIRMWARE
                ):
                    _LOGGER.warning(
                        "OPNsense Firmware of %s is below the recommended >= %s. aiopnsense will work, but there may be some missing features.",
                        fw_ver,
                        OPNSENSE_LTD_FIRMWARE,
                    )
            except (
                awesomeversion.exceptions.AwesomeVersionCompareException,
                TypeError,
                ValueError,
            ) as err:
                raise OPNsenseUnknownFirmware from err
        finally:
            self._throw_errors = orig_throw_errors
