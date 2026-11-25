"""Tests for `aiopnsense.dhcp`."""

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any, Literal
import warnings

import pytest

from aiopnsense import Access, OPNsenseClient, result_access
from aiopnsense.exceptions import (
    OPNsenseConnectionError,
    OPNsensePrivilegeMissing,
    OPNsenseTimeoutError,
)
from tests.conftest import FakeResponse, FakeRouter, make_mock_session_client, make_router_client

ClientType = Callable[..., OPNsenseClient]
HOST_OBSERVATIONS_PATH = "/api/hostdiscovery/service/search"
NDP_TABLE_PATH = "/api/diagnostics/interface/search_ndp"
ARP_TABLE_RESOLVE_PATH = "/api/diagnostics/interface/search_arp?resolve=yes"

_ARP_RESOLVE_NO = "/api/diagnostics/interface/search_arp?resolve=no"
_KEA_INTERFACES = "/api/kea/dhcpv4/get"
_KEA_LEASES4 = "/api/kea/leases4/search"
_KEA_LEASES6 = "/api/kea/leases6/search"
_KEA_RESERVATIONS = "/api/kea/dhcpv4/search_reservation"
_KEA_RESERVATIONS_CAMEL = "/api/kea/dhcpv4/searchReservation"
_DNSMASQ_LEASES = "/api/dnsmasq/leases/search"
_DNSMASQ_RANGES = "/api/dnsmasq/settings/search_range"
_SYSTEM_TIME = "/api/diagnostics/system/system_time"
_ISC_LEASES4 = "/api/dhcpv4/leases/search_lease"
_ISC_LEASES4_CAMEL = "/api/dhcpv4/leases/searchLease"
_ISC_LEASES6 = "/api/dhcpv6/leases/search_lease"
_ISC_LEASES6_CAMEL = "/api/dhcpv6/leases/searchLease"
_LEASE_ROUTER_TZ = timezone(timedelta(hours=-5))
# The firmware status the Router reports decides the Endpoint Style: 25.7 is the last
# camelCase release.
_SNAKE_CASE_FIRMWARE = "26.1.2"
_CAMEL_CASE_FIRMWARE = "25.1.1"


def _dhcp_router(
    make_client: ClientType,
    routes: Mapping[str, object] | None = None,
    *,
    throw_errors: bool = False,
    firmware: str = _SNAKE_CASE_FIRMWARE,
) -> tuple[OPNsenseClient, FakeRouter]:
    """Build a client against a Router that serves only the given GET routes.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        routes (Mapping[str, object] | None): Response served for each GET path. Every
            other request answers 404, so that DHCP backend looks not installed.
        throw_errors (bool): Whether the client raises public errors.
        firmware (str): Firmware version the Router reports.

    Returns:
        tuple[OPNsenseClient, FakeRouter]: The client and the Router recording its requests.
    """
    return make_router_client(
        make_client,
        {("GET", path): response for path, response in (routes or {}).items()},
        firmware=firmware,
        throw_errors=throw_errors,
    )


async def _get_leases(client: OPNsenseClient) -> dict[str, Any]:
    """Return ``get_dhcp_leases`` with a fixed Router timezone.

    Args:
        client (OPNsenseClient): Client backed by a fake Router.

    Returns:
        dict[str, Any]: The ``get_dhcp_leases`` result.
    """
    return await client.get_dhcp_leases(opnsense_tz=_LEASE_ROUTER_TZ)


def _leases_by_address(result: dict[str, Any], if_name: str = "lan") -> dict[str, dict[str, Any]]:
    """Return the leases of one interface keyed by address.

    Args:
        result (dict[str, Any]): ``get_dhcp_leases`` result.
        if_name (str): Interface identifier to read.

    Returns:
        dict[str, dict[str, Any]]: Leases keyed by address.
    """
    return {lease["address"]: lease for lease in result["leases"].get(if_name, [])}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "status", "expected"),
    [
        ({"rows": []}, 200, []),
        (
            {
                "rows": [
                    {"mac": "aa:bb:cc:dd:ee:ff", "ip": "2001:db8::1", "intf": "em0"},
                    {"mac": "aa:bb:cc:dd:ee:ff", "ip": "fe80::1%em0", "intf": "em0"},
                ]
            },
            200,
            [
                {"mac": "aa:bb:cc:dd:ee:ff", "ip": "2001:db8::1", "intf": "em0"},
                {"mac": "aa:bb:cc:dd:ee:ff", "ip": "fe80::1%em0", "intf": "em0"},
            ],
        ),
        ({}, 200, None),
        ({"rows": None}, 200, None),
        ({"rows": {}}, 200, None),
        ([], 200, None),
        (None, 403, None),
        (None, 404, None),
        (None, 500, None),
        (TimeoutError("NDP request timed out"), 200, None),
    ],
)
async def test_get_ndp_table_http_results(
    make_client: ClientType,
    payload: object,
    status: int,
    expected: list[dict[str, str]] | None,
) -> None:
    """Preserve IPv6 neighbor rows and distinguish absence from failed discovery.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        payload (object): JSON response or simulated transport error.
        status (int): HTTP status of the table lookup.
        expected (list[dict[str, str]] | None): Public inventory result.
    """
    response = FakeResponse(status=status, ok=status == 200, json_payload=payload)
    result = payload if isinstance(payload, TimeoutError) else response
    client, router = make_router_client(
        make_client, routes={("GET", "/api/diagnostics/interface/search_ndp"): result}
    )

    assert await client.get_ndp_table() == expected  # type: ignore[deprecated]
    assert router.count("GET", "/api/diagnostics/interface/search_ndp") == 1


@pytest.mark.asyncio
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
    status: int,
    timeout: bool,
    expected_error: type[OPNsenseConnectionError],
) -> None:
    """Throwing clients expose public errors from the NDP request.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        status (int): Response status for simulated HTTP failures.
        timeout (bool): Whether to simulate a transport timeout instead of a response.
        expected_error (type[OPNsenseConnectionError]): Expected public exception type.
    """
    result = TimeoutError("NDP timed out") if timeout else FakeResponse(status=status, ok=False)
    client, _router = make_router_client(
        make_client,
        routes={("GET", "/api/diagnostics/interface/search_ndp"): result},
        throw_errors=True,
    )

    with pytest.raises(expected_error) as raised:
        await client.get_ndp_table()  # type: ignore[deprecated]

    assert type(raised.value) is expected_error
    assert raised.value.status == (None if timeout else status)


@pytest.mark.asyncio
async def test_get_kea_leases_accepts_integer_active_state(make_client: ClientType) -> None:
    """Kea active leases should parse when OPNsense returns integer state 0.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = _dhcp_router(
        make_client,
        {
            _KEA_LEASES4: {
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
            _KEA_RESERVATIONS: {"rows": []},
        },
    )

    result = await _get_leases(client)

    assert result["leases"] == {
        "em0": [
            {
                "address": "192.0.2.10",
                "hostname": "host",
                "type": "dynamic",
                "mac": "aa:bb:cc:dd:ee:ff",
                "expires": None,
            }
        ]
    }


@pytest.mark.asyncio
async def test_get_arp_table_uses_get_query_param(make_client: ClientType) -> None:
    """Verify ARP table lookup uses GET endpoint with the expected query parameter.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _dhcp_router(
        make_client,
        {ARP_TABLE_RESOLVE_PATH: {"rows": []}, _ARP_RESOLVE_NO: {"rows": []}},
    )

    assert await client.get_arp_table(resolve_hostnames=True) == []  # type: ignore[deprecated]
    assert await client.get_arp_table(resolve_hostnames=False) == []  # type: ignore[deprecated]

    assert router.count("GET", ARP_TABLE_RESOLVE_PATH) == 1
    assert router.count("GET", _ARP_RESOLVE_NO) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"rows": []}, []),
        ({"rows": [{"mac": "aa:bb:cc:dd:ee:ff"}]}, [{"mac": "aa:bb:cc:dd:ee:ff"}]),
        ({}, None),
        ({"rows": None}, None),
        ({"rows": {}}, None),
        pytest.param(TimeoutError("ARP request timed out"), None, id="request-timeout"),
    ],
)
async def test_get_arp_table_distinguishes_empty_and_failed_lookups(
    make_client: ClientType,
    response: dict[str, Any] | TimeoutError,
    expected: list[dict[str, str]] | None,
) -> None:
    """Only a successful response with list rows is an authoritative inventory.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        response (dict[str, Any] | TimeoutError): Endpoint payload or transport failure.
        expected (list[dict[str, str]] | None): Public result for the lookup.
    """
    client, router = _dhcp_router(make_client, {ARP_TABLE_RESOLVE_PATH: response})

    assert await client.get_arp_table(resolve_hostnames=True) == expected  # type: ignore[deprecated]
    assert router.count("GET", ARP_TABLE_RESOLVE_PATH) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("throw_errors", [False, True], ids=["quiet", "throwing"])
