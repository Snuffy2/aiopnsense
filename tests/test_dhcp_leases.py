"""Tests for `aiopnsense._dhcp_leases` row normalization."""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from aiopnsense._dhcp_leases import (
    _DNSMASQ_LEASE_BACKEND,
    _ISC_LEASE_BACKEND,
    _is_live_row,
    _kea_lease_backend,
    _normalize_leases,
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
    leases = _normalize_leases([row], _ISC_LEASE_BACKEND, now=NOW, router_tz=ROUTER_TZ)

    assert leases == ([] if expected is None else [expected])


def test_normalize_isc_leases_keeps_row_order_and_does_not_mutate_rows() -> None:
    """Leases follow row order, and the source rows are left untouched."""
    rows = [_isc_row(address="192.0.2.2"), _isc_row(state="expired"), _isc_row(address="192.0.2.3")]
    snapshot = [dict(row) for row in rows]

    leases = _normalize_leases(rows, _ISC_LEASE_BACKEND, now=NOW, router_tz=ROUTER_TZ)

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


def _expected_lease(**overrides: object) -> dict[str, Any]:
    """Build the expected normalized lease defaults shared by Kea and dnsmasq, with overrides applied.

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
            _dnsmasq_row(hostname="host."), _expected_lease(hostname="host."), id="dots-kept"
        ),
        pytest.param(
            _dnsmasq_row(hostname="*"), _expected_lease(), id="star-hostname-is-placeholder"
        ),
        pytest.param(_dnsmasq_row(hostname=""), _expected_lease(), id="empty-hostname"),
        pytest.param(
            _dnsmasq_row(if_name="", **{"if": "em0"}),
            _expected_lease(if_name="em0"),
            id="empty-if-name-falls-back-to-if",
        ),
        pytest.param(
            _dnsmasq_row(if_name="lan", **{"if": "em0"}), _expected_lease(), id="if-name-wins"
        ),
        pytest.param(_dnsmasq_row(hwaddr=""), _expected_lease(mac=None), id="empty-mac"),
        pytest.param(
            _dnsmasq_row(is_reserved="1", client_id="01:aa"),
            _expected_lease(type="static", client_id="01:aa"),
            id="reserved-flag-and-client-id",
        ),
        pytest.param(
            _dnsmasq_row(is_reserved=["host1"]),
            _expected_lease(type="static", reserved_by=["host1"]),
            id="reserved-list",
        ),
        pytest.param(_dnsmasq_row(is_reserved=[]), _expected_lease(), id="empty-reserved-list"),
        pytest.param(
            _dnsmasq_row(is_reserved=1), _expected_lease(type="static"), id="reserved-integer"
        ),
        pytest.param(_dnsmasq_row(is_reserved=0), _expected_lease(), id="unreserved-integer"),
        pytest.param(_dnsmasq_row(is_reserved=None), _expected_lease(), id="missing-reserved-flag"),
        pytest.param(
            _dnsmasq_row(expire=int(FUTURE.timestamp())),
            _expected_lease(expires=FUTURE),
            id="future-epoch",
        ),
        pytest.param(_dnsmasq_row(expire=int(PAST.timestamp())), None, id="past-epoch"),
        pytest.param(
            _dnsmasq_row(expire=OVERFLOW_EPOCH),
            _expected_lease(expires=OVERFLOW_EPOCH),
            id="overflowing-epoch-keeps-raw-value",
        ),
        pytest.param(
            _dnsmasq_row(expire="never"), _expected_lease(expires="never"), id="non-integer-expire"
        ),
        pytest.param(_dnsmasq_row(expire=0), _expected_lease(expires=0), id="zero-expire"),
    ],
)
def test_normalize_dnsmasq_leases(row: dict[str, Any], expected: dict[str, Any] | None) -> None:
    """Dnsmasq rows become leases, or are omitted, according to the dnsmasq rules.

    Args:
        row (dict[str, Any]): Raw row handed to the normalizer.
        expected (dict[str, Any] | None): Expected lease, or None when the row is omitted.
    """
    leases = _normalize_leases([row], _DNSMASQ_LEASE_BACKEND, now=NOW)

    assert leases == ([] if expected is None else [expected])


RESERVATIONS = {"aa:bb:cc:dd:ee:01": "192.0.2.1"}


def _kea_row(**overrides: object) -> dict[str, Any]:
    """Build an active Kea DHCPv4 row, replacing or adding the given fields.

    Args:
        **overrides (object): Row fields to set on top of the defaults.

    Returns:
        dict[str, Any]: Raw Kea lease row.
    """
    return {
        "address": "192.0.2.1",
        "hwaddr": "aa:bb:cc:dd:ee:01",
        "if_name": "lan",
        "if_descr": "LAN",
        "state": "0",
        "is_reserved": "0",
    } | overrides


@pytest.mark.parametrize(
    ("row", "require_hardware_address", "reservations", "expected"),
    [
        pytest.param(_kea_row(), True, None, _expected_lease(), id="active-string-state"),
        pytest.param(_kea_row(state=0), True, None, _expected_lease(), id="active-integer-state"),
        pytest.param(_kea_row(state="1"), True, None, None, id="non-active-state"),
        pytest.param(_kea_row(state=None), True, None, None, id="missing-state"),
        pytest.param(_kea_row(hwaddr=""), True, None, None, id="v4-requires-mac"),
        pytest.param(
            _kea_row(hwaddr=None, duid="00:01"),
            False,
            None,
            _expected_lease(mac=None, duid="00:01"),
            id="v6-allows-missing-mac-and-copies-duid",
        ),
        pytest.param(
            _kea_row(hostname="."),
            True,
            None,
            _expected_lease(),
            id="dot-only-hostname-becomes-none",
        ),
        pytest.param(
            _kea_row(hostname="host.example."),
            True,
            None,
            _expected_lease(hostname="host.example"),
            id="surrounding-dots-stripped",
        ),
        pytest.param(
            _kea_row(hostname="*"),
            True,
            None,
            _expected_lease(hostname="*"),
            id="star-hostname-kept",
        ),
        pytest.param(
            _kea_row(is_reserved="1", client_id="01:aa"),
            True,
            None,
            _expected_lease(type="static", client_id="01:aa"),
            id="authoritative-reserved-flag",
        ),
        pytest.param(
            _kea_row(is_reserved=["res-1"]),
            True,
            None,
            _expected_lease(type="static", reserved_by=["res-1"]),
            id="authoritative-reserved-list",
        ),
        pytest.param(
            _kea_row(is_reserved=[]), True, None, _expected_lease(), id="authoritative-empty-list"
        ),
        pytest.param(
            _kea_row(is_reserved=0),
            True,
            RESERVATIONS,
            _expected_lease(),
            id="integer-zero-overrides-matching-reservation",
        ),
        pytest.param(
            _kea_row(is_reserved=1),
            True,
            None,
            _expected_lease(type="static"),
            id="integer-one-classifies-as-static",
        ),
        pytest.param(
            _kea_row(is_reserved=1.0),
            True,
            None,
            _expected_lease(type="unknown"),
            id="float-is-not-an-authoritative-flag",
        ),
        pytest.param(
            _kea_row(is_reserved=None),
            True,
            None,
            _expected_lease(type="unknown"),
            id="no-metadata-no-reservation-data-is-unknown",
        ),
        pytest.param(
            _kea_row(is_reserved=None),
            True,
            RESERVATIONS,
            _expected_lease(type="static"),
            id="reservation-matches-mac-and-address",
        ),
        pytest.param(
            _kea_row(is_reserved=None, address="192.0.2.9"),
            True,
            RESERVATIONS,
            _expected_lease(address="192.0.2.9", type="dynamic"),
            id="reservation-for-another-address",
        ),
        pytest.param(
            _kea_row(is_reserved=None), True, {}, _expected_lease(), id="empty-reservation-data"
        ),
        pytest.param(
            _kea_row(is_reserved="bogus"),
            True,
            RESERVATIONS,
            _expected_lease(type="static"),
            id="unsupported-metadata-falls-back-to-reservations",
        ),
        pytest.param(
            _kea_row(expire=int(FUTURE.timestamp())),
            True,
            None,
            _expected_lease(expires=FUTURE),
            id="future-epoch",
        ),
        pytest.param(_kea_row(expire=int(PAST.timestamp())), True, None, None, id="past-epoch"),
        pytest.param(
            _kea_row(expire=OVERFLOW_EPOCH),
            True,
            None,
            _expected_lease(expires=OVERFLOW_EPOCH),
            id="overflowing-epoch-keeps-raw-value",
        ),
    ],
)
def test_normalize_kea_leases(
    row: dict[str, Any],
    require_hardware_address: bool,
    reservations: dict[str, str] | None,
    expected: dict[str, Any] | None,
) -> None:
    """Kea rows become leases, or are omitted, according to the Kea rules.

    Args:
        row (dict[str, Any]): Raw row handed to the normalizer.
        require_hardware_address (bool): Whether the backend is DHCPv4 and requires a MAC.
        reservations (dict[str, str] | None): Reservation map, or None when unavailable.
        expected (dict[str, Any] | None): Expected lease, or None when the row is omitted.
    """
    backend = _kea_lease_backend(
        require_hardware_address=require_hardware_address, reservations=reservations
    )

    leases = _normalize_leases([row], backend, now=NOW)

    assert leases == ([] if expected is None else [expected])
    assert _is_live_row(row, backend, now=NOW) is (expected is not None)
