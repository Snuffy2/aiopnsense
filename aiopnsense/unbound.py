"""Unbound DNS blocklist methods for OPNsenseClient."""

from collections.abc import MutableMapping
from typing import Any

import aiohttp
import awesomeversion

from ._typing import AiopnsenseClientProtocol
from .const import LEGACY_UNBOUND_BLOCKLIST_FIRMWARE
from .helpers import _LOGGER, _log_errors


class UnboundMixin(AiopnsenseClientProtocol):
    """Unbound DNS blocklist methods for OPNsenseClient."""

    async def _uses_legacy_unbound_blocklist(self) -> bool | None:
        """Return whether firmware requires legacy Unbound DNSBL handling.

        Returns:
            bool | None: ``True`` when firmware is below ``25.7.8``, ``False``
            for newer firmware, or ``None`` when the version cannot be
            compared.
        """
        firmware = await self.get_host_firmware_version()
        if firmware is None:
            _LOGGER.debug(
                "Firmware version unavailable when determining which Unbound Blocklist method to use"
            )
            return None
        try:
            return awesomeversion.AwesomeVersion(firmware) < awesomeversion.AwesomeVersion(
                LEGACY_UNBOUND_BLOCKLIST_FIRMWARE
            )
        except (
            awesomeversion.exceptions.AwesomeVersionCompareException,
            TypeError,
            ValueError,
        ) as err:
            _LOGGER.error(
                "Error comparing firmware version %s when determining which Unbound Blocklist method to use. %s: %s",
                firmware,
                type(err).__name__,
                err,
            )
            return None

    @_log_errors
    async def _get_unbound_blocklist_legacy(self) -> dict[str, Any]:
        """Return legacy Unbound DNSBL settings.

        Returns:
            dict[str, Any]: Normalized legacy DNSBL settings returned by older
            OPNsense firmware.
        """
        response = await self._safe_dict_get("/api/unbound/settings/get")
        unbound_settings = response.get("unbound", {})
        if not isinstance(unbound_settings, MutableMapping):
            return {}

        dnsbl_settings = unbound_settings.get("dnsbl", {})
        if not isinstance(dnsbl_settings, MutableMapping):
            return {}

        dnsbl: dict[str, Any] = {}
        for attr in ("enabled", "safesearch", "nxdomain", "address"):
            dnsbl[attr] = dnsbl_settings.get(attr, "")
        for attr in ("type", "lists", "whitelists", "blocklists", "wildcards"):
            if isinstance(dnsbl_settings.get(attr), MutableMapping):
                dnsbl[attr] = ",".join(
                    key
                    for key, value in dnsbl_settings[attr].items()
                    if isinstance(value, MutableMapping) and value.get("selected", 0) == 1
                )
            else:
                dnsbl[attr] = ""
        return dnsbl

    async def _set_unbound_blocklist_legacy(self, set_state: bool) -> bool:
        """Enable or disable legacy Unbound DNSBL settings.

        Args:
            set_state (bool): Desired enabled state to apply.

        Returns:
            bool: ``True`` when the settings save, DNSBL apply, and service
            restart all succeed; otherwise, ``False``.
        """
        payload: dict[str, Any] = {"unbound": {"dnsbl": await self._get_unbound_blocklist_legacy()}}
        if not payload["unbound"]["dnsbl"]:
            _LOGGER.error("Unable to get Unbound Blocklist Status")
            return False

        payload["unbound"]["dnsbl"]["enabled"] = "1" if set_state else "0"

        try:
            response = await self._post("/api/unbound/settings/set", payload=payload)
        except (TimeoutError, aiohttp.ClientError, ValueError, TypeError) as err:
            _LOGGER.error(
                "Error saving legacy unbound blocklist state %s. %s: %s",
                "On" if set_state else "Off",
                type(err).__name__,
                err,
            )
            return False

        if not isinstance(response, MutableMapping) or response.get("result", "failed") != "saved":
            _LOGGER.error(
                "Unable to save legacy unbound blocklist state %s. Response: %s",
                "On" if set_state else "Off",
                response,
            )
            return False

        try:
            dnsbl_resp = await self._get("/api/unbound/service/dnsbl")
        except (TimeoutError, aiohttp.ClientError, ValueError, TypeError) as err:
            _LOGGER.error(
                "Error applying legacy unbound blocklist state %s. %s: %s",
                "On" if set_state else "Off",
                type(err).__name__,
                err,
            )
            return False

        dnsbl_status = dnsbl_resp.get("status") if isinstance(dnsbl_resp, MutableMapping) else None
        if not isinstance(dnsbl_resp, MutableMapping) or not isinstance(dnsbl_status, str):
            _LOGGER.error(
                "Malformed legacy unbound dnsbl apply response for state %s. Response: %s",
                "On" if set_state else "Off",
                dnsbl_resp,
            )
            return False
        if not dnsbl_status.startswith("OK"):
            _LOGGER.error(
                "Legacy unbound dnsbl apply failed for state %s. Response: %s",
                "On" if set_state else "Off",
                dnsbl_resp,
            )
            return False

        try:
            restart_resp = await self._post("/api/unbound/service/restart")
        except (TimeoutError, aiohttp.ClientError, ValueError, TypeError) as err:
            _LOGGER.error(
                "Error restarting legacy unbound blocklist state %s. %s: %s",
                "On" if set_state else "Off",
                type(err).__name__,
                err,
            )
            return False

        _LOGGER.debug(
            "[_set_unbound_blocklist_legacy] set_state: %s, payload: %s, response: %s, dnsbl_resp: %s, restart_resp: %s",
            "On" if set_state else "Off",
            payload,
            response,
            dnsbl_resp,
            restart_resp,
        )
        return (
            isinstance(restart_resp, MutableMapping)
            and restart_resp.get("response", "failed") == "OK"
        )

    @_log_errors
    async def get_unbound_blocklist(self) -> dict[str, Any]:
        """Return the Unbound Blocklist details.

        Returns:
            dict[str, Any]: Normalized data returned by the related OPNsense endpoint.
        """
        use_legacy = await self._uses_legacy_unbound_blocklist()
        if use_legacy is not False:
            _LOGGER.debug(
                "Getting Unbound regular blocklists for OPNsense < %s or when firmware detection is unavailable",
                LEGACY_UNBOUND_BLOCKLIST_FIRMWARE,
            )
            return {"legacy": await self._get_unbound_blocklist_legacy()}

        dnsbl_endpoint = "/api/unbound/settings/search_dnsbl"
        if not await self.is_endpoint_available(dnsbl_endpoint):
            _LOGGER.debug("Unbound DNSBL endpoint unavailable")
            return {}

        dnsbl_raw = await self._safe_dict_get(dnsbl_endpoint)
        if not isinstance(dnsbl_raw, MutableMapping):
            return {}
        dnsbl_rows = dnsbl_raw.get("rows", [])
        if not isinstance(dnsbl_rows, list) or not len(dnsbl_rows) > 0:
            return {}
        dnsbl_full: dict[str, Any] = {}
        for dnsbl in dnsbl_rows:
            if not isinstance(dnsbl, MutableMapping):
                continue
            # _LOGGER.debug("[get_unbound_blocklist] dnsbl: %s", dnsbl)
            if dnsbl.get("uuid"):
                dnsbl_full.update({dnsbl["uuid"]: dnsbl})
        # _LOGGER.debug("[get_unbound_blocklist] dnsbl_full: %s", dnsbl_full)
        _LOGGER.debug("[get_unbound_blocklist] dnsbl_full length: %s", len(dnsbl_full))
        return dnsbl_full

    async def _toggle_unbound_blocklist(self, set_state: bool, uuid: str | None) -> bool:
        """Enable or disable the unbound blocklist.

        Args:
            set_state (bool): Target alias enabled state.
            uuid (str | None): Unique identifier of the target OPNsense resource.

        Returns:
            bool: True when the toggle operation completes successfully; otherwise, False.
        """
        if not uuid:
            _LOGGER.error("Blocklist uuid must be provided for Unbound Extended Blocklists")
            return False
        endpoint = f"/api/unbound/settings/toggle_dnsbl/{uuid}/{'1' if set_state else '0'}"
        response = await self._safe_dict_post(endpoint)
        result = response.get("result")
        if (set_state and result == "Enabled") or (not set_state and result == "Disabled"):
            try:
                dnsbl_resp = await self._get("/api/unbound/service/dnsbl")
                _LOGGER.debug(
                    "[_toggle_unbound_blocklist] uuid: %s, set_state: %s, response: %s, dnsbl_resp: %s",
                    uuid,
                    "On" if set_state else "Off",
                    response,
                    dnsbl_resp,
                )
                if isinstance(dnsbl_resp, MutableMapping) and dnsbl_resp.get(
                    "status", "failed"
                ).startswith("OK"):
                    return True
            except (TimeoutError, aiohttp.ClientError, ValueError, TypeError) as e:
                _LOGGER.error(
                    "Error applying unbound blocklist change for uuid %s. %s: %s",
                    uuid,
                    type(e).__name__,
                    e,
                )
        return False

    @_log_errors
    async def enable_unbound_blocklist(self, uuid: str | None = None) -> bool:
        """Enable the unbound blocklist.

        Args:
            uuid (str | None, optional): Unique identifier of the target OPNsense resource.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        use_legacy = await self._uses_legacy_unbound_blocklist()
        if use_legacy is True:
            if uuid is not None:
                _LOGGER.error(
                    "Blocklist uuid %s is unsupported for legacy Unbound blocklists on OPNsense < %s",
                    uuid,
                    LEGACY_UNBOUND_BLOCKLIST_FIRMWARE,
                )
                return False
            _LOGGER.debug(
                "Using Unbound regular blocklists for OPNsense < %s",
                LEGACY_UNBOUND_BLOCKLIST_FIRMWARE,
            )
            return await self._set_unbound_blocklist_legacy(set_state=True)
        if use_legacy is False:
            _LOGGER.debug(
                "Using Unbound extended blocklists for OPNsense >= %s",
                LEGACY_UNBOUND_BLOCKLIST_FIRMWARE,
            )
            return await self._toggle_unbound_blocklist(set_state=True, uuid=uuid)

        _LOGGER.debug(
            "Unable to determine Unbound blocklist mode from firmware; using %s fallback",
            "extended" if uuid is not None else "legacy",
        )
        if uuid is not None:
            return await self._toggle_unbound_blocklist(set_state=True, uuid=uuid)
        return await self._set_unbound_blocklist_legacy(set_state=True)

    @_log_errors
    async def disable_unbound_blocklist(self, uuid: str | None = None) -> bool:
        """Disable the unbound blocklist.

        Args:
            uuid (str | None, optional): Unique identifier of the target OPNsense resource.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        use_legacy = await self._uses_legacy_unbound_blocklist()
        if use_legacy is True:
            if uuid is not None:
                _LOGGER.error(
                    "Blocklist uuid %s is unsupported for legacy Unbound blocklists on OPNsense < %s",
                    uuid,
                    LEGACY_UNBOUND_BLOCKLIST_FIRMWARE,
                )
                return False
            _LOGGER.debug(
                "Using Unbound regular blocklists for OPNsense < %s",
                LEGACY_UNBOUND_BLOCKLIST_FIRMWARE,
            )
            return await self._set_unbound_blocklist_legacy(set_state=False)
        if use_legacy is False:
            _LOGGER.debug(
                "Using Unbound extended blocklists for OPNsense >= %s",
                LEGACY_UNBOUND_BLOCKLIST_FIRMWARE,
            )
            return await self._toggle_unbound_blocklist(set_state=False, uuid=uuid)

        _LOGGER.debug(
            "Unable to determine Unbound blocklist mode from firmware; using %s fallback",
            "extended" if uuid is not None else "legacy",
        )
        if uuid is not None:
            return await self._toggle_unbound_blocklist(set_state=False, uuid=uuid)
        return await self._set_unbound_blocklist_legacy(set_state=False)
