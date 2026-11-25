"""Tests for `aiopnsense.speedtest`."""

from collections.abc import Callable
from typing import Any

import aiohttp
import pytest

from aiopnsense import OPNsenseClient
from aiopnsense.exceptions import OPNsenseError, OPNsensePrivilegeMissing, OPNsenseTimeoutError
from tests.conftest import MalformedJSONResponse, make_mock_session_client, make_router_client

SYSTEM_TIME_PATH = "/api/diagnostics/system/system_time"

ClientType = Callable[..., OPNsenseClient]

SHOWLOG_PATH = "/api/speedtest/service/showlog"
SHOWSTAT_PATH = "/api/speedtest/service/showstat"
RUN_PATH = "/api/speedtest/service/run"
NEW_YORK_TIME = {"datetime": "Sat Mar 14 03:10:00 EDT 2026"}
MINUS_FOUR_TIME = {"datetime": "2026-03-14 03:10:00 -0400"}


def _showlog_row(
    date: object = "2026-03-14T03:09:45",
    server_id: object = "72800",
    server: object = "Test ISP",
    download: object = "1",
    upload: object = "2",
    latency: object = "3",
    url: object = "https://www.speedtest.net/result/c/abc",
) -> list[list[object]]:
    """Return a show-log payload holding one result row.

    Args:
        date (object): Timestamp column.
        server_id (object): Server identifier column.
        server (object): Server name column.
        download (object): Download column.
        upload (object): Upload column.
        latency (object): Latency column.
        url (object): Result URL column.

    Returns:
        list[list[object]]: Payload with the nine-column row the Router returns.
    """
    return [
        [date, "198.51.100.10", server_id, server, "United States", download, upload, latency, url]
    ]


@pytest.mark.parametrize("throw_errors", [False, True])
async def test_get_speedtest_skips_calls_when_endpoint_missing(
    make_client: ClientType, throw_errors: bool
) -> None:
    """A 404 show-log reports Speedtest unavailable and reads nothing more.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Whether the client raises request errors.
    """
    client, router = make_router_client(
        make_client, {("GET", SHOWLOG_PATH): 404}, throw_errors=throw_errors
    )

    assert await client.get_speedtest() == {"available": False}

    assert router.calls() == [("GET", SHOWLOG_PATH)]


@pytest.mark.parametrize("throw_errors", [False, True])
@pytest.mark.parametrize(
    ("response", "error"),
    [
        pytest.param(403, OPNsensePrivilegeMissing, id="forbidden"),
        pytest.param(TimeoutError(), OPNsenseTimeoutError, id="timeout"),
        pytest.param(MalformedJSONResponse(), OPNsenseError, id="invalid-json"),
    ],
)
async def test_get_speedtest_handles_failed_showlog(
    make_client: ClientType, throw_errors: bool, response: object, error: type[OPNsenseError]
) -> None:
    """A failed primary read stops polling and follows the client's error mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Whether the client raises request errors.
        response (object): Simulated primary response or transport failure.
        error (type[OPNsenseError]): Expected exception in strict mode.
    """
    client, router = make_router_client(
        make_client, {("GET", SHOWLOG_PATH): response}, throw_errors=throw_errors
    )

    if throw_errors:
        with pytest.raises(error):
            await client.get_speedtest()
    else:
        assert await client.get_speedtest() is None
        if response == 403:
            assert await client.get_speedtest() is None

    assert router.calls() == [("GET", SHOWLOG_PATH)]


@pytest.mark.parametrize("throw_errors", [False, True])
async def test_get_speedtest_handles_undecodable_showstat(
    make_client: ClientType, throw_errors: bool
) -> None:
    """An undecodable statistics response preserves the latest results in non-strict mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Whether the client raises request errors.
    """
    client, router = make_router_client(
        make_client,
        {
            ("GET", SHOWLOG_PATH): _showlog_row(),
            ("GET", SHOWSTAT_PATH): MalformedJSONResponse(),
            ("GET", SYSTEM_TIME_PATH): MINUS_FOUR_TIME,
        },
        throw_errors=throw_errors,
    )

    if throw_errors:
        with pytest.raises(OPNsenseError):
            await client.get_speedtest()
        assert router.calls() == [("GET", SHOWLOG_PATH), ("GET", SHOWSTAT_PATH)]
    else:
        result = await client.get_speedtest()
        assert result is not None
        assert result["available"] is True
        assert result["last"]["download"]["value"] == 1.0
        assert result["average"]["download"]["value"] is None


