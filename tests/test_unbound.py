"""Tests for `aiopnsense.unbound`."""

from collections.abc import Callable
from copy import deepcopy
import logging
from typing import Any
from unittest.mock import AsyncMock, call

import aiohttp
import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


@pytest.mark.asyncio
async def test_uses_legacy_unbound_blocklist_returns_none_without_error_on_missing_firmware(
    make_client: ClientType,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify missing firmware returns ``None`` without logging an error.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Pytest log capture fixture.

    Returns:
        None: This test validates quiet handling for missing firmware values.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value=None)

        with caplog.at_level(logging.DEBUG):
            result = await client._uses_legacy_unbound_blocklist()

        assert result is None
        assert "Firmware version unavailable" in caplog.text
        assert "Error comparing firmware version" not in caplog.text
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_unbound_blocklist_returns_uuid_mapping(make_client) -> None:
    """The DNSBL search response should be normalized into a UUID-keyed mapping."""
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.8")
        client.is_endpoint_available = AsyncMock(return_value=True)
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
        client.is_endpoint_available.assert_awaited_once_with("/api/unbound/settings/search_dnsbl")
        client._safe_dict_get.assert_awaited_once_with("/api/unbound/settings/search_dnsbl")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("api_response", [{}, {"rows": []}, {"rows": "not-a-list"}, []])
