"""Tests for diagnostics traffic snapshot and stream helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from types import TracebackType
from typing import Any, Self, cast
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from aiopnsense import OPNsenseError, OPNsenseTimeoutError
from aiopnsense.traffic import (
    DIAGNOSTICS_TRAFFIC_ENDPOINT,
    DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX,
    normalize_traffic_payload,
)
from tests.conftest import FakeResponse, MakeClientFactory, make_router_client

STREAM_PATH = f"{DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX}/1"
EMPTY_SAMPLE: dict[str, Any] = {"time": None, "interfaces": {}}


def _sse(time: Any, rx: int, tx: int) -> bytes:
    """Return one server-sent event carrying a WAN traffic sample.

    Args:
        time (Any): Server timestamp reported by the event.
        rx (int): Received byte counter.
        tx (int): Transmitted byte counter.

    Returns:
        bytes: Encoded ``data:`` event.
    """
    event = {"time": time, "interfaces": {"wan": {"bytes received": rx, "bytes transmitted": tx}}}
    return b"data: " + json.dumps(event).encode() + b"\n\n"


class _TrackedStreamResponse(FakeResponse):
    """Stream response that counts the requests still holding it open."""

    def __init__(self, chunks: list[bytes]) -> None:
        """Initialize the response.

        Args:
            chunks (list[bytes]): Stream payload.
        """
        super().__init__(stream_chunks=chunks)
        self.open_requests = 0

    async def __aenter__(self) -> Self:
        """Count one request opening the response.

        Returns:
            Self: The response.
        """
        self.open_requests += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """Count one request releasing the response.

        Args:
            exc_type (type[BaseException] | None): Exception type raised during context usage.
            exc (BaseException | None): Exception instance raised during context usage.
            tb (TracebackType | None): Traceback for any raised exception.

        Returns:
            bool: ``False`` so exceptions are not suppressed.
        """
        self.open_requests -= 1
        return False


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
async def test_get_interface_traffic_probes_and_normalizes(
    make_client: MakeClientFactory,
) -> None:
    """`OPNsenseClient.get_interface_traffic()` fetches the Router snapshot and normalizes it.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = make_router_client(
        make_client,
        {
            ("GET", DIAGNOSTICS_TRAFFIC_ENDPOINT): {
                "time": 1710000001,
                "interfaces": {
                    "wan": {"name": "WAN", "bytes received": 120, "bytes transmitted": 240}
                },
            }
        },
    )

    traffic = await client.get_interface_traffic()

    assert traffic["time"] == 1710000001.0
    assert traffic["interfaces"]["wan"]["interface"] == "wan"
    assert traffic["interfaces"]["wan"]["name"] == "WAN"
    assert traffic["interfaces"]["wan"]["rx_bytes"] == 120
    assert "tx_bits_per_second" not in traffic["interfaces"]["wan"]
    assert router.count("GET", DIAGNOSTICS_TRAFFIC_ENDPOINT) == 1


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


@pytest.mark.asyncio
async def test_get_interface_traffic_handles_unavailable_endpoint(
    make_client: MakeClientFactory,
) -> None:
    """A 404 snapshot endpoint returns an empty sample.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = make_router_client(make_client, {("GET", DIAGNOSTICS_TRAFFIC_ENDPOINT): 404})

    assert await client.get_interface_traffic() == EMPTY_SAMPLE
    assert router.count("GET", DIAGNOSTICS_TRAFFIC_ENDPOINT) == 1


@pytest.mark.parametrize(
    "probe_error",
    [
        pytest.param(TimeoutError("probe timeout"), id="timeout"),
        pytest.param(OPNsenseError("probe failed"), id="opnsense-error"),
    ],
)
@pytest.mark.asyncio
async def test_get_interface_traffic_returns_empty_sample_on_probe_error(
    make_client: MakeClientFactory,
    probe_error: Exception,
) -> None:
    """Probe errors return an empty sample when throw_errors is disabled.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        probe_error (Exception): Error raised by the Router session for the snapshot request.
    """
    client, router = make_router_client(
        make_client, {("GET", DIAGNOSTICS_TRAFFIC_ENDPOINT): probe_error}
    )

    assert await client.get_interface_traffic() == EMPTY_SAMPLE
    assert router.count("GET", DIAGNOSTICS_TRAFFIC_ENDPOINT) == 1


@pytest.mark.asyncio
async def test_get_interface_traffic_raises_when_throw_errors_is_enabled(
    make_client: MakeClientFactory,
) -> None:
    """A snapshot timeout propagates as a public error when throw_errors is enabled.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, _router = make_router_client(
        make_client,
        {("GET", DIAGNOSTICS_TRAFFIC_ENDPOINT): TimeoutError("probe timeout")},
        throw_errors=True,
    )

    with pytest.raises(OPNsenseTimeoutError):
        await client.get_interface_traffic()


