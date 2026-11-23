"""Tests for `aiopnsense.vpn`."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import aiohttp
import pytest

import aiopnsense as aiopnsense_module
from aiopnsense import OPNsenseClient, vpn as aiopnsense_vpn
from aiopnsense.exceptions import OPNsenseConnectionError
from tests.conftest import FIRMWARE_STATUS_PATH, FakeRouter, HeldResponse, make_router_client

ClientType = Callable[..., OPNsenseClient]

SNAKE_CASE_FIRMWARE = "26.1.1"
CAMEL_CASE_FIRMWARE = "25.1.1"
NOT_FOUND = 404
UNAVAILABLE = 503

# Toggle endpoints on snake_case firmware, keyed by VPN type and collection.
TOGGLE_ENDPOINTS = {
    ("openvpn", "servers"): (
        "/api/openvpn/instances/toggle/uuid",
        "/api/openvpn/service/reconfigure",
    ),
    ("wireguard", "clients"): (
        "/api/wireguard/client/toggle_client/uuid",
        "/api/wireguard/service/reconfigure",
    ),
    ("wireguard", "servers"): (
        "/api/wireguard/server/toggle_server/uuid",
        "/api/wireguard/service/reconfigure",
    ),
    ("ipsec", "connections"): (
        "/api/ipsec/connections/toggle_connection/uuid",
        "/api/ipsec/service/reconfigure",
    ),
}


make_vpn_router = partial(make_router_client, firmware=SNAKE_CASE_FIRMWARE)


def sent(router: FakeRouter, method: str | None = None) -> list[tuple[str, str]]:
    """Return the requests the Router received, without the firmware status read.

    Args:
        router (FakeRouter): Router that recorded the requests.
        method (str | None): HTTP method to keep, or ``None`` for every method.

    Returns:
        list[tuple[str, str]]: ``(method, path)`` pairs in arrival order.
    """
    return [call for call in router.calls(method) if call[1] != FIRMWARE_STATUS_PATH]


async def cancel_while_in_flight(call: Awaitable[Any], held: HeldResponse) -> None:
    """Cancel a public call while its request is in flight and check the cancellation arrives.

    Args:
        call (Awaitable[Any]): Public client call whose request the Router holds.
        held (HeldResponse): Held response the call is waiting on.
    """
    task = asyncio.ensure_future(call)
    try:
        await asyncio.wait_for(held.entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
    finally:
        held.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def openvpn_routes(
    *,
    sessions: Any = None,
    vpn_routes: Any = None,
    providers: Any = None,
    instances: Any = None,
    details: dict[str, Any] | None = None,
    snake_case: bool = True,
) -> dict[tuple[str, str], Any]:
    """Return the OpenVPN GET routes a fake Router serves, with empty inventories by default.

    Args:
        sessions (Any): Reply for the sessions search, or an empty row list.
        vpn_routes (Any): Reply for the routes search, or an empty row list.
        providers (Any): Reply for the provider export, or an empty mapping.
        instances (Any): Reply for the instance search, or an empty row list.
        details (dict[str, Any] | None): Instance details replies keyed by instance UUID.
        snake_case (bool): Whether the Router uses snake_case endpoint names.

    Returns:
        dict[tuple[str, str], Any]: Routes keyed by ``(method, path)``.
    """
    sessions_path = (
        aiopnsense_vpn.OPENVPN_SESSIONS_SNAKE_ENDPOINT
        if snake_case
        else aiopnsense_vpn.OPENVPN_SESSIONS_CAMELCASE_ENDPOINT
    )
    routes_path = (
        aiopnsense_vpn.OPENVPN_ROUTES_SNAKE_ENDPOINT
        if snake_case
        else aiopnsense_vpn.OPENVPN_ROUTES_CAMELCASE_ENDPOINT
    )
    routes: dict[tuple[str, str], Any] = {
        ("GET", sessions_path): {"rows": []} if sessions is None else sessions,
        ("GET", routes_path): {"rows": []} if vpn_routes is None else vpn_routes,
        ("GET", aiopnsense_vpn.OPENVPN_PROVIDERS_ENDPOINT): {} if providers is None else providers,
        ("GET", aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT): (
            {"rows": []} if instances is None else instances
        ),
    }
    for uuid, reply in (details or {}).items():
        routes[("GET", f"{aiopnsense_vpn.OPENVPN_INSTANCE_DETAILS_ENDPOINT_PREFIX}{uuid}")] = reply
    return routes


def wireguard_routes(
    *, summary: Any = None, clients: Any = None, servers: Any = None
) -> dict[tuple[str, str], Any]:
    """Return the WireGuard GET routes a fake Router serves, with empty inventories by default.

    Args:
        summary (Any): Reply for the status summary, or an empty row list.
        clients (Any): Reply for the client configuration, or an empty inventory.
        servers (Any): Reply for the server configuration, or an empty inventory.

    Returns:
        dict[tuple[str, str], Any]: Routes keyed by ``(method, path)``.
    """
    return {
        ("GET", aiopnsense_vpn.WIREGUARD_SUMMARY_ENDPOINT): (
            {"rows": []} if summary is None else summary
        ),
        ("GET", aiopnsense_vpn.WIREGUARD_CLIENTS_ENDPOINT): (
            {"client": {"clients": {"client": {}}}} if clients is None else clients
        ),
        ("GET", aiopnsense_vpn.WIREGUARD_SERVERS_ENDPOINT): (
            {"server": {"servers": {"server": {}}}} if servers is None else servers
        ),
    }


def ipsec_routes(
    sessions: Any,
    connections: Any = None,
    children: Any = None,
    phase2: Any = None,
    *,
    snake_case: bool = True,
) -> dict[tuple[str, str], Any]:
    """Return the IPsec routes a fake Router serves, with empty inventories by default.

    Args:
        sessions (Any): Reply for the phase 1 sessions search.
        connections (Any): Reply for the connections configuration, or an empty row list.
        children (Any): Reply for the child configuration, or an empty row list.
        phase2 (Any): Reply for every phase 2 sessions request, or an empty row list.
        snake_case (bool): Whether the Router uses snake_case endpoint names.

    Returns:
        dict[tuple[str, str], Any]: Routes keyed by ``(method, path)``.
    """
    names = (
        (
            aiopnsense_vpn.IPSEC_PHASE1_SESSIONS_SNAKE_ENDPOINT,
            aiopnsense_vpn.IPSEC_CONNECTIONS_SNAKE_ENDPOINT,
            aiopnsense_vpn.IPSEC_CHILDREN_SNAKE_ENDPOINT,
            aiopnsense_vpn.IPSEC_PHASE2_SESSIONS_SNAKE_ENDPOINT,
        )
        if snake_case
        else (
            aiopnsense_vpn.IPSEC_PHASE1_SESSIONS_CAMELCASE_ENDPOINT,
            aiopnsense_vpn.IPSEC_CONNECTIONS_CAMELCASE_ENDPOINT,
            aiopnsense_vpn.IPSEC_CHILDREN_CAMELCASE_ENDPOINT,
            aiopnsense_vpn.IPSEC_PHASE2_SESSIONS_CAMELCASE_ENDPOINT,
        )
    )
    sessions_path, connections_path, children_path, phase2_path = names
    return {
        ("GET", sessions_path): sessions,
        ("GET", connections_path): {"rows": []} if connections is None else connections,
        ("GET", children_path): {"rows": []} if children is None else children,
        ("POST", phase2_path): {"rows": []} if phase2 is None else phase2,
    }


@pytest.mark.asyncio
async def test_wireguard_processing_and_updates() -> None:
    """Exercise static wireguard status update helpers and peer linking."""
    # Test static methods for wireguard processing and updates
    server = {
        "uuid": "s1",
        "name": "srv",
        "pubkey": "pk",
        "clients": [{"pubkey": "cpk"}],
        "interface": "wg1",
        "tunnel_addresses": ["10.0.0.1"],
        "total_bytes_recv": 0,
        "total_bytes_sent": 0,
    }
    client = {
        "uuid": "c1",
        "pubkey": "cpk",
        "servers": [{"interface": "wg1"}],
        "tunnel_addresses": ["10.0.0.2"],
        "total_bytes_recv": 0,
        "total_bytes_sent": 0,
    }
    servers = {"s1": server}
    clients = {"c1": client}

    # peer entry representing interface update
    entry_interface = {"type": "interface", "public-key": "pk", "status": "up"}
    aiopnsense_module.OPNsenseClient._update_wireguard_status([entry_interface], servers, clients)
    # server status set
    assert any(s.get("status") == "up" for s in servers.values())

    # peer update representing peer (client) with handshake and transfers
    entry_peer = {
        "type": "peer",
        "public-key": "cpk",
        "if": "wg1",
        "endpoint": "1.2.3.4:51820",
        "transfer-rx": "100",
        "transfer-tx": "200",
        "latest-handshake": "0",
    }
    aiopnsense_module.OPNsenseClient._update_wireguard_status([entry_peer], servers, clients)
    # ensure client's server linkage updated: require explicit connected state or measurable traffic
    srv = clients["c1"]["servers"][0]
    has_connected_flag = bool(srv.get("connected"))
    # accept numeric transfer counters set by implementation (bytes_recv/bytes_sent)
    rx = int(srv.get("bytes_recv") or srv.get("transfer-rx") or 0)
    tx = int(srv.get("bytes_sent") or srv.get("transfer-tx") or 0)
    assert has_connected_flag or (rx > 0 or tx > 0)


@pytest.mark.asyncio
async def test_openvpn_client_session_updates_server_stats() -> None:
    """Ensure that openvpn session with is_client True updates server/client stats appropriately."""
    # no client/session needed for this static helper test

    # Build servers and clients structures
    servers: dict = {
        "s1": {"uuid": "s1", "clients": [], "total_bytes_recv": 0, "total_bytes_sent": 0}
    }
    clients: dict = {"c1": {"uuid": "c1", "pubkey": "pk1", "servers": [{"interface": "wg1"}]}}

    # session entry as a peer will update client/server
    entry = {
        "type": "peer",
        "public-key": "pk1",
        "if": "wg1",
        "transfer-rx": "100",
        "transfer-tx": "200",
        "latest-handshake": int(datetime.now(tz=UTC).timestamp()),
    }

    aiopnsense_module.OPNsenseClient._update_wireguard_peer_status(entry, servers, clients)

    # ensure totals updated on either server or client as implementation may update parent
    server_updated = any(
        s.get("total_bytes_recv", 0) >= 100 or s.get("total_bytes_sent", 0) >= 200
        for s in servers.values()
    )
    client_updated = any(
        c.get("total_bytes_recv", 0) >= 100 or c.get("total_bytes_sent", 0) >= 200
        for c in clients.values()
    )
    assert server_updated or client_updated


@pytest.mark.asyncio
async def test_get_wireguard_full_processing_and_peer_details() -> None:
    """Ensure the wireguard peer status helper updates server/client transfer counters."""
    summary = {
        "peers": [
            {
                "public-key": "pk1",
                "latest-handshake": int(datetime.now(tz=UTC).timestamp()),
                "transfer-rx": "100",
                "transfer-tx": "200",
                "if": "wg1",
            }
        ],
        "servers": {"s1": {"uuid": "s1"}},
        "clients": {"c1": {"uuid": "c1", "pubkey": "pk1", "servers": [{"interface": "wg1"}]}},
    }
    servers: dict = {
        "s1": {"uuid": "s1", "clients": [], "total_bytes_recv": 0, "total_bytes_sent": 0}
    }
    clients_map: dict = {"c1": {"uuid": "c1", "pubkey": "pk1", "servers": [{"interface": "wg1"}]}}

    entry = summary["peers"][0]
    aiopnsense_module.OPNsenseClient._update_wireguard_peer_status(entry, servers, clients_map)
    updated = any(
        s.get("total_bytes_recv", 0) >= 100 or s.get("total_bytes_sent", 0) >= 200
        for s in servers.values()
    )
    assert updated or any(c.get("total_bytes_recv", 0) >= 100 for c in clients_map.values())


@pytest.mark.parametrize(
    "delta_minutes,expected",
    [
        (2, True),  # within 3 minutes => connected
        (3, True),  # exactly at threshold => connected
        (5, False),  # beyond threshold => not connected
    ],
)
def test__wireguard_is_connected_variants(
    monkeypatch: pytest.MonkeyPatch, delta_minutes: int, expected: bool
) -> None:
    """WireGuard connection considered active when last handshake within threshold.

    Monkeypatch `datetime.now` in the module under test to a fixed value with no
    microseconds so comparisons at the 3-minute boundary are deterministic.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing the module datetime provider.
        delta_minutes (int): Age of the latest WireGuard handshake.
        expected (bool): Connection state expected for that handshake age.
    """
    fixed_now = datetime.now().astimezone().replace(microsecond=0)
    # create a minimal fake datetime provider with a static now() returning fixed_now
    FakeDT = type("FakeDT", (), {"now": staticmethod(lambda: fixed_now)})
    monkeypatch.setattr(aiopnsense_vpn, "datetime", FakeDT)
    assert (
        aiopnsense_vpn.VPNMixin._wireguard_is_connected(
            fixed_now - timedelta(minutes=delta_minutes)
        )
        is expected
    )
    # None always False
    if delta_minutes == 5:  # only need to assert once in param set
        assert aiopnsense_vpn.VPNMixin._wireguard_is_connected(None) is False


async def test_get_openvpn_and_fetch_details(make_client: ClientType) -> None:
    """Validate openvpn server/client discovery and fetch details flow.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    details_path = f"{aiopnsense_vpn.OPENVPN_INSTANCE_DETAILS_ENDPOINT_PREFIX}uuid1"
    client, router = make_vpn_router(
        make_client,
        openvpn_routes(
            sessions={
                "rows": [
                    {
                        "type": "server",
                        "id": "1_desc_1",
                        "description": "s1",
                        "status": "connected",
                    }
                ]
            },
            vpn_routes={
                "rows": [
                    {
                        "id": "1",
                        "common_name": "c1",
                        "real_address": "1.2.3.4",
                        "virtual_address": "10.0.0.1",
                        "last_ref__time_t_": 0,
                    }
                ]
            },
            providers={"1": {"name": "prov1", "hostname": "host", "local_port": "1194"}},
            instances={
                "rows": [
                    {
                        "role": "server",
                        "uuid": "uuid1",
                        "description": "server1",
                        "enabled": "1",
                        "dev_type": "tun",
                    }
                ]
            },
            details={
                "uuid1": {
                    "instance": {
                        "server": "10.0.0.2",
                        "dns_servers": {"1": {"selected": 1, "value": "8.8.8.8"}},
                    }
                }
            },
        ),
    )

    openvpn = await client.get_openvpn()

    assert openvpn is not None
    assert "servers" in openvpn and "clients" in openvpn
    assert any(
        s.get("name") == "server1" or s.get("uuid") == "uuid1" for s in openvpn["servers"].values()
    )
    assert router.count("GET", details_path) == 1
    assert openvpn["servers"]["uuid1"]["tunnel_addresses"] == ["10.0.0.2"]


