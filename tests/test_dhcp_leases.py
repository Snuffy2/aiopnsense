"""Tests for `aiopnsense._dhcp_leases` row normalization."""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from aiopnsense._dhcp_leases import (
    DNSMASQ_LEASE_BACKEND,
    ISC_LEASE_BACKEND,
    copy_identity_fields,
    is_reserved_lease,
    normalize_leases,
)

ROUTER_TZ = timezone(timedelta(hours=-5))
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=ROUTER_TZ)


def _isc_row(**overrides: object) -> dict[str, Any]:
    """Build an active ISC row with a MAC, replacing or adding the given fields.

    Args:
        **overrides (object): Row fields to set on top of the defaults.

    Returns:
        dict[str, Any]: Raw ISC lease row.
    """
    return {
        "state": "active",
        "mac": "aa:bb:cc:dd:ee:01",
        "address": "192.0.2.1",
        "if": "lan",
        "if_descr": "LAN",
        "type": "dynamic",
    } | overrides


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        pytest.param(
            _isc_row(hostname="host", ends="2026/01/01 13:00:00", client_id="01:aa"),
            {
                "address": "192.0.2.1",
                "hostname": "host",
                "if_descr": "LAN",
                "if_name": "lan",
                "type": "dynamic",
                "mac": "aa:bb:cc:dd:ee:01",
                "expires": datetime(2026, 1, 1, 13, 0, tzinfo=ROUTER_TZ),
            },
            id="future-ends-localized-to-router-timezone",
        ),
        pytest.param(
            _isc_row(hostname="*.", type="static"),
            {
                "address": "192.0.2.1",
                "hostname": "*.",
                "if_descr": "LAN",
                "if_name": "lan",
                "type": "static",
                "mac": "aa:bb:cc:dd:ee:01",
                "expires": None,
            },
            id="hostname-and-type-pass-through-without-ends",
        ),
        pytest.param(
            _isc_row(hostname=""),
            {
                "address": "192.0.2.1",
                "hostname": None,
                "if_descr": "LAN",
                "if_name": "lan",
                "type": "dynamic",
                "mac": "aa:bb:cc:dd:ee:01",
                "expires": None,
            },
            id="empty-hostname-becomes-none",
        ),
        pytest.param(
            _isc_row(**{"if": ""}),
            {
                "address": "192.0.2.1",
                "hostname": None,
                "if_descr": "LAN",
                "if_name": "",
                "type": "dynamic",
                "mac": "aa:bb:cc:dd:ee:01",
                "expires": None,
            },
            id="empty-interface-reported-as-is",
        ),
        pytest.param(_isc_row(state="expired"), None, id="non-active-state"),
        pytest.param(_isc_row(state=None), None, id="missing-state"),
        pytest.param(_isc_row(mac=""), None, id="empty-mac"),
        pytest.param(_isc_row(ends="never"), None, id="unparseable-ends"),
        pytest.param(_isc_row(ends=12345), None, id="non-string-ends"),
        pytest.param(_isc_row(ends="2026/01/01 11:00:00"), None, id="past-ends"),
        pytest.param("not-a-row", None, id="non-mapping-row"),
    ],
)
def test_normalize_isc_leases(row: object, expected: dict[str, Any] | None) -> None:
    """ISC rows become leases, or are omitted, according to the ISC rules.

    Args:
        row (object): Raw row handed to the normalizer.
        expected (dict[str, Any] | None): Expected lease, or None when the row is omitted.
    """
    leases = normalize_leases([row], ISC_LEASE_BACKEND, now=NOW, router_tz=ROUTER_TZ)

    assert leases == ([] if expected is None else [expected])


def test_normalize_isc_leases_keeps_row_order_and_does_not_mutate_rows() -> None:
    """Leases follow row order, and the source rows are left untouched."""
    rows = [_isc_row(address="192.0.2.2"), _isc_row(state="expired"), _isc_row(address="192.0.2.3")]
    snapshot = [dict(row) for row in rows]

    leases = normalize_leases(rows, ISC_LEASE_BACKEND, now=NOW, router_tz=ROUTER_TZ)

    assert [lease["address"] for lease in leases] == ["192.0.2.2", "192.0.2.3"]
    assert rows == snapshot


def _dnsmasq_row(**overrides: object) -> dict[str, Any]:
    """Build a dnsmasq row, replacing or adding the given fields.

    Args:
        **overrides (object): Row fields to set on top of the defaults.

    Returns:
        dict[str, Any]: Raw dnsmasq lease row.
    """
    return {
        "address": "192.0.2.1",
        "hwaddr": "aa:bb:cc:dd:ee:01",
        "if_name": "lan",
        "if_descr": "LAN",
        "is_reserved": "0",
    } | overrides


