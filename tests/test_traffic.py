"""Tests for diagnostics traffic snapshot and stream helpers."""

from __future__ import annotations


from aiopnsense.traffic import (
    normalize_traffic_payload,
)


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
