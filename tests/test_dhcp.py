"""Tests for `aiopnsense.dhcp`."""

from collections.abc import Callable, MutableMapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseClient
from aiopnsense.exceptions import (
    OPNsenseConnectionError,
    OPNsensePrivilegeMissing,
    OPNsenseTimeoutError,
)
from tests.conftest import FakeResponse, make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "status", "failure_stage", "expected"),
    [
        ({"rows": []}, 200, None, []),
        (
            {
                "rows": [
                    {"mac": "aa:bb:cc:dd:ee:ff", "ip": "2001:db8::1", "intf": "em0"},
                    {"mac": "aa:bb:cc:dd:ee:ff", "ip": "fe80::1%em0", "intf": "em0"},
                ]
            },
            200,
            None,
            [
                {"mac": "aa:bb:cc:dd:ee:ff", "ip": "2001:db8::1", "intf": "em0"},
                {"mac": "aa:bb:cc:dd:ee:ff", "ip": "fe80::1%em0", "intf": "em0"},
            ],
        ),
        ({}, 200, None, None),
        ({"rows": None}, 200, None, None),
        ({"rows": {}}, 200, None, None),
        ([], 200, None, None),
        (None, 403, "probe", None),
        (None, 404, "probe", None),
        (None, 500, "request", None),
        (TimeoutError("NDP request timed out"), 200, "request", None),
        (TimeoutError("NDP probe timed out"), 200, "probe", None),
    ],
)
async def test_get_ndp_table_http_results(
    make_client: ClientType,
    payload: object,
    status: int,
    failure_stage: Literal["request", "probe"] | None,
    expected: list[dict[str, str]] | None,
) -> None:
    """Preserve IPv6 neighbor rows and distinguish absence from failed discovery.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        payload (object): JSON response or simulated transport error.
        status (int): HTTP status of the table lookup or failed probe.
        failure_stage (Literal["request", "probe"] | None): Stage that fails.
        expected (list[dict[str, str]] | None): Public inventory result.
    """
    client, session = make_mock_session_client(make_client)
    client._get = AsyncMock(side_effect=client._do_get)
    response = FakeResponse(status=status, ok=status == 200, json_payload=payload)
    result = payload if isinstance(payload, TimeoutError) else response
    session.get.side_effect = [result] if failure_stage == "probe" else [FakeResponse(), result]

    assert await client.get_ndp_table() == expected
    assert all(
        call.args[0] == "http://localhost/api/diagnostics/interface/search_ndp"
        for call in session.get.call_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["probe", "request"])