@pytest.mark.asyncio
async def test_get_interface_traffic_raises_same_opnsense_error_when_throw_errors_is_enabled(
    make_client: MakeClientFactory,
) -> None:
    """An OPNsense error from the Router session is re-raised unchanged when throw_errors is on.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    probe_error = OPNsenseError("probe failed")
    client, _router = make_router_client(
        make_client, {("GET", DIAGNOSTICS_TRAFFIC_ENDPOINT): probe_error}, throw_errors=True
    )

    with pytest.raises(OPNsenseError) as exc:
        await client.get_interface_traffic()
    assert exc.value is probe_error


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
    make_client: MakeClientFactory,
) -> None:
    """Stream method normalizes each valid stream event.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, _router = make_router_client(
        make_client,
        {
            ("GET", STREAM_PATH): FakeResponse(
                stream_chunks=[_sse(1710000005, 0, 0), _sse(1710000006, 1000, 2000)]
            )
        },
    )

    samples: list[dict[str, Any]] = []
    async for sample in client.stream_interface_traffic(poll_interval=1):
        samples.append(sample)
        if len(samples) == 1:
            break

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


@pytest.mark.asyncio
async def test_stream_interface_traffic_real_stream_path_keeps_non_ascii_name(
    make_client: MakeClientFactory,
) -> None:
    """Stream traffic parses non-ASCII interface identity from real SSE decoding.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, _router = make_router_client(
        make_client,
        {
            ("GET", STREAM_PATH): FakeResponse(
                stream_chunks=[
                    b'data: {"time": 1, "interfaces": {"wan": {"bytes received": 0, "bytes transmitted": 0}}}\n\n',
                    b'data: {"time": 2, "interfaces": {"wlan-\xc3\x9f": {"name": null, "description": "Tr\xc3\xa1ffic", "bytes received": 1200, "bytes transmitted": 2400}}}\n\n',
                ]
            )
        },
    )

    samples = [sample async for sample in client.stream_interface_traffic(poll_interval=1)]

    assert list(samples[0]["interfaces"].keys()) == ["wlan-ß"]
    assert samples[0]["interfaces"]["wlan-ß"]["name"] == "Tráffic"
    assert samples[0]["interfaces"]["wlan-ß"]["interface"] == "wlan-ß"


@pytest.mark.asyncio
async def test_stream_interface_traffic_releases_stream_when_consumer_exits_early(
    make_client: MakeClientFactory,
) -> None:
    """Closing the stream early releases the Router's stream response.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    response = _TrackedStreamResponse(
        [_sse(1710000005, 0, 0), _sse(1710000006, 1000, 2000), _sse(1710000007, 2000, 3000)]
    )
    client, _router = make_router_client(make_client, {("GET", STREAM_PATH): response})

    samples = client.stream_interface_traffic(poll_interval=1)
    first = await samples.__anext__()
    assert first["interfaces"]["wan"]["interval"] == 1.0
    assert response.open_requests == 1

    await cast(Any, samples).aclose()

    assert response.open_requests == 0


@pytest.mark.asyncio
async def test_stream_interface_traffic_clamps_poll_interval(
    make_client: MakeClientFactory,
) -> None:
    """A poll_interval below 1 is clamped to 1 for the stream path and the sample interval.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = make_router_client(
        make_client,
        {
            ("GET", STREAM_PATH): FakeResponse(
                stream_chunks=[_sse(1710000005, 1000, 1000), _sse(1710000006, 2000, 3000)]
            )
        },
    )

    samples = [sample async for sample in client.stream_interface_traffic(poll_interval=0)]

    assert router.count("GET", STREAM_PATH) == 2  # availability check, then the stream
    assert samples[0]["interfaces"]["wan"]["interval"] == 1.0


@pytest.mark.asyncio
async def test_stream_interface_traffic_uses_poll_interval_fallback_on_bad_or_backward_timestamps(
    make_client: MakeClientFactory,
) -> None:
    """Bad timestamp reseeds and then recovers interval with following valid timestamp.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, _router = make_router_client(
        make_client,
        {
            ("GET", STREAM_PATH): FakeResponse(
                stream_chunks=[
                    _sse(1710000005, 1000, 1000),
                    _sse("bad", 3000, 5000),
                    _sse(1710000008, 6000, 9000),
                    _sse(1710000010, 10000, 12000),
                    _sse(1710000009, 11000, 13000),
                    _sse(1710000012, 12000, 14000),
                ]
            )
        },
    )

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


