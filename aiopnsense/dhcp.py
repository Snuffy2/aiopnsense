"""DHCP and ARP methods for OPNsenseClient."""

from collections.abc import MutableMapping
from datetime import datetime, tzinfo
from typing import Any

from ._typing import AiopnsenseClientProtocol
from .helpers import (
    _LOGGER,
    _log_errors,
    api_value_matches,
    dict_get,
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
DNSMASQ_RANGES_SEARCH_ENDPOINT = "/api/dnsmasq/settings/search_range"
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
    def _has_authoritative_reservation_metadata(raw_reserved: object) -> bool:
        """Return whether a Kea reservation flag has a supported representation.

        Args:
            raw_reserved (object): Raw ``is_reserved`` field returned by Kea.

        Returns:
            bool: Whether the value can authoritatively classify the lease.
        """
        return isinstance(raw_reserved, list) or (
            type(raw_reserved) in (str, int, bool)
            and (api_value_matches(raw_reserved, "0") or api_value_matches(raw_reserved, "1"))
        )

    @staticmethod
    def _expiration_to_datetime(expiration: int | None) -> datetime | None:
        """Convert a DHCP expiration timestamp without propagating platform overflow.

        Args:
            expiration (int | None): Parsed DHCP expiration timestamp.

        Returns:
            datetime | None: Converted expiration, or ``None`` when the
                timestamp is outside the platform-supported range.
        """
        try:
            return timestamp_to_datetime(expiration)
        except OverflowError, OSError:
            return None

    @staticmethod
    def _is_expired_kea_lease(lease_info: MutableMapping[str, Any], current_time: datetime) -> bool:
        """Return whether a Kea lease has an expiration in the past.

        Args:
            lease_info (MutableMapping[str, Any]): Raw Kea lease row.
            current_time (datetime): Point-in-time reference used for expiration.

        Returns:
            bool: Whether the row has a parseable expiration that has passed.
        """
        expiration = try_to_int(lease_info.get("expire"))
        expiration_datetime = DHCPMixin._expiration_to_datetime(expiration) if expiration else None
        return bool(expiration_datetime and expiration_datetime < current_time)

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
        # [{'hostname': '?', 'ip-address': '<ip>', 'mac-address': '<mac>', 'interface': 'em0', 'expires': 1199, 'type': 'ethernet'}, ...]
        resolve_flag = "yes" if resolve_hostnames else "no"
        if not await self._is_get_endpoint_available(ARP_TABLE_ENDPOINT):
            _LOGGER.debug("ARP endpoint unavailable")
            return []

        arp_endpoint_resolve = f"{ARP_TABLE_ENDPOINT}?resolve={resolve_flag}"
        arp_table_info = await self._safe_dict_get(arp_endpoint_resolve)
        arp_table: list = arp_table_info.get("rows", [])
        return arp_table

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
        if opnsense_tz is None:
            opnsense_tz = await self._get_opnsense_timezone()
        leases_raw: list = await self._get_kea_dhcpv4_leases(opnsense_tz=opnsense_tz)
        leases_raw += await self._get_kea_dhcpv6_leases(opnsense_tz=opnsense_tz)
        leases_raw += await self._get_isc_dhcpv4_leases(opnsense_tz=opnsense_tz)
        leases_raw += await self._get_isc_dhcpv6_leases(opnsense_tz=opnsense_tz)
        leases_raw += await self._get_dnsmasq_leases(opnsense_tz=opnsense_tz)

        leases: dict[str, Any] = {}
        # ISC lease endpoints expose interface metadata only for interfaces with lease rows,
        # and OPNsense has no ISC configuration endpoint that lists enabled DHCP interfaces.
        # Therefore, zero-lease ISC scopes cannot be discovered without incorrectly treating
        # every assigned interface as DHCP-capable.
        lease_interfaces: dict[str, Any] = await self._get_kea_interfaces()
        lease_interfaces.update(await self._get_dnsmasq_interfaces())
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
        return dhcp_leases

    async def _get_kea_interfaces(self) -> dict[str, Any]:
        """Return interfaces selected for Kea DHCPv4.

        Returns:
            dict[str, Any]: Mapping of Kea interface identifiers to display
                names when Kea DHCPv4 is enabled; otherwise an empty mapping.
        """
        if not await self._is_get_endpoint_available(KEA_DHCPV4_GET_ENDPOINT):
            _LOGGER.debug("Kea DHCP interface endpoint unavailable")
            return {}

        response = await self._safe_dict_get(KEA_DHCPV4_GET_ENDPOINT)
        lease_interfaces: dict[str, Any] = {}
        general = dict_get(response, "dhcpv4.general", {})
        if not isinstance(general, MutableMapping):
            return {}
        if not api_value_matches(general.get("enabled", "0"), "1"):
            return {}
        interfaces = general.get("interfaces", {})
        if not isinstance(interfaces, MutableMapping):
            return {}
        for if_name, iface in interfaces.items():
            if not isinstance(iface, MutableMapping):
                continue
            if api_value_matches(iface.get("selected", 0), "1") and iface.get("value", None):
                lease_interfaces[if_name] = iface.get("value")
        return lease_interfaces

    async def _get_dnsmasq_interfaces(self) -> dict[str, Any]:
        """Return interfaces with configured Dnsmasq DHCP ranges.

        Returns:
            dict[str, Any]: Mapping of Dnsmasq range interface identifiers to
                display names, including interfaces without active leases.
        """
        if not await self._is_get_endpoint_available(DNSMASQ_RANGES_SEARCH_ENDPOINT):
            _LOGGER.debug("Dnsmasq DHCP range endpoint unavailable")
            return {}

        response = await self._safe_dict_get(DNSMASQ_RANGES_SEARCH_ENDPOINT)
        ranges = response.get("rows", [])
        if not isinstance(ranges, list):
            return {}

        lease_interfaces: dict[str, Any] = {}
        for range_info in ranges:
            if not isinstance(range_info, MutableMapping):
                continue
            if_name = range_info.get("interface")
            if not isinstance(if_name, str) or not if_name:
                continue
            if_descr = range_info.get("%interface")
            lease_interfaces[if_name] = (
                if_descr if isinstance(if_descr, str) and if_descr else if_name
            )
        return lease_interfaces

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
        return await self._get_kea_dhcp_leases(
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
        return await self._get_kea_dhcp_leases(
            lease_endpoint=KEA_LEASES6_SEARCH_ENDPOINT,
            require_hardware_address=False,
            service_name="Kea DHCPv6",
        )

    async def _get_kea_dhcp_leases(
        self,
        lease_endpoint: str,
        service_name: str,
        reservation_endpoint: str | None = None,
        reservation_camelcase_endpoint: str | None = None,
        require_hardware_address: bool = True,
    ) -> list:
        """Return active DHCP leases reported by a Kea lease endpoint.

        Args:
            lease_endpoint (str): Kea lease search endpoint path.
            service_name (str): Display name used for debug logging.
            reservation_endpoint (str | None, optional): Kea reservation endpoint path.
            reservation_camelcase_endpoint (str | None, optional): CamelCase reservation endpoint path.
            require_hardware_address (bool, optional): Whether to skip rows without ``hwaddr``.

        Returns:
            list: Normalized Kea lease entries for the supplied endpoint.
        """
        if not await self._is_get_endpoint_available(lease_endpoint):
            _LOGGER.debug("%s not installed", service_name)
            return []
        response = await self._safe_dict_get(lease_endpoint)
        if not isinstance(response.get("rows", None), list):
            return []
        leases_info: list = response.get("rows", [])
        current_time = datetime.now().astimezone()
        res_info: list[Any] | None
        needs_reservation_lookup = any(
            isinstance(lease_info, MutableMapping)
            and api_value_matches(lease_info.get("state"), "0")
            and (not require_hardware_address or bool(lease_info.get("hwaddr")))
            and not self._is_expired_kea_lease(lease_info, current_time)
            and not self._has_authoritative_reservation_metadata(lease_info.get("is_reserved"))
            for lease_info in leases_info
        )
        if (
            reservation_endpoint is None
            or reservation_camelcase_endpoint is None
            or not needs_reservation_lookup
        ):
            res_info = None
        else:
            selected_reservation_endpoint = await self._get_endpoint_path(
                snake_case_path=reservation_endpoint,
                camel_case_path=reservation_camelcase_endpoint,
            )
            if not await self._is_get_endpoint_available(selected_reservation_endpoint):
                _LOGGER.debug("%s reservation endpoint unavailable", service_name)
                res_info = None
            else:
                res_resp = await self._safe_dict_get(selected_reservation_endpoint)
                if not isinstance(res_resp.get("rows", None), list):
                    _LOGGER.debug(
                        "%s reservation lookup returned invalid rows payload", service_name
                    )
                    res_info = None
                else:
                    res_info = res_resp.get("rows", [])
        reservations = {}
        if res_info is not None:
            for res in res_info:
                if not isinstance(res, MutableMapping):
                    continue
                if res.get("hw_address", None):
                    reservations.update({res.get("hw_address"): res.get("ip_address", "")})
        leases: list = []
        for lease_info in leases_info:
            if (
                lease_info is None
                or not isinstance(lease_info, MutableMapping)
                or not api_value_matches(lease_info.get("state"), "0")
                or (require_hardware_address and not lease_info.get("hwaddr", None))
            ):
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
            raw_reserved = lease_info.get("is_reserved")
            if self._has_authoritative_reservation_metadata(raw_reserved):
                lease["type"] = "static" if self._is_reserved_lease(raw_reserved) else "dynamic"
            elif res_info is None:
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
            expiration = try_to_int(lease_info.get("expire", None))
            if expiration:
                expiration_datetime = self._expiration_to_datetime(expiration)
                lease["expires"] = (
                    expiration_datetime
                    if expiration_datetime is not None
                    else lease_info.get("expire", None)
                )
                if expiration_datetime is not None and expiration_datetime < current_time:
                    continue
            else:
                lease["expires"] = lease_info.get("expire", None)
            leases.append(lease)
        return leases

    def _keep_latest_leases(self, reservations: list[dict]) -> list[dict]:
        """Deduplicate leases and keep the entry with the latest expiration.

        Args:
            reservations (list[dict]): Lease or reservation rows that may
                include duplicate entries with different ``expire`` values.

        Returns:
            list[dict]: Deduplicated rows where all fields except ``expire``
                define identity and the highest expiration value is retained.
        """
        seen: dict[tuple, tuple[int, dict]] = {}

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
            previous_entry = seen.get(key)
            if previous_entry is None or expire > previous_entry[0]:
                seen[key] = (expire, dict(entry))

        return [entry for _, entry in seen.values()]

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
        if not await self._is_get_endpoint_available(DNSMASQ_LEASES_SEARCH_ENDPOINT):
            _LOGGER.debug("Dnsmasq DHCP not installed")
            return []
        response = await self._safe_dict_get(DNSMASQ_LEASES_SEARCH_ENDPOINT)
        leases_info: list = response.get("rows", [])
        if not isinstance(leases_info, list):
            return []
        cleaned_leases = self._keep_latest_leases(leases_info)

        leases: list = []
        current_time = datetime.now().astimezone()
        for lease_info in cleaned_leases:
            if not isinstance(lease_info, MutableMapping):
                continue
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

            expiration = try_to_int(lease_info.get("expire", None))
            if expiration:
                expiration_datetime = self._expiration_to_datetime(expiration)
                lease["expires"] = (
                    expiration_datetime
                    if expiration_datetime is not None
                    else lease_info.get("expire", None)
                )
                if expiration_datetime is not None and expiration_datetime < current_time:
                    continue
            else:
                lease["expires"] = lease_info.get("expire", None)
            leases.append(lease)
        return leases

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
        if not await self._is_get_endpoint_available(lease_endpoint):
            _LOGGER.debug("ISC DHCPv4 lease endpoint unavailable")
            return []
        response = await self._safe_dict_get(lease_endpoint)
        leases_info: list = response.get("rows", [])
        if not isinstance(leases_info, list):
            return []
        if opnsense_tz is None:
            opnsense_tz = await self._get_opnsense_timezone()
        leases: list = []
        current_time = datetime.now().astimezone()
        for lease_info in leases_info:
            if (
                not isinstance(lease_info, MutableMapping)
                or lease_info.get("state", "") != "active"
                or not lease_info.get("mac", None)
            ):
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
                    ).replace(tzinfo=opnsense_tz)
                except TypeError, ValueError:
                    continue
                lease["expires"] = dt
                if lease["expires"] < current_time:
                    continue
            else:
                lease["expires"] = lease_info.get("ends", None)
            leases.append(lease)
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
        if not await self._is_get_endpoint_available(lease_endpoint):
            _LOGGER.debug("ISC DHCPv6 lease endpoint unavailable")
            return []
        response = await self._safe_dict_get(lease_endpoint)
        leases_info: list = response.get("rows", [])
        if not isinstance(leases_info, list):
            return []
        if opnsense_tz is None:
            opnsense_tz = await self._get_opnsense_timezone()
        leases: list = []
        current_time = datetime.now().astimezone()
        for lease_info in leases_info:
            if (
                not isinstance(lease_info, MutableMapping)
                or lease_info.get("state", "") != "active"
                or not lease_info.get("mac", None)
            ):
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
                    ).replace(tzinfo=opnsense_tz)
                except TypeError, ValueError:
                    continue
                lease["expires"] = dt
                if lease["expires"] < current_time:
                    continue
            else:
                lease["expires"] = lease_info.get("ends", None)
            leases.append(lease)
        return leases
