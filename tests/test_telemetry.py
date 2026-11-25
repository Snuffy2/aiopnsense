"""Tests for `aiopnsense.telemetry`."""

from collections.abc import Mapping
from datetime import datetime
import json
import time
from typing import Any

import pytest

from aiopnsense import OPNsenseClient
from aiopnsense.exceptions import OPNsenseError, OPNsensePrivilegeMissing, OPNsenseTimeoutError
from tests.conftest import (
    FakeResponse,
    FakeRouter,
    MakeClientFactory,
    MalformedJSONResponse,
    make_router_client,
)

SNAKE_CASE_FIRMWARE = "26.1.1"
CAMELCASE_FIRMWARE = "25.1"

INTERFACES_PATH = "/api/interfaces/overview/export"
MBUF_PATH = "/api/diagnostics/system/system_mbuf"
PFSTATE_PATH = "/api/diagnostics/firewall/pf_states"
SWAP_PATH = "/api/diagnostics/system/system_swap"
CPU_STREAM_PATH = "/api/diagnostics/cpu_usage/stream"
GATEWAYS_PATH = "/api/routes/gateway/status"

# Telemetry endpoints whose path depends on the Endpoint Style the firmware selects.
SNAKE_CASE_PATHS = {
    "memory": "/api/diagnostics/system/system_resources",
    "system": "/api/diagnostics/system/system_time",
    "cpu": "/api/diagnostics/cpu_usage/get_c_p_u_type",
    "disk": "/api/diagnostics/system/system_disk",
    "temperature": "/api/diagnostics/system/system_temperature",
}
CAMELCASE_PATHS = {
    "memory": "/api/diagnostics/system/systemResources",
    "system": "/api/diagnostics/system/systemTime",
    "cpu": "/api/diagnostics/cpu_usage/getCPUType",
    "disk": "/api/diagnostics/system/systemDisk",
    "temperature": "/api/diagnostics/system/systemTemperature",
}
SYSTEM_PATH = SNAKE_CASE_PATHS["system"]
MEMORY_PATH = SNAKE_CASE_PATHS["memory"]
CPU_TYPE_PATH = SNAKE_CASE_PATHS["cpu"]
TEMPERATURE_PATH = SNAKE_CASE_PATHS["temperature"]


def _router(
    make_client: MakeClientFactory,
    routes: Mapping[str, Any],
    *,
    throw_errors: bool = False,
    firmware: str = SNAKE_CASE_FIRMWARE,
) -> tuple[OPNsenseClient, FakeRouter]:
    """Build a client against a fake Router that answers only the given GET routes.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
        routes (Mapping[str, Any]): Router state as a value for each GET path. A path
            without a value answers 404, as a missing endpoint does.
        throw_errors (bool): Public ``throw_errors`` setting of the client.
        firmware (str): Firmware version the Router reports.

    Returns:
        tuple[OPNsenseClient, FakeRouter]: Client under test and the fake Router recording its requests.
    """
    return make_router_client(
        make_client,
        {("GET", path): value for path, value in routes.items()},
        firmware=firmware,
        throw_errors=throw_errors,
    )


def _cpu_stream(usage: Mapping[str, Any]) -> FakeResponse:
    """Return the CPU usage event stream that ends with ``usage`` as its second event.

    Args:
        usage (Mapping[str, Any]): CPU usage figures of the second event.

    Returns:
        FakeResponse: Streaming response the Router answers the CPU stream path with.
    """
    return FakeResponse(
        stream_chunks=[b'data: {"total": 0}\n\ndata: ' + json.dumps(dict(usage)).encode() + b"\n\n"]
    )