async def test_get_speedtest_normalizes_latest_and_stat_payloads(make_client: ClientType) -> None:
    """get_speedtest normalizes the shared show-log and show-stat payload fields.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = make_router_client(
        make_client,
        {
            ("GET", SHOWLOG_PATH): _showlog_row(
                server="RippleFiber, Newark, NJ", download="836.05", upload="832.97", latency="4.0"
            ),
            ("GET", SHOWSTAT_PATH): {
                "samples": 10717,
                "period": {"oldest": "2023-01-22 00:29:00", "youngest": "2026-03-14 03:09:45"},
                "latency": {"avg": 13.42, "min": 2.35, "max": 1266.74},
                "download": {"avg": 723.83, "min": 4.18, "max": 942.02},
                "upload": {"avg": 706.7, "min": 1.54, "max": 890.32},
            },
            ("GET", SYSTEM_TIME_PATH): NEW_YORK_TIME,
        },
    )

    result = await client.get_speedtest()

    assert result is not None
    assert result["available"] is True
    assert result["last"]["download"]["value"] == 836.05
    assert result["last"]["download"]["server_id"] == "72800"
    assert result["last"]["download"]["server"] == "RippleFiber, Newark, NJ"
    assert result["last"]["download"]["date"] == "2026-03-14T03:09:45-04:00"
    assert result["average"]["download"]["value"] == 723.83
    assert result["average"]["download"]["min"] == 4.18
    assert result["average"]["download"]["max"] == 942.02
    assert result["average"]["download"]["samples"] == 10717
    assert result["average"]["download"]["oldest"] == "2023-01-22T00:29:00-05:00"
    assert result["average"]["download"]["youngest"] == "2026-03-14T03:09:45-04:00"


@pytest.mark.parametrize(
    "showstat_available",
    [
        pytest.param(True, id="showstat-available"),
        pytest.param(False, id="showstat-missing"),
    ],
)
async def test_get_speedtest_reads_optional_showstat_payload(
    make_client: ClientType,
    showstat_available: bool,
) -> None:
    """get_speedtest reads show-stat and uses it only when the Router answers it.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        showstat_available (bool): Whether the ``showstat`` endpoint answers with data.
    """
    client, router = make_router_client(
        make_client,
        {
            ("GET", SHOWLOG_PATH): _showlog_row(server="Test ISP, New York, NY"),
            ("GET", SHOWSTAT_PATH): {"samples": 5} if showstat_available else 404,
            ("GET", SYSTEM_TIME_PATH): MINUS_FOUR_TIME,
        },
    )

    result = await client.get_speedtest()

    assert result is not None
    assert result["available"] is True
    assert router.count("GET", SHOWLOG_PATH) == 1
    assert router.count("GET", SHOWSTAT_PATH) == 1
    assert router.count("GET", SYSTEM_TIME_PATH) == 1
    assert result["average"]["download"]["samples"] == (5 if showstat_available else None)
    assert result["last"]["download"]["value"] == 1.0
    assert result["last"]["upload"]["value"] == 2.0
    assert result["last"]["latency"]["value"] == 3.0


async def test_get_speedtest_preserves_timezone_aware_date(make_client: ClientType) -> None:
    """get_speedtest preserves an existing timestamp UTC offset over the Router timezone.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(
        make_client,
        {
            ("GET", SHOWLOG_PATH): _showlog_row(date="2026-03-14T03:09:45+01:30"),
            ("GET", SHOWSTAT_PATH): 404,
            ("GET", SYSTEM_TIME_PATH): NEW_YORK_TIME,
        },
    )

    result = await client.get_speedtest()

    assert result is not None
    assert result["last"]["download"]["date"] == "2026-03-14T03:09:45+01:30"
    assert router.count("GET", SYSTEM_TIME_PATH) == 1


