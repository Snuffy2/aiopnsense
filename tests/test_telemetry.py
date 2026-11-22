"""Tests for `aiopnsense.telemetry`."""

from collections.abc import Callable, MutableMapping
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseClient, OPNsenseError, OPNsensePrivilegeMissing
from tests.conftest import FakeRouter, install_fake_router, make_mock_session_client

ClientType = Callable[..., OPNsenseClient]

INTERFACE_PATH = "/api/interfaces/overview/export"
MBUF_PATH = "/api/diagnostics/system/system_mbuf"
PF_STATES_PATH = "/api/diagnostics/firewall/pf_states"
RESOURCES_PATH = "/api/diagnostics/system/system_resources"
SWAP_PATH = "/api/diagnostics/system/system_swap"
TIME_PATH = "/api/diagnostics/system/system_time"
CPU_TYPE_PATH = "/api/diagnostics/cpu_usage/get_c_p_u_type"
CPU_STREAM_PATH = "/api/diagnostics/cpu_usage/stream"
DISK_PATH = "/api/diagnostics/system/system_disk"
GATEWAY_PATH = "/api/routes/gateway/status"
TEMPERATURE_PATH = "/api/diagnostics/system/system_temperature"

MEMORY_EMPTY: dict[str, Any] = {"physmem": None, "used": None, "used_percent": None}
CPU_TYPE_PAYLOAD = ["Intel(R) Xeon (4 cores, 8 threads)"]
MEMORY_PAYLOAD: dict[str, Any] = {"memory": {"total": "100", "used": "50"}}
TIME_PAYLOAD: dict[str, Any] = {
    "datetime": "2026-03-07 12:00:00",
    "boottime": "2026-03-06 12:00:00",
    "uptime": "1 days, 00:00:00",
    "loadavg": "1.00, 0.50, 0.25",
}

# Each telemetry read with the endpoint it requests and the value it returns when that endpoint is unavailable.
EMPTY_READS = [
    pytest.param("get_interfaces", INTERFACE_PATH, {}, id="interfaces"),
    pytest.param("_get_telemetry_mbuf", MBUF_PATH, {}, id="mbuf"),
    pytest.param("_get_telemetry_pfstate", PF_STATES_PATH, {}, id="pfstate"),
    pytest.param("_get_telemetry_memory", RESOURCES_PATH, MEMORY_EMPTY, id="memory"),
    pytest.param("_get_telemetry_system", TIME_PATH, {}, id="system"),
    pytest.param("_get_telemetry_cpu", CPU_TYPE_PATH, {}, id="cpu"),
    pytest.param("_get_telemetry_filesystems", DISK_PATH, [], id="filesystems"),
    pytest.param("get_gateways", GATEWAY_PATH, {}, id="gateways"),
    pytest.param("_get_telemetry_temps", TEMPERATURE_PATH, {}, id="temps"),
]

# A 200 payload for each telemetry read's endpoint.
PRESENT_PAYLOADS: dict[str, Any] = {
    "get_interfaces": [{"identifier": "em0", "description": "lan", "status": "up"}],
    "_get_telemetry_mbuf": {"mbuf-statistics": {"mbuf-current": "10", "mbuf-total": "20"}},
    "_get_telemetry_pfstate": {"current": "5", "limit": "10"},
    "_get_telemetry_memory": MEMORY_PAYLOAD,
    "_get_telemetry_system": TIME_PAYLOAD,
    "_get_telemetry_cpu": CPU_TYPE_PAYLOAD,
    "_get_telemetry_filesystems": {"devices": [{"dev": "/dev/da0"}]},
    "get_gateways": {"items": [{"name": "WAN_DHCP", "status_translated": "Online"}]},
    "_get_telemetry_temps": [{"temperature": "45.5", "type_translated": "CPU", "device_seq": 0}],
}


