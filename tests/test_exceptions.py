"""Tests for `aiopnsense.exceptions`."""

from unittest.mock import MagicMock

import aiohttp
import pytest

import aiopnsense as aiopnsense_module
from aiopnsense.exceptions import _map_opnsense_exception, _opnsense_http_error


def test_voucher_server_error() -> None:
    """Raise OPNsenseVoucherServerError to ensure the exception class exists."""
    with pytest.raises(aiopnsense_module.OPNsenseVoucherServerError):
        raise aiopnsense_module.OPNsenseVoucherServerError


def test_missing_device_unique_id_error() -> None:
    """Raise OPNsenseMissingDeviceUniqueID to ensure the exception class exists."""
    with pytest.raises(aiopnsense_module.OPNsenseMissingDeviceUniqueID):
        raise aiopnsense_module.OPNsenseMissingDeviceUniqueID


def test_invalid_argument_is_public_opnsense_type_error() -> None:
    """Expose invalid client arguments as both OPNsense and type errors."""
    error = aiopnsense_module.OPNsenseInvalidArgument("invalid")

    assert isinstance(error, aiopnsense_module.OPNsenseError)
    assert isinstance(error, TypeError)


@pytest.mark.parametrize(
    ("status", "expected_exception"),
    [
        (401, aiopnsense_module.OPNsenseInvalidAuth),
        (403, aiopnsense_module.OPNsensePrivilegeMissing),
        (500, aiopnsense_module.OPNsenseConnectionError),
    ],
)
def test_http_errors_map_to_public_exceptions(
    status: int,
    expected_exception: type[aiopnsense_module.OPNsenseConnectionError],
) -> None:
    """Map HTTP statuses to public exceptions while retaining the status.

    Args:
        status (int): HTTP status to map.
        expected_exception (type[OPNsenseConnectionError]): Expected public exception type.
    """
    error = _opnsense_http_error(status, "failure")

    assert isinstance(error, expected_exception)
    assert error.status == status


@pytest.mark.parametrize(
    ("source_error", "expected_exception"),
    [
        (TimeoutError("timeout"), aiopnsense_module.OPNsenseTimeoutError),
        (aiohttp.ClientConnectionError("connection"), aiopnsense_module.OPNsenseConnectionError),
        (ValueError("invalid payload"), aiopnsense_module.OPNsenseError),
    ],
)
def test_arbitrary_errors_map_to_public_exceptions(
    source_error: Exception,
    expected_exception: type[aiopnsense_module.OPNsenseError],
) -> None:
    """Map raw runtime and transport errors to the public hierarchy.

    Args:
        source_error (Exception): Raw exception to map.
        expected_exception (type[OPNsenseError]): Expected public exception type.
    """
    assert isinstance(_map_opnsense_exception(source_error), expected_exception)


@pytest.mark.parametrize(
    ("source_url", "forbidden"),
    [
        ("https://alice:secret@api.example/opn", ("alice", "secret")),
        ("https://alice secret@api.example/opn", ("alice secret",)),
        ("'https://alice:secret@api.example/opn'", ("alice", "secret")),
        ('"https://alice:secret@api.example/opn"', ("alice", "secret")),
        ("<https://alice:secret@api.example/opn>", ("alice", "secret")),
        ("`https://alice:secret@api.example/opn`", ("alice", "secret")),
        ("https://alice@api.example/opn", ("alice",)),
        ("https://alice:@api.example/opn", ("alice",)),
        ("https://alice:pa@ss@api.example/opn", ("alice", "pa@ss")),
        ("https://u%40lice:p%40ss@api.example/opn", ("u%40lice", "p%40ss")),
        ("https://u:pa@ss@api.example/path@with@ats", ("u", "pa@ss")),
        ("https://alice:secret@[2001:db8::1]:443/path", ("alice", "secret")),
        ("https://alice:secret@[bad", ("alice", "secret")),
        (
            "https://public.example/path https://alice:secret@api.example/opn",
            ("alice", "secret"),
        ),
        ("https://alice?bad:secret@api.example/opn", ("alice?bad", "secret")),
        ("https://alice#bad:secret@api.example/opn", ("alice#bad", "secret")),
        ("https://alice:pa/ss@api.example/opn", ("alice", "pa/ss")),
        (
            "https://alice:secret@api.example/opn https://bob:pass@other.example/opn",
            ("alice", "secret", "bob", "pass"),
        ),
    ],
)
def test_invalid_url_mapping_redacts_credentials(
    source_url: str,
    forbidden: tuple[str, ...],
) -> None:
    """Map invalid URL errors to a constant-safe message.

    Args:
        source_url (str): Invalid URL that contains sensitive userinfo.
        forbidden (tuple[str, ...]): Fragments that must not appear in the mapped message.
    """
    source_error = aiohttp.InvalidURL(source_url)

    mapped = _map_opnsense_exception(source_error)

    assert isinstance(mapped, aiopnsense_module.OPNsenseInvalidURL)
    message = str(mapped)
    assert message == "Invalid OPNsense URL"
    assert source_url not in message
    for token in forbidden:
        assert token not in message


def test_client_response_error_mapping_retains_status() -> None:
    """Map raw client response failures and retain their HTTP status."""
    source_error = aiohttp.ClientResponseError(
        request_info=MagicMock(),
        history=(),
        status=403,
        message="forbidden",
        headers=None,
    )

    error = _map_opnsense_exception(source_error)

    assert isinstance(error, aiopnsense_module.OPNsensePrivilegeMissing)
    assert error.status == 403


def test_existing_opnsense_error_is_preserved() -> None:
    """Preserve an existing public exception instance during mapping."""
    source_error = aiopnsense_module.OPNsenseVoucherServerError("voucher")

    assert _map_opnsense_exception(source_error) is source_error
