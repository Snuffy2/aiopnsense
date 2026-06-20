"""Firmware-related methods for OPNsenseClient."""

from collections.abc import MutableMapping
from datetime import datetime, timedelta
from typing import Any

import awesomeversion
from dateutil.parser import ParserError, UnknownTimezoneWarning, parse

from ._typing import AiopnsenseClientProtocol
from .const import AMBIGUOUS_TZINFOS
from .helpers import _LOGGER, _log_errors, dict_get

FIRMWARE_STATUS_ENDPOINT = "/api/core/firmware/status"
FIRMWARE_CHECK_ENDPOINT = "/api/core/firmware/check"
FIRMWARE_UPGRADE_STATUS_ENDPOINT = "/api/core/firmware/upgradestatus"
FIRMWARE_ACTION_ENDPOINT_PREFIX = "/api/core/firmware/"
FIRMWARE_CHANGELOG_ENDPOINT_PREFIX = "/api/core/firmware/changelog/"


class FirmwareMixin(AiopnsenseClientProtocol):
    """Firmware methods for OPNsenseClient."""

    _firmware_version: str | None

    async def _store_host_firmware_version(self) -> None:
        """Cache the installed OPNsense firmware version or product series."""
        if not await self.is_get_endpoint_available(FIRMWARE_STATUS_ENDPOINT):
            _LOGGER.debug("Firmware status endpoint unavailable")
            self._firmware_version = None
            return

        firmware_info = await self._safe_dict_get(FIRMWARE_STATUS_ENDPOINT)
        firmware: str | None = dict_get(firmware_info, "product.product_version")
        if not firmware or not awesomeversion.AwesomeVersion(firmware).valid:
            old = firmware
            firmware = dict_get(firmware_info, "product.product_series", old)
            if firmware != old:
                _LOGGER.debug(
                    "[get_host_firmware_version] firmware: %s not valid SemVer, using %s",
                    old,
                    firmware,
                )
        else:
            _LOGGER.debug("[get_host_firmware_version] firmware: %s", firmware)
        self._firmware_version = firmware

    @_log_errors
    async def get_host_firmware_version(self) -> None | str:
        """Return the cached OPNsense firmware version.

        Returns:
            None | str: Installed firmware version, falling back to the product
                series for non-SemVer version strings, or ``None`` when the
                firmware status endpoint is unavailable.
        """
        if self._firmware_version is None:
            await self._store_host_firmware_version()
        return self._firmware_version

    @_log_errors
    async def get_firmware_update_info(self) -> MutableMapping[str, Any]:
        """Return firmware status and trigger a refresh when cached data is stale.

        Returns:
            MutableMapping[str, Any]: Firmware status payload from OPNsense,
                including product version, latest version, check status, and
                update metadata when available. Returns an empty mapping when
                the firmware status endpoint is unavailable.
        """
        if not await self.is_get_endpoint_available(FIRMWARE_STATUS_ENDPOINT):
            _LOGGER.debug("Firmware status endpoint unavailable")
            return {}

        status = await self._safe_dict_get(FIRMWARE_STATUS_ENDPOINT)

        # if error or too old trigger check (only if check is not already in progress)
        # {'status_msg': 'Firmware status check was aborted internally. Please try again.', 'status': 'error'}
        # error could be because data has not been refreshed at all OR an upgrade is currently in progress
        if error_status := bool(status.get("status") == "error"):
            _LOGGER.debug("Last firmware status check returned an error")

        product_version = dict_get(status, "product.product_version")
        product_latest = dict_get(status, "product.product_latest")
        missing_data = False
        if (
            not product_version
            or not product_latest
            or not isinstance(dict_get(status, "product.product_check"), MutableMapping)
            or not dict_get(status, "product.product_check")
        ):
            _LOGGER.debug("Missing data in firmware status")
            missing_data = True

        update_needs_info = False
        try:
            if (
                awesomeversion.AwesomeVersion(product_latest)
                > awesomeversion.AwesomeVersion(product_version)
                and status.get("status_msg", "").strip()
                == "There are no updates available on the selected mirror."
            ):
                _LOGGER.debug("Update available but missing details")
                update_needs_info = True
        except (
            awesomeversion.exceptions.AwesomeVersionCompareException,
            TypeError,
            ValueError,
        ) as e:
            _LOGGER.debug("Error checking firmware versions. %s: %s", type(e).__name__, e)
            update_needs_info = True

        last_check_str = status.get("last_check")
        last_check_expired = True
        if last_check_str:
            try:
                last_check_dt = parse(last_check_str, tzinfos=AMBIGUOUS_TZINFOS)
                if last_check_dt.tzinfo is None:
                    opnsense_tz = await self._get_opnsense_timezone()
                    last_check_dt = last_check_dt.replace(tzinfo=opnsense_tz)
                last_check_expired = (datetime.now().astimezone() - last_check_dt) > timedelta(
                    days=1
                )
                if last_check_expired:
                    _LOGGER.debug("Firmware status last check > 1 day ago")
            except (ValueError, TypeError, ParserError, UnknownTimezoneWarning) as e:
                _LOGGER.debug(
                    "Error getting firmware status last check. %s: %s", type(e).__name__, e
                )
        else:
            _LOGGER.debug("Firmware status last check is missing")

        if error_status or last_check_expired or missing_data or update_needs_info:
            _LOGGER.info("Triggering firmware check")
            self._firmware_version = None
            await self._post(FIRMWARE_CHECK_ENDPOINT)

        return status

    @_log_errors
    async def upgrade_firmware(self, type: str = "update") -> MutableMapping[str, Any] | None:
        """Trigger a firmware upgrade.

        Args:
            type (str): Firmware action to trigger. ``update`` applies minor
                updates on the current series, while ``upgrade`` starts a major
                series upgrade.

        Returns:
            MutableMapping[str, Any] | None: Firmware action response for
                supported action types, or ``None`` when ``type`` is not
                ``update`` or ``upgrade``.
        """
        # update = minor updates of the same opnsense version
        # upgrade = major updates to a new opnsense version
        if type in ("update", "upgrade"):
            self._firmware_version = None
            return await self._safe_dict_post(f"{FIRMWARE_ACTION_ENDPOINT_PREFIX}{type}")
        return None

    @_log_errors
    async def upgrade_status(self) -> MutableMapping[str, Any]:
        """Return the status of the active firmware upgrade.

        Returns:
            MutableMapping[str, Any]: Upgrade status payload, or an empty
                mapping when the upgrade-status endpoint is unavailable.
        """
        if not await self.is_get_endpoint_available(FIRMWARE_UPGRADE_STATUS_ENDPOINT):
            _LOGGER.debug("Firmware upgrade status endpoint unavailable")
            return {}
        return await self._safe_dict_get(FIRMWARE_UPGRADE_STATUS_ENDPOINT)

    @_log_errors
    async def firmware_changelog(self, version: str) -> MutableMapping[str, Any]:
        """Return the changelog for the firmware upgrade.

        Args:
            version (str): Firmware version whose changelog should be fetched.

        Returns:
            MutableMapping[str, Any]: Changelog response for the requested
                firmware version.
        """
        return await self._safe_dict_post(f"{FIRMWARE_CHANGELOG_ENDPOINT_PREFIX}{version}")
