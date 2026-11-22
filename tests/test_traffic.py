"""Tests for diagnostics traffic snapshot and stream helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any, cast
from unittest.mock import MagicMock

import aiohttp
import pytest

from aiopnsense import (
    OPNsenseConnectionError,
    OPNsenseError,
    OPNsensePrivilegeMissing,
    OPNsenseTimeoutError,
)
from aiopnsense.traffic import (
    DIAGNOSTICS_TRAFFIC_ENDPOINT,
    DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX,
    normalize_traffic_payload,
)
from tests.conftest import (
    FakeStreamResponseFactory,
    MakeClientFactory,
    install_fake_router,
    make_mock_session_client,
)


def test_diagnostics_traffic_stream_endpoint_prefix() -> None:
    """Stream endpoint prefix constant is present and points to the expected path."""
    assert DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX == "/api/diagnostics/traffic/stream"


def test_diagnostics_traffic_endpoint_points_to_interface() -> None:
    """Snapshot endpoint constant should target interface-scoped diagnostics traffic."""
    assert DIAGNOSTICS_TRAFFIC_ENDPOINT == "/api/diagnostics/traffic/interface"


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


def test_normalize_traffic_payload_skips_negative_byte_packet_rates() -> None:
    """Streaming payloads with negative byte/packet deltas should not publish rates."""
    payload = {
        "time": 1710000007,
        "interfaces": {
            "wan": {
                "bytes received": -100,
                "bytes transmitted": 200,
                "packets received": -10,
                "packets transmitted": 30,
            },
        },
    }

    normalized = normalize_traffic_payload(payload, interval=2.0)
    row = normalized["interfaces"]["wan"]

    assert row["tx_bytes"] == 200
    assert row["tx_packets"] == 30
    assert "rx_bytes" not in row
    assert "rx_packets" not in row
    assert "rx_bytes_per_second" not in row
    assert "rx_bits_per_second" not in row
    assert "rx_packets_per_second" not in row
    assert row["tx_bytes_per_second"] == 100.0
    assert row["tx_bits_per_second"] == 800.0
    assert row["tx_packets_per_second"] == 15.0


@pytest.mark.asyncio
async def test_get_interface_traffic_normalizes_snapshot_with_one_request(
    make_client: MakeClientFactory,
) -> None:
    """`get_interface_traffic()` normalizes the Router snapshot using a single request.

    Args:
        make_client (MakeClientFactory): Client factory used to build the client under test.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client,
        session,
        routes={
            ("GET", DIAGNOSTICS_TRAFFIC_ENDPOINT): {
                "time": 1710000001,
                "interfaces": {
                    "wan": {
                        "name": "WAN",
                        "bytes received": 120,
                        "bytes transmitted": 240,
                    }
                },
            }
        },
    )
    try:
        traffic = await client.get_interface_traffic()

        assert traffic["time"] == 1710000001.0
        assert traffic["interfaces"]["wan"]["interface"] == "wan"
        assert traffic["interfaces"]["wan"]["name"] == "WAN"
        assert traffic["interfaces"]["wan"]["rx_bytes"] == 120
        assert "tx_bits_per_second" not in traffic["interfaces"]["wan"]
        assert router.count("GET", DIAGNOSTICS_TRAFFIC_ENDPOINT) == 1
    finally:
        await client.async_close()


def test_normalize_traffic_payload_without_rates_for_snapshots() -> None:
    """Snapshot normalization should omit per-second rates while preserving counters."""
    payload = {
        "time": 1710000006,
        "interfaces": {
            "wan": {
                "bytes received": "1200",
                "bytes transmitted": "3400",
            }
        },
    }

    normalized = normalize_traffic_payload(payload, interval=1.0, include_per_second_rates=False)

    assert normalized["interfaces"]["wan"]["rx_bytes"] == 1200
    assert normalized["interfaces"]["wan"]["tx_bytes"] == 3400
    assert "rx_bytes_per_second" not in normalized["interfaces"]["wan"]
    assert "tx_bytes_per_second" not in normalized["interfaces"]["wan"]
    assert "interval" not in normalized["interfaces"]["wan"]


