"""Tests for `aiopnsense.telemetry`."""

from collections.abc import MutableMapping
from datetime import UTC
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

import aiopnsense as pyopnsense
from tests.conftest import make_mock_session_client


@pytest.mark.asyncio
async def test_telemetry_system_parsing_and_filesystems() -> None:
    """Test telemetry system parsing when boottime missing/invalid and filesystems path."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        # time_info with bad datetime and uptime matching regex
        time_info = {
            "datetime": "not-a-date",
            "uptime": "1 days, 01:02:03",
            "boottime": "also-bad",
            "loadavg": "bad",
        }

        async def fake_safe_get(path: str) -> dict[str, Any]:
            """Fake safe get.

            Args:
                path (str): API endpoint path to request.

            Returns:
                dict[str, Any]: Mapping containing normalized fields for downstream use.
            """
            if "system_time" in path:
                return time_info
            if "system_disk" in path:
                return {"devices": [{"dev": "/dev/da0"}]}
            return {}

        client._safe_dict_get = AsyncMock(side_effect=fake_safe_get)
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)

        sys = await client._get_telemetry_system()
        assert isinstance(sys, MutableMapping)
        # At least one of the expected fields is normalized/present
        assert any(k in sys for k in ("uptime", "boottime", "loadavg"))
        client._get_opnsense_timezone.assert_awaited_once_with("not-a-date")

        files = await client._get_telemetry_filesystems()
        assert files is None or isinstance(files, list)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_cpu_variants() -> None:
    """Test _get_telemetry_cpu behavior for empty cputype list and valid stream."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        # empty cpu type -> returns {}
        client._safe_list_get = AsyncMock(return_value=[])
        cpu_empty = await client._get_telemetry_cpu()
        assert cpu_empty == {}

        # valid cpu type and stream
        client._safe_list_get = AsyncMock(return_value=["Intel (2 cores)"])
        client._get_from_stream = AsyncMock(
            return_value={
                "total": "29",
                "user": "2",
                "nice": "0",
                "sys": "27",
                "intr": "0",
                "idle": "70",
            }
        )
        cpu = await client._get_telemetry_cpu()
        assert isinstance(cpu.get("count"), int)
        assert cpu.get("usage_total") == 29
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_mbuf_pfstate_and_temps() -> None:
    """Test telemetry mbuf, pfstate and temps parsing branches."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        # mbuf and pfstate basic numeric parsing
        client._safe_dict_get = AsyncMock(
            side_effect=[
                {"mbuf-statistics": {"mbuf-current": "10", "mbuf-total": "20"}},
                {"current": "5", "limit": "10"},
            ]
        )
        mbuf = await client._get_telemetry_mbuf()
        pf = await client._get_telemetry_pfstate()
        assert mbuf.get("used") == 10 and mbuf.get("total") == 20
        assert pf.get("used") == 5 and pf.get("total") == 10

        # temps: return list with one entry
        client._safe_list_get = AsyncMock(
            return_value=[{"temperature": "45.5", "type_translated": "CPU", "device_seq": 0}]
        )
        temps = await client._get_telemetry_temps()
        assert isinstance(temps, MutableMapping) and len(temps) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_interfaces_status_variants() -> None:
    """Ensure interface parsing handles status, associated mapping and mac filtering."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        # prepare list with various status and mac strings
        iface_list = [
            {
                "identifier": "em0",
                "description": "eth0",
                "status": "down",
                "macaddr": "aa:bb:cc:dd:ee:ff",
            },
            {
                "identifier": "em1",
                "description": "eth1",
                "status": "associated",
                "macaddr": "00:00:00:00:00:00",
            },
            {
                "identifier": "em2",
                "description": "eth2",
                "status": "up",
                "macaddr": "11:22:33:44:55:66",
            },
        ]

        client._safe_list_get = AsyncMock(return_value=iface_list)
        interfaces = await client.get_interfaces()
        assert "em0" in interfaces and interfaces["em0"]["status"] == "down"
        assert "em1" in interfaces and interfaces["em1"]["status"] == "up"
        # em1 mac should be filtered out because it's 00:00:00:00:00:00
        assert "mac" not in interfaces["em1"]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_memory_swap_branches() -> None:
    """Cover telemetry memory path including swap data branch."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        # prepare memory info with swap list present
        mem = {"memory": {"total": "8000", "used": "2000"}}
        swap = {"swap": [{"total": "1000", "used": "200"}]}

        async def fake_get(path: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            """Fake get.

            Args:
                path (str): API endpoint path to request.
                *_args (Any):  args used by this operation.
                **_kwargs (Any):  kwargs used by this operation.

            Returns:
                dict[str, Any]: Mapping containing normalized fields for downstream use.
            """
            if "system_resources" in path:
                return mem
            if "system_swap" in path:
                return swap
            return {}

        client._safe_dict_get = AsyncMock(side_effect=fake_get)
        res = await client._get_telemetry_memory()
        assert isinstance(res.get("physmem"), int) or res.get("physmem") is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_telemetry_aggregates_sections(make_client) -> None:
    """get_telemetry should aggregate all section helpers into one mapping."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._get_telemetry_mbuf = AsyncMock(return_value={"used": 1})
        client._get_telemetry_pfstate = AsyncMock(return_value={"used": 2})
        client._get_telemetry_memory = AsyncMock(return_value={"used": 3})
        client._get_telemetry_system = AsyncMock(return_value={"uptime": 4})
        client._get_telemetry_cpu = AsyncMock(return_value={"count": 5})
        client._get_telemetry_filesystems = AsyncMock(return_value=[{"fs": "/"}])
        client._get_telemetry_temps = AsyncMock(return_value={"cpu_0": {"temperature": 45.0}})

        telemetry = await client.get_telemetry()
        assert telemetry["mbuf"]["used"] == 1
        assert telemetry["pfstate"]["used"] == 2
        assert telemetry["memory"]["used"] == 3
        assert telemetry["system"]["uptime"] == 4
        assert telemetry["cpu"]["count"] == 5
        assert telemetry["filesystems"][0]["fs"] == "/"
        assert "cpu_0" in telemetry["temps"]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_interfaces_empty_and_invalid_rows(make_client) -> None:
    """get_interfaces should return empty for no rows and skip invalid row entries."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_list_get = AsyncMock(return_value=[])
        assert await client.get_interfaces() == {}

        client._safe_list_get = AsyncMock(
            return_value=[
                None,
                {"identifier": ""},
                {
                    "identifier": "em0",
                    "description": "lan",
                    "status": "unknown-status",
                    "statistics": {},
                    "macaddr": "aa:bb:cc:dd:ee:ff",
                },
            ]
        )
        interfaces = await client.get_interfaces()
        assert list(interfaces) == ["em0"]
        assert interfaces["em0"]["status"] == ""
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_memory_returns_without_swap_details(make_client) -> None:
    """_get_telemetry_memory should return memory-only payload when swap data is malformed."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(
            side_effect=[
                {"memory": {"total": "4000", "used": "1000"}},
                {"swap": "bad-shape"},
            ]
        )
        memory = await client._get_telemetry_memory()
        assert memory["physmem"] == 4000
        assert memory["used"] == 1000
        assert "swap_total" not in memory
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_system_branches_with_boottime_and_uptime_variants(make_client) -> None:
    """_get_telemetry_system should handle boottime parsing and uptime match/non-match branches."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)

        client._safe_dict_get = AsyncMock(
            return_value={
                "datetime": "2026-03-07 12:00:00",
                "boottime": "2026-03-06 12:00:00",
                "uptime": "1 days, 00:00:00",
                "loadavg": "1.00, 0.50, 0.25",
            }
        )
        matched = await client._get_telemetry_system()
        assert isinstance(matched.get("boottime"), float)
        assert matched.get("uptime") == 86400
        assert matched.get("load_average", {}).get("one_minute") == 1.0

        client._safe_dict_get = AsyncMock(
            return_value={
                "datetime": "2026-03-07 12:00:00",
                "boottime": "2026-03-06 12:00:00",
                "uptime": "bad-format",
                "loadavg": "1.00, 0.50, 0.25",
            }
        )
        unmatched = await client._get_telemetry_system()
        assert isinstance(unmatched.get("boottime"), float)
        assert isinstance(unmatched.get("uptime"), int)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_system_invalid_uptime_without_boottime(make_client) -> None:
    """_get_telemetry_system should still return load average when uptime and boottime are invalid."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)
        client._safe_dict_get = AsyncMock(
            return_value={
                "datetime": "2026-03-07 12:00:00",
                "uptime": "bad-format",
                "loadavg": "2.00, 1.00, 0.50",
            }
        )
        system = await client._get_telemetry_system()
        assert "boottime" not in system
        assert system["load_average"]["one_minute"] == 2.0
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_gateways_skips_rows_without_name(make_client) -> None:
    """get_gateways should skip malformed gateway entries that do not have a name."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(
            return_value={
                "items": [
                    {"status_translated": "Online"},
                    {"name": "gw1", "status_translated": "Online"},
                    "bad-row",
                ]
            }
        )
        gateways = await client.get_gateways()
        assert list(gateways) == ["gw1"]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_temps_empty_returns_empty_mapping(make_client) -> None:
    """_get_telemetry_temps should return an empty mapping when no sensor rows are returned."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_list_get = AsyncMock(return_value=[])
        assert await client._get_telemetry_temps() == {}
    finally:
        await client.async_close()