@pytest.mark.asyncio
async def test_stream_interface_traffic_passes_read_timeout_from_clamped_poll_interval(
    make_client: MakeClientFactory,
) -> None:
    """Stream poll interval influences the stream read timeout for delayed SSE frames.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    timeouts: list[aiohttp.ClientTimeout] = []

    def stream(*, timeout: aiohttp.ClientTimeout, **_kwargs: Any) -> FakeResponse:
        """Record the timeout of each request, then answer it with the SSE frames.

        Args:
            timeout (aiohttp.ClientTimeout): Timeout the HTTP transport applies.
            _kwargs (Any): Other request parameters passed by the transport.

        Returns:
            FakeResponse: The Router's stream response.
        """
        timeouts.append(timeout)
        return FakeResponse(stream_chunks=[_sse(1710000010, 0, 0), _sse(1710000011, 1000, 2000)])

    client, _router = make_router_client(
        make_client,
        routes={("GET", f"{DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX}/120"): stream},
    )

    samples = [sample async for sample in client.stream_interface_traffic(poll_interval=120)]

    assert len(samples) == 1
    assert timeouts[-1].sock_read == 121  # the stream request follows the availability check
    assert samples[0]["interfaces"]["wan"]["interface"] == "wan"


@pytest.mark.asyncio
async def test_stream_interface_traffic_reseeds_interval_after_malformed_json_event(
    make_client: MakeClientFactory,
) -> None:
    """Malformed JSON frame reseeds timing so the next delta uses poll_interval.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    stream_path = f"{DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX}/10"
    client, _router = make_router_client(
        make_client,
        {
            ("GET", stream_path): FakeResponse(
                stream_chunks=[
                    _sse(1710000010, 1000, 2000),
                    b"data: not-json\n\n",
                    _sse(1710000013, 2000, 3000),
                ]
            )
        },
    )

    samples = [sample async for sample in client.stream_interface_traffic(poll_interval=10)]

    assert len(samples) == 1
    assert samples[0]["interfaces"]["wan"]["interval"] == 10.0


@pytest.mark.asyncio
async def test_stream_interface_traffic_reseeds_interval_after_non_mapping_json_event(
    make_client: MakeClientFactory,
) -> None:
    """Non-mapping JSON frames reseed timing like malformed JSON.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    stream_path = f"{DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX}/10"
    client, _router = make_router_client(
        make_client,
        {
            ("GET", stream_path): FakeResponse(
                stream_chunks=[
                    _sse(1710000010, 1000, 2000),
                    b"data: [1, 2, 3]\n\n",
                    _sse(1710000013, 2000, 3000),
                ]
            )
        },
    )

    samples = [sample async for sample in client.stream_interface_traffic(poll_interval=10)]

    assert len(samples) == 1
    assert samples[0]["time"] == 1710000013
    assert samples[0]["interfaces"]["wan"]["interval"] == 10.0


@pytest.mark.asyncio
async def test_stream_interface_traffic_resets_interval_after_stream_json_reset(
    make_client: MakeClientFactory,
) -> None:
    """A dropped invalid UTF-8 frame forces the fallback interval on the next sample.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, _router = make_router_client(
        make_client,
        {
            ("GET", STREAM_PATH): FakeResponse(
                stream_chunks=[
                    _sse(1710000005, 0, 0),
                    _sse(1710000006, 1000, 2000),
                    b"\xff\xfe\xfd",
                    _sse(1710000008, 3000, 5000),
                ]
            )
        },
    )

    samples = [sample async for sample in client.stream_interface_traffic(poll_interval=1)]

    assert len(samples) == 2
    assert samples[0]["time"] == 1710000006
    assert samples[0]["interfaces"]["wan"]["interval"] == 1.0
    assert samples[1]["time"] == 1710000008
    assert samples[1]["interfaces"]["wan"]["interval"] == 1.0


