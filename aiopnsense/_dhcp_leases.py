"""Shared normalization of DHCP server rows into DHCP Leases.

Each DHCP backend (ISC DHCP, and later dnsmasq and Kea) describes how its rows differ in a
``LeaseBackend``. ``normalize_leases`` applies that description to rows that were already
fetched and checked to be a list, so the row filtering, hostname cleanup, expiry conversion and
expired-row removal live in one place. Fetching, endpoint selection and timezone lookup stay
with the callers.
"""

from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any

ISC_ENDS_FORMAT = "%Y/%m/%d %H:%M:%S"

type RowPredicate = Callable[[Mapping[str, Any]], bool]
type TypeReader = Callable[[Mapping[str, Any]], object]
type ExpiryReader = Callable[[Mapping[str, Any], datetime, tzinfo | None], tuple[bool, object]]


@dataclass(frozen=True, slots=True)
class LeaseBackend:
    """How one DHCP backend's rows differ when they become DHCP Leases.

    Attributes:
        accepts (RowPredicate): Whether a mapping row is a lease that should be reported.
        if_name_keys (tuple[str, ...]): Row keys holding the interface identifier. The first
            non-empty string wins; otherwise the last key's raw value is used.
        mac_key (str): Row key holding the MAC address.
        lease_type (TypeReader): Reads the lease ``type`` from a row.
        read_expiry (ExpiryReader): Reads a row's expiry. It receives the row, the reference
            time and the Router timezone, and returns whether the lease is still live and the
            value to report as ``expires``.
    """

    accepts: RowPredicate
    if_name_keys: tuple[str, ...]
    mac_key: str
    lease_type: TypeReader
    read_expiry: ExpiryReader


def _clean_hostname(value: object) -> str | None:
    """Return a row hostname, or None when it is missing or not a non-empty string.

    Args:
        value (object): Raw ``hostname`` value from a lease row.

    Returns:
        str | None: The hostname when it is a non-empty string; otherwise None.
    """
    return value if isinstance(value, str) and len(value) > 0 else None


def _read_if_name(row: Mapping[str, Any], keys: tuple[str, ...]) -> object:
    """Return the interface identifier of a row.

    Args:
        row (Mapping[str, Any]): Raw lease row.
        keys (tuple[str, ...]): Row keys to try, in order.

    Returns:
        object: The first non-empty string among the keys, or the last key's raw value.
    """
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return row.get(keys[-1])


def normalize_leases(
    rows: Iterable[object],
    backend: LeaseBackend,
    *,
    now: datetime,
    router_tz: tzinfo | None = None,
) -> list[dict[str, Any]]:
    """Convert raw DHCP rows into normalized DHCP Leases.

    Args:
        rows (Iterable[object]): Raw rows returned by one DHCP backend. Rows that are not
            mappings are skipped.
        backend (LeaseBackend): Description of how the backend's rows differ.
        now (datetime): Timezone-aware reference time used to drop expired leases.
        router_tz (tzinfo | None, optional): Router timezone for backends that report local
            timestamps.

    Returns:
        list[dict[str, Any]]: Leases in row order, each with ``address``, ``hostname``,
            ``if_descr``, ``if_name``, ``type``, ``mac`` and ``expires``. Rows the backend
            does not accept, expired rows and rows whose expiry cannot be read are omitted.
    """
    leases: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, MutableMapping) or not backend.accepts(row):
            continue
        live, expires = backend.read_expiry(row, now, router_tz)
        if not live:
            continue
        leases.append(
            {
                "address": row.get("address"),
                "hostname": _clean_hostname(row.get("hostname")),
                "if_descr": row.get("if_descr"),
                "if_name": _read_if_name(row, backend.if_name_keys),
                "type": backend.lease_type(row),
                "mac": row.get(backend.mac_key),
                "expires": expires,
            }
        )
    return leases


def _accepts_isc_row(row: Mapping[str, Any]) -> bool:
    """Return whether an ISC row is an active lease with a MAC address.

    Args:
        row (Mapping[str, Any]): Raw ISC lease row.

    Returns:
        bool: True when ``state`` is exactly ``active`` and ``mac`` is non-empty.
    """
    return row.get("state", "") == "active" and bool(row.get("mac"))


def _read_isc_type(row: Mapping[str, Any]) -> object:
    """Return the ISC lease type exactly as the row reports it.

    Args:
        row (Mapping[str, Any]): Raw ISC lease row.

    Returns:
        object: Raw ``type`` value, or None when absent.
    """
    return row.get("type")


def _read_isc_expiry(
    row: Mapping[str, Any], now: datetime, router_tz: tzinfo | None
) -> tuple[bool, object]:
    """Read an ISC ``ends`` timestamp, which is local to the Router.

    Args:
        row (Mapping[str, Any]): Raw ISC lease row.
        now (datetime): Timezone-aware reference time.
        router_tz (tzinfo | None): Router timezone the ``ends`` value is expressed in.

    Returns:
        tuple[bool, object]: Whether the lease is still live and the value to report as
            ``expires``. A row without ``ends`` is live with the raw value; an unparsable or
            past ``ends`` is not live.
    """
    ends = row.get("ends")
    if not ends:
        return True, ends
    try:
        expires = datetime.strptime(ends, ISC_ENDS_FORMAT).replace(tzinfo=router_tz)
    except TypeError, ValueError:
        return False, None
    return expires >= now, expires


ISC_LEASE_BACKEND = LeaseBackend(
    accepts=_accepts_isc_row,
    if_name_keys=("if",),
    mac_key="mac",
    lease_type=_read_isc_type,
    read_expiry=_read_isc_expiry,
)
"""ISC DHCPv4 and DHCPv6 rows: active state, MAC required, type passed through, ``ends`` expiry."""
