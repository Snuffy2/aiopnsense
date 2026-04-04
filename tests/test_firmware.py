"""Tests for `aiopnsense.firmware` behaviors."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.conftest import make_mock_session_client


@pytest.mark.asyncio
async def test_get_host_firmware_version_and_fallback(
    make_client: Callable[..., Any],
) -> None:
    """Firmware version lookup should prefer semver and fall back to product series."""
    client, session = make_mock_session_client(make_client)

    client.is_endpoint_available = AsyncMock(return_value=True)
    client._safe_dict_get = AsyncMock(return_value={"product": {"product_version": "25.8.0"}})
    firmware = await client.get_host_firmware_version()
    assert firmware == "25.8.0"
    client.is_endpoint_available.assert_awaited_once_with("/api/core/firmware/status")
    await client.async_close()

    fallback_client = make_client(session=session)
    fallback_client.is_endpoint_available = AsyncMock(return_value=True)
    fallback_client._safe_dict_get = AsyncMock(
        return_value={"product": {"product_version": "weird", "product_series": "seriesX"}}
    )
    fallback = await fallback_client.get_host_firmware_version()
    assert fallback == "seriesX"
    fallback_client.is_endpoint_available.assert_awaited_once_with("/api/core/firmware/status")
    await fallback_client.async_close()


@pytest.mark.asyncio
async def test_get_firmware_update_info_triggers_check_when_status_is_incomplete(
    make_client: Callable[..., Any],
) -> None:
    """Missing firmware status details should trigger a background firmware check."""
    client = make_client()
    try:
        client.is_endpoint_available = AsyncMock(return_value=True)
        status = {
            "product": {"product_version": "1.0", "product_latest": "2.0", "product_check": {}}
        }
        client._safe_dict_get = AsyncMock(return_value=status)
        client._post = AsyncMock(return_value={})

        result = await client.get_firmware_update_info()

        assert result == status
        client._post.assert_awaited_once_with("/api/core/firmware/check")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_firmware_update_info_triggers_check_when_last_check_is_stale(
    make_client: Callable[..., Any],
) -> None:
    """A stale `last_check` should trigger a firmware refresh."""
    client, _ = make_mock_session_client(make_client)
    try:
        client.is_endpoint_available = AsyncMock(return_value=True)
        status = {
            "product": {
                "product_version": "1.0.0",
                "product_latest": "1.0.0",
                "product_check": {"status": "ok"},
            },
            "last_check": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
        }
        client._safe_dict_get = AsyncMock(return_value=status)
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)
        client._post = AsyncMock(return_value={})

        await client.get_firmware_update_info()

        client._post.assert_awaited_once_with("/api/core/firmware/check")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_firmware_update_info_does_not_trigger_check_for_recent_healthy_status(
    make_client: Callable[..., Any],
) -> None:
    """A complete, recent firmware status should not trigger a refresh."""
    client = make_client()
    try:
        client.is_endpoint_available = AsyncMock(return_value=True)
        status = {
            "product": {
                "product_version": "26.1.1",
                "product_latest": "26.1.1",
                "product_check": {"status": "ok"},
            },
            "status_msg": "There are no updates available on the selected mirror.",
            "last_check": datetime.now(UTC).isoformat(),
        }
        client._safe_dict_get = AsyncMock(return_value=status)
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)
        client._post = AsyncMock(return_value={})

        result = await client.get_firmware_update_info()

        assert result == status
        client._post.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upgrade_type", "expected_path"),
    [("update", "/api/core/firmware/update"), ("upgrade", "/api/core/firmware/upgrade")],
)
async def test_upgrade_firmware_calls_expected_endpoint(
    make_client: Callable[..., Any], upgrade_type: str, expected_path: str
) -> None:
    """Supported upgrade types should call the matching firmware endpoint."""
    client = make_client()
    try:
        client._firmware_version = "26.1.1"
        client._safe_dict_post = AsyncMock(return_value={"status": "ok"})

        result = await client.upgrade_firmware(upgrade_type)

        assert result == {"status": "ok"}
        assert client._firmware_version is None
        client._safe_dict_post.assert_awaited_once_with(expected_path)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_upgrade_firmware_rejects_unknown_type(make_client: Callable[..., Any]) -> None:
    """Unknown firmware upgrade types should return `None` without calling the API."""
    client = make_client()
    try:
        client._safe_dict_post = AsyncMock()

        result = await client.upgrade_firmware("invalid")

        assert result is None
        client._safe_dict_post.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_upgrade_status_and_changelog(make_client: Callable[..., Any]) -> None:
    """Upgrade status and changelog helpers should proxy the expected endpoints."""
    client = make_client()
    try:
        client.is_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(return_value={"status": "running"})
        client._safe_dict_post = AsyncMock(return_value={"changelog": "..."})

        status = await client.upgrade_status()
        changelog = await client.firmware_changelog("26.1.1")

        assert status == {"status": "running"}
        assert changelog == {"changelog": "..."}
        client._safe_dict_get.assert_awaited_once_with("/api/core/firmware/upgradestatus")
        client._safe_dict_post.assert_awaited_once_with("/api/core/firmware/changelog/26.1.1")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_firmware_helpers_return_defaults_when_endpoints_unavailable(
    make_client: Callable[..., Any],
) -> None:
    """Firmware helpers should fail closed when status endpoints are unavailable.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates default-return behavior for unavailable firmware endpoints.
    """
    client = make_client()
    try:
        client.is_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get = AsyncMock()

        await client._store_host_firmware_version()
        host_version = await client.get_host_firmware_version()
        update_info = await client.get_firmware_update_info()
        upgrade_status = await client.upgrade_status()

        assert client._firmware_version is None
        assert host_version is None
        assert update_info == {}
        assert upgrade_status == {}
        client._safe_dict_get.assert_not_awaited()
    finally:
        await client.async_close()
