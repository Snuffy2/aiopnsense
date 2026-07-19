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
        client._is_get_endpoint_available = AsyncMock(return_value=False)
        client._safe_list_get = AsyncMock()

        result = await client.get_speedtest()

        assert result == {"available": False}
        client._safe_list_get.assert_not_awaited()
        client._is_get_endpoint_available.assert_awaited_once_with("/api/speedtest/service/showlog")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_speedtest_normalizes_latest_and_stat_payloads(make_client) -> None:
    """get_speedtest should normalize shared showlog and showstat payload fields."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(side_effect=[True, True])
        client._safe_list_get = AsyncMock(
            return_value=[
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
        )
        client._safe_dict_get = AsyncMock(
            return_value={
                "samples": 10717,
                "period": {"oldest": "2023-01-22 00:29:00", "youngest": "2026-03-14 03:09:45"},
                "latency": {"avg": 13.42, "min": 2.35, "max": 1266.74},
                "download": {"avg": 723.83, "min": 4.18, "max": 942.02},
                "upload": {"avg": 706.7, "min": 1.54, "max": 890.32},
            }
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
    ("endpoint_side_effect", "showstat_available"),
    [
        pytest.param(
            [True, True],
            True,
            id="showstat-available",
        ),
        pytest.param(
            [True, False],
            False,
            id="showstat-missing",
        ),
    ],
)
@pytest.mark.asyncio
async def test_get_speedtest_probes_showstat_before_fetching_optional_payload(
    make_client: ClientType,
    endpoint_side_effect: list[bool],
    showstat_available: bool,
) -> None:
    """Validate ``get_speedtest`` probes ``showstat`` before optional fetches.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        endpoint_side_effect (list[bool]): Endpoint availability responses in call order.
        showstat_available (bool): Whether the ``showstat`` endpoint should be fetched.

    Returns:
        None: This test validates endpoint probing order and conditional fetches.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(side_effect=endpoint_side_effect)
        client._safe_list_get = AsyncMock(
            return_value=[
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
        )
        client._safe_dict_get = AsyncMock(return_value={})

        result = await client.get_speedtest()

        assert result["available"] is True
        assert client._is_get_endpoint_available.await_args_list == [
            call("/api/speedtest/service/showlog"),
            call("/api/speedtest/service/showstat"),
        ]
        client._safe_list_get.assert_awaited_once_with("/api/speedtest/service/showlog")

        if showstat_available:
            client._safe_dict_get.assert_awaited_once_with("/api/speedtest/service/showstat")
            assert result["last"]["download"]["value"] == 1.0
            assert result["last"]["upload"]["value"] == 2.0
            assert result["last"]["latency"]["value"] == 3.0
        else:
            client._safe_dict_get.assert_not_awaited()
            assert result["last"]["download"]["value"] == 1.0
            assert result["last"]["upload"]["value"] == 2.0
            assert result["last"]["latency"]["value"] == 3.0
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_speedtest_normalizes_malformed_payloads(make_client) -> None:
    """get_speedtest should coerce malformed or missing values to None safely."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(side_effect=[True, True])
        client._safe_list_get = AsyncMock(
            return_value=[
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
        )
        client._safe_dict_get = AsyncMock(
            return_value={
                "samples": "not-an-int",
                "period": "bad-period-shape",
                "download": "bad-download-shape",
                "upload": None,
                "latency": ["bad-latency-shape"],
            }
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
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get_with_timeout = AsyncMock(return_value={"timestamp": "x"})

        result = await client.run_speedtest()

        assert result == {"timestamp": "x"}
        client._is_get_endpoint_available.assert_awaited_once_with("/api/speedtest/service/showlog")
        client._safe_dict_get_with_timeout.assert_awaited_once_with(
            "/api/speedtest/service/run", timeout_seconds=180
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_run_speedtest_returns_empty_when_endpoint_missing(make_client) -> None:
    """run_speedtest should return an empty payload when endpoint is unavailable."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get_with_timeout = AsyncMock()

        result = await client.run_speedtest()

        assert result == {}
        client._safe_dict_get_with_timeout.assert_not_awaited()
        client._is_get_endpoint_available.assert_awaited_once_with("/api/speedtest/service/showlog")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_run_speedtest_returns_empty_for_non_mapping_response(make_client) -> None:
    """run_speedtest should return an empty payload for non-mapping responses."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get_with_timeout = AsyncMock(return_value=["not", "a", "mapping"])

        result = await client.run_speedtest()

        assert result == {}
        client._is_get_endpoint_available.assert_awaited_once_with("/api/speedtest/service/showlog")
        client._safe_dict_get_with_timeout.assert_awaited_once_with(
            "/api/speedtest/service/run", timeout_seconds=180
        )
    finally:
        await client.async_close()