@pytest.mark.parametrize(
    ("firmware", "paths"),
    [(SNAKE_CASE_FIRMWARE, SNAKE_CASE_PATHS), (CAMELCASE_FIRMWARE, CAMELCASE_PATHS)],
    ids=["snake_case", "camelCase"],
)
async def test_get_telemetry_returns_every_section_from_the_endpoints_of_the_firmware_style(
    make_client: MakeClientFactory, firmware: str, paths: dict[str, str]
) -> None:
    """Telemetry parses every section from the endpoints the Router's firmware selects.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
        firmware (str): Firmware version the Router reports.
        paths (dict[str, str]): Endpoint paths of that firmware's Endpoint Style.
    """
    client, router = _router(
        make_client,
        {
            MBUF_PATH: {"mbuf-statistics": {"mbuf-current": "10", "mbuf-total": "20"}},
            PFSTATE_PATH: {"current": "5", "limit": "10"},
            paths["memory"]: {"memory": {"total": "8000", "used": "2000"}},
            SWAP_PATH: {"swap": [{"total": "1000", "used": "200"}]},
            paths["system"]: {
                "datetime": "2026-03-07 12:00:00 UTC",
                "boottime": "2026-03-06 12:00:00 UTC",
                "uptime": "1 days, 00:00:00",
                "loadavg": "1.00, 0.50, 0.25",
            },
            paths["cpu"]: ["Intel (2 cores)"],
            CPU_STREAM_PATH: _cpu_stream(
                {"total": "29", "user": "2", "nice": "0", "sys": "27", "intr": "0", "idle": "70"}
            ),
            paths["disk"]: {"devices": [{"dev": "/dev/da0"}]},
            paths["temperature"]: [
                {
                    "temperature": "45.5",
                    "type_translated": "CPU",
                    "device_seq": 0,
                    "device": "cpu.0",
                }
            ],
        },
        firmware=firmware,
    )

    telemetry = await client.get_telemetry()

    assert telemetry["mbuf"] == {"used": 10, "total": 20, "used_percent": 50}
    assert telemetry["pfstate"] == {"used": 5, "total": 10, "used_percent": 50}
    assert telemetry["memory"] == {
        "physmem": 8000,
        "used": 2000,
        "used_percent": 25,
        "swap_total": 1000,
        "swap_reserved": 200,
        "swap_used_percent": 20,
    }
    assert telemetry["system"] == {
        "boottime": datetime.fromisoformat("2026-03-06T12:00:00+00:00").timestamp(),
        "uptime": 86400,
        "load_average": {"one_minute": 1.0, "five_minute": 0.5, "fifteen_minute": 0.25},
    }
    assert telemetry["cpu"] == {
        "count": 2,
        "usage_total": 29,
        "usage_user": 2,
        "usage_nice": 0,
        "usage_system": 27,
        "usage_interrupt": 0,
        "usage_idle": 70,
    }
    assert telemetry["filesystems"] == [{"dev": "/dev/da0"}]
    assert telemetry["temps"] == {
        "cpu_0": {"temperature": 45.5, "name": "CPU 0", "device_id": "cpu.0"}
    }
    # Only the selected style's paths are requested, and each exactly once.
    for path in paths.values():
        assert router.count("GET", path) == 1
    other_paths = (CAMELCASE_PATHS if paths is SNAKE_CASE_PATHS else SNAKE_CASE_PATHS).values()
    assert all(router.count("GET", path) == 0 for path in other_paths)


async def test_telemetry_system_with_unparsable_datetime_uses_uptime_and_current_time(
    make_client: MakeClientFactory,
) -> None:
    """An unparsable system datetime still yields uptime, boottime and load fallbacks.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, _ = _router(
        make_client,
        {
            SYSTEM_PATH: {
                "datetime": "not-a-date",
                "uptime": "1 days, 01:02:03",
                "boottime": "also-bad",
                "loadavg": "bad",
            }
        },
    )

    system = (await client.get_telemetry())["system"]

    assert system["uptime"] == 90123
    assert system["boottime"] == pytest.approx(time.time() - 90123, abs=60)
    assert system["load_average"] == {
        "one_minute": None,
        "five_minute": None,
        "fifteen_minute": None,
    }


async def test_telemetry_system_boottime_with_matching_and_unmatched_uptime(
    make_client: MakeClientFactory,
) -> None:
    """Uptime comes from the Router's text when it parses and from boottime when it does not.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, router = _router(
        make_client,
        {
            SYSTEM_PATH: {
                "datetime": "2026-03-07 12:00:00",
                "boottime": "2026-03-06 12:00:00",
                "uptime": "1 days, 00:00:00",
                "loadavg": "1.00, 0.50, 0.25",
            }
        },
    )

    matched = (await client.get_telemetry())["system"]

    assert isinstance(matched["boottime"], float)
    assert matched["uptime"] == 86400
    assert matched["load_average"]["one_minute"] == 1.0

    router.routes[("GET", SYSTEM_PATH)] = {
        "datetime": "2026-03-07 12:00:00",
        "boottime": "2026-03-06 12:00:00",
        "uptime": "bad-format",
        "loadavg": "1.00, 0.50, 0.25",
    }

    unmatched = (await client.get_telemetry())["system"]

    assert isinstance(unmatched["boottime"], float)
    assert unmatched["uptime"] == 86400


