"""Tests for `aiopnsense.helpers` utility and decorator helpers."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
import inspect
import logging
import traceback
from typing import Any, NoReturn
from unittest.mock import MagicMock

import aiohttp
import pytest

from aiopnsense import (
    OPNsenseClient,
    OPNsenseError,
    OPNsenseInvalidURL,
    OPNsenseTimeoutError,
    helpers as aiopnsense_helpers,
)
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        pytest.param(0, "0 seconds", id="zero-seconds"),
        pytest.param(1, "1 second", id="singular-second"),
        pytest.param(2, "2 seconds", id="plural-seconds"),
        pytest.param(60, "1 minute", id="singular-minute"),
        pytest.param(61, "1 minute, 1 second", id="minute-and-singular-second"),
        pytest.param(65, "1 minute, 5 seconds", id="minute-and-plural-seconds"),
        pytest.param(3600, "1 hour", id="singular-hour"),
        pytest.param(7200, "2 hours", id="plural-hours"),
        pytest.param(86400, "1 day", id="singular-day"),
        pytest.param(604800, "1 week", id="singular-week"),
        pytest.param(1209600, "2 weeks", id="plural-weeks"),
        pytest.param(2419200, "1 month", id="singular-month"),
        pytest.param(4838400, "2 months", id="plural-months"),
    ],
)
def test_human_friendly_duration(seconds: int, expected: str) -> None:
    """Convert seconds to exact human-friendly duration strings.

    Args:
        seconds (int): Duration to format, in seconds.
        expected (str): Expected human-friendly duration.

    Returns:
        None: This test validates formatted output via assertions.
    """
    assert aiopnsense_helpers.human_friendly_duration(seconds) == expected


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
    ("value", "default_tz", "expected"),
    [
        ("2026-03-14T03:09:45", timezone(timedelta(hours=-4)), "2026-03-14T03:09:45-04:00"),
        ("2023-01-22 00:29:00", timezone(timedelta(hours=-4)), "2023-01-22T00:29:00-04:00"),
        ("2026-03-14T03:09:45+01:30", timezone(timedelta(hours=-4)), "2026-03-14T03:09:45+01:30"),
        ("2026-03-14T03:09:45", None, None),
        ("2026-03-14T03:09:45+01:30", None, "2026-03-14T03:09:45+01:30"),
        ("not-a-date", timezone(timedelta(hours=-4)), None),
        (12345, timezone(timedelta(hours=-4)), None),
    ],
)
def test_normalize_datetime(
    value: object, default_tz: timezone | None, expected: str | None
) -> None:
    """Normalize naive and aware datetimes while rejecting malformed values.

    Args:
        value (object): Raw datetime value under test.
        default_tz (timezone | None): Fallback timezone for naive values.
        expected (str | None): Expected timezone-aware ISO result.

    Returns:
        None: This test validates helper output via assertions.
    """
    assert (
        aiopnsense_helpers.normalize_datetime(
            value,
            default_tz,
        )
        == expected
    )


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

            Raises:
                ValueError: Always raised to simulate a comparison failure.
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
    """Compare API flag values consistently across mixed payload types.

    Args:
        value (object): API payload value evaluated for equivalence.
        expected (str): Normalized string expected from the API value.
        matches (bool): Whether the normalized comparison should succeed.
    """
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
            """Raise RuntimeError for testing error handling.

            Raises:
                RuntimeError: Always raised to exercise error handling.
            """
            raise RuntimeError("boom")

    # When error throwing is disabled, errors are logged and suppressed.
    d = Dummy(throw_errors=False)
    res = await d.boom()
    assert res is None

    # When error throwing is enabled, errors are re-raised.
    d2 = Dummy(throw_errors=True)
    with pytest.raises(OPNsenseError, match="boom"):
        await d2.boom()


@pytest.mark.asyncio
async def test_log_errors_captures_safe_details_and_caches_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture only safe details in the record and cache their rendered form.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing logging and traceback calls.
    """

    rendered_messages = 0

    class LazyMessageError(Exception):
        """Exception that records when its message is sanitized for logging."""

        def __str__(self) -> str:
            """Record message rendering during eager sanitization.

            Returns:
                str: Stable exception message.
            """
            nonlocal rendered_messages
            rendered_messages += 1
            return "request failed for https://user:secret@example.invalid"

    class Dummy:
        """Small wrapper for exercising safe exception logging."""

        _throw_errors = False

        @aiopnsense_helpers._log_errors
        async def boom(self) -> None:
            """Raise an exception whose message must be sanitized before logging.

            Raises:
                LazyMessageError: Always raised to exercise safe logging.
            """
            raise LazyMessageError

    error_log = MagicMock()
    traceback_format_calls = 0
    original_format = traceback.StackSummary.format

    def format_traceback_snapshot(
        snapshot: traceback.StackSummary,
    ) -> list[str]:
        """Record and perform formatting of a frame-free traceback snapshot.

        Args:
            snapshot (traceback.StackSummary): Captured traceback metadata.

        Returns:
            list[str]: Formatted traceback lines.
        """
        nonlocal traceback_format_calls
        traceback_format_calls += 1
        return original_format(snapshot)

    monkeypatch.setattr(aiopnsense_helpers._LOGGER, "error", error_log)
    monkeypatch.setattr(traceback.StackSummary, "format", format_traceback_snapshot)

    assert await Dummy().boom() is None
    assert rendered_messages == 1
    assert traceback_format_calls == 0
    log_details = error_log.call_args.args[2]
    traceback_snapshot = log_details._traceback_snapshot

    first_render = log_details.__str__()
    second_render = log_details.__str__()
    assert first_render.startswith(
        "LazyMessageError: request failed for https://<redacted>:<redacted>@example.invalid\n"
    )
    assert second_render is first_render
    assert rendered_messages == 1
    assert traceback_format_calls == 1
    assert not hasattr(log_details, "_error")
    assert all(not hasattr(frame, "tb_frame") for frame in traceback_snapshot)
    assert "user" not in repr(vars(log_details))
    assert "secret" not in repr(vars(log_details))


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
@pytest.mark.parametrize(
    "timeout_error",
    [
        pytest.param(TimeoutError("boom"), id="timeout-error"),
        pytest.param(aiohttp.ServerTimeoutError("srv"), id="server-timeout-error"),
    ],
)
async def test_log_errors_timeout_re_raise_and_suppress(
    make_client: ClientType, timeout_error: TimeoutError
) -> None:
    """Verify ``_log_errors`` maps or suppresses timeout-family errors by configuration.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        timeout_error (TimeoutError): Timeout-family error raised by the wrapped coroutine.

    Returns:
        None: This test validates timeout error propagation behavior.
    """
    client, _ = make_mock_session_client(make_client, url="http://x")
    try:

        async def raising_timeout(*args: Any, **kwargs: Any) -> NoReturn:
            """Raise the configured timeout-family error.

            Args:
                args (Any): Positional arguments accepted by `raising_timeout`.
                kwargs (Any): Keyword arguments accepted by `raising_timeout`.

            Returns:
                NoReturn: This helper always raises ``TimeoutError``.

            Raises:
                timeout_error: Always raised to test timeout handling.
            """
            raise timeout_error

        # wrap the coroutine with the decorator
        decorated = aiopnsense_helpers._log_errors(raising_timeout)

        # When error throwing is enabled we expect a public timeout error.
        client._throw_errors = True
        with pytest.raises(OPNsenseTimeoutError, match=str(timeout_error)):
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
            """Raise the pre-existing timeout error instance.

            Raises:
                timeout_error: Always raised to preserve the original instance.
            """
            raise timeout_error

    with pytest.raises(OPNsenseTimeoutError) as exc_info:
        await Dummy().boom()

    assert exc_info.value is timeout_error


