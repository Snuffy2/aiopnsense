"""Tests for `aiopnsense.helpers` utility and decorator helpers."""

from collections.abc import Callable
from datetime import UTC, datetime
import inspect
import logging
from unittest.mock import MagicMock
from typing import Any, NoReturn

import aiohttp
import pytest

from aiopnsense import (
    OPNsenseClient,
    OPNsenseError,
    OPNsenseInvalidURL,
    OPNsenseTimeoutError,
)
from aiopnsense import helpers as aiopnsense_helpers
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


def test_human_friendly_duration() -> None:
    """Convert seconds into a human-friendly duration string."""
    assert aiopnsense_helpers.human_friendly_duration(65) == "1 minute, 5 seconds"
    assert aiopnsense_helpers.human_friendly_duration(0) == "0 seconds"
    assert "month" in aiopnsense_helpers.human_friendly_duration(2419200)


def test_human_friendly_duration_singular_and_plural() -> None:
    """Verify singular and plural forms for all supported units.

    This covers seconds, minutes, hours, days, weeks and months and ensures
    the function emits the singular form when the value is 1 and plural
    otherwise.
    """
    # seconds
    assert aiopnsense_helpers.human_friendly_duration(1) == "1 second"
    assert aiopnsense_helpers.human_friendly_duration(2) == "2 seconds"

    # minutes + seconds
    assert aiopnsense_helpers.human_friendly_duration(60) == "1 minute"
    assert aiopnsense_helpers.human_friendly_duration(61) == "1 minute, 1 second"

    # hours
    assert aiopnsense_helpers.human_friendly_duration(3600) == "1 hour"
    assert aiopnsense_helpers.human_friendly_duration(7200) == "2 hours"

    # days
    assert aiopnsense_helpers.human_friendly_duration(86400) == "1 day"

    # weeks
    assert aiopnsense_helpers.human_friendly_duration(604800) == "1 week"
    assert aiopnsense_helpers.human_friendly_duration(1209600) == "2 weeks"

    # months (28-day month used in implementation)
    assert aiopnsense_helpers.human_friendly_duration(2419200) == "1 month"
    assert aiopnsense_helpers.human_friendly_duration(4838400) == "2 months"


def test_get_ip_key() -> None:
    """Compute sorting key for IP addresses across IPv4, IPv6, and invalid forms."""
    assert aiopnsense_helpers.get_ip_key({"address": "192.168.1.1"})[0] == 0
    assert aiopnsense_helpers.get_ip_key({"address": "::1"})[0] == 1
    assert aiopnsense_helpers.get_ip_key({"address": "notanip"})[0] == 2
    assert aiopnsense_helpers.get_ip_key({"address": "notanip"}) == (2, "")
    assert aiopnsense_helpers.get_ip_key({})[0] == 3
    assert aiopnsense_helpers.get_ip_key({}) == (3, "")


def test_dict_get() -> None:
    """Retrieve nested values from dicts and lists using dotted paths."""
    data = {"a": {"b": {"c": 1}}, "x": [0, 1, 2]}
    assert aiopnsense_helpers.dict_get(data, "a.b.c") == 1
    assert aiopnsense_helpers.dict_get(data, "x.1") == 1
    assert aiopnsense_helpers.dict_get(data, "x.10", default=42) == 42
    assert aiopnsense_helpers.dict_get({"a": {"b": [10, {"c": 3}]}}, "a.b") == [10, {"c": 3}]
    assert aiopnsense_helpers.dict_get(data, "missing.path", default=5) == 5


def test_timestamp_to_datetime() -> None:
    """Convert timestamp integers to datetime objects, handling None."""
    ts = int(datetime.now(tz=UTC).timestamp())
    dt = aiopnsense_helpers.timestamp_to_datetime(ts)
    assert isinstance(dt, datetime)
    assert dt.tzinfo is not None
    assert aiopnsense_helpers.timestamp_to_datetime(None) is None


@pytest.mark.parametrize(
    ("firmware_version", "expected"),
    [
        (None, None),
        ("   ", None),
        ("26.1.11_4", "26.1.11"),
    ],
)
def test_trim_firmware_suffix_handles_empty_and_suffixed_versions(
    firmware_version: str | None,
    expected: str | None,
) -> None:
    """Verify firmware suffix trimming handles empty and revision-suffixed values.

    Args:
        firmware_version (str | None): Firmware version value to trim.
        expected (str | None): Expected comparable firmware version.

    Returns:
        None: This test validates trim output via assertions.
    """
    assert aiopnsense_helpers.trim_firmware_suffix(firmware_version) == expected


@pytest.mark.parametrize(
    ("firmware_version", "comparison_version"),
    [
        (None, "26.1.11"),
        ("   ", "26.1.11"),
        ("26.1.11_bad", "26.1.10"),
        ("26.1.11", "   "),
    ],
)
def test_firmware_is_newer_returns_none_for_uncomparable_versions(
    firmware_version: str | None,
    comparison_version: str | None,
) -> None:
    """Verify uncomparable firmware update versions return ``None``.

    Args:
        firmware_version (str | None): Candidate firmware version.
        comparison_version (str | None): Firmware version to compare against.

    Returns:
        None: This test validates uncomparable-version handling via assertions.
    """
    assert aiopnsense_helpers.firmware_is_newer(firmware_version, comparison_version) is None