async def test_dhcp_endpoint_unavailable(make_client: ClientType, throw_errors: bool) -> None:
    """A Router without any DHCP backend yields no leases in either error mode.

    Every lease and interface endpoint answers 404, which is absent in both modes, and
    each is requested once.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Whether the client raises public errors.
    """
    client, router = _dhcp_router(make_client, throw_errors=throw_errors)

    assert await _get_leases(client) == {"lease_interfaces": {}, "leases": {}}

    for endpoint in (
        _KEA_LEASES4,
        _KEA_LEASES6,
        _DNSMASQ_LEASES,
        _ISC_LEASES4,
        _ISC_LEASES6,
        _KEA_INTERFACES,
        _DNSMASQ_RANGES,
    ):
        assert router.count("GET", endpoint) == 1, endpoint


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
async def test_get_dhcp_leases_combined_structure(make_client: ClientType) -> None:
    """Verify combined DHCP leases payload includes all expected source data.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _dhcp_router(
        make_client,
        {
            _KEA_INTERFACES: {
                "dhcpv4": {
                    "general": {
                        "enabled": "1",
                        "interfaces": {"em0": {"selected": 1, "value": "eth0"}},
                    }
                }
            },
            _KEA_LEASES4: {
                "rows": [
                    {
                        "if_name": "em0",
                        "address": "1.1.1.1",
                        "hwaddr": "m1",
                        "state": "0",
                        "is_reserved": "0",
                    }
                ]
            },
            _ISC_LEASES4: {
                "rows": [{"if": "em0", "address": "1.1.1.2", "mac": "m2", "state": "active"}]
            },
            _KEA_LEASES6: {
                "rows": [
                    {
                        "if_name": "em0",
                        "address": "2001:db8::1",
                        "hwaddr": "m6",
                        "state": "0",
                        "is_reserved": "0",
                    }
                ]
            },
        },
    )

    combined = await client.get_dhcp_leases()

    assert combined["lease_interfaces"] == {"em0": None}
    assert {(lease["address"], lease["mac"]) for lease in combined["leases"]["em0"]} == {
        ("1.1.1.1", "m1"),
        ("1.1.1.2", "m2"),
        ("2001:db8::1", "m6"),
    }
    for endpoint in (_KEA_INTERFACES, _KEA_LEASES4, _KEA_LEASES6, _ISC_LEASES4, _ISC_LEASES6):
        assert router.count("GET", endpoint) == 1
    assert router.count("GET", _DNSMASQ_LEASES) == 1
    assert router.count("GET", _DNSMASQ_RANGES) == 1
    # Without an explicit timezone the client reads it from the Router.
    assert router.count("GET", _SYSTEM_TIME) == 1


@pytest.mark.asyncio
async def test_get_dhcp_leases_reads_router_timezone_when_omitted(
    make_client: ClientType,
) -> None:
    """Without a timezone argument ISC ``ends`` is localized in the Router's timezone.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
    """
    router_tz = timezone(timedelta(hours=5, minutes=17))
    ends_at = (datetime.now(tz=router_tz) + timedelta(hours=1)).replace(microsecond=0)
    client, _router = _dhcp_router(
        make_client,
        {
            _SYSTEM_TIME: {"datetime": "2000-01-15 12:00:00 +0517"},
            _ISC_LEASES4: {
                "rows": [
                    {
                        "state": "active",
                        "if": "lan",
                        "if_descr": "LAN",
                        "type": "dynamic",
                        "address": "192.0.2.1",
                        "mac": "cc:00:00:00:00:01",
                        "ends": ends_at.strftime("%Y/%m/%d %H:%M:%S"),
                    }
                ]
            },
        },
    )

    result = await client.get_dhcp_leases()

    assert _leases_by_address(result)["192.0.2.1"]["expires"] == ends_at


@pytest.mark.asyncio
async def test_get_dhcp_leases_includes_empty_dnsmasq_range_interface(
    make_client: ClientType,
) -> None:
    """Configured Dnsmasq ranges should remain discoverable without active leases.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _dhcp_router(
        make_client,
        {
            _DNSMASQ_RANGES: {
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
        },
    )

    combined = await _get_leases(client)

    assert combined == {
        "lease_interfaces": {"opt8": "Guest_150"},
        "leases": {},
    }
    assert router.count("GET", _DNSMASQ_RANGES) == 1


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
    """
    client, _router = _dhcp_router(make_client, {_DNSMASQ_RANGES: response})

    assert (await _get_leases(client))["lease_interfaces"] == expected


@pytest.mark.asyncio
async def test_get_kea_interfaces_filters_enabled_and_selected(make_client: ClientType) -> None:
    """Verify Kea interface discovery honors enabled and selected/value constraints.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _dhcp_router(
        make_client, {_KEA_INTERFACES: {"dhcpv4": {"general": {"enabled": "0"}}}}
    )

    assert (await _get_leases(client))["lease_interfaces"] == {}

    router.routes[("GET", _KEA_INTERFACES)] = {
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

    assert (await _get_leases(client))["lease_interfaces"] == {"em0": "LAN", "em4": "OPT"}
    assert router.count("GET", _KEA_INTERFACES) == 2


@pytest.mark.asyncio
async def test_get_kea_dhcpv4_leases_covers_invalid_dynamic_and_reservations(
    make_client: ClientType,
) -> None:
    """Verify Kea DHCPv4 leases skip invalid rows and classify static/dynamic entries.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    future_ts = int(datetime.now(tz=UTC).timestamp()) + 3600
    past_ts = int(datetime.now(tz=UTC).timestamp()) - 3600
    client, router = _dhcp_router(
        make_client,
        {
            _KEA_LEASES4: {
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
            _KEA_RESERVATIONS: {
                "rows": [
                    None,
                    {"ip_address": "192.0.2.13"},
                    {"hw_address": "aa:bb", "ip_address": "192.0.2.13"},
                ]
            },
        },
    )

    leases = (await _get_leases(client))["leases"]

    assert set(leases) == {"em0", "em1"}
    assert [(lease["address"], lease["type"]) for lease in leases["em0"]] == [
        ("192.0.2.11", "dynamic")
    ]
    assert len(leases["em1"]) == 1
    assert leases["em1"][0]["address"] == "192.0.2.13"
    assert leases["em1"][0]["type"] == "static"
    assert leases["em1"][0]["client_id"] == "01:aa:bb"
    assert leases["em1"][0]["reserved_by"] == ["client_id"]

    # Reservation lookup failures should not misclassify entries as dynamic/static.
    router.routes[("GET", _KEA_LEASES4)] = {
        "rows": [{"state": "0", "hwaddr": "aa", "address": "10.0.0.1", "if_name": "em0"}]
    }
    router.routes[("GET", _KEA_RESERVATIONS)] = {"rows": "bad"}

    leases = (await _get_leases(client))["leases"]

    assert [(lease["address"], lease["type"]) for lease in leases["em0"]] == [
        ("10.0.0.1", "unknown")
    ]


@pytest.mark.asyncio
async def test_get_kea_dhcpv4_leases_skips_redundant_reservation_lookup(
    make_client: ClientType,
) -> None:
    """Use lease reservation metadata without collecting the reservation table.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    base = {"state": "0", "if_name": "lan"}
    client, router = _dhcp_router(
        make_client,
        {
            _KEA_LEASES4: {
                "rows": [
                    {
                        **base,
                        "hwaddr": "aa:bb:cc:dd:ee:01",
                        "address": "192.0.2.10",
                        "is_reserved": [],
                    },
                    {
                        **base,
                        "hwaddr": "aa:bb:cc:dd:ee:02",
                        "address": "192.0.2.11",
                        "is_reserved": ["hwaddr"],
                    },
                    {
                        **base,
                        "hwaddr": "aa:bb:cc:dd:ee:03",
                        "address": "192.0.2.12",
                        "is_reserved": False,
                    },
                    {
                        **base,
                        "hwaddr": "aa:bb:cc:dd:ee:04",
                        "address": "192.0.2.13",
                        "is_reserved": True,
                    },
                ]
            }
        },
    )

    leases = (await _get_leases(client))["leases"]["lan"]

    assert [lease["type"] for lease in leases] == ["dynamic", "static", "dynamic", "static"]
    assert router.count("GET", _KEA_LEASES4) == 1
    assert router.count("GET", _KEA_RESERVATIONS) == 0


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
    """
    client, router = _dhcp_router(
        make_client,
        {
            _KEA_LEASES4: {
                "rows": [
                    {
                        "state": "0",
                        "hwaddr": "aa:bb:cc:dd:ee:01",
                        "address": "192.0.2.10",
                        "if_name": "lan",
                        "is_reserved": is_reserved,
                    }
                ]
            },
            _KEA_RESERVATIONS: {
                "rows": [{"hw_address": "aa:bb:cc:dd:ee:01", "ip_address": "192.0.2.10"}]
            },
        },
    )

    leases = (await _get_leases(client))["leases"]["lan"]

    assert leases[0]["type"] == "static"
    assert router.count("GET", _KEA_RESERVATIONS) == 1


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
    """
    client, router = _dhcp_router(
        make_client,
        {
            _KEA_LEASES4: {
                "rows": [
                    {
                        "state": "0",
                        "hwaddr": "aa:bb:cc:dd:ee:01",
                        "address": "192.0.2.10",
                        "if_name": "lan",
                        "is_reserved": {"unexpected": "truthy"},
                    }
                ]
            },
            _KEA_RESERVATIONS: {"rows": reservation_rows},
        },
    )

    leases = (await _get_leases(client))["leases"]["lan"]

    assert leases[0]["type"] == "dynamic"
    assert router.count("GET", _KEA_RESERVATIONS) == 1


@pytest.mark.asyncio
async def test_get_kea_dhcpv4_leases_ignores_expired_rows_for_reservation_lookup(
    make_client: ClientType,
) -> None:
    """Avoid reservation lookup when only an expired row lacks metadata.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    past_ts = int(datetime.now(tz=UTC).timestamp()) - 3600
    base = {"state": "0", "if_name": "lan"}
    client, router = _dhcp_router(
        make_client,
        {
            _KEA_LEASES4: {
                "rows": [
                    {
                        **base,
                        "hwaddr": "aa:bb:cc:dd:ee:01",
                        "address": "192.0.2.10",
                        "expire": past_ts,
                    },
                    {
                        **base,
                        "hwaddr": "aa:bb:cc:dd:ee:02",
                        "address": "192.0.2.11",
                        "is_reserved": 0,
                    },
                    {
                        **base,
                        "hwaddr": "aa:bb:cc:dd:ee:03",
                        "address": "192.0.2.12",
                        "is_reserved": ["hwaddr"],
                    },
                ]
            }
        },
    )

    leases = (await _get_leases(client))["leases"]["lan"]

    assert [lease["type"] for lease in leases] == ["dynamic", "static"]
    assert router.count("GET", _KEA_LEASES4) == 1
    assert router.count("GET", _KEA_RESERVATIONS) == 0


@pytest.mark.asyncio
async def test_get_kea_dhcpv6_leases_accepts_duid_only_rows(make_client: ClientType) -> None:
    """Verify Kea DHCPv6 leases parse the OPNsense DUID-oriented row shape.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    future_ts = int(datetime.now(tz=UTC).timestamp()) + 3600
    client, router = _dhcp_router(
        make_client,
        {
            _KEA_LEASES6: {
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
        },
    )

    leases = _leases_by_address(await _get_leases(client), "em0")

    assert len(leases) == 3
    first = leases["2001:db8::10"]
    assert first["type"] == "static"
    assert first["hostname"] == "host6"
    assert first["duid"] == "00:01:00:01:aa:bb"
    assert first["client_id"] == "00:01:00:01:aa:bb"
    assert first["reserved_by"] == ["duid"]
    assert first["mac"] is None
    second = leases["2001:db8::11"]
    assert second["type"] == "dynamic"
    assert second["duid"] == "00:01:00:01:cc:dd"
    assert second["hostname"] == "skip"
    assert second["mac"] is None
    third = leases["2001:db8::12"]
    assert third["type"] == "dynamic"
    assert third["duid"] == "00:01:00:01:ee:ff"
    assert third["mac"] is None
    assert router.count("GET", _KEA_LEASES6) == 1


@pytest.mark.asyncio
async def test_get_kea_dhcpv6_leases_preserves_unknown_reservation_metadata(
    make_client: ClientType,
) -> None:
    """Keep unsupported Kea DHCPv6 reservation metadata unclassified.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    base = {"state": 0, "if_name": "lan"}
    client, router = _dhcp_router(
        make_client,
        {
            _KEA_LEASES6: {
                "rows": [
                    {**base, "address": "2001:db8::10", "duid": "missing"},
                    {**base, "address": "2001:db8::11", "duid": "none", "is_reserved": None},
                    {
                        **base,
                        "address": "2001:db8::12",
                        "duid": "mapping",
                        "is_reserved": {"unexpected": "value"},
                    },
                    {**base, "address": "2001:db8::13", "duid": "false", "is_reserved": False},
                ]
            }
        },
    )

    leases = _leases_by_address(await _get_leases(client))

    assert {address: lease["type"] for address, lease in leases.items()} == {
        "2001:db8::10": "unknown",
        "2001:db8::11": "unknown",
        "2001:db8::12": "unknown",
        "2001:db8::13": "dynamic",
    }
    assert router.count("GET", _KEA_LEASES6) == 1


@pytest.mark.asyncio
async def test_get_dnsmasq_leases_invalid_rows_and_expiry_paths(make_client: ClientType) -> None:
    """Verify dnsmasq lease parsing handles malformed and expired rows safely.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    past_ts = int(datetime.now(tz=UTC).timestamp()) - 3600
    client, router = _dhcp_router(
        make_client,
        {
            _DNSMASQ_LEASES: {
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
        },
    )

    result = await _get_leases(client)

    em0 = _leases_by_address(result, "em0")
    lan = _leases_by_address(result, "lan")
    assert set(result["leases"]) == {"em0", "lan"}
    assert set(em0) == {"192.0.2.20", "192.0.2.24"}
    assert set(lan) == {"192.0.2.22", "192.0.2.23"}
    assert em0["192.0.2.20"]["type"] == "dynamic"
    assert em0["192.0.2.24"]["type"] == "dynamic"
    assert lan["192.0.2.22"]["type"] == "dynamic"
    assert lan["192.0.2.22"]["client_id"] == "01:11:22"
    assert lan["192.0.2.22"]["mac"] is None
    assert lan["192.0.2.22"]["expires"] == "never"
    assert lan["192.0.2.23"]["type"] == "static"
    assert lan["192.0.2.23"]["reserved_by"] == ["hwaddr"]
    assert lan["192.0.2.23"]["client_id"] == "01:33:44"
    assert router.count("GET", _DNSMASQ_LEASES) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        (_KEA_LEASES4, {"rows": "bad"}),
        (_DNSMASQ_LEASES, {"rows": "bad-shape"}),
        (_ISC_LEASES4, {"rows": "bad"}),
        (_ISC_LEASES6, {"rows": "bad"}),
    ],
    ids=["kea-dhcpv4", "dnsmasq", "isc-dhcpv4", "isc-dhcpv6"],
)
async def test_get_dhcp_leases_ignores_non_list_lease_rows(
    make_client: ClientType, endpoint: str, payload: dict[str, str]
) -> None:
    """A lease response whose rows are not a list contributes no leases.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        endpoint (str): Lease endpoint that answers with a malformed body.
        payload (dict[str, str]): Malformed lease response.
    """
    client, router = _dhcp_router(make_client, {endpoint: payload})

    assert await _get_leases(client) == {"lease_interfaces": {}, "leases": {}}

    assert router.count("GET", endpoint) == 1


@pytest.mark.asyncio
async def test_version_switched_dhcp_endpoints_skip_unneeded_kea_reservations(
    make_client: ClientType,
) -> None:
    """Skip the Kea reservation lookup when the lease table has no rows.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _dhcp_router(make_client, {_KEA_LEASES4: {"rows": []}})

    assert await _get_leases(client) == {"lease_interfaces": {}, "leases": {}}

    assert router.count("GET", _KEA_LEASES4) == 1
    assert router.count("GET", _KEA_RESERVATIONS) == 0


@pytest.mark.asyncio
async def test_version_switched_kea_dhcpv4_returns_leases_when_reservation_unavailable(
    make_client: ClientType,
) -> None:
    """Verify Kea returns unknown lease type when reservation endpoint is unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _dhcp_router(
        make_client,
        {
            _KEA_LEASES4: {
                "rows": [
                    {
                        "state": "0",
                        "hwaddr": "aa:bb:cc:dd:ee:ff",
                        "address": "192.0.2.10",
                        "hostname": "host-a.",
                        "if_name": "lan",
                    }
                ]
            }
        },
    )

    leases = (await _get_leases(client))["leases"]["lan"]

    assert len(leases) == 1
    assert leases[0].get("mac") == "aa:bb:cc:dd:ee:ff"
    assert leases[0].get("type") == "unknown"
    assert router.count("GET", _KEA_LEASES4) == 1
    assert router.count("GET", _KEA_RESERVATIONS) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("firmware", "used", "unused"),
    [
        pytest.param(
            _SNAKE_CASE_FIRMWARE,
            (_KEA_RESERVATIONS, _ISC_LEASES4, _ISC_LEASES6),
            (_KEA_RESERVATIONS_CAMEL, _ISC_LEASES4_CAMEL, _ISC_LEASES6_CAMEL),
            id="snake_case",
        ),
        pytest.param(
            _CAMEL_CASE_FIRMWARE,
            (_KEA_RESERVATIONS_CAMEL, _ISC_LEASES4_CAMEL, _ISC_LEASES6_CAMEL),
            (_KEA_RESERVATIONS, _ISC_LEASES4, _ISC_LEASES6),
            id="camelCase",
        ),
    ],
)
async def test_dhcp_switched_endpoints_follow_selected_case(
    make_client: ClientType,
    firmware: str,
    used: tuple[str, str, str],
    unused: tuple[str, str, str],
) -> None:
    """Verify DHCP reads choose snake_case or camelCase endpoints from the Router firmware.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        firmware (str): Firmware version the Router reports.
        used (tuple[str, str, str]): Reservation, ISC DHCPv4 and ISC DHCPv6 paths the client
            should request.
        unused (tuple[str, str, str]): Paths of the other endpoint style, which must not be
            requested.
    """
    client, router = _dhcp_router(
        make_client,
        {
            _KEA_LEASES4: {
                "rows": [
                    {
                        "state": "0",
                        "hwaddr": "aa:bb:cc:dd:ee:ff",
                        "address": "192.0.2.10",
                        "if_name": "lan",
                    }
                ]
            },
            **{path: {"rows": []} for path in used},
        },
        firmware=firmware,
    )

    await _get_leases(client)

    assert [router.count("GET", path) for path in used] == [1, 1, 1]
    assert [router.count("GET", path) for path in unused] == [0, 0, 0]


@pytest.mark.asyncio
async def test_version_switched_get_arp_table_endpoint_unavailable(
    make_client: ClientType,
) -> None:
    """Verify an unavailable ARP endpoint is not an authoritative empty table.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _dhcp_router(make_client, {ARP_TABLE_RESOLVE_PATH: {"rows": []}})

    assert await client.get_arp_table(resolve_hostnames=True) == []  # type: ignore[deprecated]
    assert await client.get_arp_table(resolve_hostnames=False) is None  # type: ignore[deprecated]

    assert router.count("GET", ARP_TABLE_RESOLVE_PATH) == 1
    assert router.count("GET", _ARP_RESOLVE_NO) == 1


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
    client, router = make_router_client(
        make_client,
        routes={
            ("POST", HOST_OBSERVATIONS_PATH): {
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
        },
    )
    with pytest.warns(DeprecationWarning, match="Use get_host_observation_entries"):
        result = await client.get_host_observations()  # type: ignore[deprecated]
    assert result is not None
    assert result["source"] == "discovery"
    assert result["rows"][0]["mac"] == "aa:bb:cc:dd:ee:01"
    assert result["rows"][0]["last_seen"] == datetime(2026, 1, 1, tzinfo=UTC)
    assert result["rows"][0]["ip"] == family_ip.replace("0db8", "db8")
    assert router.count("POST", HOST_OBSERVATIONS_PATH) == 1


@pytest.mark.asyncio
async def test_host_observation_entries_returns_typed_result_with_one_fetch(
    make_client: ClientType,
) -> None:
    """Return frozen typed observations from one request to the discovery endpoint.

    Args:
        make_client (ClientType): Factory for a client with a mocked HTTP session.
    """
    client, router = make_router_client(
        make_client,
        routes={
            ("POST", HOST_OBSERVATIONS_PATH): {
                "total": 1,
                "rows": [
                    {
                        "source": "discovery",
                        "ether_address": "AA-BB-CC-DD-EE-01",
                        "ip_address": "2001:0db8::1",
                        "last_seen": "2026-01-01T02:00:00+02:00",
                    }
                ],
            }
        },
    )

    with warnings.catch_warnings(record=True) as emitted_warnings:
        warnings.simplefilter("always", DeprecationWarning)
        observations = await client.get_host_observation_entries()

    assert not any(
        "Use get_host_observation_entries" in str(warning.message) for warning in emitted_warnings
    )
    assert isinstance(observations, OPNsenseClient.HostObservations)
    assert observations.source == "discovery"
    assert observations.entries == (
        OPNsenseClient.HostObservation(
            mac="aa:bb:cc:dd:ee:01",
            ip="2001:db8::1",
            last_seen=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    with pytest.raises(FrozenInstanceError):
        observations.entries[0].ip = "192.0.2.1"  # type: ignore[misc]
    assert router.requests == [("POST", HOST_OBSERVATIONS_PATH, {"rowCount": -1})]


@pytest.mark.asyncio
async def test_host_observation_entries_preserves_disabled_source(
    make_client: ClientType,
) -> None:
    """Represent disabled host discovery as an available empty typed result.

    Args:
        make_client (ClientType): Factory for a client with a mocked HTTP session.
    """
    client, router = make_router_client(
        make_client,
        routes={
            ("POST", HOST_OBSERVATIONS_PATH): {
                "total": 1,
                "rows": [{"source": "arp-ndp", "last_seen": ""}],
            }
        },
    )

    observations = await client.get_host_observation_entries()

    assert observations == OPNsenseClient.HostObservations(source="arp-ndp", entries=())
    assert router.count("POST", HOST_OBSERVATIONS_PATH) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("throw_errors", "payload", "status", "expected_error"),
    [
        (False, {"total": 1, "rows": [{}]}, 200, None),
        (False, None, 404, None),
        (False, None, 403, None),
        (True, None, 403, OPNsensePrivilegeMissing),
    ],
)
async def test_host_observation_entries_preserves_failure_semantics(
    make_client: ClientType,
    throw_errors: bool,
    payload: object,
    status: int,
    expected_error: type[OPNsensePrivilegeMissing] | None,
) -> None:
    """Keep unavailable and malformed results distinct from throwing-client errors.

    Args:
        make_client (ClientType): Factory for a client with a mocked HTTP session.
        throw_errors (bool): Whether the client should raise public API failures.
        payload (object): JSON response for a successful request.
        status (int): Response status of the endpoint request.
        expected_error (type[OPNsensePrivilegeMissing] | None): Expected mapped error.
    """
    client, _router = make_router_client(
        make_client,
        routes={("POST", HOST_OBSERVATIONS_PATH): payload if status == 200 else status},
        throw_errors=throw_errors,
    )

    if expected_error is not None:
        with pytest.raises(expected_error):
            await client.get_host_observation_entries()
    else:
        assert await client.get_host_observation_entries() is None


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
    client, _router = make_router_client(
        make_client,
        routes={
            ("POST", HOST_OBSERVATIONS_PATH): {
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
        },
    )

    assert await client.get_host_observations() is None  # type: ignore[deprecated]


@pytest.mark.asyncio
async def test_host_observations_requests_all_rows_through_post_json(
    make_client: ClientType,
) -> None:
    """Request every host observation beyond the server's default 9,999-row page.

    Args:
        make_client (ClientType): Factory for a client with a mocked HTTP session.
    """
    observation = {
        "source": "discovery",
        "ether_address": "aa:bb:cc:dd:ee:01",
        "ip_address": "192.0.2.1",
        "last_seen": datetime.now(UTC).isoformat(),
    }
    observations = [observation.copy() for _ in range(10_000)]
    client, router = make_router_client(
        make_client,
        routes={
            ("POST", HOST_OBSERVATIONS_PATH): {"total": len(observations), "rows": observations}
        },
    )

    result = await client.get_host_observations()  # type: ignore[deprecated]

    assert result is not None
    assert len(result["rows"]) == 10_000
    assert router.requests == [("POST", HOST_OBSERVATIONS_PATH, {"rowCount": -1})]


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
                {"ether_address": "aa:bb:cc:dd:ee:gg"},
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
    client, _router = make_router_client(
        make_client, routes={("POST", HOST_OBSERVATIONS_PATH): payload}
    )
    assert await client.get_host_observations() is None  # type: ignore[deprecated]


@pytest.mark.asyncio
async def test_host_observations_distinguishes_disabled_service(make_client: ClientType) -> None:
    """Hostwatch's timestamp-free fallback must not masquerade as discovery observations.

    Args:
        make_client (ClientType): Factory for a client with a mocked HTTP session.
    """
    client, _router = make_router_client(
        make_client,
        routes={
            ("POST", HOST_OBSERVATIONS_PATH): {
                "total": 1,
                "rows": [{"source": "arp-ndp", "last_seen": ""}],
            }
        },
    )
    assert await client.get_host_observations() == {"source": "arp-ndp", "rows": []}  # type: ignore[deprecated]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 500])
async def test_host_observations_http_failure_is_unavailable(
    make_client: ClientType, status: int
) -> None:
    """Unavailable discovery never provides an authoritative empty observation set.

    Args:
        make_client (ClientType): Factory for a client with a mocked HTTP session.
        status (int): HTTP failure status of the endpoint request.
    """
    client, _router = make_router_client(
        make_client, routes={("POST", HOST_OBSERVATIONS_PATH): status}
    )
    assert await client.get_host_observations() is None  # type: ignore[deprecated]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lookup_name", "route", "payload", "expected"),
    [
        ("arp", ("GET", "/api/diagnostics/interface/search_arp?resolve=no"), {"rows": []}, []),
        ("ndp", ("GET", NDP_TABLE_PATH), {"rows": []}, []),
        (
            "host_observations",
            ("POST", HOST_OBSERVATIONS_PATH),
            {"total": 1, "rows": [{"source": "arp-ndp", "last_seen": ""}]},
            {"source": "arp-ndp", "rows": []},
        ),
    ],
)
async def test_neighbor_lookup_logs_failure_once_until_recovery_then_again(
    make_client: ClientType,
    caplog: pytest.LogCaptureFixture,
    lookup_name: Literal["arp", "ndp", "host_observations"],
    route: tuple[str, str],
    payload: object,
    expected: object,
) -> None:
    """Throttle outage logs to one error per outage and log each recovery once.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        caplog (pytest.LogCaptureFixture): Captured log records for the lookup sequence.
        lookup_name (Literal["arp", "ndp", "host_observations"]): Public lookup to exercise.
        route (tuple[str, str]): HTTP method and path of the one data request.
        payload (object): Successful response payload for the recovery requests.
        expected (object): Public lookup result after recovery.
    """
    client, router = make_router_client(make_client, routes={route: 503})

    async def lookup() -> object:
        """Call the selected public neighbor lookup method.

        Returns:
            object: Result returned by the selected lookup method.
        """
        if lookup_name == "arp":
            return await client.get_arp_table()  # type: ignore[deprecated]
        if lookup_name == "ndp":
            return await client.get_ndp_table()  # type: ignore[deprecated]
        return await client.get_host_observations()  # type: ignore[deprecated]

    with caplog.at_level("DEBUG"):
        assert await lookup() is None
        assert await lookup() is None
        router.routes[route] = payload
        assert await lookup() == expected
        router.routes[route] = 500
        assert await lookup() is None
        router.routes[route] = payload
        assert await lookup() == expected
        router.routes[route] = 500
        assert await lookup() is None

    failures = [
        record
        for record in caplog.records
        if record.getMessage().startswith(("Error in do_get_outcome", "Error in do_post_outcome"))
    ]
    recoveries = [
        record for record in caplog.records if " request recovered for " in record.getMessage()
    ]
    assert [record.levelname for record in failures] == ["ERROR", "DEBUG", "ERROR", "ERROR"]
    assert [record.levelname for record in recoveries] == ["INFO", "INFO"]


@pytest.mark.asyncio
async def test_neighbor_outage_logging_is_per_client(
    make_client: ClientType,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep one client's repeated outage logs from muting another client.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        caplog (pytest.LogCaptureFixture): Captured records used to inspect log levels.
    """
    first_client, _first_router = make_router_client(make_client, {("GET", NDP_TABLE_PATH): 503})
    second_client, _second_router = make_router_client(make_client, {("GET", NDP_TABLE_PATH): 503})

    with caplog.at_level("DEBUG"):
        assert await first_client.get_ndp_table() is None  # type: ignore[deprecated]
        assert await second_client.get_ndp_table() is None  # type: ignore[deprecated]
        assert await first_client.get_ndp_table() is None  # type: ignore[deprecated]

    failures = [
        record
        for record in caplog.records
        if record.getMessage().startswith("Error in do_get_outcome")
    ]
    assert [record.levelname for record in failures] == ["ERROR", "ERROR", "DEBUG"]


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
    client, _router = make_router_client(
        make_client,
        routes={("GET", NDP_TABLE_PATH): FakeResponse(status=500, reason="Server Error", ok=False)},
        throw_errors=True,
    )

    with caplog.at_level("DEBUG"), pytest.raises(OPNsenseConnectionError):
        await client.get_ndp_table()  # type: ignore[deprecated]

    failures = [
        record
        for record in caplog.records
        if record.getMessage().startswith(("Error in do_get", "Error in get_ndp_table"))
    ]
    assert len(failures) == 1
    assert failures[0].getMessage().startswith("Error in do_get")


async def _fetch_entries(
    make_client: ClientType,
    family: Literal["arp", "ndp"],
    payload: object,
    **client_options: Any,
) -> OPNsenseClient.ArpNdpTable | None:
    """Fetch a normalized table through the HTTP-like client seam.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        family (Literal["arp", "ndp"]): Neighbor table family to fetch.
        payload (object): JSON response envelope for the table request.
        **client_options (Any): Options passed to the client factory.

    Returns:
        OPNsenseClient.ArpNdpTable | None: Normalized table.
    """
    path = ARP_TABLE_RESOLVE_PATH if family == "arp" else NDP_TABLE_PATH
    client, _router = make_router_client(make_client, {("POST", path): payload}, **client_options)
    if family == "arp":
        return await client.get_arp_entries(resolve_hostnames=True)
    return await client.get_ndp_entries()


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["arp", "ndp"])
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"rows": None},
        {"rows": {}},
        {"rows": []},
        {"total": True, "rows": []},
        {"total": -1, "rows": []},
        {"total": "0", "rows": []},
        {"total": 2, "rows": [{"mac": "aa:bb:cc:dd:ee:ff"}]},
        [],
    ],
)
async def test_neighbor_entries_unavailable_or_malformed_outer_payload(
    make_client: ClientType, family: Literal["arp", "ndp"], payload: object
) -> None:
    """Reject malformed or truncated envelopes instead of returning partial inventories.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        family (Literal["arp", "ndp"]): Neighbor table family.
        payload (object): Malformed table response.
    """
    table = await _fetch_entries(make_client, family, payload)
    assert table is None


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["arp", "ndp"])
async def test_neighbor_entries_unavailable_endpoint_and_empty_table(
    make_client: ClientType, family: Literal["arp", "ndp"]
) -> None:
    """Distinguish an unavailable endpoint from a successful empty table.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        family (Literal["arp", "ndp"]): Neighbor table family.
    """
    before_fetch = datetime.now(UTC)
    table = await _fetch_entries(make_client, family, {"total": 0, "rows": []})
    after_fetch = datetime.now(UTC)
    assert table is not None
    assert table.entries == ()
    assert table.unparsed_row_count == 0
    assert table.raw_rows == ()
    assert table.fetched_at.tzinfo is UTC
    assert before_fetch <= table.fetched_at <= after_fetch
    fetched_at = table.fetched_at
    assert table.fetched_at is fetched_at

    path = "/api/diagnostics/interface/search_arp?resolve=no" if family == "arp" else NDP_TABLE_PATH
    client, _router = make_router_client(make_client, routes={("POST", path): 404})
    fetch = client.get_arp_entries if family == "arp" else client.get_ndp_entries
    assert await fetch() is None


