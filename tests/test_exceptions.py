"""Tests for `aiopnsense.exceptions`."""

import pytest

import aiopnsense as aiopnsense_module


def test_voucher_server_error() -> None:
    """Raise OPNsenseVoucherServerError to ensure the exception class exists."""
    with pytest.raises(aiopnsense_module.OPNsenseVoucherServerError):
        raise aiopnsense_module.OPNsenseVoucherServerError


def test_missing_device_unique_id_error() -> None:
    """Raise OPNsenseMissingDeviceUniqueID to ensure the exception class exists."""
    with pytest.raises(aiopnsense_module.OPNsenseMissingDeviceUniqueID):
        raise aiopnsense_module.OPNsenseMissingDeviceUniqueID
