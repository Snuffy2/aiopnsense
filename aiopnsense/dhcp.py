"""DHCP, ARP, and NDP data normalization and methods for OPNsenseClient."""

from collections.abc import Mapping, MutableMapping
import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
import ipaddress
import re
from typing import Any, Literal
from warnings import deprecated

from ._dhcp_leases import (
    _DNSMASQ_LEASE_BACKEND,
    _ISC_LEASE_BACKEND,
    _has_authoritative_reservation_metadata,
    _is_live_row,
    _kea_lease_backend,
    _normalize_leases,
)
from ._endpoint_outcome import _EndpointOutcome
from ._error_policy import _log_errors
from ._styled_endpoint import _StyledEndpoint
from ._typing import AiopnsenseClientProtocol
from .access import Access, AccessDict, AccessList
from .helpers import _LOGGER, api_value_matches, coerce_bool, dict_get, get_ip_key, try_to_int

ARP_TABLE_ENDPOINT = "/api/diagnostics/interface/search_arp"
NDP_TABLE_ENDPOINT = "/api/diagnostics/interface/search_ndp"
HOST_OBSERVATIONS_ENDPOINT = "/api/hostdiscovery/service/search"
KEA_DHCPV4_GET_ENDPOINT = "/api/kea/dhcpv4/get"
KEA_LEASES4_SEARCH_ENDPOINT = "/api/kea/leases4/search"
KEA_LEASES6_SEARCH_ENDPOINT = "/api/kea/leases6/search"
KEA_DHCPV4_SEARCH_RESERVATION_ENDPOINT = _StyledEndpoint(
    snake_case="/api/kea/dhcpv4/search_reservation",
    camel_case="/api/kea/dhcpv4/searchReservation",
)
DNSMASQ_LEASES_SEARCH_ENDPOINT = "/api/dnsmasq/leases/search"
DNSMASQ_RANGES_SEARCH_ENDPOINT = "/api/dnsmasq/settings/search_range"
ISC_DHCPV4_LEASES_SEARCH_ENDPOINT = _StyledEndpoint(
    snake_case="/api/dhcpv4/leases/search_lease",
    camel_case="/api/dhcpv4/leases/searchLease",
)
ISC_DHCPV6_LEASES_SEARCH_ENDPOINT = _StyledEndpoint(
    snake_case="/api/dhcpv6/leases/search_lease",
    camel_case="/api/dhcpv6/leases/searchLease",
)


_MAC_PATTERN = re.compile(r"^([0-9a-f]{2}:){5}([0-9a-f]{2})$")


