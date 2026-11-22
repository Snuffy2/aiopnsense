"""Tests for `aiopnsense.vpn`."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

import aiopnsense as aiopnsense_module
from aiopnsense import OPNsenseClient, OPNsenseError, vpn as aiopnsense_vpn
from aiopnsense.exceptions import OPNsenseConnectionError
from tests.conftest import install_fake_router, make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


EMPTY_INVENTORY: dict[str, Any] = {"servers": {}, "clients": {}}
OPENVPN_READY_ROUTES: dict[tuple[str, str], Any] = {
    ("GET", aiopnsense_vpn.OPENVPN_SESSIONS_SNAKE_ENDPOINT): {"rows": []},
    ("GET", aiopnsense_vpn.OPENVPN_ROUTES_SNAKE_ENDPOINT): {"rows": []},
    ("GET", aiopnsense_vpn.OPENVPN_PROVIDERS_ENDPOINT): {},
    ("GET", aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT): {"rows": []},
}
WIREGUARD_READY_ROUTES: dict[tuple[str, str], Any] = {
    ("GET", aiopnsense_vpn.WIREGUARD_SUMMARY_ENDPOINT): {"rows": []},
    ("GET", aiopnsense_vpn.WIREGUARD_CLIENTS_ENDPOINT): {"client": {"clients": {"client": {}}}},
    ("GET", aiopnsense_vpn.WIREGUARD_SERVERS_ENDPOINT): {"server": {"servers": {"server": {}}}},
}
IPSEC_READY_ROUTES: dict[tuple[str, str], Any] = {
    ("GET", aiopnsense_vpn.IPSEC_PHASE1_SESSIONS_SNAKE_ENDPOINT): {"rows": []},
    ("GET", aiopnsense_vpn.IPSEC_CONNECTIONS_SNAKE_ENDPOINT): {"rows": []},
    ("GET", aiopnsense_vpn.IPSEC_CHILDREN_SNAKE_ENDPOINT): {"rows": []},
}
# Router answers for a read: an HTTP status or a transport exception, default and throw modes.
FAILURE_CASES = [
    pytest.param(404, False, id="absent"),
    pytest.param(403, False, id="forbidden"),
    pytest.param(500, False, id="server-error"),
    pytest.param(TimeoutError("timed out"), False, id="timeout"),
    pytest.param(404, True, id="absent-throw"),
    pytest.param(403, True, id="forbidden-throw"),
    pytest.param(500, True, id="server-error-throw"),
    pytest.param(TimeoutError("timed out"), True, id="timeout-throw"),
]
STATUS_CASES = [pytest.param(200, False, id="data"), *FAILURE_CASES]


@pytest.mark.asyncio
async def test_get_openvpn_and_fetch_details(make_client: ClientType) -> None:
    """Validate openvpn server/client discovery and fetch details flow.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        install_fake_router(
            client,
            session,
            routes={
                ("GET", aiopnsense_vpn.OPENVPN_SESSIONS_SNAKE_ENDPOINT): {
                    "rows": [
                        {
                            "type": "server",
                            "id": "1_desc_1",
                            "description": "s1",
                            "status": "connected",
                        }
                    ]
                },
                ("GET", aiopnsense_vpn.OPENVPN_ROUTES_SNAKE_ENDPOINT): {
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
                ("GET", aiopnsense_vpn.OPENVPN_PROVIDERS_ENDPOINT): {
                    "1": {"name": "prov1", "hostname": "host", "local_port": "1194"}
                },
                ("GET", aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT): {
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
                (
                    "GET",
                    f"{aiopnsense_vpn.OPENVPN_INSTANCE_DETAILS_ENDPOINT_PREFIX}uuid1",
                ): {
                    "instance": {
                        "server": "10.0.0.2",
                        "dns_servers": {"1": {"selected": 1, "value": "8.8.8.8"}},
                    }
                },
            },
        )
        openvpn = await client.get_openvpn()
        assert openvpn is not None
        assert "servers" in openvpn and "clients" in openvpn
        # servers should include uuid1
        assert any(
            s.get("name") == "server1" or s.get("uuid") == "uuid1"
            for s in openvpn["servers"].values()
        )
    finally:
        await client.async_close()


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
@pytest.mark.parametrize(
    "vpn_type,path,post_resp,expected",
    [
        ("openvpn", "servers", {"changed": False}, False),
        ("openvpn", "servers", [{"changed": True}, {"result": "ok"}], True),
        ("wireguard", "clients", [{"changed": True}, {"result": "ok"}], True),
        ("wireguard", "servers", {"changed": False}, False),
        ("ipsec", "connections", [{"changed": True}, {"status": "ok"}], True),
        ("ipsec", "connections", [{"changed": True}, {"status": "failed"}], False),
        ("ipsec", "connections", {"changed": False}, False),
        ("ipsec", "servers", {"changed": True}, False),
    ],
)
async def test_toggle_vpn_instance_variants(
    vpn_toggle_client: OPNsenseClient,
    vpn_type: str,
    path: str,
    post_resp: dict[str, Any] | list[dict[str, Any]],
    expected: bool,
) -> None:
    """Parametrized toggle_vpn_instance covering OpenVPN, WireGuard, and IPsec variants.

    Args:
        vpn_toggle_client (OPNsenseClient): Client fixture configured for VPN toggle requests.
        vpn_type (str): VPN implementation selected for the toggle request.
        path (str): API path expected for the selected VPN implementation.
        post_resp (dict[str, Any] | list[dict[str, Any]]): Canned response from the toggle request.
        expected (bool): Whether the toggle operation should report success.
    """
    client = vpn_toggle_client
    client._use_snake_case = True

    if isinstance(post_resp, list):
        post_mock = AsyncMock(side_effect=post_resp)
    else:
        post_mock = AsyncMock(return_value=post_resp)
    client._do_post = post_mock

    res = await client.toggle_vpn_instance(vpn_type, path, "uuid")
    assert res is expected
    await client.async_close()


@pytest.mark.asyncio
async def test_openvpn_more_detail_parsing(make_client: ClientType) -> None:
    """Exercise additional OpenVPN parsing branches (no sessions, missing fields).

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        install_fake_router(
            client,
            session,
            routes={
                **OPENVPN_READY_ROUTES,
                ("GET", aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT): {
                    "rows": [{"role": "client", "uuid": "c1", "enabled": "0"}]
                },
            },
        )
        res = await client.get_openvpn()
        assert res is not None
        assert "servers" in res and "clients" in res
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_openvpn_processing_and_fetch_details(make_client: ClientType) -> None:
    """Test processing of OpenVPN instances/providers/sessions/routes and fetching details.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        install_fake_router(
            client,
            session,
            routes={
                ("GET", aiopnsense_vpn.OPENVPN_SESSIONS_SNAKE_ENDPOINT): {
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
                ("GET", aiopnsense_vpn.OPENVPN_ROUTES_SNAKE_ENDPOINT): {
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
                ("GET", aiopnsense_vpn.OPENVPN_PROVIDERS_ENDPOINT): {
                    "srv1": {"name": "prov1", "hostname": "host.example", "local_port": "1194"},
                    "srv2": {"name": "provider-two"},
                },
                ("GET", aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT): {
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
                (
                    "GET",
                    f"{aiopnsense_vpn.OPENVPN_INSTANCE_DETAILS_ENDPOINT_PREFIX}srv1",
                ): {
                    "instance": {
                        "server": "10.0.0.1",
                        "dns_servers": {"0": {"selected": "1", "value": "8.8.8.8"}},
                    }
                },
            },
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
    finally:
        await client.async_close()


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
async def test_fetch_openvpn_server_details_missing_server_field(
    make_client: ClientType,
) -> None:
    """When instance details lack 'server' key, no tunnel_addresses should be set.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        install_fake_router(
            client,
            session,
            routes={
                **OPENVPN_READY_ROUTES,
                ("GET", aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT): {
                    "rows": [{"role": "server", "uuid": "srv1", "enabled": "1"}]
                },
                (
                    "GET",
                    f"{aiopnsense_vpn.OPENVPN_INSTANCE_DETAILS_ENDPOINT_PREFIX}srv1",
                ): {"instance": {}},
            },
        )
        openvpn = await client.get_openvpn()
        assert openvpn is not None
        server = openvpn["servers"]["srv1"]
        assert "tunnel_addresses" not in server
        assert server["dns_servers"] == []
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "throw_errors"), FAILURE_CASES)
async def test_openvpn_server_details_absent_or_transient_keep_defaults(
    make_client: ClientType,
    status: Any,
    throw_errors: bool,
) -> None:
    """Keep the server and omit its tunnel address when instance details are unavailable.

    Instance details are optional enrichment: an absent or transient details read
    leaves the defaults in default mode, and throw mode raises for a forbidden or
    transient failure.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        status (Any): Router answer for the instance details endpoint.
        throw_errors (bool): Whether the client raises on failures.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._throw_errors = throw_errors
        install_fake_router(
            client,
            session,
            routes={
                **OPENVPN_READY_ROUTES,
                ("GET", aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT): {
                    "rows": [{"role": "server", "uuid": "srv1", "enabled": "1"}]
                },
                (
                    "GET",
                    f"{aiopnsense_vpn.OPENVPN_INSTANCE_DETAILS_ENDPOINT_PREFIX}srv1",
                ): status,
            },
        )
        if throw_errors and status != 404:
            with pytest.raises(OPNsenseError):
                await client.get_openvpn()
        else:
            openvpn = await client.get_openvpn()
            assert openvpn is not None
            server = openvpn["servers"]["srv1"]
            assert "tunnel_addresses" not in server
            assert server["connected_clients"] == 0
            assert server["dns_servers"] == []
    finally:
        await client.async_close()


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


@pytest.mark.asyncio
async def test_get_wireguard_success_and_invalid(make_client: ClientType) -> None:
    """Exercise get_wireguard success path and invalid structure early return.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)

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

    router = install_fake_router(
        client,
        session,
        routes={
            ("GET", aiopnsense_vpn.WIREGUARD_SUMMARY_ENDPOINT): summary_resp,
            ("GET", aiopnsense_vpn.WIREGUARD_CLIENTS_ENDPOINT): clients_resp,
            ("GET", aiopnsense_vpn.WIREGUARD_SERVERS_ENDPOINT): servers_resp,
        },
    )
    wg = await client.get_wireguard()
    assert wg is not None
    assert "servers" in wg and "clients" in wg and wg["servers"]["s1"]["uuid"] == "s1"
    # client peer should reflect disconnected (old handshake)
    assert wg["clients"]["c1"].get("connected_servers") == 0

    # Invalid summary must not report configured peers as disconnected.
    router.routes[("GET", aiopnsense_vpn.WIREGUARD_SUMMARY_ENDPOINT)] = {"rows": {}}
    assert await client.get_wireguard() is None
    await client.async_close()


@pytest.mark.asyncio
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
    client, session = make_mock_session_client(make_client)
    try:
        client._throw_errors = throw_errors
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
        install_fake_router(
            client,
            session,
            routes={
                ("GET", aiopnsense_vpn.WIREGUARD_SUMMARY_ENDPOINT): {"rows": []},
                ("GET", aiopnsense_vpn.WIREGUARD_CLIENTS_ENDPOINT): {
                    "client": {"clients": {"client": client_summ}}
                },
                ("GET", aiopnsense_vpn.WIREGUARD_SERVERS_ENDPOINT): {
                    "server": {"servers": {"server": server_summ}}
                },
            },
        )

        assert await client.get_wireguard() is None
    finally:
        await client.async_close()


READ_CASES: dict[str, tuple[str, str, dict[tuple[str, str], Any], Any]] = {
    "openvpn-sessions": (
        "get_openvpn",
        aiopnsense_vpn.OPENVPN_SESSIONS_SNAKE_ENDPOINT,
        OPENVPN_READY_ROUTES,
        None,
    ),
    "openvpn-providers": (
        "get_openvpn",
        aiopnsense_vpn.OPENVPN_PROVIDERS_ENDPOINT,
        OPENVPN_READY_ROUTES,
        EMPTY_INVENTORY,
    ),
    "openvpn-instances": (
        "get_openvpn",
        aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT,
        OPENVPN_READY_ROUTES,
        EMPTY_INVENTORY,
    ),
    "wireguard-summary": (
        "get_wireguard",
        aiopnsense_vpn.WIREGUARD_SUMMARY_ENDPOINT,
        WIREGUARD_READY_ROUTES,
        None,
    ),
    "wireguard-clients": (
        "get_wireguard",
        aiopnsense_vpn.WIREGUARD_CLIENTS_ENDPOINT,
        WIREGUARD_READY_ROUTES,
        EMPTY_INVENTORY,
    ),
    "wireguard-servers": (
        "get_wireguard",
        aiopnsense_vpn.WIREGUARD_SERVERS_ENDPOINT,
        WIREGUARD_READY_ROUTES,
        EMPTY_INVENTORY,
    ),
    "ipsec-sessions": (
        "get_ipsec",
        aiopnsense_vpn.IPSEC_PHASE1_SESSIONS_SNAKE_ENDPOINT,
        IPSEC_READY_ROUTES,
        None,
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("read_id", list(READ_CASES))
@pytest.mark.parametrize(("status", "throw_errors"), STATUS_CASES)
async def test_vpn_read_maps_router_outcome_to_public_result(
    make_client: ClientType,
    read_id: str,
    status: Any,
    throw_errors: bool,
) -> None:
    """Map each Router answer for a VPN read to the public result the read documents.

    A 404, and a 403 in default mode, is absent and gives the read's empty value. A
    transient failure gives ``None`` in default mode. In throw mode a 403 or a
    transient failure raises ``OPNsenseError``, while a 404 still gives the empty value.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        read_id (str): Key of the read in ``READ_CASES``.
        status (Any): Router answer for the read: an HTTP status, a data answer or an exception.
        throw_errors (bool): Whether the client raises on failures.
    """
    public_method, path, ready_routes, absent_result = READ_CASES[read_id]
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._throw_errors = throw_errors
        value = ready_routes[("GET", path)] if status == 200 else status
        install_fake_router(client, session, routes={**ready_routes, ("GET", path): value})
        read = getattr(client, public_method)
        if status == 200:
            assert await read() is not None
        elif throw_errors and status != 404:
            with pytest.raises(OPNsenseError):
                await read()
        elif status in (404, 403):
            assert await read() == absent_result
        else:
            assert await read() is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_wireguard_known_missing_inventory_is_not_reread(
    make_client: ClientType,
) -> None:
    """Answer a cached absent WireGuard inventory without another request.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    try:
        router = install_fake_router(
            client,
            session,
            routes={
                **WIREGUARD_READY_ROUTES,
                ("GET", aiopnsense_vpn.WIREGUARD_CLIENTS_ENDPOINT): 404,
            },
        )
        assert await client.get_wireguard() == EMPTY_INVENTORY
        assert await client.get_wireguard() == EMPTY_INVENTORY
        assert router.count("GET", aiopnsense_vpn.WIREGUARD_CLIENTS_ENDPOINT) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
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
    client, session = make_mock_session_client(make_client)
    try:
        client._throw_errors = throw_errors
        client._use_snake_case = True
        install_fake_router(
            client,
            session,
            routes={
                **OPENVPN_READY_ROUTES,
                ("GET", aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT): instances,
                ("GET", aiopnsense_vpn.OPENVPN_PROVIDERS_ENDPOINT): providers,
            },
        )

        assert await client.get_openvpn() is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_get_openvpn_routes_request_failure(
    make_client: ClientType, throw_errors: bool
) -> None:
    """Degrade a failed optional routes request to partial data, or raise in throw mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Whether strict request errors are enabled.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client.toggle_throwing_errors(throw_errors)
        client._use_snake_case = True
        install_fake_router(
            client,
            session,
            routes={
                **OPENVPN_READY_ROUTES,
                ("GET", aiopnsense_vpn.OPENVPN_ROUTES_SNAKE_ENDPOINT): 500,
                ("GET", aiopnsense_vpn.OPENVPN_SESSIONS_SNAKE_ENDPOINT): {
                    "rows": [{"type": "server", "id": "server", "status": "connected"}]
                },
                ("GET", aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT): {
                    "rows": [{"role": "server", "uuid": "server", "enabled": "1"}]
                },
            },
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
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_openvpn_routes_cancellation_propagates(make_client: ClientType) -> None:
    """Never swallow cancellation raised by the optional routes request.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        install_fake_router(
            client,
            session,
            routes={
                **OPENVPN_READY_ROUTES,
                ("GET", aiopnsense_vpn.OPENVPN_ROUTES_SNAKE_ENDPOINT): asyncio.CancelledError(),
            },
        )

        with pytest.raises(asyncio.CancelledError):
            await client.get_openvpn()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_openvpn_ignores_explicit_unsupported_role(make_client: ClientType) -> None:
    """Ignore an explicit unsupported role while validating required records.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        install_fake_router(
            client,
            session,
            routes={
                **OPENVPN_READY_ROUTES,
                ("GET", aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT): {
                    "rows": [{"role": "other"}]
                },
            },
        )

        assert await client.get_openvpn() == EMPTY_INVENTORY
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("use_snake_case", "expected_sessions", "expected_routes", "expected_client_toggle"),
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
    vpn_toggle_client: OPNsenseClient,
    use_snake_case: bool,
    expected_sessions: str,
    expected_routes: str,
    expected_client_toggle: str,
) -> None:
    """Verify switched VPN endpoints follow the selected endpoint style.

    Args:
        vpn_toggle_client (OPNsenseClient): Client with a running request queue.
        use_snake_case (bool): Whether the client should prefer snake_case endpoints.
        expected_sessions (str): Expected OpenVPN session-search endpoint path.
        expected_routes (str): Expected OpenVPN route-search endpoint path.
        expected_client_toggle (str): Expected WireGuard client-toggle endpoint path.

    Returns:
        None: This test validates VPN endpoint selection behavior.
    """
    client = vpn_toggle_client
    try:
        client._use_snake_case = use_snake_case
        router = install_fake_router(
            client,
            client._session,
            routes={
                ("GET", expected_sessions): {"rows": []},
                ("GET", expected_routes): {"rows": []},
                ("GET", aiopnsense_vpn.OPENVPN_PROVIDERS_ENDPOINT): {},
                ("GET", aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT): {"rows": []},
            },
        )
        # The WireGuard toggle reads the client state through the direct transport helper.
        client._safe_dict_get = AsyncMock(return_value={"rows": []})
        post_mock = AsyncMock(side_effect=[{"changed": True}, {"result": "ok"}])
        client._do_post = post_mock

        await client.get_openvpn()
        await client.toggle_vpn_instance("wireguard", "clients", "uuid")

        assert router.count("GET", expected_sessions) == 1
        assert router.count("GET", expected_routes) == 1
        assert post_mock.await_args_list[0].args[0] == expected_client_toggle
    finally:
        await client.async_close()


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


def _ipsec_routes(
    sessions: dict[str, Any],
    connections: dict[str, Any],
    children: dict[str, Any] | None = None,
) -> dict[tuple[str, str], Any]:
    """Return the Router answers for the IPsec snake_case read endpoints.

    Args:
        sessions (dict[str, Any]): Payload for the phase 1 sessions endpoint.
        connections (dict[str, Any]): Payload for the connections configuration endpoint.
        children (dict[str, Any] | None): Payload for the child configuration endpoint;
            defaults to an empty row list.

    Returns:
        dict[tuple[str, str], Any]: Values keyed by ``("GET", path)`` for the phase 1
            sessions, connections and children endpoints.
    """
    return {
        ("GET", aiopnsense_vpn.IPSEC_PHASE1_SESSIONS_SNAKE_ENDPOINT): sessions,
        ("GET", aiopnsense_vpn.IPSEC_CONNECTIONS_SNAKE_ENDPOINT): connections,
        ("GET", aiopnsense_vpn.IPSEC_CHILDREN_SNAKE_ENDPOINT): (
            children if children is not None else {"rows": []}
        ),
    }


@pytest.mark.asyncio
async def test_get_ipsec_success_normalizes_rows(make_client: ClientType) -> None:
    """Normalize connected, disconnected, and malformed IPsec phase 1 rows.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
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
        router = install_fake_router(client, session, routes=_ipsec_routes(sessions, configured))
        client._safe_dict_post = AsyncMock(return_value={"rows": []})

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
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_ipsec_reports_configured_connections_without_session(
    make_client: ClientType,
) -> None:
    """Report configured connections without a session, including disabled ones.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
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
        install_fake_router(client, session, routes=_ipsec_routes(sessions, configured))
        client._safe_dict_post = AsyncMock(return_value={"rows": []})

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
    finally:
        await client.async_close()


@pytest.mark.asyncio
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
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        sessions = {"rows": [{"ikeid": "uuid-up", "name": "uuid-up", "connected": True}]}
        configured = {
            "rows": [
                {"uuid": "uuid-up", "enabled": enabled, "version": version},
                {"uuid": "uuid-off", "enabled": enabled, "version": version},
            ]
        }
        install_fake_router(client, session, routes=_ipsec_routes(sessions, configured))
        client._safe_dict_post = AsyncMock(return_value={"rows": []})

        ipsec = await client.get_ipsec()

        assert ipsec is not None
        for uuid in ("uuid-up", "uuid-off"):
            row = ipsec["connections"][uuid]
            if expected_enabled == "missing":
                assert "enabled" not in row
            else:
                assert row["enabled"] is expected_enabled
        assert ipsec["connections"]["uuid-off"]["version"] == expected_version
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "connections",
    [
        pytest.param(500, id="transient-failure"),
        pytest.param({"rows": "nope"}, id="malformed-rows"),
        pytest.param({}, id="missing-rows"),
    ],
)
async def test_get_ipsec_without_connection_inventory_returns_none(
    make_client: ClientType,
    connections: Any,
) -> None:
    """Report an unavailable inventory when connection configuration cannot be read.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        connections (Any): Router answer for the connections configuration endpoint.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        sessions = {"rows": [{"ikeid": "uuid-up", "name": "uuid-up", "connected": True}]}
        routes = _ipsec_routes(sessions, {"rows": []})
        routes[("GET", aiopnsense_vpn.IPSEC_CONNECTIONS_SNAKE_ENDPOINT)] = connections
        install_fake_router(client, session, routes=routes)

        ipsec = await client.get_ipsec()

        assert ipsec is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
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
    client, session = make_mock_session_client(make_client)
    try:
        client._throw_errors = throw_errors
        client._use_snake_case = True
        sessions = {"rows": [{"ikeid": "uuid-up", "name": "uuid-up", "connected": True}]}
        configured = {
            "rows": [
                {"uuid": "uuid-disabled", "enabled": "0"},
                malformed_row,
            ]
        }
        install_fake_router(client, session, routes=_ipsec_routes(sessions, configured))
        client._safe_dict_post = AsyncMock(return_value={"rows": []})

        assert await client.get_ipsec() is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "throw_errors"), FAILURE_CASES)
async def test_get_ipsec_connections_absent_or_transient_inventory(
    make_client: ClientType,
    status: Any,
    throw_errors: bool,
) -> None:
    """Report session-only connections when the connections configuration is absent.

    A 404, or a 403 in default mode, leaves ``enabled`` unset and still returns the
    session data. A transient failure returns ``None``, and throw mode raises for a
    forbidden or transient failure.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        status (Any): Router answer for the connections configuration endpoint.
        throw_errors (bool): Whether the client raises on failures.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._throw_errors = throw_errors
        sessions = {"rows": [{"ikeid": "uuid-up", "name": "uuid-up", "connected": False}]}
        routes = _ipsec_routes(sessions, {"rows": []})
        routes[("GET", aiopnsense_vpn.IPSEC_CONNECTIONS_SNAKE_ENDPOINT)] = status
        install_fake_router(client, session, routes=routes)

        if throw_errors and status != 404:
            with pytest.raises(OPNsenseError):
                await client.get_ipsec()
        elif status in (404, 403):
            ipsec = await client.get_ipsec()
            assert ipsec is not None
            assert set(ipsec["connections"]) == {"uuid-up"}
            assert ipsec["connections"]["uuid-up"]["enabled"] is None
            assert ipsec["children"] == {}
        else:
            assert await client.get_ipsec() is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_ipsec_children_aggregate_phase2_sessions(make_client: ClientType) -> None:
    """Aggregate phase 2 SAs per child and keep configured children without SAs.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
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

        async def fake_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
            """Return the phase 2 SA rows for the requested phase 1 session.

            Args:
                path (str): API endpoint path requested by the client.
                payload (dict[str, Any]): Request body naming the phase 1 session.

            Returns:
                dict[str, Any]: Canned phase 2 sessions payload.
            """
            assert path == aiopnsense_vpn.IPSEC_PHASE2_SESSIONS_SNAKE_ENDPOINT
            return phase2[payload["id"]]

        install_fake_router(
            client,
            session,
            routes=_ipsec_routes(sessions, {"rows": []}, children_config),
        )
        client._safe_dict_post = AsyncMock(side_effect=fake_post)

        ipsec = await client.get_ipsec()

        assert ipsec is not None
        assert [call.args[1] for call in client._safe_dict_post.await_args_list] == [
            {"id": "ike-1"},
            {"id": "con1"},
        ]
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
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("children_status", "phase2_payload", "expected_children"),
    [
        pytest.param(
            404,
            {"rows": [{"name": "child-x", "state": "INSTALLED"}]},
            {"child-x"},
            id="absent-config-keeps-session-children",
        ),
        pytest.param(500, {"rows": []}, None, id="transient-config-omits-children"),
        pytest.param(404, {}, None, id="malformed-phase2-omits-children"),
        pytest.param(404, {"rows": "nope"}, None, id="phase2-rows-not-list"),
    ],
)
async def test_get_ipsec_children_fail_closed(
    make_client: ClientType,
    children_status: Any,
    phase2_payload: dict[str, Any],
    expected_children: set[str] | None,
) -> None:
    """Omit ``children`` when phase 2 data cannot be read completely.

    A transient child configuration failure or a malformed phase 2 reply omits the
    key. A child configuration endpoint answered 404 falls back to the children seen in
    the sessions API.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        children_status (Any): Router answer for the child configuration endpoint.
        phase2_payload (dict[str, Any]): Phase 2 sessions payload returned by the API.
        expected_children (set[str] | None): Expected child keys, or ``None`` when
            ``children`` must be omitted.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        sessions = {"rows": [{"ikeid": "ike-1", "name": "ike-1", "connected": True}]}
        routes = _ipsec_routes(sessions, {"rows": []})
        routes[("GET", aiopnsense_vpn.IPSEC_CHILDREN_SNAKE_ENDPOINT)] = children_status
        install_fake_router(client, session, routes=routes)
        client._safe_dict_post = AsyncMock(return_value=phase2_payload)

        ipsec = await client.get_ipsec()

        assert ipsec is not None
        assert set(ipsec["connections"]) == {"ike-1"}
        if expected_children is None:
            assert "children" not in ipsec
        else:
            assert set(ipsec["children"]) == expected_children
            assert ipsec["children"]["child-x"]["enabled"] is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_get_ipsec_phase2_request_failure(
    make_client: ClientType, throw_errors: bool
) -> None:
    """Omit children when a phase 2 request fails, or raise in throw mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Whether strict request errors are enabled.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._throw_errors = throw_errors
        client._use_snake_case = True
        sessions = {"rows": [{"ikeid": "ike-1", "name": "ike-1", "connected": True}]}
        install_fake_router(client, session, routes=_ipsec_routes(sessions, {"rows": []}))
        client._safe_dict_post = AsyncMock(side_effect=OPNsenseConnectionError("phase 2 failed"))

        if throw_errors:
            with pytest.raises(OPNsenseConnectionError):
                await client.get_ipsec()
        else:
            result = await client.get_ipsec()
            assert set(result["connections"]) == {"ike-1"}
            assert "children" not in result
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_ipsec_phase2_cancellation_propagates(make_client: ClientType) -> None:
    """Never swallow cancellation raised by a phase 2 sessions request.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        sessions = {"rows": [{"ikeid": "ike-1", "name": "ike-1", "connected": True}]}
        install_fake_router(client, session, routes=_ipsec_routes(sessions, {"rows": []}))
        client._safe_dict_post = AsyncMock(side_effect=asyncio.CancelledError)

        with pytest.raises(asyncio.CancelledError):
            await client.get_ipsec()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "throw_errors"), FAILURE_CASES)
async def test_get_ipsec_children_absent_or_transient_inventory(
    make_client: ClientType,
    status: Any,
    throw_errors: bool,
) -> None:
    """Report an empty child inventory when absent and omit it when transient.

    A 404, or a 403 in default mode, gives an empty ``children`` mapping. A transient
    failure omits ``children``, and throw mode raises for a forbidden or transient
    failure.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        status (Any): Router answer for the child configuration endpoint.
        throw_errors (bool): Whether the client raises on failures.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._throw_errors = throw_errors
        routes = _ipsec_routes({"rows": []}, {"rows": []})
        routes[("GET", aiopnsense_vpn.IPSEC_CHILDREN_SNAKE_ENDPOINT)] = status
        install_fake_router(client, session, routes=routes)

        if throw_errors and status != 404:
            with pytest.raises(OPNsenseError):
                await client.get_ipsec()
        else:
            ipsec = await client.get_ipsec()
            assert ipsec is not None
            if status in (404, 403):
                assert ipsec["children"] == {}
            else:
                assert "children" not in ipsec
    finally:
        await client.async_close()


@pytest.mark.asyncio
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
    client, session = make_mock_session_client(make_client)
    try:
        client._throw_errors = throw_errors
        client._use_snake_case = True
        sessions = {"rows": [{"ikeid": "ike-1", "name": "ike-1", "connected": True}]}
        children = {
            "rows": [
                {"uuid": "child-disabled", "enabled": "0"},
                malformed_row,
            ]
        }
        install_fake_router(client, session, routes=_ipsec_routes(sessions, {"rows": []}, children))
        client._safe_dict_post = AsyncMock(return_value={"rows": []})

        result = await client.get_ipsec()

        assert result is not None
        assert set(result["connections"]) == {"ike-1"}
        assert "children" not in result
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_ipsec_children_skip_phase2_without_connected_sessions(
    make_client: ClientType,
) -> None:
    """Report configured children as disconnected without querying phase 2.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        sessions = {"rows": [{"ikeid": "ike-1", "name": "ike-1", "connected": False}]}
        children_config = {"rows": [{"uuid": "child-a", "enabled": "1", "connection": "ike-1"}]}
        install_fake_router(
            client,
            session,
            routes=_ipsec_routes(sessions, {"rows": []}, children_config),
        )
        client._safe_dict_post = AsyncMock()

        ipsec = await client.get_ipsec()

        assert ipsec is not None
        assert ipsec["children"]["child-a"]["status"] == "disconnected"
        assert ipsec["children"]["child-a"]["name"] == "child-a"
        client._safe_dict_post.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
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
    vpn_toggle_client: OPNsenseClient,
    vpn_type: str,
    collection: str,
    snake_case: bool,
    expected_toggle: str,
    expected_get: str | None,
) -> None:
    """Send the explicit target and then apply it using the firmware's endpoints.

    Args:
        vpn_toggle_client (OPNsenseClient): Client with a running request queue.
        vpn_type (str): VPN service under test.
        collection (str): Collection containing the requested item.
        snake_case (bool): Whether firmware uses snake_case endpoint names.
        expected_toggle (str): Expected toggle endpoint path.
        expected_get (str | None): Expected state-read endpoint, if needed.
    """
    client = vpn_toggle_client
    client._use_snake_case = snake_case
    client._do_get = AsyncMock(return_value={collection[:-1]: {"enabled": "0"}})
    apply_response = {"status": "ok"} if vpn_type == "ipsec" else {"result": "ok"}
    posts = AsyncMock(side_effect=[{"changed": True, "result": "Enabled"}, apply_response])
    client._do_post = posts

    assert await client.toggle_vpn_instance(vpn_type, collection, "uuid", "on") is True
    assert [call.args[0] for call in client._do_get.await_args_list] == (
        [expected_get] if expected_get else []
    )
    assert [call.args[0] for call in posts.await_args_list] == [
        expected_toggle,
        f"/api/{vpn_type}/service/reconfigure",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"rows": {"not": "a list"}},
        {"rows": "nope"},
    ],
)
async def test_get_ipsec_malformed_payload_returns_none(
    make_client: ClientType, payload: dict[str, Any]
) -> None:
    """Return None when the sessions rows are not a list.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        payload (dict[str, Any]): Malformed sessions payload returned by the API.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        install_fake_router(client, session, routes=_ipsec_routes(payload, {"rows": []}))

        ipsec = await client.get_ipsec()

        assert ipsec is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_ipsec_without_rows_returns_none(make_client: ClientType) -> None:
    """Treat a sessions payload without rows as unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        install_fake_router(client, session, routes=_ipsec_routes({}, {"rows": []}))

        ipsec = await client.get_ipsec()

        assert ipsec is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_ipsec_endpoint_unavailable_fails_closed(make_client: ClientType) -> None:
    """Skip the other reads when the phase 1 sessions endpoint is absent.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = False
        router = install_fake_router(
            client,
            session,
            routes={("GET", aiopnsense_vpn.IPSEC_PHASE1_SESSIONS_CAMELCASE_ENDPOINT): 404},
        )

        assert await client.get_ipsec() is None
        assert router.count("GET") == 1
    finally:
        await client.async_close()


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "routes",
    [
        pytest.param(404, id="absent"),
        pytest.param(500, id="transient"),
        pytest.param({}, id="empty-body"),
        pytest.param({"rows": None}, id="rows-missing"),
        pytest.param({"rows": []}, id="no-clients"),
    ],
)
async def test_openvpn_unavailable_routes_omit_client_counts(
    make_client: ClientType, routes: Any
) -> None:
    """Keep session status while distinguishing unknown routes from no clients.

    Args:
        make_client (ClientType): Client factory fixture.
        routes (Any): Router answer for the routes endpoint.
    """
    client, session = make_mock_session_client(make_client)
    client._use_snake_case = True
    install_fake_router(
        client,
        session,
        routes={
            ("GET", aiopnsense_vpn.OPENVPN_SESSIONS_SNAKE_ENDPOINT): {
                "rows": [{"type": "server", "id": "server", "status": "connected"}]
            },
            ("GET", aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT): {
                "rows": [{"role": "server", "uuid": "server", "enabled": "1"}]
            },
            ("GET", aiopnsense_vpn.OPENVPN_ROUTES_SNAKE_ENDPOINT): routes,
        },
    )
    try:
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
    finally:
        await client.async_close()


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

    async def get(self, path: str, _caller: str) -> dict[str, Any]:
        """Return the simulated WireGuard item.

        Args:
            path (str): Requested item endpoint.
            _caller (str): Unused queue caller label.

        Returns:
            dict[str, Any]: Item payload.
        """
        key = "client" if "/client/" in path else "server"
        return {key: {"enabled": self.enabled}}

    async def post(
        self, path: str, _payload: dict[str, Any] | None, _caller: str
    ) -> dict[str, Any]:
        """Apply a simulated toggle or service reconfigure.

        Args:
            path (str): Requested endpoint.
            _payload (dict[str, Any] | None): Unused request payload.
            _caller (str): Unused queue caller label.

        Returns:
            dict[str, Any]: Response shaped like the OPNsense endpoint.
        """
        if path.endswith("/service/reconfigure"):
            self.reconfigure_count += 1
            result_key = "status" if path.startswith("/api/ipsec/") else "result"
            if self.reconfigure_count == self.fail_reconfigure_number:
                return {result_key: "failed"}
            self.applied_enabled = self.enabled
            return {result_key: "ok"}
        self.toggle_count += 1
        last_segment = path.rsplit("/", 1)[-1]
        target = last_segment if self.honors_target and last_segment in {"0", "1"} else None
        new_state = target or ("0" if self.enabled == "1" else "1")
        changed = new_state != self.enabled
        self.enabled = new_state
        self.mutation_count += int(changed)
        return {"result": "Enabled" if new_state == "1" else "Disabled", "changed": changed}


def use_fake_vpn_api(client: OPNsenseClient, api: FakeVpnToggleApi) -> None:
    """Simulate OPNsense responses at the transport boundary.

    Args:
        client (OPNsenseClient): Client under test.
        api (FakeVpnToggleApi): Simulated OPNsense API.
    """
    client._do_get = AsyncMock(side_effect=api.get)
    client._do_post = AsyncMock(side_effect=api.post)


@pytest.mark.asyncio
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
    vpn_toggle_client: OPNsenseClient,
    vpn_type: str,
    collection: str,
    honors_target: bool,
    initial: str,
    target: str,
    final: str,
    reconfigures: int,
) -> None:
    """Reach the requested state and reconfigure only after a real change.

    Args:
        vpn_toggle_client (OPNsenseClient): Client with a running request queue.
        vpn_type (str): VPN implementation under test.
        collection (str): VPN collection under test.
        honors_target (bool): Whether simulated firmware honors the target.
        initial (str): Initial stored state.
        target (str): Requested target state.
        final (str): Expected final stored state.
        reconfigures (int): Expected service reconfigure count.
    """
    client = vpn_toggle_client
    api = FakeVpnToggleApi(initial, honors_target)
    use_fake_vpn_api(client, api)

    result = await client.toggle_vpn_instance(vpn_type, collection, "uuid", target)

    assert result is True
    assert api.enabled == api.applied_enabled == final
    assert api.reconfigure_count == reconfigures
    if vpn_type == "wireguard" and reconfigures == 0:
        assert api.toggle_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("correction", "expected"),
    [
        pytest.param({"changed": True, "result": "Enabled"}, True, id="corrected"),
        pytest.param({"result": "failed"}, False, id="retry-needed"),
    ],
)
async def test_toggle_vpn_instance_wireguard_handles_change_after_read(
    vpn_toggle_client: OPNsenseClient, correction: dict[str, object], expected: bool
) -> None:
    """Correct the legacy firmware's wrong-way flip or retain its unapplied save.

    Args:
        vpn_toggle_client (OPNsenseClient): Client with a running request queue.
        correction (dict[str, object]): Response from the corrective toggle.
        expected (bool): Whether the original target request succeeds.
    """
    client = vpn_toggle_client
    client._do_get = AsyncMock(return_value={"client": {"enabled": "0"}})
    posts = AsyncMock(
        side_effect=[{"changed": True, "result": "Disabled"}, correction, {"result": "ok"}]
    )
    client._do_post = posts
    toggle = "/api/wireguard/client/toggle_client/uuid/1"
    apply = "/api/wireguard/service/reconfigure"

    assert await client.toggle_vpn_instance("wireguard", "clients", "uuid", "on") is expected
    if not expected:
        assert [call.args[0] for call in posts.await_args_list] == [toggle, toggle]
        assert await client.toggle_vpn_instance("wireguard", "clients", "uuid", "off") is True
    assert [call.args[0] for call in posts.await_args_list] == [toggle, toggle, apply]


