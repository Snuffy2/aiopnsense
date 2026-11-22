"""Tests for `aiopnsense._error_policy`."""

from collections.abc import Callable
import inspect
import logging
import traceback
from typing import Any, NoReturn
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from aiopnsense import (
    OPNsenseClient,
    OPNsenseError,
    OPNsenseInvalidURL,
    OPNsenseTimeoutError,
    _error_policy as aiopnsense_error_policy,
    helpers as aiopnsense_helpers,
)
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


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

        @aiopnsense_error_policy._log_errors
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

        @aiopnsense_error_policy._log_errors
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
    assert second_render == first_render
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

        @aiopnsense_error_policy._log_errors
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
        decorated = aiopnsense_error_policy._log_errors(raising_timeout)

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

        @aiopnsense_error_policy._log_errors
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

        @aiopnsense_error_policy._log_errors
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
async def test_best_effort_step_failure_is_still_reported_by_the_method_wrapper(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A step logged at a low level must not stop the wrapper reporting the raised failure.

    Args:
        caplog (pytest.LogCaptureFixture): Log capture used to inspect record levels.
    """

    class Dummy:
        def __init__(self) -> None:
            """Initialize a client that throws errors and whose step always fails."""
            self._throw_errors = True
            self._fail = AsyncMock(side_effect=OPNsenseError("down"))

        @aiopnsense_error_policy._log_errors
        async def read(self) -> str:
            """Run one best-effort step that fails with a public error.

            Returns:
                str: Never returned because the step failure is raised.
            """
            return await aiopnsense_error_policy._attempt(
                self,
                self._fail,
                empty="empty",
                name="optional step",
                log_level=logging.DEBUG,
            )

    with (
        caplog.at_level(logging.DEBUG, logger="aiopnsense"),
        pytest.raises(OPNsenseError, match="down"),
    ):
        await Dummy().read()

    assert [record.levelno for record in caplog.records] == [logging.DEBUG, logging.ERROR]
