"""Unbound DNS blocklist methods for OPNsenseClient."""

from collections.abc import MutableMapping
import logging
from typing import Any

from ._error_policy import _log_errors
from ._typing import AiopnsenseClientProtocol
from .client_toggle import ToggleSpec, ToggleStep, ToggleTarget
from .const import LEGACY_UNBOUND_BLOCKLIST_FIRMWARE
from .helpers import _LOGGER, api_value_matches, firmware_is_at_least

UNBOUND_SETTINGS_GET_ENDPOINT = "/api/unbound/settings/get"
UNBOUND_SETTINGS_SET_ENDPOINT = "/api/unbound/settings/set"
UNBOUND_SERVICE_DNSBL_ENDPOINT = "/api/unbound/service/dnsbl"
UNBOUND_SERVICE_RESTART_ENDPOINT = "/api/unbound/service/restart"
UNBOUND_SETTINGS_SEARCH_DNSBL_ENDPOINT = "/api/unbound/settings/search_dnsbl"
UNBOUND_SETTINGS_TOGGLE_DNSBL_ENDPOINT_PREFIX = "/api/unbound/settings/toggle_dnsbl/"


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
        uses_extended_blocklist = firmware_is_at_least(firmware, LEGACY_UNBOUND_BLOCKLIST_FIRMWARE)
        if uses_extended_blocklist is not None:
            return not uses_extended_blocklist
        _LOGGER.error(
            "Error comparing firmware version %s when determining which Unbound Blocklist method to use",
            firmware,
        )
        return None

    @_log_errors
    async def _get_unbound_blocklist_legacy(
        self, *, require_complete: bool = False
    ) -> dict[str, Any]:
        """Return legacy Unbound DNSBL settings.

        Args:
            require_complete (bool): Require every field sent by the legacy settings write.

        Returns:
            dict[str, Any]: Normalized legacy DNSBL settings returned by older
            OPNsense firmware, or an empty mapping if the required settings are missing.
        """
        response = await self._safe_dict_get(UNBOUND_SETTINGS_GET_ENDPOINT)
        unbound_settings = response.get("unbound", {})
        if not isinstance(unbound_settings, MutableMapping):
            return {}

        dnsbl_settings = unbound_settings.get("dnsbl", {})
        if not isinstance(dnsbl_settings, MutableMapping):
            return {}

        scalar_fields = ("enabled", "safesearch", "nxdomain", "address")
        selector_fields = ("type", "lists", "whitelists", "blocklists", "wildcards")
        if require_complete and any(
            field not in dnsbl_settings for field in (*scalar_fields, *selector_fields)
        ):
            return {}

        dnsbl: dict[str, Any] = {}
        for attr in scalar_fields:
            dnsbl[attr] = dnsbl_settings.get(attr, "")
        for attr in selector_fields:
            if isinstance(dnsbl_settings.get(attr), MutableMapping):
                dnsbl[attr] = ",".join(
                    key
                    for key, value in dnsbl_settings[attr].items()
                    if isinstance(value, MutableMapping)
                    and api_value_matches(value.get("selected", 0), "1")
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
            restart all succeed; otherwise, ``False``. Incomplete settings
            prevent the save. When ``throw_errors`` is enabled, a failed
            read, save, apply or restart request raises an
            ``OPNsenseError`` subclass instead of returning ``False``.
        """
        dnsbl = await self._get_unbound_blocklist_legacy(require_complete=True)
        if not dnsbl:
            _LOGGER.error("Unable to get Unbound Blocklist Status")
            return False

        dnsbl["enabled"] = "1" if set_state else "0"
        payload = {"unbound": {"dnsbl": dnsbl}}
        return await self._run_toggle(
            ToggleSpec(
                label="enable_unbound_blocklist" if set_state else "disable_unbound_blocklist",
                target=ToggleTarget.ON if set_state else ToggleTarget.OFF,
                save=(
                    ToggleStep.save(
                        UNBOUND_SETTINGS_SET_ENDPOINT,
                        frozenset({"saved"}),
                        payload=payload,
                        failure_log_level=logging.ERROR,
                    ),
                ),
                apply=(
                    ToggleStep.apply(
                        UNBOUND_SERVICE_DNSBL_ENDPOINT,
                        method="GET",
                        failure_log_level=logging.ERROR,
                    ),
                    ToggleStep.apply(UNBOUND_SERVICE_RESTART_ENDPOINT, response_key="response"),
                ),
            )
        )

    @_log_errors
    async def get_unbound_blocklist(self) -> dict[str, Any]:
        """Return Unbound DNS blocklist configuration.

        Returns:
            dict[str, Any]:  A UUID-keyed mapping of DNSBL rows from
                ``search_dnsbl``. For legacy firmware, a mapping with ``legacy``
                set to the regular DNSBL settings. Returns an empty mapping when
                the endpoint is unavailable or malformed.
        """
        use_legacy = await self._uses_legacy_unbound_blocklist()
        if use_legacy is not False:
            _LOGGER.debug(
                "Getting Unbound regular blocklists for OPNsense < %s or when firmware detection is unavailable",
                LEGACY_UNBOUND_BLOCKLIST_FIRMWARE,
            )
            return {"legacy": await self._get_unbound_blocklist_legacy()}

        if not await self._is_get_endpoint_available(UNBOUND_SETTINGS_SEARCH_DNSBL_ENDPOINT):
            _LOGGER.debug("Unbound DNSBL endpoint unavailable")
            return {}

        dnsbl_raw = await self._safe_dict_get(UNBOUND_SETTINGS_SEARCH_DNSBL_ENDPOINT)
        if not isinstance(dnsbl_raw, MutableMapping):
            return {}
        dnsbl_rows = dnsbl_raw.get("rows", [])
        if not isinstance(dnsbl_rows, list) or not len(dnsbl_rows) > 0:
            return {}
        dnsbl_full: dict[str, Any] = {}
        for dnsbl in dnsbl_rows:
            if not isinstance(dnsbl, MutableMapping):
                continue
            if dnsbl.get("uuid"):
                dnsbl_full.update({dnsbl["uuid"]: dnsbl})
        _LOGGER.debug("[get_unbound_blocklist] dnsbl_full length: %s", len(dnsbl_full))
        return dnsbl_full

    async def _toggle_unbound_blocklist(self, set_state: bool, uuid: str | None) -> bool:
        """Enable or disable one extended Unbound DNSBL entry.

        Args:
            set_state (bool): Desired enabled state for the DNSBL entry.
            uuid (str | None): UUID of the extended DNSBL entry to toggle.

        Returns:
            bool: ``True`` when OPNsense reports the toggle result matching the
                requested state and the DNSBL apply step succeeds; otherwise,
                ``False``. Transport errors raise when ``throw_errors`` is enabled.
        """
        if not uuid:
            _LOGGER.error("Blocklist uuid must be provided for Unbound Extended Blocklists")
            return False
        endpoint = (
            f"{UNBOUND_SETTINGS_TOGGLE_DNSBL_ENDPOINT_PREFIX}{uuid}/{'1' if set_state else '0'}"
        )
        return await self._run_toggle(
            ToggleSpec(
                label="enable_unbound_blocklist" if set_state else "disable_unbound_blocklist",
                target=ToggleTarget.ON if set_state else ToggleTarget.OFF,
                save=(
                    ToggleStep.save(endpoint, frozenset({"Enabled" if set_state else "Disabled"})),
                ),
                apply=(ToggleStep.apply(UNBOUND_SERVICE_DNSBL_ENDPOINT),),
            )
        )

    async def _set_unbound_blocklist(self, set_state: bool, uuid: str | None = None) -> bool:
        """Route an Unbound blocklist state change to the correct backend mode.

        Args:
            set_state (bool): Desired enabled state.
            uuid (str | None, optional): UUID of an extended DNSBL entry. Omit
                for legacy firmware where regular DNSBL has no per-entry UUID.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        use_legacy = await self._uses_legacy_unbound_blocklist()
        state_name = "enable" if set_state else "disable"
        if use_legacy is True:
            if uuid is not None:
                _LOGGER.error(
                    "Blocklist uuid %s is unsupported when trying to %s legacy Unbound blocklists on OPNsense < %s",
                    uuid,
                    state_name,
                    LEGACY_UNBOUND_BLOCKLIST_FIRMWARE,
                )
                return False
            _LOGGER.debug(
                "Using Unbound regular blocklists for OPNsense < %s",
                LEGACY_UNBOUND_BLOCKLIST_FIRMWARE,
            )
            return await self._set_unbound_blocklist_legacy(set_state=set_state)
        if use_legacy is False:
            _LOGGER.debug(
                "Using Unbound extended blocklists for OPNsense >= %s",
                LEGACY_UNBOUND_BLOCKLIST_FIRMWARE,
            )
            return await self._toggle_unbound_blocklist(set_state=set_state, uuid=uuid)

        _LOGGER.debug(
            "Unable to determine Unbound blocklist mode from firmware; using %s fallback",
            "extended" if uuid is not None else "legacy",
        )
        if uuid is not None:
            return await self._toggle_unbound_blocklist(set_state=set_state, uuid=uuid)
        return await self._set_unbound_blocklist_legacy(set_state=set_state)

    @_log_errors
    async def enable_unbound_blocklist(self, uuid: str | None = None) -> bool:
        """Enable Unbound DNS blocklist filtering.

        Args:
            uuid (str | None, optional): UUID of an extended DNSBL entry. Omit
                when enabling the legacy regular DNSBL setting.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        return await self._set_unbound_blocklist(set_state=True, uuid=uuid)

    @_log_errors
    async def disable_unbound_blocklist(self, uuid: str | None = None) -> bool:
        """Disable Unbound DNS blocklist filtering.

        Args:
            uuid (str | None, optional): UUID of an extended DNSBL entry. Omit
                when disabling the legacy regular DNSBL setting.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        return await self._set_unbound_blocklist(set_state=False, uuid=uuid)
