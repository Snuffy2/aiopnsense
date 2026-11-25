"""Unbound DNS blocklist methods for OPNsenseClient."""

from collections.abc import MutableMapping
import logging
from typing import Any

from ._error_policy import _log_boolean_errors, _log_errors, _throws_errors
from ._typing import AiopnsenseClientProtocol
from .client_toggle import ToggleSpec, ToggleStep, ToggleTarget
from .const import EXTENDED_UNBOUND_BLOCKLIST_CAPABILITY
from .helpers import _LOGGER, api_value_matches, dict_get

UNBOUND_SETTINGS_GET_ENDPOINT = "/api/unbound/settings/get"
UNBOUND_SETTINGS_SET_ENDPOINT = "/api/unbound/settings/set"
UNBOUND_SERVICE_DNSBL_ENDPOINT = "/api/unbound/service/dnsbl"
UNBOUND_SERVICE_RESTART_ENDPOINT = "/api/unbound/service/restart"
UNBOUND_SETTINGS_SEARCH_DNSBL_ENDPOINT = "/api/unbound/settings/search_dnsbl"
UNBOUND_SETTINGS_TOGGLE_DNSBL_ENDPOINT_PREFIX = "/api/unbound/settings/toggle_dnsbl/"


class UnboundMixin(AiopnsenseClientProtocol):
    """Unbound DNS blocklist methods for OPNsenseClient."""

    @staticmethod
    def _normalize_unbound_blocklist_legacy(
        response: dict[str, Any], *, require_complete: bool = False
    ) -> dict[str, Any]:
        """Normalize legacy Unbound DNSBL settings.

        Args:
            response (dict[str, Any]): Decoded Unbound settings response.
            require_complete (bool): Require every field sent by the legacy settings write.

        Returns:
            dict[str, Any]: Normalized legacy DNSBL settings returned by older
            OPNsense firmware, or an empty mapping if the required settings are missing.
        """
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
        response = await self._safe_dict_get(UNBOUND_SETTINGS_GET_ENDPOINT)
        dnsbl = self._normalize_unbound_blocklist_legacy(response, require_complete=True)
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
    async def get_unbound_blocklist(self) -> dict[str, Any] | None:
        """Return Unbound DNS blocklist configuration.

        Returns:
            dict[str, Any] | None: A UUID-keyed mapping of extended DNSBL rows,
                or the regular DNSBL settings under ``legacy`` on older firmware.
                A missing endpoint (404) returns an empty mapping. With errors
                disabled, a denied or failed read, undecodable JSON, or a wrongly
                shaped inventory returns ``None``. With errors enabled, decoded
                payloads use the existing normalization rules, and the mapped
                ``OPNsenseError`` propagates for request or decoding failures,
                including a legacy endpoint's 404.
        """
        extended_supported = await self._firmware_capability_enabled(
            EXTENDED_UNBOUND_BLOCKLIST_CAPABILITY
        )
        if not extended_supported:
            _LOGGER.debug(
                "Getting Unbound regular blocklists for OPNsense < %s or unknown firmware",
                EXTENDED_UNBOUND_BLOCKLIST_CAPABILITY.minimum,
            )
            if _throws_errors(self):
                # Keep the legacy plain request's strict-mode handling of a 404.
                response = await self._safe_dict_get(UNBOUND_SETTINGS_GET_ENDPOINT)
                return {"legacy": self._normalize_unbound_blocklist_legacy(response)}

        endpoint = (
            UNBOUND_SETTINGS_SEARCH_DNSBL_ENDPOINT
            if extended_supported
            else UNBOUND_SETTINGS_GET_ENDPOINT
        )
        dnsbl_outcome = await self._fetch_endpoint_outcome(endpoint)
        if not dnsbl_outcome.is_data:
            return {} if dnsbl_outcome.http_status == 404 else None

        dnsbl_raw = dnsbl_outcome.mapping()
        if not extended_supported:
            enabled = dict_get(dnsbl_raw, "unbound.dnsbl.enabled")
            if not (api_value_matches(enabled, "0") or api_value_matches(enabled, "1")):
                _LOGGER.debug("[get_unbound_blocklist] Invalid legacy DNSBL settings")
                return None
            return {"legacy": self._normalize_unbound_blocklist_legacy(dnsbl_raw)}

        throw_errors = _throws_errors(self)
        dnsbl_rows = dnsbl_raw.get("rows")
        if not isinstance(dnsbl_rows, list):
            _LOGGER.debug("[get_unbound_blocklist] Invalid DNSBL row list")
            return {} if throw_errors else None
        dnsbl_full: dict[str, Any] = {}
        for dnsbl in dnsbl_rows:
            uuid = dnsbl.get("uuid") if isinstance(dnsbl, MutableMapping) else None
            if not throw_errors and (not isinstance(uuid, str) or not uuid):
                _LOGGER.debug("[get_unbound_blocklist] DNSBL row has no valid UUID")
                return None
            if uuid:
                dnsbl_full[uuid] = dnsbl
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
            bool: True when the operation succeeds. Otherwise, False. Transport
                errors during the save or apply raise when ``throw_errors`` is
                enabled.
        """
        supported = await self._firmware_capability(EXTENDED_UNBOUND_BLOCKLIST_CAPABILITY)
        state_name = "enable" if set_state else "disable"
        if supported is False:
            if uuid is not None:
                _LOGGER.error(
                    "Blocklist uuid %s is unsupported when trying to %s legacy Unbound blocklists on OPNsense < %s",
                    uuid,
                    state_name,
                    EXTENDED_UNBOUND_BLOCKLIST_CAPABILITY.minimum,
                )
                return False
            _LOGGER.debug(
                "Using Unbound regular blocklists for OPNsense < %s",
                EXTENDED_UNBOUND_BLOCKLIST_CAPABILITY.minimum,
            )
            return await self._set_unbound_blocklist_legacy(set_state=set_state)
        if supported is True:
            _LOGGER.debug(
                "Using Unbound extended blocklists for OPNsense >= %s",
                EXTENDED_UNBOUND_BLOCKLIST_CAPABILITY.minimum,
            )
            return await self._toggle_unbound_blocklist(set_state=set_state, uuid=uuid)

        if uuid is not None:
            return await self._toggle_unbound_blocklist(set_state=set_state, uuid=uuid)
        return await self._set_unbound_blocklist_legacy(set_state=set_state)

    @_log_boolean_errors
    async def enable_unbound_blocklist(self, uuid: str | None = None) -> bool:
        """Enable Unbound DNS blocklist filtering.

        Args:
            uuid (str | None, optional): UUID of an extended DNSBL entry. Omit
                when enabling the legacy regular DNSBL setting.

        Returns:
            bool: True when the operation succeeds. Otherwise, False. Transport
                errors raise ``OPNsenseError`` when ``throw_errors`` is enabled.
        """
        return await self._set_unbound_blocklist(set_state=True, uuid=uuid)

    @_log_boolean_errors
    async def disable_unbound_blocklist(self, uuid: str | None = None) -> bool:
        """Disable Unbound DNS blocklist filtering.

        Args:
            uuid (str | None, optional): UUID of an extended DNSBL entry. Omit
                when disabling the legacy regular DNSBL setting.

        Returns:
            bool: True when the operation succeeds; otherwise, False. Transport
                errors raise ``OPNsenseError`` when ``throw_errors`` is enabled.
        """
        return await self._set_unbound_blocklist(set_state=False, uuid=uuid)
