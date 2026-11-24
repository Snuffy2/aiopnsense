"""Tests for `aiopnsense.vpn`."""

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import aiohttp
import pytest

import aiopnsense as aiopnsense_module
from aiopnsense import (
    Access,
    OPNsenseClient,
    OPNsensePrivilegeMissing,
    result_access,
    vpn as aiopnsense_vpn,
)
from aiopnsense.exceptions import OPNsenseConnectionError
from tests.conftest import FIRMWARE_STATUS_PATH, FakeRouter, HeldResponse, make_router_client

IPSEC_CONNECTIONS_SNAKE_PATH = "/api/ipsec/connections/search_connection"
IPSEC_PHASE1_SESSIONS_CAMELCASE_PATH = "/api/ipsec/sessions/searchPhase1"
IPSEC_PHASE1_SESSIONS_SNAKE_PATH = "/api/ipsec/sessions/search_phase1"
IPSEC_PHASE2_SESSIONS_SNAKE_PATH = "/api/ipsec/sessions/search_phase2"
OPENVPN_INSTANCE_DETAILS_PATH_PREFIX = "/api/openvpn/instances/get/"
OPENVPN_SESSIONS_SNAKE_PATH = "/api/openvpn/service/search_sessions"
WIREGUARD_SUMMARY_PATH = "/api/wireguard/service/show"

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
        OPENVPN_SESSIONS_SNAKE_PATH if snake_case else "/api/openvpn/service/searchSessions"
    )
    routes_path = (
        "/api/openvpn/service/search_routes" if snake_case else "/api/openvpn/service/searchRoutes"
    )
    routes: dict[tuple[str, str], Any] = {
        ("GET", sessions_path): {"rows": []} if sessions is None else sessions,
        ("GET", routes_path): {"rows": []} if vpn_routes is None else vpn_routes,
        ("GET", "/api/openvpn/export/providers"): {} if providers is None else providers,
        ("GET", "/api/openvpn/instances/search"): (
            {"rows": []} if instances is None else instances
        ),
    }
    for uuid, reply in (details or {}).items():
        routes[("GET", f"{OPENVPN_INSTANCE_DETAILS_PATH_PREFIX}{uuid}")] = reply
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
        ("GET", WIREGUARD_SUMMARY_PATH): ({"rows": []} if summary is None else summary),
        ("GET", "/api/wireguard/client/get"): (
            {"client": {"clients": {"client": {}}}} if clients is None else clients
        ),
        ("GET", "/api/wireguard/server/get"): (
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
            IPSEC_PHASE1_SESSIONS_SNAKE_PATH,
            IPSEC_CONNECTIONS_SNAKE_PATH,
            "/api/ipsec/connections/search_child",
            IPSEC_PHASE2_SESSIONS_SNAKE_PATH,
        )
        if snake_case
        else (
            IPSEC_PHASE1_SESSIONS_CAMELCASE_PATH,
            "/api/ipsec/connections/searchConnection",
            "/api/ipsec/connections/searchChild",
            "/api/ipsec/sessions/searchPhase2",
        )
    )
    sessions_path, connections_path, children_path, phase2_path = names
    return {
        ("GET", sessions_path): sessions,
        ("GET", connections_path): {"rows": []} if connections is None else connections,
        ("GET", children_path): {"rows": []} if children is None else children,
        ("POST", phase2_path): {"rows": []} if phase2 is None else phase2,
    }


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
    details_path = f"{OPENVPN_INSTANCE_DETAILS_PATH_PREFIX}uuid1"
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
    details_path = f"{OPENVPN_INSTANCE_DETAILS_PATH_PREFIX}srv1"
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


