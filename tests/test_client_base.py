"""Tests for client validation and base object behavior."""

import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from aiopnsense import (
    OPNsenseClient,
    client as aiopnsense_client,
)
from aiopnsense.const import (
    OPNSENSE_LTD_FIRMWARE,
    OPNSENSE_MIN_FIRMWARE,
)
from aiopnsense.exceptions import (
    OPNsenseBelowMinFirmware,
    OPNsenseConnectionError,
    OPNsenseInvalidAuth,
    OPNsenseInvalidURL,
    OPNsenseMissingDeviceUniqueID,
    OPNsensePrivilegeMissing,
    OPNsenseSSLError,
    OPNsenseTimeoutError,
    OPNsenseUnknownFirmware,
)
from tests.conftest import (
    FakeClientSession,
    FakeResponse,
    MakeClientFactory,
    make_mock_session_client,
)


class _TestClientSSLError(aiohttp.ClientSSLError):
    """Minimal ``ClientSSLError`` subclass used for validation tests."""

    def __init__(self) -> None:
        """Initialize the synthetic SSL error instance."""
        Exception.__init__(self, "ssl")

    def __str__(self) -> str:
        """Return a stable string for logging and assertion output.

        Returns:
            str: Constant error message for deterministic test behavior.
        """
        return "ssl"


def _client_response_error(status: int) -> aiohttp.ClientResponseError:
    """Build a minimal ``ClientResponseError`` for validation tests.

    Args:
        status (int): HTTP status code exposed by the error.

    Returns:
        aiohttp.ClientResponseError: Response error instance with the requested status.
    """
    return aiohttp.ClientResponseError(
        request_info=MagicMock(),
        history=(),
        status=status,
        message="boom",
        headers={},
    )


def _patch_validate_requests(
    monkeypatch: pytest.MonkeyPatch,
    client: OPNsenseClient,
    get_host_firmware_version: AsyncMock,
    get_device_unique_id: AsyncMock,
) -> None:
    """Patch validation probes on a client.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for overriding client methods.
        client (OPNsenseClient): Client instance under test.
        get_host_firmware_version (AsyncMock): Firmware probe mock.
        get_device_unique_id (AsyncMock): Device ID probe mock.

    Returns:
        None: This helper mutates the supplied client.
    """
    monkeypatch.setattr(
        client,
        "get_host_firmware_version",
        get_host_firmware_version,
    )
    monkeypatch.setattr(
        client,
        "get_device_unique_id",
        get_device_unique_id,
    )


