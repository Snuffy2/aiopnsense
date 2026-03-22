"""Tests for `aiopnsense.dhcp`."""

from collections.abc import MutableMapping
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

import aiopnsense as pyopnsense
from tests.conftest import make_mock_session_client


@pytest.mark.asyncio
async def test_dhcp_leases_and_keep_latest_and_dnsmasq(make_client) -> None:
    """Cover Kea and dnsmasq lease parsing and _keep_latest_leases helper."""
    client, session = make_mock_session_client(make_client)
    try:
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
async def test_get_arp_table_uses_get_query_param(make_client) -> None:
    """get_arp_table should call diagnostics search_arp via GET with resolve query parameter."""
    client, session = make_mock_session_client(make_client)
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
async def test_isc_dhcp_endpoint_unavailable(make_client) -> None:
    """ISC DHCP lease methods should return empty list when endpoints are unavailable."""
    client, session = make_mock_session_client(make_client)
    try:
        client.is_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get = AsyncMock()

        # Test DHCPv4
        leases_v4 = await client._get_isc_dhcpv4_leases()
        assert leases_v4 == []
        client._safe_dict_get.assert_not_awaited()

        # Test DHCPv6
        client._safe_dict_get.reset_mock()
        leases_v6 = await client._get_isc_dhcpv6_leases()
        assert leases_v6 == []
        client._safe_dict_get.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_dhcp_edge_cases_and_keep_latest(make_client) -> None:
    """Ensure DHCP parsing and _keep_latest_leases handle odd entries."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        # kea leases: missing address/expire
        client._safe_dict_get = AsyncMock(
            side_effect=[
                {"dhcpv4": {"general": {"enabled": "1", "interfaces": {}}}},
                {"rows": [{"if_name": "em0", "hwaddr": "mac1", "hostname": "h"}]},
            ]
        )
        leases = await client._get_kea_dhcpv4_leases()
        assert isinstance(leases, list)

        # keep_latest with numeric expiries (avoid None comparison error)
        res = client._keep_latest_leases(
            [{"a": 1, "expire": 10}, {"a": 1, "expire": 20}, {"b": 2, "expire": 5}]
        )
        filtered = [item for item in res if item.get("a") == 1]
        assert len(filtered) == 1
        assert filtered[0]["expire"] == 20
        assert any(item for item in res if item.get("b") == 2)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_isc_dhcpv4_and_v6_parsing() -> None:
    """Test ISC DHCPv4/v6 parsing of 'ends' -> datetime and filtering logic."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        local_tz = datetime.now().astimezone().tzinfo
        assert local_tz is not None
        client._get_opnsense_timezone = AsyncMock(return_value=local_tz)

        # v4: ends present and in future
        future_dt = (datetime.now() + timedelta(hours=1)).strftime("%Y/%m/%d %H:%M:%S")
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
async def test_get_dhcp_leases_combined_structure() -> None:
    """Ensure get_dhcp_leases combines multiple sources and returns expected mapping."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
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
async def test_get_dhcp_leases_calls_isc_methods_independently() -> None:
    """get_dhcp_leases should call both ISC helpers regardless of top-level endpoint status."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        local_tz = datetime.now().astimezone().tzinfo
        assert local_tz is not None
        client._get_opnsense_timezone = AsyncMock(return_value=local_tz)
        client._get_kea_dhcpv4_leases = AsyncMock(
            return_value=[{"if_name": "em0", "address": "1.1.1.1", "mac": "m1"}]
        )
        client._get_dnsmasq_leases = AsyncMock(return_value=[])
        client._get_isc_dhcpv4_leases = AsyncMock(return_value=[])
        client._get_isc_dhcpv6_leases = AsyncMock(return_value=[])
        client._get_kea_interfaces = AsyncMock(return_value={"em0": "eth0"})

        combined = await client.get_dhcp_leases()

        assert "em0" in combined["leases"]
        client._get_isc_dhcpv4_leases.assert_awaited_once_with(opnsense_tz=local_tz)
        client._get_isc_dhcpv6_leases.assert_awaited_once_with(opnsense_tz=local_tz)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_kea_leases_with_reservations_and_expiry_handling() -> None:
    """Exercise _get_kea_dhcpv4_leases reservation matching and expiry logic."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        # reservation maps hw_address -> ip
        res_rows = [{"hw_address": "aa:bb", "ip_address": "192.0.2.1"}]

        # lease row matches reservation and has future expire
        future_ts = int((datetime.now().timestamp()) + 3600)
        lease_rows = [
            {
                "address": "192.0.2.1",
                "hwaddr": "aa:bb",
                "state": "0",
                "if_name": "em0",
                "expire": future_ts,
                "hostname": "h",
            }
        ]

        async def fake_safe(path):
            """Fake safe.

            Args:
                path (str): API endpoint path to request.

            Returns:
                Any: Mock value returned to support test behavior.
            """
            if path == "/api/kea/dhcpv4/search_reservation":
                return {"rows": res_rows}
            if path == "/api/kea/leases4/search":
                return {"rows": lease_rows}
            return {}

        client._safe_dict_get = AsyncMock(side_effect=fake_safe)
        leases = await client._get_kea_dhcpv4_leases()
        assert isinstance(leases, list) and len(leases) == 1
        assert leases[0].get("type") == "static"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_kea_interfaces_filters_enabled_and_selected(make_client) -> None:
    """_get_kea_interfaces should honor enabled flag and selected/value filtering."""
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
async def test_get_kea_dhcpv4_leases_covers_invalid_dynamic_and_reservations(make_client) -> None:
    """_get_kea_dhcpv4_leases should skip invalid/expired leases and classify static/dynamic."""
    client, _session = make_mock_session_client(make_client)
    try:
        future_ts = int(datetime.now().timestamp()) + 3600
        past_ts = int(datetime.now().timestamp()) - 3600

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

        # Reservation rows not being a list should be tolerated.
        client._safe_dict_get = AsyncMock(
            side_effect=[
                {"rows": [{"state": "0", "hwaddr": "aa", "address": "10.0.0.1"}]},
                {"rows": "bad"},
            ]
        )
        assert isinstance(await client._get_kea_dhcpv4_leases(), list)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_dnsmasq_leases_invalid_rows_and_expiry_paths(make_client) -> None:
    """_get_dnsmasq_leases should handle malformed rows and expiry/type branches."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(return_value={"rows": "bad-shape"})
        assert await client._get_dnsmasq_leases() == []

        past_ts = int(datetime.now().timestamp()) - 3600
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
                ]
            }
        )

        leases = await client._get_dnsmasq_leases()
        assert len(leases) == 1
        assert leases[0]["address"] == "192.0.2.22"
        assert leases[0]["type"] == "dynamic"
        assert leases[0]["mac"] is None
        assert leases[0]["expires"] == "never"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_isc_dhcpv4_and_v6_cover_invalid_and_expired_paths(make_client) -> None:
    """ISC lease helpers should skip invalid rows, bad timestamps, and expired leases."""
    client, _session = make_mock_session_client(make_client)
    try:
        local_tz = datetime.now().astimezone().tzinfo
        assert local_tz is not None
        client._get_opnsense_timezone = AsyncMock(return_value=local_tz)
        client.is_endpoint_available = AsyncMock(return_value=True)

        past_str = (datetime.now() - timedelta(hours=2)).strftime("%Y/%m/%d %H:%M:%S")

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