@pytest.fixture
def wireguard_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Freeze the clock used to classify WireGuard handshakes.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing the VPN clock.

    Returns:
        datetime: Fixed reference time in UTC.
    """
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    clock = type("Clock", (), {"now": staticmethod(lambda: now)})
    monkeypatch.setattr(aiopnsense_vpn, "datetime", clock)
    return now


@pytest.mark.parametrize(
    "handshake_age",
    [
        pytest.param(timedelta(seconds=30), id="fresh-handshake"),
        pytest.param(timedelta(minutes=10), id="stale-handshake"),
        pytest.param(None, id="empty-summary"),
    ],
)
async def test_get_wireguard_reports_live_details_for_each_configured_view(
    make_client: ClientType, wireguard_now: datetime, handshake_age: timedelta | None
) -> None:
    """Keep live status on the matching link and retain the idle link in both views.

    Args:
        make_client (ClientType): Fixture factory returning client instances.
        wireguard_now (datetime): Fixed reference time for handshake status.
        handshake_age (timedelta | None): Age of the reported handshake, or no summary rows.
    """
    handshake = wireguard_now - handshake_age if handshake_age is not None else None
    endpoint = "192.0.2.1:51820"
    rows: list[dict[str, Any]] = []
    if handshake is not None:
        rows = [
            {"type": "interface", "public-key": "server-key-1", "status": "up"},
            {
                "type": "peer",
                "public-key": "client-key",
                "if": "wg1",
                "endpoint": endpoint,
                "transfer-rx": "100",
                "transfer-tx": "250",
                "latest-handshake": int(handshake.timestamp()),
            },
        ]
    client_config = {
        "name": "Client",
        "pubkey": "client-key",
        "servers": {
            "server-1": {"selected": "1", "value": "Server 1"},
            "server-2": {"selected": "1", "value": "Server 2"},
        },
    }
    server_config = {
        "server-1": {
            "name": "Server 1",
            "pubkey": "server-key-1",
            "instance": "1",
            "peers": {"client-uuid": {"selected": "1", "value": "Client"}},
        },
        "server-2": {
            "name": "Server 2",
            "pubkey": "server-key-2",
            "instance": "2",
            "peers": {"client-uuid": {"selected": "1", "value": "Client"}},
        },
    }
    client, router = make_vpn_router(
        make_client,
        wireguard_routes(
            summary={"rows": rows},
            clients={"client": {"clients": {"client": {"client-uuid": client_config}}}},
            servers={"server": {"servers": {"server": server_config}}},
        ),
    )

    result = await client.get_wireguard()

    assert result is not None
    assert result_access(result) is Access.CONFIGURATION
    server_1 = result["servers"]["server-1"]
    server_2 = result["servers"]["server-2"]
    configured_client = result["clients"]["client-uuid"]
    assert server_1["uuid"] == "server-1"
    assert server_2["uuid"] == "server-2"
    assert configured_client["uuid"] == "client-uuid"

    connected = handshake_age == timedelta(seconds=30)
    recv, sent = (100, 250) if handshake is not None else (0, 0)
    server_1_links = {link["uuid"]: link for link in server_1["clients"]}
    server_2_links = {link["uuid"]: link for link in server_2["clients"]}
    client_links = {link["uuid"]: link for link in configured_client["servers"]}
    assert list(client_links) == ["server-1", "server-2"]
    for link in (server_1_links["client-uuid"], client_links["server-1"]):
        assert link["connected"] is connected
        if handshake is not None:
            assert link["endpoint"] == endpoint
            assert link["bytes_recv"] == 100
            assert link["bytes_sent"] == 250
            assert link["latest_handshake"] == handshake
        else:
            assert {"endpoint", "bytes_recv", "bytes_sent", "latest_handshake"}.isdisjoint(link)

    for link in (server_2_links["client-uuid"], client_links["server-2"]):
        assert link["connected"] is False
        assert {"endpoint", "bytes_recv", "bytes_sent", "latest_handshake"}.isdisjoint(link)

    assert server_1["connected_clients"] == int(connected)
    assert configured_client["connected_servers"] == int(connected)
    for parent in (server_1, configured_client):
        assert parent["total_bytes_recv"] == recv
        assert parent["total_bytes_sent"] == sent
        assert parent.get("latest_handshake") == (handshake if connected else None)
    assert server_1.get("status") == ("up" if handshake is not None else None)
    assert server_2["connected_clients"] == 0
    assert server_2["total_bytes_recv"] == 0
    assert server_2["total_bytes_sent"] == 0
    assert server_2.get("latest_handshake") is None

    router.routes[("GET", WIREGUARD_SUMMARY_PATH)] = {"rows": {}}
    assert await client.get_wireguard() is None


@pytest.mark.parametrize("selecting_view", ["servers", "clients"])
async def test_get_wireguard_enriches_one_sided_links(
    make_client: ClientType, wireguard_now: datetime, selecting_view: str
) -> None:
    """Enrich only links selected by each view and ignore unmatched rows.

    Args:
        make_client (ClientType): Fixture factory returning client instances.
        wireguard_now (datetime): Fixed reference time for handshake status.
        selecting_view (str): Parent inventory that selected the link.
    """
    handshake = wireguard_now - timedelta(seconds=30)
    peer_row = {
        "type": "peer",
        "public-key": "client-key",
        "if": "wg1",
        "endpoint": "192.0.2.1:51820",
        "transfer-rx": "35",
        "transfer-tx": "80",
        "latest-handshake": int(handshake.timestamp()),
    }
    client_config = {
        "pubkey": "client-key",
        "servers": {
            "server-uuid": {"selected": str(int(selecting_view == "clients")), "value": "Server"}
        },
    }
    server_config = {
        "name": "Server",
        "instance": "1",
        "peers": {
            "client-uuid": {"selected": str(int(selecting_view == "servers")), "value": "Client"}
        },
    }
    client, _router = make_vpn_router(
        make_client,
        wireguard_routes(
            summary={"rows": [peer_row, {**peer_row, "public-key": "unconfigured-client"}]},
            clients={"client": {"clients": {"client": {"client-uuid": client_config}}}},
            servers={"server": {"servers": {"server": {"server-uuid": server_config}}}},
        ),
    )

    result = await client.get_wireguard()

    assert result is not None
    for parent_view, parent_uuid, link_view, link_uuid in (
        ("servers", "server-uuid", "clients", "client-uuid"),
        ("clients", "client-uuid", "servers", "server-uuid"),
    ):
        parent = result[parent_view][parent_uuid]
        links = {link["uuid"]: link for link in parent[link_view]}
        if selecting_view == parent_view:
            assert list(links) == [link_uuid]
            link = links[link_uuid]
            assert link["endpoint"] == "192.0.2.1:51820"
            assert link["bytes_recv"] == 35
            assert link["bytes_sent"] == 80
            assert link["latest_handshake"] == handshake
            assert link["connected"] is True
            assert parent[f"connected_{link_view}"] == 1
            assert parent["total_bytes_recv"] == 35
            assert parent["total_bytes_sent"] == 80
            assert parent["latest_handshake"] == handshake
        else:
            assert links == {}
            assert parent[f"connected_{link_view}"] == 0
            assert parent["total_bytes_recv"] == 0
            assert parent["total_bytes_sent"] == 0
            assert parent.get("latest_handshake") is None


@pytest.mark.parametrize("denied_path", ["/api/wireguard/client/get", "/api/wireguard/server/get"])
async def test_get_wireguard_status_access_links_live_rows(
    make_client: ClientType, wireguard_now: datetime, denied_path: str
) -> None:
    """Use public-key identity and live links after either configuration read is denied.

    Args:
        make_client (ClientType): Client factory fixture.
        wireguard_now (datetime): Fixed reference time for handshake status.
        denied_path (str): Configuration endpoint that starts returning 403.
    """
    handshake = wireguard_now - timedelta(seconds=30)
    rows = [
        {
            "type": "peer",
            "public-key": "client-public-key",
            "name": "Client",
            "if": "wg1",
            "transfer-rx": "15",
            "transfer-tx": "20",
            "latest-handshake": int(handshake.timestamp()),
        },
        {
            "type": "interface",
            "public-key": "server-public-key",
            "if": "wg1",
            "ifname": "Server 1",
            "status": "up",
        },
        {
            "type": "interface",
            "public-key": "other-server-public-key",
            "if": "wg2",
            "name": "Server 2",
        },
        {
            "type": "peer",
            "public-key": "client-public-key",
            "name": "Client",
            "if": "wg2",
            "transfer-rx": "5",
        },
    ]
    client, router = make_vpn_router(
        make_client,
        wireguard_routes(
            summary={"rows": rows},
            clients={
                "client": {"clients": {"client": {"client-id": {"pubkey": "client-public-key"}}}}
            },
            servers={
                "server": {"servers": {"server": {"server-id": {"pubkey": "server-public-key"}}}}
            },
        ),
    )
    configured = await client.get_wireguard()
    assert configured is not None
    assert result_access(configured) is Access.CONFIGURATION
    assert configured["clients"]["client-id"]["key_hash"] == "53f6b5178afbdb4e"
    assert configured["servers"]["server-id"]["key_hash"] == "006f9c9fb3b751aa"

    router.routes[("GET", denied_path)] = 403
    for _ in range(2):
        result = await client.get_wireguard()
        assert result is not None
        assert result_access(result) is Access.STATUS
        assert set(result["servers"]) == {"006f9c9fb3b751aa", "d3ef32285d74752e"}
        assert set(result["clients"]) == {"53f6b5178afbdb4e"}
        for inventory in result.values():
            for key_hash, entry in inventory.items():
                assert entry["uuid"] is None
                assert entry["key_hash"] == key_hash
                assert {"enabled", "tunnel_addresses", "dns_servers"}.isdisjoint(entry)
        server = result["servers"]["006f9c9fb3b751aa"]
        assert server["name"] == "Server 1"
        assert server["status"] == "up"
        assert result["servers"]["d3ef32285d74752e"]["status"] is None
        assert server["connected_clients"] == 1
        assert server["clients"][0]["latest_handshake"] == handshake
        peer = result["clients"]["53f6b5178afbdb4e"]
        assert peer["name"] == "Client"
        assert peer["total_bytes_recv"] == 20
        assert peer["total_bytes_sent"] == 20
        assert peer["connected_servers"] == 1
        assert {link["name"]: link["connected"] for link in peer["servers"]} == {
            "Server 1": True,
            "Server 2": False,
        }
    assert router.count("GET", denied_path) == 2


@pytest.mark.parametrize("reverse_rows", [False, True])
async def test_get_wireguard_status_access_aggregates_shared_server_key(
    make_client: ClientType, wireguard_now: datetime, reverse_rows: bool
) -> None:
    """Aggregate links and counters when status interfaces share a public key.

    Args:
        make_client (ClientType): Client factory fixture.
        wireguard_now (datetime): Fixed reference time for handshake status.
        reverse_rows (bool): Whether the Router reports the rows in reverse order.
    """
    handshake = int((wireguard_now - timedelta(seconds=30)).timestamp())
    rows = [
        {
            "type": "interface",
            "public-key": "shared-server-key",
            "if": "wg0",
            "ifname": "Server 0",
            "status": "up",
        },
        {
            "type": "peer",
            "public-key": "client-key-0",
            "name": "Client 0",
            "if": "wg0",
            "transfer-rx": "10",
            "transfer-tx": "11",
            "latest-handshake": handshake,
        },
        {
            "type": "interface",
            "public-key": "shared-server-key",
            "if": "wg1",
            "ifname": "Server 1",
            "status": "down",
        },
        {
            "type": "peer",
            "public-key": "client-key-1",
            "name": "Client 1",
            "if": "wg1",
            "transfer-rx": "20",
            "transfer-tx": "21",
            "latest-handshake": handshake,
        },
    ]
    if reverse_rows:
        rows.reverse()
    client, _router = make_vpn_router(
        make_client,
        wireguard_routes(summary={"rows": rows}, clients=403, servers=403),
    )

    result = await client.get_wireguard()

    assert result is not None
    assert result_access(result) is Access.STATUS
    assert set(result["servers"]) == {"cc021956c09576eb"}
    server = result["servers"]["cc021956c09576eb"]
    assert server["key_hash"] == "cc021956c09576eb"
    assert server["uuid"] is None
    assert (server["name"], server["interface"], server["status"]) == ("Server 0", "wg0", "up")
    assert (server["total_bytes_recv"], server["total_bytes_sent"]) == (30, 32)
    assert server["connected_clients"] == 2
    assert {link["pubkey"] for link in server["clients"]} == {"client-key-0", "client-key-1"}
    clients = {entry["pubkey"]: entry for entry in result["clients"].values()}
    assert set(clients) == {"client-key-0", "client-key-1"}
    for public_key, interface, name, received, sent in (
        ("client-key-0", "wg0", "Server 0", 10, 11),
        ("client-key-1", "wg1", "Server 1", 20, 21),
    ):
        peer = clients[public_key]
        assert (peer["total_bytes_recv"], peer["total_bytes_sent"]) == (received, sent)
        (link,) = peer["servers"]
        assert (link["interface"], link["name"]) == (interface, name)


@pytest.mark.parametrize(
    ("statuses", "expected_interface", "expected_status"),
    [
        (("up", "up"), "wg0", "up"),
        (("down", "down"), "wg0", "down"),
        ((None, None), "wg0", None),
        ((None, "up"), "wg1", "up"),
        (("down", None), "wg1", None),
    ],
)
async def test_get_wireguard_status_access_shared_server_status(
    make_client: ClientType,
    statuses: tuple[str | None, str | None],
    expected_interface: str,
    expected_status: str | None,
) -> None:
    """Report the shared server status even when peer data or interface status is absent.

    Args:
        make_client (ClientType): Client factory fixture.
        statuses (tuple[str | None, str | None]): Statuses of the two interface rows.
        expected_interface (str): Interface representing the shared server.
        expected_status (str | None): Status reported for the shared server.
    """
    rows = [
        {
            "type": "interface",
            "if": f"wg{index}",
            "public-key": "shared-server-key",
            **({"status": status} if status is not None else {}),
        }
        for index, status in enumerate(statuses)
    ]
    client, router = make_vpn_router(
        make_client,
        wireguard_routes(summary={"rows": rows}, clients=403, servers=403),
    )

    for ordered_rows in (rows, rows[::-1]):
        router.routes[("GET", WIREGUARD_SUMMARY_PATH)] = {"rows": ordered_rows}
        result = await client.get_wireguard()
        assert result is not None
        server = result["servers"]["cc021956c09576eb"]
        assert (server["interface"], server["status"]) == (expected_interface, expected_status)


async def test_get_wireguard_retains_duplicate_and_incomplete_links(
    make_client: ClientType,
) -> None:
    """Preserve matches for duplicate interfaces and equality of malformed JSON keys.

    Args:
        make_client (ClientType): Fixture factory returning client instances.
    """
    client_config = {
        "pubkey": ["malformed", {"number": 1}],
        "servers": {
            "server-1": {"selected": "1", "value": "Server 1"},
            "server-2": {"selected": "1", "value": "Server 2"},
            "missing-server": {"selected": "1", "value": "Missing server"},
        },
    }
    server_config = {
        "instance": "1",
        "peers": {
            "client-uuid": {"selected": "1", "value": "Client"},
            "missing-client": {"selected": "1", "value": "Missing client"},
        },
    }
    row = {
        "type": "peer",
        "public-key": ["malformed", {"number": True}],
        "if": "wg1",
        "transfer-rx": "15",
        "transfer-tx": "20",
    }
    client, _router = make_vpn_router(
        make_client,
        wireguard_routes(
            summary={"rows": [row, {**row, "if": ["wg1"]}]},
            clients={"client": {"clients": {"client": {"client-uuid": client_config}}}},
            servers={
                "server": {
                    "servers": {
                        "server": {
                            "server-1": {**server_config, "name": "Server 1"},
                            "server-2": {**server_config, "name": "Server 2"},
                        },
                    }
                }
            },
        ),
    )

    result = await client.get_wireguard()

    assert result is not None
    for server_uuid in ("server-1", "server-2"):
        server = result["servers"][server_uuid]
        assert (server["total_bytes_recv"], server["total_bytes_sent"]) == (15, 20)
        assert [link["uuid"] for link in server["clients"]] == ["client-uuid", "missing-client"]
        active, missing = server["clients"]
        assert (active["bytes_recv"], active["bytes_sent"]) == (15, 20)
        assert missing == {
            "uuid": "missing-client",
            "name": "Missing client",
            "pubkey": None,
            "connected": False,
        }

    configured_client = result["clients"]["client-uuid"]
    assert (configured_client["total_bytes_recv"], configured_client["total_bytes_sent"]) == (
        30,
        40,
    )
    assert [link["uuid"] for link in configured_client["servers"]] == [
        "server-1",
        "server-2",
        "missing-server",
    ]
    first, second, missing = configured_client["servers"]
    for link in (first, second):
        assert (link["bytes_recv"], link["bytes_sent"]) == (15, 20)
    assert missing == {"uuid": "missing-server", "name": "Missing server", "connected": False}


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
    assert sent(router) == [("GET", OPENVPN_SESSIONS_SNAKE_PATH)]


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


async def test_get_openvpn_routes_unexpected_failure_keeps_partial_data(
    make_client: ClientType,
) -> None:
    """Keep partial data when the optional routes request fails with an unexpected error.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = make_vpn_router(
        make_client,
        openvpn_routes(
            sessions={"rows": [{"type": "server", "id": "server", "status": "connected"}]},
            vpn_routes=ValueError("routes unreadable"),
            instances={"rows": [{"role": "server", "uuid": "server", "enabled": "1"}]},
        ),
    )

    result = await client.get_openvpn()

    assert result is not None
    server = result["servers"]["server"]
    assert server["status"] == "up"
    assert "clients" not in server


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