async def test_openvpn_more_detail_parsing(make_client: ClientType) -> None:
    """Exercise additional OpenVPN parsing branches (no sessions, missing fields).

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = make_vpn_router(
        make_client,
        openvpn_routes(
            instances={"rows": [{"role": "client", "uuid": "c1", "enabled": "0"}]},
            details={"c1": {"instance": {}}},  # missing details
        ),
    )

    res = await client.get_openvpn()

    assert res is not None
    assert "servers" in res and "clients" in res


async def test_openvpn_processing_and_fetch_details(make_client: ClientType) -> None:
    """Test processing of OpenVPN instances/providers/sessions/routes and fetching details.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    details = {
        "instance": {
            "server": "10.0.0.1",
            "dns_servers": {"0": {"selected": "1", "value": "8.8.8.8"}},
        }
    }
    client, _router = make_vpn_router(
        make_client,
        openvpn_routes(
            sessions={
                "rows": [
                    {
                        "type": "server",
                        "id": "srv1_1",
                        "description": "S1",
                        "status": "connected",
                    },
                    "malformed",
                    {"type": "server"},
                    {"type": "client", "id": "phantomserver_1", "status": "connected"},
                    {"type": "server", "id": "srv2_1", "status": "failed"},
                ]
            },
            vpn_routes={
                "rows": [
                    {"id": "unknown", "common_name": "ignored"},
                    {
                        "id": "srv1",
                        "common_name": "cname",
                        "real_address": "1.2.3.4",
                        "virtual_address": "10.0.0.1",
                    },
                    {
                        "id": "srv2",
                        "common_name": "client-a",
                        "real_address": "1.2.3.5",
                        "virtual_address": "10.0.0.2",
                    },
                    None,
                ]
            },
            providers={
                "srv1": {"name": "prov1", "hostname": "host.example", "local_port": "1194"},
                "srv2": {"name": "provider-two"},
            },
            instances={
                "rows": [
                    {
                        "role": "server",
                        "uuid": "srv1",
                        "description": "Serv1",
                        "enabled": "1",
                        "dev_type": "tun",
                    },
                    {
                        "role": "client",
                        "uuid": "c1",
                        "description": "client1",
                        "enabled": "1",
                    },
                ]
            },
            details={"srv1": details, "srv2": details},
        ),
    )

    openvpn = await client.get_openvpn()

    assert openvpn is not None
    assert set(openvpn["servers"]) == {"srv1", "srv2"}
    assert set(openvpn["clients"]) == {"c1"}
    servers = openvpn["servers"]
    srv = servers.get("srv1")
    assert srv is not None
    assert srv["status"] == "up"
    assert srv.get("dns_servers") == ["8.8.8.8"]
    assert len(srv["clients"]) == 1
    assert srv["clients"][0]["name"] == "cname"
    assert servers["srv2"]["name"] == "provider-two"
    assert servers["srv2"]["status"] == "failed"
    assert len(servers["srv2"]["clients"]) == 1
    assert servers["srv2"]["clients"][0]["name"] == "client-a"
    assert openvpn["clients"]["c1"]["name"] == "client1"
    assert openvpn["clients"]["c1"]["enabled"] is True