def _client_with_router(
    make_client: ClientType,
    routes: dict[tuple[str, str], Any],
    *,
    throw_errors: bool = False,
) -> tuple[OPNsenseClient, FakeRouter]:
    """Return a snake_case client backed by a fake Router answering ``routes``.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        routes (dict[tuple[str, str], Any]): Values keyed by ``(method, path)``.
        throw_errors (bool): Whether the client should raise public errors.

    Returns:
        tuple[OPNsenseClient, FakeRouter]: Client and the fake Router recording its requests.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes=routes)
    client._use_snake_case = True
    if throw_errors:
        client.toggle_throwing_errors(True)
    return client, router


@pytest.mark.asyncio
@pytest.mark.parametrize("read, path, empty", EMPTY_READS)
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(404, id="absent-404"),
        pytest.param(403, id="forbidden-403"),
        pytest.param(500, id="transient-500"),
        pytest.param(TimeoutError(), id="transient-timeout"),
    ],
)
async def test_telemetry_read_returns_empty_value_when_endpoint_fails(
    make_client: ClientType, read: str, path: str, empty: Any, failure: Any
) -> None:
    """Each telemetry read returns its empty value for 404, 403 and transient failures.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        read (str): Name of the telemetry read method.
        path (str): Endpoint path the read requests.
        empty (Any): Value the read returns when the endpoint is unavailable.
        failure (Any): HTTP status or exception the fake Router answers with.
    """
    client, router = _client_with_router(make_client, {("GET", path): failure})
    try:
        assert await getattr(client, read)() == empty
        assert router.count("GET", path) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("read, path, empty", EMPTY_READS)
async def test_telemetry_read_costs_one_request_when_endpoint_present(
    make_client: ClientType, read: str, path: str, empty: Any
) -> None:
    """A present telemetry endpoint is requested once per read, with no separate probe.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        read (str): Name of the telemetry read method.
        path (str): Endpoint path the read requests.
        empty (Any): Value the read returns when the endpoint is unavailable.
    """
    client, router = _client_with_router(make_client, {("GET", path): PRESENT_PAYLOADS[read]})
    client._get_from_stream = AsyncMock(return_value={})
    try:
        assert await getattr(client, read)() != empty
        assert router.count("GET", path) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("read, path, empty", EMPTY_READS)
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(403, id="forbidden-403"),
        pytest.param(500, id="transient-500"),
        pytest.param(TimeoutError(), id="transient-timeout"),
    ],
)
async def test_telemetry_read_raises_for_forbidden_or_transient_failure_in_throw_mode(
    make_client: ClientType, read: str, path: str, empty: Any, failure: Any
) -> None:
    """In throw mode a 403 or transient failure raises an ``OPNsenseError`` instead of the empty value.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        read (str): Name of the telemetry read method.
        path (str): Endpoint path the read requests.
        empty (Any): Value the read returns in default mode.
        failure (Any): HTTP status or exception the fake Router answers with.
    """
    client, _router = _client_with_router(make_client, {("GET", path): failure}, throw_errors=True)
    try:
        with pytest.raises(OPNsenseError):
            await getattr(client, read)()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("read, path, empty", EMPTY_READS)
async def test_telemetry_read_returns_empty_without_raising_for_absent_endpoint_in_throw_mode(
    make_client: ClientType, read: str, path: str, empty: Any
) -> None:
    """In throw mode a 404 is still the absent endpoint and returns the empty value.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        read (str): Name of the telemetry read method.
        path (str): Endpoint path the read requests.
        empty (Any): Value the read returns for an absent endpoint.
    """
    client, _router = _client_with_router(make_client, {("GET", path): 404}, throw_errors=True)
    try:
        assert await getattr(client, read)() == empty
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_system_parsing_and_filesystems(make_client: ClientType) -> None:
    """Validate telemetry system parsing and filesystem branch behavior.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
    """
    client, _router = _client_with_router(
        make_client,
        {
            ("GET", TIME_PATH): {
                "datetime": "not-a-date",
                "uptime": "1 days, 01:02:03",
                "boottime": "also-bad",
                "loadavg": "bad",
            },
            ("GET", DISK_PATH): {"devices": [{"dev": "/dev/da0"}]},
        },
    )
    try:
        system = await client._get_telemetry_system()
        assert isinstance(system, MutableMapping)
        assert system["uptime"] == 90123
        assert "boottime" in system
        assert system["load_average"] == {
            "one_minute": None,
            "five_minute": None,
            "fifteen_minute": None,
        }

        files = await client._get_telemetry_filesystems()
        assert files == [{"dev": "/dev/da0"}]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_cpu_variants(make_client: ClientType) -> None:
    """Validate CPU telemetry behavior for empty and valid stream branches.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
    """
    client, router = _client_with_router(
        make_client,
        {("GET", CPU_TYPE_PATH): [], ("GET", CPU_STREAM_PATH): 200},
    )
    try:
        assert await client._get_telemetry_cpu() == {}

        router.routes[("GET", CPU_TYPE_PATH)] = CPU_TYPE_PAYLOAD
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
        assert cpu["count"] == 4
        assert cpu["usage_total"] == 29
        assert cpu["usage_idle"] == 70
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    "stream_answer",
    [
        pytest.param(404, id="absent-404"),
        pytest.param(403, id="forbidden-403"),
        pytest.param(500, id="transient-500"),
    ],
)
@pytest.mark.asyncio
async def test_telemetry_cpu_omits_usage_when_stream_unavailable(
    make_client: ClientType, stream_answer: int
) -> None:
    """A CPU stream the Router does not serve leaves only the core count, without reading the stream.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
        stream_answer (int): HTTP status the Router returns for the CPU stream endpoint.
    """
    client, router = _client_with_router(
        make_client,
        {
            ("GET", CPU_TYPE_PATH): ["Intel (2 cores)"],
            ("GET", CPU_STREAM_PATH): stream_answer,
        },
    )
    try:
        client._get_from_stream = AsyncMock()

        assert await client._get_telemetry_cpu() == {"count": 2}
        assert router.count("GET", CPU_STREAM_PATH) == 1
        client._get_from_stream.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_cpu_raises_permission_error_for_forbidden_stream_in_throw_mode(
    make_client: ClientType,
) -> None:
    """A 403 on the CPU stream raises in throw mode instead of omitting the usage fields.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
    """
    client, _router = _client_with_router(
        make_client,
        {("GET", CPU_TYPE_PATH): ["Intel (2 cores)"], ("GET", CPU_STREAM_PATH): 403},
        throw_errors=True,
    )
    try:
        with pytest.raises(OPNsensePrivilegeMissing):
            await client._get_telemetry_cpu()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_mbuf_pfstate_and_temps(make_client: ClientType) -> None:
    """Validate telemetry parsing for mbuf, pfstate, and temperature sensors.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
    """
    client, _router = _client_with_router(
        make_client,
        {
            ("GET", MBUF_PATH): {"mbuf-statistics": {"mbuf-current": "10", "mbuf-total": "20"}},
            ("GET", PF_STATES_PATH): {"current": "5", "limit": "10"},
            ("GET", TEMPERATURE_PATH): [
                {"temperature": "45.5", "type_translated": "CPU", "device_seq": 0}
            ],
        },
    )
    try:
        mbuf = await client._get_telemetry_mbuf()
        pf = await client._get_telemetry_pfstate()
        assert mbuf == {"used": 10, "total": 20, "used_percent": 50}
        assert pf == {"used": 5, "total": 10, "used_percent": 50}

        temps = await client._get_telemetry_temps()
        assert isinstance(temps, MutableMapping) and len(temps) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_interfaces_status_variants(make_client: ClientType) -> None:
    """Validate interface parsing for status normalization and MAC filtering.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
    """
    client, _router = _client_with_router(
        make_client,
        {
            ("GET", INTERFACE_PATH): [
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
        },
    )
    try:
        interfaces = await client.get_interfaces()
        assert "em0" in interfaces and interfaces["em0"]["status"] == "down"
        assert "em1" in interfaces and interfaces["em1"]["status"] == "up"
        # em1 mac should be filtered out because it's 00:00:00:00:00:00
        assert "mac" not in interfaces["em1"]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_memory_swap_branches(make_client: ClientType) -> None:
    """Validate memory telemetry parsing when swap details are available.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
    """
    client, router = _client_with_router(
        make_client,
        {
            ("GET", RESOURCES_PATH): {"memory": {"total": "8000", "used": "2000"}},
            ("GET", SWAP_PATH): {"swap": [{"total": "1000", "used": "200"}]},
        },
    )
    try:
        memory = await client._get_telemetry_memory()
        assert memory == {
            "physmem": 8000,
            "used": 2000,
            "used_percent": 25,
            "swap_total": 1000,
            "swap_reserved": 200,
            "swap_used_percent": 20,
        }
        assert router.count("GET", RESOURCES_PATH) == 1
        assert router.count("GET", SWAP_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_telemetry_aggregates_sections(make_client: ClientType) -> None:
    """Validate that telemetry aggregation combines all section helper outputs.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
    """
    client, _router = _client_with_router(make_client, {})
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
async def test_get_interfaces_empty_and_invalid_rows(make_client: ClientType) -> None:
    """Validate interface helper behavior for empty and malformed row payloads.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
    """
    client, router = _client_with_router(make_client, {("GET", INTERFACE_PATH): []})
    try:
        assert await client.get_interfaces() == {}

        router.routes[("GET", INTERFACE_PATH)] = [
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
        interfaces = await client.get_interfaces()
        assert list(interfaces) == ["em0"]
        assert interfaces["em0"]["status"] == ""
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_memory_returns_without_swap_details(make_client: ClientType) -> None:
    """Validate memory telemetry when swap payload shape is malformed.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
    """
    client, _router = _client_with_router(
        make_client,
        {
            ("GET", RESOURCES_PATH): {"memory": {"total": "4000", "used": "1000"}},
            ("GET", SWAP_PATH): {"swap": "bad-shape"},
        },
    )
    try:
        memory = await client._get_telemetry_memory()
        assert memory["physmem"] == 4000
        assert memory["used"] == 1000
        assert "swap_total" not in memory
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_memory_returns_base_payload_when_swap_endpoint_missing(
    make_client: ClientType,
) -> None:
    """Telemetry memory should keep base memory fields when swap endpoint is absent.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
    """
    client, router = _client_with_router(
        make_client,
        {
            ("GET", RESOURCES_PATH): MEMORY_PAYLOAD,
            ("GET", SWAP_PATH): 404,
        },
    )
    try:
        memory = await client._get_telemetry_memory()

        assert memory == {
            "physmem": 100,
            "used": 50,
            "used_percent": 50,
        }
        assert router.count("GET", RESOURCES_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_system_branches_with_boottime_and_uptime_variants(
    make_client: ClientType,
) -> None:
    """Validate system telemetry boottime parsing and uptime fallback branches.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
    """
    client, router = _client_with_router(make_client, {("GET", TIME_PATH): TIME_PAYLOAD})
    try:
        matched = await client._get_telemetry_system()
        assert isinstance(matched.get("boottime"), float)
        assert matched.get("uptime") == 86400
        assert matched.get("load_average", {}).get("one_minute") == 1.0

        router.routes[("GET", TIME_PATH)] = {**TIME_PAYLOAD, "uptime": "bad-format"}
        unmatched = await client._get_telemetry_system()
        assert isinstance(unmatched.get("boottime"), float)
        assert isinstance(unmatched.get("uptime"), int)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_system_invalid_uptime_without_boottime(make_client: ClientType) -> None:
    """Validate system telemetry when uptime and boottime are missing or malformed.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
    """
    client, _router = _client_with_router(
        make_client,
        {
            ("GET", TIME_PATH): {
                "datetime": "2026-03-07 12:00:00",
                "uptime": "bad-format",
                "loadavg": "2.00, 1.00, 0.50",
            }
        },
    )
    try:
        system = await client._get_telemetry_system()
        assert "boottime" not in system
        assert system["load_average"]["one_minute"] == 2.0
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_gateways_skips_rows_without_name(make_client: ClientType) -> None:
    """Validate gateway parsing skips malformed entries without a name field.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
    """
    client, _router = _client_with_router(
        make_client,
        {
            ("GET", GATEWAY_PATH): {
                "items": [
                    {"status_translated": "Online"},
                    {"name": "gw1", "status_translated": "Online"},
                    "bad-row",
                ]
            }
        },
    )
    try:
        gateways = await client.get_gateways()
        assert list(gateways) == ["gw1"]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_temps_empty_returns_empty_mapping(make_client: ClientType) -> None:
    """Validate temperature telemetry returns an empty mapping for empty payloads.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
    """
    client, _router = _client_with_router(make_client, {("GET", TEMPERATURE_PATH): []})
    try:
        assert await client._get_telemetry_temps() == {}
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("use_snake_case", "endpoints"),
    [
        pytest.param(
            True,
            (
                "/api/diagnostics/system/system_resources",
                "/api/diagnostics/system/system_time",
                "/api/diagnostics/cpu_usage/get_c_p_u_type",
                "/api/diagnostics/system/system_disk",
                "/api/diagnostics/system/system_temperature",
            ),
            id="snake-case",
        ),
        pytest.param(
            False,
            (
                "/api/diagnostics/system/systemResources",
                "/api/diagnostics/system/systemTime",
                "/api/diagnostics/cpu_usage/getCPUType",
                "/api/diagnostics/system/systemDisk",
                "/api/diagnostics/system/systemTemperature",
            ),
            id="camel-case",
        ),
    ],
)
@pytest.mark.asyncio
async def test_telemetry_switched_endpoints_follow_selected_case(
    make_client: ClientType, use_snake_case: bool, endpoints: tuple[str, ...]
) -> None:
    """Verify telemetry reads request only the endpoint style selected by the firmware.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
        use_snake_case (bool): Whether the client should prefer snake_case endpoints.
        endpoints (tuple[str, ...]): Memory, time, CPU type, disk and temperature paths
            for the selected endpoint style.
    """
    payloads: tuple[Any, ...] = (
        MEMORY_PAYLOAD,
        TIME_PAYLOAD,
        CPU_TYPE_PAYLOAD,
        {"devices": []},
        [],
    )
    client, router = _client_with_router(
        make_client,
        {("GET", path): payload for path, payload in zip(endpoints, payloads, strict=True)},
    )
    try:
        client._use_snake_case = use_snake_case
        client._get_from_stream = AsyncMock(return_value={})

        await client._get_telemetry_memory()
        await client._get_telemetry_system()
        await client._get_telemetry_cpu()
        await client._get_telemetry_filesystems()
        await client._get_telemetry_temps()

        requested = {url.removeprefix(client._url) for _method, url, _body in router.requests}
        other_style = {
            "/api/diagnostics/system/system_resources",
            "/api/diagnostics/system/system_time",
            "/api/diagnostics/cpu_usage/get_c_p_u_type",
            "/api/diagnostics/system/system_disk",
            "/api/diagnostics/system/system_temperature",
            "/api/diagnostics/system/systemResources",
            "/api/diagnostics/system/systemTime",
            "/api/diagnostics/cpu_usage/getCPUType",
            "/api/diagnostics/system/systemDisk",
            "/api/diagnostics/system/systemTemperature",
        } - set(endpoints)
        assert set(endpoints) <= requested
        assert not requested & other_style
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_telemetry_system_aware_datetime_boottime_and_load_fallback(
    make_client: ClientType,
) -> None:
    """System telemetry should preserve aware timestamps and fallback malformed load average.

    Args:
        make_client (ClientType): Fixture factory used to create client instances.
    """
    client, _router = _client_with_router(
        make_client,
        {
            ("GET", TIME_PATH): {
                "datetime": "2026-03-07T12:00:00-05:00",
                "boottime": "2026-03-07T11:00:00-05:00",
                "uptime": "bad-format",
                "loadavg": "bad-load",
            }
        },
    )
    try:
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