async def test_get_speedtest_drops_malformed_date(make_client: ClientType) -> None:
    """get_speedtest omits malformed timestamp values.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(
        make_client,
        {
            ("GET", SHOWLOG_PATH): _showlog_row(date="not-a-date"),
            ("GET", SHOWSTAT_PATH): 404,
            ("GET", SYSTEM_TIME_PATH): NEW_YORK_TIME,
        },
    )

    result = await client.get_speedtest()

    assert result is not None
    assert result["last"]["download"]["date"] is None
    assert router.count("GET", SYSTEM_TIME_PATH) == 1


async def test_get_speedtest_preserves_aware_date_and_drops_naive_periods_when_timezone_unresolved(
    make_client: ClientType,
) -> None:
    """With no resolvable Router timezone, aware dates stay and naive period fields are dropped.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(
        make_client,
        {
            ("GET", SHOWLOG_PATH): _showlog_row(date="2026-03-14T03:09:45+01:30"),
            ("GET", SHOWSTAT_PATH): {
                "samples": 10717,
                "period": {"oldest": "2023-01-22 00:29:00", "youngest": "2026-03-14 03:09:45"},
            },
            ("GET", SYSTEM_TIME_PATH): 404,
        },
    )

    result = await client.get_speedtest()

    assert result is not None
    assert result["last"]["download"]["date"] == "2026-03-14T03:09:45+01:30"
    assert result["average"]["download"]["oldest"] is None
    assert result["average"]["download"]["youngest"] is None
    assert router.count("GET", SYSTEM_TIME_PATH) == 1


async def test_get_speedtest_normalizes_malformed_payloads(make_client: ClientType) -> None:
    """get_speedtest coerces malformed or missing values to None safely.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = make_router_client(
        make_client,
        {
            ("GET", SHOWLOG_PATH): [
                [
                    12345,
                    "198.51.100.10",
                    None,
                    "Regional POP - NYC",
                    "United States",
                    "bad-number",
                    "12.5",
                    None,
                    999,
                ]
            ],
            ("GET", SHOWSTAT_PATH): {
                "samples": "not-an-int",
                "period": "bad-period-shape",
                "download": "bad-download-shape",
                "upload": None,
                "latency": ["bad-latency-shape"],
            },
            ("GET", SYSTEM_TIME_PATH): MINUS_FOUR_TIME,
        },
    )

    result = await client.get_speedtest()

    assert result is not None
    assert result["available"] is True
    assert result["last"]["download"]["server_id"] is None
    assert result["last"]["download"]["server"] == "Regional POP - NYC"
    assert result["last"]["download"]["date"] is None
    assert result["last"]["download"]["url"] is None
    assert result["last"]["download"]["value"] is None
    assert result["last"]["upload"]["value"] == 12.5
    assert result["last"]["latency"]["value"] is None

    assert result["average"]["download"]["value"] is None
    assert result["average"]["download"]["min"] is None
    assert result["average"]["download"]["max"] is None
    assert result["average"]["download"]["samples"] is None
    assert result["average"]["download"]["oldest"] is None
    assert result["average"]["download"]["youngest"] is None


@pytest.mark.parametrize("show_log", [None, {}, [], ["malformed-row"], [["too", "short"]]])
@pytest.mark.asyncio
async def test_parse_showlog_latest_rejects_malformed_rows(
    make_client: ClientType, show_log: object
) -> None:
    """_parse_showlog_latest should reject missing or malformed history rows.

    Args:
        make_client (ClientType): Client fixture returning the parameterized show-log row.
        show_log (object): Malformed show-log payload expected to be rejected.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        assert client._parse_showlog_latest(show_log) == {}
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    (
        "raw_server_id",
        "raw_server_name",
        "expected_server_id",
        "expected_server_name",
    ),
    [
        (
            "",
            "123 Fiber",
            None,
            "123 Fiber",
        ),
        (
            "72800",
            "",
            "72800",
            None,
        ),
        (
            True,
            "123 Fiber",
            None,
            "123 Fiber",
        ),
        (
            False,
            "123 Fiber",
            None,
            "123 Fiber",
        ),
    ],
    ids=(
        "empty-id-with-name",
        "empty-name-with-id",
        "bool-true-id",
        "bool-false-id",
    ),
)
@pytest.mark.asyncio
async def test_parse_showlog_latest_preserves_server_fields(
    make_client: ClientType,
    raw_server_id: object,
    raw_server_name: str,
    expected_server_id: str | None,
    expected_server_name: str | None,
) -> None:
    """_parse_showlog_latest should preserve separate server id and server name.

    Args:
        make_client (ClientType): Client fixture returning the parameterized show-log row.
        raw_server_id (object): Server identifier in the raw show-log payload.
        raw_server_name (str): Server name in the raw show-log payload.
        expected_server_id (str | None): Normalized server identifier expected in the result.
        expected_server_name (str | None): Normalized server name expected in the result.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        parsed = client._parse_showlog_latest(
            [
                [
                    "2026-03-14T03:09:45",
                    "198.51.100.10",
                    raw_server_id,
                    raw_server_name,
                    "United States",
                    "1",
                    "2",
                    "3",
                    "https://www.speedtest.net/result/c/abc",
                ]
            ]
        )

        assert parsed.get("server_id") == expected_server_id
        assert parsed.get("server") == expected_server_name
    finally:
        await client.async_close()


async def test_run_speedtest_uses_extended_timeout(make_client: ClientType) -> None:
    """run_speedtest checks show-log, then starts the run with the extended timeout.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    timeouts: list[float | None] = []

    def start_run(*, timeout: aiohttp.ClientTimeout, **_kwargs: Any) -> dict[str, str]:
        """Record the total timeout of the run request before the Router answers it.

        Args:
            timeout (aiohttp.ClientTimeout): Timeout the HTTP transport applies.
            _kwargs (Any): Other request parameters passed by the transport.

        Returns:
            dict[str, str]: The Router's reply to the run request.
        """
        timeouts.append(timeout.total)
        return {"timestamp": "x"}

    client, router = make_router_client(
        make_client, routes={("GET", SHOWLOG_PATH): _showlog_row(), ("GET", RUN_PATH): start_run}
    )

    assert await client.run_speedtest() == {"timestamp": "x"}

    assert router.count("GET", SHOWLOG_PATH) == 1
    assert router.count("GET", RUN_PATH) == 1
    assert timeouts == [180]