async def test_get_openvpn_status_access_uses_session_inventory(make_client: ClientType) -> None:
    """Use visible server sessions and skip instance details after a cached denial.

    Args:
        make_client (ClientType): Fixture factory returning client instances.
    """
    client, router = make_vpn_router(
        make_client,
        openvpn_routes(
            sessions={
                "rows": [
                    {"id": "visible", "type": "server", "description": "Visible"},
                    {"id": "client", "type": "client"},
                ]
            },
            providers={"hidden": {"name": "Export only"}},
            instances=403,
        ),
    )

    for _ in range(2):
        result = await client.get_openvpn()
        assert result_access(result) is Access.STATUS
        assert result == {
            "servers": {
                "visible": {
                    "uuid": "visible",
                    "name": "Visible",
                    "status": "down",
                    "clients": [],
                    "connected_clients": 0,
                    "total_bytes_recv": 0,
                    "total_bytes_sent": 0,
                }
            },
            "clients": {},
        }
        assert result_access(deepcopy(result)) is Access.STATUS
        assert result_access(dict(result)) is None
    assert router.count("GET", "/api/openvpn/instances/search") == 1
    assert router.count("GET", "/api/openvpn/instances/get/visible") == 0


@pytest.mark.parametrize(
    ("vpn", "status_path", "config_path"),
    [
        ("openvpn", OPENVPN_SESSIONS_SNAKE_PATH, "/api/openvpn/instances/search"),
        ("openvpn", OPENVPN_SESSIONS_SNAKE_PATH, "/api/openvpn/export/providers"),
        ("wireguard", WIREGUARD_SUMMARY_PATH, "/api/wireguard/client/get"),
        ("wireguard", WIREGUARD_SUMMARY_PATH, "/api/wireguard/server/get"),
        ("ipsec", IPSEC_PHASE1_SESSIONS_SNAKE_PATH, IPSEC_CONNECTIONS_SNAKE_PATH),
        ("ipsec", IPSEC_PHASE1_SESSIONS_SNAKE_PATH, "/api/ipsec/connections/search_child"),
    ],
)
async def test_vpn_access_preserves_http_error_policy(
    make_client: ClientType, vpn: str, status_path: str, config_path: str
) -> None:
    """Distinguish an absent inventory from strict privilege and status failures.

    Args:
        make_client (ClientType): Client factory fixture.
        vpn (str): VPN getter to exercise.
        status_path (str): Required status endpoint.
        config_path (str): Configuration endpoint to deny or remove.
    """
    routes = openvpn_routes() | wireguard_routes() | ipsec_routes({"rows": []})
    strict_client, strict_router = make_vpn_router(make_client, routes, throw_errors=True)
    client, router = make_vpn_router(make_client, routes)
    get_vpn = getattr(client, f"get_{vpn}")
    expected: dict[str, dict[str, Any]] = (
        {"connections": {}, "children": {}} if vpn == "ipsec" else {"servers": {}, "clients": {}}
    )
    result = await get_vpn()
    assert result == expected
    assert result_access(result) is Access.CONFIGURATION

    strict_router.routes[("GET", config_path)] = 403
    with pytest.raises(OPNsensePrivilegeMissing):
        await getattr(strict_client, f"get_{vpn}")()

    router.routes[("GET", config_path)] = 404
    result = await get_vpn()
    assert result == expected
    assert result_access(result) is Access.CONFIGURATION

    router.routes[("GET", status_path)] = 403
    assert await get_vpn() is None