async def test_openvpn_server_details_missing_server_field(make_client: ClientType) -> None:
    """When instance details lack 'server' key, no tunnel_addresses should be set.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = make_vpn_router(
        make_client,
        openvpn_routes(
            instances={"rows": [{"role": "server", "uuid": "srv1", "enabled": "1"}]},
            details={"srv1": {"instance": {}}},
        ),
    )

    openvpn = await client.get_openvpn()

    assert openvpn is not None
    srv = openvpn["servers"]["srv1"]
    assert "tunnel_addresses" not in srv or srv["tunnel_addresses"] == []


async def test_openvpn_server_details_skip_unavailable_endpoint(make_client: ClientType) -> None:
    """Verify unsupported OpenVPN per-instance endpoints are skipped.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    details_path = f"{aiopnsense_vpn.OPENVPN_INSTANCE_DETAILS_ENDPOINT_PREFIX}srv1"
    client, router = make_vpn_router(
        make_client,
        openvpn_routes(
            instances={"rows": [{"role": "server", "uuid": "srv1", "enabled": "1"}]},
            details={"srv1": NOT_FOUND},
        ),
    )

    openvpn = await client.get_openvpn()

    assert openvpn is not None
    server = openvpn["servers"]["srv1"]
    assert server["connected_clients"] == 0
    assert server["total_bytes_sent"] == 0
    assert server["total_bytes_recv"] == 0
    assert router.count("GET", details_path) == 1


async def test_get_wireguard_success_and_invalid(make_client: ClientType) -> None:
    """Exercise get_wireguard success path and invalid structure early return.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    now = datetime.now().astimezone()
    old_handshake = int((now - timedelta(minutes=10)).timestamp())  # disconnected

    # Build server/client structures expected by API shape
    summary_resp = {
        "rows": [
            {"type": "interface", "public-key": "spk", "status": "up"},
            {
                "type": "peer",
                "public-key": "cpk",
                "if": "wg1",
                "latest-handshake": old_handshake,
                "transfer-rx": "1",
                "transfer-tx": "2",
            },
        ]
    }
    clients_resp = {
        "client": {
            "clients": {
                "client": {
                    "c1": {
                        "name": "client1",
                        "pubkey": "cpk",
                        "enabled": 1,
                        "tunneladdress": {},
                        "servers": {"s1": {"selected": "1", "value": "srv1"}},
                    }
                }
            }
        }
    }
    servers_resp = {
        "server": {
            "servers": {
                "server": {
                    "s1": {
                        "name": "srv1",
                        "pubkey": "spk",
                        "enabled": 1,
                        "instance": "1",
                        "tunneladdress": {"0": {"selected": "1", "value": "10.0.0.1"}},
                        "peers": {"c1": {"selected": "1", "value": "client1"}},
                    }
                }
            }
        }
    }
    client, router = make_vpn_router(
        make_client,
        wireguard_routes(summary=summary_resp, clients=clients_resp, servers=servers_resp),
    )

    wg = await client.get_wireguard()

    assert wg is not None
    assert "servers" in wg and "clients" in wg and wg["servers"]["s1"]["uuid"] == "s1"
    # client peer should reflect disconnected (old handshake)
    assert wg["clients"]["c1"].get("connected_servers") == 0

    # Invalid summary must not report configured peers as disconnected.
    router.routes[("GET", aiopnsense_vpn.WIREGUARD_SUMMARY_ENDPOINT)] = {"rows": {}}
    assert await client.get_wireguard() is None


@pytest.mark.parametrize("malformed_inventory", ["clients", "servers"])
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_get_wireguard_rejects_malformed_inventory_entries(
    make_client: ClientType,
    malformed_inventory: str,
    throw_errors: bool,
) -> None:
    """Reject malformed client or server records before peer processing.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        malformed_inventory (str): Configuration inventory with an invalid record.
        throw_errors (bool): Whether strict request errors are enabled.
    """
    client_summ: dict[str, Any] = {
        "client-disabled": {
            "name": "disabled client",
            "enabled": "0",
            "tunneladdress": {},
            "servers": {},
        }
    }
    server_summ: dict[str, Any] = {
        "server-disabled": {
            "name": "disabled server",
            "enabled": "0",
            "instance": "1",
            "tunneladdress": {},
            "peers": {},
        }
    }
    if malformed_inventory == "clients":
        client_summ["client-malformed"] = None
        server_summ["server-disabled"]["peers"] = {
            "client-malformed": {"selected": "1", "value": "invalid"}
        }
    else:
        server_summ["server-malformed"] = None
        client_summ["client-disabled"]["servers"] = {
            "server-malformed": {"selected": "1", "value": "invalid"}
        }
    client, _router = make_vpn_router(
        make_client,
        wireguard_routes(
            clients={"client": {"clients": {"client": client_summ}}},
            servers={"server": {"servers": {"server": server_summ}}},
        ),
        throw_errors=throw_errors,
    )

    assert await client.get_wireguard() is None


@pytest.mark.parametrize("status", [NOT_FOUND, UNAVAILABLE])
@pytest.mark.parametrize("unavailable_inventory", ["clients", "servers"])
async def test_get_wireguard_missing_or_unavailable_inventory(
    make_client: ClientType, unavailable_inventory: str, status: int
) -> None:
    """Keep a missing configuration inventory empty, and a transient failure unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        unavailable_inventory (str): Configuration endpoint that fails.
        status (int): HTTP status the failing endpoint answers with.
    """
    client, _router = make_vpn_router(
        make_client, wireguard_routes(**{unavailable_inventory: status})
    )

    expected: dict[str, Any] | None = (
        None if status == UNAVAILABLE else {"servers": {}, "clients": {}}
    )
    assert await client.get_wireguard() == expected


@pytest.mark.parametrize("throw_errors", [False, True])
@pytest.mark.parametrize(
    ("client_rows", "server_rows", "expected"),
    [
        ([], [], {"servers": {}, "clients": {}}),
        ([], {}, {"servers": {}, "clients": {}}),
        ({}, [], {"servers": {}, "clients": {}}),
        ([{"name": "client"}], [], None),
        ([], [{"name": "server"}], None),
    ],
)
async def test_get_wireguard_list_shaped_inventory(
    make_client: ClientType,
    client_rows: Any,
    server_rows: Any,
    expected: dict[str, dict[str, Any]] | None,
    throw_errors: bool,
) -> None:
    """Treat empty list inventories as empty and non-empty lists as malformed.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        client_rows (Any): Payload at ``client.clients.client``.
        server_rows (Any): Payload at ``server.servers.server``.
        expected (dict[str, dict[str, Any]] | None): Expected ``get_wireguard`` result.
        throw_errors (bool): Whether strict request errors are enabled.
    """
    client, _router = make_vpn_router(
        make_client,
        wireguard_routes(
            clients={"client": {"clients": {"client": client_rows}}},
            servers={"server": {"servers": {"server": server_rows}}},
        ),
        throw_errors=throw_errors,
    )

    assert await client.get_wireguard() == expected


async def test_get_wireguard_empty_inventories(make_client: ClientType) -> None:
    """Report an empty WireGuard inventory as empty rather than unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = make_vpn_router(make_client, wireguard_routes())

    assert await client.get_wireguard() == {"servers": {}, "clients": {}}


async def test_get_wireguard_known_missing_summary_returns_none(make_client: ClientType) -> None:
    """Return None when the WireGuard summary is known missing, since it has no empty fallback.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = make_vpn_router(make_client, wireguard_routes(summary=NOT_FOUND))

    assert await client.get_wireguard() is None


async def test_openvpn_sessions_failure_stops_the_read(make_client: ClientType) -> None:
    """Fail closed without further requests when the OpenVPN sessions endpoint is unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_vpn_router(make_client, openvpn_routes(sessions=UNAVAILABLE))

    assert await client.get_openvpn() is None
    assert sent(router) == [("GET", aiopnsense_vpn.OPENVPN_SESSIONS_SNAKE_ENDPOINT)]