@pytest.mark.asyncio
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
    vpn_toggle_client: OPNsenseClient,
    vpn_type: str,
    collection: str,
    honors_target: bool,
    external_change: bool,
) -> None:
    """Retry a failed apply after revalidating the requested item state.

    Args:
        vpn_toggle_client (OPNsenseClient): Client with a running request queue.
        vpn_type (str): VPN service under test.
        collection (str): Collection containing the VPN instance.
        honors_target (bool): Whether the fake endpoint honors explicit targets.
        external_change (bool): Whether another writer changes the saved state before retry.
    """
    client = vpn_toggle_client
    api = FakeVpnToggleApi("0", honors_target, fail_reconfigure_number=1)
    use_fake_vpn_api(client, api)

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


@pytest.mark.asyncio
@pytest.mark.parametrize("collection", ["clients", "servers"])
async def test_wireguard_toggle_rejects_empty_uuid(
    vpn_toggle_client: OPNsenseClient, collection: str
) -> None:
    """Reject an empty item UUID before reading or changing configuration.

    Args:
        vpn_toggle_client (OPNsenseClient): Client with a running request queue.
        collection (str): WireGuard collection under test.
    """
    client = vpn_toggle_client
    client._do_get = AsyncMock(return_value={collection[:-1]: {"enabled": "1"}})
    client._do_post = AsyncMock()

    assert await client.toggle_vpn_instance("wireguard", collection, "", "on") is False
    client._do_get.assert_not_awaited()
    client._do_post.assert_not_awaited()


