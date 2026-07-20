"""DHCP and ARP methods for OPNsenseClient."""

from collections.abc import MutableMapping
from datetime import datetime, tzinfo
from typing import Any

from ._typing import AiopnsenseClientProtocol, CategoryResult, CategoryState
from .helpers import (
    _LOGGER,
    _log_errors,
    api_value_matches,
    get_ip_key,
    timestamp_to_datetime,
    try_to_int,
)

ARP_TABLE_ENDPOINT = "/api/diagnostics/interface/search_arp"
KEA_DHCPV4_GET_ENDPOINT = "/api/kea/dhcpv4/get"
KEA_LEASES4_SEARCH_ENDPOINT = "/api/kea/leases4/search"
KEA_LEASES6_SEARCH_ENDPOINT = "/api/kea/leases6/search"
KEA_DHCPV4_SEARCH_RESERVATION_ENDPOINT = "/api/kea/dhcpv4/search_reservation"
KEA_DHCPV4_SEARCH_RESERVATION_CAMELCASE_ENDPOINT = "/api/kea/dhcpv4/searchReservation"
DNSMASQ_LEASES_SEARCH_ENDPOINT = "/api/dnsmasq/leases/search"
ISC_DHCPV4_LEASES_SEARCH_ENDPOINT = "/api/dhcpv4/leases/search_lease"
ISC_DHCPV4_LEASES_SEARCH_CAMELCASE_ENDPOINT = "/api/dhcpv4/leases/searchLease"
ISC_DHCPV6_LEASES_SEARCH_ENDPOINT = "/api/dhcpv6/leases/search_lease"
ISC_DHCPV6_LEASES_SEARCH_CAMELCASE_ENDPOINT = "/api/dhcpv6/leases/searchLease"


