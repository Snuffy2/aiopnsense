"""Diagnostics traffic methods for OPNsenseClient."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from ._typing import AiopnsenseClientProtocol
from .helpers import _LOGGER, _log_errors, try_to_float, try_to_int

DIAGNOSTICS_TRAFFIC_ENDPOINT = "/api/diagnostics/traffic"
DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX = "/api/diagnostics/traffic/stream"

_INTERFACE_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "rx_bytes": ("rx_bytes", "inbytes", "bytes received", "bytes_received"),
    "tx_bytes": ("tx_bytes", "outbytes", "bytes transmitted", "bytes_transmitted"),
    "rx_packets": ("rx_packets", "inpkts", "packets received", "packets_received"),
    "tx_packets": ("tx_packets", "outpkts", "packets transmitted", "packets_transmitted"),
    "rx_errors": ("rx_errors", "inerrs", "input errors", "input_errors"),
    "tx_errors": ("tx_errors", "outerrs", "output errors", "output_errors"),
    "collisions": ("collisions",),
}


def _coalesce_identity(
    value: Any,
    *,
    fallback: str,
    description: Any = None,
) -> str:
    """Return a usable interface identity value with safe fallbacks."""
    if isinstance(value, str):
        candidate = value.strip()
        if candidate:
            return value

    if isinstance(description, str):
        candidate = description.strip()
        if candidate:
            return description

    return fallback


def _first_int(row: Mapping[str, Any], aliases: tuple[str, ...]) -> int | None:
    """Return the first parseable integer from a row for the supplied aliases."""
    for alias in aliases:
        value = try_to_int(row.get(alias))
        if value is not None:
            return value
    return None


def _source_interfaces(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the interface mapping from supported OPNsense traffic payload shapes."""
    interfaces = payload.get("interfaces")
    if isinstance(interfaces, Mapping):
        return interfaces
    return {
        key: value for key, value in payload.items() if key != "time" and isinstance(value, Mapping)
    }


def normalize_traffic_payload(
    payload: Mapping[str, Any],
    *,
    interval: float,
) -> dict[str, Any]:
    """Normalize OPNsense diagnostics traffic payloads.

    Args:
        payload: Raw traffic payload from ``/api/diagnostics/traffic`` or a stream event.
        interval: Seconds represented by the traffic counters in the payload.

    Returns:
        dict[str, Any]: Normalized traffic sample with an ``interfaces`` mapping keyed by interface name.
    """
    sample_interval = interval if interval > 0 else 1.0
    sample_time = try_to_float(payload.get("time"))
    normalized: dict[str, Any] = {
        "time": sample_time,
        "interfaces": {},
    }

    for interface_name, row in _source_interfaces(payload).items():
        if not isinstance(interface_name, str) or not isinstance(row, Mapping):
            continue
        normalized_row: dict[str, Any] = {
            "interface": _coalesce_identity(row.get("interface"), fallback=interface_name),
            "name": _coalesce_identity(
                row.get("name"),
                fallback=interface_name,
                description=row.get("description"),
            ),
        }
        for normalized_name, aliases in _INTERFACE_FIELD_ALIASES.items():
            value = _first_int(row, aliases)
            if value is not None:
                normalized_row[normalized_name] = value

        rx_bytes = normalized_row.get("rx_bytes")
        tx_bytes = normalized_row.get("tx_bytes")
        rx_packets = normalized_row.get("rx_packets")
        tx_packets = normalized_row.get("tx_packets")
        if not any(
            isinstance(value, int) for value in (rx_bytes, tx_bytes, rx_packets, tx_packets)
        ):
            continue

        normalized_row["interval"] = sample_interval
        if isinstance(rx_bytes, int):
            normalized_row["rx_bytes_per_second"] = rx_bytes / sample_interval
            normalized_row["rx_bits_per_second"] = rx_bytes * 8 / sample_interval
        if isinstance(tx_bytes, int):
            normalized_row["tx_bytes_per_second"] = tx_bytes / sample_interval
            normalized_row["tx_bits_per_second"] = tx_bytes * 8 / sample_interval
        if isinstance(rx_packets, int):
            normalized_row["rx_packets_per_second"] = rx_packets / sample_interval
        if isinstance(tx_packets, int):
            normalized_row["tx_packets_per_second"] = tx_packets / sample_interval
        normalized["interfaces"][interface_name] = normalized_row

    return normalized


class TrafficMixin(AiopnsenseClientProtocol):
    """Diagnostics traffic methods for OPNsenseClient."""

    @_log_errors
    async def get_interface_traffic(self) -> dict[str, Any]:
        """Return a normalized diagnostics traffic snapshot.

        Returns:
            dict[str, Any]: Normalized diagnostics traffic sample. Returns an
                empty traffic sample when endpoint probing or response parsing
                fails.
        """
        if not await self._is_get_endpoint_available(DIAGNOSTICS_TRAFFIC_ENDPOINT):
            _LOGGER.debug("Diagnostics traffic endpoint unavailable")
            return {"time": None, "interfaces": {}}
        payload = await self._safe_dict_get(DIAGNOSTICS_TRAFFIC_ENDPOINT)
        return normalize_traffic_payload(payload, interval=1.0)

    async def stream_interface_traffic(
        self,
        poll_interval: int = 1,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield normalized diagnostics traffic stream samples.

        Args:
            poll_interval: OPNsense stream sample interval in seconds. Values
                less than 1 are clamped to 1.

        Yields:
            Normalized traffic samples. The first stream event is discarded because
            OPNsense stream endpoints commonly emit an initialization sample
            before interval deltas stabilize.
        """
        interval = max(poll_interval, 1)
        endpoint = f"{DIAGNOSTICS_TRAFFIC_STREAM_ENDPOINT_PREFIX}/{interval}"
        if not await self._is_get_endpoint_available(endpoint):
            _LOGGER.debug("Diagnostics traffic stream endpoint unavailable")
            return

        event_count = 0
        previous_time: float | None = None
        async for event in self._stream_json_events(endpoint):
            event_count += 1
            event_time = try_to_float(event.get("time"))
            sample_interval = float(interval)
            if previous_time is not None and event_time is not None and event_time > previous_time:
                sample_interval = event_time - previous_time
            previous_time = event_time

            if event_count == 1:
                continue
            yield normalize_traffic_payload(event, interval=sample_interval)
