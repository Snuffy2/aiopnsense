"""Tests for `aiopnsense._dhcp_leases` row normalization."""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from aiopnsense._dhcp_leases import ISC_LEASE_BACKEND, normalize_leases

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
