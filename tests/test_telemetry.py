"""Tests for `aiopnsense.telemetry`."""

from collections.abc import Callable, MutableMapping
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiopnsense._endpoint_outcome import _EndpointOutcome
from tests.conftest import make_mock_session_client, stub_endpoint_outcome


@pytest.mark.asyncio
async def test_telemetry_system_parsing_and_filesystems(
    make_client: Callable[..., Any],
) -> None:
    """Validate telemetry system parsing and filesystem branch behavior.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates parsing fallbacks and filesystem response handling.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        # time_info with bad datetime and uptime matching regex
        time_info = {
            "datetime": "not-a-date",
            "uptime": "1 days, 01:02:03",
            "boottime": "also-bad",
            "loadavg": "bad",
        }

        async def fake_fetch(path: str) -> _EndpointOutcome:
            """Return fixture outcomes for telemetry endpoint paths.

            Args:
                path (str): API endpoint path to request.

            Returns:
                _EndpointOutcome: Data outcome with the system time, disk device, or
                    empty fixture payload selected by the requested path.
            """
            if "system_time" in path:
                return _EndpointOutcome.data(time_info)
            if "system_disk" in path:
                return _EndpointOutcome.data({"devices": [{"dev": "/dev/da0"}]})
            return _EndpointOutcome.data({})

        read = stub_endpoint_outcome(client, _EndpointOutcome.data({}))
        read.side_effect = fake_fetch
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)

        sys = await client._get_telemetry_system()
        assert isinstance(sys, MutableMapping)
        # At least one of the expected fields is normalized/present
        assert any(k in sys for k in ("uptime", "boottime", "loadavg"))
        client._get_opnsense_timezone.assert_awaited_once_with("not-a-date")

        files = await client._get_telemetry_filesystems()
        assert isinstance(files, list)
        assert all(isinstance(filesystem, MutableMapping) for filesystem in files)
        assert all(isinstance(filesystem.get("dev"), str) for filesystem in files)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_cpu_variants(make_client: Callable[..., Any]) -> None:
    """Validate CPU telemetry behavior for empty, unavailable, and valid stream branches.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates CPU count and usage normalization across branches.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        # empty cpu type -> returns {}
        stub_endpoint_outcome(client, _EndpointOutcome.data([]))
        cpu_empty = await client._get_telemetry_cpu()
        assert cpu_empty == {}

        # available cpu type but unavailable stream endpoint -> fail closed
        stub_endpoint_outcome(client, _EndpointOutcome.data(["Intel (2 cores)"]))
        stub_endpoint_outcome(client, _EndpointOutcome.absent(), read="status")
        client._get_from_stream = AsyncMock()
        cpu_stream_unavailable = await client._get_telemetry_cpu()
        assert cpu_stream_unavailable == {"count": 2}
        client._get_from_stream.assert_not_awaited()

        # valid cpu type and stream
        stub_endpoint_outcome(client, _EndpointOutcome.data(["Intel (2 cores)"]))
        stub_endpoint_outcome(client, _EndpointOutcome.data(None), read="status")
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
async def test_telemetry_mbuf_pfstate_and_temps(make_client: Callable[..., Any]) -> None:
    """Validate telemetry parsing for mbuf, pfstate, and temperature sensors.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates numeric parsing and temperature mapping behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        # mbuf and pfstate basic numeric parsing
        read = stub_endpoint_outcome(client, _EndpointOutcome.data({}))
        read.side_effect = [
            _EndpointOutcome.data({"mbuf-statistics": {"mbuf-current": "10", "mbuf-total": "20"}}),
            _EndpointOutcome.data({"current": "5", "limit": "10"}),
        ]
        mbuf = await client._get_telemetry_mbuf()
        pf = await client._get_telemetry_pfstate()
        assert mbuf.get("used") == 10 and mbuf.get("total") == 20
        assert pf.get("used") == 5 and pf.get("total") == 10

        # temps: return list with one entry
        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                [{"temperature": "45.5", "type_translated": "CPU", "device_seq": 0}]
            ),
        )
        temps = await client._get_telemetry_temps()
        assert isinstance(temps, MutableMapping) and len(temps) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_interfaces_status_variants(make_client: Callable[..., Any]) -> None:
    """Validate interface parsing for status normalization and MAC filtering.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates associated-status mapping and zero-MAC filtering.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
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

        stub_endpoint_outcome(client, _EndpointOutcome.data(iface_list))
        interfaces = await client.get_interfaces()
        assert "em0" in interfaces and interfaces["em0"]["status"] == "down"
        assert "em1" in interfaces and interfaces["em1"]["status"] == "up"
        # em1 mac should be filtered out because it's 00:00:00:00:00:00
        assert "mac" not in interfaces["em1"]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_memory_swap_branches(make_client: Callable[..., Any]) -> None:
    """Validate memory telemetry parsing when swap details are available.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates base memory parsing and swap enrichment behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        # prepare memory info with swap list present
        mem = {"memory": {"total": "8000", "used": "2000"}}
        swap = {"swap": [{"total": "1000", "used": "200"}]}

        async def fake_get(path: str, *_args: Any, **_kwargs: Any) -> _EndpointOutcome:
            """Return fixture outcomes for memory and swap endpoint paths.

            Args:
                path (str): API endpoint path to request.
                _args (Any): Positional arguments accepted by `fake_get`.
                _kwargs (Any): Keyword arguments accepted by `fake_get`.

            Returns:
                _EndpointOutcome: Data outcome with the memory, swap, or empty fixture
                    payload selected by the requested path.
            """
            if "system_resources" in path:
                return _EndpointOutcome.data(mem)
            if "system_swap" in path:
                return _EndpointOutcome.data(swap)
            return _EndpointOutcome.data({})

        read = stub_endpoint_outcome(client, _EndpointOutcome.data({}))
        read.side_effect = fake_get
        res = await client._get_telemetry_memory()
        assert isinstance(res.get("physmem"), int) or res.get("physmem") is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_telemetry_aggregates_sections(make_client: Callable[..., Any]) -> None:
    """Validate that telemetry aggregation combines all section helper outputs.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates section aggregation into the telemetry payload.
    """
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
async def test_get_interfaces_empty_and_invalid_rows(make_client: Callable[..., Any]) -> None:
    """Validate interface helper behavior for empty and malformed row payloads.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates empty-return and invalid-row filtering behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        stub_endpoint_outcome(client, _EndpointOutcome.data([]))
        assert await client.get_interfaces() == {}

        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                [
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
            ),
        )
        interfaces = await client.get_interfaces()
        assert list(interfaces) == ["em0"]
        assert interfaces["em0"]["status"] == ""
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_memory_returns_without_swap_details(
    make_client: Callable[..., Any],
) -> None:
    """Validate memory telemetry when swap payload shape is malformed.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates that memory fields are returned without swap enrichment.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        read = stub_endpoint_outcome(client, _EndpointOutcome.data({}))
        read.side_effect = [
            _EndpointOutcome.data({"memory": {"total": "4000", "used": "1000"}}),
            _EndpointOutcome.data({"swap": "bad-shape"}),
        ]
        memory = await client._get_telemetry_memory()
        assert memory["physmem"] == 4000
        assert memory["used"] == 1000
        assert "swap_total" not in memory
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_system_branches_with_boottime_and_uptime_variants(
    make_client: Callable[..., Any],
) -> None:
    """Validate system telemetry boottime parsing and uptime fallback branches.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates matched and unmatched uptime parsing behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)

        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {
                    "datetime": "2026-03-07 12:00:00",
                    "boottime": "2026-03-06 12:00:00",
                    "uptime": "1 days, 00:00:00",
                    "loadavg": "1.00, 0.50, 0.25",
                }
            ),
        )
        matched = await client._get_telemetry_system()
        assert isinstance(matched.get("boottime"), float)
        assert matched.get("uptime") == 86400
        assert matched.get("load_average", {}).get("one_minute") == 1.0

        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {
                    "datetime": "2026-03-07 12:00:00",
                    "boottime": "2026-03-06 12:00:00",
                    "uptime": "bad-format",
                    "loadavg": "1.00, 0.50, 0.25",
                }
            ),
        )
        unmatched = await client._get_telemetry_system()
        assert isinstance(unmatched.get("boottime"), float)
        assert isinstance(unmatched.get("uptime"), int)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_system_invalid_uptime_without_boottime(
    make_client: Callable[..., Any],
) -> None:
    """Validate system telemetry when uptime and boottime are missing or malformed.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates load-average parsing without boottime fallback data.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)
        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {
                    "datetime": "2026-03-07 12:00:00",
                    "uptime": "bad-format",
                    "loadavg": "2.00, 1.00, 0.50",
                }
            ),
        )
        system = await client._get_telemetry_system()
        assert "boottime" not in system
        assert system["load_average"]["one_minute"] == 2.0
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_gateways_skips_rows_without_name(make_client: Callable[..., Any]) -> None:
    """Validate gateway parsing skips malformed entries without a name field.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates malformed-row filtering for gateway payloads.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {
                    "items": [
                        {"status_translated": "Online"},
                        {"name": "gw1", "status_translated": "Online"},
                        "bad-row",
                    ]
                }
            ),
        )
        gateways = await client.get_gateways()
        assert list(gateways) == ["gw1"]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_temps_empty_returns_empty_mapping(
    make_client: Callable[..., Any],
) -> None:
    """Validate temperature telemetry returns an empty mapping for empty payloads.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates fail-closed behavior for empty temperature rows.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        stub_endpoint_outcome(client, _EndpointOutcome.data([]))
        assert await client._get_telemetry_temps() == {}
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_version_switched_telemetry_endpoints_return_empty_data(
    make_client: Callable[..., Any],
) -> None:
    """Switched telemetry endpoints should return empty structures when unavailable.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates behavior via assertions only.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        read = stub_endpoint_outcome(client, _EndpointOutcome.absent())

        assert await client._get_telemetry_memory() == {
            "physmem": None,
            "used": None,
            "used_percent": None,
        }
        assert await client._get_telemetry_system() == {}
        assert await client._get_telemetry_cpu() == {}
        assert await client._get_telemetry_filesystems() == []
        assert await client._get_telemetry_temps() == {}
        # One request per read; no payload is used when the endpoint is absent.
        assert read.await_count == 5
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_memory_returns_base_payload_when_swap_endpoint_missing(
    make_client: Callable[..., Any],
) -> None:
    """Telemetry memory should keep base memory fields when swap endpoint is unavailable.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates behavior via assertions only.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        read = stub_endpoint_outcome(client, _EndpointOutcome.data({}))
        read.side_effect = [
            _EndpointOutcome.data(
                {
                    "memory": {
                        "total": "100",
                        "used": "50",
                    }
                }
            ),
            _EndpointOutcome.absent(),
        ]

        memory = await client._get_telemetry_memory()

        assert memory == {
            "physmem": 100,
            "used": 50,
            "used_percent": 50,
        }
        read.assert_any_await("/api/diagnostics/system/system_resources")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "use_snake_case",
        "expected_memory",
        "expected_system",
        "expected_cpu",
        "expected_disk",
        "expected_temp",
    ),
    [
        (
            True,
            "/api/diagnostics/system/system_resources",
            "/api/diagnostics/system/system_time",
            "/api/diagnostics/cpu_usage/get_c_p_u_type",
            "/api/diagnostics/system/system_disk",
            "/api/diagnostics/system/system_temperature",
        ),
        (
            False,
            "/api/diagnostics/system/systemResources",
            "/api/diagnostics/system/systemTime",
            "/api/diagnostics/cpu_usage/getCPUType",
            "/api/diagnostics/system/systemDisk",
            "/api/diagnostics/system/systemTemperature",
        ),
    ],
)
async def test_telemetry_switched_endpoints_follow_selected_case(
    make_client: Callable[..., Any],
    use_snake_case: bool,
    expected_memory: str,
    expected_system: str,
    expected_cpu: str,
    expected_disk: str,
    expected_temp: str,
) -> None:
    """Verify switched telemetry helpers follow the selected endpoint style.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.
        use_snake_case (bool): Whether the client should prefer snake_case endpoints.
        expected_memory (str): Expected memory endpoint path.
        expected_system (str): Expected system-time endpoint path.
        expected_cpu (str): Expected CPU-type endpoint path.
        expected_disk (str): Expected filesystem endpoint path.
        expected_temp (str): Expected temperature endpoint path.

    Returns:
        None: This test validates telemetry endpoint selection behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = use_snake_case
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)
        client._get_from_stream = AsyncMock(return_value={})

        read = stub_endpoint_outcome(
            client, _EndpointOutcome.data({"memory": {"total": "100", "used": "50"}})
        )
        read.side_effect = [
            _EndpointOutcome.data({"memory": {"total": "100", "used": "50"}}),
            _EndpointOutcome.absent(),
        ]
        await client._get_telemetry_memory()
        read.assert_any_await(expected_memory)

        read = stub_endpoint_outcome(
            client,
            _EndpointOutcome.data({"datetime": "2026-03-07 12:00:00", "loadavg": "bad"}),
        )
        await client._get_telemetry_system()
        read.assert_awaited_once_with(expected_system)

        read = stub_endpoint_outcome(client, _EndpointOutcome.data(["Intel (2 cores)"]))
        stub_endpoint_outcome(client, _EndpointOutcome.data(None), read="status")
        await client._get_telemetry_cpu()
        read.assert_awaited_once_with(expected_cpu)

        read = stub_endpoint_outcome(client, _EndpointOutcome.data({"devices": []}))
        await client._get_telemetry_filesystems()
        read.assert_awaited_once_with(expected_disk)

        read = stub_endpoint_outcome(client, _EndpointOutcome.data([]))
        await client._get_telemetry_temps()
        read.assert_awaited_once_with(expected_temp)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_version_switched_telemetry_public_endpoints_fail_closed(
    make_client: Callable[..., Any],
) -> None:
    """Public telemetry endpoints should return empty data when endpoints are unavailable.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates fail-closed behavior for switched telemetry endpoints.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(client, _EndpointOutcome.absent())

        assert await client.get_interfaces() == {}
        assert await client._get_telemetry_mbuf() == {}
        assert await client._get_telemetry_pfstate() == {}
        assert await client.get_gateways() == {}
        # One request per read; no payload is used when the endpoint is absent.
        assert read.await_count == 4
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_system_aware_datetime_boottime_and_load_fallback(
    make_client: Callable[..., Any],
) -> None:
    """System telemetry should preserve aware timestamps and fallback malformed load average.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates aware timestamp parsing and malformed loadavg fallback.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)
        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {
                    "datetime": "2026-03-07T12:00:00-05:00",
                    "boottime": "2026-03-07T11:00:00-05:00",
                    "uptime": "bad-format",
                    "loadavg": "bad-load",
                }
            ),
        )

        system = await client._get_telemetry_system()
        expected_boottime = datetime.fromisoformat("2026-03-07T11:00:00-05:00").timestamp()

        assert isinstance(system["boottime"], float)
        assert system["boottime"] == expected_boottime
        assert system["uptime"] == 3600
        assert system["load_average"] == {
            "one_minute": None,
            "five_minute": None,
            "fifteen_minute": None,
        }
    finally:
        await client.async_close()