@pytest.mark.parametrize(
    ("status", "timeout", "expected_error"),
    [
        (403, False, OPNsensePrivilegeMissing),
        (500, False, OPNsenseConnectionError),
        (200, True, OPNsenseTimeoutError),
    ],
    ids=["permission", "server", "timeout"],
)
async def test_get_ndp_table_propagates_public_errors(
    make_client: ClientType,
    failure_stage: Literal["probe", "request"],
    status: int,
    timeout: bool,
    expected_error: type[OPNsenseConnectionError],
) -> None:
    """Throwing clients expose public errors from either NDP HTTP stage.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        failure_stage (Literal["probe", "request"]): HTTP stage that fails.
        status (int): Response status for simulated HTTP failures.
        timeout (bool): Whether to simulate a transport timeout instead of a response.
        expected_error (type[OPNsenseConnectionError]): Expected public exception type.
    """
    client, session = make_mock_session_client(partial(make_client, throw_errors=True))
    client._get = AsyncMock(side_effect=client._do_get)
    result = TimeoutError("NDP timed out") if timeout else FakeResponse(status=status, ok=False)
    session.get.side_effect = [result] if failure_stage == "probe" else [FakeResponse(), result]

    with pytest.raises(expected_error) as raised:
        await client.get_ndp_table()

    assert type(raised.value) is expected_error
    assert raised.value.status == (None if timeout else status)


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
        client._is_get_endpoint_available = AsyncMock(return_value=True)
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
        client._is_get_endpoint_available = AsyncMock(return_value=True)
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
    ("response", "expected", "failure_stage"),
    [
        ({"rows": []}, [], None),
        ({"rows": [{"mac": "aa:bb:cc:dd:ee:ff"}]}, [{"mac": "aa:bb:cc:dd:ee:ff"}], None),
        ({}, None, None),
        ({"rows": None}, None, None),
        ({"rows": {}}, None, None),
        pytest.param(TimeoutError("ARP request timed out"), None, "request", id="request-timeout"),
        pytest.param(
            TimeoutError("ARP endpoint probe timed out"), None, "probe", id="probe-timeout"
        ),
    ],
)
async def test_get_arp_table_distinguishes_empty_and_failed_lookups(
    make_client: ClientType,
    response: dict[str, Any] | TimeoutError,
    expected: list[dict[str, str]] | None,
    failure_stage: Literal["request", "probe"] | None,
) -> None:
    """Only a successful response with list rows is an authoritative inventory.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        response (dict[str, Any] | TimeoutError): Endpoint payload or transport failure.
        expected (list[dict[str, str]] | None): Public result for the lookup.
        failure_stage (Literal["request", "probe"] | None): Stage where a timeout occurs.
    """
    client, session = make_mock_session_client(make_client)
    get_mock: AsyncMock | None = None
    try:
        if failure_stage == "request":
            client._is_get_endpoint_available = AsyncMock(return_value=True)
            get_mock = AsyncMock(side_effect=client._do_get)
            client._get = get_mock
            session.get.side_effect = response
        elif failure_stage == "probe":
            session.get.side_effect = response
        else:
            client._safe_dict_get = AsyncMock(return_value=response)

        assert await client.get_arp_table(resolve_hostnames=True) == expected
        if get_mock is not None:
            get_mock.assert_awaited_once_with(
                path="/api/diagnostics/interface/search_arp?resolve=yes"
            )
            assert session.get.call_args.args[0].endswith(
                "/api/diagnostics/interface/search_arp?resolve=yes"
            )
        elif failure_stage == "probe":
            session.get.assert_called_once()
            assert session.get.call_args.args[0].endswith("/api/diagnostics/interface/search_arp")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "endpoint"),
    [
        ("_get_kea_dhcpv4_leases", "/api/kea/leases4/search"),
        ("_get_kea_dhcpv6_leases", "/api/kea/leases6/search"),
        ("_get_dnsmasq_leases", "/api/dnsmasq/leases/search"),
        ("_get_isc_dhcpv4_leases", "/api/dhcpv4/leases/search_lease"),
        ("_get_isc_dhcpv6_leases", "/api/dhcpv6/leases/search_lease"),
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
        client._use_snake_case = True
        client._is_get_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get = AsyncMock()
        leases = await getattr(client, method_name)()
        assert leases == []
        client._is_get_endpoint_available.assert_awaited_once_with(endpoint)
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
async def test_is_reserved_lease_handles_legacy_and_list_flags(
    make_client: ClientType,
    raw_reserved: object,
    expected: bool,
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
        client._is_get_endpoint_available = AsyncMock(return_value=True)
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
        client._is_get_endpoint_available = AsyncMock(return_value=True)
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
        client._get_kea_dhcpv6_leases = AsyncMock(
            return_value=[{"if_name": "em0", "address": "2001:db8::1", "mac": "m6"}]
        )
        client._get_isc_dhcpv6_leases = AsyncMock(return_value=[])
        client._get_dnsmasq_leases = AsyncMock(return_value=[])
        client._get_kea_interfaces = AsyncMock(return_value={"em0": "eth0"})
        client._get_dnsmasq_interfaces = AsyncMock(return_value={})

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
        client._get_dnsmasq_interfaces.assert_awaited_once_with()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_dhcp_leases_includes_empty_dnsmasq_range_interface(
    make_client: ClientType,
) -> None:
    """Configured Dnsmasq ranges should remain discoverable without active leases.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates empty Dnsmasq range interface discovery.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        local_tz = datetime.now().astimezone().tzinfo
        assert local_tz is not None
        client._get_opnsense_timezone = AsyncMock(return_value=local_tz)
        client._get_kea_dhcpv4_leases = AsyncMock(return_value=[])
        client._get_kea_dhcpv6_leases = AsyncMock(return_value=[])
        client._get_isc_dhcpv4_leases = AsyncMock(return_value=[])
        client._get_isc_dhcpv6_leases = AsyncMock(return_value=[])
        client._get_dnsmasq_leases = AsyncMock(return_value=[])
        client._get_kea_interfaces = AsyncMock(return_value={})
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {
                        "interface": "opt8",
                        "%interface": "Guest_150",
                        "start_addr": "10.100.150.100",
                        "end_addr": "10.100.150.254",
                    },
                    {
                        "interface": "",
                        "constructor": "opt8",
                        "%constructor": "Guest_150",
                        "start_addr": "::1000",
                        "end_addr": "::2000",
                    },
                ]
            }
        )

        combined = await client.get_dhcp_leases()

        assert combined == {
            "lease_interfaces": {"opt8": "Guest_150"},
            "leases": {},
        }
        client._safe_dict_get.assert_awaited_once_with("/api/dnsmasq/settings/search_range")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_dnsmasq_interfaces_returns_empty_when_endpoint_unavailable(
    make_client: ClientType,
) -> None:
    """Dnsmasq range discovery should fail closed when its endpoint is unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates endpoint availability gating.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get = AsyncMock()

        assert await client._get_dnsmasq_interfaces() == {}
        client._safe_dict_get.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"rows": "invalid"}, {}),
        (
            {
                "rows": [
                    None,
                    {"interface": "", "%interface": "Any"},
                    {"interface": "opt9", "%interface": None},
                    {"constructor": "opt10", "%constructor": "Guest IPv6"},
                ]
            },
            {"opt9": "opt9", "opt10": "Guest IPv6"},
        ),
    ],
)
async def test_get_dnsmasq_interfaces_handles_malformed_ranges(
    make_client: ClientType,
    response: dict[str, Any],
    expected: dict[str, str],
) -> None:
    """Dnsmasq range discovery should skip malformed rows and retain stable identifiers.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        response (dict[str, Any]): Dnsmasq range endpoint response to parse.
        expected (dict[str, str]): Expected valid interface mapping.

    Returns:
        None: This test validates malformed range handling.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(return_value=response)

        assert await client._get_dnsmasq_interfaces() == expected
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
        client._is_get_endpoint_available = AsyncMock(return_value=True)
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
async def test_get_kea_dhcpv4_leases_skips_redundant_reservation_lookup(
    make_client: ClientType,
) -> None:
    """Use lease reservation metadata without collecting the reservation table.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test verifies the workload-reduction boundary and classifications.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {
                        "state": "0",
                        "hwaddr": "aa:bb:cc:dd:ee:01",
                        "address": "192.0.2.10",
                        "is_reserved": [],
                    },
                    {
                        "state": "0",
                        "hwaddr": "aa:bb:cc:dd:ee:02",
                        "address": "192.0.2.11",
                        "is_reserved": ["hwaddr"],
                    },
                    {
                        "state": "0",
                        "hwaddr": "aa:bb:cc:dd:ee:03",
                        "address": "192.0.2.12",
                        "is_reserved": False,
                    },
                    {
                        "state": "0",
                        "hwaddr": "aa:bb:cc:dd:ee:04",
                        "address": "192.0.2.13",
                        "is_reserved": True,
                    },
                ]
            }
        )

        leases = await client._get_kea_dhcpv4_leases()

        assert [lease["type"] for lease in leases] == [
            "dynamic",
            "static",
            "dynamic",
            "static",
        ]
        client._safe_dict_get.assert_awaited_once_with("/api/kea/leases4/search")
        client._is_get_endpoint_available.assert_awaited_once_with("/api/kea/leases4/search")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("is_reserved", [None, {"unexpected": "value"}])