async def test_openvpn_provider_denial_only_omits_endpoint(make_client: ClientType) -> None:
    """Preserve Configuration Access when only the provider export is forbidden.

    Args:
        make_client (ClientType): Client factory fixture.
    """
    client, router = make_vpn_router(
        make_client,
        openvpn_routes(
            providers=403,
            instances={"rows": [{"role": "server", "uuid": "server", "enabled": "1"}]},
        ),
    )
    for _ in range(2):
        result = await client.get_openvpn()
        assert result is not None
        assert result_access(result) is Access.CONFIGURATION
        assert result["servers"]["server"]["enabled"] is True
        assert "endpoint" not in result["servers"]["server"]
    assert router.count("GET", "/api/openvpn/export/providers") == 1


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
            ("POST", "/api/wireguard/service/reconfigure"): {"result": "ok"},
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
    assert router.count("GET", IPSEC_PHASE1_SESSIONS_SNAKE_PATH) == 1
    assert router.count("GET", IPSEC_CONNECTIONS_SNAKE_PATH) == 1


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
    assert router.count("GET", IPSEC_PHASE1_SESSIONS_SNAKE_PATH) == 1


async def test_get_ipsec_status_access_only_reports_sessions(make_client: ClientType) -> None:
    """Return live IKE and child entries without configuration after a cached denial.

    Args:
        make_client (ClientType): Client factory fixture.
    """
    client, router = make_vpn_router(
        make_client,
        ipsec_routes(
            {"rows": [{"ikeid": "visible", "name": "session", "connected": True}]},
            connections=403,
            children={"rows": [{"uuid": "hidden", "enabled": "1"}]},
            phase2={
                "rows": [
                    {
                        "name": "active-child",
                        "ikeid": "visible",
                        "state": "INSTALLED",
                        "bytes-in": "418",
                    }
                ]
            },
        ),
    )

    for _ in range(2):
        result = await client.get_ipsec()
        assert result is not None
        assert result_access(result) is Access.STATUS
        assert set(result["connections"]) == {"visible"}
        assert "enabled" not in result["connections"]["visible"]
        assert set(result["children"]) == {"active-child"}
        child = result["children"]["active-child"]
        assert "enabled" not in child
        assert child["connection"] == "visible"
        assert child["installed_sas"] == 1
        assert child["total_bytes_recv"] == 418
    assert router.count("GET", IPSEC_CONNECTIONS_SNAKE_PATH) == 1