@pytest.mark.parametrize(
    ("instances", "providers"),
    [
        pytest.param(
            {"rows": [{"role": "client", "uuid": "disabled", "enabled": "0"}, None]},
            {"server": {"name": "server"}},
            id="malformed_instance_row",
        ),
        pytest.param(
            {"rows": [{"role": "server", "uuid": ""}, {"role": "client", "uuid": "ok"}]},
            {},
            id="empty_server_uuid",
        ),
        pytest.param(
            {
                "rows": [
                    {"role": "client", "uuid": "disabled", "enabled": "0"},
                    {"uuid": "lost", "enabled": "0"},
                ]
            },
            {},
            id="missing_instance_role",
        ),
        pytest.param(
            {
                "rows": [
                    {"role": "client", "uuid": "disabled", "enabled": "0"},
                    {"role": "", "uuid": "lost", "enabled": "0"},
                ]
            },
            {},
            id="empty_instance_role",
        ),
        pytest.param(
            {"rows": [{"role": "client", "uuid": "disabled", "enabled": "0"}]},
            {"good": {"name": "good"}, "malformed": None},
            id="malformed_provider_record",
        ),
    ],
)
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_get_openvpn_rejects_malformed_required_inventory_entries(
    make_client: ClientType,
    instances: dict[str, Any],
    providers: dict[str, Any],
    throw_errors: bool,
) -> None:
    """Return unavailable when an OpenVPN configuration record is malformed.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        instances (dict[str, Any]): Instance-search payload returned by the API.
        providers (dict[str, Any]): Provider payload returned by the API.
        throw_errors (bool): Whether strict request errors are enabled.
    """
    client, _router = make_vpn_router(
        make_client,
        openvpn_routes(instances=instances, providers=providers),
        throw_errors=throw_errors,
    )

    assert await client.get_openvpn() is None


@pytest.mark.parametrize("throw_errors", [False, True])
async def test_get_openvpn_routes_request_failure(
    make_client: ClientType, throw_errors: bool
) -> None:
    """Degrade a failed optional routes request to partial data, or raise in throw mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Whether strict request errors are enabled.
    """
    client, _router = make_vpn_router(
        make_client,
        openvpn_routes(
            sessions={"rows": [{"type": "server", "id": "server", "status": "connected"}]},
            vpn_routes=aiohttp.ClientConnectionError("routes unavailable"),
            instances={"rows": [{"role": "server", "uuid": "server", "enabled": "1"}]},
        ),
        throw_errors=throw_errors,
    )

    if throw_errors:
        with pytest.raises(OPNsenseConnectionError):
            await client.get_openvpn()
    else:
        result = await client.get_openvpn()
        assert result is not None
        server = result["servers"]["server"]
        assert server["status"] == "up"
        assert "clients" not in server
        assert "connected_clients" not in server


async def test_get_openvpn_routes_cancellation_propagates(make_client: ClientType) -> None:
    """Never swallow cancellation that arrives while the optional routes request is in flight.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    held = HeldResponse(json_payload={"rows": []})
    client, _router = make_vpn_router(make_client, openvpn_routes(vpn_routes=held))

    await cancel_while_in_flight(client.get_openvpn(), held)


async def test_get_openvpn_ignores_explicit_unsupported_role(make_client: ClientType) -> None:
    """Ignore an explicit unsupported role while validating required records.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = make_vpn_router(
        make_client, openvpn_routes(instances={"rows": [{"role": "other"}]})
    )

    assert await client.get_openvpn() == {"servers": {}, "clients": {}}


@pytest.mark.parametrize("status", [NOT_FOUND, UNAVAILABLE])
@pytest.mark.parametrize("unavailable_inventory", ["providers", "instances"])
async def test_get_openvpn_missing_or_unavailable_inventory(
    make_client: ClientType, unavailable_inventory: str, status: int
) -> None:
    """Keep a missing configuration inventory empty, and a transient failure unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        unavailable_inventory (str): Configuration endpoint that fails.
        status (int): HTTP status the failing endpoint answers with.
    """
    client, _router = make_vpn_router(
        make_client, openvpn_routes(**{unavailable_inventory: status})
    )

    expected: dict[str, Any] | None = (
        None if status == UNAVAILABLE else {"servers": {}, "clients": {}}
    )
    assert await client.get_openvpn() == expected


async def test_get_openvpn_empty_inventories(make_client: ClientType) -> None:
    """Report an empty OpenVPN inventory as empty rather than unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = make_vpn_router(make_client, openvpn_routes())

    assert await client.get_openvpn() == {"servers": {}, "clients": {}}


@pytest.mark.parametrize(
    ("snake_case", "expected_sessions", "expected_routes", "expected_client_toggle"),
    [
        (
            True,
            "/api/openvpn/service/search_sessions",
            "/api/openvpn/service/search_routes",
            "/api/wireguard/client/toggle_client/uuid",
        ),
        (
            False,
            "/api/openvpn/service/searchSessions",
            "/api/openvpn/service/searchRoutes",
            "/api/wireguard/client/toggleClient/uuid",
        ),
    ],
)
async def test_vpn_switched_endpoints_follow_selected_case(
    make_client: ClientType,
    snake_case: bool,
    expected_sessions: str,
    expected_routes: str,
    expected_client_toggle: str,
) -> None:
    """Verify switched VPN endpoints follow the endpoint style the Router's firmware selects.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        snake_case (bool): Whether the Router's firmware uses snake_case endpoints.
        expected_sessions (str): Expected OpenVPN session-search endpoint path.
        expected_routes (str): Expected OpenVPN route-search endpoint path.
        expected_client_toggle (str): Expected WireGuard client-toggle endpoint path.
    """
    client, router = make_vpn_router(
        make_client,
        {
            **openvpn_routes(snake_case=snake_case),
            ("POST", expected_client_toggle): {"changed": True},
            ("POST", aiopnsense_vpn.WIREGUARD_SERVICE_RECONFIGURE_ENDPOINT): {"result": "ok"},
        },
        firmware=SNAKE_CASE_FIRMWARE if snake_case else CAMEL_CASE_FIRMWARE,
    )

    await client.get_openvpn()
    assert await client.toggle_vpn_instance("wireguard", "clients", "uuid") is True

    assert router.count("GET", expected_sessions) == 1
    assert router.count("GET", expected_routes) == 1
    assert router.count("POST", expected_client_toggle) == 1


@pytest.mark.asyncio
async def test_update_wireguard_peer_details_endpoint_none_does_not_override() -> None:
    """When endpoint is '(none)' existing endpoint value should remain unchanged."""
    server = {
        "total_bytes_recv": 0,
        "total_bytes_sent": 0,
        "connected_servers": 0,
        "latest_handshake": None,
    }
    peer = {"endpoint": "keep"}
    aiopnsense_module.OPNsenseClient._update_wireguard_peer_details(  # type: ignore[arg-type]
        peer=peer,
        server_or_client=server,
        endpoint="(none)",
        transfer_rx=0,
        transfer_tx=0,
        handshake_time=None,
        is_connected=False,
        connection_counter_key="connected_servers",
    )
    assert peer.get("endpoint") == "keep"


@pytest.mark.asyncio
async def test_update_wireguard_peer_details_latest_handshake() -> None:
    """_update_wireguard_peer_details should update latest_handshake when newer."""
    server: dict = {"total_bytes_recv": 0, "total_bytes_sent": 0, "connected_clients": 0}
    peer: dict = {}
    old_time = datetime.now().astimezone() - timedelta(minutes=10)
    server["latest_handshake"] = old_time
    new_time = datetime.now().astimezone()
    aiopnsense_module.OPNsenseClient._update_wireguard_peer_details(  # type: ignore[arg-type]
        peer=peer,
        server_or_client=server,
        endpoint="1.2.3.4:51820",
        transfer_rx=10,
        transfer_tx=20,
        handshake_time=new_time,
        is_connected=True,
        connection_counter_key="connected_clients",
    )
    assert server.get("latest_handshake") == new_time
    assert server.get("connected_clients") == 1
    assert peer.get("connected") is True


async def test_get_ipsec_success_normalizes_rows(make_client: ClientType) -> None:
    """Normalize connected, disconnected, and malformed IPsec phase 1 rows.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    sessions = {
        "rows": [
            {
                "ikeid": "uuid-up",
                "name": "uuid-up",
                "phase1desc": "Site A",
                "version": "IKEv2",
                "local-addrs": "198.51.100.1",
                "remote-addrs": "203.0.113.5",
                "local-id": "198.51.100.1",
                "remote-id": "203.0.113.5",
                "connected": True,
                "routed": True,
                "install-time": 1234,
                "bytes-in": 100,
                "bytes-out": "200",
                "packets-in": 3,
                "packets-out": "4",
            },
            {
                "ikeid": "uuid-down",
                "phase1desc": "",
                "version": "IKEv1",
                "connected": "0",
                "routed": "0",
                "install-time": 99,
                "bytes-in": "bad",
            },
            {"phase1desc": "no identifier"},
            "not-a-mapping",
        ]
    }
    configured = {"rows": [{"uuid": "uuid-up", "description": "Site A", "enabled": "1"}]}
    client, router = make_vpn_router(make_client, ipsec_routes(sessions, configured))

    ipsec = await client.get_ipsec()

    assert ipsec is not None
    assert set(ipsec) == {"connections", "children"}
    assert set(ipsec["connections"]) == {"uuid-up", "uuid-down"}
    up = ipsec["connections"]["uuid-up"]
    assert up == {
        "uuid": "uuid-up",
        "name": "Site A",
        "connected": True,
        "status": "connected",
        "enabled": True,
        "version": "IKEv2",
        "local_addrs": "198.51.100.1",
        "remote_addrs": "203.0.113.5",
        "local_id": "198.51.100.1",
        "remote_id": "203.0.113.5",
        "routed": True,
        "install_time": 1234,
        "total_bytes_recv": 100,
        "total_bytes_sent": 200,
        "total_packets_recv": 3,
        "total_packets_sent": 4,
    }
    down = ipsec["connections"]["uuid-down"]
    assert down["name"] == "uuid-down"
    assert down["connected"] is False
    assert down["status"] == "disconnected"
    assert down["routed"] is False
    assert down["install_time"] is None
    assert down["total_bytes_recv"] == 0
    assert down["total_packets_sent"] == 0
    assert down["enabled"] is None
    assert router.count("GET", aiopnsense_vpn.IPSEC_PHASE1_SESSIONS_SNAKE_ENDPOINT) == 1
    assert router.count("GET", aiopnsense_vpn.IPSEC_CONNECTIONS_SNAKE_ENDPOINT) == 1


