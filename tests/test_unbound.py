"""Tests for `aiopnsense.unbound`."""

import asyncio
from collections.abc import Callable
from copy import deepcopy
import logging
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest

from aiopnsense import (
    OPNsenseClient,
    OPNsenseConnectionError,
    OPNsenseError,
    OPNsenseInvalidURL,
    OPNsenseTimeoutError,
)
from tests.conftest import FirmwareRouter, RouterReply, ScriptedRouter, make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


@pytest.mark.asyncio
async def test_get_unbound_blocklist_returns_uuid_mapping(make_client: ClientType) -> None:
    """The DNSBL search response should be normalized into a UUID-keyed mapping.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.8")
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {"uuid": "dnsbl1", "enabled": "1"},
                    {"uuid": "dnsbl2", "enabled": "0"},
                    {"no_uuid": "skip-me"},
                    "bad-row",
                ]
            }
        )

        result = await client.get_unbound_blocklist()

        assert result == {
            "dnsbl1": {"uuid": "dnsbl1", "enabled": "1"},
            "dnsbl2": {"uuid": "dnsbl2", "enabled": "0"},
        }
        client._is_get_endpoint_available.assert_awaited_once_with(
            "/api/unbound/settings/search_dnsbl"
        )
        client._safe_dict_get.assert_awaited_once_with("/api/unbound/settings/search_dnsbl")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("api_response", [{}, {"rows": []}, {"rows": "not-a-list"}, []])
async def test_get_unbound_blocklist_handles_empty_or_invalid_responses(
    make_client: ClientType, api_response: object
) -> None:
    """Malformed or empty DNSBL responses should normalize to an empty mapping.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        api_response (object): Opaque malformed or empty API response.
    """
    client = make_client()
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.8")
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(return_value=api_response)

        result = await client.get_unbound_blocklist()

        assert result == {}
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_unbound_blocklist_returns_empty_when_endpoint_unavailable(
    make_client: ClientType,
) -> None:
    """When DNSBL endpoint is unavailable, blocklist retrieval should fail closed.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates fail-closed DNSBL lookup behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.8")
        client._is_get_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get = AsyncMock()

        result = await client.get_unbound_blocklist()

        assert result == {}
        client._is_get_endpoint_available.assert_awaited_once_with(
            "/api/unbound/settings/search_dnsbl"
        )
        client._safe_dict_get.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("firmware_version", ["25.7.7", "25.7.7_1"])