@pytest.mark.asyncio
async def test_neighbor_entries_fetch_all_rows_with_one_post(
    make_client: ClientType,
) -> None:
    """Request the full ARP result set in one POST instead of accepting a page.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
    """
    rows = [{"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.0.2.1"}] * 10_000
    client, router = make_router_client(
        make_client,
        routes={
            ("POST", ARP_TABLE_RESOLVE_PATH): {
                "total": len(rows),
                "rowCount": -1,
                "current": 1,
                "rows": rows,
            }
        },
    )

    table = await client.get_arp_entries(resolve_hostnames=True)

    assert table is not None
    assert len(table.entries) == 10_000
    assert router.requests == [("POST", ARP_TABLE_RESOLVE_PATH, {"rowCount": -1})]


@pytest.mark.asyncio
async def test_neighbor_entries_propagate_cancellation(
    make_client: ClientType,
) -> None:
    """Propagate cancellation during a normalized table request.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
    """
    client, _router = make_router_client(
        make_client,
        routes={
            ("POST", "/api/diagnostics/interface/search_arp?resolve=no"): asyncio.CancelledError()
        },
    )

    with pytest.raises(asyncio.CancelledError):
        await client.get_arp_entries()


@pytest.mark.asyncio
@pytest.mark.parametrize("resolve_hostnames", [True, False])
async def test_get_arp_entries_requests_table_once_with_resolve_flag(
    make_client: ClientType, resolve_hostnames: bool
) -> None:
    """Fetching normalized ARP rows makes one table request and forwards the resolve flag.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        resolve_hostnames (bool): Requested reverse-DNS behavior.
    """
    resolve = "yes" if resolve_hostnames else "no"
    path = f"/api/diagnostics/interface/search_arp?resolve={resolve}"
    client, router = make_router_client(
        make_client, routes={("POST", path): {"total": 1, "rows": [{"mac": "aa:bb:cc:dd:ee:ff"}]}}
    )

    table = await client.get_arp_entries(resolve_hostnames=resolve_hostnames)

    assert table is not None
    assert len(table.entries) == 1
    assert router.requests == [("POST", path, {"rowCount": -1})]


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["arp", "ndp"])
@pytest.mark.parametrize(
    ("status", "timeout", "expected_error"),
    [
        (403, False, OPNsensePrivilegeMissing),
        (500, False, OPNsenseConnectionError),
        (200, True, OPNsenseTimeoutError),
    ],
    ids=["permission", "server", "timeout"],
)
async def test_neighbor_entries_propagate_public_errors_when_throwing(
    make_client: ClientType,
    family: Literal["arp", "ndp"],
    status: int,
    timeout: bool,
    expected_error: type[OPNsenseConnectionError],
) -> None:
    """Throwing clients raise the public error and non-throwing clients return None.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        family (Literal["arp", "ndp"]): Neighbor table family.
        status (int): Response status for simulated HTTP failures.
        timeout (bool): Whether to simulate a transport timeout instead of a response.
        expected_error (type[OPNsenseConnectionError]): Expected public exception type.
    """
    for throw_errors in (True, False):
        failure = TimeoutError("timed out") if timeout else FakeResponse(status=status, ok=False)
        path = (
            "/api/diagnostics/interface/search_arp?resolve=no"
            if family == "arp"
            else NDP_TABLE_PATH
        )
        client, _router = make_router_client(
            make_client, routes={("POST", path): failure}, throw_errors=throw_errors
        )
        fetch = client.get_arp_entries if family == "arp" else client.get_ndp_entries
        if throw_errors:
            with pytest.raises(expected_error):
                await fetch()
        else:
            assert await fetch() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "mac", "valid", "ipv4", "ipv6"),
    [
        (
            {"mac": " AA-BB-CC-DD-EE-FF ", "ip": " 192.0.2.1 "},
            "aa:bb:cc:dd:ee:ff",
            "aa:bb:cc:dd:ee:ff",
            "192.0.2.1",
            None,
        ),
        (
            {"mac-address": "aa:bb:cc:dd:ee:ff", "ip-address": "192.0.2.1"},
            "aa:bb:cc:dd:ee:ff",
            "aa:bb:cc:dd:ee:ff",
            "192.0.2.1",
            None,
        ),
        (
            {"mac": " ", "mac-address": " AB-CD ", "ip": None, "ip-address": "fe80:0::1%em0"},
            "ab:cd",
            None,
            None,
            "fe80::1%em0",
        ),
        ({"mac": 5, "mac-address": [], "ip": [], "ip-address": 42}, None, None, None, None),
        ({"mac": "zz:zz:zz:zz:zz:zz", "ip": "bad"}, "zz:zz:zz:zz:zz:zz", None, None, None),
        ({}, None, None, None, None),
    ],
    ids=["primary", "arp-aliases", "partial-mac-scoped-ipv6", "wrong-types", "invalid", "empty"],
)
async def test_neighbor_entries_normalize_identity_and_retain_invalid_mappings(
    make_client: ClientType,
    row: dict[str, object],
    mac: str | None,
    valid: str | None,
    ipv4: str | None,
    ipv6: str | None,
) -> None:
    """Normalize aliases and families, retaining mappings with invalid identity fields.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        row (dict[str, object]): Raw neighbor values.
        mac (str | None): Expected permissive MAC.
        valid (str | None): Expected strict MAC.
        ipv4 (str | None): Expected IPv4 address.
        ipv6 (str | None): Expected IPv6 address.
    """
    table = await _fetch_entries(make_client, "ndp", {"total": 1, "rows": [row]})

    assert table is not None
    assert table.unparsed_row_count == 0
    assert len(table.entries) == 1
    entry = table.entries[0]
    assert isinstance(entry, OPNsenseClient.ArpNdpEntry)
    assert (entry.mac, entry.valid_mac, entry.ipv4_address, entry.ipv6_address) == (
        mac,
        valid,
        ipv4,
        ipv6,
    )