async def test_get_ipsec_reports_configured_connections_without_session(
    make_client: ClientType,
) -> None:
    """Report configured connections without a session, including disabled ones.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    sessions = {"rows": [{"ikeid": "uuid-up", "name": "uuid-up", "connected": True}]}
    configured = {
        "rows": [
            {"uuid": "uuid-up", "description": "Site A", "enabled": "1"},
            {
                "uuid": "uuid-off",
                "description": "Site B",
                "enabled": "0",
                "version": "2",
                "local_addrs": "198.51.100.1",
                "remote_addrs": "203.0.113.9",
            },
        ]
    }
    client, _router = make_vpn_router(make_client, ipsec_routes(sessions, configured))

    ipsec = await client.get_ipsec()

    assert ipsec is not None
    assert set(ipsec["connections"]) == {"uuid-up", "uuid-off"}
    assert ipsec["connections"]["uuid-up"]["enabled"] is True
    assert ipsec["connections"]["uuid-off"] == {
        "uuid": "uuid-off",
        "name": "Site B",
        "connected": False,
        "status": "disconnected",
        "enabled": False,
        "version": "IKEv2",
        "local_addrs": "198.51.100.1",
        "remote_addrs": "203.0.113.9",
        "local_id": None,
        "remote_id": None,
        "routed": False,
        "install_time": None,
        "total_bytes_recv": 0,
        "total_bytes_sent": 0,
        "total_packets_recv": 0,
        "total_packets_sent": 0,
    }


@pytest.mark.parametrize(
    ("enabled", "version", "expected_enabled", "expected_version"),
    [
        ("1", "1", True, "IKEv1"),
        (0, "2", False, "IKEv2"),
        (True, "0", True, None),
        (None, "2", "missing", "IKEv2"),
        ("", "2", "missing", "IKEv2"),
        ("yes", "9", "missing", None),
    ],
)
async def test_get_ipsec_configured_enabled_and_version_normalization(
    make_client: ClientType,
    enabled: Any,
    version: str,
    expected_enabled: bool | str,
    expected_version: str | None,
) -> None:
    """Normalize configured IKE versions and omit unrecognized enabled values.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        enabled (Any): Raw ``enabled`` value of the configuration rows.
        version (str): Raw ``version`` value of the configuration rows.
        expected_enabled (bool | str): Expected ``enabled`` value, or ``"missing"``
            when the key must be omitted.
        expected_version (str | None): Expected ``version`` of a configured-only row.
    """
    sessions = {"rows": [{"ikeid": "uuid-up", "name": "uuid-up", "connected": True}]}
    configured = {
        "rows": [
            {"uuid": "uuid-up", "enabled": enabled, "version": version},
            {"uuid": "uuid-off", "enabled": enabled, "version": version},
        ]
    }
    client, _router = make_vpn_router(make_client, ipsec_routes(sessions, configured))

    ipsec = await client.get_ipsec()

    assert ipsec is not None
    for uuid in ("uuid-up", "uuid-off"):
        row = ipsec["connections"][uuid]
        if expected_enabled == "missing":
            assert "enabled" not in row
        else:
            assert row["enabled"] is expected_enabled
    assert ipsec["connections"]["uuid-off"]["version"] == expected_version


@pytest.mark.parametrize(
    "configured",
    [
        pytest.param(UNAVAILABLE, id="unavailable"),
        pytest.param({"rows": "nope"}, id="rows_not_a_list"),
        pytest.param({}, id="no_rows"),
    ],
)
async def test_get_ipsec_without_connection_inventory_returns_none(
    make_client: ClientType, configured: Any
) -> None:
    """Report an unavailable inventory when connection configuration cannot be read.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        configured (Any): Connections reply: a malformed payload or an HTTP status.
    """
    sessions = {"rows": [{"ikeid": "uuid-up", "name": "uuid-up", "connected": True}]}
    client, _router = make_vpn_router(make_client, ipsec_routes(sessions, configured))

    assert await client.get_ipsec() is None


@pytest.mark.parametrize(
    "malformed_row",
    [pytest.param(None, id="non_mapping_row"), pytest.param({"uuid": ""}, id="empty_uuid")],
)
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_get_ipsec_rejects_malformed_connection_rows(
    make_client: ClientType,
    malformed_row: Any,
    throw_errors: bool,
) -> None:
    """Reject a malformed connection record even beside a valid disabled row.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        malformed_row (Any): Invalid configured connection row.
        throw_errors (bool): Whether strict request errors are enabled.
    """
    sessions = {"rows": [{"ikeid": "uuid-up", "name": "uuid-up", "connected": True}]}
    configured = {"rows": [{"uuid": "uuid-disabled", "enabled": "0"}, malformed_row]}
    client, _router = make_vpn_router(
        make_client, ipsec_routes(sessions, configured), throw_errors=throw_errors
    )

    assert await client.get_ipsec() is None


async def test_get_ipsec_known_unavailable_connection_inventory_reports_no_toggle(
    make_client: ClientType,
) -> None:
    """Report ``enabled=None`` when the connections endpoint is missing.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    sessions = {"rows": [{"ikeid": "uuid-up", "name": "uuid-up", "connected": True}]}
    client, router = make_vpn_router(make_client, ipsec_routes(sessions, NOT_FOUND))

    ipsec = await client.get_ipsec()

    assert ipsec is not None
    assert set(ipsec["connections"]) == {"uuid-up"}
    assert ipsec["connections"]["uuid-up"]["enabled"] is None
    assert router.count("GET", aiopnsense_vpn.IPSEC_PHASE1_SESSIONS_SNAKE_ENDPOINT) == 1


