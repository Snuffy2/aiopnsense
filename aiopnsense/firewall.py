"""Firewall, NAT, alias, and state methods for OPNsenseClient."""

from collections.abc import MutableMapping
from typing import Any

from ._typing import PyOPNsenseClientProtocol
from .helpers import _LOGGER, _log_errors


class FirewallMixin(PyOPNsenseClientProtocol):
    """Firewall methods for OPNsenseClient."""

    @_log_errors
    async def get_firewall(self) -> dict[str, Any]:
        """Retrieve all firewall and NAT rules from OPNsense.

        Returns:
            dict[str, Any]: Normalized data returned by the related OPNsense endpoint.
        """
        firewall: dict[str, Any] = {"nat": {}}
        firewall["rules"] = await self._get_firewall_rules()
        firewall["nat"]["d_nat"] = await self._get_nat_destination_rules()
        firewall["nat"]["one_to_one"] = await self._get_nat_one_to_one_rules()
        firewall["nat"]["source_nat"] = await self._get_nat_source_rules()
        firewall["nat"]["npt"] = await self._get_nat_npt_rules()
        # _LOGGER.debug("[get_firewall] firewall: %s", firewall)
        return firewall

    @_log_errors
    async def _get_firewall_rules(self) -> dict[str, Any]:
        """Retrieve firewall rules from OPNsense.

        Returns:
            dict[str, Any]: Mapping containing normalized fields for downstream use.
        """
        endpoint = "/api/firewall/filter/search_rule"
        if not await self.is_endpoint_available(endpoint):
            _LOGGER.debug("Firewall filter endpoint not available")
            return {}

        response = await self._safe_dict_get(endpoint)
        # _LOGGER.debug("[get_firewall_rules] response: %s", response)
        rules: list = response.get("rows", [])
        # _LOGGER.debug("[get_firewall_rules] rules: %s", rules)
        rules_dict: dict[str, Any] = {}
        for rule in rules:
            if not isinstance(rule, MutableMapping):
                continue
            uuid = rule.get("uuid")
            if not uuid or "lockout" in str(uuid):
                continue
            new_rule = dict(rule)
            rules_dict[str(new_rule["uuid"])] = new_rule
        # _LOGGER.debug("[get_firewall_rules] rules_dict: %s", rules_dict)
        _LOGGER.debug("[get_firewall_rules] rules_dict length: %s", len(rules_dict))
        return rules_dict

    @_log_errors
    async def _get_nat_destination_rules(self) -> dict[str, Any]:
        """Retrieve NAT destination rules from OPNsense.

        Returns:
            dict[str, Any]: Mapping containing normalized fields for downstream use.
        """
        endpoint = "/api/firewall/d_nat/search_rule"
        if not await self.is_endpoint_available(endpoint):
            _LOGGER.debug("NAT destination endpoint not available")
            return {}

        response = await self._safe_dict_get(endpoint)
        # _LOGGER.debug("[get_nat_destination_rules] response: %s", response)
        rules: list = response.get("rows", [])
        # _LOGGER.debug("[get_nat_destination_rules] rules: %s", rules)
        rules_dict: dict[str, Any] = {}
        for rule in rules:
            if not isinstance(rule, MutableMapping):
                continue
            uuid = rule.get("uuid")
            if not uuid or "lockout" in str(uuid):
                continue  # skip lockout rules
            new_rule = dict(rule)
            new_rule["description"] = new_rule.pop("descr", "")
            new_rule["enabled"] = "1" if new_rule.pop("disabled", "0") == "0" else "0"
            rules_dict[str(new_rule["uuid"])] = new_rule
        # _LOGGER.debug("[get_nat_destination_rules] rules_dict: %s", rules_dict)
        _LOGGER.debug("[get_nat_destination_rules] rules_dict length: %s", len(rules_dict))
        return rules_dict

    @_log_errors
    async def _get_nat_one_to_one_rules(self) -> dict[str, Any]:
        """Retrieve NAT one-to-one rules from OPNsense.

        Returns:
            dict[str, Any]: Mapping of NAT one-to-one rules keyed by rule
                identifiers, with each value containing the corresponding
                rule details (for example, source, destination, external IP,
                and description fields when present).
        """
        endpoint = "/api/firewall/one_to_one/search_rule"
        if not await self.is_endpoint_available(endpoint):
            _LOGGER.debug("NAT one-to-one endpoint not available")
            return {}

        response = await self._safe_dict_get(endpoint)
        # _LOGGER.debug("[get_nat_one_to_one_rules] response: %s", response)
        rules: list = response.get("rows", [])
        # _LOGGER.debug("[get_nat_one_to_one_rules] rules: %s", rules)
        rules_dict: dict[str, Any] = {}
        for rule in rules:
            if not isinstance(rule, MutableMapping):
                continue
            uuid = rule.get("uuid")
            if not uuid or "lockout" in str(uuid):
                continue
            new_rule = dict(rule)
            rules_dict[str(new_rule["uuid"])] = new_rule
        _LOGGER.debug("[get_nat_one_to_one_rules] rules_dict length: %s", len(rules_dict))
        # _LOGGER.debug("[get_nat_one_to_one_rules] rules_dict: %s", rules_dict)
        return rules_dict

    @_log_errors
    async def _get_nat_source_rules(self) -> dict[str, Any]:
        """Retrieve NAT source rules from OPNsense.

        Returns:
            dict[str, Any]: Mapping containing normalized fields for downstream use.
        """
        endpoint = "/api/firewall/source_nat/search_rule"
        if not await self.is_endpoint_available(endpoint):
            _LOGGER.debug("NAT source endpoint not available")
            return {}

        response = await self._safe_dict_get(endpoint)
        # _LOGGER.debug("[get_nat_source_rules] response: %s", response)
        rules: list = response.get("rows", [])
        # _LOGGER.debug("[get_nat_source_rules] rules: %s", rules)
        rules_dict: dict[str, Any] = {}
        for rule in rules:
            if not isinstance(rule, MutableMapping):
                continue
            uuid = rule.get("uuid")
            if not uuid or "lockout" in str(uuid):
                continue
            new_rule = dict(rule)
            rules_dict[str(new_rule["uuid"])] = new_rule
        # _LOGGER.debug("[get_nat_source_rules] rules_dict: %s", rules_dict)
        _LOGGER.debug("[get_nat_source_rules] rules_dict length: %s", len(rules_dict))
        return rules_dict

    @_log_errors
    async def _get_nat_npt_rules(self) -> dict[str, Any]:
        """Retrieve NAT NPT rules from OPNsense.

        Returns:
            dict[str, Any]: Mapping containing normalized fields for downstream use.
        """
        endpoint = "/api/firewall/npt/search_rule"
        if not await self.is_endpoint_available(endpoint):
            _LOGGER.debug("NAT NPT endpoint not available")
            return {}

        response = await self._safe_dict_get(endpoint)
        # _LOGGER.debug("[get_nat_npt_rules] response: %s", response)
        rules: list = response.get("rows", [])
        # _LOGGER.debug("[get_nat_npt_rules] rules: %s", rules)
        rules_dict: dict[str, Any] = {}
        for rule in rules:
            if not isinstance(rule, MutableMapping):
                continue
            uuid = rule.get("uuid")
            if not uuid or "lockout" in str(uuid):
                continue
            new_rule = dict(rule)
            rules_dict[str(new_rule["uuid"])] = new_rule
        # _LOGGER.debug("[get_nat_npt_rules] rules_dict: %s", rules_dict)
        _LOGGER.debug("[get_nat_npt_rules] rules_dict length: %s", len(rules_dict))
        return rules_dict

    async def toggle_firewall_rule(self, uuid: str, toggle_on_off: str | None = None) -> bool:
        """Toggle Firewall Rule on and off.

        Args:
            uuid (str): Unique identifier of the target OPNsense resource.
            toggle_on_off (str | None, optional): Target enabled state for the selected item.

        Returns:
            bool: True when the toggle operation completes successfully; otherwise, False.
        """
        payload: dict[str, Any] = {}
        url = f"/api/firewall/filter/toggle_rule/{uuid}"
        if toggle_on_off == "on":
            url = f"{url}/1"
        elif toggle_on_off == "off":
            url = f"{url}/0"
        response = await self._safe_dict_post(
            url,
            payload=payload,
        )
        _LOGGER.debug(
            "[toggle_firewall_rule] uuid: %s, action: %s, url: %s, response: %s",
            uuid,
            toggle_on_off,
            url,
            response,
        )
        if response.get("result") == "failed":
            return False

        apply_resp = await self._safe_dict_post("/api/firewall/filter/apply")
        if apply_resp.get("status", "").strip() != "OK":
            return False

        return True

    async def toggle_nat_rule(
        self, nat_rule_type: str, uuid: str, toggle_on_off: str | None = None
    ) -> bool:
        """Toggle NAT Rule on and off.

        Args:
            nat_rule_type (str): NAT rule category to toggle.
            uuid (str): Unique identifier of the target OPNsense resource.
            toggle_on_off (str | None, optional): Target enabled state for the selected item.

        Returns:
            bool: True when the toggle operation completes successfully; otherwise, False.
        """
        payload: dict[str, Any] = {}
        url = f"/api/firewall/{nat_rule_type}/toggle_rule/{uuid}"
        # d_nat uses opposite logic for on/off
        if nat_rule_type == "d_nat" and toggle_on_off is not None:
            if toggle_on_off == "on":
                url = f"{url}/0"
            elif toggle_on_off == "off":
                url = f"{url}/1"
        elif toggle_on_off == "on":
            url = f"{url}/1"
        elif toggle_on_off == "off":
            url = f"{url}/0"
        response = await self._safe_dict_post(
            url,
            payload=payload,
        )
        _LOGGER.debug(
            "[toggle_nat_rule] uuid: %s, action: %s, url: %s, response: %s",
            uuid,
            toggle_on_off,
            url,
            response,
        )
        if response.get("result") == "failed":
            return False

        apply_resp = await self._safe_dict_post(f"/api/firewall/{nat_rule_type}/apply")
        if apply_resp.get("status", "").strip() != "OK":
            return False

        return True

    async def kill_states(self, ip_addr: str) -> MutableMapping[str, Any]:
        """Kill the active states of the IP address.

        Args:
            ip_addr (str): IP address whose states should be terminated.

        Returns:
            MutableMapping[str, Any]: Mapping containing normalized fields for downstream use.
        """
        payload: dict[str, Any] = {"filter": ip_addr}
        response = await self._safe_dict_post(
            "/api/diagnostics/firewall/kill_states/",
            payload=payload,
        )
        _LOGGER.debug("[kill_states] ip_addr: %s, response: %s", ip_addr, response)
        return {
            "success": bool(response.get("result", "") == "ok"),
            "dropped_states": response.get("dropped_states", 0),
        }

    async def toggle_alias(self, alias: str, toggle_on_off: str | None = None) -> bool:
        """Toggle alias on and off.

        Args:
            alias (str): Alias name to toggle in firewall configuration.
            toggle_on_off (str | None, optional): Target enabled state for the selected item.

        Returns:
            bool: True when the toggle operation completes successfully; otherwise, False.
        """
        endpoint = "/api/firewall/alias/search_item"
        if not await self.is_endpoint_available(endpoint):
            _LOGGER.debug("Firewall alias endpoint not available")
            return False

        alias_list_resp = await self._safe_dict_get(endpoint)
        alias_list: list = alias_list_resp.get("rows", [])
        if not isinstance(alias_list, list):
            return False
        uuid: str | None = None
        for item in alias_list:
            if not isinstance(item, MutableMapping):
                continue
            if item.get("name") == alias:
                uuid = item.get("uuid")
                break
        if not uuid:
            return False
        payload: dict[str, Any] = {}
        url: str = f"/api/firewall/alias/toggle_item/{uuid}"
        if toggle_on_off == "on":
            url = f"{url}/1"
        elif toggle_on_off == "off":
            url = f"{url}/0"
        response = await self._safe_dict_post(
            url,
            payload=payload,
        )
        _LOGGER.debug(
            "[toggle_alias] alias: %s, uuid: %s, action: %s, url: %s, response: %s",
            alias,
            uuid,
            toggle_on_off,
            url,
            response,
        )
        if response.get("result") == "failed":
            return False

        set_resp = await self._safe_dict_post("/api/firewall/alias/set")
        if set_resp.get("result") != "saved":
            return False

        reconfigure_resp = await self._safe_dict_post("/api/firewall/alias/reconfigure")
        if reconfigure_resp.get("status") != "ok":
            return False

        return True