@pytest.mark.parametrize(
    ("side_effect", "expected_exception"),
    [
        (aiohttp.InvalidURL("http://bad"), OPNsenseInvalidURL),
        (_TestClientSSLError(), OPNsenseSSLError),
        (TimeoutError("timeout"), OPNsenseTimeoutError),
        (aiohttp.ServerTimeoutError("server-timeout"), OPNsenseTimeoutError),
        (_client_response_error(401), OPNsenseInvalidAuth),
        (_client_response_error(403), OPNsensePrivilegeMissing),
        (_client_response_error(500), OPNsenseConnectionError),
        (aiohttp.ClientConnectionError("connection"), OPNsenseConnectionError),
        (None, OPNsenseUnknownFirmware),
    ],
)
@pytest.mark.asyncio
async def test_validate_maps_failures_and_restores_throw_errors(
    side_effect: BaseException | None,
    expected_exception: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
    make_client: MakeClientFactory,
) -> None:
    """Verify ``validate`` maps transport failures and restores state.

    Args:
        side_effect (BaseException | None): Firmware lookup result or exception to inject.
        expected_exception (type[Exception]): Public exception expected from ``validate``.
        monkeypatch (pytest.MonkeyPatch): Fixture for overriding client methods.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts exception mapping and state restoration behavior.
    """
    client, _session = make_mock_session_client(make_client)
    client._throw_errors = False
    try:
        if side_effect is None:
            get_host_firmware_version = AsyncMock(return_value=None)
        else:
            get_host_firmware_version = AsyncMock(side_effect=side_effect)

        monkeypatch.setattr(
            client,
            "get_host_firmware_version",
            get_host_firmware_version,
        )

        with pytest.raises(expected_exception):
            await client.validate()

        assert client._throw_errors is False
        get_host_firmware_version.assert_awaited_once()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_validate_handles_firmware_thresholds_and_restores_throw_errors(
    monkeypatch: pytest.MonkeyPatch,
    make_client: MakeClientFactory,
) -> None:
    """Verify ``validate`` enforces firmware thresholds and preserves state.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for overriding client methods.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts minimum-version rejection, warning emission, and success behavior.
    """
    client, _session = make_mock_session_client(make_client)
    client._throw_errors = False
    try:
        get_host_firmware_version = AsyncMock(
            side_effect=[
                "24.7",
                f"{OPNSENSE_MIN_FIRMWARE}_1",
                f"{OPNSENSE_LTD_FIRMWARE}_1",
            ]
        )
        get_device_unique_id = AsyncMock(return_value="aa_bb_cc")
        _patch_validate_requests(
            monkeypatch,
            client,
            get_host_firmware_version,
            get_device_unique_id,
        )
        logger_warning = MagicMock()

        monkeypatch.setattr(aiopnsense_client._LOGGER, "warning", logger_warning)

        with pytest.raises(OPNsenseBelowMinFirmware):
            await client.validate()
        assert client._throw_errors is False

        await client.validate()
        logger_warning.assert_called_once_with(
            "OPNsense Firmware of %s is below the recommended >= %s. aiopnsense will work, but there may be some missing features.",
            f"{OPNSENSE_MIN_FIRMWARE}_1",
            OPNSENSE_LTD_FIRMWARE,
        )
        assert client._throw_errors is False

        await client.validate()
        assert logger_warning.call_count == 1
        assert client._throw_errors is False
        assert get_host_firmware_version.await_count == 3
        assert get_device_unique_id.await_count == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_validate_raises_when_device_unique_id_missing(
    monkeypatch: pytest.MonkeyPatch,
    make_client: MakeClientFactory,
) -> None:
    """Verify ``validate`` fails when no device unique ID can be resolved.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for overriding client methods.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts missing device ID validation and state restoration.
    """
    client, _session = make_mock_session_client(make_client)
    client._throw_errors = False
    try:
        get_host_firmware_version = AsyncMock(return_value=OPNSENSE_LTD_FIRMWARE)
        get_device_unique_id = AsyncMock(side_effect=OPNsenseMissingDeviceUniqueID)
        _patch_validate_requests(
            monkeypatch,
            client,
            get_host_firmware_version,
            get_device_unique_id,
        )

        with pytest.raises(OPNsenseMissingDeviceUniqueID):
            await client.validate()

        assert client._throw_errors is False
        get_host_firmware_version.assert_awaited_once()
        get_device_unique_id.assert_awaited_once()
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("side_effect", "expected_exception"),
    [
        (aiohttp.InvalidURL("http://bad"), OPNsenseInvalidURL),
        (_TestClientSSLError(), OPNsenseSSLError),
        (TimeoutError("timeout"), OPNsenseTimeoutError),
        (aiohttp.ServerTimeoutError("server-timeout"), OPNsenseTimeoutError),
        (_client_response_error(401), OPNsenseInvalidAuth),
        (_client_response_error(403), OPNsensePrivilegeMissing),
        (_client_response_error(500), OPNsenseConnectionError),
        (aiohttp.ClientConnectionError("connection"), OPNsenseConnectionError),
    ],
)
@pytest.mark.asyncio
async def test_validate_maps_device_unique_id_failures_and_restores_throw_errors(
    side_effect: BaseException,
    expected_exception: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
    make_client: MakeClientFactory,
) -> None:
    """Verify device unique ID validation failures map to public exceptions.

    Args:
        side_effect (BaseException): Device unique ID lookup exception to inject.
        expected_exception (type[Exception]): Public exception expected from ``validate``.
        monkeypatch (pytest.MonkeyPatch): Fixture for overriding client methods.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts exception mapping and state restoration behavior.
    """
    client, _session = make_mock_session_client(make_client)
    client._throw_errors = False
    try:
        get_host_firmware_version = AsyncMock(return_value=OPNSENSE_LTD_FIRMWARE)
        get_device_unique_id = AsyncMock(side_effect=side_effect)
        _patch_validate_requests(
            monkeypatch,
            client,
            get_host_firmware_version,
            get_device_unique_id,
        )

        with pytest.raises(expected_exception):
            await client.validate()

        assert client._throw_errors is False
        get_host_firmware_version.assert_awaited_once()
        get_device_unique_id.assert_awaited_once()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expect_warning", "expected_throw_errors"),
    [
        ({}, False, False),
        ({"throw_errors": True}, False, True),
        ({"initial": True}, True, True),
        ({"initial": False}, True, False),
        ({"initial": True, "throw_errors": False}, True, False),
    ],
)
async def test_client_constructor_throw_errors_configuration(
    kwargs: dict[str, bool],
    expect_warning: bool,
    expected_throw_errors: bool,
    make_client: MakeClientFactory,
) -> None:
    """Verify constructor error-propagation configuration and deprecation handling."""
    client: OPNsenseClient | None = None
    try:
        warning_context = (
            pytest.warns(DeprecationWarning, match="`initial` is deprecated")
            if expect_warning
            else contextlib.nullcontext()
        )
        with warning_context:
            client = make_client(**kwargs)
        assert client._throw_errors is expected_throw_errors
    finally:
        if client is not None:
            await client.async_close()


