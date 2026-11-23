"""Tests for `aiopnsense.speedtest`."""

from collections.abc import Callable
from datetime import timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from aiopnsense import OPNsenseClient
from aiopnsense._endpoint_outcome import _EndpointOutcome
from aiopnsense.exceptions import OPNsenseConnectionError
from aiopnsense.system import SYSTEM_TIME_ENDPOINT
from tests.conftest import make_mock_session_client, stub_endpoint_outcome

ClientType = Callable[..., OPNsenseClient]


@pytest.mark.asyncio
async def test_get_speedtest_skips_calls_when_endpoint_missing(make_client: ClientType) -> None:
    """get_speedtest should skip speedtest API calls when endpoint is unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(client, _EndpointOutcome.absent())

        result = await client.get_speedtest()

        assert result == {"available": False}
        read.assert_awaited_once_with("/api/speedtest/service/showlog")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_speedtest_normalizes_latest_and_stat_payloads(make_client: ClientType) -> None:
    """get_speedtest should normalize shared showlog and showstat payload fields.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(client, _EndpointOutcome.absent())
        read.side_effect = [
            _EndpointOutcome.data(
                [
                    [
                        "2026-03-14T03:09:45",
                        "198.51.100.10",
                        "72800",
                        "RippleFiber, Newark, NJ",
                        "United States",
                        "836.05",
                        "832.97",
                        "4.0",
                        "https://www.speedtest.net/result/c/abc",
                    ]
                ]
            ),
            _EndpointOutcome.data(
                {
                    "samples": 10717,
                    "period": {"oldest": "2023-01-22 00:29:00", "youngest": "2026-03-14 03:09:45"},
                    "latency": {"avg": 13.42, "min": 2.35, "max": 1266.74},
                    "download": {"avg": 723.83, "min": 4.18, "max": 942.02},
                    "upload": {"avg": 706.7, "min": 1.54, "max": 890.32},
                }
            ),
        ]
        client._get_resolved_opnsense_timezone = AsyncMock(
            return_value=ZoneInfo("America/New_York")
        )

        result = await client.get_speedtest()

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
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    "showstat_available",
    [
        pytest.param(True, id="showstat-available"),
        pytest.param(False, id="showstat-missing"),
    ],
)
@pytest.mark.asyncio
async def test_get_speedtest_reads_optional_showstat_payload(
    make_client: ClientType,
    showstat_available: bool,
) -> None:
    """Validate ``get_speedtest`` reads the ``showstat`` payload and uses it only when present.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        showstat_available (bool): Whether the ``showstat`` endpoint answers with data.

    Returns:
        None: This test validates that a missing showstat endpoint yields empty statistics.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        endpoint_outcomes = {
            "/api/speedtest/service/showlog": _EndpointOutcome.data(
                [
                    [
                        "2026-03-14T03:09:45",
                        "198.51.100.10",
                        "72800",
                        "Test ISP, New York, NY",
                        "United States",
                        "1",
                        "2",
                        "3",
                        "https://www.speedtest.net/result/c/abc",
                    ]
                ]
            ),
            "/api/speedtest/service/showstat": (
                _EndpointOutcome.data({}) if showstat_available else _EndpointOutcome.absent()
            ),
        }
        read = stub_endpoint_outcome(client, _EndpointOutcome.absent())
        read.side_effect = lambda endpoint: endpoint_outcomes[endpoint]
        client._get_resolved_opnsense_timezone = AsyncMock(
            return_value=timezone(timedelta(hours=-4))
        )

        result = await client.get_speedtest()

        assert result["available"] is True
        assert read.await_count == 2
        read.assert_any_await("/api/speedtest/service/showlog")
        read.assert_any_await("/api/speedtest/service/showstat")
        client._get_resolved_opnsense_timezone.assert_awaited_once_with()
        assert result["last"]["download"]["value"] == 1.0
        assert result["last"]["upload"]["value"] == 2.0
        assert result["last"]["latency"]["value"] == 3.0
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_speedtest_preserves_timezone_aware_date(make_client: ClientType) -> None:
    """get_speedtest should preserve an existing timestamp UTC offset.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(client, _EndpointOutcome.absent())
        read.side_effect = [
            _EndpointOutcome.data(
                [
                    [
                        "2026-03-14T03:09:45+01:30",
                        "198.51.100.10",
                        "72800",
                        "Test ISP",
                        "United States",
                        "1",
                        "2",
                        "3",
                        "https://www.speedtest.net/result/c/abc",
                    ]
                ]
            ),
            _EndpointOutcome.absent(),
        ]
        client._get_resolved_opnsense_timezone = AsyncMock()

        result = await client.get_speedtest()

        assert result["last"]["download"]["date"] == "2026-03-14T03:09:45+01:30"
        client._get_resolved_opnsense_timezone.assert_awaited_once_with()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_speedtest_drops_malformed_date(make_client: ClientType) -> None:
    """get_speedtest should omit malformed timestamp values.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(client, _EndpointOutcome.absent())
        read.side_effect = [
            _EndpointOutcome.data(
                [
                    [
                        "not-a-date",
                        "198.51.100.10",
                        "72800",
                        "Test ISP",
                        "United States",
                        "1",
                        "2",
                        "3",
                        "https://www.speedtest.net/result/c/abc",
                    ]
                ]
            ),
            _EndpointOutcome.absent(),
        ]
        client._get_resolved_opnsense_timezone = AsyncMock()

        result = await client.get_speedtest()

        assert result["last"]["download"]["date"] is None
        client._get_resolved_opnsense_timezone.assert_awaited_once_with()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_speedtest_preserves_aware_date_and_drops_naive_periods_when_timezone_unresolved(
    make_client: ClientType,
) -> None:
    """When OPNsense timezone is unresolved, keep aware date fields but drop naive period fields.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(client, _EndpointOutcome.absent())
        read.side_effect = [
            _EndpointOutcome.data(
                [
                    [
                        "2026-03-14T03:09:45+01:30",
                        "198.51.100.10",
                        "72800",
                        "Test ISP",
                        "United States",
                        "1",
                        "2",
                        "3",
                        "https://www.speedtest.net/result/c/abc",
                    ]
                ]
            ),
            _EndpointOutcome.absent(),
        ]
        client._get_resolved_opnsense_timezone = AsyncMock(return_value=None)

        result = await client.get_speedtest()

        assert result["last"]["download"]["date"] == "2026-03-14T03:09:45+01:30"
        assert result["average"]["download"]["oldest"] is None
        assert result["average"]["download"]["youngest"] is None
        client._get_resolved_opnsense_timezone.assert_awaited_once_with()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_speedtest_normalizes_malformed_payloads(make_client: ClientType) -> None:
    """get_speedtest should coerce malformed or missing values to None safely.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(client, _EndpointOutcome.absent())
        read.side_effect = [
            _EndpointOutcome.data(
                [
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
                ]
            ),
            _EndpointOutcome.data(
                {
                    "samples": "not-an-int",
                    "period": "bad-period-shape",
                    "download": "bad-download-shape",
                    "upload": None,
                    "latency": ["bad-latency-shape"],
                }
            ),
        ]
        client._get_resolved_opnsense_timezone = AsyncMock(
            return_value=timezone(timedelta(hours=-4))
        )

        result = await client.get_speedtest()

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
    finally:
        await client.async_close()


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


@pytest.mark.asyncio
async def test_run_speedtest_uses_extended_timeout(make_client: ClientType) -> None:
    """run_speedtest should use custom timeout helper for long-running endpoint calls.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        status = stub_endpoint_outcome(client, _EndpointOutcome.data(None), read="status")
        client._safe_dict_get_with_timeout = AsyncMock(return_value={"timestamp": "x"})

        result = await client.run_speedtest()

        assert result == {"timestamp": "x"}
        status.assert_awaited_once_with("/api/speedtest/service/showlog")
        client._safe_dict_get_with_timeout.assert_awaited_once_with(
            "/api/speedtest/service/run", timeout_seconds=180
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_run_speedtest_returns_empty_when_endpoint_missing(make_client: ClientType) -> None:
    """run_speedtest should return an empty payload when endpoint is unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        status = stub_endpoint_outcome(client, _EndpointOutcome.absent(), read="status")
        client._safe_dict_get_with_timeout = AsyncMock()

        result = await client.run_speedtest()

        assert result == {}
        client._safe_dict_get_with_timeout.assert_not_awaited()
        status.assert_awaited_once_with("/api/speedtest/service/showlog")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_run_speedtest_returns_empty_for_non_mapping_response(
    make_client: ClientType,
) -> None:
    """run_speedtest should return an empty payload for non-mapping responses.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        status = stub_endpoint_outcome(client, _EndpointOutcome.data(None), read="status")
        client._do_get = AsyncMock(return_value=["not", "a", "mapping"])

        result = await client.run_speedtest()

        assert result == {}
        status.assert_awaited_once_with("/api/speedtest/service/showlog")
        client._do_get.assert_awaited_once()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_get_speedtest_survives_failed_system_time_lookup(
    make_client: ClientType, throw_errors: bool
) -> None:
    """A failed system-time lookup only drops naive timestamps in either error mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Whether strict request errors are enabled.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._throw_errors = throw_errors

        async def fake_fetch(path: str, *_args: Any, **_kwargs: Any) -> _EndpointOutcome:
            """Fail the system-time request and serve the show-log and show-stat reads.

            Args:
                path (str): API endpoint path requested by the getter.
                _args (Any): Positional arguments accepted by `fake_fetch`.
                _kwargs (Any): Keyword arguments accepted by `fake_fetch`.

            Returns:
                _EndpointOutcome: Transient failure for the system-time endpoint, otherwise the
                    canned show-log or show-stat payload.
            """
            if path == SYSTEM_TIME_ENDPOINT:
                return _EndpointOutcome.transient(
                    OPNsenseConnectionError("system time unavailable")
                )
            if path == "/api/speedtest/service/showlog":
                return _EndpointOutcome.data(
                    [
                        [
                            "2026-03-14T03:09:45+01:30",
                            "198.51.100.10",
                            "72800",
                            "Test ISP",
                            "United States",
                            "1",
                            "2",
                            "3",
                            "https://www.speedtest.net/result/c/abc",
                        ]
                    ]
                )
            return _EndpointOutcome.data(
                {
                    "samples": 10717,
                    "period": {"oldest": "2023-01-22 00:29:00", "youngest": "2026-03-14 03:09:45"},
                }
            )

        client._fetch_endpoint_outcome = AsyncMock(side_effect=fake_fetch)

        result = await client.get_speedtest()

        assert result["last"]["download"]["date"] == "2026-03-14T03:09:45+01:30"
        assert result["average"]["download"]["oldest"] is None
        assert result["average"]["download"]["youngest"] is None
    finally:
        await client.async_close()
