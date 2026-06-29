"""Tests for diagnostics traffic snapshot and stream helpers."""

from __future__ import annotations


from collections.abc import AsyncIterator, Callable
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import FakeStreamResponseFactory
from aiopnsense.traffic import (
    DIAGNOSTICS_TRAFFIC_ENDPOINT,
    DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX,
    normalize_traffic_payload,
)


def test_diagnostics_traffic_stream_endpoint_prefix() -> None:
    """Stream endpoint prefix constant is present and points to the expected path."""
    assert DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX == "/api/diagnostics/traffic/stream"


def test_normalize_traffic_payload_from_interfaces_mapping() -> None:
    """Traffic payloads with an interfaces mapping should normalize aliases and rates."""
    payload = {
        "time": 1710000001,
        "interfaces": {
            "wan": {
                "interface": "wan",
                "name": "WAN",
                "bytes received": "1200",
                "bytes transmitted": "3400",
                "packets received": "12",
                "packets transmitted": "34",
                "input errors": "1",
                "output errors": "2",
                "collisions": "3",
            }
        },
    }

    normalized = normalize_traffic_payload(payload, interval=1.0)

    assert normalized == {
        "time": 1710000001.0,
        "interfaces": {
            "wan": {
                "interface": "wan",
                "name": "WAN",
                "rx_bytes": 1200,
                "tx_bytes": 3400,
                "rx_packets": 12,
                "tx_packets": 34,
                "rx_errors": 1,
                "tx_errors": 2,
                "collisions": 3,
                "interval": 1.0,
                "rx_bytes_per_second": 1200.0,
                "tx_bytes_per_second": 3400.0,
                "rx_bits_per_second": 9600.0,
                "tx_bits_per_second": 27200.0,
                "rx_packets_per_second": 12.0,
                "tx_packets_per_second": 34.0,
            }
        },
    }


def test_normalize_traffic_payload_from_top_level_interfaces() -> None:
    """Traffic payloads keyed directly by interface name should normalize."""
    payload = {
        "time": "1710000002.5",
        "lan": {
            "name": "LAN",
            "rx_bytes": 2000,
            "tx_bytes": 4000,
            "rx_packets": 20,
            "tx_packets": 40,
        },
    }

    normalized = normalize_traffic_payload(payload, interval=2.0)

    assert normalized["time"] == 1710000002.5
    assert normalized["interfaces"]["lan"]["rx_bytes_per_second"] == 1000.0
    assert normalized["interfaces"]["lan"]["tx_bits_per_second"] == 16000.0


def test_normalize_traffic_payload_skips_invalid_rows() -> None:
    """Malformed interface rows should be ignored without dropping valid rows."""
    payload = {
        "time": 1710000003,
        "interfaces": {
            "valid": {"bytes received": 100, "bytes transmitted": 200},
            "invalid_list": ["not", "a", "mapping"],
            "invalid_empty": {},
        },
    }

    normalized = normalize_traffic_payload(payload, interval=1.0)

    assert list(normalized["interfaces"]) == ["valid"]
    assert normalized["interfaces"]["valid"]["rx_bytes"] == 100
    assert normalized["interfaces"]["valid"]["tx_bytes"] == 200


@pytest.mark.asyncio
async def test_get_interface_traffic_probes_and_normalizes(
    make_client: Callable[..., Any],
) -> None:
    """`OPNsenseClient.get_interface_traffic()` should probe, fetch, and normalize."""
    client = make_client()
    try:
        assert isinstance(client, OPNsenseClient)
        assert hasattr(client, "get_interface_traffic")

        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={
                "time": 1710000001,
                "interfaces": {
                    "wan": {
                        "name": "WAN",
                        "bytes received": 120,
                        "bytes transmitted": 240,
                    }
                },
            }
        )

        traffic = await client.get_interface_traffic()
        assert traffic["time"] == 1710000001.0
        assert traffic["interfaces"]["wan"]["interface"] == "wan"
        assert traffic["interfaces"]["wan"]["name"] == "WAN"
        assert traffic["interfaces"]["wan"]["rx_bytes"] == 120
        assert traffic["interfaces"]["wan"]["tx_bits_per_second"] == 1920.0
        client._is_get_endpoint_available.assert_awaited_once_with(DIAGNOSTICS_TRAFFIC_ENDPOINT)
        client._safe_dict_get.assert_awaited_once_with(DIAGNOSTICS_TRAFFIC_ENDPOINT)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_interface_traffic_handles_unavailable_endpoint(
    make_client: Callable[..., Any],
) -> None:
    """Unavailable traffic endpoint should return an empty sample and avoid GET calls."""
    client = make_client()
    try:
        assert isinstance(client, OPNsenseClient)

        client._is_get_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get = AsyncMock()

        traffic = await client.get_interface_traffic()
        assert traffic == {"time": None, "interfaces": {}}
        client._is_get_endpoint_available.assert_awaited_once_with(DIAGNOSTICS_TRAFFIC_ENDPOINT)
        client._safe_dict_get.assert_not_awaited()
    finally:
        await client.async_close()