async def test_ipsec_child_denial_preserves_connection_access(make_client: ClientType) -> None:
    """Retain configured connections and live children when only child inventory is denied.

    Args:
        make_client (ClientType): Client factory fixture.
    """
    client, router = make_vpn_router(
        make_client,
        ipsec_routes(
            {"rows": [{"ikeid": "active", "name": "active", "connected": True}]},
            connections={"rows": [{"uuid": "hidden-from-status", "enabled": "0"}]},
            children=403,
            phase2={"rows": [{"name": "live-child", "state": "INSTALLED"}]},
        ),
    )
    for _ in range(2):
        result = await client.get_ipsec()
        assert result is not None
        assert result_access(result) is Access.CONFIGURATION
        assert result["connections"]["hidden-from-status"]["enabled"] is False
        assert set(result["children"]) == {"live-child"}
        assert result["children"]["live-child"]["connected"] is True
    assert router.count("GET", "/api/ipsec/connections/search_child") == 1


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
        body["id"] for body in router.bodies("POST", IPSEC_PHASE2_SESSIONS_SNAKE_PATH)
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
    assert router.count("POST", IPSEC_PHASE2_SESSIONS_SNAKE_PATH) == 0


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
    assert sent(router) == [("GET", IPSEC_PHASE1_SESSIONS_SNAKE_PATH)]