async def test_get_ipsec_children_aggregate_phase2_sessions(make_client: ClientType) -> None:
    """Aggregate phase 2 SAs per child and keep configured children without SAs.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    sessions = {
        "rows": [
            {"ikeid": "ike-1", "name": "ike-1", "connected": True},
            {"ikeid": "1", "name": "con1", "connected": True},
            {"ikeid": "ike-down", "name": "ike-down", "connected": False},
        ]
    }
    children_config = {
        "rows": [
            {
                "uuid": "child-a",
                "description": "Site A nets",
                "enabled": "1",
                "connection": "ike-1",
                "local_ts": "10.0.2.0/24,10.111.111.0/24",
                "remote_ts": "10.50.0.0/16",
            },
            {
                "uuid": "child-off",
                "description": "Disabled",
                "enabled": "0",
                "connection": "ike-down",
            },
        ]
    }
    phase2: dict[str, dict[str, Any]] = {
        "ike-1": {
            "rows": [
                {
                    "name": "child-a",
                    "state": "INSTALLED",
                    "local-ts": "10.111.111.0/24",
                    "remote-ts": "10.50.0.0/16",
                    "install-time": "155",
                    "bytes-in": "10",
                    "bytes-out": 20,
                    "packets-in": "1",
                    "packets-out": "2",
                },
                {
                    "name": "child-a",
                    "state": "INSTALLED",
                    "local-ts": "10.0.2.0/24",
                    "remote-ts": "10.50.0.0/16",
                    "install-time": "2430",
                    "bytes-in": "100",
                    "bytes-out": "200",
                    "packets-in": 3,
                    "packets-out": 4,
                },
                {
                    "name": "child-a",
                    "state": "REKEYED",
                    "local-ts": "192.0.2.0/24",
                    "install-time": "9999",
                    "bytes-in": "1",
                },
                {"state": "INSTALLED"},
                "not-a-mapping",
            ]
        },
        "con1": {
            "rows": [
                {
                    "name": "con1-legacy",
                    "phase2desc": "Legacy P2",
                    "ikeid": "1",
                    "state": "INSTALLED",
                    "local-ts": "10.0.0.0/8",
                    "remote-ts": "172.16.1.0/24",
                    "install-time": "20",
                }
            ]
        },
    }

    def phase2_rows(*, json: dict[str, str], **_kwargs: Any) -> Any:
        """Return the phase 2 SA rows for the phase 1 session the request names.

        Args:
            json (dict[str, str]): JSON request body naming the phase 1 session.
            _kwargs (Any): Other request parameters passed by the transport.

        Returns:
            Any: Phase 2 sessions reply.
        """
        return phase2[json["id"]]

    client, router = make_vpn_router(
        make_client, ipsec_routes(sessions, children=children_config, phase2=phase2_rows)
    )

    ipsec = await client.get_ipsec()

    assert ipsec is not None
    assert sorted(
        body["id"]
        for body in router.bodies("POST", aiopnsense_vpn.IPSEC_PHASE2_SESSIONS_SNAKE_ENDPOINT)
    ) == ["con1", "ike-1"]
    children = ipsec["children"]
    assert set(children) == {"child-a", "child-off", "con1-legacy"}
    assert children["child-a"] == {
        "uuid": "child-a",
        "name": "Site A nets",
        "connection": "ike-1",
        "enabled": True,
        "connected": True,
        "status": "connected",
        "installed_sas": 2,
        "local_ts": "10.0.2.0/24,10.111.111.0/24",
        "remote_ts": "10.50.0.0/16",
        "active_local_ts": ["10.0.2.0/24", "10.111.111.0/24"],
        "active_remote_ts": ["10.50.0.0/16"],
        "install_time": 2430,
        "total_bytes_recv": 111,
        "total_bytes_sent": 220,
        "total_packets_recv": 4,
        "total_packets_sent": 6,
    }
    off = children["child-off"]
    assert off["enabled"] is False
    assert off["status"] == "disconnected"
    assert off["installed_sas"] == 0
    assert off["install_time"] is None
    legacy = children["con1-legacy"]
    assert legacy["name"] == "Legacy P2"
    assert legacy["connection"] == "1"
    assert legacy["enabled"] is None
    assert legacy["status"] == "connected"


@pytest.mark.parametrize(
    ("children_status", "phase2_payload", "expected_children"),
    [
        (UNAVAILABLE, {"rows": []}, None),
        (NOT_FOUND, {"rows": [{"name": "child-x", "state": "INSTALLED"}]}, {"child-x"}),
        (NOT_FOUND, {}, None),
        (NOT_FOUND, {"rows": "nope"}, None),
    ],
)
async def test_get_ipsec_children_fail_closed(
    make_client: ClientType,
    children_status: int,
    phase2_payload: dict[str, Any],
    expected_children: set[str] | None,
) -> None:
    """Omit ``children`` when phase 2 data cannot be read completely.

    A transient child configuration failure or a malformed phase 2 reply omits the
    key; a child configuration endpoint that is missing falls back to the children
    seen in the sessions API.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        children_status (int): HTTP status of the child configuration endpoint.
        phase2_payload (dict[str, Any]): Phase 2 sessions payload returned by the API.
        expected_children (set[str] | None): Expected child keys, or ``None`` when
            ``children`` must be omitted.
    """
    sessions = {"rows": [{"ikeid": "ike-1", "name": "ike-1", "connected": True}]}
    client, _router = make_vpn_router(
        make_client, ipsec_routes(sessions, children=children_status, phase2=phase2_payload)
    )

    ipsec = await client.get_ipsec()

    assert ipsec is not None
    assert set(ipsec["connections"]) == {"ike-1"}
    if expected_children is None:
        assert "children" not in ipsec
    else:
        assert set(ipsec["children"]) == expected_children
        assert ipsec["children"]["child-x"]["enabled"] is None


@pytest.mark.parametrize("throw_errors", [False, True])
async def test_get_ipsec_phase2_request_failure(
    make_client: ClientType, throw_errors: bool
) -> None:
    """Omit children when a phase 2 request fails, or raise in throw mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Whether strict request errors are enabled.
    """
    sessions = {"rows": [{"ikeid": "ike-1", "name": "ike-1", "connected": True}]}
    client, _router = make_vpn_router(
        make_client,
        ipsec_routes(sessions, phase2=aiohttp.ClientConnectionError("phase 2 failed")),
        throw_errors=throw_errors,
    )

    if throw_errors:
        with pytest.raises(OPNsenseConnectionError):
            await client.get_ipsec()
    else:
        result = await client.get_ipsec()
        assert result is not None
        assert set(result["connections"]) == {"ike-1"}
        assert "children" not in result


async def test_get_ipsec_phase2_cancellation_propagates(make_client: ClientType) -> None:
    """Never swallow cancellation that arrives while a phase 2 sessions request is in flight.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    sessions = {"rows": [{"ikeid": "ike-1", "name": "ike-1", "connected": True}]}
    held = HeldResponse(json_payload={"rows": []})
    client, _router = make_vpn_router(make_client, ipsec_routes(sessions, phase2=held))

    await cancel_while_in_flight(client.get_ipsec(), held)


@pytest.mark.parametrize(
    "malformed_row",
    [None, {"uuid": ""}],
)
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_get_ipsec_malformed_child_config_omits_only_children(
    make_client: ClientType,
    malformed_row: Any,
    throw_errors: bool,
) -> None:
    """Omit child inventory when any configured child row is malformed.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        malformed_row (Any): Invalid configured child row.
        throw_errors (bool): Whether strict request errors are enabled.
    """
    sessions = {"rows": [{"ikeid": "ike-1", "name": "ike-1", "connected": True}]}
    children = {"rows": [{"uuid": "child-disabled", "enabled": "0"}, malformed_row]}
    client, _router = make_vpn_router(
        make_client, ipsec_routes(sessions, children=children), throw_errors=throw_errors
    )

    result = await client.get_ipsec()

    assert result is not None
    assert set(result["connections"]) == {"ike-1"}
    assert "children" not in result


async def test_get_ipsec_children_skip_phase2_without_connected_sessions(
    make_client: ClientType,
) -> None:
    """Report configured children as disconnected without querying phase 2.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    sessions = {"rows": [{"ikeid": "ike-1", "name": "ike-1", "connected": False}]}
    children_config = {"rows": [{"uuid": "child-a", "enabled": "1", "connection": "ike-1"}]}
    client, router = make_vpn_router(make_client, ipsec_routes(sessions, children=children_config))

    ipsec = await client.get_ipsec()

    assert ipsec is not None
    assert ipsec["children"]["child-a"]["status"] == "disconnected"
    assert ipsec["children"]["child-a"]["name"] == "child-a"
    assert router.count("POST", aiopnsense_vpn.IPSEC_PHASE2_SESSIONS_SNAKE_ENDPOINT) == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"rows": {"not": "a list"}},
        {"rows": "nope"},
        {},
    ],
    ids=["rows_mapping", "rows_string", "no_rows"],
)
async def test_get_ipsec_malformed_sessions_payload_returns_none(
    make_client: ClientType, payload: dict[str, Any]
) -> None:
    """Return None when the sessions payload has no list of rows.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        payload (dict[str, Any]): Malformed sessions payload returned by the API.
    """
    client, router = make_vpn_router(make_client, ipsec_routes(payload))

    assert await client.get_ipsec() is None
    assert sent(router) == [("GET", aiopnsense_vpn.IPSEC_PHASE1_SESSIONS_SNAKE_ENDPOINT)]


async def test_get_ipsec_endpoint_unavailable_fails_closed(make_client: ClientType) -> None:
    """Request only the sessions endpoint, in the Router's endpoint style, when it is unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_vpn_router(
        make_client, ipsec_routes(UNAVAILABLE, snake_case=False), firmware=CAMEL_CASE_FIRMWARE
    )

    assert await client.get_ipsec() is None
    assert sent(router) == [("GET", aiopnsense_vpn.IPSEC_PHASE1_SESSIONS_CAMELCASE_ENDPOINT)]


@pytest.mark.parametrize("routes", [None, {}, {"rows": None}, {"rows": []}])
async def test_openvpn_unavailable_routes_omit_client_counts(
    make_client: ClientType, routes: dict[str, Any] | None
) -> None:
    """Keep session status while distinguishing unknown routes from no clients.

    Args:
        make_client (ClientType): Client factory fixture.
        routes (dict[str, Any] | None): Routes payload, or unavailable endpoint.
    """
    client, _router = make_vpn_router(
        make_client,
        openvpn_routes(
            sessions={"rows": [{"type": "server", "id": "server", "status": "connected"}]},
            vpn_routes=UNAVAILABLE if routes is None else routes,
            instances={"rows": [{"role": "server", "uuid": "server", "enabled": "1"}]},
        ),
    )

    result = await client.get_openvpn()

    assert result is not None
    server = result["servers"]["server"]
    assert server["status"] == "up"
    if routes == {"rows": []}:
        assert server["clients"] == []
        assert server["connected_clients"] == 0
    else:
        assert "clients" not in server
        assert "connected_clients" not in server


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        (None, False),
        ("1", True),
        ("0", False),
        ("true", True),
        ("False", False),
        (" yes ", True),
        (1, True),
        (0, False),
    ],
)
def test_ipsec_flag_variants(value: Any, expected: bool) -> None:
    """Interpret boolean-like IPsec session fields consistently.

    Args:
        value (Any): Raw field value reported by the IPsec sessions API.
        expected (bool): Expected normalized boolean.
    """
    assert aiopnsense_vpn.VPNMixin._ipsec_flag(value) is expected


