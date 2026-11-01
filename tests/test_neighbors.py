"""Tests for neighbor row normalization."""

from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from aiopnsense.neighbors import normalize_neighbor_entry


@pytest.mark.parametrize(
    ("row", "mac", "valid", "ipv4", "ipv6"),
    [
        (
            {"mac": " AA-BB-CC-DD-EE-FF ", "ip": " 192.0.2.1 "},
            "aa:bb:cc:dd:ee:ff",
            "aa:bb:cc:dd:ee:ff",
            "192.0.2.1",
            None,
        ),
        (
            {"mac": " ", "mac-address": " AB-CD ", "ip": None, "ip-address": "fe80:0::1%em0"},
            "ab:cd",
            None,
            None,
            "fe80::1%em0",
        ),
        ({"mac": 5, "mac-address": [], "ip": [], "ip-address": 42}, None, None, None, None),
        ({"mac": "zz:zz:zz:zz:zz:zz", "ip": "bad"}, "zz:zz:zz:zz:zz:zz", None, None, None),
    ],
)
def test_normalization(
    row: dict[str, object], mac: str | None, valid: str | None, ipv4: str | None, ipv6: str | None
) -> None:
    """Normalize aliases, malformed values, and both address families.

    Args:
        row (dict[str, object]): Raw neighbor values.
        mac (str | None): Expected permissive MAC.
        valid (str | None): Expected strict MAC.
        ipv4 (str | None): Expected IPv4 address.
        ipv6 (str | None): Expected IPv6 address.
    """
    entry = normalize_neighbor_entry(row)
    assert (entry.mac, entry.valid_mac, entry.ipv4_address, entry.ipv6_address) == (
        mac,
        valid,
        ipv4,
        ipv6,
    )
    assert entry.usable


def test_metadata_is_copied_and_entry_is_frozen() -> None:
    """Preserve metadata, isolate nested source values, and freeze fields."""
    interface = {"name": ["lan"]}
    expires = [1]
    row = {
        "hostname": "??router??",
        "manufacturer": "Vendor",
        "expired": "0",
        "intf_description": interface,
        "intf": "fallback",
        "expires": expires,
        "type": {"kind": "dynamic"},
    }
    entry = normalize_neighbor_entry(row)
    interface["name"].append("changed")
    expires.append(2)
    assert entry.hostname == "router"
    assert entry.manufacturer == "Vendor"
    assert entry.expired is True
    assert entry.interface == {"name": ["lan"]}
    assert entry.expires == [1]
    assert entry.arp_type == {"kind": "dynamic"}
    with pytest.raises(FrozenInstanceError):
        entry.hostname = "other"  # type: ignore[misc]


def test_mapping_usability_and_missing_metadata() -> None:
    """Immutable mappings are normalized but not classified as usable."""
    entry = normalize_neighbor_entry(
        MappingProxyType({"hostname": "???", "manufacturer": 3, "intf": "wan"})
    )
    assert not entry.usable
    assert entry.hostname is None
    assert entry.manufacturer is None
    assert entry.interface == "wan"
    assert not entry.expired
