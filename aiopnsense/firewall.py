"""Firewall, NAT, alias, and state methods for OPNsenseClient."""

from collections.abc import Callable, MutableMapping
from typing import Any

from ._typing import AiopnsenseClientProtocol
from .const import OPNSENSE_26_1_11_COMPAT_FIRMWARE, UNIFIED_NAT_TEMPLATE_FIRMWARE
from .helpers import _LOGGER, _log_errors, api_value_matches, firmware_is_at_least

FIREWALL_FILTER_RULES_SEARCH_ENDPOINT = "/api/firewall/filter/search_rule"
FIREWALL_DNAT_RULES_SEARCH_ENDPOINT = "/api/firewall/d_nat/search_rule"
FIREWALL_ONE_TO_ONE_RULES_SEARCH_ENDPOINT = "/api/firewall/one_to_one/search_rule"
FIREWALL_SOURCE_NAT_RULES_SEARCH_ENDPOINT = "/api/firewall/source_nat/search_rule"
FIREWALL_NPT_RULES_SEARCH_ENDPOINT = "/api/firewall/npt/search_rule"
FIREWALL_FILTER_TOGGLE_RULE_ENDPOINT_PREFIX = "/api/firewall/filter/toggle_rule/"
FIREWALL_FILTER_APPLY_ENDPOINT = "/api/firewall/filter/apply"
FIREWALL_NAT_TOGGLE_RULE_ENDPOINT_PREFIX = "/api/firewall/"
FIREWALL_NAT_APPLY_ENDPOINT_SUFFIX = "/apply"
FIREWALL_KILL_STATES_ENDPOINT = "/api/diagnostics/firewall/kill_states/"
FIREWALL_ALIAS_SEARCH_ENDPOINT = "/api/firewall/alias/search_item"
FIREWALL_ALIAS_SEARCH_CAMELCASE_ENDPOINT = "/api/firewall/alias/searchItem"
FIREWALL_ALIAS_TOGGLE_ENDPOINT_PREFIX = "/api/firewall/alias/toggle_item/"
FIREWALL_ALIAS_TOGGLE_CAMELCASE_ENDPOINT_PREFIX = "/api/firewall/alias/toggleItem/"
FIREWALL_ALIAS_SET_ENDPOINT = "/api/firewall/alias/set"
FIREWALL_ALIAS_RECONFIGURE_ENDPOINT = "/api/firewall/alias/reconfigure"