async def test_get_unbound_blocklist_returns_legacy_payload_for_older_firmware(
    make_client: ClientType,
    firmware_version: str,
) -> None:
    """Older firmware should return the legacy DNSBL payload under the legacy key.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        firmware_version (str): Older firmware version requiring the legacy endpoint.

    Returns:
        None: This test validates legacy DNSBL payload normalization.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value=firmware_version)
        client._is_get_endpoint_available = AsyncMock()
        client._safe_dict_get = AsyncMock(
            return_value={
                "unbound": {
                    "dnsbl": {
                        "enabled": "1",
                        "safesearch": "0",
                        "nxdomain": "1",
                        "address": "0.0.0.0",
                        "type": {
                            "ads": {"selected": 1},
                            "malware": {"selected": 0},
                            "tracking": {"selected": "1"},
                        },
                        "lists": {
                            "list-a": {"selected": 1},
                            "list-b": {"selected": 1},
                            "list-c": {"selected": "1"},
                        },
                        "whitelists": {
                            "allow-a": {"selected": 1},
                        },
                        "blocklists": {
                            "deny-a": {"selected": 1},
                        },
                        "wildcards": {
                            "wild-a": {"selected": 1},
                        },
                    }
                }
            }
        )

        result = await client.get_unbound_blocklist()

        assert result == {
            "legacy": {
                "enabled": "1",
                "safesearch": "0",
                "nxdomain": "1",
                "address": "0.0.0.0",
                "type": "ads,tracking",
                "lists": "list-a,list-b,list-c",
                "whitelists": "allow-a",
                "blocklists": "deny-a",
                "wildcards": "wild-a",
            }
        }
        client._is_get_endpoint_available.assert_not_awaited()
        client._safe_dict_get.assert_awaited_once_with("/api/unbound/settings/get")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "api_response",
    [
        {"unbound": []},
        {"unbound": {"dnsbl": []}},
    ],
)
async def test_get_unbound_blocklist_returns_empty_for_invalid_legacy_payloads(
    make_client: ClientType,
    api_response: dict[str, Any],
) -> None:
    """Verify malformed legacy DNSBL payloads normalize to an empty mapping.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        api_response (dict[str, Any]): Legacy API payload returned by OPNsense.

    Returns:
        None: This test validates malformed legacy DNSBL payload handling.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.7")
        client._safe_dict_get = AsyncMock(return_value=api_response)

        result = await client.get_unbound_blocklist()

        assert result == {"legacy": {}}
        client._safe_dict_get.assert_awaited_once_with("/api/unbound/settings/get")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_unbound_blocklist_legacy_uses_empty_strings_for_non_mapping_selector_groups(
    make_client: ClientType,
) -> None:
    """Verify invalid selector groups in legacy DNSBL payloads normalize to empty strings.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates selector-group normalization for legacy DNSBL payloads.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.7")
        client._safe_dict_get = AsyncMock(
            return_value={
                "unbound": {
                    "dnsbl": {
                        "enabled": "1",
                        "safesearch": "0",
                        "nxdomain": "1",
                        "address": "0.0.0.0",
                        "type": "invalid",
                        "lists": "invalid",
                        "whitelists": "invalid",
                        "blocklists": "invalid",
                        "wildcards": "invalid",
                    }
                }
            }
        )

        result = await client.get_unbound_blocklist()

        assert result == {
            "legacy": {
                "enabled": "1",
                "safesearch": "0",
                "nxdomain": "1",
                "address": "0.0.0.0",
                "type": "",
                "lists": "",
                "whitelists": "",
                "blocklists": "",
                "wildcards": "",
            }
        }
    finally:
        await client.async_close()


SETTINGS_GET = "/api/unbound/settings/get"
SETTINGS_SET = "/api/unbound/settings/set"
DNSBL_APPLY = "/api/unbound/service/dnsbl"
RESTART = "/api/unbound/service/restart"
EXTENDED_FIRMWARE = "25.7.8"
LEGACY_FIRMWARE = "25.7.7"
OK_APPLIED = {"status": "OK applied"}


def _toggle_path(uuid: str, enabled: bool) -> str:
    """Return the extended DNSBL toggle endpoint.

    Args:
        uuid (str): DNSBL row UUID.
        enabled (bool): Requested state.

    Returns:
        str: Toggle endpoint path.
    """
    return f"/api/unbound/settings/toggle_dnsbl/{uuid}/{'1' if enabled else '0'}"


def _script_legacy(
    router: ScriptedRouter,
    payload: dict[str, Any],
    *,
    save: RouterReply = {"result": "saved"},  # noqa: B006
    apply: RouterReply = OK_APPLIED,
    restart: RouterReply = {"response": "OK"},  # noqa: B006
) -> None:
    """Script a legacy Router.

    Args:
        router (ScriptedRouter): Router to script.
        payload (dict[str, Any]): Legacy settings the Router returns on read.
        save (RouterReply): Reply to the settings save.
        apply (RouterReply): Reply to the DNSBL apply GET.
        restart (RouterReply): Reply to the service restart.
    """
    router.reply(SETTINGS_GET, deepcopy(payload), method="GET")
    router.reply(SETTINGS_SET, save)
    router.reply(DNSBL_APPLY, apply, method="GET")
    router.reply(RESTART, restart)


def _firmware(client: OPNsenseClient, version: str | None) -> None:
    """Pin the firmware version the client reports.

    Args:
        client (OPNsenseClient): Client to patch.
        version (str | None): Firmware version to report.
    """
    client.get_host_firmware_version = AsyncMock(return_value=version)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "uuid", "toggle_reply", "apply_reply", "expected", "applied"),
    [
        ("enable_unbound_blocklist", "uuid1", {"result": "Enabled"}, OK_APPLIED, True, True),
        ("disable_unbound_blocklist", "uuid1", {"result": "Disabled"}, OK_APPLIED, True, True),
        ("enable_unbound_blocklist", None, {"result": "Enabled"}, OK_APPLIED, False, False),
        ("enable_unbound_blocklist", "uuid1", {"result": "failed"}, OK_APPLIED, False, False),
        ("enable_unbound_blocklist", "uuid1", {"result": "Disabled"}, OK_APPLIED, False, False),
        ("disable_unbound_blocklist", "uuid1", {"result": "Enabled"}, OK_APPLIED, False, False),
        (
            "enable_unbound_blocklist",
            "uuid1",
            {"result": "Enabled"},
            {"status": "failed"},
            False,
            True,
        ),
    ],
)
async def test_enable_disable_unbound_blocklist_extended(
    scripted_router: ScriptedRouter,
    method_name: str,
    uuid: str | None,
    toggle_reply: dict[str, Any],
    apply_reply: dict[str, Any],
    expected: bool,
    applied: bool,
) -> None:
    """Extended toggles need a UUID, a save result matching the requested state, and an OK apply.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        method_name (str): Client toggle method name.
        uuid (str | None): Optional DNSBL row UUID.
        toggle_reply (dict[str, Any]): Reply to the toggle request.
        apply_reply (dict[str, Any]): Reply to the DNSBL apply.
        expected (bool): Expected operation result.
        applied (bool): Whether the DNSBL apply should have been sent.
    """
    enabled = method_name == "enable_unbound_blocklist"
    scripted_router.reply(_toggle_path("uuid1", enabled), toggle_reply)
    scripted_router.reply(DNSBL_APPLY, apply_reply)
    client = scripted_router.client()
    try:
        _firmware(client, EXTENDED_FIRMWARE)

        assert await getattr(client, method_name)(uuid) is expected
        assert scripted_router.requested(DNSBL_APPLY) is applied
        assert not scripted_router.requested(DNSBL_APPLY, method="GET")
        if uuid is None:
            assert scripted_router.requests == []
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "expected_enabled"),
    [("enable_unbound_blocklist", "1"), ("disable_unbound_blocklist", "0")],
)
async def test_enable_disable_unbound_blocklist_legacy_for_older_firmware(
    scripted_router: ScriptedRouter,
    method_name: str,
    expected_enabled: str,
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """Older firmware saves the requested state, applies the DNSBL with a GET, then restarts.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        method_name (str): Client method under test.
        expected_enabled (str): Expected enabled value in the saved payload.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings payload.
    """
    legacy_dnsbl_payload["unbound"]["dnsbl"]["type"] = {"ads": {"selected": 1}}
    legacy_dnsbl_payload["unbound"]["dnsbl"]["lists"] = {"list-a": {"selected": 1}}
    _script_legacy(scripted_router, legacy_dnsbl_payload)
    client = scripted_router.client()
    try:
        _firmware(client, LEGACY_FIRMWARE)

        assert await getattr(client, method_name)() is True

        saved = next(r for r in scripted_router.requests if r.path == SETTINGS_SET)
        assert saved.payload == {
            "unbound": {
                "dnsbl": {
                    "enabled": expected_enabled,
                    "safesearch": "0",
                    "nxdomain": "1",
                    "address": "0.0.0.0",
                    "type": "ads",
                    "lists": "list-a",
                    "whitelists": "",
                    "blocklists": "",
                    "wildcards": "",
                }
            }
        }
        assert scripted_router.requested(DNSBL_APPLY, method="GET")
        assert not scripted_router.requested(DNSBL_APPLY)
        assert scripted_router.requested(RESTART)
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["enable_unbound_blocklist", "disable_unbound_blocklist"])
async def test_legacy_unbound_blocklist_rejects_uuid(
    scripted_router: ScriptedRouter, method_name: str, legacy_dnsbl_payload: dict[str, Any]
) -> None:
    """Legacy Unbound blocklists reject UUID-based operations without saving anything.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        method_name (str): Client method under test.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings payload.
    """
    _script_legacy(scripted_router, legacy_dnsbl_payload)
    client = scripted_router.client()
    try:
        _firmware(client, LEGACY_FIRMWARE)

        assert await getattr(client, method_name)("uuid1") is False
        assert not scripted_router.requested(SETTINGS_SET)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_enable_unbound_blocklist_legacy_returns_false_when_state_cannot_be_loaded(
    scripted_router: ScriptedRouter,
) -> None:
    """Legacy DNSBL updates fail closed when current settings cannot be read.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    _script_legacy(scripted_router, {"unbound": {"dnsbl": []}})
    client = scripted_router.client()
    try:
        _firmware(client, LEGACY_FIRMWARE)

        assert await client.enable_unbound_blocklist() is False
        assert not scripted_router.requested(SETTINGS_SET)
        assert not scripted_router.requested(DNSBL_APPLY, method="GET")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("save_reply", [None, {}, {"result": "failed"}, {"result": "SAVED"}, []])