class DHCPMixin(AiopnsenseClientProtocol):
    """DHCP methods for OPNsenseClient."""

    def _normalize_lease_key_value(self, value: Any) -> Any:
        """Convert nested lease values into stable, hashable objects.

        Args:
            value (Any): Raw value from a lease row field.

        Returns:
            Any: Hashable representation suitable for key construction.
        """
        if isinstance(value, MutableMapping):
            return tuple(
                sorted((key, self._normalize_lease_key_value(val)) for key, val in value.items())
            )
        if isinstance(value, list):
            return tuple(self._normalize_lease_key_value(item) for item in value)
        if isinstance(value, set):
            return tuple(sorted(self._normalize_lease_key_value(item) for item in value))
        return value

    def _is_reserved_lease(self, raw_reserved: object) -> bool:
        """Return whether a DHCP lease row should be treated as reserved.

        Args:
            raw_reserved (object): Raw ``is_reserved`` field returned by a DHCP lease API.

        Returns:
            bool: ``True`` for reserved/static lease rows, ``False`` otherwise.
        """
        if isinstance(raw_reserved, str):
            return api_value_matches(raw_reserved, "1")
        if isinstance(raw_reserved, list):
            return len(raw_reserved) > 0
        return bool(raw_reserved)

    @staticmethod
    def _copy_lease_identity_fields(
        lease: dict[str, Any], lease_info: MutableMapping[str, Any]
    ) -> None:
        """Copy optional DHCP client identity metadata to a normalized lease.

        Args:
            lease (dict[str, Any]): Normalized lease row being built.
            lease_info (MutableMapping[str, Any]): Raw lease row returned by OPNsense.

        Returns:
            None: The ``lease`` mapping is updated in place.
        """
        if lease_info.get("client_id") not in (None, ""):
            lease["client_id"] = lease_info.get("client_id")
        raw_reserved = lease_info.get("is_reserved")
        if isinstance(raw_reserved, list) and raw_reserved:
            lease["reserved_by"] = list(raw_reserved)

    @_log_errors
    async def get_arp_table(self, resolve_hostnames: bool = False) -> list:
        """Return active ARP table entries.

        Args:
            resolve_hostnames (bool): Whether reverse DNS lookups should be requested.

        Returns:
            list: ARP rows from OPNsense, optionally with resolved hostnames,
                including fields such as IP address, MAC address, interface,
                expiration, and entry type when provided by the endpoint.
        """
        return (await self.get_arp_table_result(resolve_hostnames=resolve_hostnames)).data

    async def get_arp_table_result(
        self, resolve_hostnames: bool = False
    ) -> CategoryResult[list[dict[str, Any]]]:
        """Return ARP rows with schema-aware endpoint authority metadata."""
        # [{'hostname': '?', 'ip-address': '<ip>', 'mac-address': '<mac>', 'interface': 'em0', 'expires': 1199, 'type': 'ethernet'}, ...]
        resolve_flag = "yes" if resolve_hostnames else "no"
        arp_endpoint_resolve = f"{ARP_TABLE_ENDPOINT}?resolve={resolve_flag}"
        result = CategoryResult.coerce(
            await self._check_optional_get_endpoint(
                arp_endpoint_resolve,
                cache_path=ARP_TABLE_ENDPOINT,
            )
        )
        if result.state != "available":
            _LOGGER.debug("ARP endpoint unavailable")
            return CategoryResult([], result.state, False)
        arp_table_info = result.data
        if not isinstance(arp_table_info, MutableMapping):
            return CategoryResult([], "malformed", False)
        if "rows" not in arp_table_info or not isinstance(arp_table_info["rows"], list):
            return CategoryResult([], "malformed", False)
        rows = arp_table_info["rows"]
        arp_table = [dict(row) for row in rows if isinstance(row, MutableMapping)]
        if len(arp_table) != len(rows):
            return CategoryResult(arp_table, "malformed", False)
        return CategoryResult(arp_table, "available", True)

    @_log_errors
    async def get_dhcp_leases(self, opnsense_tz: tzinfo | None = None) -> dict[str, Any]:
        """Return active DHCP leases grouped by interface.

        Args:
            opnsense_tz (tzinfo | None, optional): Timezone used to localize
                ISC lease expiration timestamps. Fetched from OPNsense when
                omitted.

        Returns:
            dict[str, Any]: Mapping with ``lease_interfaces`` keyed by
                interface name and ``leases`` keyed by interface name. Lease
                entries are normalized across Kea, ISC, and dnsmasq and
                include address, hostname, interface, type, MAC,
                and expiration when available.
        """
        return (await self.get_dhcp_leases_result(opnsense_tz=opnsense_tz)).data

    async def get_dhcp_leases_result(
        self, opnsense_tz: tzinfo | None = None
    ) -> CategoryResult[dict[str, Any]]:
        """Return DHCP leases with authority derived from every applicable source."""
        if opnsense_tz is None:
            opnsense_tz = await self._get_opnsense_timezone()
        state_token = self._dhcp_source_states_context.set([])
        try:
            leases_raw: list = await self._get_kea_dhcpv4_leases(opnsense_tz=opnsense_tz)
            leases_raw += await self._get_kea_dhcpv6_leases(opnsense_tz=opnsense_tz)
            leases_raw += await self._get_isc_dhcpv4_leases(opnsense_tz=opnsense_tz)
            leases_raw += await self._get_isc_dhcpv6_leases(opnsense_tz=opnsense_tz)
            leases_raw += await self._get_dnsmasq_leases(opnsense_tz=opnsense_tz)
            lease_interfaces: dict[str, Any] = await self._get_kea_interfaces()
            source_states = list(self._dhcp_source_states_context.get() or [])
        finally:
            self._dhcp_source_states_context.reset(state_token)

        leases: dict[str, Any] = {}
        for lease in leases_raw:
            if (
                not isinstance(lease, MutableMapping)
                or not isinstance(lease.get("if_name", None), str)
                or len(lease.get("if_name", "")) == 0
            ):
                continue
            if_name = lease.pop("if_name", None)
            if_descr = lease.pop("if_descr", None)
            if if_name not in leases:
                lease_interfaces[if_name] = if_descr
                leases[if_name] = []
            leases[if_name].append(lease)

        sorted_lease_interfaces: dict[str, Any] = {
            key: lease_interfaces[key] for key in sorted(lease_interfaces)
        }
        sorted_leases: dict[str, Any] = {}
        for if_name in sorted(leases):
            if_subnet = leases[if_name]
            sorted_leases[if_name] = sorted(if_subnet, key=get_ip_key)

        dhcp_leases: dict[str, Any] = {
            "lease_interfaces": sorted_lease_interfaces,
            "leases": sorted_leases,
        }
        aggregate_state, authoritative = self._aggregate_dhcp_source_states(source_states)
        return CategoryResult(dhcp_leases, aggregate_state, authoritative)

    def _record_dhcp_source_state(self, state: CategoryState) -> None:
        """Record the schema-aware state of one DHCP provider."""
        source_states = self._dhcp_source_states_context.get()
        if source_states is not None:
            source_states.append(state)

    @staticmethod
    def _aggregate_dhcp_source_states(
        source_states: list[CategoryState],
    ) -> tuple[CategoryState, bool]:
        """Combine provider states without treating absent plugins as uncertainty."""
        if "pending" in source_states:
            return "pending", False
        if "transient" in source_states:
            return "transient", False
        if "malformed" in source_states:
            return "malformed", False
        if "available" in source_states:
            return "available", True
        return "missing", False

    async def _get_kea_interfaces(self) -> dict[str, Any]:
        """Return the data from the status-aware Kea interface lookup."""
        result = await self._get_kea_interfaces_result()
        if result.state != "available":
            self._record_dhcp_source_state(result.state)
        return result.data

    async def _get_kea_interfaces_result(self) -> CategoryResult[dict[str, Any]]:
        """Return interfaces selected for Kea DHCPv4.

        Returns:
            dict[str, Any]: Mapping of Kea interface identifiers to display
                names when Kea DHCPv4 is enabled; otherwise an empty mapping.
        """
        source_result = CategoryResult.coerce(
            await self._check_optional_get_endpoint(KEA_DHCPV4_GET_ENDPOINT)
        )
        if source_result.state != "available":
            _LOGGER.debug("Kea DHCP interface endpoint unavailable")
            return CategoryResult({}, source_result.state, False)

        response = source_result.data
        if not isinstance(response, MutableMapping):
            return CategoryResult({}, "malformed", False)
        dhcpv4 = response.get("dhcpv4")
        if not isinstance(dhcpv4, MutableMapping):
            return CategoryResult({}, "malformed", False)
        general = dhcpv4.get("general")
        if not isinstance(general, MutableMapping):
            return CategoryResult({}, "malformed", False)
        if "enabled" not in general:
            return CategoryResult({}, "malformed", False)
        enabled = general["enabled"]
        if isinstance(enabled, bool):
            is_enabled = enabled
        elif isinstance(enabled, int) and enabled in {0, 1}:
            is_enabled = enabled == 1
        elif isinstance(enabled, str) and enabled in {"0", "1"}:
            is_enabled = enabled == "1"
        else:
            return CategoryResult({}, "malformed", False)
        if not is_enabled:
            return CategoryResult({}, "available", True)
        interfaces = general.get("interfaces")
        if not isinstance(interfaces, MutableMapping):
            return CategoryResult({}, "malformed", False)
        lease_interfaces: dict[str, Any] = {}
        malformed = False
        for if_name, iface in interfaces.items():
            if not isinstance(iface, MutableMapping):
                malformed = True
                continue
            if api_value_matches(iface.get("selected", 0), "1") and iface.get("value", None):
                lease_interfaces[if_name] = iface.get("value")
        if malformed:
            return CategoryResult(lease_interfaces, "malformed", False)
        return CategoryResult(lease_interfaces, "available", True)

    async def _get_kea_dhcpv4_leases(self, opnsense_tz: tzinfo | None = None) -> list:
        """Return active IPv4 DHCP leases reported by Kea.

        Args:
            opnsense_tz (tzinfo | None, optional): Unused timezone parameter
                accepted for parity with other lease providers.

        Returns:
            list: Normalized Kea IPv4 lease entries. Expired leases,
                non-active rows, malformed rows, and rows without hardware
                addresses are omitted; lease ``type`` is ``static``,
                ``dynamic``, or ``unknown`` depending on reservation data.
        """
        result = await self._get_kea_dhcpv4_leases_result()
        self._record_dhcp_source_state(result.state)
        return result.data

    async def _get_kea_dhcpv4_leases_result(self) -> CategoryResult[list[dict[str, Any]]]:
        """Return status-aware normalized Kea DHCPv4 leases."""
        return await self._get_kea_dhcp_leases_result(
            lease_endpoint=KEA_LEASES4_SEARCH_ENDPOINT,
            reservation_endpoint=KEA_DHCPV4_SEARCH_RESERVATION_ENDPOINT,
            reservation_camelcase_endpoint=KEA_DHCPV4_SEARCH_RESERVATION_CAMELCASE_ENDPOINT,
            require_hardware_address=True,
            service_name="Kea DHCPv4",
        )

    async def _get_kea_dhcpv6_leases(self, opnsense_tz: tzinfo | None = None) -> list:
        """Return active IPv6 DHCP leases reported by Kea.

        Args:
            opnsense_tz (tzinfo | None, optional): Unused timezone parameter
                accepted for parity with other lease providers.

        Returns:
            list: Normalized Kea IPv6 lease entries. Expired leases,
                non-active rows, and malformed rows are omitted; lease
                ``type`` is ``static``, ``dynamic``, or ``unknown`` depending
                on reservation data.
        """
        result = await self._get_kea_dhcpv6_leases_result()
        self._record_dhcp_source_state(result.state)
        return result.data

    async def _get_kea_dhcpv6_leases_result(self) -> CategoryResult[list[dict[str, Any]]]:
        """Return status-aware normalized Kea DHCPv6 leases."""
        return await self._get_kea_dhcp_leases_result(
            lease_endpoint=KEA_LEASES6_SEARCH_ENDPOINT,
            require_hardware_address=False,
            service_name="Kea DHCPv6",
            dynamic_when_reservation_lookup_unavailable=True,
        )

    async def _get_kea_dhcp_leases(
        self,
        lease_endpoint: str,
        service_name: str,
        reservation_endpoint: str | None = None,
        reservation_camelcase_endpoint: str | None = None,
        require_hardware_address: bool = True,
        dynamic_when_reservation_lookup_unavailable: bool = False,
    ) -> list:
        """Return data from the status-aware generic Kea lease lookup."""
        result = await self._get_kea_dhcp_leases_result(
            lease_endpoint=lease_endpoint,
            service_name=service_name,
            reservation_endpoint=reservation_endpoint,
            reservation_camelcase_endpoint=reservation_camelcase_endpoint,
            require_hardware_address=require_hardware_address,
            dynamic_when_reservation_lookup_unavailable=(
                dynamic_when_reservation_lookup_unavailable
            ),
        )
        self._record_dhcp_source_state(result.state)
        return result.data

    async def _get_kea_dhcp_leases_result(
        self,
        lease_endpoint: str,
        service_name: str,
        reservation_endpoint: str | None = None,
        reservation_camelcase_endpoint: str | None = None,
        require_hardware_address: bool = True,
        dynamic_when_reservation_lookup_unavailable: bool = False,
    ) -> CategoryResult[list[dict[str, Any]]]:
        """Return active DHCP leases reported by a Kea lease endpoint.

        Args:
            lease_endpoint (str): Kea lease search endpoint path.
            service_name (str): Display name used for debug logging.
            reservation_endpoint (str | None, optional): Kea reservation endpoint path.
            reservation_camelcase_endpoint (str | None, optional): CamelCase reservation endpoint path.
            require_hardware_address (bool, optional): Whether to skip rows without ``hwaddr``.
            dynamic_when_reservation_lookup_unavailable (bool, optional): Whether lease rows with
                explicit ``is_reserved`` set to false should be treated as ``dynamic`` when
                reservation metadata is unavailable.

        Returns:
            CategoryResult[list[dict[str, Any]]]: Normalized leases and the
                combined lease/reservation endpoint state.
        """
        source_result = CategoryResult.coerce(
            await self._check_optional_get_endpoint(lease_endpoint)
        )
        if source_result.state != "available":
            _LOGGER.debug("%s not installed", service_name)
            return CategoryResult([], source_result.state, False)
        response = source_result.data
        if not isinstance(response, MutableMapping):
            return CategoryResult([], "malformed", False)
        if "rows" not in response or not isinstance(response["rows"], list):
            return CategoryResult([], "malformed", False)
        malformed = False
        auxiliary_state: CategoryState = "available"
        res_info: list[Any] | None
        if reservation_endpoint is None or reservation_camelcase_endpoint is None:
            res_info = None
        else:
            selected_reservation_endpoint = await self._get_endpoint_path(
                snake_case_path=reservation_endpoint,
                camel_case_path=reservation_camelcase_endpoint,
            )
            reservation_result = CategoryResult.coerce(
                await self._check_optional_get_endpoint(selected_reservation_endpoint)
            )
            if reservation_result.state != "available":
                _LOGGER.debug("%s reservation endpoint unavailable", service_name)
                res_info = None
                if reservation_result.state != "missing":
                    auxiliary_state = reservation_result.state
            else:
                res_resp = reservation_result.data
                if not isinstance(res_resp, MutableMapping):
                    malformed = True
                    res_info = None
                    res_resp = {}
                if "rows" not in res_resp or not isinstance(res_resp["rows"], list):
                    malformed = True
                    _LOGGER.debug(
                        "%s reservation lookup returned invalid rows payload", service_name
                    )
                    res_info = None
                else:
                    res_info = res_resp["rows"]
        reservations = {}
        if res_info is not None:
            for res in res_info:
                if not isinstance(res, MutableMapping):
                    malformed = True
                    continue
                if res.get("hw_address", None):
                    reservations.update({res.get("hw_address"): res.get("ip_address", "")})
        leases_info: list = response["rows"]
        leases: list = []
        for lease_info in leases_info:
            if not isinstance(lease_info, MutableMapping):
                malformed = True
                continue
            if not api_value_matches(lease_info.get("state"), "0") or (
                require_hardware_address and not lease_info.get("hwaddr", None)
            ):
                continue
            if not isinstance(lease_info.get("address"), str) or not isinstance(
                lease_info.get("if_name"), str
            ):
                malformed = True
                continue
            lease: dict[str, Any] = {}
            lease["address"] = lease_info.get("address", None)
            lease["hostname"] = (
                lease_info.get("hostname", None).strip(".")
                if isinstance(lease_info.get("hostname", None), str)
                and len(lease_info.get("hostname", "")) > 0
                else None
            )
            lease["if_descr"] = lease_info.get("if_descr", None)
            lease["if_name"] = lease_info.get("if_name", None)
            if self._is_reserved_lease(lease_info.get("is_reserved")):
                lease["type"] = "static"
            elif res_info is None:
                if (
                    dynamic_when_reservation_lookup_unavailable
                    and "is_reserved" in lease_info
                    and not self._is_reserved_lease(lease_info.get("is_reserved"))
                ):
                    lease["type"] = "dynamic"
                else:
                    lease["type"] = "unknown"
            elif (
                lease_info.get("hwaddr", None)
                and lease_info.get("hwaddr") in reservations
                and reservations[lease_info.get("hwaddr")] == lease_info.get("address", None)
            ):
                lease["type"] = "static"
            else:
                lease["type"] = "dynamic"
            lease["mac"] = lease_info.get("hwaddr", None) or None
            if "duid" in lease_info:
                lease["duid"] = lease_info.get("duid")
            self._copy_lease_identity_fields(lease, lease_info)
            if try_to_int(lease_info.get("expire", None)):
                lease["expires"] = timestamp_to_datetime(
                    try_to_int(lease_info.get("expire", None)) or 0
                )
                if lease["expires"] < datetime.now().astimezone():
                    continue
            else:
                lease["expires"] = lease_info.get("expire", None)
            leases.append(lease)
        result_state: CategoryState = "malformed" if malformed else auxiliary_state
        return CategoryResult(leases, result_state, result_state == "available")

    def _keep_latest_leases(self, reservations: list[dict]) -> list[dict]:
        """Deduplicate leases and keep the entry with the latest expiration.

        Args:
            reservations (list[dict]): Lease or reservation rows that may
                include duplicate entries with different ``expire`` values.

        Returns:
            list[dict]: Deduplicated rows where all fields except ``expire``
                define identity and the highest expiration value is retained.
        """
        seen: dict[tuple, dict] = {}

        for entry in reservations:
            if not isinstance(entry, MutableMapping):
                continue
            expire = try_to_int(entry.get("expire"), -1)
            if expire is None:
                continue
            # Create a key from all fields except 'expire'
            key = tuple(
                sorted(
                    (key, self._normalize_lease_key_value(value))
                    for key, value in entry.items()
                    if key != "expire"
                )
            )

            # Keep the entry with the latest expiration time
            seen_expire = try_to_int(seen.get(key, {}).get("expire"), -1)
            if seen_expire is None:
                seen_expire = -1
            if key not in seen or expire > seen_expire:
                seen[key] = dict(entry)

        return list(seen.values())

    async def _get_dnsmasq_leases(self, opnsense_tz: tzinfo | None = None) -> list:
        """Return active IPv4 and IPv6 DHCP leases reported by dnsmasq.

        Args:
            opnsense_tz (tzinfo | None, optional): Unused timezone parameter
                accepted for parity with other lease providers.

        Returns:
            list: Normalized dnsmasq lease entries. Duplicate rows are reduced
                to the latest expiration, expired rows are omitted, and lease
                ``type`` is derived from dnsmasq reservation metadata.
        """
        result = await self._get_dnsmasq_leases_result()
        self._record_dhcp_source_state(result.state)
        return result.data

    async def _get_dnsmasq_leases_result(self) -> CategoryResult[list[dict[str, Any]]]:
        """Return status-aware normalized dnsmasq leases."""
        source_result = CategoryResult.coerce(
            await self._check_optional_get_endpoint(DNSMASQ_LEASES_SEARCH_ENDPOINT)
        )
        if source_result.state != "available":
            _LOGGER.debug("Dnsmasq DHCP not installed")
            return CategoryResult([], source_result.state, False)
        response = source_result.data
        if not isinstance(response, MutableMapping):
            return CategoryResult([], "malformed", False)
        if "rows" not in response or not isinstance(response["rows"], list):
            return CategoryResult([], "malformed", False)
        leases_info: list = response["rows"]
        malformed = any(not isinstance(row, MutableMapping) for row in leases_info)
        cleaned_leases = self._keep_latest_leases(leases_info)

        leases: list = []
        for lease_info in cleaned_leases:
            lease: dict[str, Any] = {}
            lease["address"] = lease_info.get("address", None)
            lease["hostname"] = (
                lease_info.get("hostname", None)
                if isinstance(lease_info.get("hostname", None), str)
                and lease_info.get("hostname", None) != "*"
                and len(lease_info.get("hostname", "")) > 0
                else None
            )
            lease["if_descr"] = lease_info.get("if_descr", None)
            if_name = lease_info.get("if_name", None)
            if not isinstance(if_name, str) or len(if_name) == 0:
                if_name = lease_info.get("if", None)
            if not isinstance(lease_info.get("address"), str) or not isinstance(if_name, str):
                malformed = True
                continue
            lease["if_name"] = if_name
            if self._is_reserved_lease(lease_info.get("is_reserved")):
                lease["type"] = "static"
            else:
                lease["type"] = "dynamic"
            lease["mac"] = (
                lease_info.get("hwaddr", None)
                if isinstance(lease_info.get("hwaddr", None), str)
                and len(lease_info.get("hwaddr", "")) > 0
                else None
            )
            self._copy_lease_identity_fields(lease, lease_info)

            if try_to_int(lease_info.get("expire", None)):
                lease["expires"] = timestamp_to_datetime(
                    try_to_int(lease_info.get("expire", None)) or 0
                )
                if lease["expires"] < datetime.now().astimezone():
                    continue
            else:
                lease["expires"] = lease_info.get("expire", None)
            leases.append(lease)
        state: CategoryState = "malformed" if malformed else "available"
        return CategoryResult(leases, state, state == "available")

    async def _get_isc_dhcpv4_leases(self, opnsense_tz: tzinfo | None = None) -> list:
        """Return active IPv4 DHCP leases reported by ISC DHCP.

        Args:
            opnsense_tz (tzinfo | None, optional): Timezone used to localize
                ISC ``ends`` timestamps. Fetched from OPNsense when omitted.

        Returns:
            list: Normalized ISC DHCPv4 lease entries. Non-active, expired,
                malformed, and MAC-less rows are omitted.
        """
        lease_endpoint = await self._get_endpoint_path(
            snake_case_path=ISC_DHCPV4_LEASES_SEARCH_ENDPOINT,
            camel_case_path=ISC_DHCPV4_LEASES_SEARCH_CAMELCASE_ENDPOINT,
        )
        source_result = CategoryResult.coerce(
            await self._check_optional_get_endpoint(lease_endpoint)
        )
        if source_result.state != "available":
            self._record_dhcp_source_state(source_result.state)
            _LOGGER.debug("ISC DHCPv4 lease endpoint unavailable")
            return []
        response = source_result.data
        if not isinstance(response, MutableMapping):
            self._record_dhcp_source_state("malformed")
            return []
        if "rows" not in response or not isinstance(response["rows"], list):
            self._record_dhcp_source_state("malformed")
            return []
        leases_info: list = response["rows"]
        if opnsense_tz is None:
            opnsense_tz = await self._get_opnsense_timezone()
        leases: list = []
        malformed = False
        for lease_info in leases_info:
            if not isinstance(lease_info, MutableMapping):
                malformed = True
                continue
            if lease_info.get("state", "") != "active" or not lease_info.get("mac", None):
                continue
            if not isinstance(lease_info.get("address"), str) or not isinstance(
                lease_info.get("if"), str
            ):
                malformed = True
                continue
            lease: dict[str, Any] = {}
            lease["address"] = lease_info.get("address", None)
            lease["hostname"] = (
                lease_info.get("hostname", None)
                if isinstance(lease_info.get("hostname", None), str)
                and len(lease_info.get("hostname", "")) > 0
                else None
            )
            lease["if_descr"] = lease_info.get("if_descr", None)
            lease["if_name"] = lease_info.get("if", None)
            lease["type"] = lease_info.get("type", None)
            lease["mac"] = lease_info.get("mac", None)
            if lease_info.get("ends", None):
                try:
                    dt: datetime = datetime.strptime(
                        lease_info.get("ends", None), "%Y/%m/%d %H:%M:%S"
                    )
                except TypeError, ValueError:
                    malformed = True
                    continue
                lease["expires"] = dt.replace(tzinfo=opnsense_tz)
                if lease["expires"] < datetime.now().astimezone():
                    continue
            else:
                lease["expires"] = lease_info.get("ends", None)
            leases.append(lease)
        self._record_dhcp_source_state("malformed" if malformed else "available")
        return leases

    async def _get_isc_dhcpv6_leases(self, opnsense_tz: tzinfo | None = None) -> list:
        """Return active IPv6 DHCP leases reported by ISC DHCP.

        Args:
            opnsense_tz (tzinfo | None, optional): Timezone used to localize
                ISC ``ends`` timestamps. Fetched from OPNsense when omitted.

        Returns:
            list: Normalized ISC DHCPv6 lease entries. Non-active, expired,
                malformed, and MAC-less rows are omitted.
        """
        lease_endpoint = await self._get_endpoint_path(
            snake_case_path=ISC_DHCPV6_LEASES_SEARCH_ENDPOINT,
            camel_case_path=ISC_DHCPV6_LEASES_SEARCH_CAMELCASE_ENDPOINT,
        )
        source_result = CategoryResult.coerce(
            await self._check_optional_get_endpoint(lease_endpoint)
        )
        if source_result.state != "available":
            self._record_dhcp_source_state(source_result.state)
            _LOGGER.debug("ISC DHCPv6 lease endpoint unavailable")
            return []
        response = source_result.data
        if not isinstance(response, MutableMapping):
            self._record_dhcp_source_state("malformed")
            return []
        if "rows" not in response or not isinstance(response["rows"], list):
            self._record_dhcp_source_state("malformed")
            return []
        leases_info: list = response["rows"]
        if opnsense_tz is None:
            opnsense_tz = await self._get_opnsense_timezone()
        leases: list = []
        malformed = False
        for lease_info in leases_info:
            if not isinstance(lease_info, MutableMapping):
                malformed = True
                continue
            if lease_info.get("state", "") != "active" or not lease_info.get("mac", None):
                continue
            if not isinstance(lease_info.get("address"), str) or not isinstance(
                lease_info.get("if"), str
            ):
                malformed = True
                continue
            lease: dict[str, Any] = {}
            lease["address"] = lease_info.get("address", None)
            lease["hostname"] = (
                lease_info.get("hostname", None)
                if isinstance(lease_info.get("hostname", None), str)
                and len(lease_info.get("hostname", "")) > 0
                else None
            )
            lease["if_descr"] = lease_info.get("if_descr", None)
            lease["if_name"] = lease_info.get("if", None)
            lease["type"] = lease_info.get("type", None)
            lease["mac"] = lease_info.get("mac", None)
            if lease_info.get("ends", None):
                try:
                    dt: datetime = datetime.strptime(
                        lease_info.get("ends", None), "%Y/%m/%d %H:%M:%S"
                    )
                except TypeError, ValueError:
                    malformed = True
                    continue
                lease["expires"] = dt.replace(tzinfo=opnsense_tz)
                if lease["expires"] < datetime.now().astimezone():
                    continue
            else:
                lease["expires"] = lease_info.get("ends", None)
            leases.append(lease)
        self._record_dhcp_source_state("malformed" if malformed else "available")
        return leases