def test_firmware_is_newer_returns_none_when_version_comparison_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify firmware update comparison failures return ``None``.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing AwesomeVersion.

    Returns:
        None: This test validates comparison exception handling via assertions.
    """

    class RaisingAwesomeVersion:
        """AwesomeVersion stand-in that raises during construction."""

        def __init__(self, _version: str) -> None:
            """Raise a comparison setup error.

            Args:
                _version (str): Version value passed by the helper.
            """
            raise ValueError("comparison failed")

    monkeypatch.setattr(aiopnsense_helpers.awesomeversion, "AwesomeVersion", RaisingAwesomeVersion)

    assert aiopnsense_helpers.firmware_is_newer("26.1.11", "26.1.10") is None


def test_try_to_int_and_float() -> None:
    """Coerce numeric-like strings to int/float with defaults."""
    assert aiopnsense_helpers.try_to_int("5") == 5
    assert aiopnsense_helpers.try_to_int(None, 7) == 7
    assert aiopnsense_helpers.try_to_float("5.5") == 5.5
    assert aiopnsense_helpers.try_to_float(None, 3.3) == 3.3


def test_coerce_bool() -> None:
    """Verify ``coerce_bool`` handles common bool-like edge cases."""
    assert aiopnsense_helpers.coerce_bool(True) is True
    assert aiopnsense_helpers.coerce_bool(False) is False
    assert aiopnsense_helpers.coerce_bool(1) is True
    assert aiopnsense_helpers.coerce_bool(0) is False
    assert aiopnsense_helpers.coerce_bool(0.0) is False
    assert aiopnsense_helpers.coerce_bool("1") is True
    assert aiopnsense_helpers.coerce_bool("true") is True
    assert aiopnsense_helpers.coerce_bool("yes") is True
    assert aiopnsense_helpers.coerce_bool("on") is True
    assert aiopnsense_helpers.coerce_bool("") is False
    assert aiopnsense_helpers.coerce_bool(None) is False


def test_normalize_lookup_token() -> None:
    """Verify ``normalize_lookup_token`` lower-cases and trims lookup values."""
    assert aiopnsense_helpers.normalize_lookup_token("Hello") == "hello"
    assert aiopnsense_helpers.normalize_lookup_token("  WORLD  ") == "world"
    assert aiopnsense_helpers.normalize_lookup_token(42) == "42"
    assert aiopnsense_helpers.normalize_lookup_token(None) == ""


@pytest.mark.parametrize(
    ("value", "expected", "matches"),
    [
        ("0", "0", True),
        (0, "0", True),
        (False, "0", True),
        ("1", "1", True),
        (1, "1", True),
        (True, "1", True),
        ("active", "active", True),
        (False, "1", False),
        (True, "0", False),
        (1, "0", False),
        (None, "0", False),
    ],
)
def test_api_value_matches(value: object, expected: str, matches: bool) -> None:
    """Compare API flag values consistently across mixed payload types."""
    assert aiopnsense_helpers.api_value_matches(value, expected) is matches


def test_get_ip_key_sorting() -> None:
    """Sort IP-like items using get_ip_key ordering."""
    items = [
        {"address": "192.168.1.2"},
        {"address": "::1"},
        {"address": "notanip"},
        {},
    ]
    sorted_items = sorted(items, key=aiopnsense_helpers.get_ip_key)
    assert sorted_items[0]["address"] == "192.168.1.2"
    assert sorted_items[1]["address"] == "::1"
    assert sorted_items[2]["address"] == "notanip"
    assert sorted_items[3] == {}


@pytest.mark.asyncio
async def test_log_errors_decorator_re_raise_and_suppress() -> None:
    """The ``_log_errors`` decorator should re-raise when errors are enabled."""

    class Dummy:
        def __init__(self, throw_errors: bool) -> None:
            """Initialize the Dummy instance.

            Args:
                throw_errors (bool): Whether wrapped errors should be re-raised.
            """
            self._throw_errors = throw_errors

        @aiopnsense_helpers._log_errors
        async def boom(self) -> None:
            """Raise RuntimeError for testing error handling."""
            raise RuntimeError("boom")

    # When error throwing is disabled, errors are logged and suppressed.
    d = Dummy(throw_errors=False)
    res = await d.boom()
    assert res is None

    # When error throwing is enabled, errors are re-raised.
    d2 = Dummy(throw_errors=True)
    with pytest.raises(OPNsenseError, match="boom"):
        await d2.boom()


def test_log_errors_preserves_wrapped_metadata() -> None:
    """Verify ``_log_errors`` preserves wrapped method metadata for autodoc."""

    class Dummy:
        """Test helper exposing a decorated async echo method for autodoc checks."""

        @aiopnsense_helpers._log_errors
        async def boom(self, value: str) -> str:
            """Return the provided value unchanged.

            Args:
                value (str): Input value to echo.

            Returns:
                str: Echoed input value.
            """
            return value

    assert Dummy.boom.__name__ == "boom"
    assert Dummy.boom.__doc__ is not None
    assert "Return the provided value unchanged." in Dummy.boom.__doc__
    assert str(inspect.signature(Dummy.boom)) == "(self, value: str) -> str"


@pytest.mark.asyncio
async def test_log_errors_timeout_re_raise_and_suppress(make_client: ClientType) -> None:
    """Verify ``_log_errors`` re-raises or suppresses ``TimeoutError`` by configuration.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates timeout error propagation behavior.
    """
    client, _ = make_mock_session_client(make_client, url="http://x")
    try:

        async def raising_timeout(*args: Any, **kwargs: Any) -> NoReturn:
            """Raising timeout.

            Args:
                *args (Any): Positional arguments forwarded to the wrapped callable.
                **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

            Returns:
                NoReturn: This helper always raises ``TimeoutError``.
            """
            raise TimeoutError("boom")

        # wrap the coroutine with the decorator
        decorated = aiopnsense_helpers._log_errors(raising_timeout)

        # When error throwing is enabled we expect a public timeout error.
        client._throw_errors = True
        with pytest.raises(OPNsenseTimeoutError, match="boom"):
            await decorated(client)

        # When error throwing is disabled the decorator suppresses ``TimeoutError``.
        client._throw_errors = False
        res = await decorated(client)
        assert res is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_log_errors_re_raises_existing_opnsense_timeout_instance() -> None:
    """Verify existing OPNsense timeout errors are propagated unchanged."""
    timeout_error = OPNsenseTimeoutError("already mapped")

    class Dummy:
        """Small wrapper for testing timeout exception identity."""

        _throw_errors = True

        @aiopnsense_helpers._log_errors
        async def boom(self) -> None:
            """Raise the pre-existing timeout error instance."""
            raise timeout_error

    with pytest.raises(OPNsenseTimeoutError) as exc_info:
        await Dummy().boom()

    assert exc_info.value is timeout_error


@pytest.mark.asyncio
async def test_log_errors_server_timeout_re_raise_and_suppress(make_client: ClientType) -> None:
    """Verify ``_log_errors`` re-raises or suppresses ``ServerTimeoutError`` by configuration.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates server-timeout error propagation behavior.
    """
    client, _ = make_mock_session_client(make_client, url="http://x")
    try:

        async def raising_server_timeout(*args: Any, **kwargs: Any) -> Any:
            """Raising server timeout.

            Args:
                *args (Any): Positional arguments forwarded to the wrapped callable.
                **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

            Returns:
                Any: Value produced by the wrapped callable.
            """
            raise aiohttp.ServerTimeoutError("srv")

        decorated = aiopnsense_helpers._log_errors(raising_server_timeout)

        client._throw_errors = True
        with pytest.raises(OPNsenseTimeoutError, match="srv"):
            await decorated(client)

        client._throw_errors = False
        assert await decorated(client) is None
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("raw_url", "forbidden"),
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
@pytest.mark.asyncio
async def test_log_errors_redacts_url_userinfo(raw_url: str, forbidden: tuple[str, ...]) -> None:
    """Verify _log_errors maps invalid URLs using a constant-safe message.

    Args:
        raw_url (str): URL containing credentials to redact.
        forbidden (tuple[str, ...]): Fragments that must not appear in the mapped message.
    """

    class Dummy:
        """Small wrapper for testing redaction in error logs and mapping."""

        _throw_errors = True

        @aiopnsense_helpers._log_errors
        async def boom(self) -> None:
            """Raise an invalid URL with credential leaks."""
            raise aiohttp.InvalidURL(raw_url)

    client = Dummy()
    with pytest.raises(OPNsenseInvalidURL) as exc_info:
        await client.boom()

    message = str(exc_info.value)
    assert message == "Invalid OPNsense URL"
    assert raw_url not in message
    for token in forbidden:
        assert token not in message


@pytest.mark.asyncio
async def test_log_errors_redacts_client_response_error_userinfo(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify _log_errors redacts credentials in ClientResponseError messages."""

    class Dummy:
        """Small wrapper for testing logged ClientResponseError redaction."""

        _throw_errors = False

        @aiopnsense_helpers._log_errors
        async def boom(self) -> None:
            """Raise ClientResponseError with embedded user credentials."""
            request_info = MagicMock()
            request_info.real_url = "https://alice:secret@api.example/opn"
            raise aiohttp.ClientResponseError(
                request_info=request_info,
                history=(),
                status=403,
                message="forbidden",
                headers=None,
            )

    client = Dummy()
    with caplog.at_level(logging.ERROR):
        assert await client.boom() is None

    assert "alice" not in caplog.text
    assert "secret" not in caplog.text
    assert "api.example/opn" in caplog.text
    assert "<redacted>" in caplog.text
