"""Tests for `aiopnsense.dhcp`."""

from collections.abc import Callable, MutableMapping
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from aiopnsense import CategoryResult, OPNsenseClient
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


def optional_get_from_safe_dict(client: OPNsenseClient) -> AsyncMock:
    """Adapt legacy payload mocks to the status-aware optional GET contract."""

    async def optional_get(path: str, **_kwargs: object) -> CategoryResult[object]:
        """Return the mocked safe-dict payload as an available result."""
        return CategoryResult(await client._safe_dict_get(path), "available", True)

    return AsyncMock(side_effect=optional_get)


@pytest.mark.asyncio
async def test_dhcp_leases_and_keep_latest_and_dnsmasq(make_client: ClientType) -> None:
    """Cover Kea and dnsmasq lease parsing and lease de-duplication behavior.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates normalized lease parsing behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._check_optional_get_endpoint = optional_get_from_safe_dict(client)
        # _get_kea_interfaces returns mapping and kea leases: one valid
        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "dhcpv4": {
                        "general": {
                            "enabled": "1",
                            "interfaces": {"em0": {"selected": 1, "value": "desc"}},
                        }
                    }
                },
                {
                    "rows": [
                        {
                            "if_name": "em0",
                            "if_descr": "d",
                            "state": "0",
                            "hwaddr": "mac1",
                            "address": "1.2.3.4",
                            "hostname": "host.",
                        }
                    ]
                },
                {"rows": []},
                {},
            ]
        )
        # monkeypatch internal helpers by calling _get_kea_dhcpv4_leases directly
        leases = await client._get_kea_dhcpv4_leases()
        assert isinstance(leases, list)

        # test _keep_latest_leases via instance
        res = client._keep_latest_leases(
            [{"a": 1, "expire": 10}, {"a": 1, "expire": 20}, {"a": 2, "expire": 5}]
        )
        # should keep a single latest lease for duplicate keys
        filtered = [item for item in res if item.get("a") == 1]
        assert len(filtered) == 1
        assert filtered[0]["expire"] == 20

        # dnsmasq leases behavior
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {
                        "address": "1.2.3.4",
                        "hostname": "*",
                        "if_descr": "d",
                        "if": "em0",
                        "is_reserved": "1",
                        "hwaddr": "mac1",
                        "expire": 9999999999,
                    }
                ]
            }
        )
        dns = await client._get_dnsmasq_leases()
        assert isinstance(dns, list)
        assert len(dns) > 0
        assert dns[0]["address"] == "1.2.3.4"
        assert dns[0]["mac"] == "mac1"
        assert dns[0]["type"] == "static"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_kea_leases_accepts_integer_active_state(make_client: ClientType) -> None:
    """Kea active leases should parse when OPNsense returns integer state 0.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates mixed integer/string Kea lease state handling.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._check_optional_get_endpoint = optional_get_from_safe_dict(client)
        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "rows": [
                        {
                            "address": "192.0.2.10",
                            "hwaddr": "aa:bb:cc:dd:ee:ff",
                            "state": 0,
                            "if_name": "em0",
                            "if_descr": "LAN",
                            "hostname": "host.",
                        },
                        {
                            "address": "192.0.2.11",
                            "hwaddr": "aa:bb:cc:dd:ee:00",
                            "state": 1,
                            "if_name": "em0",
                        },
                        {
                            "address": "192.0.2.12",
                            "hwaddr": "aa:bb:cc:dd:ee:11",
                            "if_name": "em0",
                        },
                    ]
                },
                {"rows": []},
            ]
        )

        leases = await client._get_kea_dhcpv4_leases()

        assert leases == [
            {
                "address": "192.0.2.10",
                "hostname": "host",
                "if_descr": "LAN",
                "if_name": "em0",
                "type": "dynamic",
                "mac": "aa:bb:cc:dd:ee:ff",
                "expires": None,
            }
        ]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_arp_table_uses_get_query_param(make_client: ClientType) -> None:
    """Verify ARP table lookup uses GET endpoint with the expected query parameter.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates resolve query parameter forwarding.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=CategoryResult({"rows": []}, "available", True)
        )

        await client.get_arp_table(resolve_hostnames=True)
        client._check_optional_get_endpoint.assert_awaited_with(
            "/api/diagnostics/interface/search_arp?resolve=yes",
            cache_path="/api/diagnostics/interface/search_arp",
        )

        await client.get_arp_table(resolve_hostnames=False)
        assert client._check_optional_get_endpoint.await_args_list[1].args[0] == (
            "/api/diagnostics/interface/search_arp?resolve=no"
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "endpoint"),
    [
        ("_get_kea_dhcpv4_leases", "/api/kea/leases4/search"),
        ("_get_kea_dhcpv6_leases", "/api/kea/leases6/search"),
        ("_get_dnsmasq_leases", "/api/dnsmasq/leases/search"),
    ],
)
async def test_dhcp_endpoint_unavailable(
    make_client: ClientType,
    method_name: str,
    endpoint: str,
) -> None:
    """Verify DHCP lease helpers return empty results when endpoints are unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        method_name (str): Lease helper method name to invoke.
        endpoint (str): Endpoint expected for availability probing.

    Returns:
        None: This test validates endpoint gating behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=CategoryResult({}, "missing", False)
        )
        client._safe_dict_get = AsyncMock()
        leases = await getattr(client, method_name)()
        assert leases == []
        client._check_optional_get_endpoint.assert_awaited_once_with(endpoint)
        client._safe_dict_get.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "endpoint"),
    [
        ("_get_isc_dhcpv4_leases", "/api/dhcpv4/leases/search_lease"),
        ("_get_isc_dhcpv6_leases", "/api/dhcpv6/leases/search_lease"),
    ],
)
@pytest.mark.parametrize("status", ["missing", "unavailable", "malformed"])
async def test_isc_dhcp_optional_endpoint_failure(
    make_client: ClientType,
    method_name: str,
    endpoint: str,
    status: str,
) -> None:
    """ISC DHCP lease helpers fail closed for non-available plugin states.

    Args:
        make_client: Fixture factory returning ``OPNsenseClient`` instances.
        method_name: ISC lease helper method name to invoke.
        endpoint: Selected optional lease endpoint.
        status: Optional endpoint state returned by the reconciliation helper.
    """
    client, _session = make_mock_session_client(make_client)
    client._use_snake_case = True
    client._check_optional_get_endpoint = AsyncMock(return_value=(status, {}))
    try:
        assert await getattr(client, method_name)() == []
        client._check_optional_get_endpoint.assert_awaited_once_with(endpoint)
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reservations", "expected_key", "expected_expire"),
    [
        ([{"a": 1, "expire": 10}, {"a": 1, "expire": 20}, {"a": 2, "expire": 5}], "a", 20),
        ([{"b": 1, "expire": 10}, {"b": 1, "expire": 20}, {"c": 2, "expire": 5}], "b", 20),
    ],
)
async def test_keep_latest_leases_prefers_latest_expiration(
    make_client: ClientType,
    reservations: list[dict[str, int]],
    expected_key: str,
    expected_expire: int,
) -> None:
    """Verify latest lease entry is selected per deduplication key.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        reservations (list[dict[str, int]]): Lease rows containing duplicate keys and expirations.
        expected_key (str): Key used to identify the duplicate reservation group.
        expected_expire (int): Expected latest expiration for the duplicate group.

    Returns:
        None: This test validates lease deduplication selection.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        result = client._keep_latest_leases(reservations)
        matching = [item for item in result if item.get(expected_key) == 1]
        assert len(matching) == 1
        assert matching[0]["expire"] == expected_expire
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_keep_latest_leases_handles_list_values(make_client: ClientType) -> None:
    """Verify lease deduplication supports list-valued fields without key errors.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates hash-safe deduplication key construction.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        reservations: list[dict[str, object]] = [
            {"address": "10.0.0.2", "hostnames": ["foo", "bar"], "expire": 10},
            {"address": "10.0.0.2", "hostnames": ["foo", "bar"], "expire": 20},
            {"address": "10.0.0.3", "hostnames": ["baz"], "expire": 5},
        ]

        result = client._keep_latest_leases(reservations)
        matching = [item for item in result if item.get("address") == "10.0.0.2"]
        assert len(matching) == 1
        assert matching[0]["expire"] == 20
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_reserved", "expected"),
    [
        ("1", True),
        ("0", False),
        (["hwaddr"], True),
        ([], False),
        (1, True),
        (0, False),
        (None, False),
    ],
)
async def test_is_reserved_lease_handles_legacy_and_list_flags(
    make_client: ClientType,
    raw_reserved: object,
    expected: bool,  # noqa: FBT001 - pytest injects parametrized values by name.
) -> None:
    """Verify reserved lease detection supports legacy and new value shapes.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        raw_reserved (object): Raw ``is_reserved`` value from API payload.
        expected (bool): Expected reserved/static detection result.

    Returns:
        None: This test validates reserved lease coercion behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        assert client._is_reserved_lease(raw_reserved) is expected
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_normalize_lease_key_value_handles_nested_structures(make_client: ClientType) -> None:
    """Verify lease key normalization converts nested structures into hashable tuples.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates stable normalization for dict/list/set values.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        normalized = client._normalize_lease_key_value(
            {
                "z": [{"n": 2}, {"n": 1}],
                "a": {"set": {"x", "y"}, "list": [3, 4]},
            }
        )

        assert isinstance(normalized, tuple)
        as_dict = dict(normalized)
        assert as_dict["z"] == ((("n", 2),), (("n", 1),))
        a_dict = dict(as_dict["a"])
        assert a_dict["list"] == (3, 4)
        assert a_dict["set"] == ("x", "y")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_copy_lease_identity_fields_sets_reserved_by_only_for_non_empty_list(
    make_client: ClientType,
) -> None:
    """Verify ``reserved_by`` is copied only for non-empty reservation lists.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates lease identity metadata normalization.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        lease: dict[str, object] = {}

        client._copy_lease_identity_fields(lease, {"is_reserved": []})
        assert "reserved_by" not in lease

        client._copy_lease_identity_fields(lease, {"is_reserved": ["host1"]})
        assert lease["reserved_by"] == ["host1"]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_isc_dhcpv4_and_v6_parsing(make_client: ClientType) -> None:
    """Verify ISC DHCPv4/v6 parsing converts and filters lease fields correctly.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates timestamp conversion and lease normalization.
    """
    client, _ = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        local_tz = datetime.now().astimezone().tzinfo
        assert local_tz is not None
        client._get_opnsense_timezone = AsyncMock(return_value=local_tz)

        # v4: ends present and in future
        future_dt = (datetime.now(tz=local_tz) + timedelta(hours=1)).strftime("%Y/%m/%d %H:%M:%S")
        client._check_optional_get_endpoint = AsyncMock(
            return_value=(
                "available",
                {
                    "rows": [
                        {
                            "state": "active",
                            "mac": "m1",
                            "address": "10.0.0.1",
                            "hostname": "h1",
                            "if": "em0",
                            "ends": future_dt,
                        }
                    ]
                },
            )
        )
        v4 = await client._get_isc_dhcpv4_leases()
        assert isinstance(v4, list) and len(v4) == 1
        assert v4[0]["address"] == "10.0.0.1"
        assert v4[0]["mac"] == "m1"
        assert v4[0]["hostname"] == "h1"
        assert isinstance(v4[0].get("expires"), datetime)
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/dhcpv4/leases/search_lease"
        )

        # v6: ends missing -> field passed through
        client._check_optional_get_endpoint = AsyncMock(
            return_value=(
                "available",
                {
                    "rows": [
                        {
                            "state": "active",
                            "mac": "m2",
                            "address": "fe80::1",
                            "hostname": "h2",
                            "if": "em1",
                        }
                    ]
                },
            )
        )
        v6 = await client._get_isc_dhcpv6_leases()
        assert isinstance(v6, list) and len(v6) == 1
        assert v6[0]["address"] == "fe80::1"
        assert v6[0]["mac"] == "m2"
        assert v6[0]["hostname"] == "h2"
        assert "ends" not in v6[0]
        assert v6[0].get("expires") is None
        assert "ends_at" not in v6[0] or v6[0]["ends_at"] is None
        assert "expiry" not in v6[0] or v6[0]["expiry"] is None
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/dhcpv6/leases/search_lease"
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_dhcp_leases_combined_structure(make_client: ClientType) -> None:
    """Verify combined DHCP leases payload includes all expected source data.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates aggregate DHCP lease structure.
    """
    client, _ = make_mock_session_client(make_client)
    try:
        local_tz = datetime.now().astimezone().tzinfo
        assert local_tz is not None
        client._get_opnsense_timezone = AsyncMock(return_value=local_tz)

        # return one lease from each source and one interface mapping
        client._get_kea_dhcpv4_leases = AsyncMock(
            return_value=[{"if_name": "em0", "address": "1.1.1.1", "mac": "m1"}]
        )
        client._get_isc_dhcpv4_leases = AsyncMock(
            return_value=[{"if_name": "em0", "address": "1.1.1.2", "mac": "m2"}]
        )
        client._get_kea_dhcpv6_leases = AsyncMock(
            return_value=[{"if_name": "em0", "address": "2001:db8::1", "mac": "m6"}]
        )
        client._get_isc_dhcpv6_leases = AsyncMock(return_value=[])
        client._get_dnsmasq_leases = AsyncMock(return_value=[])
        client._get_kea_interfaces = AsyncMock(return_value={"em0": "eth0"})

        combined = await client.get_dhcp_leases()
        assert isinstance(combined, MutableMapping)
        assert "em0" in combined["lease_interfaces"]
        assert combined["lease_interfaces"]["em0"] is None
        assert isinstance(combined["leases"], MutableMapping)
        assert "em0" in combined["leases"]
        assert len(combined["leases"]["em0"]) > 0
        assert any(
            lease.get("address") == "1.1.1.1" and lease.get("mac") == "m1"
            for lease in combined["leases"]["em0"]
        )
        assert any(
            lease.get("address") == "1.1.1.2" and lease.get("mac") == "m2"
            for lease in combined["leases"]["em0"]
        )
        assert any(
            lease.get("address") == "2001:db8::1" and lease.get("mac") == "m6"
            for lease in combined["leases"]["em0"]
        )
        client._get_opnsense_timezone.assert_awaited_once_with()
        client._get_kea_dhcpv4_leases.assert_awaited_once_with(opnsense_tz=local_tz)
        client._get_kea_dhcpv6_leases.assert_awaited_once_with(opnsense_tz=local_tz)
        client._get_isc_dhcpv4_leases.assert_awaited_once_with(opnsense_tz=local_tz)
        client._get_isc_dhcpv6_leases.assert_awaited_once_with(opnsense_tz=local_tz)
        client._get_dnsmasq_leases.assert_awaited_once_with(opnsense_tz=local_tz)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_kea_interfaces_filters_enabled_and_selected(make_client: ClientType) -> None:
    """Verify Kea interface discovery honors enabled and selected/value constraints.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates Kea interface filtering behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = optional_get_from_safe_dict(client)
        client._safe_dict_get = AsyncMock(return_value={"dhcpv4": {"general": {"enabled": "0"}}})
        assert await client._get_kea_interfaces() == {}

        client._safe_dict_get = AsyncMock(
            return_value={
                "dhcpv4": {
                    "general": {
                        "enabled": "1",
                        "interfaces": {
                            "em0": {"selected": 1, "value": "LAN"},
                            "em1": {"selected": 0, "value": "WAN"},
                            "em2": {"selected": 1, "value": ""},
                            "em3": "invalid",
                            "em4": {"selected": "1", "value": "OPT"},
                        },
                    }
                }
            }
        )

        assert await client._get_kea_interfaces() == {"em0": "LAN", "em4": "OPT"}
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_kea_dhcpv4_leases_covers_invalid_dynamic_and_reservations(
    make_client: ClientType,
) -> None:
    """Verify Kea DHCPv4 leases skip invalid rows and classify static/dynamic entries.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates Kea lease filtering and reservation handling.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._check_optional_get_endpoint = optional_get_from_safe_dict(client)
        future_ts = int(datetime.now(tz=UTC).timestamp()) + 3600
        past_ts = int(datetime.now(tz=UTC).timestamp()) - 3600

        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "rows": [
                        None,
                        {"state": "1", "hwaddr": "skip"},
                        {"state": "0", "address": "192.0.2.10"},
                        {
                            "state": "0",
                            "address": "192.0.2.11",
                            "hostname": "dyn.",
                            "if_name": "em0",
                            "if_descr": "LAN",
                            "hwaddr": "bb:cc",
                            "expire": "never",
                        },
                        {
                            "state": "0",
                            "address": "192.0.2.12",
                            "hostname": "expired",
                            "if_name": "em0",
                            "hwaddr": "cc:dd",
                            "expire": past_ts,
                        },
                        {
                            "state": "0",
                            "address": "192.0.2.13",
                            "hostname": "stat.",
                            "if_name": "em1",
                            "if_descr": "OPT",
                            "hwaddr": "aa:bb",
                            "client_id": "01:aa:bb",
                            "expire": future_ts,
                            "is_reserved": ["client_id"],
                        },
                    ]
                },
                {
                    "rows": [
                        None,
                        {"ip_address": "192.0.2.13"},
                        {"hw_address": "aa:bb", "ip_address": "192.0.2.13"},
                    ]
                },
            ]
        )

        leases = await client._get_kea_dhcpv4_leases()
        assert len(leases) == 2
        assert any(
            lease["address"] == "192.0.2.11" and lease["type"] == "dynamic" for lease in leases
        )
        assert any(
            lease["address"] == "192.0.2.13"
            and lease["type"] == "static"
            and lease["client_id"] == "01:aa:bb"
            and lease["reserved_by"] == ["client_id"]
            for lease in leases
        )

        # Reservation lookup failures should not misclassify entries as dynamic/static.
        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "rows": [
                        {
                            "state": "0",
                            "hwaddr": "aa",
                            "address": "10.0.0.1",
                            "if_name": "em0",
                        }
                    ]
                },
                {"rows": "bad"},
            ]
        )
        leases = await client._get_kea_dhcpv4_leases()
        assert isinstance(leases, list)
        assert len(leases) == 1
        assert leases[0]["type"] == "unknown"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_kea_dhcpv6_leases_accepts_duid_only_rows(make_client: ClientType) -> None:
    """Verify Kea DHCPv6 leases parse the OPNsense DUID-oriented row shape.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates Kea DHCPv6 lease normalization.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = optional_get_from_safe_dict(client)
        future_ts = int(datetime.now(tz=UTC).timestamp()) + 3600
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {
                        "address": "2001:db8::10",
                        "duid": "00:01:00:01:aa:bb",
                        "client_id": "00:01:00:01:aa:bb",
                        "hwaddr": "",
                        "state": 0,
                        "if_name": "em0",
                        "if_descr": "LAN",
                        "hostname": "host6.",
                        "expire": future_ts,
                        "is_reserved": ["duid"],
                    },
                    {
                        "address": "2001:db8::11",
                        "duid": "00:01:00:01:cc:dd",
                        "state": 0,
                        "if_name": "em0",
                        "if_descr": "LAN",
                        "hostname": "skip.",
                        "is_reserved": [],
                        "expire": future_ts,
                    },
                    {
                        "address": "2001:db8::12",
                        "duid": "00:01:00:01:ee:ff",
                        "state": 0,
                        "if_name": "em0",
                        "is_reserved": [],
                    },
                ]
            }
        )

        leases = await client._get_kea_dhcpv6_leases()

        assert len(leases) == 3
        assert any(
            lease["address"] == "2001:db8::10"
            and lease["type"] == "static"
            and lease["hostname"] == "host6"
            and lease["duid"] == "00:01:00:01:aa:bb"
            and lease["client_id"] == "00:01:00:01:aa:bb"
            and lease["reserved_by"] == ["duid"]
            and lease["mac"] is None
            for lease in leases
        )
        assert any(
            lease["address"] == "2001:db8::11"
            and lease["type"] == "dynamic"
            and lease["duid"] == "00:01:00:01:cc:dd"
            and lease["hostname"] == "skip"
            and lease["mac"] is None
            for lease in leases
        )
        assert any(
            lease["address"] == "2001:db8::12"
            and lease["type"] == "dynamic"
            and lease["duid"] == "00:01:00:01:ee:ff"
            and lease["mac"] is None
            for lease in leases
        )
        client._safe_dict_get.assert_awaited_once_with("/api/kea/leases6/search")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_kea_dhcpv6_leases_omits_invalid_required_fields(
    make_client: ClientType,
) -> None:
    """Invalid Kea address and interface fields are omitted and mark partial data malformed."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=CategoryResult(
                {
                    "rows": [
                        {"state": 0, "address": None, "if_name": "em0"},
                        {"state": 0, "address": "2001:db8::2", "if_name": None},
                        {"state": 0, "address": "2001:db8::3", "if_name": "em0"},
                    ]
                },
                "available",
                True,
            )
        )

        result = await client._get_kea_dhcpv6_leases_result()

        assert result == CategoryResult(
            [
                {
                    "address": "2001:db8::3",
                    "hostname": None,
                    "if_descr": None,
                    "if_name": "em0",
                    "type": "unknown",
                    "mac": None,
                    "expires": None,
                }
            ],
            "malformed",
            False,
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_dnsmasq_leases_invalid_rows_and_expiry_paths(make_client: ClientType) -> None:
    """Verify dnsmasq lease parsing handles malformed and expired rows safely.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates dnsmasq lease expiry and type parsing.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = optional_get_from_safe_dict(client)
        client._safe_dict_get = AsyncMock(return_value={"rows": "bad-shape"})
        assert await client._get_dnsmasq_leases() == []

        past_ts = int(datetime.now(tz=UTC).timestamp()) - 3600
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    None,
                    {
                        "address": "192.0.2.20",
                        "hostname": "fallback-if-name",
                        "if": "em0",
                        "if_name": "",
                        "is_reserved": "0",
                        "hwaddr": "aa:bb:cc:dd:ee:ff",
                        "expire": "never",
                    },
                    {
                        "address": "192.0.2.21",
                        "hostname": "expired",
                        "if": "em0",
                        "is_reserved": "0",
                        "hwaddr": "aa:aa:aa",
                        "expire": past_ts,
                    },
                    {
                        "address": "192.0.2.22",
                        "hostname": "dynamic-host",
                        "if": "em0",
                        "if_name": "lan",
                        "is_reserved": "0",
                        "client_id": "01:11:22",
                        "hwaddr": "",
                        "expire": "never",
                    },
                    {
                        "address": "192.0.2.23",
                        "hostname": "static-host-list",
                        "if": "em0",
                        "if_name": "lan",
                        "is_reserved": ["hwaddr"],
                        "client_id": "01:33:44",
                        "hwaddr": "bb:bb:bb",
                        "expire": "never",
                    },
                    {
                        "address": "192.0.2.24",
                        "hostname": "dynamic-host-empty-list",
                        "if": "em0",
                        "is_reserved": [],
                        "hwaddr": "cc:cc:cc",
                        "expire": "never",
                    },
                    {
                        "address": None,
                        "hostname": "missing-address",
                        "if": "em0",
                        "expire": "never",
                    },
                    {
                        "address": "192.0.2.25",
                        "hostname": "missing-interface",
                        "expire": "never",
                    },
                ]
            }
        )

        leases = await client._get_dnsmasq_leases()
        assert len(leases) == 4
        lease_by_address = {lease["address"]: lease for lease in leases}

        assert lease_by_address["192.0.2.20"]["type"] == "dynamic"
        assert lease_by_address["192.0.2.20"]["if_name"] == "em0"

        assert lease_by_address["192.0.2.22"]["type"] == "dynamic"
        assert lease_by_address["192.0.2.22"]["if_name"] == "lan"
        assert lease_by_address["192.0.2.22"]["client_id"] == "01:11:22"
        assert lease_by_address["192.0.2.22"]["mac"] is None
        assert lease_by_address["192.0.2.22"]["expires"] == "never"

        assert lease_by_address["192.0.2.23"]["type"] == "static"
        assert lease_by_address["192.0.2.23"]["reserved_by"] == ["hwaddr"]
        assert lease_by_address["192.0.2.23"]["client_id"] == "01:33:44"
        assert lease_by_address["192.0.2.24"]["type"] == "dynamic"
        assert "192.0.2.25" not in lease_by_address
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_isc_dhcpv4_and_v6_cover_invalid_and_expired_paths(
    make_client: ClientType,
) -> None:
    """Verify ISC lease helpers skip invalid, unparsable, and expired lease rows.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates invalid/expired ISC lease handling paths.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        local_tz = datetime.now().astimezone().tzinfo
        assert local_tz is not None
        client._get_opnsense_timezone = AsyncMock(return_value=local_tz)
        past_str = (datetime.now(tz=local_tz) - timedelta(hours=2)).strftime("%Y/%m/%d %H:%M:%S")

        client._check_optional_get_endpoint = AsyncMock(
            side_effect=[
                ("available", "bad-response"),
                ("available", {"rows": "bad"}),
                (
                    "available",
                    {
                        "rows": [
                            None,
                            {"state": "inactive", "mac": "skip"},
                            {
                                "state": "active",
                                "mac": "bad-identity",
                                "address": None,
                                "if": "em0",
                            },
                            {
                                "state": "active",
                                "mac": "bad-time",
                                "address": "10.0.0.7",
                                "if": "em0",
                                "ends": "invalid-date",
                            },
                            {
                                "state": "active",
                                "mac": "expired",
                                "address": "10.0.0.8",
                                "if": "em0",
                                "ends": past_str,
                            },
                            {
                                "state": "active",
                                "mac": "ok",
                                "address": "10.0.0.9",
                                "if": "em0",
                            },
                        ]
                    },
                ),
            ]
        )
        assert await client._get_isc_dhcpv4_leases() == []
        assert await client._get_isc_dhcpv4_leases() == []
        v4_leases = await client._get_isc_dhcpv4_leases()
        assert len(v4_leases) == 1
        assert v4_leases[0]["mac"] == "ok"
        assert v4_leases[0]["expires"] is None

        client._check_optional_get_endpoint = AsyncMock(
            side_effect=[
                ("available", "bad-response"),
                ("available", {"rows": "bad"}),
                (
                    "available",
                    {
                        "rows": [
                            None,
                            {"state": "inactive", "mac": "skip-v6"},
                            {"state": "active", "mac": None},
                            {
                                "state": "active",
                                "mac": "bad-identity-v6",
                                "address": "2001:db8::7",
                            },
                            {
                                "state": "active",
                                "mac": "bad-time-v6",
                                "address": "2001:db8::8",
                                "if": "em1",
                                "ends": "invalid-date",
                            },
                            {
                                "state": "active",
                                "mac": "expired-v6",
                                "address": "2001:db8::9",
                                "if": "em1",
                                "ends": past_str,
                            },
                            {
                                "state": "active",
                                "mac": "ok-v6",
                                "address": "2001:db8::10",
                                "if": "em1",
                            },
                        ]
                    },
                ),
            ]
        )
        assert await client._get_isc_dhcpv6_leases() == []
        assert await client._get_isc_dhcpv6_leases() == []
        v6_leases = await client._get_isc_dhcpv6_leases()
        assert len(v6_leases) == 1
        assert v6_leases[0]["mac"] == "ok-v6"
        assert v6_leases[0]["expires"] is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_version_switched_dhcp_endpoints_rows_empty_when_reservation_unavailable(
    make_client: ClientType,
) -> None:
    """Verify lease endpoints return empty rows when reservation endpoint is unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates behavior via assertions only.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._check_optional_get_endpoint = AsyncMock(
            side_effect=[
                CategoryResult({"rows": []}, "available", True),
                CategoryResult({}, "missing", False),
            ]
        )

        assert await client._get_kea_dhcpv4_leases() == []
        assert client._check_optional_get_endpoint.await_count == 2
        assert (
            client._check_optional_get_endpoint.await_args_list[0].args[0]
            == "/api/kea/leases4/search"
        )
        assert (
            client._check_optional_get_endpoint.await_args_list[1].args[0]
            == "/api/kea/dhcpv4/search_reservation"
        )

        client._check_optional_get_endpoint = AsyncMock(return_value=("missing", {}))
        assert await client._get_isc_dhcpv4_leases() == []
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/dhcpv4/leases/search_lease"
        )

        client._check_optional_get_endpoint = AsyncMock(return_value=("missing", {}))
        assert await client._get_isc_dhcpv6_leases() == []
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/dhcpv6/leases/search_lease"
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_version_switched_kea_dhcpv4_returns_leases_when_reservation_unavailable(
    make_client: ClientType,
) -> None:
    """Verify Kea returns unknown lease type when reservation endpoint is unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This async test validates behavior via assertions only.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        lease_payload = {
            "rows": [
                {
                    "state": "0",
                    "hwaddr": "aa:bb:cc:dd:ee:ff",
                    "address": "192.0.2.10",
                    "if_name": "em0",
                    "hostname": "host-a.",
                }
            ]
        }
        client._check_optional_get_endpoint = AsyncMock(
            side_effect=[
                CategoryResult(lease_payload, "available", True),
                CategoryResult({}, "missing", False),
            ]
        )

        leases = await client._get_kea_dhcpv4_leases()

        assert len(leases) == 1
        assert leases[0].get("mac") == "aa:bb:cc:dd:ee:ff"
        assert leases[0].get("type") == "unknown"
        assert client._check_optional_get_endpoint.await_count == 2
        assert (
            client._check_optional_get_endpoint.await_args_list[0].args[0]
            == "/api/kea/leases4/search"
        )
        assert (
            client._check_optional_get_endpoint.await_args_list[1].args[0]
            == "/api/kea/dhcpv4/search_reservation"
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("use_snake_case", "expected_kea", "expected_v4", "expected_v6"),
    [
        (
            True,
            "/api/kea/dhcpv4/search_reservation",
            "/api/dhcpv4/leases/search_lease",
            "/api/dhcpv6/leases/search_lease",
        ),
        (
            False,
            "/api/kea/dhcpv4/searchReservation",
            "/api/dhcpv4/leases/searchLease",
            "/api/dhcpv6/leases/searchLease",
        ),
    ],
)
async def test_dhcp_switched_endpoints_follow_selected_case(
    make_client: ClientType,
    use_snake_case: bool,
    expected_kea: str,
    expected_v4: str,
    expected_v6: str,
) -> None:
    """Verify DHCP helpers choose snake_case or camelCase endpoints consistently.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        use_snake_case (bool): Whether the client should prefer snake_case endpoints.
        expected_kea (str): Expected reservation lookup endpoint path.
        expected_v4 (str): Expected ISC DHCPv4 lease endpoint path.
        expected_v6 (str): Expected ISC DHCPv6 lease endpoint path.

    Returns:
        None: This test validates DHCP endpoint selection behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = use_snake_case
        local_tz = datetime.now().astimezone().tzinfo
        assert local_tz is not None
        client._get_opnsense_timezone = AsyncMock(return_value=local_tz)
        client._check_optional_get_endpoint = optional_get_from_safe_dict(client)

        client._safe_dict_get = AsyncMock(side_effect=[{"rows": []}, {"rows": []}])
        await client._get_kea_dhcpv4_leases()
        assert client._check_optional_get_endpoint.await_args_list[1].args[0] == expected_kea

        client._check_optional_get_endpoint = AsyncMock(return_value=("available", {"rows": []}))
        await client._get_isc_dhcpv4_leases()
        client._check_optional_get_endpoint.assert_awaited_once_with(expected_v4)

        client._check_optional_get_endpoint = AsyncMock(return_value=("available", {"rows": []}))
        await client._get_isc_dhcpv6_leases()
        client._check_optional_get_endpoint.assert_awaited_once_with(expected_v6)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_version_switched_get_arp_table_endpoint_unavailable(
    make_client: ClientType,
) -> None:
    """Verify ARP table helper fails closed when endpoint becomes unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This async test validates endpoint-gating behavior via assertions only.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            side_effect=[
                CategoryResult({"rows": []}, "available", True),
                CategoryResult({}, "missing", False),
            ]
        )

        assert await client.get_arp_table(resolve_hostnames=True) == []
        assert client._check_optional_get_endpoint.await_count == 1

        assert await client.get_arp_table(resolve_hostnames=False) == []
        assert client._check_optional_get_endpoint.await_count == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_arp_table_result_rejects_non_mapping_data(
    make_client: ClientType,
) -> None:
    """Verify available ARP responses with non-mapping data are malformed.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates schema-aware ARP response handling.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=CategoryResult("bad-response", "available", True)
        )

        assert await client.get_arp_table_result() == CategoryResult([], "malformed", False)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_version_switched_get_kea_interfaces_endpoint_unavailable(
    make_client: ClientType,
) -> None:
    """Verify Kea interface helper fails closed when endpoint becomes unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This async test validates endpoint-gating behavior via assertions only.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            side_effect=[
                CategoryResult({"dhcpv4": {"general": {"enabled": "0"}}}, "available", True),
                CategoryResult({}, "missing", False),
            ]
        )

        assert await client._get_kea_interfaces() == {}
        assert client._check_optional_get_endpoint.await_count == 1
        assert (
            client._check_optional_get_endpoint.await_args_list[0].args[0] == "/api/kea/dhcpv4/get"
        )

        assert await client._get_kea_interfaces() == {}
        assert client._check_optional_get_endpoint.await_count == 2
        assert (
            client._check_optional_get_endpoint.await_args_list[1].args[0] == "/api/kea/dhcpv4/get"
        )
    finally:
        await client.async_close()