@pytest.mark.asyncio
async def test_stream_interface_traffic_returns_when_endpoint_unavailable(
    make_client: MakeClientFactory,
) -> None:
    """A 404 stream endpoint ends the stream without reading it.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = make_router_client(make_client, {("GET", STREAM_PATH): 404})

    samples = [sample async for sample in client.stream_interface_traffic(poll_interval=1)]

    assert samples == []
    assert router.count("GET", STREAM_PATH) == 1  # the availability check only


@pytest.mark.parametrize(
    "probe_error",
    [
        pytest.param(TimeoutError("probe timeout"), id="timeout"),
        pytest.param(OPNsenseError("probe failed"), id="opnsense-error"),
    ],
)
@pytest.mark.asyncio
async def test_stream_interface_traffic_returns_empty_iteration_on_probe_error(
    make_client: MakeClientFactory,
    probe_error: Exception,
) -> None:
    """Probe errors end the stream empty when throw_errors is disabled.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        probe_error (Exception): Error raised by the Router session for the availability check.
    """
    client, router = make_router_client(make_client, {("GET", STREAM_PATH): probe_error})

    samples = [sample async for sample in client.stream_interface_traffic(poll_interval=1)]

    assert samples == []
    assert router.count("GET", STREAM_PATH) == 1  # the failed check; the stream was never read


@pytest.mark.asyncio
async def test_stream_interface_traffic_raises_same_opnsense_error_when_throw_errors_enabled(
    make_client: MakeClientFactory,
) -> None:
    """An OPNsense error from the Router session is re-raised unchanged when throw_errors is on.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    probe_error = OPNsenseError("probe failed")
    client, _router = make_router_client(
        make_client, {("GET", STREAM_PATH): probe_error}, throw_errors=True
    )

    with pytest.raises(OPNsenseError) as exc:
        [sample async for sample in client.stream_interface_traffic(poll_interval=1)]
    assert exc.value is probe_error


@pytest.mark.asyncio
async def test_stream_interface_traffic_raises_when_probe_timeout_and_throw_errors_enabled(
    make_client: MakeClientFactory,
) -> None:
    """A stream availability timeout propagates as a public error when throw_errors is enabled.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, _router = make_router_client(
        make_client, {("GET", STREAM_PATH): TimeoutError("probe timeout")}, throw_errors=True
    )

    with pytest.raises(OPNsenseTimeoutError):
        [sample async for sample in client.stream_interface_traffic(poll_interval=1)]


# The tests below pin how `_attempt` handles a failing traffic step: its debug log level and
# which errors it lets through. They inject the failure at the private request methods on
# purpose. Through the real request path, a timeout is logged at ERROR (snapshot) or WARNING
# (stream check) by the transport, and every other exception in the queued snapshot read is
# logged at ERROR by the queue worker, which then maps it to an OPNsenseError. So neither the
# quiet-log assertion nor the unhandled-error propagation can be observed at the session.
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
@pytest.mark.asyncio
async def test_get_interface_traffic_failure_follows_throw_errors_contract(
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
    throw_errors: bool,
    make_error: Callable[[], Exception],
    expected_error: type[OPNsenseError],
) -> None:
    """A handled failure yields an empty sample, logged quietly, or a mapped error.

    Args:
        make_client (MakeClientFactory): Client factory.
        caplog (pytest.LogCaptureFixture): Log capture used to check the failure stays at debug.
        throw_errors (bool): Whether the client raises public errors.
        make_error (Callable[[], Exception]): Factory for a fresh injected error.
        expected_error (type[OPNsenseError]): Public error expected with throw on.
    """
    client = make_client(throw_errors=throw_errors)
    try:
        client._fetch_endpoint_outcome = AsyncMock(side_effect=make_error())

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
        client._check_endpoint_status = AsyncMock(side_effect=make_error())
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
        client._fetch_endpoint_outcome = AsyncMock(side_effect=error)
        client._check_endpoint_status = AsyncMock(side_effect=error)

        with pytest.raises(type(error)):
            if method == "sample":
                await client.get_interface_traffic()
            else:
                [s async for s in client.stream_interface_traffic(poll_interval=1)]
    finally:
        await client.async_close()
