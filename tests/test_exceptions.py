"""Tests for `aiopnsense.exceptions`."""

import pytest

import aiopnsense as pyopnsense


def test_voucher_server_error() -> None:
    """Raise VoucherServerError to ensure the exception class exists."""
    with pytest.raises(pyopnsense.VoucherServerError):
        raise pyopnsense.VoucherServerError