async def test_run_speedtest_returns_empty_when_endpoint_missing(make_client: ClientType) -> None:
    """run_speedtest returns an empty payload and starts no run when show-log is a 404.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(make_client, {("GET", SHOWLOG_PATH): 404})

    assert await client.run_speedtest() == {}

    assert router.count("GET", SHOWLOG_PATH) == 1
    assert router.count("GET", RUN_PATH) == 0


async def test_run_speedtest_returns_empty_for_non_mapping_response(
    make_client: ClientType,
) -> None:
    """run_speedtest returns an empty payload when the run answers with a non-mapping body.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(
        make_client,
        {("GET", SHOWLOG_PATH): _showlog_row(), ("GET", RUN_PATH): ["not", "a", "mapping"]},
    )

    assert await client.run_speedtest() == {}

    assert router.count("GET", RUN_PATH) == 1


@pytest.mark.parametrize("throw_errors", [False, True], ids=["errors-off", "errors-on"])
async def test_get_speedtest_survives_failed_system_time_lookup(
    make_client: ClientType, throw_errors: bool
) -> None:
    """A failed system-time lookup only drops naive timestamps in either error mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Public ``throw_errors`` setting of the client.
    """
    client, router = make_router_client(
        make_client,
        {
            ("GET", SHOWLOG_PATH): _showlog_row(date="2026-03-14T03:09:45+01:30"),
            ("GET", SHOWSTAT_PATH): {
                "samples": 10717,
                "period": {"oldest": "2023-01-22 00:29:00", "youngest": "2026-03-14 03:09:45"},
            },
            ("GET", SYSTEM_TIME_PATH): 503,
        },
        throw_errors=throw_errors,
    )

    result = await client.get_speedtest()

    assert result is not None
    assert result["last"]["download"]["date"] == "2026-03-14T03:09:45+01:30"
    assert result["average"]["download"]["oldest"] is None
    assert result["average"]["download"]["youngest"] is None
    assert router.count("GET", SYSTEM_TIME_PATH) == 1