async def test_telemetry_system_invalid_uptime_without_boottime(
    make_client: MakeClientFactory,
) -> None:
    """Without a usable uptime or boottime, system telemetry still reports the load average.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, _ = _router(
        make_client,
        {
            SYSTEM_PATH: {
                "datetime": "2026-03-07 12:00:00",
                "uptime": "bad-format",
                "loadavg": "2.00, 1.00, 0.50",
            }
        },
    )

    system = (await client.get_telemetry())["system"]

    assert "boottime" not in system
    assert system["load_average"]["one_minute"] == 2.0


async def test_telemetry_system_aware_datetime_boottime_and_load_fallback(
    make_client: MakeClientFactory,
) -> None:
    """System telemetry preserves aware timestamps and falls back for a malformed load average.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, _ = _router(
        make_client,
        {
            SYSTEM_PATH: {
                "datetime": "2026-03-07T17:00:00+00:00",
                "boottime": "2026-03-07T11:00:00-05:00",
                "uptime": "bad-format",
                "loadavg": "bad-load",
            }
        },
    )

    system = (await client.get_telemetry())["system"]

    expected_boottime = datetime.fromisoformat("2026-03-07T11:00:00-05:00").timestamp()
    assert isinstance(system["boottime"], float)
    assert system["boottime"] == expected_boottime
    assert system["uptime"] == 3600
    assert system["load_average"] == {
        "one_minute": None,
        "five_minute": None,
        "fifteen_minute": None,
    }


async def test_telemetry_cpu_without_core_list_or_stream(make_client: MakeClientFactory) -> None:
    """CPU telemetry is empty without a CPU type and keeps only the count without a stream.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, router = _router(make_client, {CPU_TYPE_PATH: []})

    assert (await client.get_telemetry())["cpu"] == {}

    router.routes[("GET", CPU_TYPE_PATH)] = ["Intel (2 cores)"]
    router.routes[("GET", CPU_STREAM_PATH)] = 404

    assert (await client.get_telemetry())["cpu"] == {"count": 2}
    # The unavailable stream is only checked; its usage is never read.
    assert router.count("GET", CPU_STREAM_PATH) == 1


async def test_telemetry_memory_returns_without_swap_details_when_swap_shape_is_malformed(
    make_client: MakeClientFactory,
) -> None:
    """Memory telemetry keeps its fields when the swap payload has an unexpected shape.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, _ = _router(
        make_client,
        {
            MEMORY_PATH: {"memory": {"total": "4000", "used": "1000"}},
            SWAP_PATH: {"swap": "bad-shape"},
        },
    )

    memory = (await client.get_telemetry())["memory"]

    assert memory["physmem"] == 4000
    assert memory["used"] == 1000
    assert "swap_total" not in memory


@pytest.mark.parametrize("throw_errors", [False, True])
async def test_telemetry_memory_returns_base_payload_when_swap_endpoint_missing(
    make_client: MakeClientFactory, throw_errors: bool
) -> None:
    """A missing swap endpoint leaves the base memory fields, in either error mode.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
        throw_errors (bool): Public ``throw_errors`` setting of the client.
    """
    client, router = _router(
        make_client,
        {MEMORY_PATH: {"memory": {"total": "100", "used": "50"}}},
        throw_errors=throw_errors,
    )

    memory = (await client.get_telemetry())["memory"]

    assert memory == {"physmem": 100, "used": 50, "used_percent": 50}
    assert router.count("GET", MEMORY_PATH) == 1
    assert router.count("GET", SWAP_PATH) == 1