async def test_get_unbound_blocklist_handles_empty_or_invalid_responses(
    make_client, api_response
) -> None:
    """Malformed or empty DNSBL responses should normalize to an empty mapping."""
    client = make_client()
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.8")
        client.is_endpoint_available = AsyncMock(return_value=True)
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
        client.is_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get = AsyncMock()

        result = await client.get_unbound_blocklist()

        assert result == {}
        client.is_endpoint_available.assert_awaited_once_with("/api/unbound/settings/search_dnsbl")
        client._safe_dict_get.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_unbound_blocklist_returns_legacy_payload_for_older_firmware(
    make_client: ClientType,
) -> None:
    """Older firmware should return the legacy DNSBL payload under the legacy key.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates legacy DNSBL payload normalization.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.7")
        client.is_endpoint_available = AsyncMock()
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
                        },
                        "lists": {
                            "list-a": {"selected": 1},
                            "list-b": {"selected": 1},
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
                "type": "ads",
                "lists": "list-a,list-b",
                "whitelists": "allow-a",
                "blocklists": "deny-a",
                "wildcards": "wild-a",
            }
        }
        client.is_endpoint_available.assert_not_awaited()
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


@pytest.mark.asyncio
@pytest.mark.parametrize("firmware_version", ["not-a-version", None])
async def test_get_unbound_blocklist_falls_back_to_legacy_endpoint_on_invalid_firmware(
    make_client: ClientType,
    firmware_version: str | None,
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """Invalid firmware versions should fall back to the legacy DNSBL endpoint.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        firmware_version (str | None): Firmware version returned by the mocked client.
        legacy_dnsbl_payload (dict[str, Any]): Shared legacy DNSBL payload fixture.

    Returns:
        None: This test validates the legacy fallback path when firmware comparison fails.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value=firmware_version)
        payload = deepcopy(legacy_dnsbl_payload)
        payload["unbound"]["dnsbl"]["enabled"] = "1"
        payload["unbound"]["dnsbl"]["type"] = {"ads": {"selected": 1}}
        client.is_endpoint_available = AsyncMock()
        client._safe_dict_get = AsyncMock(return_value=payload)

        result = await client.get_unbound_blocklist()

        assert result == {
            "legacy": {
                "enabled": "1",
                "safesearch": "0",
                "nxdomain": "1",
                "address": "0.0.0.0",
                "type": "ads",
                "lists": "",
                "whitelists": "",
                "blocklists": "",
                "wildcards": "",
            }
        }
        client.is_endpoint_available.assert_not_awaited()
        client._safe_dict_get.assert_awaited_once_with("/api/unbound/settings/get")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "uuid", "toggle_result", "dnsbl_result", "expected"),
    [
        (
            "enable_unbound_blocklist",
            "uuid1",
            {"result": "Enabled"},
            {"status": "OK applied"},
            True,
        ),
        (
            "disable_unbound_blocklist",
            "uuid1",
            {"result": "Disabled"},
            {"status": "OK applied"},
            True,
        ),
        ("enable_unbound_blocklist", None, {"result": "Enabled"}, {"status": "OK applied"}, False),
        (
            "enable_unbound_blocklist",
            "uuid1",
            {"result": "failed"},
            {"status": "OK applied"},
            False,
        ),
        ("enable_unbound_blocklist", "uuid1", {"result": "Enabled"}, {"status": "failed"}, False),
    ],
)
async def test_enable_disable_unbound_blocklist(
    make_client, method_name, uuid, toggle_result, dnsbl_result, expected
) -> None:
    """DNSBL toggles should require a UUID, a successful toggle response, and an OK apply status."""
    client = make_client()
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.8")
        client._safe_dict_post = AsyncMock(return_value=toggle_result)
        client._get = AsyncMock(return_value=dnsbl_result)

        result = await getattr(client, method_name)(uuid)

        assert result is expected
        if uuid:
            expected_state = "1" if method_name == "enable_unbound_blocklist" else "0"
            client._safe_dict_post.assert_awaited_once_with(
                f"/api/unbound/settings/toggle_dnsbl/{uuid}/{expected_state}"
            )
        else:
            client._safe_dict_post.assert_not_awaited()
            client._get.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "expected_enabled"),
    [
        ("enable_unbound_blocklist", "1"),
        ("disable_unbound_blocklist", "0"),
    ],
)
async def test_enable_disable_unbound_blocklist_legacy_for_older_firmware(
    make_client: ClientType,
    method_name: str,
    expected_enabled: str,
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """Older firmware should save legacy DNSBL settings and restart unbound.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        method_name (str): Client method under test.
        expected_enabled (str): Expected enabled value in the saved payload.

    Returns:
        None: This test validates the legacy DNSBL enable and disable workflow.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.7")
        payload = deepcopy(legacy_dnsbl_payload)
        payload["unbound"]["dnsbl"]["type"] = {"ads": {"selected": 1}}
        payload["unbound"]["dnsbl"]["lists"] = {"list-a": {"selected": 1}}
        client._safe_dict_get = AsyncMock(return_value=payload)
        client._post = AsyncMock(side_effect=[{"result": "saved"}, {"response": "OK"}])
        client._get = AsyncMock(return_value={"status": "OK applied"})

        result = await getattr(client, method_name)()

        assert result is True
        client._safe_dict_get.assert_awaited_once_with("/api/unbound/settings/get")
        assert client._post.await_args_list == [
            call(
                "/api/unbound/settings/set",
                payload={
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
                },
            ),
            call("/api/unbound/service/restart"),
        ]
        client._get.assert_awaited_once_with("/api/unbound/service/dnsbl")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name",
    ["enable_unbound_blocklist", "disable_unbound_blocklist"],
)
async def test_legacy_unbound_blocklist_rejects_uuid(
    make_client: ClientType,
    method_name: str,
) -> None:
    """Verify legacy Unbound blocklists reject UUID-based operations.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        method_name (str): Client method under test.

    Returns:
        None: This test validates that UUIDs are rejected for legacy Unbound blocklists.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.7")
        client._set_unbound_blocklist_legacy = AsyncMock()

        result = await getattr(client, method_name)("uuid1")

        assert result is False
        client._set_unbound_blocklist_legacy.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_enable_unbound_blocklist_legacy_returns_false_when_state_cannot_be_loaded(
    make_client: ClientType,
) -> None:
    """Verify legacy DNSBL updates fail closed when current state cannot be loaded.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates fail-closed legacy DNSBL writes.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.7")
        client._safe_dict_get = AsyncMock(return_value={"unbound": {"dnsbl": []}})
        client._post = AsyncMock()
        client._get = AsyncMock()

        result = await client.enable_unbound_blocklist()

        assert result is False
        client._post.assert_not_awaited()
        client._get.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_call", ["get", "post"])
async def test_enable_unbound_blocklist_legacy_returns_false_on_apply_exception(
    make_client: ClientType,
    legacy_dnsbl_payload: dict[str, Any],
    failing_call: str,
) -> None:
    """Verify legacy DNSBL updates fail closed when apply or restart raises.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        legacy_dnsbl_payload (dict[str, Any]): Shared legacy DNSBL payload fixture.
        failing_call (str): Legacy apply step that raises an exception.

    Returns:
        None: This test validates exception handling for legacy DNSBL writes.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.7")
        payload = deepcopy(legacy_dnsbl_payload)
        client._safe_dict_get = AsyncMock(return_value=payload)
        if failing_call == "get":
            client._post = AsyncMock(return_value={"result": "saved"})
            client._get = AsyncMock(side_effect=aiohttp.ClientError("boom"))
        else:
            client._post = AsyncMock(side_effect=[{"result": "saved"}, aiohttp.ClientError("boom")])
            client._get = AsyncMock(return_value={"status": "OK"})

        result = await client.enable_unbound_blocklist()

        assert result is False
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("save_response", [{}, {"result": "failed"}, []])
async def test_enable_unbound_blocklist_legacy_does_not_apply_or_restart_after_failed_save(
    make_client: ClientType,
    save_response: dict[str, Any] | list[Any],
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """Verify legacy DNSBL updates stop before apply and restart on failed save.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        save_response (dict[str, Any] | list[Any]): Mock response returned by the legacy save request.

    Returns:
        None: This test validates that failed legacy saves do not trigger apply or restart calls.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.7")
        payload = deepcopy(legacy_dnsbl_payload)
        client._safe_dict_get = AsyncMock(return_value=payload)
        client._post = AsyncMock(return_value=save_response)
        client._get = AsyncMock()

        result = await client.enable_unbound_blocklist()

        assert result is False
        client._post.assert_awaited_once_with(
            "/api/unbound/settings/set",
            payload={
                "unbound": {
                    "dnsbl": {
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
            },
        )
        client._get.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("dnsbl_response", [{"status": None}, {"status": 1}, []])
async def test_enable_unbound_blocklist_legacy_returns_false_for_malformed_apply_status(
    make_client: ClientType,
    dnsbl_response: dict[str, Any] | list[Any],
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """Verify legacy DNSBL updates fail closed on malformed apply responses.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        dnsbl_response (dict[str, Any] | list[Any]): Mock response returned by the DNSBL apply request.

    Returns:
        None: This test validates malformed apply-response handling for legacy DNSBL writes.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.7")
        payload = deepcopy(legacy_dnsbl_payload)
        client._safe_dict_get = AsyncMock(return_value=payload)
        client._post = AsyncMock(side_effect=[{"result": "saved"}, {"response": "OK"}])
        client._get = AsyncMock(return_value=dnsbl_response)

        result = await client.enable_unbound_blocklist()

        assert result is False
        assert client._post.await_args_list == [
            call(
                "/api/unbound/settings/set",
                payload={
                    "unbound": {
                        "dnsbl": {
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
                },
            ),
        ]
        client._get.assert_awaited_once_with("/api/unbound/service/dnsbl")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("firmware_version", ["invalid", None])
async def test_enable_unbound_blocklist_uses_legacy_fallback_when_firmware_is_invalid(
    make_client: ClientType,
    firmware_version: str | None,
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """Missing comparable firmware data should use the legacy path when no UUID is supplied.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        firmware_version (str | None): Firmware version returned by the mocked client.

    Returns:
        None: This test validates the invalid-firmware legacy fallback.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value=firmware_version)
        payload = deepcopy(legacy_dnsbl_payload)
        client._safe_dict_get = AsyncMock(return_value=payload)
        client._post = AsyncMock(side_effect=[{"result": "saved"}, {"response": "OK"}])
        client._get = AsyncMock(return_value={"status": "OK"})
        client._safe_dict_post = AsyncMock()

        result = await client.enable_unbound_blocklist()

        assert result is True
        client._safe_dict_post.assert_not_awaited()
        client._safe_dict_get.assert_awaited_once_with("/api/unbound/settings/get")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("firmware_version", ["invalid", None])
async def test_enable_unbound_blocklist_uses_extended_fallback_when_firmware_is_invalid_and_uuid_is_supplied(
    make_client: ClientType,
    firmware_version: str | None,
) -> None:
    """Verify invalid firmware falls back to extended DNSBL toggles when UUID is supplied.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        firmware_version (str | None): Firmware version returned by the mocked client.

    Returns:
        None: This test validates the invalid-firmware extended fallback for enable.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value=firmware_version)
        client._safe_dict_post = AsyncMock(return_value={"result": "Enabled"})
        client._get = AsyncMock(return_value={"status": "OK"})
        client._safe_dict_get = AsyncMock()

        result = await client.enable_unbound_blocklist("uuid1")

        assert result is True
        client._safe_dict_post.assert_awaited_once_with(
            "/api/unbound/settings/toggle_dnsbl/uuid1/1"
        )
        client._safe_dict_get.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("firmware_version", "uuid", "expected_enabled", "expected_toggle_calls", "expected_result"),
    [
        ("invalid", None, "0", 0, True),
        (None, None, "0", 0, True),
        ("invalid", "", None, 0, False),
        (None, "", None, 0, False),
        ("invalid", "uuid1", None, 1, True),
        (None, "uuid1", None, 1, True),
    ],
)
async def test_disable_unbound_blocklist_uses_expected_invalid_firmware_fallback(
    make_client: ClientType,
    firmware_version: str | None,
    uuid: str | None,
    expected_enabled: str | None,
    expected_toggle_calls: int,
    expected_result: bool,
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """Verify disable fallback selection when firmware cannot be compared.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        firmware_version (str | None): Firmware version returned by the mocked client.
        uuid (str | None): Optional UUID supplied to the client method.
        expected_enabled (str | None): Expected legacy ``enabled`` value when the legacy path is used.
        expected_toggle_calls (int): Expected number of calls to the extended toggle endpoint.
        expected_result (bool): Expected method result for the supplied fallback case.

    Returns:
        None: This test validates invalid-firmware fallback selection for disable.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value=firmware_version)
        client._safe_dict_post = AsyncMock(return_value={"result": "Disabled"})
        client._get = AsyncMock(return_value={"status": "OK"})
        client._post = AsyncMock(side_effect=[{"result": "saved"}, {"response": "OK"}])
        payload = deepcopy(legacy_dnsbl_payload)
        payload["unbound"]["dnsbl"]["enabled"] = "1"
        client._safe_dict_get = AsyncMock(return_value=payload)

        result = await client.disable_unbound_blocklist(uuid)

        assert result is expected_result
        assert client._safe_dict_post.await_count == expected_toggle_calls
        if uuid is None:
            client._safe_dict_get.assert_awaited_once_with("/api/unbound/settings/get")
            assert client._post.await_args_list == [
                call(
                    "/api/unbound/settings/set",
                    payload={
                        "unbound": {
                            "dnsbl": {
                                "enabled": expected_enabled,
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
                    },
                ),
                call("/api/unbound/service/restart"),
            ]
        else:
            client._safe_dict_get.assert_not_awaited()
            if expected_toggle_calls == 0:
                client._safe_dict_post.assert_not_awaited()
            else:
                client._safe_dict_post.assert_awaited_once_with(
                    f"/api/unbound/settings/toggle_dnsbl/{uuid}/0"
                )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_toggle_unbound_blocklist_handles_apply_exception(
    make_client: ClientType,
) -> None:
    """Verify DNSBL toggle returns ``False`` when apply endpoint raises a client error.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates exception handling for DNSBL apply requests.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.8")
        client._safe_dict_post = AsyncMock(return_value={"result": "Enabled"})
        client._get = AsyncMock(side_effect=aiohttp.ClientError("boom"))

        result = await client.enable_unbound_blocklist("uuid1")

        assert result is False
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_toggle_unbound_blocklist_uses_expected_endpoints(
    make_client: ClientType,
) -> None:
    """Verify DNSBL toggle helper calls expected toggle and apply endpoints.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates endpoint path selection for DNSBL toggles.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.8")
        client._safe_dict_post = AsyncMock(return_value={"result": "Disabled"})
        client._get = AsyncMock(return_value={"status": "OK"})

        result = await client.disable_unbound_blocklist("uuid1")

        assert result is True
        client._safe_dict_post.assert_awaited_once_with(
            "/api/unbound/settings/toggle_dnsbl/uuid1/0"
        )
        client._get.assert_awaited_once_with("/api/unbound/service/dnsbl")
    finally:
        await client.async_close()