def test_normalize_traffic_payload_falls_back_identity_fields() -> None:
    """Null interface identity values should fall back to key fields and description."""
    payload = {
        "time": 1710000004,
        "interfaces": {
            "wan": {
                "interface": None,
                "name": None,
                "description": "WAN",
                "bytes received": 10,
                "bytes transmitted": 20,
            },
            "lan": {
                "interface": "",
                "name": "",
                "description": "",
                "bytes received": 5,
                "bytes transmitted": 10,
            },
        },
    }

    normalized = normalize_traffic_payload(payload, interval=1.0)

    assert normalized["interfaces"]["wan"]["interface"] == "wan"
    assert normalized["interfaces"]["wan"]["name"] == "WAN"
    assert normalized["interfaces"]["lan"]["interface"] == "lan"
    assert normalized["interfaces"]["lan"]["name"] == "lan"


@pytest.mark.asyncio
async def test_stream_interface_traffic_yields_normalized_samples(
    make_client: Callable[..., Any],
) -> None:
    """Stream method should normalize each valid stream event."""

    async def fake_stream(_path: str) -> AsyncIterator[dict[str, Any]]:
        yield {
            "time": 1710000005,
            "interfaces": {"wan": {"bytes received": 0, "bytes transmitted": 0}},
        }
        yield {
            "time": 1710000006,
            "interfaces": {"wan": {"bytes received": 1000, "bytes transmitted": 2000}},
        }

    client = make_client()
    try:
        assert isinstance(client, OPNsenseClient)
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._stream_json_events = cast(Any, fake_stream)

        samples: list[dict[str, Any]] = []
        async for sample in client.stream_interface_traffic(poll_interval=1):
            samples.append(sample)
            if len(samples) == 1:
                break

        client._is_get_endpoint_available.assert_awaited_once_with(
            f"{DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX}/1"
        )
        assert samples == [
            {
                "time": 1710000006.0,
                "interfaces": {
                    "wan": {
                        "interface": "wan",
                        "name": "wan",
                        "rx_bytes": 1000,
                        "tx_bytes": 2000,
                        "interval": 1.0,
                        "rx_bytes_per_second": 1000.0,
                        "tx_bytes_per_second": 2000.0,
                        "rx_bits_per_second": 8000.0,
                        "tx_bits_per_second": 16000.0,
                    }
                },
            }
        ]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_real_stream_path_keeps_non_ascii_name(
    make_client: Callable[..., Any],
    fake_stream_response_factory: FakeStreamResponseFactory,
) -> None:
    """Stream traffic should parse non-ASCII interface identity from real SSE decoding."""
    session = MagicMock()
    session.get = lambda *a, **k: fake_stream_response_factory(
        [
            b'data: {"time": 1, "interfaces": {"wan": {"bytes received": 0, "bytes transmitted": 0}}}\n\n',
            b'data: {"time": 2, "interfaces": {"wlan-\xc3\x9f": {"name": null, "description": "Tr\xc3\xa1ffic", "bytes received": 1200, "bytes transmitted": 2400}}}\n\n',
        ]
    )
    client = make_client(session=session)
    try:
        assert isinstance(client, OPNsenseClient)
        client._is_get_endpoint_available = AsyncMock(return_value=True)

        samples = [sample async for sample in client.stream_interface_traffic(poll_interval=1)]

        client._is_get_endpoint_available.assert_awaited_once_with(
            f"{DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX}/1"
        )
        assert list(samples[0]["interfaces"].keys()) == ["wlan-ß"]
        assert samples[0]["interfaces"]["wlan-ß"]["name"] == "Tráffic"
        assert samples[0]["interfaces"]["wlan-ß"]["interface"] == "wlan-ß"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_closes_inner_iterator(
    make_client: Callable[..., Any],
) -> None:
    """Stream should close its inner SSE iterator when consumer exits early."""

    events_closed = False

    async def fake_stream(_path: str) -> AsyncIterator[dict[str, Any]]:
        try:
            yield {
                "time": 1710000005,
                "interfaces": {"wan": {"bytes received": 0, "bytes transmitted": 0}},
            }
            yield {
                "time": 1710000006,
                "interfaces": {"wan": {"bytes received": 1000, "bytes transmitted": 2000}},
            }
        finally:
            nonlocal events_closed
            events_closed = True

    client = make_client()
    try:
        assert isinstance(client, OPNsenseClient)
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._stream_json_events = cast(Any, fake_stream)

        samples = client.stream_interface_traffic(poll_interval=1)
        first = await samples.__anext__()
        assert first["interfaces"]["wan"]["interval"] == 1.0

        await cast(Any, samples).aclose()

        assert events_closed
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_clamps_poll_interval(
    make_client: Callable[..., Any],
) -> None:
    """poll_interval below 1 should clamp to 1 for endpoint probing and interval."""

    async def fake_stream(_path: str) -> AsyncIterator[dict[str, Any]]:
        yield {
            "time": 1710000005,
            "interfaces": {"wan": {"bytes received": 1000, "bytes transmitted": 1000}},
        }
        yield {
            "time": 1710000006,
            "interfaces": {"wan": {"bytes received": 2000, "bytes transmitted": 3000}},
        }

    client = make_client()
    try:
        assert isinstance(client, OPNsenseClient)
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._stream_json_events = cast(Any, fake_stream)

        samples = [sample async for sample in client.stream_interface_traffic(poll_interval=0)]

        client._is_get_endpoint_available.assert_awaited_once_with(
            f"{DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX}/1"
        )
        assert samples[0]["interfaces"]["wan"]["interval"] == 1.0
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_uses_poll_interval_fallback_on_bad_or_backward_timestamps(
    make_client: Callable[..., Any],
) -> None:
    """Bad or backward timestamps should fall back to polling interval and keep state clean."""

    async def fake_stream(_path: str) -> AsyncIterator[dict[str, Any]]:
        yield {
            "time": 1710000006,
            "interfaces": {"wan": {"bytes received": 1000, "bytes transmitted": 1000}},
        }
        yield {
            "time": "bad",
            "interfaces": {"wan": {"bytes received": 3000, "bytes transmitted": 5000}},
        }
        yield {
            "time": 1710000004,
            "interfaces": {"wan": {"bytes received": 6000, "bytes transmitted": 9000}},
        }
        yield {
            "time": 1710000008,
            "interfaces": {"wan": {"bytes received": 10000, "bytes transmitted": 12000}},
        }

    client = make_client()
    try:
        assert isinstance(client, OPNsenseClient)
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._stream_json_events = cast(Any, fake_stream)

        samples = [sample async for sample in client.stream_interface_traffic(poll_interval=1)]

        assert len(samples) == 3
        assert samples[0]["time"] is None
        assert samples[0]["interfaces"]["wan"]["interval"] == 1.0

        # Skipped first event used to initialize stream state.
        # A bad timestamp should use fallback interval.
        assert samples[1]["time"] == 1710000004.0
        assert samples[1]["interfaces"]["wan"]["interval"] == 1.0

        # Backward timestamp after fallback should also keep fallback interval.
        assert samples[2]["time"] == 1710000008.0
        assert samples[2]["interfaces"]["wan"]["interval"] == 2.0
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_returns_when_endpoint_unavailable(
    make_client: Callable[..., Any],
) -> None:
    """Stream method should end without yielding when the endpoint is unavailable."""
    client = make_client()
    try:
        assert isinstance(client, OPNsenseClient)

        client._is_get_endpoint_available = AsyncMock(return_value=False)
        client._stream_json_events = AsyncMock()

        samples = [sample async for sample in client.stream_interface_traffic(poll_interval=1)]

        assert samples == []
        client._stream_json_events.assert_not_called()
    finally:
        await client.async_close()