@pytest.mark.parametrize(
    "route_value",
    [
        pytest.param(404, id="missing-endpoint"),
        pytest.param(403, id="permission-denied"),
        pytest.param(500, id="server-error"),
        pytest.param(TimeoutError("timed out"), id="timeout"),
        pytest.param(aiohttp.ClientConnectionError("down"), id="connection-error"),
    ],
)
@pytest.mark.asyncio
async def test_get_interface_traffic_returns_empty_sample_when_endpoint_unavailable(
    make_client: MakeClientFactory,
    route_value: Any,
) -> None:
    """Absent or failing traffic endpoints give an empty sample in default mode.

    Args:
        make_client (MakeClientFactory): Client factory used to build the client under test.
        route_value (Any): Response the Router gives for the traffic endpoint.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client, session, routes={("GET", DIAGNOSTICS_TRAFFIC_ENDPOINT): route_value}
    )
    try:
        traffic = await client.get_interface_traffic()

        assert traffic == {"time": None, "interfaces": {}}
        assert router.count("GET", DIAGNOSTICS_TRAFFIC_ENDPOINT) == 1
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    "route_value, expected_error",
    [
        pytest.param(403, OPNsensePrivilegeMissing, id="permission-denied"),
        pytest.param(500, OPNsenseConnectionError, id="server-error"),
        pytest.param(TimeoutError("timed out"), OPNsenseTimeoutError, id="timeout"),
    ],
)
@pytest.mark.asyncio
async def test_get_interface_traffic_raises_in_throw_mode(
    make_client: MakeClientFactory,
    route_value: Any,
    expected_error: type[OPNsenseError],
) -> None:
    """Permission and transient failures raise a public error when throw_errors is enabled.

    Args:
        make_client (MakeClientFactory): Client factory used to build the client under test.
        route_value (Any): Response the Router gives for the traffic endpoint.
        expected_error (type[OPNsenseError]): Public exception type expected from the call.
    """
    client, session = make_mock_session_client(make_client)
    install_fake_router(
        client, session, routes={("GET", DIAGNOSTICS_TRAFFIC_ENDPOINT): route_value}
    )
    client._throw_errors = True
    try:
        with pytest.raises(expected_error):
            await client.get_interface_traffic()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_interface_traffic_missing_endpoint_does_not_raise_in_throw_mode(
    make_client: MakeClientFactory,
) -> None:
    """A missing traffic endpoint (404) is an empty sample, not an error, even in throw mode.

    Args:
        make_client (MakeClientFactory): Client factory used to build the client under test.
    """
    client, session = make_mock_session_client(make_client)
    install_fake_router(client, session, routes={("GET", DIAGNOSTICS_TRAFFIC_ENDPOINT): 404})
    client._throw_errors = True
    try:
        assert await client.get_interface_traffic() == {"time": None, "interfaces": {}}
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


def test_normalize_traffic_payload_strips_identity_whitespace() -> None:
    """Identity fields should strip padding before preserving their values."""
    payload = {
        "time": 1710000004,
        "interfaces": {
            "wan": {
                "interface": "  wan  ",
                "name": "  WAN  ",
                "bytes received": 10,
                "bytes transmitted": 20,
            },
            "lan": {
                "interface": None,
                "name": None,
                "description": "  LAN  ",
                "bytes received": 5,
                "bytes transmitted": 10,
            },
        },
    }

    normalized = normalize_traffic_payload(payload, interval=1.0)

    assert normalized["interfaces"]["wan"]["interface"] == "wan"
    assert normalized["interfaces"]["wan"]["name"] == "WAN"
    assert normalized["interfaces"]["lan"]["interface"] == "lan"
    assert normalized["interfaces"]["lan"]["name"] == "LAN"


@pytest.mark.asyncio
async def test_stream_interface_traffic_yields_normalized_samples(
    make_client: Callable[..., Any],
) -> None:
    """Stream method should normalize each valid stream event.

    Args:
        make_client (Callable[..., Any]): Client factory yielding raw traffic samples for normalization.
    """

    async def fake_stream(_path: str, **_: Any) -> AsyncIterator[dict[str, Any]]:
        yield {
            "time": 1710000005,
            "interfaces": {"wan": {"bytes received": 0, "bytes transmitted": 0}},
        }
        yield {
            "time": 1710000006,
            "interfaces": {"wan": {"bytes received": 1000, "bytes transmitted": 2000}},
        }

    session = MagicMock()
    client = make_client(session=session)
    try:
        router = install_fake_router(client, session)
        client._stream_json_events = cast(Any, fake_stream)

        samples: list[dict[str, Any]] = []
        async for sample in client.stream_interface_traffic(poll_interval=1):
            samples.append(sample)
            if len(samples) == 1:
                break

        assert router.count("GET", f"{DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX}/1") == 1
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
    """Stream traffic should parse non-ASCII interface identity from real SSE decoding.

    Args:
        make_client (Callable[..., Any]): Client factory using the real streaming code path.
        fake_stream_response_factory (FakeStreamResponseFactory): Factory serving the non-ASCII stream response.
    """
    session = MagicMock()
    client = make_client(session=session)
    install_fake_router(client, session)
    session.get = lambda *a, **k: fake_stream_response_factory(
        [
            b'data: {"time": 1, "interfaces": {"wan": {"bytes received": 0, "bytes transmitted": 0}}}\n\n',
            b'data: {"time": 2, "interfaces": {"wlan-\xc3\x9f": {"name": null, "description": "Tr\xc3\xa1ffic", "bytes received": 1200, "bytes transmitted": 2400}}}\n\n',
        ]
    )
    try:
        samples = [sample async for sample in client.stream_interface_traffic(poll_interval=1)]

        assert list(samples[0]["interfaces"].keys()) == ["wlan-ß"]
        assert samples[0]["interfaces"]["wlan-ß"]["name"] == "Tráffic"
        assert samples[0]["interfaces"]["wlan-ß"]["interface"] == "wlan-ß"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_closes_inner_iterator(
    make_client: Callable[..., Any],
) -> None:
    """Stream should close its inner SSE iterator when consumer exits early.

    Args:
        make_client (Callable[..., Any]): Client factory yielding a closable inner iterator.
    """

    events_closed = False

    async def fake_stream(_path: str, **_: Any) -> AsyncIterator[dict[str, Any]]:
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

    session = MagicMock()
    client = make_client(session=session)
    try:
        install_fake_router(client, session)
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
    """poll_interval below 1 should clamp to 1 for endpoint probing and interval.

    Args:
        make_client (Callable[..., Any]): Client factory that records clamped polling requests.
    """

    async def fake_stream(_path: str, **_: Any) -> AsyncIterator[dict[str, Any]]:
        yield {
            "time": 1710000005,
            "interfaces": {"wan": {"bytes received": 1000, "bytes transmitted": 1000}},
        }
        yield {
            "time": 1710000006,
            "interfaces": {"wan": {"bytes received": 2000, "bytes transmitted": 3000}},
        }

    session = MagicMock()
    client = make_client(session=session)
    try:
        router = install_fake_router(client, session)
        client._stream_json_events = cast(Any, fake_stream)

        samples = [sample async for sample in client.stream_interface_traffic(poll_interval=0)]

        assert router.count("GET", f"{DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX}/1") == 1
        assert samples[0]["interfaces"]["wan"]["interval"] == 1.0
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_uses_poll_interval_fallback_on_bad_or_backward_timestamps(
    make_client: Callable[..., Any],
) -> None:
    """Bad timestamp should reseed and then recover interval with following valid timestamp.

    Args:
        make_client (Callable[..., Any]): Client factory yielding samples with unusable timestamps.
    """

    async def fake_stream(_path: str, **_: Any) -> AsyncIterator[dict[str, Any]]:
        yield {
            "time": 1710000005,
            "interfaces": {"wan": {"bytes received": 1000, "bytes transmitted": 1000}},
        }
        yield {
            "time": "bad",
            "interfaces": {"wan": {"bytes received": 3000, "bytes transmitted": 5000}},
        }
        yield {
            "time": 1710000008,
            "interfaces": {"wan": {"bytes received": 6000, "bytes transmitted": 9000}},
        }
        yield {
            "time": 1710000010,
            "interfaces": {"wan": {"bytes received": 10000, "bytes transmitted": 12000}},
        }
        yield {
            "time": 1710000009,
            "interfaces": {"wan": {"bytes received": 11000, "bytes transmitted": 13000}},
        }
        yield {
            "time": 1710000012,
            "interfaces": {"wan": {"bytes received": 12000, "bytes transmitted": 14000}},
        }

    session = MagicMock()
    client = make_client(session=session)
    try:
        install_fake_router(client, session)
        client._stream_json_events = cast(Any, fake_stream)

        samples = [sample async for sample in client.stream_interface_traffic(poll_interval=1)]

        assert len(samples) == 3
        assert samples[0]["time"] == 1710000008.0
        assert samples[0]["interfaces"]["wan"]["interval"] == 1.0

        # Skipped first event used to initialize stream state.
        # Bad and backward timestamps should be dropped, then the next valid
        # sample should use the fallback interval while timing is reseeded.
        assert samples[1]["time"] == 1710000010.0
        assert samples[1]["interfaces"]["wan"]["interval"] == 2.0

        assert samples[2]["time"] == 1710000012.0
        assert samples[2]["interfaces"]["wan"]["interval"] == 1.0
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_passes_read_timeout_from_clamped_poll_interval(
    make_client: Callable[..., Any],
    fake_stream_response_factory: FakeStreamResponseFactory,
) -> None:
    """Stream poll interval should influence stream read timeout for delayed SSE frames.

    Args:
        make_client (Callable[..., Any]): Client factory using the real streaming code path.
        fake_stream_response_factory (FakeStreamResponseFactory): Factory that records stream read-timeout options.
    """
    captured_timeout: aiohttp.ClientTimeout | None = None

    def fake_get(*_args: Any, **kwargs: Any) -> Any:
        nonlocal captured_timeout
        captured_timeout = kwargs["timeout"]
        return fake_stream_response_factory(
            [
                b'data: {"time": 1710000010, "interfaces": {"wan": {"bytes received": 0, "bytes transmitted": 0}}}\n\n',
                b'data: {"time": 1710000011, "interfaces": {"wan": {"bytes received": 1000, "bytes transmitted": 2000}}}\n\n',
            ]
        )

    session = MagicMock()
    client = make_client(session=session)
    install_fake_router(client, session)
    session.get = fake_get
    try:
        samples = [sample async for sample in client.stream_interface_traffic(poll_interval=120)]

        assert len(samples) == 1
        assert captured_timeout is not None
        assert captured_timeout.sock_read == 121
        assert samples[0]["interfaces"]["wan"]["interface"] == "wan"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_reseeds_interval_after_malformed_json_event(
    make_client: Callable[..., Any],
    fake_stream_response_factory: FakeStreamResponseFactory,
) -> None:
    """Malformed JSON frame should reseed timing so next delta uses poll_interval.

    Args:
        make_client (Callable[..., Any]): Client factory using the real streaming code path.
        fake_stream_response_factory (FakeStreamResponseFactory): Factory serving a malformed JSON stream event.
    """
    session = MagicMock()
    client = make_client(session=session)
    install_fake_router(client, session)
    session.get = lambda *a, **k: fake_stream_response_factory(
        [
            b'data: {"time": 1710000010, "interfaces": {"wan": {"bytes received": 1000, "bytes transmitted": 2000}}}\n\n',
            b"data: not-json\n\n",
            b'data: {"time": 1710000013, "interfaces": {"wan": {"bytes received": 2000, "bytes transmitted": 3000}}}\n\n',
        ]
    )
    try:
        samples = [sample async for sample in client.stream_interface_traffic(poll_interval=10)]

        assert len(samples) == 1
        assert samples[0]["interfaces"]["wan"]["interval"] == 10.0
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_reseeds_interval_after_non_mapping_json_event(
    make_client: Callable[..., Any],
    fake_stream_response_factory: FakeStreamResponseFactory,
) -> None:
    """Non-mapping JSON frames should reseed timing like malformed JSON.

    Args:
        make_client (Callable[..., Any]): Client factory using the real streaming code path.
        fake_stream_response_factory (FakeStreamResponseFactory): Factory serving a non-mapping JSON stream event.
    """

    session = MagicMock()
    client = make_client(session=session)
    install_fake_router(client, session)
    session.get = lambda *a, **k: fake_stream_response_factory(
        [
            b'data: {"time": 1710000010, "interfaces": {"wan": {"bytes received": 1000, "bytes transmitted": 2000}}}\n\n',
            b"data: [1, 2, 3]\n\n",
            b'data: {"time": 1710000013, "interfaces": {"wan": {"bytes received": 2000, "bytes transmitted": 3000}}}\n\n',
        ]
    )
    try:
        samples = [sample async for sample in client.stream_interface_traffic(poll_interval=10)]

        assert len(samples) == 1
        assert samples[0]["time"] == 1710000013
        assert samples[0]["interfaces"]["wan"]["interval"] == 10.0
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_resets_interval_after_stream_json_reset(
    make_client: Callable[..., Any],
    fake_stream_response_factory: FakeStreamResponseFactory,
) -> None:
    """A dropped invalid UTF-8 frame should force fallback interval on next sample.

    Args:
        make_client (Callable[..., Any]): Client factory using the real streaming code path.
        fake_stream_response_factory (FakeStreamResponseFactory): Factory serving a reset-marker stream event.
    """

    session = MagicMock()
    client = make_client(session=session)
    install_fake_router(client, session)
    session.get = lambda *a, **k: fake_stream_response_factory(
        [
            b'data: {"time": 1710000005, "interfaces": {"wan": {"bytes received": 0, "bytes transmitted": 0}}}\n\n',
            b'data: {"time": 1710000006, "interfaces": {"wan": {"bytes received": 1000, "bytes transmitted": 2000}}}\n\n',
            b"\xff\xfe\xfd",
            b'data: {"time": 1710000008, "interfaces": {"wan": {"bytes received": 3000, "bytes transmitted": 5000}}}\n\n',
        ]
    )
    try:
        samples = [sample async for sample in client.stream_interface_traffic(poll_interval=1)]

        assert len(samples) == 2
        assert samples[0]["time"] == 1710000006
        assert samples[0]["interfaces"]["wan"]["interval"] == 1.0
        assert samples[1]["time"] == 1710000008
        assert samples[1]["interfaces"]["wan"]["interval"] == 1.0
    finally:
        await client.async_close()


STREAM_PATH = f"{DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX}/1"


@pytest.mark.parametrize(
    "status",
    [
        pytest.param(404, id="absent-404"),
        pytest.param(403, id="forbidden-403"),
    ],
)
@pytest.mark.asyncio
async def test_stream_interface_traffic_ends_quietly_when_endpoint_unavailable(
    make_client: Callable[..., Any],
    status: int,
) -> None:
    """Stream method should end without yielding when the Router reports the endpoint unavailable.

    Args:
        make_client (Callable[..., Any]): Client factory backed by a fake Router.
        status (int): HTTP status the Router returns for the stream endpoint.
    """
    session = MagicMock()
    client = make_client(session=session)
    router = install_fake_router(client, session, routes={("GET", STREAM_PATH): status})
    try:
        samples = [sample async for sample in client.stream_interface_traffic(poll_interval=1)]

        assert samples == []
        assert router.count("GET", STREAM_PATH) == 1
        assert router.count() == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_raises_permission_error_when_throw_errors_enabled(
    make_client: Callable[..., Any],
) -> None:
    """A 403 on the stream endpoint should raise in throw mode while a 404 still ends quietly.

    Args:
        make_client (Callable[..., Any]): Client factory configured to raise public errors.
    """
    session = MagicMock()
    client = make_client(session=session, throw_errors=True)
    router = install_fake_router(client, session, routes={("GET", STREAM_PATH): 403})
    try:
        with pytest.raises(OPNsensePrivilegeMissing):
            [sample async for sample in client.stream_interface_traffic(poll_interval=1)]
        assert router.count("GET", STREAM_PATH) == router.count() == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_ends_quietly_on_absent_endpoint_in_throw_mode(
    make_client: Callable[..., Any],
) -> None:
    """A 404 on the stream endpoint is a normal empty result even when throw_errors is enabled.

    Args:
        make_client (Callable[..., Any]): Client factory configured to raise public errors.
    """
    session = MagicMock()
    client = make_client(session=session, throw_errors=True)
    router = install_fake_router(client, session, routes={("GET", STREAM_PATH): 404})
    try:
        samples = [sample async for sample in client.stream_interface_traffic(poll_interval=1)]

        assert samples == []
        assert router.count("GET", STREAM_PATH) == router.count() == 1
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    "probe_error",
    [
        pytest.param(TimeoutError("probe timeout"), id="timeout"),
        pytest.param(OPNsenseError("probe failed"), id="opnsense-error"),
    ],
)
@pytest.mark.asyncio
async def test_stream_interface_traffic_returns_empty_iteration_on_check_error(
    make_client: MakeClientFactory,
    probe_error: Exception,
) -> None:
    """Failed stream checks should return an empty stream when throw_errors is disabled.

    Args:
        make_client (MakeClientFactory): Client factory whose stream check fails.
        probe_error (Exception): Error raised by the Router for the stream endpoint.
    """
    session = MagicMock()
    client = make_client(session=session)
    router = install_fake_router(client, session, routes={("GET", STREAM_PATH): probe_error})
    try:
        samples = [sample async for sample in client.stream_interface_traffic(poll_interval=1)]

        assert samples == []
        assert router.count("GET", STREAM_PATH) == router.count() == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_raises_same_opnsense_error_when_throw_errors_enabled(
    make_client: MakeClientFactory,
) -> None:
    """Mapped OPNsense errors from the stream check should be re-raised when throw_errors is enabled.

    Args:
        make_client (MakeClientFactory): Client factory configured to re-raise ``OPNsenseError``.
    """
    session = MagicMock()
    client = make_client(session=session, throw_errors=True)
    probe_error = OPNsenseError("probe failed")
    install_fake_router(client, session, routes={("GET", STREAM_PATH): probe_error})
    try:
        with pytest.raises(OPNsenseError) as exc:
            [sample async for sample in client.stream_interface_traffic(poll_interval=1)]
        assert exc.value is probe_error
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_raises_when_probe_timeout_and_throw_errors_enabled(
    make_client: Callable[..., Any],
) -> None:
    """A stream check timeout should propagate when throw_errors is enabled.

    Args:
        make_client (Callable[..., Any]): Client factory configured to re-raise the stream timeout.
    """
    session = MagicMock()
    client = make_client(session=session, throw_errors=True)
    install_fake_router(
        client, session, routes={("GET", STREAM_PATH): TimeoutError("probe timeout")}
    )
    try:
        with pytest.raises(OPNsenseTimeoutError):
            [sample async for sample in client.stream_interface_traffic(poll_interval=1)]
    finally:
        await client.async_close()


# Keep factories in the shared immutable cases: pytest reuses parameter values, while
# raised public errors carry logged state and therefore need a fresh instance per test.
_TRAFFIC_FAILURE_CASES: tuple[Any, ...] = (
    pytest.param(lambda: TimeoutError("timeout"), OPNsenseTimeoutError, id="timeout"),
    pytest.param(lambda: RuntimeError("runtime"), OPNsenseError, id="runtime"),
    pytest.param(lambda: TypeError("type"), OPNsenseError, id="type"),
    pytest.param(lambda: ValueError("value"), OPNsenseError, id="value"),
    pytest.param(lambda: AttributeError("attribute"), OPNsenseError, id="attribute"),
    pytest.param(lambda: OPNsenseError("opnsense"), OPNsenseError, id="opnsense"),
)


@pytest.mark.parametrize("throw_errors", [False, True])
@pytest.mark.parametrize(("make_error", "expected_error"), _TRAFFIC_FAILURE_CASES)
@pytest.mark.parametrize("failing_step", ["probe", "fetch"])
@pytest.mark.asyncio
async def test_get_interface_traffic_failure_follows_throw_errors_contract(
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
    throw_errors: bool,
    make_error: Callable[[], Exception],
    expected_error: type[OPNsenseError],
    failing_step: str,
) -> None:
    """A handled failure yields an empty sample, logged quietly, or a mapped error.

    Args:
        make_client (MakeClientFactory): Client factory.
        caplog (pytest.LogCaptureFixture): Log capture used to check the failure stays at debug.
        throw_errors (bool): Whether the client raises public errors.
        make_error (Callable[[], Exception]): Factory for a fresh injected error.
        expected_error (type[OPNsenseError]): Public error expected with throw on.
        failing_step (str): Request step that raises, the probe or the sample fetch.
    """
    client = make_client(throw_errors=throw_errors)
    try:
        client._is_get_endpoint_available = AsyncMock(
            side_effect=make_error() if failing_step == "probe" else None,
            return_value=True,
        )
        client._safe_dict_get = AsyncMock(
            side_effect=make_error() if failing_step == "fetch" else None
        )

        with caplog.at_level("DEBUG", logger="aiopnsense"):
            if throw_errors:
                with pytest.raises(expected_error):
                    await client.get_interface_traffic()
            else:
                assert await client.get_interface_traffic() == {"time": None, "interfaces": {}}
                assert not [r for r in caplog.records if r.levelname in {"WARNING", "ERROR"}]
    finally:
        await client.async_close()


@pytest.mark.parametrize("throw_errors", [False, True])
@pytest.mark.parametrize(("make_error", "expected_error"), _TRAFFIC_FAILURE_CASES)
@pytest.mark.asyncio
async def test_stream_interface_traffic_probe_failure_follows_throw_errors_contract(
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
    throw_errors: bool,
    make_error: Callable[[], Exception],
    expected_error: type[OPNsenseError],
) -> None:
    """A handled probe failure ends the stream empty, logged quietly, or raises a mapped error.

    Args:
        make_client (MakeClientFactory): Client factory.
        caplog (pytest.LogCaptureFixture): Log capture used to check the failure stays at debug.
        throw_errors (bool): Whether the client raises public errors.
        make_error (Callable[[], Exception]): Factory for a fresh injected error.
        expected_error (type[OPNsenseError]): Public error expected with throw on.
    """
    client = make_client(throw_errors=throw_errors)
    try:
        client._is_get_endpoint_available = AsyncMock(side_effect=make_error())
        client._stream_json_events = MagicMock()

        with caplog.at_level("DEBUG", logger="aiopnsense"):
            if throw_errors:
                with pytest.raises(expected_error):
                    [s async for s in client.stream_interface_traffic(poll_interval=1)]
            else:
                assert [s async for s in client.stream_interface_traffic(poll_interval=1)] == []
                assert not [r for r in caplog.records if r.levelname in {"WARNING", "ERROR"}]
        client._stream_json_events.assert_not_called()
    finally:
        await client.async_close()


@pytest.mark.parametrize("throw_errors", [False, True])
@pytest.mark.parametrize("method", ["sample", "stream"])
@pytest.mark.parametrize(
    "make_error",
    [lambda: asyncio.CancelledError(), lambda: KeyError("unhandled")],
    ids=["cancelled", "unhandled-keyerror"],
)
@pytest.mark.asyncio
async def test_traffic_probe_propagates_cancellation_and_unhandled_errors(
    make_client: MakeClientFactory,
    throw_errors: bool,
    method: str,
    make_error: Callable[[], BaseException],
) -> None:
    """Cancellation and errors outside the handled set propagate in both modes.

    Args:
        make_client (MakeClientFactory): Client factory.
        throw_errors (bool): Whether the client raises public errors.
        method (str): Which traffic method is called.
        make_error (Callable[[], BaseException]): Factory for a fresh injected error.
    """
    client = make_client(throw_errors=throw_errors)
    try:
        error = make_error()
        client._is_get_endpoint_available = AsyncMock(side_effect=error)

        with pytest.raises(type(error)):
            if method == "sample":
                await client.get_interface_traffic()
            else:
                [s async for s in client.stream_interface_traffic(poll_interval=1)]
    finally:
        await client.async_close()