@pytest.mark.asyncio
async def test_neighbor_entries_partial_table_preserves_order_and_counts_nonmappings(
    make_client: ClientType,
) -> None:
    """Keep mapping rows in source order and count rows that are not mappings.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
    """
    rows = [
        {"mac": "aa:bb:cc:dd:ee:01", "ip": "192.0.2.1"},
        "not a row",
        {"mac": "bad", "ip": "also bad"},
        None,
        {"mac": "aa:bb:cc:dd:ee:02", "ip": "192.0.2.2"},
    ]

    table = await _fetch_entries(make_client, "arp", {"total": len(rows), "rows": rows})

    assert table is not None
    assert [entry.mac for entry in table.entries] == [
        "aa:bb:cc:dd:ee:01",
        "bad",
        "aa:bb:cc:dd:ee:02",
    ]
    assert table.entries[1].valid_mac is None
    assert table.unparsed_row_count == 2
    assert table.raw_rows == tuple(rows)


@pytest.mark.asyncio
async def test_neighbor_entries_copy_metadata_and_raw_rows_and_are_frozen(
    make_client: ClientType,
) -> None:
    """Isolate entries and diagnostic rows from the response and from each other.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
    """
    interface = {"name": ["lan"]}
    expires = [1]
    row = {
        "hostname": "??router??",
        "manufacturer": "Vendor",
        "expired": "0",
        "intf_description": interface,
        "intf": "fallback",
        "expires": expires,
        "type": {"kind": "dynamic"},
    }
    client, _router = make_router_client(
        make_client,
        routes={
            ("POST", "/api/diagnostics/interface/search_arp?resolve=no"): {
                "total": 1,
                "rows": [row],
            }
        },
    )

    table = await client.get_arp_entries()
    interface["name"].append("changed")
    expires.append(2)

    assert table is not None
    entry = table.entries[0]
    assert entry.hostname == "router"
    assert entry.manufacturer == "Vendor"
    assert entry.expired is False
    assert entry.interface == {"name": ["lan"]}
    assert entry.expires == [1]
    assert entry.arp_type == {"kind": "dynamic"}
    assert table.raw_rows == ({**row, "intf_description": {"name": ["lan"]}, "expires": [1]},)
    raw_row = table.raw_rows[0]
    assert isinstance(raw_row, dict)
    raw_row["type"]["kind"] = "changed"
    assert entry.arp_type == {"kind": "dynamic"}
    with pytest.raises(FrozenInstanceError):
        entry.hostname = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        table.unparsed_row_count = 1  # type: ignore[misc]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({}, False),
        ({"expired": False}, False),
        ({"expired": True}, True),
        ({"expired": 0}, False),
        ({"expired": 1}, True),
        ({"expired": ""}, False),
        ({"expired": " Yes "}, True),
        ({"expired": "NO"}, False),
        ({"expired": " true"}, True),
        ({"expired": "False "}, False),
        ({"expired": "1"}, True),
        ({"expired": "0"}, False),
        ({"expired": None}, False),
        ({"expired": 2}, True),
        ({"expired": -1}, True),
        ({"expired": 1.0}, True),
        ({"expired": "maybe"}, False),
        ({"expired": []}, False),
    ],
)
async def test_neighbor_entries_boolean_flag_conversion(
    make_client: ClientType, row: dict[str, object], expected: bool
) -> None:
    """Normalize expiration and permanence with the shared boolean coercion contract.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        row (dict[str, object]): Raw neighbor row.
        expected (bool): Expected expiration and permanent-entry flags.
    """
    table = await _fetch_entries(
        make_client,
        "arp",
        {"total": 1, "rows": [{**row, "permanent": row.get("expired")}]},
    )

    assert table is not None
    assert table.entries[0].expired is expected
    assert table.entries[0].permanent is expected


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            {
                "ip-address": "192.0.2.1",
                "mac-address": "aa:bb:cc:dd:ee:ff",
                "interface": "em0",
                "expires": 1199,
                "type": "ethernet",
            },
            "em0",
        ),
        ({"intf": "em1", "interface": "em0"}, "em1"),
        ({"intf_description": "LAN", "intf": "em1", "interface": "em0"}, "LAN"),
        ({"intf_description": None, "intf": "em1", "interface": "em0"}, "em1"),
        ({"intf_description": " \t ", "intf": "em1"}, "em1"),
        ({"intf": None, "interface": "em0"}, None),
    ],
    ids=[
        "raw-arp",
        "ndp-interface",
        "description",
        "null-description",
        "blank-description",
        "null-interface",
    ],
)
def test_neighbor_interface_aliases_and_mapping_proxy_rows(
    row: dict[str, object], expected: object
) -> None:
    """Preserve raw ARP interfaces, NDP field precedence, and accept any Mapping row.

    A JSON response only yields dict rows, so the parser is called directly to cover
    other mappings.

    Args:
        row (dict[str, object]): Raw neighbor row with interface aliases.
        expected (object): Expected normalized interface metadata.
    """
    table = OPNsenseClient._parse_neighbor_table([MappingProxyType(row)])

    assert table is not None
    assert table.unparsed_row_count == 0
    assert table.entries[0].interface == expected
    assert table.entries[0].interface_name == row.get("intf", row.get("interface"))
    assert table.entries[0].interface_description == row.get("intf_description")
    assert table.raw_rows == (row,)


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["arp", "ndp"])
async def test_normalized_neighbors_preserve_all_endpoint_fields(
    make_client: ClientType, family: Literal["arp", "ndp"]
) -> None:
    """Keep distinct interface fields and all original values without deprecated calls.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        family (Literal["arp", "ndp"]): Neighbor endpoint to exercise.
    """
    row = {
        "mac": "AA-BB-CC-DD-EE-FF",
        "ip": "192.0.2.1" if family == "arp" else "fe80:0::1%em0",
        "intf": "em0",
        "intf_description": "LAN",
        "hostname": "?router?",
        "manufacturer": "Vendor",
        "expired": "0",
        "permanent": "yes",
        "expires": 1199,
        "type": "ethernet",
        "future_field": {"values": [1]},
    }
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "error", message="Use get_(arp|ndp)_entries", category=DeprecationWarning
        )
        table = await _fetch_entries(make_client, family, {"total": 1, "rows": [row]})
    assert table is not None
    entry = table.entries[0]
    assert entry.interface == "LAN"
    assert entry.interface_name == "em0"
    assert entry.interface_description == "LAN"
    assert entry.permanent is True
    assert entry.expires == 1199
    assert entry.arp_type == "ethernet"
    assert table.raw_rows == (row,)


