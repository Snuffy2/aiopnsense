"""Normalize raw OPNsense neighbor rows for consumers."""

from collections.abc import Mapping, MutableMapping
import copy
from dataclasses import dataclass
import ipaddress
import re

_MAC_PATTERN = re.compile(r"^([0-9a-f]{2}:){5}([0-9a-f]{2})$")


@dataclass(frozen=True, slots=True)
class NeighborEntry:
    """Normalized neighbor row, with copied metadata isolated from its source."""

    mac: str | None
    valid_mac: str | None
    ipv4_address: str | None
    ipv6_address: str | None
    hostname: str | None
    manufacturer: str | None
    expired: bool
    interface: object
    expires: object
    arp_type: object
    usable: bool


def _normalized_ip(value: object, *, version: int) -> str | None:
    """Return a canonical address if it belongs to the requested IP family.

    Args:
        value (object): Raw address.
        version (int): Expected IP family.

    Returns:
        str | None: Compressed address, or None if invalid or of the wrong family.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    return address.compressed if address.version == version else None


def normalize_neighbor_entry(row: Mapping[str, object]) -> NeighborEntry:
    """Copy and normalize one raw ARP or NDP neighbor row.

    Args:
        row (Mapping[str, object]): Mapping returned by the neighbor table API.

    Returns:
        NeighborEntry: A normalized entry. Only mutable mappings are classified as usable;
        metadata objects are deep-copied from the source row.
    """
    copied = copy.deepcopy(dict(row))
    raw_mac = copied.get("mac")
    if not isinstance(raw_mac, str) or not raw_mac.strip():
        raw_mac = copied.get("mac-address")
    mac = raw_mac.strip().lower().replace("-", ":") if isinstance(raw_mac, str) else ""
    valid_mac = mac if _MAC_PATTERN.match(mac) else None
    raw_ip = copied.get("ip")
    if not isinstance(raw_ip, str) or not raw_ip.strip():
        raw_ip = copied.get("ip-address")
    hostname = copied.get("hostname")
    if isinstance(hostname, str):
        hostname = hostname.strip("?") or None
    else:
        hostname = None
    manufacturer = copied.get("manufacturer")
    if not isinstance(manufacturer, str) or not manufacturer:
        manufacturer = None
    return NeighborEntry(
        mac=mac or None,
        valid_mac=valid_mac,
        ipv4_address=_normalized_ip(raw_ip, version=4),
        ipv6_address=_normalized_ip(raw_ip, version=6),
        hostname=hostname,
        manufacturer=manufacturer,
        expired=bool(copied.get("expired", False)),
        interface=copied.get("intf_description", copied.get("intf")),
        expires=copied.get("expires"),
        arp_type=copied.get("type"),
        usable=isinstance(row, MutableMapping),
    )