@pytest.mark.parametrize(
    ("vpn_type", "collection", "toggle_reply", "apply_reply", "expected"),
    [
        ("openvpn", "servers", {"changed": False}, None, False),
        ("openvpn", "servers", {"changed": True}, {"result": "ok"}, True),
        ("wireguard", "clients", {"changed": True}, {"result": "ok"}, True),
        ("wireguard", "servers", {"changed": False}, None, False),
        ("ipsec", "connections", {"changed": True}, {"status": "ok"}, True),
        ("ipsec", "connections", {"changed": True}, {"status": "failed"}, False),
        ("ipsec", "connections", {"changed": False}, None, False),
        ("ipsec", "servers", {"changed": True}, None, False),
    ],
)
async def test_toggle_vpn_instance_variants(
    make_client: ClientType,
    vpn_type: str,
    collection: str,
    toggle_reply: dict[str, Any],
    apply_reply: dict[str, Any] | None,
    expected: bool,
) -> None:
    """Flip OpenVPN, WireGuard and IPsec instances and apply only a saved change.

    An unknown collection is rejected before any request is sent.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        vpn_type (str): VPN implementation selected for the toggle request.
        collection (str): Collection containing the instance.
        toggle_reply (dict[str, Any]): Router reply to the save request.
        apply_reply (dict[str, Any] | None): Router reply to the apply request, or
            ``None`` when no apply request is expected.
        expected (bool): Whether the toggle operation should report success.
    """
    endpoints = TOGGLE_ENDPOINTS.get((vpn_type, collection))
    routes: dict[tuple[str, str], Any] = {}
    if endpoints is not None:
        routes[("POST", endpoints[0])] = toggle_reply
        if apply_reply is not None:
            routes[("POST", endpoints[1])] = apply_reply
    client, router = make_vpn_router(make_client, routes)

    assert await client.toggle_vpn_instance(vpn_type, collection, "uuid") is expected

    if endpoints is None:
        assert router.requests == []
        return
    toggle_path, apply_path = endpoints
    assert router.count("POST", toggle_path) == 1
    assert router.count("POST", apply_path) == (apply_reply is not None)


@pytest.mark.parametrize(
    ("vpn_type", "collection", "snake_case", "expected_toggle", "expected_get"),
    [
        ("ipsec", "connections", True, "/api/ipsec/connections/toggle_connection/uuid/1", None),
        ("ipsec", "connections", False, "/api/ipsec/connections/toggleConnection/uuid/1", None),
        (
            "wireguard",
            "clients",
            True,
            "/api/wireguard/client/toggle_client/uuid/1",
            "/api/wireguard/client/get_client/uuid",
        ),
        (
            "wireguard",
            "clients",
            False,
            "/api/wireguard/client/toggleClient/uuid/1",
            "/api/wireguard/client/getClient/uuid",
        ),
        (
            "wireguard",
            "servers",
            True,
            "/api/wireguard/server/toggle_server/uuid/1",
            "/api/wireguard/server/get_server/uuid",
        ),
        (
            "wireguard",
            "servers",
            False,
            "/api/wireguard/server/toggleServer/uuid/1",
            "/api/wireguard/server/getServer/uuid",
        ),
    ],
)
async def test_toggle_vpn_instance_posts_to_firmware_endpoints(
    make_client: ClientType,
    vpn_type: str,
    collection: str,
    snake_case: bool,
    expected_toggle: str,
    expected_get: str | None,
) -> None:
    """Send the explicit target and then apply it using the firmware's endpoints.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        vpn_type (str): VPN service under test.
        collection (str): Collection containing the requested item.
        snake_case (bool): Whether firmware uses snake_case endpoint names.
        expected_toggle (str): Expected toggle endpoint path.
        expected_get (str | None): Expected state-read endpoint, if needed.
    """
    apply_path = f"/api/{vpn_type}/service/reconfigure"
    routes: dict[tuple[str, str], Any] = {
        ("POST", expected_toggle): {"changed": True, "result": "Enabled"},
        ("POST", apply_path): {"status": "ok"} if vpn_type == "ipsec" else {"result": "ok"},
    }
    if expected_get is not None:
        routes[("GET", expected_get)] = {collection[:-1]: {"enabled": "0"}}
    client, router = make_vpn_router(
        make_client, routes, firmware=SNAKE_CASE_FIRMWARE if snake_case else CAMEL_CASE_FIRMWARE
    )

    assert await client.toggle_vpn_instance(vpn_type, collection, "uuid", "on") is True

    assert [path for _method, path in sent(router, "GET")] == (
        [expected_get] if expected_get else []
    )
    assert [path for _method, path in sent(router, "POST")] == [expected_toggle, apply_path]


class FakeVpnToggleApi:
    """Simulate the OPNsense VPN toggle, read, and reconfigure endpoints."""

    def __init__(
        self,
        enabled: str,
        honors_target: bool,
        fail_reconfigure_number: int | None = None,
    ) -> None:
        """Initialize the simulated VPN item.

        Args:
            enabled (str): Stored state.
            honors_target (bool): Whether toggle requests honor ``/0`` and ``/1``.
            fail_reconfigure_number (int | None): One-based service apply that fails.
        """
        self.enabled = enabled
        self.honors_target = honors_target
        self.fail_reconfigure_number = fail_reconfigure_number
        self.toggle_count = 0
        self.mutation_count = 0
        self.reconfigure_count = 0
        self.applied_enabled = enabled

    def routes(self, vpn_type: str, collection: str) -> dict[tuple[str, str], Any]:
        """Return the routes that serve one item's read, toggle and service reconfigure.

        Args:
            vpn_type (str): VPN implementation under test.
            collection (str): Collection that holds the item.

        Returns:
            dict[tuple[str, str], Any]: Routes keyed by ``(method, path)``.
        """
        toggle, apply = TOGGLE_ENDPOINTS[(vpn_type, collection)]
        item = collection[:-1]
        routes: dict[tuple[str, str], Any] = {
            ("POST", apply): partial(
                self.reconfigure, "status" if vpn_type == "ipsec" else "result"
            ),
            ("POST", toggle): partial(self.toggle, None),
            ("POST", f"{toggle}/0"): partial(self.toggle, "0"),
            ("POST", f"{toggle}/1"): partial(self.toggle, "1"),
        }
        if vpn_type == "wireguard":
            routes[("GET", f"/api/wireguard/{item}/get_{item}/uuid")] = partial(self.read, item)
        return routes

    def read(self, item: str, **_kwargs: Any) -> dict[str, Any]:
        """Answer a read of the stored item state.

        Args:
            item (str): Response key, ``client`` or ``server``.
            _kwargs (Any): Request parameters passed by the transport.

        Returns:
            dict[str, Any]: Response shaped like the OPNsense endpoint.
        """
        return {item: {"enabled": self.enabled}}

    def reconfigure(self, result_key: str, **_kwargs: Any) -> dict[str, str]:
        """Answer a service reconfigure, applying the stored state unless it is set to fail.

        Args:
            result_key (str): Response key, ``status`` for IPsec and ``result`` otherwise.
            _kwargs (Any): Request parameters passed by the transport.

        Returns:
            dict[str, str]: Response shaped like the OPNsense endpoint.
        """
        self.reconfigure_count += 1
        if self.reconfigure_count == self.fail_reconfigure_number:
            return {result_key: "failed"}
        self.applied_enabled = self.enabled
        return {result_key: "ok"}

    def toggle(self, target: str | None, **_kwargs: Any) -> dict[str, Any]:
        """Answer a toggle request, honoring an explicit target when the firmware does.

        Args:
            target (str | None): ``0`` or ``1`` from the request path, or ``None`` for a flip.
            _kwargs (Any): Request parameters passed by the transport.

        Returns:
            dict[str, Any]: Response shaped like the OPNsense endpoint.
        """
        self.toggle_count += 1
        new_state = (target if self.honors_target else None) or (
            "0" if self.enabled == "1" else "1"
        )
        changed = new_state != self.enabled
        self.enabled = new_state
        self.mutation_count += int(changed)
        return {"result": "Enabled" if new_state == "1" else "Disabled", "changed": changed}