@pytest.mark.asyncio
async def test_neighbor_entries_trim_host_metadata_and_preserve_raw_values(
    make_client: ClientType,
) -> None:
    """Trim normalized metadata while preserving interface source fields and raw rows.

    Args:
        make_client (ClientType): Factory for a client with a mocked HTTP session.
    """
    rows = [
        {
            "hostname": "  ?? router ??  ",
            "manufacturer": "  Vendor Inc  ",
            "intf_description": " \t ",
            "intf": "em0",
        },
        {
            "hostname": " \t ",
            "manufacturer": "   ",
            "intf": "em1",
        },
        {"hostname": "??", "manufacturer": None},
    ]
    table = await _fetch_entries(make_client, "arp", {"total": len(rows), "rows": rows})

    assert table is not None
    first, second, third = table.entries
    assert (first.hostname, first.manufacturer) == ("router", "Vendor Inc")
    assert first.interface == "em0"
    assert first.interface_name == "em0"
    assert first.interface_description == " \t "
    assert (second.hostname, second.manufacturer) == (None, None)
    assert second.interface == "em1"
    assert second.interface_name == "em1"
    assert second.interface_description is None
    assert (third.hostname, third.manufacturer) == (None, None)
    assert table.raw_rows == tuple(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["arp", "ndp"])
