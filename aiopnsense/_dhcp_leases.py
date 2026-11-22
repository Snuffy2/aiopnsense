"""Shared normalization of DHCP server rows into DHCP Leases.

Each DHCP backend (ISC DHCP, dnsmasq and Kea) describes how its rows differ in a
``LeaseBackend``. ``normalize_leases`` applies that description to rows that were already
fetched and checked to be a list, so the row filtering, hostname cleanup, expiry conversion and
expired-row removal live in one place. Fetching, endpoint selection and timezone lookup stay
with the callers.
"""

from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any

from .helpers import api_value_matches, timestamp_to_datetime, try_to_int

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
        copy_identity_fields (bool): Whether to copy ``client_id`` and ``reserved_by``.
        copy_duid (bool): Whether to copy the DHCPv6 ``duid`` when the row has one.
        strip_hostname_dots (bool): Whether to strip leading and trailing dots from hostnames.
            A hostname made only of dots therefore becomes an empty string, not None.
        placeholder_hostnames (frozenset[str]): Hostnames the backend uses as a placeholder for
            "no hostname", reported as None.
    """

    accepts: RowPredicate
    if_name_keys: tuple[str, ...]
    mac_key: str
    lease_type: TypeReader
    read_expiry: ExpiryReader
    copy_identity_fields: bool = False
    copy_duid: bool = False
    strip_hostname_dots: bool = False
    placeholder_hostnames: frozenset[str] = frozenset()


def _clean_hostname(value: object, backend: LeaseBackend) -> str | None:
    """Return a row hostname, or None when it is missing or a placeholder.

    Args:
        value (object): Raw ``hostname`` value from a lease row.
        backend (LeaseBackend): Backend whose hostname rules apply.

    Returns:
        str | None: The hostname when it is a non-empty string other than a placeholder, with
            dots stripped if the backend requires it; otherwise None.
    """
    if not isinstance(value, str) or not value or value in backend.placeholder_hostnames:
        return None
    return value.strip(".") if backend.strip_hostname_dots else value


def _clean_mac(value: object) -> str | None:
    """Return a row MAC address, or None when it is not a non-empty string.

    Args:
        value (object): Raw MAC value from a lease row.

    Returns:
        str | None: The MAC address when it is a non-empty string; otherwise None.
    """
    return value if isinstance(value, str) and value else None


def is_reserved_lease(raw_reserved: object) -> bool:
    """Return whether an ``is_reserved`` row value marks a reserved (static) lease.

    Args:
        raw_reserved (object): Raw ``is_reserved`` field returned by a DHCP lease API.

    Returns:
        bool: ``True`` for reserved/static lease rows, ``False`` otherwise.
    """
    if isinstance(raw_reserved, str):
        return api_value_matches(raw_reserved, "1")
    if isinstance(raw_reserved, list):
        return len(raw_reserved) > 0
    return bool(raw_reserved)


def copy_identity_fields(lease: dict[str, Any], row: Mapping[str, Any]) -> None:
    """Copy optional DHCP client identity metadata to a normalized lease.

    Args:
        lease (dict[str, Any]): Normalized lease being built; updated in place.
        row (Mapping[str, Any]): Raw lease row returned by OPNsense.
    """
    if row.get("client_id") not in (None, ""):
        lease["client_id"] = row.get("client_id")
    raw_reserved = row.get("is_reserved")
    if isinstance(raw_reserved, list) and raw_reserved:
        lease["reserved_by"] = list(raw_reserved)


def epoch_to_datetime(expiration: int | None) -> datetime | None:
    """Convert a DHCP expiration timestamp without propagating platform overflow.

    Args:
        expiration (int | None): Parsed DHCP expiration timestamp in epoch seconds.

    Returns:
        datetime | None: Converted expiration, or ``None`` when the timestamp is outside the
            platform-supported range.
    """
    try:
        return timestamp_to_datetime(expiration)
    except OverflowError, OSError:
        return None


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
        lease: dict[str, Any] = {
            "address": row.get("address"),
            "hostname": _clean_hostname(row.get("hostname"), backend),
            "if_descr": row.get("if_descr"),
            "if_name": _read_if_name(row, backend.if_name_keys),
            "type": backend.lease_type(row),
            "mac": _clean_mac(row.get(backend.mac_key)),
        }
        if backend.copy_duid and "duid" in row:
            lease["duid"] = row["duid"]
        if backend.copy_identity_fields:
            copy_identity_fields(lease, row)
        lease["expires"] = expires
        leases.append(lease)
    return leases


def is_live_row(
    row: object,
    backend: LeaseBackend,
    *,
    now: datetime,
    router_tz: tzinfo | None = None,
) -> bool:
    """Return whether ``normalize_leases`` would report a lease for a row.

    Args:
        row (object): Raw row returned by one DHCP backend.
        backend (LeaseBackend): Description of how the backend's rows differ.
        now (datetime): Timezone-aware reference time used to drop expired leases.
        router_tz (tzinfo | None, optional): Router timezone for backends that report local
            timestamps.

    Returns:
        bool: True when the row is accepted by the backend and has not expired.
    """
    return (
        isinstance(row, MutableMapping)
        and backend.accepts(row)
        and backend.read_expiry(row, now, router_tz)[0]
    )


def read_epoch_expiry(
    row: Mapping[str, Any], now: datetime, router_tz: tzinfo | None
) -> tuple[bool, object]:
    """Read an ``expire`` value expressed in epoch seconds.

    Args:
        row (Mapping[str, Any]): Raw lease row.
        now (datetime): Timezone-aware reference time.
        router_tz (tzinfo | None): Unused; epoch values do not depend on the Router timezone.

    Returns:
        tuple[bool, object]: Whether the lease is still live and the value to report as
            ``expires``. A missing, falsy or non-integer ``expire`` is live with the raw value,
            as is a timestamp outside the platform datetime range; a past timestamp is not live.
    """
    raw_expire = row.get("expire")
    expiration = try_to_int(raw_expire)
    if not expiration:
        return True, raw_expire
    expiration_datetime = epoch_to_datetime(expiration)
    if expiration_datetime is None:
        return True, raw_expire
    return expiration_datetime >= now, expiration_datetime


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


def _accepts_any_row(row: Mapping[str, Any]) -> bool:
    """Accept every mapping row.

    Args:
        row (Mapping[str, Any]): Raw lease row.

    Returns:
        bool: Always True.
    """
    return True


def _read_dnsmasq_type(row: Mapping[str, Any]) -> object:
    """Return ``static`` for reserved dnsmasq rows and ``dynamic`` otherwise.

    Args:
        row (Mapping[str, Any]): Raw dnsmasq lease row.

    Returns:
        object: The lease type derived from ``is_reserved``.
    """
    return "static" if is_reserved_lease(row.get("is_reserved")) else "dynamic"


def has_authoritative_reservation_metadata(raw_reserved: object) -> bool:
    """Return whether a Kea ``is_reserved`` value has a supported representation.

    Args:
        raw_reserved (object): Raw ``is_reserved`` field returned by Kea.

    Returns:
        bool: Whether the value can authoritatively classify the lease.
    """
    return isinstance(raw_reserved, list) or (
        type(raw_reserved) in (str, int, bool)
        and (api_value_matches(raw_reserved, "0") or api_value_matches(raw_reserved, "1"))
    )


def kea_lease_backend(
    *, require_hardware_address: bool, reservations: Mapping[str, object] | None
) -> LeaseBackend:
    """Describe Kea DHCPv4 or DHCPv6 rows.

    Kea rows are leases when ``state`` matches ``0``, and DHCPv4 rows also need a ``hwaddr``.
    The lease type comes from authoritative ``is_reserved`` metadata first, then from the
    reservation map, and is ``unknown`` when no reservation data is available.

    Args:
        require_hardware_address (bool): Whether rows without a ``hwaddr`` are omitted.
        reservations (Mapping[str, object] | None): Reserved address by MAC address, or None
            when reservation data is unavailable.

    Returns:
        LeaseBackend: Description of Kea rows.
    """

    def accepts(row: Mapping[str, Any]) -> bool:
        """Return whether a Kea row is an active lease that should be reported.

        Args:
            row (Mapping[str, Any]): Raw Kea lease row.

        Returns:
            bool: Whether ``state`` matches ``0`` and a ``hwaddr`` is present when required.
        """
        return api_value_matches(row.get("state"), "0") and (
            not require_hardware_address or bool(row.get("hwaddr"))
        )

    def lease_type(row: Mapping[str, Any]) -> object:
        """Return ``static``, ``dynamic`` or ``unknown`` for a Kea row.

        Args:
            row (Mapping[str, Any]): Raw Kea lease row.

        Returns:
            object: The lease type derived from the row and the reservation map.
        """
        raw_reserved = row.get("is_reserved")
        if has_authoritative_reservation_metadata(raw_reserved):
            return "static" if is_reserved_lease(raw_reserved) else "dynamic"
        if reservations is None:
            return "unknown"
        hwaddr = row.get("hwaddr")
        if hwaddr and hwaddr in reservations and reservations[hwaddr] == row.get("address"):
            return "static"
        return "dynamic"

    return LeaseBackend(
        accepts=accepts,
        if_name_keys=("if_name",),
        mac_key="hwaddr",
        lease_type=lease_type,
        read_expiry=read_epoch_expiry,
        copy_identity_fields=True,
        copy_duid=True,
        strip_hostname_dots=True,
    )


ISC_LEASE_BACKEND = LeaseBackend(
    accepts=_accepts_isc_row,
    if_name_keys=("if",),
    mac_key="mac",
    lease_type=_read_isc_type,
    read_expiry=_read_isc_expiry,
)
"""ISC DHCPv4 and DHCPv6 rows: active state, MAC required, type passed through, ``ends`` expiry."""

DNSMASQ_LEASE_BACKEND = LeaseBackend(
    accepts=_accepts_any_row,
    if_name_keys=("if_name", "if"),
    mac_key="hwaddr",
    lease_type=_read_dnsmasq_type,
    read_expiry=read_epoch_expiry,
    copy_identity_fields=True,
    placeholder_hostnames=frozenset({"*"}),
)
"""Dnsmasq rows: every row kept, ``*`` hostname is a placeholder, type from ``is_reserved``."""