@pytest.mark.asyncio
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
    vpn_toggle_client: OPNsenseClient,
    vpn_type: str,
    item: str,
    read: dict[str, object],
    toggle: dict[str, object],
    expected_posts: int,
) -> None:
    """Reject a missing item or malformed response without reconfiguring the service.

    Args:
        vpn_toggle_client (OPNsenseClient): Client with a running request queue.
        vpn_type (str): VPN service under test.
        item (str): Item collection.
        read (dict[str, object]): Item state response, if read.
        toggle (dict[str, object]): Toggle response, if dispatched.
        expected_posts (int): How many toggle requests can safely be dispatched.
    """
    client = vpn_toggle_client
    client._do_get = AsyncMock(return_value=read)
    posts = AsyncMock(side_effect=[toggle] if expected_posts else [])
    client._do_post = posts

    assert await client.toggle_vpn_instance(vpn_type, item, "uuid", "on") is False
    assert posts.await_count == expected_posts
    assert all(not call.args[0].endswith("/reconfigure") for call in posts.await_args_list)


@pytest.mark.asyncio
async def test_toggle_vpn_instance_wireguard_serializes_concurrent_calls(
    vpn_toggle_client: OPNsenseClient,
) -> None:
    """Serialize same-client state reads through reconfigure for WireGuard calls.

    Args:
        vpn_toggle_client (OPNsenseClient): Client with a running request queue.
    """
    client = vpn_toggle_client
    first_read_started = asyncio.Event()
    release_first_read = asyncio.Event()

    api = FakeVpnToggleApi("0", honors_target=False)

    async def hold_first_read(path: str, caller: str) -> dict[str, Any]:
        """Hold the first GET response while a second VPN call starts.

        Args:
            path (str): Requested endpoint.
            caller (str): Queue caller label.

        Returns:
            dict[str, Any]: Simulated WireGuard state.
        """
        response = await api.get(path, caller)
        if not first_read_started.is_set():
            first_read_started.set()
            await release_first_read.wait()
        return response

    use_fake_vpn_api(client, api)
    read = AsyncMock(side_effect=hold_first_read)
    client._do_get = read
    tasks: list[asyncio.Task[bool]] = []
    try:
        tasks.append(
            asyncio.create_task(client.toggle_vpn_instance("wireguard", "clients", "uuid", "on"))
        )
        await asyncio.wait_for(first_read_started.wait(), timeout=1)
        tasks.append(
            asyncio.create_task(
                client.toggle_vpn_instance("wireguard", "clients", "uuid", "on"), eager_start=True
            )
        )
        await asyncio.sleep(0)
        read.assert_awaited_once()

        release_first_read.set()
        assert await asyncio.wait_for(asyncio.gather(*tasks), timeout=3) == [True, True]
        assert (api.enabled, api.toggle_count, api.reconfigure_count) == ("1", 1, 1)
    finally:
        release_first_read.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.async_close()