async def test_enable_unbound_blocklist_legacy_does_not_apply_or_restart_after_failed_save(
    scripted_router: ScriptedRouter,
    save_reply: RouterReply,
    legacy_dnsbl_payload: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Legacy DNSBL updates stop before apply and restart when the save is not confirmed.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        save_reply (RouterReply): Reply to the legacy settings save.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings payload.
        caplog (pytest.LogCaptureFixture): Logs emitted while rejecting the save.
    """
    _script_legacy(scripted_router, legacy_dnsbl_payload, save=save_reply)
    client = scripted_router.client()
    try:
        _firmware(client, LEGACY_FIRMWARE)

        assert await client.enable_unbound_blocklist() is False
        assert any(r.levelno == logging.ERROR and SETTINGS_SET in r.message for r in caplog.records)
        assert not scripted_router.requested(DNSBL_APPLY, method="GET")
        assert not scripted_router.requested(RESTART)
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "apply_reply", [None, {"status": None}, {"status": 1}, {"status": "failed"}, []]
)
async def test_enable_unbound_blocklist_legacy_returns_false_for_bad_apply_status(
    scripted_router: ScriptedRouter,
    apply_reply: RouterReply,
    legacy_dnsbl_payload: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Legacy DNSBL updates fail closed, without a restart, on a bad apply response.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        apply_reply (RouterReply): Reply to the DNSBL apply.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings payload.
        caplog (pytest.LogCaptureFixture): Logs emitted while rejecting the apply.
    """
    _script_legacy(scripted_router, legacy_dnsbl_payload, apply=apply_reply)
    client = scripted_router.client()
    try:
        _firmware(client, LEGACY_FIRMWARE)

        assert await client.enable_unbound_blocklist() is False
        assert any(r.levelno == logging.ERROR and DNSBL_APPLY in r.message for r in caplog.records)
        assert scripted_router.requested(DNSBL_APPLY, method="GET")
        assert not scripted_router.requested(RESTART)
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("restart_reply", "expected"),
    [
        ({"response": "OK"}, True),
        ({"response": "failed"}, False),
        ({"status": "OK"}, False),
        (None, False),
    ],
)
async def test_enable_unbound_blocklist_legacy_requires_restart_response(
    scripted_router: ScriptedRouter,
    restart_reply: RouterReply,
    expected: bool,
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """The legacy restart reports its outcome under ``response``.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        restart_reply (RouterReply): Reply to the service restart.
        expected (bool): Expected Toggle result.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings payload.
    """
    _script_legacy(scripted_router, legacy_dnsbl_payload, restart=restart_reply)
    client = scripted_router.client()
    try:
        _firmware(client, LEGACY_FIRMWARE)

        assert await client.enable_unbound_blocklist() is expected
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("firmware_version", ["invalid", None])
@pytest.mark.parametrize(
    ("method_name", "uuid", "backend", "expected"),
    [
        ("enable_unbound_blocklist", None, "legacy", True),
        ("disable_unbound_blocklist", None, "legacy", True),
        ("enable_unbound_blocklist", "uuid1", "extended", True),
        ("disable_unbound_blocklist", "uuid1", "extended", True),
        ("disable_unbound_blocklist", "", "none", False),
    ],
)
async def test_unbound_blocklist_firmware_fallback(
    scripted_router: ScriptedRouter,
    firmware_version: str | None,
    method_name: str,
    uuid: str | None,
    backend: str,
    expected: bool,
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """Uncomparable firmware uses the extended backend with a UUID, else the legacy one.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        firmware_version (str | None): Firmware version the client reports.
        method_name (str): Client method under test.
        uuid (str | None): Optional UUID supplied to the client method.
        backend (str): Backend expected to receive requests.
        expected (bool): Expected method result.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings payload.
    """
    enabled = method_name == "enable_unbound_blocklist"
    toggle_path = _toggle_path("uuid1", enabled)
    _script_legacy(scripted_router, legacy_dnsbl_payload)
    scripted_router.reply(toggle_path, {"result": "Enabled" if enabled else "Disabled"})
    scripted_router.reply(DNSBL_APPLY, OK_APPLIED)
    client = scripted_router.client()
    try:
        _firmware(client, firmware_version)

        assert await getattr(client, method_name)(uuid) is expected
        assert scripted_router.requested(SETTINGS_SET) is (backend == "legacy")
        assert scripted_router.requested(toggle_path) is (backend == "extended")
        assert scripted_router.requested(DNSBL_APPLY) is (backend == "extended")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["legacy", "extended"])
@pytest.mark.parametrize("throw_errors", [True, False])
async def test_unbound_blocklist_apply_transport_error(
    scripted_router: ScriptedRouter,
    backend: str,
    throw_errors: bool,
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """A transport failure in the apply raises with ``throw_errors`` on, else returns ``False``.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        backend (str): Backend under test.
        throw_errors (bool): Client ``throw_errors`` setting.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings payload.
    """
    error = (
        OPNsenseInvalidURL("https://user:password@api.example/api/unbound/service/dnsbl")
        if throw_errors
        else None
    )
    if backend == "legacy":
        _script_legacy(scripted_router, legacy_dnsbl_payload, apply=error)
        uuid, firmware = None, LEGACY_FIRMWARE
    else:
        scripted_router.reply(_toggle_path("uuid1", True), {"result": "Enabled"})
        scripted_router.reply(DNSBL_APPLY, error)
        uuid, firmware = "uuid1", EXTENDED_FIRMWARE
    client = scripted_router.client(throw_errors=throw_errors)
    try:
        _firmware(client, firmware)

        if throw_errors:
            with pytest.raises(OPNsenseError):
                await client.enable_unbound_blocklist(uuid)
        else:
            assert await client.enable_unbound_blocklist(uuid) is False
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_legacy_blocklist_completion_log_omits_configuration_and_response_data(
    make_client: ClientType,
    caplog: pytest.LogCaptureFixture,
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """Legacy completion logs should report outcome without logging API data.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Captured logs for the public operation.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings returned before the save.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.7")
        payload = deepcopy(legacy_dnsbl_payload)
        payload["unbound"]["dnsbl"]["address"] = "CONFIG_MARKER"
        client._post = AsyncMock(
            side_effect=[
                {"result": "saved", "detail": "SAVE_RESPONSE_MARKER"},
                {"response": "OK", "detail": "RESTART_RESPONSE_MARKER"},
            ]
        )
        client._get = AsyncMock(
            side_effect=[payload, {"status": "OK applied", "detail": "APPLY_RESPONSE_MARKER"}]
        )

        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            result = await client.enable_unbound_blocklist()

        assert result is True
        assert "CONFIG_MARKER" not in caplog.text
        assert "SAVE_RESPONSE_MARKER" not in caplog.text
        assert "APPLY_RESPONSE_MARKER" not in caplog.text
        assert "RESTART_RESPONSE_MARKER" not in caplog.text
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["enable_unbound_blocklist", "disable_unbound_blocklist"])
@pytest.mark.parametrize("failing_step", ["save", "apply", "restart"])
@pytest.mark.parametrize(
    ("make_error", "expected_error"),
    [
        (lambda: aiohttp.ClientError("boom"), OPNsenseConnectionError),
        (
            lambda: OPNsenseInvalidURL("https://user:password@api.example/api/unbound/x"),
            OPNsenseInvalidURL,
        ),
        (lambda: TimeoutError("slow"), OPNsenseTimeoutError),
    ],
)
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_legacy_unbound_blocklist_request_failure_follows_throw_errors(
    make_client: ClientType,
    caplog: pytest.LogCaptureFixture,
    legacy_dnsbl_payload: dict[str, Any],
    method_name: str,
    failing_step: str,
    make_error: Callable[[], Exception],
    expected_error: type[Exception],
    throw_errors: bool,
) -> None:
    """Verify a failed legacy save, apply or restart request follows ``throw_errors``.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Log capture used to check the failure is logged once
            when errors are thrown.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings returned before the save.
        method_name (str): Client toggle method name.
        failing_step (str): Legacy step whose request raises: ``save``, ``apply`` or ``restart``.
        make_error (Callable[[], Exception]): Factory for a fresh exception, because a raised
            public error is marked as logged.
        expected_error (type[Exception]): Public error expected when errors are thrown.
        throw_errors (bool): Client error propagation setting.

    Returns:
        None: This test validates legacy request failure handling in both error modes.
    """
    client, _session = make_mock_session_client(make_client)
    client._throw_errors = throw_errors
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.7")
        post_results: list[Any] = [{"result": "saved"}, {"response": "OK"}]
        get_results: list[Any] = [deepcopy(legacy_dnsbl_payload), {"status": "OK"}]
        if failing_step == "save":
            post_results[0] = make_error()
        elif failing_step == "apply":
            get_results[1] = make_error()
        else:
            post_results[1] = make_error()
        client._post = AsyncMock(side_effect=post_results)
        client._get = AsyncMock(side_effect=get_results)

        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            if throw_errors:
                with pytest.raises(expected_error):
                    await getattr(client, method_name)()
            else:
                assert await getattr(client, method_name)() is False

        expected_posts = {"save": 1, "apply": 1, "restart": 2}[failing_step]
        assert client._post.await_count == expected_posts
        assert client._get.await_count == (1 if failing_step == "save" else 2)
        failure_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(failure_records) == 1
        assert "password" not in caplog.text
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["enable_unbound_blocklist", "disable_unbound_blocklist"])
@pytest.mark.parametrize(
    ("make_apply_error", "expected_error"),
    [
        (lambda: aiohttp.ClientError("boom"), OPNsenseConnectionError),
        (
            lambda: OPNsenseInvalidURL(
                "https://user:password@api.example/api/unbound/service/dnsbl"
            ),
            OPNsenseInvalidURL,
        ),
        (lambda: TimeoutError("slow"), OPNsenseTimeoutError),
    ],
)
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_toggle_unbound_blocklist_apply_exception_follows_throw_errors(
    make_client: ClientType,
    caplog: pytest.LogCaptureFixture,
    method_name: str,
    make_apply_error: Callable[[], Exception],
    expected_error: type[Exception],
    throw_errors: bool,
) -> None:
    """Verify a failed DNSBL apply request returns ``False`` or raises per ``throw_errors``.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Log capture used to check the failure is logged once.
        method_name (str): Client toggle method name.
        make_apply_error (Callable[[], Exception]): Factory for a fresh apply-phase exception,
            because a raised public error is marked as logged.
        expected_error (type[Exception]): Public error expected when errors are thrown.
        throw_errors (bool): Client error propagation setting.

    Returns:
        None: This test validates DNSBL apply failure handling in both error modes.
    """
    client, _session = make_mock_session_client(make_client)
    client._throw_errors = throw_errors
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.8")
        result = "Enabled" if method_name.startswith("enable") else "Disabled"
        client._post = AsyncMock(side_effect=[{"result": result}, make_apply_error()])

        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            if throw_errors:
                with pytest.raises(expected_error):
                    await getattr(client, method_name)("uuid1")
            else:
                assert await getattr(client, method_name)("uuid1") is False

        failure_records = [record for record in caplog.records if record.levelno >= logging.WARNING]
        assert len(failure_records) == 1
        assert "password" not in caplog.text
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_toggle_unbound_blocklist_apply_cancellation_propagates(
    make_client: ClientType,
    throw_errors: bool,
) -> None:
    """Verify cancellation during the DNSBL apply request is never swallowed.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Client error propagation setting.

    Returns:
        None: This test validates cancellation propagation in both error modes.
    """
    client, _session = make_mock_session_client(make_client)
    client._throw_errors = throw_errors
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.8")
        client._post = AsyncMock(side_effect=[{"result": "Enabled"}, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await client.enable_unbound_blocklist("uuid1")
    finally:
        await client.async_close()


UNBOUND_GET_PATH = "/api/unbound/settings/get"
UNBOUND_SET_PATH = "/api/unbound/settings/set"
UNBOUND_SEARCH_DNSBL_PATH = "/api/unbound/settings/search_dnsbl"
UNBOUND_DNSBL_PATH = "/api/unbound/service/dnsbl"
UNBOUND_RESTART_PATH = "/api/unbound/service/restart"
UNBOUND_TOGGLE_PATH = "/api/unbound/settings/toggle_dnsbl/uuid1/"

LEGACY_FIRMWARE_VERSIONS = ["25.7.7", "25.7.7_1"]
EXTENDED_FIRMWARE_VERSIONS = ["25.7.8", "26.1"]
UNKNOWN_FIRMWARE = [
    pytest.param(None, id="missing"),
    pytest.param("not-a-version", id="not-comparable"),
]


@pytest.fixture
def unbound_router(
    firmware_router: tuple[OPNsenseClient, FirmwareRouter],
    legacy_dnsbl_payload: dict[str, Any],
) -> tuple[OPNsenseClient, FirmwareRouter]:
    """Return a scripted Router serving both the legacy and the extended Unbound blocklist.

    Args:
        firmware_router (tuple[OPNsenseClient, FirmwareRouter]): Client and the Router that
            scripts it.
        legacy_dnsbl_payload (dict[str, Any]): Shared legacy DNSBL payload fixture.

    Returns:
        tuple[OPNsenseClient, FirmwareRouter]: Client and Router with both blocklist APIs.
    """
    client, router = firmware_router
    router.endpoints.update(
        {
            UNBOUND_GET_PATH: deepcopy(legacy_dnsbl_payload),
            UNBOUND_SEARCH_DNSBL_PATH: {"rows": [{"uuid": "uuid1", "enabled": "1"}]},
            UNBOUND_SET_PATH: {"result": "saved"},
            UNBOUND_DNSBL_PATH: {"status": "OK"},
            UNBOUND_RESTART_PATH: {"response": "OK"},
            f"{UNBOUND_TOGGLE_PATH}1": {"result": "Enabled"},
            f"{UNBOUND_TOGGLE_PATH}0": {"result": "Disabled"},
        }
    )
    return client, router


@pytest.mark.parametrize(
    ("firmware", "expect_legacy"),
    [
        *[
            pytest.param(firmware, True, id=f"legacy-{firmware}")
            for firmware in LEGACY_FIRMWARE_VERSIONS
        ],
        *[
            pytest.param(firmware, False, id=f"extended-{firmware}")
            for firmware in EXTENDED_FIRMWARE_VERSIONS
        ],
        *[
            pytest.param(param.values[0], True, id=f"unknown-{param.id}")
            for param in UNKNOWN_FIRMWARE
        ],
    ],
)
async def test_get_unbound_blocklist_uses_method_for_firmware(
    unbound_router: tuple[OPNsenseClient, FirmwareRouter],
    firmware: str | None,
    expect_legacy: bool,
) -> None:
    """Verify reads use the extended blocklist from 25.7.8 and the legacy one otherwise.

    Args:
        unbound_router (tuple[OPNsenseClient, FirmwareRouter]): Client and Router with both APIs.
        firmware (str | None): Firmware version reported by the Router.
        expect_legacy (bool): Whether the legacy blocklist is expected.
    """
    client, router = unbound_router
    router.firmware = firmware

    result = await client.get_unbound_blocklist()

    if expect_legacy:
        assert list(result) == ["legacy"]
        assert router.get_paths[-1] == UNBOUND_GET_PATH
    else:
        assert result == {"uuid1": {"uuid": "uuid1", "enabled": "1"}}
        assert UNBOUND_GET_PATH not in router.get_paths


@pytest.mark.parametrize("method_name", ["enable_unbound_blocklist", "disable_unbound_blocklist"])
@pytest.mark.parametrize(
    ("firmware", "uuid", "expected_result", "expect_legacy_save", "expect_toggle"),
    [
        *[
            pytest.param(firmware, None, True, True, False, id=f"legacy-{firmware}")
            for firmware in LEGACY_FIRMWARE_VERSIONS
        ],
        pytest.param("25.7.7", "uuid1", False, False, False, id="legacy-refuses-uuid"),
        pytest.param("25.7.8", "uuid1", True, False, True, id="extended-uuid"),
        pytest.param("25.7.8", None, False, False, False, id="extended-needs-uuid"),
        pytest.param(None, None, True, True, False, id="unknown-missing-no-uuid"),
        pytest.param(None, "uuid1", True, False, True, id="unknown-missing-uuid"),
        pytest.param("not-a-version", None, True, True, False, id="unknown-not-comparable-no-uuid"),
        pytest.param("not-a-version", "uuid1", True, False, True, id="unknown-not-comparable-uuid"),
        pytest.param(None, "", False, False, False, id="unknown-empty-uuid"),
    ],
)
async def test_unbound_blocklist_write_uses_method_for_firmware(
    unbound_router: tuple[OPNsenseClient, FirmwareRouter],
    method_name: str,
    firmware: str | None,
    uuid: str | None,
    expected_result: bool,
    expect_legacy_save: bool,
    expect_toggle: bool,
) -> None:
    """Verify writes pick the blocklist method from firmware and the supplied UUID.

    Known firmware decides the method. Unknown firmware uses the extended blocklist only when
    an entry UUID is supplied, and the legacy one otherwise.

    Args:
        unbound_router (tuple[OPNsenseClient, FirmwareRouter]): Client and Router with both APIs.
        method_name (str): Public enable or disable method under test.
        firmware (str | None): Firmware version reported by the Router.
        uuid (str | None): Entry UUID passed to the method.
        expected_result (bool): Expected method result.
        expect_legacy_save (bool): Whether the legacy settings save and restart are expected.
        expect_toggle (bool): Whether the extended per-entry toggle is expected.
    """
    client, router = unbound_router
    router.firmware = firmware
    state = "1" if method_name == "enable_unbound_blocklist" else "0"

    assert await getattr(client, method_name)(uuid) is expected_result

    assert (UNBOUND_SET_PATH in router.post_paths) is expect_legacy_save
    assert (UNBOUND_RESTART_PATH in router.post_paths) is expect_legacy_save
    assert (f"{UNBOUND_TOGGLE_PATH}{state}" in router.post_paths) is expect_toggle


@pytest.mark.parametrize(
    ("firmware", "expected_level", "expected_text"),
    [
        pytest.param(None, logging.DEBUG, "firmware version is unavailable", id="missing"),
        pytest.param("not-a-version", logging.ERROR, "cannot be compared", id="not-comparable"),
    ],
)
async def test_unbound_unknown_firmware_is_logged_once_by_the_capability_rule(
    unbound_router: tuple[OPNsenseClient, FirmwareRouter],
    caplog: pytest.LogCaptureFixture,
    firmware: str | None,
    expected_level: int,
    expected_text: str,
) -> None:
    """Verify unknown firmware is reported once, at the level the capability rule sets.

    Args:
        unbound_router (tuple[OPNsenseClient, FirmwareRouter]): Client and Router with both APIs.
        caplog (pytest.LogCaptureFixture): Log capture fixture.
        firmware (str | None): Unknown firmware value reported by the Router.
        expected_level (int): Level the unknown-firmware message must use.
        expected_text (str): Text identifying the unknown-firmware reason.
    """
    client, router = unbound_router
    router.firmware = firmware
    caplog.set_level(logging.DEBUG, logger="aiopnsense")

    await client.get_unbound_blocklist()
    await client.enable_unbound_blocklist()
    await client.disable_unbound_blocklist()

    firmware_records = [
        record for record in caplog.records if "unknown-firmware answer" in record.getMessage()
    ]
    assert [record.levelno for record in firmware_records] == [expected_level]
    assert expected_text in firmware_records[0].getMessage()