async def test_raw_neighbor_getters_warn_and_preserve_results(
    make_client: ClientType, family: Literal["arp", "ndp"]
) -> None:
    """Direct legacy calls warn with a replacement and retain the raw response contract.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        family (Literal["arp", "ndp"]): Deprecated endpoint to exercise.
    """
    rows = [{"ip": "invalid", "future_field": [1]}]
    path = "/api/diagnostics/interface/search_arp?resolve=no" if family == "arp" else NDP_TABLE_PATH
    client, _router = make_router_client(make_client, routes={("GET", path): {"rows": rows}})
    with pytest.warns(DeprecationWarning, match=f"Use get_{family}_entries"):
        result = await getattr(client, f"get_{family}_table")()
    assert result == rows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reservation_rows", [None, [{"hw_address": "aa:00:00:00:00:0a", "ip_address": "192.0.2.10"}]]
)
async def test_get_dhcp_leases_kea_row_rules(
    make_client: ClientType, reservation_rows: list[dict[str, str]] | None
) -> None:
    """Kea rows keep their hostname, type, identity and expiry rules.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        reservation_rows (list[dict[str, str]] | None): Reservation rows served by Kea, or
            ``None`` when the reservation endpoint is not available.
    """
    future = int(datetime.now(tz=UTC).timestamp()) + 3600
    past = future - 7200
    overflow = 10**40
    base = {"if_name": "lan", "if_descr": "LAN", "state": "0"}
    rows = [
        {
            **base,
            "address": "192.0.2.1",
            "hostname": ".",
            "hwaddr": "aa:00:00:00:00:01",
            "is_reserved": "0",
        },
        {
            **base,
            "address": "192.0.2.2",
            "hostname": "host.example.",
            "hwaddr": "aa:00:00:00:00:02",
            "state": 0,
            "is_reserved": ["res-1"],
            "client_id": "01:aa",
            "expire": future,
        },
        {**base, "address": "192.0.2.3", "hostname": "no-mac", "hwaddr": "", "is_reserved": "0"},
        {
            **base,
            "address": "192.0.2.4",
            "hwaddr": "aa:00:00:00:00:04",
            "is_reserved": "0",
            "state": "1",
        },
        {
            **base,
            "address": "192.0.2.5",
            "hwaddr": "aa:00:00:00:00:05",
            "is_reserved": "0",
            "expire": past,
        },
        {
            **base,
            "address": "192.0.2.6",
            "hwaddr": "aa:00:00:00:00:06",
            "is_reserved": "0",
            "expire": overflow,
        },
        {
            **base,
            "address": "192.0.2.7",
            "hwaddr": "aa:00:00:00:00:07",
            "is_reserved": "1",
            "expire": "never",
        },
        {**base, "address": "192.0.2.10", "hwaddr": "aa:00:00:00:00:0a"},
        {**base, "address": "192.0.2.11", "hwaddr": "aa:00:00:00:00:0b"},
        None,
    ]
    routes: dict[str, object] = {_KEA_LEASES4: {"rows": rows}}
    if reservation_rows is not None:
        routes[_KEA_RESERVATIONS] = {"rows": reservation_rows}

    client, _router = _dhcp_router(make_client, routes)
    result = await _get_leases(client)
    leases = _leases_by_address(result)

    def expected(address: str, mac: str, **fields: object) -> dict[str, Any]:
        """Build the expected lease with the shared Kea defaults.

        Args:
            address (str): Lease address.
            mac (str): Lease MAC address.
            **fields (object): Fields overriding the defaults.

        Returns:
            dict[str, Any]: Expected normalized lease.
        """
        return {
            "address": address,
            "hostname": None,
            "type": "dynamic",
            "mac": mac,
            "expires": None,
        } | fields

    assert set(leases) == {
        "192.0.2.1",
        "192.0.2.2",
        "192.0.2.6",
        "192.0.2.7",
        "192.0.2.10",
        "192.0.2.11",
    }
    assert leases["192.0.2.1"] == expected("192.0.2.1", "aa:00:00:00:00:01")
    assert leases["192.0.2.2"] == expected(
        "192.0.2.2",
        "aa:00:00:00:00:02",
        hostname="host.example",
        type="static",
        expires=datetime.fromtimestamp(future, tz=UTC),
        client_id="01:aa",
        reserved_by=["res-1"],
    )
    assert leases["192.0.2.6"]["expires"] == overflow
    assert leases["192.0.2.7"] == expected(
        "192.0.2.7", "aa:00:00:00:00:07", type="static", expires="never"
    )
    if reservation_rows is None:
        assert leases["192.0.2.10"]["type"] == "unknown"
        assert leases["192.0.2.11"]["type"] == "unknown"
    else:
        assert leases["192.0.2.10"]["type"] == "static"
        assert leases["192.0.2.11"]["type"] == "dynamic"
    assert result["lease_interfaces"] == {"lan": "LAN"}