@pytest.mark.parametrize(
    ("vpn_type", "collection", "honors_target"),
    [
        pytest.param("openvpn", "servers", True, id="openvpn"),
        pytest.param("wireguard", "clients", False, id="wireguard-client"),
        pytest.param("wireguard", "servers", False, id="wireguard-server"),
        pytest.param("ipsec", "connections", True, id="ipsec"),
    ],
)
@pytest.mark.parametrize(
    ("initial", "target", "final", "reconfigures"),
    [
        pytest.param("0", "on", "1", 1, id="on"),
        pytest.param("1", "off", "0", 1, id="off"),
        pytest.param("1", "toggle", "0", 1, id="toggle-flips"),
        pytest.param("1", "on", "1", 0, id="already-on"),
    ],
)
async def test_toggle_vpn_instance_reaches_requested_state(
    make_client: ClientType,
    vpn_type: str,
    collection: str,
    honors_target: bool,
    initial: str,
    target: str,
    final: str,
    reconfigures: int,
) -> None:
    """Reach the requested state and send the apply request only after a real change.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        vpn_type (str): VPN implementation under test.
        collection (str): VPN collection under test.
        honors_target (bool): Whether simulated firmware honors the target.
        initial (str): Initial stored state.
        target (str): Requested target state.
        final (str): Expected final stored state.
        reconfigures (int): Expected service reconfigure count.
    """
    api = FakeVpnToggleApi(initial, honors_target)
    client, router = make_vpn_router(make_client, api.routes(vpn_type, collection))
    _toggle_path, apply_path = TOGGLE_ENDPOINTS[(vpn_type, collection)]

    result = await client.toggle_vpn_instance(vpn_type, collection, "uuid", target)

    assert result is True
    assert api.enabled == api.applied_enabled == final
    assert router.count("POST", apply_path) == reconfigures
    if vpn_type == "wireguard" and reconfigures == 0:
        assert api.toggle_count == 0


@pytest.mark.parametrize(
    ("correction", "expected"),
    [
        pytest.param({"changed": True, "result": "Enabled"}, True, id="corrected"),
        pytest.param({"result": "failed"}, False, id="retry-needed"),
    ],
)
async def test_toggle_vpn_instance_wireguard_handles_change_after_read(
    make_client: ClientType, correction: dict[str, object], expected: bool
) -> None:
    """Correct the legacy firmware's wrong-way flip or retain its unapplied save.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        correction (dict[str, object]): Response from the corrective toggle.
        expected (bool): Whether the original target request succeeds.
    """
    toggle = "/api/wireguard/client/toggle_client/uuid/1"
    apply = "/api/wireguard/service/reconfigure"
    toggle_replies = iter([{"changed": True, "result": "Disabled"}, correction])

    client, router = make_vpn_router(
        make_client,
        {
            ("GET", "/api/wireguard/client/get_client/uuid"): {"client": {"enabled": "0"}},
            ("POST", toggle): lambda **_kwargs: next(toggle_replies),
            ("POST", apply): {"result": "ok"},
        },
    )

    assert await client.toggle_vpn_instance("wireguard", "clients", "uuid", "on") is expected
    if not expected:
        assert router.count("POST", toggle) == 2
        assert router.count("POST", apply) == 0
        assert await client.toggle_vpn_instance("wireguard", "clients", "uuid", "off") is True
    assert router.count("POST", toggle) == 2
    assert router.count("POST", apply) == 1


@pytest.mark.parametrize(
    ("vpn_type", "collection", "honors_target", "external_change"),
    [
        pytest.param("openvpn", "servers", True, False, id="openvpn-matching"),
        pytest.param("openvpn", "servers", True, True, id="openvpn-external-change"),
        pytest.param("ipsec", "connections", True, False, id="ipsec-matching"),
        pytest.param("wireguard", "clients", False, False, id="wireguard-matching"),
    ],
)
async def test_toggle_vpn_instance_retries_known_failed_apply(
    make_client: ClientType,
    vpn_type: str,
    collection: str,
    honors_target: bool,
    external_change: bool,
) -> None:
    """Retry a failed apply after revalidating the requested item state.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        vpn_type (str): VPN service under test.
        collection (str): Collection containing the VPN instance.
        honors_target (bool): Whether the fake endpoint honors explicit targets.
        external_change (bool): Whether another writer changes the saved state before retry.
    """
    api = FakeVpnToggleApi("0", honors_target, fail_reconfigure_number=1)
    client, _router = make_vpn_router(make_client, api.routes(vpn_type, collection))

    assert await client.toggle_vpn_instance(vpn_type, collection, "uuid", "on") is False
    assert (api.enabled, api.applied_enabled, api.toggle_count, api.reconfigure_count) == (
        "1",
        "0",
        1,
        1,
    )

    if external_change:
        # The later target request revalidates after an external writer.
        api.enabled = "0"
    assert await client.toggle_vpn_instance(vpn_type, collection, "uuid", "on") is True
    assert api.enabled == api.applied_enabled == "1"
    assert api.reconfigure_count == 2
    assert api.mutation_count == (2 if external_change else 1)

    assert await client.toggle_vpn_instance(vpn_type, collection, "uuid", "on") is True
    assert api.reconfigure_count == 2
    assert api.mutation_count == (2 if external_change else 1)


@pytest.mark.parametrize("collection", ["clients", "servers"])
async def test_wireguard_toggle_rejects_empty_uuid(
    make_client: ClientType, collection: str
) -> None:
    """Reject an empty item UUID before reading or changing configuration.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        collection (str): WireGuard collection under test.
    """
    client, router = make_vpn_router(
        make_client, FakeVpnToggleApi("1", False).routes("wireguard", collection)
    )

    assert await client.toggle_vpn_instance("wireguard", collection, "", "on") is False

    assert router.requests == []


@pytest.mark.parametrize(
    ("vpn_type", "item", "read", "toggle", "expected_posts"),
    [
        pytest.param("openvpn", "servers", {}, {"result": "failed"}, 1, id="missing-openvpn"),
        pytest.param("ipsec", "connections", {}, {"result": "failed"}, 1, id="missing-ipsec"),
        pytest.param("wireguard", "clients", {}, {}, 0, id="missing-wireguard"),
        pytest.param(
            "wireguard", "clients", {"client": {"enabled": []}}, {}, 0, id="malformed-read"
        ),
        pytest.param(
            "wireguard",
            "clients",
            {"client": {"enabled": "0"}},
            {"changed": True, "result": []},
            1,
            id="malformed-toggle",
        ),
    ],
)
async def test_toggle_vpn_instance_rejects_invalid_item_or_response(
    make_client: ClientType,
    vpn_type: str,
    item: str,
    read: dict[str, object],
    toggle: dict[str, object],
    expected_posts: int,
) -> None:
    """Reject a missing item or malformed response without sending the service apply.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        vpn_type (str): VPN service under test.
        item (str): Item collection.
        read (dict[str, object]): Item state response, if read.
        toggle (dict[str, object]): Toggle response, if dispatched.
        expected_posts (int): How many toggle requests can safely be dispatched.
    """
    toggle_path, apply_path = TOGGLE_ENDPOINTS[(vpn_type, item)]
    routes: dict[tuple[str, str], Any] = {
        ("GET", "/api/wireguard/client/get_client/uuid"): read,
        ("POST", f"{toggle_path}/1"): toggle,
    }
    client, router = make_vpn_router(make_client, routes)

    assert await client.toggle_vpn_instance(vpn_type, item, "uuid", "on") is False

    assert len(sent(router, "POST")) == expected_posts
    assert router.count("POST", apply_path) == 0


async def test_toggle_vpn_instance_wireguard_serializes_concurrent_calls(
    make_client: ClientType,
) -> None:
    """Serialize same-client state reads through reconfigure for WireGuard calls.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    read_path = "/api/wireguard/client/get_client/uuid"
    api = FakeVpnToggleApi("0", honors_target=False)
    held = HeldResponse(json_payload={"client": {"enabled": "0"}})

    routes = api.routes("wireguard", "clients")
    simulated_read = routes[("GET", read_path)]
    routes[("GET", read_path)] = lambda **kwargs: (
        simulated_read(**kwargs) if held.entered.is_set() else held
    )
    client, router = make_vpn_router(make_client, routes)
    tasks: list[asyncio.Task[bool]] = []
    try:
        tasks.append(
            asyncio.create_task(client.toggle_vpn_instance("wireguard", "clients", "uuid", "on"))
        )
        await asyncio.wait_for(held.entered.wait(), timeout=1)
        tasks.append(
            asyncio.create_task(
                client.toggle_vpn_instance("wireguard", "clients", "uuid", "on"), eager_start=True
            )
        )
        await asyncio.sleep(0)
        assert router.count("GET", read_path) == 1

        held.release.set()
        assert await asyncio.wait_for(asyncio.gather(*tasks), timeout=3) == [True, True]
        assert (api.enabled, api.toggle_count, api.reconfigure_count) == ("1", 1, 1)
    finally:
        held.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
