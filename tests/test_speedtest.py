"""Tests for `aiopnsense.speedtest`."""

from collections.abc import Callable
from unittest.mock import AsyncMock, call

import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


@pytest.mark.asyncio
async def test_get_speedtest_skips_calls_when_endpoint_missing(make_client) -> None:
    """get_speedtest should skip speedtest API calls when endpoint is unavailable."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(return_value=("missing", {}))

        result = await client.get_speedtest()

        assert result == {"available": False}
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/speedtest/service/showlog"
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_speedtest_preserves_plugin_availability_during_transient_failure(
    make_client,
) -> None:
    """A transient router failure must not masquerade as plugin removal."""
    client, _session = make_mock_session_client(make_client)
    client._check_optional_get_endpoint = AsyncMock(return_value=("unavailable", {}))
    try:
        assert await client.get_speedtest() == {
            "available": True,
            "last": {},
            "average": {},
        }
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_speedtest_normalizes_latest_and_stat_payloads(make_client) -> None:
    """get_speedtest should normalize shared showlog and showstat payload fields."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            side_effect=[
                (
                    "available",
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
                    ],
                ),
                (
                    "available",
                    {
                        "samples": 10717,
                        "period": {
                            "oldest": "2023-01-22 00:29:00",
                            "youngest": "2026-03-14 03:09:45",
                        },
                        "latency": {"avg": 13.42, "min": 2.35, "max": 1266.74},
                        "download": {"avg": 723.83, "min": 4.18, "max": 942.02},
                        "upload": {"avg": 706.7, "min": 1.54, "max": 890.32},
                    },
                ),
            ]
        )

        result = await client.get_speedtest()

        assert result["available"] is True
        assert result["last"]["download"]["value"] == 836.05
        assert result["last"]["download"]["server_id"] == "72800"
        assert result["last"]["download"]["server"] == "RippleFiber, Newark, NJ"
        assert result["average"]["download"]["value"] == 723.83
        assert result["average"]["download"]["min"] == 4.18
        assert result["average"]["download"]["max"] == 942.02
        assert result["average"]["download"]["samples"] == 10717
        assert result["average"]["download"]["oldest"] == "2023-01-22 00:29:00"
        assert result["average"]["download"]["youngest"] == "2026-03-14 03:09:45"
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("optional_results", "showstat_available"),
    [
        pytest.param(
            [
                (
                    "available",
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
                    ],
                ),
                (
                    "available",
                    {
                        "samples": 42,
                        "period": {
                            "oldest": "2026-03-01 00:00:00",
                            "youngest": "2026-03-14 03:09:45",
                        },
                        "download": {"avg": 100.5, "min": 90.0, "max": 110.0},
                        "upload": {"avg": 20.5, "min": 18.0, "max": 23.0},
                        "latency": {"avg": 3.5, "min": 2.0, "max": 5.0},
                    },
                ),
            ],
            True,
            id="showstat-available",
        ),
        pytest.param(
            [
                (
                    "available",
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
                    ],
                ),
                ("missing", {}),
            ],
            False,
            id="showstat-missing",
        ),
        pytest.param(
            [
                (
                    "available",
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
                    ],
                ),
                ("transient", {}),
            ],
            False,
            id="showstat-transient",
        ),
    ],
)
@pytest.mark.asyncio
async def test_get_speedtest_probes_showstat_before_fetching_optional_payload(
    make_client: ClientType,
    optional_results: list[tuple[str, object]],
    showstat_available: bool,
) -> None:
    """Validate ``get_speedtest`` probes ``showstat`` before optional fetches.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        optional_results: Optional endpoint states and payloads in call order.
        showstat_available (bool): Whether the ``showstat`` endpoint should be fetched.

    Returns:
        None: This test validates endpoint probing order and conditional fetches.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(side_effect=optional_results)

        result = await client.get_speedtest()

        assert result["available"] is True
        assert client._check_optional_get_endpoint.await_args_list == [
            call("/api/speedtest/service/showlog"),
            call("/api/speedtest/service/showstat"),
        ]

        assert result["last"]["download"]["value"] == 1.0
        assert result["last"]["upload"]["value"] == 2.0
        assert result["last"]["latency"]["value"] == 3.0

        if showstat_available:
            assert result["average"]["download"] == {
                "value": 100.5,
                "min": 90.0,
                "max": 110.0,
                "oldest": "2026-03-01 00:00:00",
                "youngest": "2026-03-14 03:09:45",
                "samples": 42,
            }
            assert result["average"]["upload"]["value"] == 20.5
            assert result["average"]["latency"]["value"] == 3.5
        else:
            for metric in ("download", "upload", "latency"):
                assert result["average"][metric] == {
                    "value": None,
                    "min": None,
                    "max": None,
                    "oldest": None,
                    "youngest": None,
                    "samples": None,
                }
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_speedtest_normalizes_malformed_payloads(make_client) -> None:
    """get_speedtest should coerce malformed or missing values to None safely."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            side_effect=[
                (
                    "available",
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
                    ],
                ),
                (
                    "available",
                    {
                        "samples": "not-an-int",
                        "period": "bad-period-shape",
                        "download": "bad-download-shape",
                        "upload": None,
                        "latency": ["bad-latency-shape"],
                    },
                ),
            ]
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
    """_parse_showlog_latest should reject missing or malformed history rows."""
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
    """_parse_showlog_latest should preserve separate server id and server name."""
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
async def test_run_speedtest_uses_extended_timeout(make_client) -> None:
    """run_speedtest should use custom timeout helper for long-running endpoint calls."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(return_value=("available", {}))
        client._safe_dict_get_with_timeout = AsyncMock(return_value={"timestamp": "x"})

        result = await client.run_speedtest()

        assert result == {"timestamp": "x"}
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/speedtest/service/showlog"
        )
        client._safe_dict_get_with_timeout.assert_awaited_once_with(
            "/api/speedtest/service/run", timeout_seconds=180
        )
    finally:
        await client.async_close()


@pytest.mark.parametrize("optional_state", ["missing", "unavailable"])
@pytest.mark.asyncio
async def test_run_speedtest_returns_empty_when_endpoint_not_ready(
    make_client, optional_state: str
) -> None:
    """run_speedtest should return an empty payload when probe is absent or blocked."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(return_value=(optional_state, {}))
        client._safe_dict_get_with_timeout = AsyncMock()

        result = await client.run_speedtest()

        assert result == {}
        client._safe_dict_get_with_timeout.assert_not_awaited()
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/speedtest/service/showlog"
        )
    finally:
        await client.async_close()


@pytest.mark.parametrize("optional_state", ["available", "malformed"])
@pytest.mark.asyncio
async def test_run_speedtest_allows_malformed_and_available_probe_payloads(
    make_client: ClientType, optional_state: str
) -> None:
    """run_speedtest should proceed when probe payload is malformed or available."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(return_value=(optional_state, {}))
        client._safe_dict_get_with_timeout = AsyncMock(return_value={"timestamp": "x"})

        result = await client.run_speedtest()

        assert result == {"timestamp": "x"}
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/speedtest/service/showlog"
        )
        client._safe_dict_get_with_timeout.assert_awaited_once_with(
            "/api/speedtest/service/run", timeout_seconds=180
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_run_speedtest_returns_empty_for_non_mapping_response(make_client) -> None:
    """run_speedtest should return an empty payload for non-mapping responses."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(return_value=("available", {}))
        client._safe_dict_get_with_timeout = AsyncMock(return_value=["not", "a", "mapping"])

        result = await client.run_speedtest()

        assert result == {}
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/speedtest/service/showlog"
        )
        client._safe_dict_get_with_timeout.assert_awaited_once_with(
            "/api/speedtest/service/run", timeout_seconds=180
        )
    finally:
        await client.async_close()
