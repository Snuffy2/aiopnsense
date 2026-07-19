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
            "/api/speedtest/service/showrecent"
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
async def test_get_speedtest_normalizes_recent_and_stat_payloads(make_client) -> None:
    """get_speedtest should normalize showrecent and showstat payload fields."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            side_effect=[
                (
                    "available",
                    {
                        "date": "2026-03-14T03:09:45",
                        "server": "72800 RippleFiber, Newark, NJ",
                        "download": "836.05",
                        "upload": "832.97",
                        "latency": "4.0",
                        "url": "https://www.speedtest.net/result/c/abc",
                    },
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
                ("available", {"download": "1", "upload": "2", "latency": "3"}),
                ("available", {}),
            ],
            True,
            id="showstat-available",
        ),
        pytest.param(
            [
                ("available", {"download": "1", "upload": "2", "latency": "3"}),
                ("missing", {}),
            ],
            False,
            id="showstat-missing",
        ),
    ],
)
@pytest.mark.asyncio
async def test_get_speedtest_probes_showstat_before_fetching_optional_payload(
    make_client: ClientType,
    optional_results: list[tuple[str, dict[str, str]]],
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
            call("/api/speedtest/service/showrecent"),
            call("/api/speedtest/service/showstat"),
        ]

        if showstat_available:
            assert result["last"]["download"]["value"] == 1.0
            assert result["last"]["upload"]["value"] == 2.0
            assert result["last"]["latency"]["value"] == 3.0
        else:
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
        client._check_optional_get_endpoint = AsyncMock(
            side_effect=[
                (
                    "available",
                    {
                        "date": 12345,
                        "server": "Regional POP - NYC",
                        "download": "bad-number",
                        "upload": "12.5",
                        "latency": None,
                        "url": 999,
                    },
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


@pytest.mark.asyncio
async def test_parse_recent_server_variants(make_client) -> None:
    """_parse_recent_server should parse known server formats safely."""
    client, _session = make_mock_session_client(make_client)
    try:
        assert client._parse_recent_server(None) == (None, None)
        assert client._parse_recent_server("   ") == (None, None)
        assert client._parse_recent_server("10001 Test ISP, NY") == ("10001", "Test ISP, NY")
        assert client._parse_recent_server("Unstructured Server Name") == (
            None,
            "Unstructured Server Name",
        )
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
            "/api/speedtest/service/showrecent"
        )
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
        client._check_optional_get_endpoint = AsyncMock(return_value=("missing", {}))
        client._safe_dict_get_with_timeout = AsyncMock()

        result = await client.run_speedtest()

        assert result == {}
        client._safe_dict_get_with_timeout.assert_not_awaited()
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/speedtest/service/showrecent"
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
            "/api/speedtest/service/showrecent"
        )
        client._safe_dict_get_with_timeout.assert_awaited_once_with(
            "/api/speedtest/service/run", timeout_seconds=180
        )
    finally:
        await client.async_close()