class FirewallMixin(AiopnsenseClientProtocol):
    """Firewall methods for OPNsenseClient."""

    @staticmethod
    def _index_rule_rows(
        rows: list,
        normalizer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        include_rule: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        """Return UUID-keyed rule rows after filtering malformed and lockout rows.

        Args:
            rows (list): Raw rule rows returned by an OPNsense search endpoint.
            normalizer (Callable[[dict[str, Any]], dict[str, Any]] | None, optional):
                Optional row normalizer applied after common filtering.
            include_rule (Callable[[dict[str, Any]], bool] | None, optional):
                Optional row predicate applied after normalization.

        Returns:
            dict[str, Any]: Rule mapping keyed by UUID.
        """
        rules_dict: dict[str, Any] = {}
        for rule in rows:
            if not isinstance(rule, MutableMapping):
                continue
            uuid = rule.get("uuid")
            if not uuid or "lockout" in str(uuid):
                continue
            new_rule = dict(rule)
            if normalizer is not None:
                new_rule = normalizer(new_rule)
            if include_rule is not None and not include_rule(new_rule):
                continue
            rules_dict[str(new_rule["uuid"])] = new_rule
        return rules_dict

    @staticmethod
    def _is_user_firewall_rule(rule: dict[str, Any]) -> bool:
        """Return whether a firewall filter rule should be exposed.

        Args:
            rule (dict[str, Any]): Normalized firewall filter rule row returned by OPNsense.

        Returns:
            bool: ``True`` when the rule is not automatically generated.
        """
        return rule.get("is_automatic") is not True

    @staticmethod
    def _is_user_source_nat_rule(rule: dict[str, Any]) -> bool:
        """Return whether a source NAT rule should be exposed.

        Args:
            rule (dict[str, Any]): Normalized source NAT rule row returned by OPNsense.

        Returns:
            bool: ``True`` when the rule is not automatically generated.
        """
        return not api_value_matches(rule.get("is_automatic", "0"), "1")

    @staticmethod
    def _filters_automatic_source_nat_rules(firmware_version: str | None) -> bool:
        """Return whether firmware exposes generated source NAT rows.

        Args:
            firmware_version (str | None): Installed OPNsense firmware version.

        Returns:
            bool: ``True`` when firmware is at or above the 26.1.11 source NAT
                API behavior change, otherwise ``False``.
        """
        should_filter = firmware_is_at_least(firmware_version, OPNSENSE_26_1_11_COMPAT_FIRMWARE)
        if should_filter is None:
            _LOGGER.debug(
                "Unable to compare firmware version %s for source NAT automatic rule filtering",
                firmware_version,
            )
            return False
        return should_filter

    @staticmethod
    def _uses_unified_nat_template(firmware_version: str | None) -> bool:
        """Return whether OPNsense uses the unified NAT UI template.

        Args:
            firmware_version (str | None): Installed OPNsense firmware version.

        Returns:
            bool: ``True`` when the firmware is at or above the unified NAT
                template threshold, otherwise ``False``.
        """
        uses_template = firmware_is_at_least(firmware_version, UNIFIED_NAT_TEMPLATE_FIRMWARE)
        if uses_template is None:
            _LOGGER.debug(
                "Unable to compare firmware version %s for DNAT category normalization",
                firmware_version,
            )
            return False
        return uses_template

    @staticmethod
    def _normalize_nat_destination_rule(
        rule: dict[str, Any], *, normalize_categories: bool = False
    ) -> dict[str, Any]:
        """Normalize destination NAT rule field names and enabled state.

        Args:
            rule (dict[str, Any]): Destination NAT rule row returned by OPNsense.
            normalize_categories (bool): Whether to mirror DNAT's singular
                category keys to plural category keys for newer unified NAT UI
                template firmware.

        Returns:
            dict[str, Any]: Normalized destination NAT rule row.
        """
        rule["description"] = rule.pop("descr", "")
        if "enabled" in rule:
            rule["enabled"] = rule.pop("enabled")
        else:
            rule["enabled"] = "1" if api_value_matches(rule.pop("disabled", "0"), "0") else "0"
        rule.pop("disabled", None)
        if normalize_categories:
            if "category" in rule and "categories" not in rule:
                rule["categories"] = rule["category"]
            if "%category" in rule and "%categories" not in rule:
                rule["%categories"] = rule["%category"]
        return rule

    async def _get_uuid_indexed_rules(
        self,
        endpoint: str,
        debug_label: str,
        normalizer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        include_rule: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        """Fetch rule rows from one endpoint and return them keyed by UUID.

        Args:
            endpoint (str): Search endpoint to query.
            debug_label (str): Label used in endpoint-unavailable and result-size logs.
            normalizer (Callable[[dict[str, Any]], dict[str, Any]] | None, optional):
                Optional row normalizer applied after common filtering.
            include_rule (Callable[[dict[str, Any]], bool] | None, optional):
                Optional row predicate applied after normalization.

        Returns:
            dict[str, Any]: Rule mapping keyed by UUID.
        """
        if not await self._is_get_endpoint_available(endpoint):
            _LOGGER.debug("%s endpoint not available", debug_label)
            return {}

        response = await self._safe_dict_get(endpoint)
        rules: list = response.get("rows", [])
        rules_dict = self._index_rule_rows(rules, normalizer=normalizer, include_rule=include_rule)
        _LOGGER.debug("[%s] rules_dict length: %s", debug_label, len(rules_dict))
        return rules_dict

    @_log_errors
    async def get_firewall(self) -> dict[str, Any]:
        """Return firewall filter rules and all supported NAT rule groups.

        Returns:
            dict[str, Any]: Mapping with top-level ``rules`` for firewall
                filter rules, excluding lockout and automatically generated
                firewall rules, and ``nat`` groups for destination NAT,
                one-to-one NAT, source NAT, and NPT rules. Rule groups are
                keyed by rule UUID.
        """
        firewall: dict[str, Any] = {"nat": {}}
        firewall["rules"] = await self._get_firewall_rules()
        firewall["nat"]["d_nat"] = await self._get_nat_destination_rules()
        firewall["nat"]["one_to_one"] = await self._get_nat_one_to_one_rules()
        firewall["nat"]["source_nat"] = await self._get_nat_source_rules()
        firewall["nat"]["npt"] = await self._get_nat_npt_rules()
        return firewall

    @_log_errors
    async def _get_firewall_rules(self) -> dict[str, Any]:
        """Retrieve firewall rules from OPNsense.

        Returns:
            dict[str, Any]: Firewall filter rules keyed by UUID, excluding
                malformed rows, lockout rules, and automatically generated
                rules.
        """
        return await self._get_uuid_indexed_rules(
            endpoint=FIREWALL_FILTER_RULES_SEARCH_ENDPOINT,
            debug_label="get_firewall_rules",
            include_rule=self._is_user_firewall_rule,
        )

    @_log_errors
    async def _get_nat_destination_rules(self) -> dict[str, Any]:
        """Retrieve NAT destination rules from OPNsense.

        Returns:
            dict[str, Any]: Destination NAT rules keyed by UUID, with
                ``descr`` normalized to ``description`` and disabled-state
                values converted into an ``enabled`` flag.
        """
        if not await self._is_get_endpoint_available(FIREWALL_DNAT_RULES_SEARCH_ENDPOINT):
            _LOGGER.debug("%s endpoint not available", "get_nat_destination_rules")
            return {}

        response = await self._safe_dict_get(FIREWALL_DNAT_RULES_SEARCH_ENDPOINT)
        normalize_categories = self._uses_unified_nat_template(
            await self.get_host_firmware_version()
        )
        rules: list = response.get("rows", [])
        rules_dict = self._index_rule_rows(
            rules,
            normalizer=lambda rule: self._normalize_nat_destination_rule(
                rule, normalize_categories=normalize_categories
            ),
        )
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
        return await self._get_uuid_indexed_rules(
            endpoint=FIREWALL_ONE_TO_ONE_RULES_SEARCH_ENDPOINT,
            debug_label="get_nat_one_to_one_rules",
        )

    @_log_errors
    async def _get_nat_source_rules(self) -> dict[str, Any]:
        """Retrieve NAT source rules from OPNsense.

        Returns:
            dict[str, Any]: Source NAT rules keyed by UUID, excluding
                malformed rows and lockout rules. For OPNsense 26.1.11 and
                newer, generated automatic source NAT rows are also excluded.
        """
        if not await self._is_get_endpoint_available(FIREWALL_SOURCE_NAT_RULES_SEARCH_ENDPOINT):
            _LOGGER.debug("%s endpoint not available", "get_nat_source_rules")
            return {}

        response = await self._safe_dict_get(FIREWALL_SOURCE_NAT_RULES_SEARCH_ENDPOINT)
        rules: list = response.get("rows", [])
        include_rule = None
        has_automatic_rows = any(
            isinstance(rule, MutableMapping)
            and api_value_matches(rule.get("is_automatic", "0"), "1")
            for rule in rules
        )
        if has_automatic_rows and self._filters_automatic_source_nat_rules(
            await self.get_host_firmware_version()
        ):
            include_rule = self._is_user_source_nat_rule
        rules_dict = self._index_rule_rows(rules, include_rule=include_rule)
        _LOGGER.debug(
            "[%s] rules_dict length: %s",
            "get_nat_source_rules",
            len(rules_dict),
        )
        return rules_dict

    @_log_errors
    async def _get_nat_npt_rules(self) -> dict[str, Any]:
        """Retrieve NAT NPT rules from OPNsense.

        Returns:
            dict[str, Any]: NPT NAT rules keyed by UUID, excluding malformed
                rows and lockout rules.
        """
        return await self._get_uuid_indexed_rules(
            endpoint=FIREWALL_NPT_RULES_SEARCH_ENDPOINT,
            debug_label="get_nat_npt_rules",
        )

    async def toggle_firewall_rule(self, uuid: str, toggle_on_off: str | None = None) -> bool:
        """Toggle Firewall Rule on and off.

        Args:
            uuid (str): UUID of the firewall filter rule to toggle.
            toggle_on_off (str | None, optional): Target state. Use ``on`` to
                enable, ``off`` to disable, or ``None`` to let OPNsense toggle
                the current state.

        Returns:
            bool: True when the toggle operation completes successfully; otherwise, False.
        """
        payload: dict[str, Any] = {}
        url = f"{FIREWALL_FILTER_TOGGLE_RULE_ENDPOINT_PREFIX}{uuid}"
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

        apply_resp = await self._safe_dict_post(FIREWALL_FILTER_APPLY_ENDPOINT)
        if apply_resp.get("status", "").strip() != "OK":
            return False

        return True

    async def toggle_nat_rule(
        self, nat_rule_type: str, uuid: str, toggle_on_off: str | None = None
    ) -> bool:
        """Toggle NAT Rule on and off.

        Args:
            nat_rule_type (str): NAT rule category path segment, such as
                ``d_nat``, ``source_nat``, ``one_to_one``, or ``npt``.
            uuid (str): UUID of the NAT rule to toggle.
            toggle_on_off (str | None, optional): Target state. Use ``on`` to
                enable, ``off`` to disable, or ``None`` to let OPNsense toggle
                the current state.

        Returns:
            bool: True when the toggle operation completes successfully; otherwise, False.
        """
        payload: dict[str, Any] = {}
        url = f"{FIREWALL_NAT_TOGGLE_RULE_ENDPOINT_PREFIX}{nat_rule_type}/toggle_rule/{uuid}"
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

        apply_resp = await self._safe_dict_post(
            f"{FIREWALL_NAT_TOGGLE_RULE_ENDPOINT_PREFIX}{nat_rule_type}{FIREWALL_NAT_APPLY_ENDPOINT_SUFFIX}"
        )
        if apply_resp.get("status", "").strip() != "OK":
            return False

        return True

    async def kill_states(self, ip_addr: str) -> MutableMapping[str, Any]:
        """Kill the active states of the IP address.

        Args:
            ip_addr (str): IP address whose states should be terminated.

        Returns:
            MutableMapping[str, Any]: Mapping with ``success`` and
                ``dropped_states`` from the firewall state-kill response.
        """
        payload: dict[str, Any] = {"filter": ip_addr}
        response = await self._safe_dict_post(
            FIREWALL_KILL_STATES_ENDPOINT,
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
            toggle_on_off (str | None, optional): Target state. Use ``on`` to
                enable, ``off`` to disable, or ``None`` to let OPNsense toggle
                the current state.

        Returns:
            bool: True when the toggle operation completes successfully; otherwise, False.
        """
        alias_search_endpoint = await self._get_endpoint_path(
            snake_case_path=FIREWALL_ALIAS_SEARCH_ENDPOINT,
            camel_case_path=FIREWALL_ALIAS_SEARCH_CAMELCASE_ENDPOINT,
        )
        if not await self._is_get_endpoint_available(alias_search_endpoint):
            _LOGGER.debug("Firewall alias search endpoint unavailable")
            return False

        alias_list_resp = await self._safe_dict_get(alias_search_endpoint)
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
        url: str = await self._get_endpoint_path(
            snake_case_path=f"{FIREWALL_ALIAS_TOGGLE_ENDPOINT_PREFIX}{uuid}",
            camel_case_path=f"{FIREWALL_ALIAS_TOGGLE_CAMELCASE_ENDPOINT_PREFIX}{uuid}",
        )
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

        set_resp = await self._safe_dict_post(FIREWALL_ALIAS_SET_ENDPOINT)
        if set_resp.get("result") != "saved":
            return False

        reconfigure_resp = await self._safe_dict_post(FIREWALL_ALIAS_RECONFIGURE_ENDPOINT)
        if reconfigure_resp.get("status") != "ok":
            return False

        return True