async def test_get_ipsec_endpoint_unavailable_fails_closed(make_client: ClientType) -> None:
    """Request only the sessions endpoint, in the Router's endpoint style, when it is unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_vpn_router(
        make_client, ipsec_routes(UNAVAILABLE, snake_case=False), firmware=CAMEL_CASE_FIRMWARE
    )

    assert await client.get_ipsec() is None
    assert sent(router) == [("GET", IPSEC_PHASE1_SESSIONS_CAMELCASE_PATH)]


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


@pytest.mark.parametrize(
    ("firmware", "paths", "firmware_reads"),
    [
        pytest.param(
            SNAKE_CASE_FIRMWARE,
            (
                "/api/ipsec/sessions/search_phase1",
                "/api/ipsec/connections/search_connection",
                "/api/ipsec/connections/search_child",
                "/api/ipsec/sessions/search_phase2",
            ),
            1,
            id="snake_case",
        ),
        pytest.param(
            CAMEL_CASE_FIRMWARE,
            (
                "/api/ipsec/sessions/searchPhase1",
                "/api/ipsec/connections/searchConnection",
                "/api/ipsec/connections/searchChild",
                "/api/ipsec/sessions/searchPhase2",
            ),
            1,
            id="camelCase",
        ),
        pytest.param(
            None,
            (
                "/api/ipsec/sessions/search_phase1",
                "/api/ipsec/connections/search_connection",
                "/api/ipsec/connections/search_child",
                "/api/ipsec/sessions/search_phase2",
            ),
            # Phase 1, connections, children and one phase 2 path shared by both sessions.
            4,
            id="unknown-firmware",
        ),
    ],
)
async def test_get_ipsec_requests_follow_the_firmware_style(
    make_client: ClientType,
    firmware: str | None,
    paths: tuple[str, str, str, str],
    firmware_reads: int,
) -> None:
    """Read IPsec through the style the firmware selects, choosing the phase 2 path once.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        firmware (str | None): Firmware version the Router reports, ``None`` when it is unknown.
        paths (tuple[str, str, str, str]): Phase 1, connections, children and phase 2 paths.
        firmware_reads (int): Firmware status reads the whole call is expected to make.
    """
    phase1, connections, children, phase2 = paths
    sessions = {
        "rows": [
            {"ikeid": "ike-1", "name": "ike-1", "connected": True},
            {"ikeid": "ike-2", "name": "ike-2", "connected": True},
        ]
    }
    client, router = make_router_client(
        make_client,
        {
            ("GET", phase1): sessions,
            ("GET", connections): {"rows": []},
            ("GET", children): {"rows": []},
            ("POST", phase2): {"rows": []},
        },
        firmware=firmware,
    )

    assert await client.get_ipsec() is not None

    assert sent(router) == [
        ("GET", phase1),
        ("GET", connections),
        ("GET", children),
        ("POST", phase2),
        ("POST", phase2),
    ]
    assert router.bodies("POST", phase2) == [{"id": "ike-1"}, {"id": "ike-2"}]
    assert router.count("GET", FIRMWARE_STATUS_PATH) == firmware_reads


async def test_get_openvpn_routes_firmware_read_failure_propagates_in_throw_mode(
    make_client: ClientType,
) -> None:
    """A firmware read that fails before the routes read raises instead of being swallowed.

    The first firmware read answers with no version, which leaves the firmware
    unknown, so the routes read asks again and that second read fails.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    firmware_reads = 0

    def firmware_status(**_kwargs: Any) -> Any:
        """Answer the first firmware read with no version and fail the next.

        Args:
            _kwargs (Any): Request parameters passed by the HTTP transport.

        Returns:
            Any: Router reply for the firmware status read.
        """
        nonlocal firmware_reads
        firmware_reads += 1
        if firmware_reads == 1:
            return {}
        return aiohttp.ClientConnectionError("firmware read failed")

    client, router = make_router_client(
        make_client,
        {
            ("GET", "/api/openvpn/service/search_sessions"): {"rows": []},
            ("GET", FIRMWARE_STATUS_PATH): firmware_status,
        },
        throw_errors=True,
    )

    with pytest.raises(OPNsenseConnectionError):
        await client.get_openvpn()

    assert router.count("GET", FIRMWARE_STATUS_PATH) == 2
    assert router.count("GET", "/api/openvpn/service/search_routes") == 0
