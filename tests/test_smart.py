"""Tests for `aiopnsense.smart`."""

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


@pytest.mark.asyncio
async def test_get_smart_normalizes_device_names(make_client: ClientType) -> None:
    """SMART list queries should normalize bare device names into mappings.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates normalization of SMART device name rows.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.is_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_post = AsyncMock(return_value={"devices": ["nvme0", "ada0"]})

        smart_devices = await client.get_smart(details=False)

        assert smart_devices == [{"device": "nvme0"}, {"device": "ada0"}]
        client._safe_dict_post.assert_awaited_once_with("/api/smart/service/list")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_details_preserves_mapping_rows(make_client: ClientType) -> None:
    """Detailed SMART list queries should preserve mapping rows and infer devices.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates detailed SMART row normalization.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.is_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_post = AsyncMock(
            return_value={
                "devices": [
                    {"dev": "nvme0", "status": "PASSED"},
                    {"device": "ada0", "status": "FAILED"},
                    "ignored",
                ]
            }
        )

        smart_devices = await client.get_smart(details=True)

        assert smart_devices == [
            {"dev": "nvme0", "status": "PASSED", "device": "nvme0"},
            {"device": "ada0", "status": "FAILED"},
        ]
        client._safe_dict_post.assert_awaited_once_with("/api/smart/service/list/1")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_fails_closed_when_endpoint_is_unavailable(make_client: ClientType) -> None:
    """SMART list queries should fail closed when the plugin endpoint is unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates unavailable-endpoint behavior for SMART list queries.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.is_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_post = AsyncMock()

        assert await client.get_smart() == []
        client._safe_dict_post.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_info_returns_json_output(make_client: ClientType) -> None:
    """SMART info queries should request JSON output and return the decoded payload.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates SMART info payload construction and response handling.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.is_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_post = AsyncMock(
            return_value={"output": {"smart_status": "PASSED", "temperature": 35}}
        )

        smart_info = await client.get_smart_info("nvme0")

        assert smart_info == {"smart_status": "PASSED", "temperature": 35}
        client._safe_dict_post.assert_awaited_once_with(
            "/api/smart/service/info",
            {"device": "nvme0", "type": "a", "json": True},
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_info_wraps_non_mapping_output(make_client: ClientType) -> None:
    """SMART info queries should preserve non-mapping outputs in a stable wrapper.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates fallback handling for non-mapping SMART info payloads.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.is_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_post = AsyncMock(return_value={"output": ["line1", "line2"]})

        smart_info = await client.get_smart_info("nvme0", info_type="H")

        assert smart_info == {"output": ["line1", "line2"]}
        client._safe_dict_post.assert_awaited_once_with(
            "/api/smart/service/info",
            {"device": "nvme0", "type": "H", "json": True},
        )
    finally:
        await client.async_close()