@pytest.mark.asyncio
async def test_client_constructor_invalid_throw_errors_raises_type_error(
    make_client: MakeClientFactory,
) -> None:
    """Verify invalid ``throw_errors`` values raise ``TypeError``."""
    with pytest.raises(TypeError, match="`throw_errors` must be a bool"):
        make_client(throw_errors="false")


@pytest.mark.asyncio
async def test_client_constructor_invalid_initial_raises_type_error(
    make_client: MakeClientFactory,
) -> None:
    """Verify invalid deprecated ``initial`` values raise ``TypeError``."""
    with pytest.raises(TypeError, match="`initial` must be a bool"):
        make_client(initial="false")


@pytest.mark.asyncio
async def test_client_constructor_allows_positional_name_after_initial() -> None:
    """Verify legacy positional ``name`` remains valid after ``initial``."""
    with pytest.warns(DeprecationWarning, match="`initial` is deprecated"):
        client = OPNsenseClient(
            "http://localhost",
            "u",
            "p",
            FakeClientSession(),  # type: ignore[arg-type]
            None,
            False,
            "Custom",
        )
    try:
        assert client.name == "Custom"
        assert client._throw_errors is False
    finally:
        await client.async_close()


def test_client_constructor_rejects_positional_throw_errors() -> None:
    """Verify ``throw_errors`` can no longer be passed positionally."""
    args: tuple[Any, ...] = (
        "http://localhost",
        "u",
        "p",
        FakeClientSession(),  # type: ignore[arg-type]
        None,
        False,
        "Custom",
        True,
    )
    with pytest.raises(TypeError):
        OPNsenseClient(*args)


@pytest.mark.asyncio
async def test_toggle_throwing_errors_updates_state(make_client: MakeClientFactory) -> None:
    """Verify ``toggle_throwing_errors`` toggles and sets the error mode."""
    client = make_client()
    try:
        assert client.toggle_throwing_errors() is True
        assert client._throw_errors is True
        assert client.toggle_throwing_errors(False) is False
        assert client._throw_errors is False
        assert client.toggle_throwing_errors(True) is True
        assert client._throw_errors is True
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_toggle_throwing_errors_invalid_value_raises_type_error(
    make_client: MakeClientFactory,
) -> None:
    """Verify invalid toggle values raise ``TypeError``."""
    client = make_client()
    try:
        with pytest.raises(TypeError, match="`throw_errors` must be a bool or None"):
            client.toggle_throwing_errors("false")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_client_constructor_initializes_snake_case_state(
    make_client: MakeClientFactory,
) -> None:
    """Verify the client starts with unset snake-case endpoint state.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates the initial snake-case state.
    """
    client = make_client()
    try:
        assert client._use_snake_case is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_client_name_property(make_client: MakeClientFactory) -> None:
    """Verify the client reports the expected human-readable name.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts the client name property value.
    """
    client, _session = make_mock_session_client(make_client, username="user", password="pass")
    try:
        assert client.name == "OPNsense"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_reset_and_get_query_counts(make_client: MakeClientFactory) -> None:
    """Verify query counter retrieval and reset behavior.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts query counter increment and reset semantics.
    """
    client, session = make_mock_session_client(make_client, username="user", password="pass")
    session.get = lambda *args, **kwargs: FakeResponse(
        status=200,
        reason="OK",
        ok=True,
        json_payload={"ok": True},
    )
    try:
        await client._do_get("/api/test", caller="test_reset_and_get_query_counts")
        count = await client.get_query_counts()
        assert count > 0

        await client.reset_query_counts()
        count = await client.get_query_counts()
        assert count == 0
    finally:
        await client.async_close()
