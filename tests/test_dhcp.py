"""Tests for `aiopnsense.dhcp`."""

from collections.abc import Callable, MutableMapping
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


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
        client.is_endpoint_available = AsyncMock(return_value=True)
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
async def test_get_arp_table_uses_get_query_param(make_client: ClientType) -> None:
    """Verify ARP table lookup uses GET endpoint with the expected query parameter.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates resolve query parameter forwarding.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(return_value={"rows": []})

        await client.get_arp_table(resolve_hostnames=True)
        client._safe_dict_get.assert_awaited_with(
            "/api/diagnostics/interface/search_arp?resolve=yes"
        )

        await client.get_arp_table(resolve_hostnames=False)
        assert client._safe_dict_get.await_args_list[1].args[0] == (
            "/api/diagnostics/interface/search_arp?resolve=no"
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "endpoint"),
    [
        ("_get_kea_dhcpv4_leases", "/api/kea/leases4/search"),
        ("_get_dnsmasq_leases", "/api/dnsmasq/leases/search"),
        ("_get_isc_dhcpv4_leases", "/api/dhcpv4/service/status"),
        ("_get_isc_dhcpv6_leases", "/api/dhcpv6/service/status"),
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
        client.is_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get = AsyncMock()
        leases = await getattr(client, method_name)()
        assert leases == []
        client.is_endpoint_available.assert_awaited_once_with(endpoint)
        client._safe_dict_get.assert_not_awaited()
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
async def test_is_dnsmasq_reserved_lease_handles_legacy_and_list_flags(
    make_client: ClientType,
    raw_reserved: Any,
    expected: bool,
) -> None:
    """Verify dnsmasq reserved lease detection supports legacy and new value shapes.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        raw_reserved (Any): Raw ``is_reserved`` value from API payload.
        expected (bool): Expected reserved/static detection result.

    Returns:
        None: This test validates reserved lease coercion behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        assert client._is_dnsmasq_reserved_lease(raw_reserved) is expected
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
        client.is_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            side_effect=[
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
                {"rows": []},
            ]
        )
        v4_safe_dict_get = client._safe_dict_get
        v4 = await client._get_isc_dhcpv4_leases()
        assert isinstance(v4, list) and len(v4) == 1
        assert v4[0]["address"] == "10.0.0.1"
        assert v4[0]["mac"] == "m1"
        assert v4[0]["hostname"] == "h1"
        assert isinstance(v4[0].get("expires"), datetime)
        v4_safe_dict_get.assert_awaited_once_with("/api/dhcpv4/leases/search_lease")

        # v6: ends missing -> field passed through
        client.is_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {
                        "state": "active",
                        "mac": "m2",
                        "address": "fe80::1",
                        "hostname": "h2",
                        "if": "em1",
                    }
                ]
            }
        )
        v6_safe_dict_get = client._safe_dict_get
        v6 = await client._get_isc_dhcpv6_leases()
        assert isinstance(v6, list) and len(v6) == 1
        assert v6[0]["address"] == "fe80::1"
        assert v6[0]["mac"] == "m2"
        assert v6[0]["hostname"] == "h2"
        assert "ends" not in v6[0]
        assert v6[0].get("expires") is None
        assert "ends_at" not in v6[0] or v6[0]["ends_at"] is None
        assert "expiry" not in v6[0] or v6[0]["expiry"] is None
        v6_safe_dict_get.assert_awaited_once_with("/api/dhcpv6/leases/search_lease")
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
        client._get_opnsense_timezone.assert_awaited_once_with()
        client._get_kea_dhcpv4_leases.assert_awaited_once_with(opnsense_tz=local_tz)
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
                        },
                    }
                }
            }
        )

        assert await client._get_kea_interfaces() == {"em0": "LAN"}
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
        client.is_endpoint_available = AsyncMock(return_value=True)
        future_ts = int(datetime.now(tz=timezone.utc).timestamp()) + 3600
        past_ts = int(datetime.now(tz=timezone.utc).timestamp()) - 3600

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
                            "expire": future_ts,
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
            lease["address"] == "192.0.2.13" and lease["type"] == "static" for lease in leases
        )

        # Reservation lookup failures should not misclassify entries as dynamic/static.
        client._safe_dict_get = AsyncMock(
            side_effect=[
                {"rows": [{"state": "0", "hwaddr": "aa", "address": "10.0.0.1"}]},
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
async def test_get_dnsmasq_leases_invalid_rows_and_expiry_paths(make_client: ClientType) -> None:
    """Verify dnsmasq lease parsing handles malformed and expired rows safely.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates dnsmasq lease expiry and type parsing.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(return_value={"rows": "bad-shape"})
        assert await client._get_dnsmasq_leases() == []

        past_ts = int(datetime.now(tz=timezone.utc).timestamp()) - 3600
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    None,
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
                        "is_reserved": "0",
                        "hwaddr": "",
                        "expire": "never",
                    },
                    {
                        "address": "192.0.2.23",
                        "hostname": "static-host-list",
                        "if": "em0",
                        "is_reserved": ["hwaddr"],
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
                ]
            }
        )

        leases = await client._get_dnsmasq_leases()
        assert len(leases) == 3
        lease_by_address = {lease["address"]: lease for lease in leases}

        assert lease_by_address["192.0.2.22"]["type"] == "dynamic"
        assert lease_by_address["192.0.2.22"]["mac"] is None
        assert lease_by_address["192.0.2.22"]["expires"] == "never"

        assert lease_by_address["192.0.2.23"]["type"] == "static"
        assert lease_by_address["192.0.2.24"]["type"] == "dynamic"
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
        client.is_endpoint_available = AsyncMock(return_value=True)

        past_str = (datetime.now(tz=local_tz) - timedelta(hours=2)).strftime("%Y/%m/%d %H:%M:%S")

        client._safe_dict_get = AsyncMock(
            side_effect=[
                {"rows": "bad"},
                {
                    "rows": [
                        {"state": "inactive", "mac": "skip"},
                        {"state": "active", "mac": "bad-time", "ends": "invalid-date"},
                        {"state": "active", "mac": "expired", "ends": past_str},
                        {"state": "active", "mac": "ok", "address": "10.0.0.9", "if": "em0"},
                    ]
                },
            ]
        )
        assert await client._get_isc_dhcpv4_leases() == []
        v4_leases = await client._get_isc_dhcpv4_leases()
        assert len(v4_leases) == 1
        assert v4_leases[0]["mac"] == "ok"
        assert v4_leases[0]["expires"] is None

        client._safe_dict_get = AsyncMock(
            side_effect=[
                {"rows": "bad"},
                {
                    "rows": [
                        None,
                        {"state": "active", "mac": "bad-time-v6", "ends": "invalid-date"},
                        {"state": "active", "mac": "expired-v6", "ends": past_str},
                        {"state": "active", "mac": "ok-v6", "address": "2001:db8::10", "if": "em1"},
                    ]
                },
            ]
        )
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
        client.is_endpoint_available = AsyncMock(side_effect=[True, False])
        client._safe_dict_get = AsyncMock(return_value={"rows": []})

        assert await client._get_kea_dhcpv4_leases() == []
        client._safe_dict_get.assert_awaited_once_with("/api/kea/leases4/search")
        assert client.is_endpoint_available.await_count == 2
        assert client.is_endpoint_available.await_args_list[0].args[0] == "/api/kea/leases4/search"
        assert (
            client.is_endpoint_available.await_args_list[1].args[0]
            == "/api/kea/dhcpv4/search_reservation"
        )

        client.is_endpoint_available = AsyncMock(side_effect=[True, False])
        client._safe_dict_get = AsyncMock()
        assert await client._get_isc_dhcpv4_leases() == []
        assert client.is_endpoint_available.await_count == 2
        assert (
            client.is_endpoint_available.await_args_list[0].args[0] == "/api/dhcpv4/service/status"
        )
        assert (
            client.is_endpoint_available.await_args_list[1].args[0]
            == "/api/dhcpv4/leases/search_lease"
        )
        client._safe_dict_get.assert_not_awaited()

        client.is_endpoint_available = AsyncMock(side_effect=[True, False])
        client._safe_dict_get = AsyncMock()
        assert await client._get_isc_dhcpv6_leases() == []
        assert client.is_endpoint_available.await_count == 2
        assert (
            client.is_endpoint_available.await_args_list[0].args[0] == "/api/dhcpv6/service/status"
        )
        assert (
            client.is_endpoint_available.await_args_list[1].args[0]
            == "/api/dhcpv6/leases/search_lease"
        )
        client._safe_dict_get.assert_not_awaited()
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
        client.is_endpoint_available = AsyncMock(side_effect=[True, False])
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {
                        "state": "0",
                        "hwaddr": "aa:bb:cc:dd:ee:ff",
                        "address": "192.0.2.10",
                        "hostname": "host-a.",
                    }
                ]
            }
        )

        leases = await client._get_kea_dhcpv4_leases()

        assert len(leases) == 1
        assert leases[0].get("mac") == "aa:bb:cc:dd:ee:ff"
        assert leases[0].get("type") == "unknown"
        client._safe_dict_get.assert_awaited_once_with("/api/kea/leases4/search")
        assert client.is_endpoint_available.await_count == 2
        assert client.is_endpoint_available.await_args_list[0].args[0] == "/api/kea/leases4/search"
        assert (
            client.is_endpoint_available.await_args_list[1].args[0]
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
        client.is_endpoint_available = AsyncMock(return_value=True)

        client._safe_dict_get = AsyncMock(side_effect=[{"rows": []}, {"rows": []}])
        await client._get_kea_dhcpv4_leases()
        assert client._safe_dict_get.await_args_list[1].args[0] == expected_kea

        client._safe_dict_get = AsyncMock(return_value={"rows": []})
        await client._get_isc_dhcpv4_leases()
        client._safe_dict_get.assert_awaited_once_with(expected_v4)

        client._safe_dict_get = AsyncMock(return_value={"rows": []})
        await client._get_isc_dhcpv6_leases()
        client._safe_dict_get.assert_awaited_once_with(expected_v6)
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
        client.is_endpoint_available = AsyncMock(side_effect=[True, False])
        client._safe_dict_get = AsyncMock(return_value={"rows": []})

        assert await client.get_arp_table(resolve_hostnames=True) == []
        client._safe_dict_get.assert_awaited_once_with(
            "/api/diagnostics/interface/search_arp?resolve=yes"
        )
        assert client.is_endpoint_available.await_count == 1
        assert (
            client.is_endpoint_available.await_args_list[0].args[0]
            == "/api/diagnostics/interface/search_arp"
        )

        client._safe_dict_get = AsyncMock()
        assert await client.get_arp_table(resolve_hostnames=False) == []
        client._safe_dict_get.assert_not_awaited()
        assert client.is_endpoint_available.await_count == 2
        assert (
            client.is_endpoint_available.await_args_list[1].args[0]
            == "/api/diagnostics/interface/search_arp"
        )
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
        client.is_endpoint_available = AsyncMock(side_effect=[True, False])
        client._safe_dict_get = AsyncMock(return_value={"dhcpv4": {"general": {"enabled": "0"}}})

        assert await client._get_kea_interfaces() == {}
        client._safe_dict_get.assert_awaited_once_with("/api/kea/dhcpv4/get")
        assert client.is_endpoint_available.await_count == 1
        assert client.is_endpoint_available.await_args_list[0].args[0] == "/api/kea/dhcpv4/get"

        client._safe_dict_get = AsyncMock()
        assert await client._get_kea_interfaces() == {}
        client._safe_dict_get.assert_not_awaited()
        assert client.is_endpoint_available.await_count == 2
        assert client.is_endpoint_available.await_args_list[1].args[0] == "/api/kea/dhcpv4/get"
    finally:
        await client.async_close()