async def test_telemetry_temps_empty_returns_empty_mapping(make_client: MakeClientFactory) -> None:
    """Temperature telemetry is an empty mapping when the Router reports no sensors.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, _ = _router(make_client, {TEMPERATURE_PATH: []})

    assert (await client.get_telemetry())["temps"] == {}


@pytest.mark.parametrize(
    ("section", "path", "other_section", "other_path", "other_reply", "other_result"),
    [
        (
            "filesystems",
            "/api/diagnostics/system/system_disk",
            "temps",
            "/api/diagnostics/system/system_temperature",
            [{"device": "cpu.0", "temperature": "45"}],
            {"cpu_0": {"device_id": "cpu.0", "name": "Num 0", "temperature": 45.0}},
        ),
        (
            "temps",
            "/api/diagnostics/system/system_temperature",
            "filesystems",
            "/api/diagnostics/system/system_disk",
            {"devices": [{"mountpoint": "/"}]},
            [{"mountpoint": "/"}],
        ),
    ],
    ids=["filesystems", "temps"],
)
@pytest.mark.parametrize(
    ("reply", "requests"),
    [(403, 1), (TimeoutError(), 2), (MalformedJSONResponse(), 2)],
    ids=["forbidden", "timeout", "undecodable"],
)
async def test_telemetry_inventory_failure_preserves_the_other_inventory(
    make_client: MakeClientFactory,
    section: str,
    path: str,
    other_section: str,
    other_path: str,
    other_reply: list | dict,
    other_result: list | dict,
    reply: int | TimeoutError | MalformedJSONResponse,
    requests: int,
) -> None:
    """An unreadable inventory is absent from reconciliation on successive reads.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
        section (str): Unreadable telemetry section.
        path (str): Endpoint for the unreadable inventory.
        other_section (str): Readable telemetry section.
        other_path (str): Endpoint for the readable inventory.
        other_reply (list | dict): Router response for the readable inventory.
        other_result (list | dict): Expected normalized value of the readable inventory.
        reply (int | TimeoutError | MalformedJSONResponse): Failure from the Router.
        requests (int): Number of HTTP reads of the unreadable inventory across both calls.
    """
    client, router = _router(make_client, {path: reply, other_path: other_reply})

    for _ in range(2):
        telemetry = await client.get_telemetry()
        assert telemetry[section] is None
        assert telemetry[other_section] == other_result

    assert router.count("GET", path) == requests
    assert router.count("GET", other_path) == 2


@pytest.mark.parametrize(
    ("disk_reply", "temp_reply", "error", "temp_requests"),
    [
        (403, TimeoutError(), OPNsensePrivilegeMissing, 0),
        (404, TimeoutError(), OPNsenseTimeoutError, 1),
        (MalformedJSONResponse(), 403, OPNsenseError, 0),
    ],
    ids=["forbidden-before-timeout", "missing-before-timeout", "undecodable-before-forbidden"],
)
async def test_telemetry_strict_mode_raises_first_inventory_error(
    make_client: MakeClientFactory,
    disk_reply: int | MalformedJSONResponse,
    temp_reply: int | TimeoutError,
    error: type[OPNsenseError],
    temp_requests: int,
) -> None:
    """Strict reads stop at the first error and continue past a 404.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
        disk_reply (int | MalformedJSONResponse): Router reply to the filesystem read.
        temp_reply (int | TimeoutError): Router reply to the temperature read.
        error (type[OPNsenseError]): Public error expected from the strict call.
        temp_requests (int): Number of temperature reads before the error.
    """
    client, router = _router(
        make_client,
        {"/api/diagnostics/system/system_disk": disk_reply, TEMPERATURE_PATH: temp_reply},
        throw_errors=True,
    )

    with pytest.raises(error) as exc_info:
        await client.get_telemetry()

    assert type(exc_info.value) is error
    assert router.count("GET", TEMPERATURE_PATH) == temp_requests


async def test_get_interfaces_status_variants(make_client: MakeClientFactory) -> None:
    """Interfaces normalize the associated status and drop an all-zero MAC address.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, _ = _router(
        make_client,
        {
            INTERFACES_PATH: [
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

    interfaces = await client.get_interfaces()

    assert interfaces["em0"]["status"] == "down"
    assert interfaces["em1"]["status"] == "up"
    assert "mac" not in interfaces["em1"]
    assert interfaces["em2"]["mac"] == "11:22:33:44:55:66"


async def test_get_interfaces_empty_and_invalid_rows(make_client: MakeClientFactory) -> None:
    """Interfaces are empty for no rows and skip malformed rows.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, router = _router(make_client, {INTERFACES_PATH: []})

    assert await client.get_interfaces() == {}

    router.routes[("GET", INTERFACES_PATH)] = [
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


async def test_get_gateways_skips_rows_without_name(make_client: MakeClientFactory) -> None:
    """Gateways skip rows that are not named mappings.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, _ = _router(
        make_client,
        {
            GATEWAYS_PATH: {
                "items": [
                    {"status_translated": "Online"},
                    {"name": "gw1", "status_translated": "Online"},
                    "bad-row",
                ]
            }
        },
    )

    gateways = await client.get_gateways()

    assert list(gateways) == ["gw1"]
    assert gateways["gw1"]["status"] == "online"


@pytest.mark.parametrize("throw_errors", [False, True])
async def test_telemetry_endpoints_missing_on_router_return_empty_data(
    make_client: MakeClientFactory, throw_errors: bool
) -> None:
    """Every telemetry read returns its empty value when the Router lacks the endpoint.

    A missing endpoint is absent in either error mode, and each read sends one request.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
        throw_errors (bool): Public ``throw_errors`` setting of the client.
    """
    client, router = _router(make_client, {}, throw_errors=throw_errors)

    telemetry = await client.get_telemetry()

    assert telemetry == {
        "mbuf": {},
        "pfstate": {},
        "memory": {"physmem": None, "used": None, "used_percent": None},
        "system": {},
        "cpu": {},
        "filesystems": [],
        "temps": {},
    }
    assert await client.get_interfaces() == {}
    assert await client.get_gateways() == {}
    for path in (
        MBUF_PATH,
        PFSTATE_PATH,
        *SNAKE_CASE_PATHS.values(),
        INTERFACES_PATH,
        GATEWAYS_PATH,
    ):
        assert router.count("GET", path) == 1
    # Without a CPU type the usage stream is never requested.
    assert router.count("GET", CPU_STREAM_PATH) == 0
    # A missing memory endpoint stops before the swap read.
    assert router.count("GET", SWAP_PATH) == 0