@pytest.mark.asyncio
async def test_log_errors_redacts_url_userinfo() -> None:
    """Verify _log_errors integration maps a representative credentialed invalid URL safely."""
    raw_url = "https://alice:secret@api.example/opn"

    class Dummy:
        """Small wrapper for testing redaction in error logs and mapping."""

        _throw_errors = True

        @aiopnsense_helpers._log_errors
        async def boom(self) -> None:
            """Raise an invalid URL with credential leaks.

            Raises:
                aiohttp.InvalidURL: Always raised to test credential redaction.
            """
            raise aiohttp.InvalidURL(raw_url)

    client = Dummy()
    with pytest.raises(OPNsenseInvalidURL) as exc_info:
        await client.boom()

    message = str(exc_info.value)
    assert message == "Invalid OPNsense URL"
    assert raw_url not in message
    for token in ("alice", "secret"):
        assert token not in message


@pytest.mark.asyncio
async def test_log_errors_redacts_client_response_error_userinfo(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify _log_errors redacts credentials in ClientResponseError messages.

    Args:
        caplog (pytest.LogCaptureFixture): Captures the redacted response-error log output.
    """

    class Dummy:
        """Small wrapper for testing logged ClientResponseError redaction."""

        _throw_errors = False

        @aiopnsense_helpers._log_errors
        async def boom(self) -> None:
            """Raise ClientResponseError with embedded user credentials.

            Raises:
                aiohttp.ClientResponseError: Always raised to test credential redaction.
            """
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