@pytest.mark.asyncio
async def test_get_dhcp_leases_kea_dhcpv6_rows_without_mac(make_client: ClientType) -> None:
    """Kea DHCPv6 keeps rows without a MAC and passes the DUID through.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
    """
    rows = [
        {
            "if_name": "lan",
            "if_descr": "LAN",
            "state": "0",
            "address": "2001:db8::1",
            "hostname": "v6.",
            "duid": "00:01:02",
            "is_reserved": "0",
        },
        {"if_name": "lan", "state": "1", "address": "2001:db8::2", "is_reserved": "0"},
    ]

    client, _router = _dhcp_router(make_client, {_KEA_LEASES6: {"rows": rows}})
    result = await _get_leases(client)

    assert _leases_by_address(result) == {
        "2001:db8::1": {
            "address": "2001:db8::1",
            "hostname": "v6",
            "type": "dynamic",
            "mac": None,
            "duid": "00:01:02",
            "expires": None,
        }
    }


@pytest.mark.asyncio
async def test_get_dhcp_leases_dnsmasq_row_rules(make_client: ClientType) -> None:
    """Dnsmasq rows keep their hostname, interface, type, identity and expiry rules.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
    """
    now = int(datetime.now(tz=UTC).timestamp())
    base = {"if_descr": "LAN", "hwaddr": "bb:00:00:00:00:01", "is_reserved": "0"}
    rows = [
        {**base, "address": "192.0.2.1", "hostname": "*", "if": "lan", "expire": now + 200},
        {**base, "address": "192.0.2.1", "hostname": "*", "if": "lan", "expire": now + 100},
        {
            **base,
            "address": "192.0.2.2",
            "hostname": "host.",
            "if_name": "lan",
            "if": "ignored",
            "is_reserved": "1",
            "client_id": "01:bb",
            "hwaddr": "",
        },
        {**base, "address": "192.0.2.3", "hostname": "", "if_name": "lan", "expire": now - 3600},
        {
            **base,
            "address": "192.0.2.4",
            "hostname": "...",
            "if_name": "lan",
            "is_reserved": ["x"],
            "expire": 10**40,
        },
    ]

    client, _router = _dhcp_router(make_client, {_DNSMASQ_LEASES: {"rows": rows}})
    result = await _get_leases(client)

    assert len([lease for lease in result["leases"]["lan"] if lease["address"] == "192.0.2.1"]) == 1
    assert _leases_by_address(result) == {
        # Duplicates keep the latest expiry, and a "*" hostname becomes None.
        "192.0.2.1": {
            "address": "192.0.2.1",
            "hostname": None,
            "type": "dynamic",
            "mac": "bb:00:00:00:00:01",
            "expires": datetime.fromtimestamp(now + 200, tz=UTC),
        },
        # Dnsmasq does not strip dots, and an empty MAC becomes None.
        "192.0.2.2": {
            "address": "192.0.2.2",
            "hostname": "host.",
            "type": "static",
            "mac": None,
            "client_id": "01:bb",
            "expires": None,
        },
        "192.0.2.4": {
            "address": "192.0.2.4",
            "hostname": None,
            "type": "static",
            "mac": "bb:00:00:00:00:01",
            "reserved_by": ["x"],
            "expires": 10**40,
        },
    }
    assert result["lease_interfaces"] == {"lan": "LAN"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "address"),
    [(_ISC_LEASES4, "192.0.2.1"), (_ISC_LEASES6, "2001:db8::1")],
    ids=["dhcpv4", "dhcpv6"],
)
async def test_get_dhcp_leases_isc_row_rules(
    make_client: ClientType, path: str, address: str
) -> None:
    """ISC rows keep their filtering, hostname, type and expiry rules for both families.

    Args:
        make_client (ClientType): Factory for clients using a mocked HTTP session.
        path (str): ISC lease search endpoint for the address family.
        address (str): Address used for the lease that stays active.
    """
    ends_at = (datetime.now(tz=_LEASE_ROUTER_TZ) + timedelta(hours=1)).replace(microsecond=0)
    past = (datetime.now(tz=_LEASE_ROUTER_TZ) - timedelta(hours=1)).strftime("%Y/%m/%d %H:%M:%S")
    base = {"state": "active", "if": "lan", "if_descr": "LAN", "type": "static"}
    rows = [
        {
            **base,
            "address": address,
            "mac": "cc:00:00:00:00:01",
            "hostname": "*.",
            "ends": ends_at.strftime("%Y/%m/%d %H:%M:%S"),
            "client_id": "01:cc",
            "is_reserved": ["x"],
        },
        {
            **base,
            "address": "no-ends",
            "mac": "cc:00:00:00:00:02",
            "hostname": ".",
            "type": "dynamic",
        },
        {**base, "address": "inactive", "mac": "cc:00:00:00:00:03", "state": "expired"},
        {**base, "address": "no-mac", "mac": ""},
        {**base, "address": "bad-ends", "mac": "cc:00:00:00:00:04", "ends": "never"},
        {**base, "address": "past", "mac": "cc:00:00:00:00:05", "ends": past},
        {"state": "active", "address": "no-interface", "mac": "cc:00:00:00:00:06"},
        None,
    ]

    client, _router = _dhcp_router(make_client, {path: {"rows": rows}})
    result = await _get_leases(client)

    assert _leases_by_address(result) == {
        # ISC preserves "*." and localizes ``ends`` in the Router timezone.
        address: {
            "address": address,
            "hostname": "*.",
            "type": "static",
            "mac": "cc:00:00:00:00:01",
            "expires": ends_at,
        },
        "no-ends": {
            "address": "no-ends",
            "hostname": None,
            "type": "dynamic",
            "mac": "cc:00:00:00:00:02",
            "expires": None,
        },
    }
    # A lease without an interface cannot be grouped, so it is left out.
    assert set(result["leases"]) == {"lan"}
    assert result["lease_interfaces"] == {"lan": "LAN"}


