"""Tests for `aiopnsense.exceptions`."""

import pytest

import aiopnsense as aiopnsense_module


def test_voucher_server_error() -> None:
    """Raise OPNsenseVoucherServerError to ensure the exception class exists."""
    with pytest.raises(aiopnsense_module.OPNsenseVoucherServerError):
        raise aiopnsense_module.OPNsenseVoucherServerError