class DHCPMixin(AiopnsenseClientProtocol):
    """DHCP and neighbor-table methods for OPNsenseClient."""

    @dataclass(frozen=True, slots=True)
    class ArpNdpEntry:
        """Normalized neighbor row, with copied metadata isolated from its source.

        Attributes:
            mac (str | None): Permissively normalized MAC address, including malformed values.
            valid_mac (str | None): Canonical six-octet MAC address, or None if invalid.
            ipv4_address (str | None): Canonical IPv4 address, or None for other input.
            ipv6_address (str | None): Canonical IPv6 address with any interface scope retained.
            hostname (str | None): Trimmed hostname with surrounding question marks removed.
            manufacturer (str | None): Trimmed manufacturer, or None if absent or blank.
            expired (bool): Expiration flag normalized by ``coerce_bool``. Nonzero numbers
                and trimmed case-insensitive ``1``, ``true``, ``yes``, or ``on`` strings are
                True; absent flags and other values are False.
            interface (object): Copied interface description, falling back to the interface
                identifier when the description is missing or blank.
            expires (object): Copied raw expiration metadata.
            arp_type (object): Copied raw neighbor entry type.
            interface_name (object): Copied interface identifier from ``intf`` or ``interface``.
            interface_description (object): Copied interface description from ``intf_description``.
            permanent (bool): Permanent-entry flag normalized by ``coerce_bool``.
        """

        mac: str | None
        valid_mac: str | None
        ipv4_address: str | None
        ipv6_address: str | None
        hostname: str | None
        manufacturer: str | None
        expired: bool
        interface: object
        expires: object
        arp_type: object
        interface_name: object = None
        interface_description: object = None
        permanent: bool = False

    @dataclass(frozen=True, slots=True)
    class ArpNdpTable:
        """Normalized ARP or NDP table returned by one lookup.

        Attributes:
            entries (tuple[ArpNdpEntry, ...]): One entry for every mapping row, in source order.
                Rows with missing or invalid identities are retained. Strict MAC/IP fields
                are None when invalid; the permissively normalized ``mac`` text is preserved.
            unparsed_row_count (int): Number of source rows that are not mappings and therefore
                have no entry. A nonzero count means the entries may not describe the whole
                table.
            raw_rows (tuple[object, ...]): Deep copies of every source row in source order, with
                mapping rows copied as plain dicts, isolated from both the response and the
                entries, for diagnostics and access to original or unknown fields.
            fetched_at (datetime): UTC-aware time when this table was normalized. The value
                stays fixed for the lifetime of this table.
        """

        entries: tuple[DHCPMixin.ArpNdpEntry, ...]
        unparsed_row_count: int
        raw_rows: tuple[object, ...]
        fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @dataclass(frozen=True, slots=True)
    class HostObservation:
        """One validated MAC and IP sighting from OPNsense host discovery.

        Attributes:
            mac (str): Canonical lowercase six-octet MAC address.
            ip (str): Compressed IPv4 or IPv6 address.
            last_seen (datetime): UTC-aware timestamp when the host was observed.
        """

        mac: str
        ip: str
        last_seen: datetime

    @dataclass(frozen=True, slots=True)
    class HostObservations:
        """Validated host-discovery observations and their data source.

        Attributes:
            source (Literal['discovery', 'arp-ndp']): Source of the observation result.
                ``arp-ndp`` means host discovery is disabled and has no timestamps.
            entries (tuple[DHCPMixin.HostObservation, ...]): Timestamped discovery entries;
                empty for the timestamp-free ``arp-ndp`` fallback.
        """

        source: Literal["discovery", "arp-ndp"]
        entries: tuple[DHCPMixin.HostObservation, ...]

    @staticmethod
    def _normalized_ip(value: object, *, version: int) -> str | None:
        """Return a canonical address if it belongs to the requested IP family.

        Args:
            value (object): Raw address.
            version (int): Expected IP family.

        Returns:
            str | None: Compressed address, or None if invalid or of the wrong family.
        """
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            address = ipaddress.ip_address(value.strip())
        except ValueError:
            return None
        return address.compressed if address.version == version else None

    @staticmethod
    def _parse_neighbor_row(row: Mapping[str, object]) -> ArpNdpEntry:
        """Copy and normalize one raw ARP or NDP neighbor row.

        Args:
            row (Mapping[str, object]): Mapping returned by the neighbor table API.

        Returns:
            ArpNdpEntry: A normalized entry whose metadata objects are deep-copied from the
                source row.
        """
        copied = copy.deepcopy(dict(row))
        raw_mac = copied.get("mac")
        if not isinstance(raw_mac, str) or not raw_mac.strip():
            raw_mac = copied.get("mac-address")
        mac = raw_mac.strip().lower().replace("-", ":") if isinstance(raw_mac, str) else ""
        valid_mac = mac if _MAC_PATTERN.match(mac) else None
        raw_ip = copied.get("ip")
        if not isinstance(raw_ip, str) or not raw_ip.strip():
            raw_ip = copied.get("ip-address")
        hostname = copied.get("hostname")
        if isinstance(hostname, str):
            hostname = hostname.strip().strip("?").strip() or None
        else:
            hostname = None
        manufacturer = copied.get("manufacturer")
        if isinstance(manufacturer, str):
            manufacturer = manufacturer.strip() or None
        else:
            manufacturer = None
        interface_name = copied.get("intf", copied.get("interface"))
        interface_description = copied.get("intf_description")
        interface = interface_description
        if interface_description is None or (
            isinstance(interface_description, str) and not interface_description.strip()
        ):
            interface = interface_name
        return DHCPMixin.ArpNdpEntry(
            mac=mac or None,
            valid_mac=valid_mac,
            ipv4_address=DHCPMixin._normalized_ip(raw_ip, version=4),
            ipv6_address=DHCPMixin._normalized_ip(raw_ip, version=6),
            hostname=hostname,
            manufacturer=manufacturer,
            expired=coerce_bool(copied.get("expired")),
            interface=interface,
            expires=copied.get("expires"),
            arp_type=copied.get("type"),
            interface_name=interface_name,
            interface_description=interface_description,
            permanent=coerce_bool(copied.get("permanent")),
        )

    @staticmethod
    def _parse_neighbor_table(rows: list | None) -> ArpNdpTable | None:
        """Normalize raw neighbor rows into a table.

        Args:
            rows (list | None): Raw rows from a neighbor table getter.

        Returns:
            ArpNdpTable | None: Normalized table, or None when the lookup was unavailable.
        """
        if rows is None:
            return None
        return DHCPMixin.ArpNdpTable(
            entries=tuple(
                DHCPMixin._parse_neighbor_row(row) for row in rows if isinstance(row, Mapping)
            ),
            unparsed_row_count=sum(1 for row in rows if not isinstance(row, Mapping)),
            raw_rows=tuple(
                copy.deepcopy(dict(row) if isinstance(row, Mapping) else row) for row in rows
            ),
        )

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

    @_log_errors
    async def _get_arp_table(self, resolve_hostnames: bool = False) -> list | None:
        """Return active ARP table entries.

        Args:
            resolve_hostnames (bool): Whether reverse DNS lookups should be requested.

        Returns:
            list | None: ARP rows from OPNsense, optionally with resolved hostnames,
                including fields such as IP address, MAC address, interface,
                expiration, and entry type when provided by the endpoint. An
                unavailable endpoint, or an absent or malformed rows field,
                returns None rather than an authoritative empty table.
        """
        # [{'hostname': '?', 'ip-address': '<ip>', 'mac-address': '<mac>', 'interface': 'em0', 'expires': 1199, 'type': 'ethernet'}, ...]
        resolve_flag = "yes" if resolve_hostnames else "no"
        outcome = await self._fetch_endpoint_outcome(f"{ARP_TABLE_ENDPOINT}?resolve={resolve_flag}")
        if not outcome.is_data:
            _LOGGER.debug("ARP endpoint unavailable")
            return None

        arp_table = outcome.mapping().get("rows")
        return arp_table if isinstance(arp_table, list) else None

    @_log_errors
    async def _get_ndp_table(self) -> list | None:
        """Return IPv6 neighbors from the OPNsense NDP table.

        Returns:
            list | None: Unmodified neighbor rows containing ``ip``, ``mac``,
                ``intf``, ``intf_description``, and ``manufacturer`` when supplied.
                IPv6 addresses may include an interface scope suffix. Multiple
                addresses for the same MAC are preserved, including SLAAC and
                privacy addresses; DHCPv6 is not required. An empty list means
                a successful lookup found no neighbors. An unavailable endpoint
                or absent or malformed rows field returns ``None``.
        """
        outcome = await self._fetch_endpoint_outcome(NDP_TABLE_ENDPOINT)
        if not outcome.is_data:
            _LOGGER.debug("NDP endpoint unavailable")
            return None

        ndp_table = outcome.mapping().get("rows")
        return ndp_table if isinstance(ndp_table, list) else None

    @_log_errors
    async def _get_complete_neighbor_rows(self, request_path: str) -> list | None:
        """Fetch every row for a normalized neighbor table and verify completeness.

        Args:
            request_path (str): Neighbor search path, including any query parameters.

        Returns:
            list | None: Complete rows, including a successful empty list, or None when the
                endpoint is unavailable or the response is missing, malformed, or truncated.
        """
        outcome = await self._fetch_post_endpoint_outcome(request_path, {"rowCount": -1})
        if not outcome.is_data:
            _LOGGER.debug("Neighbor endpoint unavailable: %s", request_path)
            return None

        payload = outcome.mapping()
        rows = payload.get("rows")
        total = payload.get("total")
        if (
            not isinstance(rows, list)
            or isinstance(total, bool)
            or not isinstance(total, int)
            or total < 0
            or total != len(rows)
        ):
            _LOGGER.debug("Neighbor endpoint returned an incomplete table: %s", request_path)
            return None
        return rows

    @deprecated("Use get_arp_entries() instead; raw rows remain available as table.raw_rows.")
    async def get_arp_table(self, resolve_hostnames: bool = False) -> list | None:
        """Return raw ARP rows through the deprecated compatibility API.

        Deprecated: use :meth:`get_arp_entries` for normalized entries and complete raw rows.

        Args:
            resolve_hostnames (bool): Whether reverse DNS lookups should be requested.

        Returns:
            list | None: Unmodified rows, or None when unavailable or malformed.
        """
        return await self._get_arp_table(resolve_hostnames=resolve_hostnames)

    @deprecated("Use get_ndp_entries() instead; raw rows remain available as table.raw_rows.")
    async def get_ndp_table(self) -> list | None:
        """Return raw NDP rows through the deprecated compatibility API.

        Deprecated: use :meth:`get_ndp_entries` for normalized entries and complete raw rows.

        Returns:
            list | None: Unmodified rows, or None when unavailable or malformed.
        """
        return await self._get_ndp_table()

    async def get_arp_entries(self, resolve_hostnames: bool = False) -> ArpNdpTable | None:
        """Return the active ARP table with normalized entries.

        Fetches all rows in one POST request and normalizes them locally. A response whose
        ``total`` does not match the returned row count is treated as unavailable.

        Args:
            resolve_hostnames (bool): Whether reverse DNS lookups should be requested.

        Returns:
            ArpNdpTable | None: Normalized rows plus the count of rows that could not be
                interpreted and isolated raw copies for diagnostics. None means the lookup
                was unavailable or malformed, as for :meth:`get_arp_table`; a successful
                empty table has no entries.
        """
        resolve_flag = "yes" if resolve_hostnames else "no"
        return self._parse_neighbor_table(
            await self._get_complete_neighbor_rows(
                f"{ARP_TABLE_ENDPOINT}?resolve={resolve_flag}",
            )
        )

    async def get_ndp_entries(self) -> ArpNdpTable | None:
        """Return the IPv6 neighbor table with normalized entries.

        Fetches all rows in one POST request and normalizes them locally. A response whose
        ``total`` does not match the returned row count is treated as unavailable.

        Returns:
            ArpNdpTable | None: Normalized rows plus the count of rows that could not be
                interpreted and isolated raw copies for diagnostics. None means the lookup
                was unavailable or malformed, as for :meth:`get_ndp_table`; a successful
                empty table has no entries.
        """
        return self._parse_neighbor_table(
            await self._get_complete_neighbor_rows(NDP_TABLE_ENDPOINT)
        )

    async def _fetch_host_observations(self) -> HostObservations | None:
        """Fetch and validate timestamped host observations from OPNsense.

        Returns:
            HostObservations | None: Validated discovery entries, an empty timestamp-free
                result when discovery is disabled, or None when the endpoint is unavailable
                or the response is incomplete or malformed. At most one POST is sent; a
                fresh cached absent status can return without sending a request.
        """
        outcome = await self._fetch_post_endpoint_outcome(
            HOST_OBSERVATIONS_ENDPOINT, {"rowCount": -1}
        )
        if not outcome.is_data:
            return None
        payload = outcome.mapping()
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            return None
        total = payload.get("total")
        if isinstance(total, bool) or not isinstance(total, int) or total != len(rows):
            return None
        if any(
            not isinstance(row, MutableMapping) or not isinstance(row.get("source"), str)
            for row in rows
        ):
            return None
        sources = {row.get("source") for row in rows}
        if sources == {"arp-ndp"}:
            return DHCPMixin.HostObservations(source="arp-ndp", entries=())
        if sources != {"discovery"}:
            return None

        observations: list[DHCPMixin.HostObservation] = []
        latest_allowed = datetime.now(UTC) + timedelta(minutes=5)
        for row in rows:
            mac = row.get("ether_address")
            address = row.get("ip_address")
            last_seen = row.get("last_seen")
            if (
                not isinstance(mac, str)
                or not isinstance(address, str)
                or not isinstance(last_seen, str)
            ):
                return None
            mac = mac.strip().lower().replace("-", ":")
            if _MAC_PATTERN.fullmatch(mac) is None:
                return None
            try:
                ip = ipaddress.ip_address(address.strip()).compressed
                observed = datetime.fromisoformat(last_seen)
            except ValueError:
                return None
            if observed.tzinfo is None or observed.utcoffset() is None:
                return None
            observed = observed.astimezone(UTC)
            if observed > latest_allowed:
                return None
            observations.append(DHCPMixin.HostObservation(mac=mac, ip=ip, last_seen=observed))
        return DHCPMixin.HostObservations(source="discovery", entries=tuple(observations))

    @deprecated("Use get_host_observation_entries() instead.")
    @_log_errors
    async def get_host_observations(self) -> dict[str, Any] | None:
        """Return timestamped MAC observations as dictionaries for compatibility.

        Deprecated: use :meth:`get_host_observation_entries` for immutable typed results.

        Returns:
            dict[str, Any] | None: The historical ``{"source": ..., "rows": ...}`` shape.
                Discovery rows contain normalized ``mac``, ``ip``, and UTC ``last_seen``
                values. A disabled discovery service returns ``{"source": "arp-ndp",
                "rows": []}``. An unavailable endpoint or malformed response returns None.
        """
        observations = await self._fetch_host_observations()
        if observations is None:
            return None
        if observations.source == "arp-ndp":
            return {"source": "arp-ndp", "rows": []}
        return {
            "source": "discovery",
            "rows": [
                {"mac": entry.mac, "ip": entry.ip, "last_seen": entry.last_seen}
                for entry in observations.entries
            ],
        }

    @_log_errors
    async def get_host_observation_entries(self) -> HostObservations | None:
        """Return validated host observations as immutable typed entries.

        Returns:
            HostObservations | None: Frozen normalized observations, including an empty
                ``arp-ndp`` result when host discovery is disabled. None means unavailable
                or malformed data. Each call sends at most one observation request; a
                fresh cached absent status can avoid the request.
        """
        return await self._fetch_host_observations()

    @_log_errors
    async def get_dhcp_leases(self, opnsense_tz: tzinfo | None = None) -> dict[str, Any] | None:
        """Return active DHCP leases grouped by interface.

        Args:
            opnsense_tz (tzinfo | None, optional): Timezone used to localize
                ISC lease expiration timestamps. Fetched from OPNsense when
                omitted.

        Returns:
            dict[str, Any] | None: Mapping with ``lease_interfaces`` keyed by
                interface name and ``leases`` keyed by interface name. Lease
                entries are normalized across Kea, ISC, and dnsmasq and
                include address, hostname, interface, type, MAC,
                and expiration when available. Hostnames made only of dots
                are reported as None, like empty or missing hostnames.
                ``lease_interfaces`` also lists interfaces that Kea DHCPv4 or
                Dnsmasq is configured to serve, including those without leases.
                The result reports its access level through ``result_access()``.
                A forbidden (403) lease, Kea DHCPv4 settings or Dnsmasq range
                endpoint, including a cached denial, reports Status Access: that
                endpoint contributes nothing, so leases and interfaces it would
                have supplied may be missing. Otherwise the result reports
                Configuration Access. A missing (404) endpoint means its backend
                is not installed and contributes nothing. With the default
                ``throw_errors=False``, a Kea reservation endpoint that cannot be
                read only drops reservation detail, and the method returns ``None``
                (unknown) when any lease endpoint fails with another error or
                returns a malformed payload, and when a configuration endpoint
                does so without a denial elsewhere establishing Status Access. When
                ``self._throw_errors`` is ``True``, a failed request raises
                ``OPNsenseError`` instead, specifically ``OPNsensePrivilegeMissing``
                for a forbidden (403) endpoint; a 404 never raises, and a
                malformed payload still returns ``None``.
        """
        if opnsense_tz is None:
            opnsense_tz = await self._get_opnsense_timezone()
        lease_reads = [
            await self._get_kea_dhcpv4_leases(opnsense_tz=opnsense_tz),
            await self._get_kea_dhcpv6_leases(opnsense_tz=opnsense_tz),
            await self._get_isc_dhcpv4_leases(opnsense_tz=opnsense_tz),
            await self._get_isc_dhcpv6_leases(opnsense_tz=opnsense_tz),
            await self._get_dnsmasq_leases(opnsense_tz=opnsense_tz),
        ]

        leases: dict[str, Any] = {}
        # ISC lease endpoints expose interface metadata only for interfaces with lease rows,
        # and OPNsense has no ISC configuration endpoint that lists enabled DHCP interfaces.
        # Therefore, zero-lease ISC scopes cannot be discovered without incorrectly treating
        # every assigned interface as DHCP-capable.
        interface_reads = [await self._get_kea_interfaces(), await self._get_dnsmasq_interfaces()]

        lease_sources = [source for source in lease_reads if source is not None]
        if len(lease_sources) != len(lease_reads):
            _LOGGER.debug("[get_dhcp_leases] leases unknown (failed or malformed lease read)")
            return None
        interface_sources = [source for source in interface_reads if source is not None]
        denied = any(source.access is Access.STATUS for source in lease_sources) or any(
            source.access is Access.STATUS for source in interface_sources
        )
        if not denied and len(interface_sources) != len(interface_reads):
            _LOGGER.debug(
                "[get_dhcp_leases] interfaces unknown (failed or malformed configuration read)"
            )
            return None
        access = Access.STATUS if denied else Access.CONFIGURATION
        lease_interfaces: dict[str, Any] = {}
        for interface_source in interface_sources:
            lease_interfaces.update(interface_source)
        leases_raw = [lease for source in lease_sources for lease in source]
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

        dhcp_leases = AccessDict(
            {"lease_interfaces": sorted_lease_interfaces, "leases": sorted_leases},
            access=access,
        )
        _LOGGER.debug(
            "[get_dhcp_leases] interfaces: %s, access: %s", len(sorted_lease_interfaces), access
        )
        return dhcp_leases

    @staticmethod
    def _unread_dhcp_source_access(outcome: _EndpointOutcome) -> Access | None:
        """Return the access level a DHCP endpoint without data implies.

        Args:
            outcome (_EndpointOutcome): Outcome of a request that returned no data.

        Returns:
            Access | None: Status Access after a forbidden (403) read, including a
                cached denial, Configuration Access for a missing (404) endpoint, and
                ``None`` when a transient failure leaves the source unknown.
        """
        if outcome.is_transient:
            return None
        return Access.STATUS if outcome.http_status == 403 else Access.CONFIGURATION

    @classmethod
    def _unread_interfaces(cls, outcome: _EndpointOutcome) -> AccessDict[str, Any] | None:
        """Return the interface inventory of a configuration endpoint without data.

        Args:
            outcome (_EndpointOutcome): Outcome of a request that returned no data.

        Returns:
            AccessDict[str, Any] | None: Empty inventory carrying the access level from
                ``_unread_dhcp_source_access``, or ``None`` when the inventory is unknown.
        """
        access = cls._unread_dhcp_source_access(outcome)
        return None if access is None else AccessDict({}, access=access)

    @classmethod
    def _unread_leases(cls, outcome: _EndpointOutcome) -> AccessList[Any] | None:
        """Return the leases of a lease endpoint without data.

        Args:
            outcome (_EndpointOutcome): Outcome of a request that returned no data.

        Returns:
            AccessList[Any] | None: No leases, carrying the access level from
                ``_unread_dhcp_source_access``, or ``None`` when the leases are unknown.
        """
        access = cls._unread_dhcp_source_access(outcome)
        return None if access is None else AccessList([], access=access)

    async def _get_kea_interfaces(self) -> AccessDict[str, Any] | None:
        """Return interfaces selected for Kea DHCPv4.

        Returns:
            AccessDict[str, Any] | None: Mapping of Kea interface identifiers to
                display names when Kea DHCPv4 is enabled; otherwise an empty mapping.
                A forbidden (403) settings endpoint gives an empty mapping with Status
                Access; every other mapping has Configuration Access. ``None`` when the
                settings read fails or its payload is malformed.
        """
        outcome = await self._fetch_endpoint_outcome(KEA_DHCPV4_GET_ENDPOINT)
        if not outcome.is_data:
            _LOGGER.debug("Kea DHCP interface endpoint unavailable")
            return self._unread_interfaces(outcome)

        response = outcome.mapping()
        lease_interfaces: dict[str, Any] = {}
        general = dict_get(response, "dhcpv4.general")
        if not isinstance(general, MutableMapping):
            _LOGGER.debug("Kea DHCP interface lookup returned invalid settings payload")
            return None
        if not api_value_matches(general.get("enabled", "0"), "1"):
            return AccessDict({}, access=Access.CONFIGURATION)
        interfaces = general.get("interfaces")
        if not isinstance(interfaces, MutableMapping):
            _LOGGER.debug("Kea DHCP interface lookup returned invalid interfaces payload")
            return None
        for if_name, iface in interfaces.items():
            if not isinstance(iface, MutableMapping):
                continue
            if api_value_matches(iface.get("selected", 0), "1") and iface.get("value", None):
                lease_interfaces[if_name] = iface.get("value")
        return AccessDict(lease_interfaces, access=Access.CONFIGURATION)

    async def _get_dnsmasq_interfaces(self) -> AccessDict[str, Any] | None:
        """Return interfaces with configured Dnsmasq DHCP ranges.

        Returns:
            AccessDict[str, Any] | None: Mapping of Dnsmasq range interface identifiers
                to display names, including interfaces without active leases. A
                forbidden (403) range endpoint gives an empty mapping with Status
                Access; every other mapping has Configuration Access. ``None`` when
                the range read fails or its rows are missing or malformed.
        """
        outcome = await self._fetch_endpoint_outcome(DNSMASQ_RANGES_SEARCH_ENDPOINT)
        if not outcome.is_data:
            _LOGGER.debug("Dnsmasq DHCP range endpoint unavailable")
            return self._unread_interfaces(outcome)

        response = outcome.mapping()
        ranges = response.get("rows")
        if not isinstance(ranges, list):
            _LOGGER.debug("Dnsmasq DHCP range lookup returned invalid rows payload")
            return None

        lease_interfaces: dict[str, Any] = {}
        for range_info in ranges:
            if not isinstance(range_info, MutableMapping):
                continue
            for interface_field in ("interface", "constructor"):
                if_name = range_info.get(interface_field)
                if not isinstance(if_name, str) or not if_name:
                    continue
                if_descr = range_info.get(f"%{interface_field}")
                if if_name not in lease_interfaces or (
                    lease_interfaces[if_name] == if_name and isinstance(if_descr, str) and if_descr
                ):
                    lease_interfaces[if_name] = (
                        if_descr if isinstance(if_descr, str) and if_descr else if_name
                    )
        return AccessDict(lease_interfaces, access=Access.CONFIGURATION)

    async def _get_kea_dhcpv4_leases(
        self, opnsense_tz: tzinfo | None = None
    ) -> AccessList[Any] | None:
        """Return active IPv4 DHCP leases reported by Kea.

        Args:
            opnsense_tz (tzinfo | None, optional): Unused timezone parameter
                accepted for parity with other lease providers.

        Returns:
            AccessList[Any] | None: Normalized Kea IPv4 lease entries. Expired leases,
                non-active rows, malformed rows, and rows without hardware
                addresses are omitted; lease ``type`` is ``static``,
                ``dynamic``, or ``unknown`` depending on reservation data.
                See ``_unread_leases`` for an endpoint without data; ``None``
                when the leases are unknown.
        """
        return await self._get_kea_dhcp_leases(
            lease_endpoint=KEA_LEASES4_SEARCH_ENDPOINT,
            reservation_endpoint=KEA_DHCPV4_SEARCH_RESERVATION_ENDPOINT,
            require_hardware_address=True,
            service_name="Kea DHCPv4",
        )

    async def _get_kea_dhcpv6_leases(
        self, opnsense_tz: tzinfo | None = None
    ) -> AccessList[Any] | None:
        """Return active IPv6 DHCP leases reported by Kea.

        Args:
            opnsense_tz (tzinfo | None, optional): Unused timezone parameter
                accepted for parity with other lease providers.

        Returns:
            AccessList[Any] | None: Normalized Kea IPv6 lease entries. Expired leases,
                non-active rows, and malformed rows are omitted; lease
                ``type`` is ``static``, ``dynamic``, or ``unknown`` depending
                on reservation data.
                See ``_unread_leases`` for an endpoint without data; ``None``
                when the leases are unknown.
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
        reservation_endpoint: _StyledEndpoint | None = None,
        require_hardware_address: bool = True,
    ) -> AccessList[Any] | None:
        """Return active DHCP leases reported by a Kea lease endpoint.

        Args:
            lease_endpoint (str): Kea lease search endpoint path.
            service_name (str): Display name used for debug logging.
            reservation_endpoint (_StyledEndpoint | None, optional): Kea reservation endpoint.
            require_hardware_address (bool, optional): Whether to skip rows without ``hwaddr``.

        Returns:
            AccessList[Any] | None: Normalized Kea lease entries for the supplied
                endpoint with Configuration Access. See ``_unread_leases`` for an
                endpoint without data; ``None`` when the rows are missing or malformed.
        """
        outcome = await self._fetch_endpoint_outcome(lease_endpoint)
        if not outcome.is_data:
            _LOGGER.debug("%s lease endpoint unavailable", service_name)
            return self._unread_leases(outcome)
        response = outcome.mapping()
        if not isinstance(response.get("rows", None), list):
            _LOGGER.debug("%s lease lookup returned invalid rows payload", service_name)
            return None
        leases_info: list = response.get("rows", [])
        current_time = datetime.now().astimezone()
        res_info: list[Any] | None
        lease_backend = _kea_lease_backend(
            require_hardware_address=require_hardware_address, reservations=None
        )
        needs_reservation_lookup = any(
            _is_live_row(lease_info, lease_backend, now=current_time)
            and not _has_authoritative_reservation_metadata(lease_info.get("is_reserved"))
            for lease_info in leases_info
        )
        if reservation_endpoint is None or not needs_reservation_lookup:
            res_info = None
        else:
            res_outcome = await self._fetch_endpoint_outcome(reservation_endpoint)
            if not res_outcome.is_data:
                _LOGGER.debug("%s reservation endpoint unavailable", service_name)
                res_info = None
            else:
                res_rows = res_outcome.mapping().get("rows", None)
                if not isinstance(res_rows, list):
                    _LOGGER.debug(
                        "%s reservation lookup returned invalid rows payload", service_name
                    )
                    res_info = None
                else:
                    res_info = res_rows
        reservations: dict[str, object] = {}
        if res_info is not None:
            for res in res_info:
                if not isinstance(res, MutableMapping):
                    continue
                if hw_address := res.get("hw_address", None):
                    reservations[hw_address] = res.get("ip_address", "")
        return AccessList(
            _normalize_leases(
                leases_info,
                _kea_lease_backend(
                    require_hardware_address=require_hardware_address,
                    reservations=None if res_info is None else reservations,
                ),
                now=current_time,
            ),
            access=Access.CONFIGURATION,
        )

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

    async def _get_dnsmasq_leases(
        self, opnsense_tz: tzinfo | None = None
    ) -> AccessList[Any] | None:
        """Return active IPv4 and IPv6 DHCP leases reported by dnsmasq.

        Args:
            opnsense_tz (tzinfo | None, optional): Unused timezone parameter
                accepted for parity with other lease providers.

        Returns:
            AccessList[Any] | None: Normalized dnsmasq lease entries. Duplicate rows are reduced
                to the latest expiration, expired rows are omitted, and lease
                ``type`` is derived from dnsmasq reservation metadata.
                See ``_unread_leases`` for an endpoint without data; ``None``
                when the leases are unknown.
        """
        outcome = await self._fetch_endpoint_outcome(DNSMASQ_LEASES_SEARCH_ENDPOINT)
        if not outcome.is_data:
            _LOGGER.debug("Dnsmasq DHCP lease endpoint unavailable")
            return self._unread_leases(outcome)
        leases_info = outcome.mapping().get("rows")
        if not isinstance(leases_info, list):
            _LOGGER.debug("Dnsmasq DHCP lease lookup returned invalid rows payload")
            return None
        return AccessList(
            _normalize_leases(
                self._keep_latest_leases(leases_info),
                _DNSMASQ_LEASE_BACKEND,
                now=datetime.now().astimezone(),
            ),
            access=Access.CONFIGURATION,
        )

    async def _get_isc_dhcpv4_leases(self, opnsense_tz: tzinfo) -> AccessList[Any] | None:
        """Return active IPv4 DHCP leases reported by ISC DHCP.

        Args:
            opnsense_tz (tzinfo): Timezone used to localize ISC ``ends`` timestamps.

        Returns:
            AccessList[Any] | None: Normalized ISC DHCPv4 lease entries. Non-active, expired,
                malformed, and MAC-less rows are omitted.
                See ``_unread_leases`` for an endpoint without data; ``None``
                when the leases are unknown.
        """
        return await self._get_isc_dhcp_leases(
            lease_endpoint=ISC_DHCPV4_LEASES_SEARCH_ENDPOINT,
            service_name="ISC DHCPv4",
            opnsense_tz=opnsense_tz,
        )

    async def _get_isc_dhcpv6_leases(self, opnsense_tz: tzinfo) -> AccessList[Any] | None:
        """Return active IPv6 DHCP leases reported by ISC DHCP.

        Args:
            opnsense_tz (tzinfo): Timezone used to localize ISC ``ends`` timestamps.

        Returns:
            AccessList[Any] | None: Normalized ISC DHCPv6 lease entries. Non-active, expired,
                malformed, and MAC-less rows are omitted.
                See ``_unread_leases`` for an endpoint without data; ``None``
                when the leases are unknown.
        """
        return await self._get_isc_dhcp_leases(
            lease_endpoint=ISC_DHCPV6_LEASES_SEARCH_ENDPOINT,
            service_name="ISC DHCPv6",
            opnsense_tz=opnsense_tz,
        )

    async def _get_isc_dhcp_leases(
        self,
        lease_endpoint: _StyledEndpoint,
        service_name: str,
        opnsense_tz: tzinfo,
    ) -> AccessList[Any] | None:
        """Return active DHCP leases reported by an ISC DHCP lease endpoint.

        Args:
            lease_endpoint (_StyledEndpoint): ISC lease search endpoint.
            service_name (str): Display name used for debug logging.
            opnsense_tz (tzinfo): Timezone used to localize ISC ``ends`` timestamps.

        Returns:
            AccessList[Any] | None: Normalized ISC lease entries for the supplied
                endpoint with Configuration Access. See ``_unread_leases`` for an
                endpoint without data; ``None`` when the rows are missing or malformed.
        """
        outcome = await self._fetch_endpoint_outcome(lease_endpoint)
        if not outcome.is_data:
            _LOGGER.debug("%s lease endpoint unavailable", service_name)
            return self._unread_leases(outcome)
        leases_info = outcome.mapping().get("rows")
        if not isinstance(leases_info, list):
            _LOGGER.debug("%s lease lookup returned invalid rows payload", service_name)
            return None
        return AccessList(
            _normalize_leases(
                leases_info,
                _ISC_LEASE_BACKEND,
                now=datetime.now().astimezone(),
                router_tz=opnsense_tz,
            ),
            access=Access.CONFIGURATION,
        )