def _access_routes(**overrides: object) -> dict[str, object]:
    """Return a Router with Kea and Dnsmasq leases and readable configuration.

    Kea DHCPv4 serves ``lan`` (one lease) and ``opt1`` (no lease); Dnsmasq serves
    ``opt2`` (one lease) and ``opt3`` (no lease).

    Args:
        overrides (object): Responses that replace a default, keyed by ``kea`` (Kea
            DHCPv4 settings), ``ranges`` (Dnsmasq ranges), ``reservations`` (Kea
            reservations) or ``kea_leases`` (Kea DHCPv4 leases).

    Returns:
        dict[str, object]: Response served for each GET path.
    """
    routes: dict[str, object] = {
        _KEA_LEASES4: {
            "rows": [
                {
                    "state": "0",
                    "hwaddr": "aa:bb:cc:dd:ee:01",
                    "address": "192.0.2.10",
                    "hostname": "kea-host",
                    "if_name": "lan",
                    "if_descr": "LAN",
                }
            ]
        },
        _KEA_RESERVATIONS: {"rows": []},
        _KEA_INTERFACES: {
            "dhcpv4": {
                "general": {
                    "enabled": "1",
                    "interfaces": {
                        "lan": {"selected": 1, "value": "LAN"},
                        "opt1": {"selected": 1, "value": "IoT"},
                    },
                }
            }
        },
        _DNSMASQ_LEASES: {
            "rows": [
                {
                    "address": "198.51.100.10",
                    "hwaddr": "aa:bb:cc:dd:ee:02",
                    "hostname": "dnsmasq-host",
                    "if": "opt2",
                    "if_descr": "Guest",
                    "expire": 4102444800,
                }
            ]
        },
        _DNSMASQ_RANGES: {
            "rows": [
                {"interface": "opt2", "%interface": "Guest"},
                {"interface": "opt3", "%interface": "Lab"},
            ]
        },
    }
    names = {
        "kea": _KEA_INTERFACES,
        "ranges": _DNSMASQ_RANGES,
        "reservations": _KEA_RESERVATIONS,
        "kea_leases": _KEA_LEASES4,
    }
    routes.update({names[name]: value for name, value in overrides.items()})
    return routes


@pytest.mark.asyncio
async def test_get_dhcp_leases_reports_configuration_access(make_client: ClientType) -> None:
    """Readable configuration lists zero-lease interfaces with Configuration Access.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = _dhcp_router(make_client, _access_routes())

    result = await _get_leases(client)

    assert result_access(result) is Access.CONFIGURATION
    assert result["lease_interfaces"] == {
        "lan": "LAN",
        "opt1": "IoT",
        "opt2": "Guest",
        "opt3": "Lab",
    }
    assert set(result["leases"]) == {"lan", "opt2"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("denied", "denied_path", "expected_interfaces"),
    [
        pytest.param(
            "kea",
            "/api/kea/dhcpv4/get",
            {"lan": "LAN", "opt2": "Guest", "opt3": "Lab"},
            id="kea-settings",
        ),
        pytest.param(
            "ranges",
            "/api/dnsmasq/settings/search_range",
            {"lan": "LAN", "opt1": "IoT", "opt2": "Guest"},
            id="dnsmasq-ranges",
        ),
    ],
)
async def test_get_dhcp_leases_forbidden_interface_inventory_reports_status_access(
    make_client: ClientType,
    denied: str,
    denied_path: str,
    expected_interfaces: dict[str, str],
) -> None:
    """A forbidden interface inventory reports Status Access and keeps the leases.

    The denied backend's zero-lease interface is missing, interfaces with leases stay,
    and a second call reuses the cached denial without asking the Router again.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        denied (str): Configuration endpoint that answers 403.
        denied_path (str): Path of that endpoint.
        expected_interfaces (dict[str, str]): Interfaces the result still lists.
    """
    client, router = _dhcp_router(make_client, _access_routes(**{denied: 403}))

    result = await _get_leases(client)

    assert result_access(result) is Access.STATUS
    assert result["lease_interfaces"] == expected_interfaces
    assert _leases_by_address(result)["192.0.2.10"]["mac"] == "aa:bb:cc:dd:ee:01"
    assert _leases_by_address(result, "opt2")["198.51.100.10"]["mac"] == "aa:bb:cc:dd:ee:02"

    cached = await _get_leases(client)

    assert cached == result
    assert result_access(cached) is Access.STATUS
    assert router.count("GET", denied_path) == 1


@pytest.mark.asyncio
async def test_get_dhcp_leases_forbidden_reservations_keep_configuration_access(
    make_client: ClientType,
) -> None:
    """A forbidden Kea reservation endpoint only drops reservation detail.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _dhcp_router(make_client, _access_routes(reservations=403))

    result = await _get_leases(client)

    assert result_access(result) is Access.CONFIGURATION
    assert set(result["lease_interfaces"]) == {"lan", "opt1", "opt2", "opt3"}
    assert _leases_by_address(result)["192.0.2.10"]["type"] == "unknown"
    assert router.count("GET", "/api/kea/dhcpv4/search_reservation") == 1


@pytest.mark.asyncio
async def test_get_dhcp_leases_missing_configuration_keeps_configuration_access(
    make_client: ClientType,
) -> None:
    """Missing (404) configuration endpoints add no interfaces and keep Configuration Access.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = _dhcp_router(make_client, _access_routes(kea=404, ranges=404))

    result = await _get_leases(client)

    assert result_access(result) is Access.CONFIGURATION
    assert result["lease_interfaces"] == {"lan": "LAN", "opt2": "Guest"}


@pytest.mark.asyncio
async def test_get_dhcp_leases_failed_lease_endpoints_keep_inventory(
    make_client: ClientType,
) -> None:
    """Failing lease endpoints still return the configured interfaces without leases.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    routes = _access_routes(kea_leases=500)
    routes[_DNSMASQ_LEASES] = 403
    client, _router = _dhcp_router(make_client, routes)

    result = await _get_leases(client)

    assert result_access(result) is Access.CONFIGURATION
    assert result == {
        "lease_interfaces": {"lan": "LAN", "opt1": "IoT", "opt2": "Guest", "opt3": "Lab"},
        "leases": {},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "expected_error", "unrequested"),
    [
        pytest.param(
            {"kea_leases": 500, "kea": 403},
            OPNsenseConnectionError,
            "/api/kea/dhcpv4/get",
            id="lease-failure-first",
        ),
        pytest.param(
            {"kea": 403, "ranges": 403},
            OPNsensePrivilegeMissing,
            "/api/dnsmasq/settings/search_range",
            id="kea-denial-before-ranges",
        ),
    ],
)
async def test_get_dhcp_leases_strict_mode_raises_first_failure(
    make_client: ClientType,
    overrides: dict[str, int],
    expected_error: type[OPNsenseConnectionError],
    unrequested: str,
) -> None:
    """Throwing clients raise the first failed request and send nothing after it.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        overrides (dict[str, int]): Endpoints that fail, by ``_access_routes`` name.
        expected_error (type[OPNsenseConnectionError]): Expected public exception type.
        unrequested (str): Later endpoint the client must not reach.
    """
    client, router = _dhcp_router(make_client, _access_routes(**overrides), throw_errors=True)

    with pytest.raises(expected_error) as raised:
        await _get_leases(client)

    assert type(raised.value) is expected_error
    assert router.count("GET", unrequested) == 0