def _dnsmasq_lease(**overrides: object) -> dict[str, Any]:
    """Build the lease expected for the default dnsmasq row, with overrides applied.

    Args:
        **overrides (object): Lease fields to set on top of the defaults.

    Returns:
        dict[str, Any]: Expected normalized lease.
    """
    return {
        "address": "192.0.2.1",
        "hostname": None,
        "if_descr": "LAN",
        "if_name": "lan",
        "type": "dynamic",
        "mac": "aa:bb:cc:dd:ee:01",
        "expires": None,
    } | overrides


FUTURE = NOW + timedelta(hours=1)
PAST = NOW - timedelta(hours=1)
OVERFLOW_EPOCH = 10**40


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        pytest.param(
            _dnsmasq_row(hostname="host."), _dnsmasq_lease(hostname="host."), id="dots-kept"
        ),
        pytest.param(
            _dnsmasq_row(hostname="*"), _dnsmasq_lease(), id="star-hostname-is-placeholder"
        ),
        pytest.param(_dnsmasq_row(hostname=""), _dnsmasq_lease(), id="empty-hostname"),
        pytest.param(
            _dnsmasq_row(if_name="", **{"if": "em0"}),
            _dnsmasq_lease(if_name="em0"),
            id="empty-if-name-falls-back-to-if",
        ),
        pytest.param(
            _dnsmasq_row(if_name="lan", **{"if": "em0"}), _dnsmasq_lease(), id="if-name-wins"
        ),
        pytest.param(_dnsmasq_row(hwaddr=""), _dnsmasq_lease(mac=None), id="empty-mac"),
        pytest.param(
            _dnsmasq_row(is_reserved="1", client_id="01:aa"),
            _dnsmasq_lease(type="static", client_id="01:aa"),
            id="reserved-flag-and-client-id",
        ),
        pytest.param(
            _dnsmasq_row(is_reserved=["host1"]),
            _dnsmasq_lease(type="static", reserved_by=["host1"]),
            id="reserved-list",
        ),
        pytest.param(_dnsmasq_row(is_reserved=[]), _dnsmasq_lease(), id="empty-reserved-list"),
        pytest.param(
            _dnsmasq_row(expire=int(FUTURE.timestamp())),
            _dnsmasq_lease(expires=FUTURE),
            id="future-epoch",
        ),
        pytest.param(_dnsmasq_row(expire=int(PAST.timestamp())), None, id="past-epoch"),
        pytest.param(
            _dnsmasq_row(expire=OVERFLOW_EPOCH),
            _dnsmasq_lease(expires=OVERFLOW_EPOCH),
            id="overflowing-epoch-keeps-raw-value",
        ),
        pytest.param(
            _dnsmasq_row(expire="never"), _dnsmasq_lease(expires="never"), id="non-integer-expire"
        ),
        pytest.param(_dnsmasq_row(expire=0), _dnsmasq_lease(expires=0), id="zero-expire"),
    ],
)
def test_normalize_dnsmasq_leases(row: dict[str, Any], expected: dict[str, Any] | None) -> None:
    """Dnsmasq rows become leases, or are omitted, according to the dnsmasq rules.

    Args:
        row (dict[str, Any]): Raw row handed to the normalizer.
        expected (dict[str, Any] | None): Expected lease, or None when the row is omitted.
    """
    leases = normalize_leases([row], DNSMASQ_LEASE_BACKEND, now=NOW)

    assert leases == ([] if expected is None else [expected])


@pytest.mark.parametrize(
    ("raw_reserved", "expected"),
    [
        ("1", True),
        ("0", False),
        (["hwaddr"], True),
        ([], False),
        (1, True),
        (0, False),
        (None, False),
    ],
)
def test_is_reserved_lease_handles_legacy_and_list_flags(
    raw_reserved: object, expected: bool
) -> None:
    """Reserved lease detection supports legacy and list value shapes.

    Args:
        raw_reserved (object): Raw ``is_reserved`` value from an API payload.
        expected (bool): Expected reserved/static detection result.
    """
    assert is_reserved_lease(raw_reserved) is expected


def test_copy_identity_fields_sets_reserved_by_only_for_non_empty_list() -> None:
    """``reserved_by`` is copied only for non-empty reservation lists."""
    lease: dict[str, Any] = {}

    copy_identity_fields(lease, {"is_reserved": []})
    assert "reserved_by" not in lease

    copy_identity_fields(lease, {"is_reserved": ["host1"]})
    assert lease["reserved_by"] == ["host1"]