async def test_get_kea_dhcpv4_leases_falls_back_for_invalid_reservation_metadata(
    make_client: ClientType,
    is_reserved: object,
) -> None:
    """Use reservation-table fallback for unsupported Kea metadata values.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        is_reserved (object): Unsupported reservation metadata value to exercise.

    Returns:
        None: This test verifies matching legacy reservations remain static.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "rows": [
                        {
                            "state": "0",
                            "hwaddr": "aa:bb:cc:dd:ee:01",
                            "address": "192.0.2.10",
                            "is_reserved": is_reserved,
                        }
                    ]
                },
                {
                    "rows": [
                        {
                            "hw_address": "aa:bb:cc:dd:ee:01",
                            "ip_address": "192.0.2.10",
                        }
                    ]
                },
            ]
        )

        leases = await client._get_kea_dhcpv4_leases()

        assert leases[0]["type"] == "static"
        assert client._safe_dict_get.await_count == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reservation_rows",
    [
        [],
        [{"hw_address": "aa:bb:cc:dd:ee:02", "ip_address": "192.0.2.11"}],
    ],
)
async def test_get_kea_dhcpv4_leases_invalid_truthy_metadata_falls_back_to_dynamic(
    make_client: ClientType,
    reservation_rows: list[dict[str, str]],
) -> None:
    """Classify invalid truthy metadata from a non-matching reservation fallback.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        reservation_rows (list[dict[str, str]]): Empty or mismatched reservation-table rows.

    Returns:
        None: This test verifies unsupported truthy metadata does not imply a static lease.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "rows": [
                        {
                            "state": "0",
                            "hwaddr": "aa:bb:cc:dd:ee:01",
                            "address": "192.0.2.10",
                            "is_reserved": {"unexpected": "truthy"},
                        }
                    ]
                },
                {"rows": reservation_rows},
            ]
        )

        leases = await client._get_kea_dhcpv4_leases()

        assert leases[0]["type"] == "dynamic"
        assert client._safe_dict_get.await_count == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_kea_dhcpv4_leases_ignores_expired_rows_for_reservation_lookup(
    make_client: ClientType,
) -> None:
    """Avoid reservation lookup when only an expired row lacks metadata.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test verifies expired rows do not create reservation-table I/O.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        past_ts = int(datetime.now(tz=UTC).timestamp()) - 3600
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {
                        "state": "0",
                        "hwaddr": "aa:bb:cc:dd:ee:01",
                        "address": "192.0.2.10",
                        "expire": past_ts,
                    },
                    {
                        "state": "0",
                        "hwaddr": "aa:bb:cc:dd:ee:02",
                        "address": "192.0.2.11",
                        "is_reserved": 0,
                    },
                    {
                        "state": "0",
                        "hwaddr": "aa:bb:cc:dd:ee:03",
                        "address": "192.0.2.12",
                        "is_reserved": ["hwaddr"],
                    },
                ]
            }
        )

        leases = await client._get_kea_dhcpv4_leases()

        assert [lease["type"] for lease in leases] == ["dynamic", "static"]
        client._safe_dict_get.assert_awaited_once_with("/api/kea/leases4/search")
        client._is_get_endpoint_available.assert_awaited_once_with("/api/kea/leases4/search")
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
        client._is_get_endpoint_available = AsyncMock(return_value=True)
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
async def test_get_kea_dhcpv6_leases_preserves_unknown_reservation_metadata(
    make_client: ClientType,
) -> None:
    """Keep unsupported Kea DHCPv6 reservation metadata unclassified.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test verifies unsupported values remain unknown while false is dynamic.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {"address": "2001:db8::10", "duid": "missing", "state": 0},
                    {
                        "address": "2001:db8::11",
                        "duid": "none",
                        "state": 0,
                        "is_reserved": None,
                    },
                    {
                        "address": "2001:db8::12",
                        "duid": "mapping",
                        "state": 0,
                        "is_reserved": {"unexpected": "value"},
                    },
                    {
                        "address": "2001:db8::13",
                        "duid": "false",
                        "state": 0,
                        "is_reserved": False,
                    },
                ]
            }
        )

        leases = await client._get_kea_dhcpv6_leases()

        assert [lease["type"] for lease in leases] == [
            "unknown",
            "unknown",
            "unknown",
            "dynamic",
        ]
        client._safe_dict_get.assert_awaited_once_with("/api/kea/leases6/search")
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
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_kea_and_dnsmasq_preserve_overflowing_expiration(
    make_client: ClientType,
) -> None:
    """Keep leases whose numeric expiration exceeds the platform datetime range.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates overflow-safe Kea and dnsmasq expiration parsing.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        overflowing_expiration = 10**40
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {
                        "address": "192.0.2.30",
                        "hostname": "kea-overflow",
                        "hwaddr": "aa:bb:cc:dd:ee:30",
                        "if_name": "lan",
                        "is_reserved": "0",
                        "state": "0",
                        "expire": overflowing_expiration,
                    }
                ]
            }
        )

        kea_leases = await client._get_kea_dhcpv4_leases()
        assert len(kea_leases) == 1
        assert kea_leases[0]["expires"] == overflowing_expiration

        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {
                        "address": "192.0.2.31",
                        "hostname": "dnsmasq-overflow",
                        "hwaddr": "aa:bb:cc:dd:ee:31",
                        "if_name": "lan",
                        "is_reserved": "0",
                        "expire": overflowing_expiration,
                    }
                ]
            }
        )

        dnsmasq_leases = await client._get_dnsmasq_leases()
        assert len(dnsmasq_leases) == 1
        assert dnsmasq_leases[0]["expires"] == overflowing_expiration
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
        client._is_get_endpoint_available = AsyncMock(return_value=True)

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
async def test_version_switched_dhcp_endpoints_skip_unneeded_kea_reservations(
    make_client: ClientType,
) -> None:
    """Skip Kea reservations for empty rows and gate unavailable ISC endpoints.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates behavior via assertions only.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(return_value={"rows": []})

        assert await client._get_kea_dhcpv4_leases() == []
        client._safe_dict_get.assert_awaited_once_with("/api/kea/leases4/search")
        client._is_get_endpoint_available.assert_awaited_once_with("/api/kea/leases4/search")

        client._is_get_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get = AsyncMock()
        assert await client._get_isc_dhcpv4_leases() == []
        client._is_get_endpoint_available.assert_awaited_once_with(
            "/api/dhcpv4/leases/search_lease"
        )
        client._safe_dict_get.assert_not_awaited()

        client._is_get_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get = AsyncMock()
        assert await client._get_isc_dhcpv6_leases() == []
        client._is_get_endpoint_available.assert_awaited_once_with(
            "/api/dhcpv6/leases/search_lease"
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
        client._is_get_endpoint_available = AsyncMock(side_effect=[True, False])
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
        assert client._is_get_endpoint_available.await_count == 2
        assert (
            client._is_get_endpoint_available.await_args_list[0].args[0]
            == "/api/kea/leases4/search"
        )
        assert (
            client._is_get_endpoint_available.await_args_list[1].args[0]
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
        client._is_get_endpoint_available = AsyncMock(return_value=True)

        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "rows": [
                        {
                            "state": "0",
                            "hwaddr": "aa:bb:cc:dd:ee:ff",
                            "address": "192.0.2.10",
                        }
                    ]
                },
                {"rows": []},
            ]
        )
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
    """Verify an unavailable ARP endpoint is not an authoritative empty table.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This async test validates endpoint-gating behavior via assertions only.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(side_effect=[True, False])
        client._safe_dict_get = AsyncMock(return_value={"rows": []})

        assert await client.get_arp_table(resolve_hostnames=True) == []
        client._safe_dict_get.assert_awaited_once_with(
            "/api/diagnostics/interface/search_arp?resolve=yes"
        )
        assert client._is_get_endpoint_available.await_count == 1
        assert (
            client._is_get_endpoint_available.await_args_list[0].args[0]
            == "/api/diagnostics/interface/search_arp"
        )

        client._safe_dict_get = AsyncMock()
        assert await client.get_arp_table(resolve_hostnames=False) is None
        client._safe_dict_get.assert_not_awaited()
        assert client._is_get_endpoint_available.await_count == 2
        assert (
            client._is_get_endpoint_available.await_args_list[1].args[0]
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
        client._is_get_endpoint_available = AsyncMock(side_effect=[True, False])
        client._safe_dict_get = AsyncMock(return_value={"dhcpv4": {"general": {"enabled": "0"}}})

        assert await client._get_kea_interfaces() == {}
        client._safe_dict_get.assert_awaited_once_with("/api/kea/dhcpv4/get")
        assert client._is_get_endpoint_available.await_count == 1
        assert client._is_get_endpoint_available.await_args_list[0].args[0] == "/api/kea/dhcpv4/get"

        client._safe_dict_get = AsyncMock()
        assert await client._get_kea_interfaces() == {}
        client._safe_dict_get.assert_not_awaited()
        assert client._is_get_endpoint_available.await_count == 2
        assert client._is_get_endpoint_available.await_args_list[1].args[0] == "/api/kea/dhcpv4/get"
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("family_ip", ["192.0.2.1", "2001:0db8::1", "fe80::1%igc1"])
async def test_host_observations_normalizes_packet_sightings(
    make_client: ClientType, family_ip: str
) -> None:
    """Expose timestamped MAC observations from either address family over HTTP.

    Args:
        make_client (ClientType): Factory for a client with a mocked HTTP session.
        family_ip (str): IPv4 or IPv6 address observed by hostwatch.
    """
    client, session = make_mock_session_client(make_client)
    client._post = AsyncMock(side_effect=client._do_post)
    session.post.return_value = FakeResponse(
        json_payload={
            "total": 1,
            "rows": [
                {
                    "source": "discovery",
                    "ether_address": "AA-BB-CC-DD-EE-01",
                    "ip_address": family_ip,
                    "last_seen": "2026-01-01T02:00:00+02:00",
                }
            ],
        }
    )
    result = await client.get_host_observations()
    assert result is not None
    assert result["source"] == "discovery"
    assert result["rows"][0]["mac"] == "aa:bb:cc:dd:ee:01"
    assert result["rows"][0]["last_seen"] == datetime(2026, 1, 1, tzinfo=UTC)
    assert result["rows"][0]["ip"] == family_ip.replace("0db8", "db8")
    assert all(
        call.args[0] == "http://localhost/api/hostdiscovery/service/search"
        for call in session.post.call_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("total", [1.9, 1.0, True, False, "1", "1.9", None, -1, 2])
async def test_host_observations_rejects_invalid_totals(
    make_client: ClientType, total: object
) -> None:
    """Reject malformed or mismatched counts even when the observation is valid.

    Args:
        make_client (ClientType): Factory for a client with a mocked HTTP session.
        total (object): Invalid count supplied by the search endpoint.
    """
    client, session = make_mock_session_client(make_client)
    client._post = AsyncMock(side_effect=client._do_post)
    session.post.return_value = FakeResponse(
        json_payload={
            "total": total,
            "rows": [
                {
                    "source": "discovery",
                    "ether_address": "aa:bb:cc:dd:ee:01",
                    "ip_address": "192.0.2.1",
                    "last_seen": "2026-01-01T00:00:00Z",
                }
            ],
        }
    )

    assert await client.get_host_observations() is None


@pytest.mark.asyncio
async def test_host_observations_requests_all_rows_through_post_json(
    make_client: ClientType,
) -> None:
    """Request every host observation beyond the server's default 9,999-row page.

    Args:
        make_client (ClientType): Factory for a client with a mocked HTTP session.
    """
    client, session = make_mock_session_client(make_client)
    client._post = AsyncMock(side_effect=client._do_post)
    observation = {
        "source": "discovery",
        "ether_address": "aa:bb:cc:dd:ee:01",
        "ip_address": "192.0.2.1",
        "last_seen": datetime.now(UTC).isoformat(),
    }
    observations = [observation.copy() for _ in range(10_000)]

    def search_response(*args: object, **kwargs: object) -> FakeResponse:
        """Model searchRecordsetBase rowCount handling for the host search endpoint.

        Args:
            args (object): Positional HTTP request arguments, including the URL.
            kwargs (object): Keyword HTTP request arguments, including JSON payload.

        Returns:
            FakeResponse: Search results limited by the POST body's rowCount value.
        """
        del args
        request_payload = kwargs.get("json")
        row_count = (
            request_payload.get("rowCount", 9999)
            if isinstance(request_payload, MutableMapping)
            else 9999
        )
        rows = observations if row_count == -1 else observations[:row_count]
        return FakeResponse(json_payload={"total": len(observations), "rows": rows})

    session.post.side_effect = search_response
    session.get.side_effect = search_response

    result = await client.get_host_observations()

    assert result is not None
    assert len(result["rows"]) == 10_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"total": 0, "rows": []},
        {"total": 2, "rows": [{}]},
        {"total": 1, "rows": [None]},
        {
            "total": 2,
            "rows": [
                {
                    "source": "discovery",
                    "ether_address": "aa:bb:cc:dd:ee:01",
                    "ip_address": "192.0.2.1",
                    "last_seen": "2026-01-01T00:00:00Z",
                },
                {
                    "source": "arp-ndp",
                    "ether_address": "aa:bb:cc:dd:ee:02",
                    "ip_address": "192.0.2.2",
                    "last_seen": "2026-01-01T00:00:00Z",
                },
            ],
        },
        *[
            {
                "total": 1,
                "rows": [
                    {
                        "source": "discovery",
                        "ether_address": "aa:bb:cc:dd:ee:01",
                        "ip_address": "2001:db8::1",
                        "last_seen": "2026-01-01T00:00:00Z",
                    }
                    | changes
                ],
            }
            for changes in [
                {"source": []},
                {"source": "unknown"},
                {"ether_address": "incomplete"},
                {"ether_address": None},
                {"ip_address": "invalid"},
                {"ip_address": 192},
                {"last_seen": None},
                {"last_seen": "invalid"},
                {"last_seen": "2026-01-01T00:00:00"},
                {"last_seen": "2999-01-01T00:00:00Z"},
            ]
        ],
    ],
)
async def test_host_observations_rejects_non_authoritative_payloads(
    make_client: ClientType, payload: object
) -> None:
    """Malformed or partial observations must not become successful empty scans.

    Args:
        make_client (ClientType): Factory for a client with a mocked HTTP session.
        payload (object): Unusable host-observation response.
    """
    client, session = make_mock_session_client(make_client)
    client._post = AsyncMock(side_effect=client._do_post)
    session.post.return_value = FakeResponse(json_payload=payload)
    assert await client.get_host_observations() is None


@pytest.mark.asyncio
async def test_host_observations_distinguishes_disabled_service(make_client: ClientType) -> None:
    """Hostwatch's timestamp-free fallback must not masquerade as discovery observations.

    Args:
        make_client (ClientType): Factory for a client with a mocked HTTP session.
    """
    client, session = make_mock_session_client(make_client)
    client._post = AsyncMock(side_effect=client._do_post)
    session.post.return_value = FakeResponse(
        json_payload={
            "total": 1,
            "rows": [{"source": "arp-ndp", "last_seen": ""}],
        }
    )
    assert await client.get_host_observations() == {"source": "arp-ndp", "rows": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 500])
async def test_host_observations_http_failure_is_unavailable(
    make_client: ClientType, status: int
) -> None:
    """Unavailable discovery never provides an authoritative empty observation set.

    Args:
        make_client (ClientType): Factory for a client with a mocked HTTP session.
        status (int): HTTP failure status of the endpoint probe.
    """
    client, session = make_mock_session_client(make_client)
    session.post.return_value = FakeResponse(status=status, ok=False)
    assert await client.get_host_observations() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lookup_name", "payload", "expected"),
    [
        ("arp", {"rows": []}, []),
        ("ndp", {"rows": []}, []),
        (
            "host_observations",
            {"total": 1, "rows": [{"source": "arp-ndp", "last_seen": ""}]},
            {"source": "arp-ndp", "rows": []},
        ),
    ],
)
async def test_neighbor_lookup_logs_failure_once_until_recovery_then_again(
    make_client: ClientType,
    caplog: pytest.LogCaptureFixture,
    lookup_name: Literal["arp", "ndp", "host_observations"],
    payload: object,
    expected: object,
) -> None:
    """Throttle outage logs across probes and real neighbor table requests.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        caplog (pytest.LogCaptureFixture): Captured log records for the lookup sequence.
        lookup_name (Literal["arp", "ndp", "host_observations"]): Public lookup to exercise.
        payload (object): Successful response payload for the final recovery request.
        expected (object): Public lookup result after recovery.
    """
    client, session = make_mock_session_client(make_client)
    client._endpoint_cache_ttl_seconds = 0

    async def lookup() -> object:
        """Call the selected public neighbor lookup method.

        Returns:
            object: Result returned by the selected lookup method.
        """
        if lookup_name == "arp":
            return await client.get_arp_table()
        if lookup_name == "ndp":
            return await client.get_ndp_table()
        return await client.get_host_observations()

    failed_probe = FakeResponse(status=503, reason="Unavailable", ok=False)
    successful_probe = FakeResponse()
    failed_request = FakeResponse(status=500, reason="Server Error", ok=False)
    successful_request = FakeResponse(json_payload=payload)

    if lookup_name == "host_observations":
        client._post = AsyncMock(side_effect=client._do_post)
        session.post.side_effect = [
            failed_probe,
            failed_probe,
            successful_probe,
            failed_request,
            successful_probe,
            failed_request,
            successful_probe,
            successful_request,
            successful_probe,
            failed_request,
        ]
    else:
        client._get = AsyncMock(side_effect=client._do_get)
        session.get.side_effect = [
            failed_probe,
            failed_probe,
            successful_probe,
            failed_request,
            successful_probe,
            failed_request,
            successful_probe,
            successful_request,
            successful_probe,
            failed_request,
        ]

    with caplog.at_level("DEBUG"):
        assert await lookup() is None
        assert await lookup() is None
        assert await lookup() is None
        assert await lookup() is None
        assert await lookup() == expected
        assert await lookup() is None

    probe_failures = [
        record
        for record in caplog.records
        if record.msg.startswith("Transient ") and "endpoint check failure" in record.msg
    ]
    request_failures = [
        record
        for record in caplog.records
        if record.msg.startswith(("Error in do_get", "Error in do_post"))
    ]
    probe_recoveries = [
        record for record in caplog.records if " probe recovered for " in record.getMessage()
    ]
    request_recoveries = [
        record for record in caplog.records if " request recovered for " in record.getMessage()
    ]

    assert [record.levelname for record in probe_failures] == ["WARNING", "DEBUG"]
    assert [record.levelname for record in request_failures] == ["ERROR", "DEBUG", "ERROR"]
    assert len(probe_recoveries) == 1
    assert len(request_recoveries) == 1


@pytest.mark.asyncio
async def test_neighbor_probe_failure_logging_is_per_client(
    make_client: ClientType,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep one client's repeated probe failures from muting another client.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        caplog (pytest.LogCaptureFixture): Captured records used to inspect log levels.
    """
    first_client, first_session = make_mock_session_client(make_client)
    second_client, second_session = make_mock_session_client(make_client)
    for client, session in (
        (first_client, first_session),
        (second_client, second_session),
    ):
        client._get = AsyncMock(side_effect=client._do_get)
        session.get.return_value = FakeResponse(status=503, reason="Unavailable", ok=False)

    with caplog.at_level("DEBUG"):
        assert await first_client.get_ndp_table() is None
        assert await second_client.get_ndp_table() is None
        assert await first_client.get_ndp_table() is None

    failures = [
        record
        for record in caplog.records
        if record.msg.startswith("Transient ") and "endpoint check failure" in record.msg
    ]
    assert [record.levelname for record in failures] == ["WARNING", "WARNING", "DEBUG"]


@pytest.mark.asyncio
async def test_throwing_neighbor_lookup_does_not_duplicate_request_failure_log(
    make_client: ClientType,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Avoid logging the same transport failure again in the public wrapper.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        caplog (pytest.LogCaptureFixture): Captured records used to inspect log count.
    """
    client, session = make_mock_session_client(partial(make_client, throw_errors=True))
    client._get = AsyncMock(side_effect=client._do_get)
    session.get.side_effect = [
        FakeResponse(),
        FakeResponse(status=500, reason="Server Error", ok=False),
    ]

    with caplog.at_level("DEBUG"), pytest.raises(OPNsenseConnectionError):
        await client.get_ndp_table()

    failures = [
        record
        for record in caplog.records
        if record.msg.startswith("Error in do_get")
        or record.msg.startswith("Error in get_ndp_table")
    ]
    assert len(failures) == 1
    assert failures[0].msg.startswith("Error in do_get")
