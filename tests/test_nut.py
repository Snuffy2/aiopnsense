"""Tests for `aiopnsense.nut`."""

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


@pytest.mark.asyncio
async def test_get_nut_ups_status_returns_status_payload(make_client: ClientType) -> None:
    """NUT UPS status queries should return the decoded diagnostics payload.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates NUT UPS status payload handling.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={
                "status": {
                    "ups.status": "OL",
                    "battery.charge": "100",
                    "ups.load": "12",
                }
            }
        )

        nut_status = await client.get_nut_ups_status()

        assert nut_status == {
            "status": {
                "ups.status": "OL",
                "battery.charge": "100",
                "ups.load": "12",
            }
        }
        client._is_get_endpoint_available.assert_awaited_once_with("/api/nut/diagnostics/upsstatus")
        client._safe_dict_get.assert_awaited_once_with("/api/nut/diagnostics/upsstatus")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_nut_ups_status_returns_empty_dict_when_endpoint_unavailable(
    make_client: ClientType,
) -> None:
    """NUT UPS status queries should fail closed when the endpoint is unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates fail-closed behavior for unavailable NUT APIs.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get = AsyncMock(return_value={})

        nut_status = await client.get_nut_ups_status()

        assert nut_status == {}
        client._is_get_endpoint_available.assert_awaited_once_with("/api/nut/diagnostics/upsstatus")
        client._safe_dict_get.assert_not_awaited()
    finally:
        await client.async_close()
