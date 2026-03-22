"""Tests for `aiopnsense.services`."""

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


@pytest.mark.asyncio
async def test_service_management_and_get_services(make_client: ClientType) -> None:
    """Verify service listing, state checks, and service control helpers.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates service normalization and control-path behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {"name": "svc1", "running": 1, "id": "svc1"},
                    "malformed",
                    123,
                    None,
                ]
            }
        )
        services = await client.get_services()
        assert len(services) == 1
        assert services[0]["status"] is True
        assert await client.get_service_is_running("svc1") is True

        # manage service via _safe_dict_post
        client._safe_dict_post = AsyncMock(return_value={"result": "ok"})
        ok = await client._manage_service("start", "svc1")
        assert ok is True
        assert await client.start_service("svc1") is True
        assert await client.stop_service("svc1") is True
        assert await client.restart_service("svc1") is True
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_manage_service_and_restart_if_running(
    monkeypatch: pytest.MonkeyPatch,
    make_client: ClientType,
) -> None:
    """Verify service-management helper behavior and restart-if-running branching.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture used to patch restart helper behavior.
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates service-management endpoint and restart control flow.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        # _manage_service should return False when service empty
        assert await client._manage_service("start", "") is False

        # when _safe_dict_post returns ok result, manage_service returns True
        client._safe_dict_post = AsyncMock(return_value={"result": "ok"})
        assert await client._manage_service("start", "svc1") is True
        assert client._safe_dict_post.await_args.args[0] == "/api/core/service/start/svc1"

        # service identifiers are URL-encoded before endpoint construction
        assert await client._manage_service("restart", "svc /name") is True
        assert (
            client._safe_dict_post.await_args.args[0] == "/api/core/service/restart/svc%20%2Fname"
        )

        # get_service_is_running uses get_services; test restart_service_if_running branches
        restart_service_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(client, "restart_service", restart_service_mock, raising=False)
        client.get_service_is_running = AsyncMock(return_value=True)
        assert await client.restart_service_if_running("svc1") is True
        restart_service_mock.assert_awaited_once_with("svc1")

        restart_service_mock.reset_mock()
        client.get_service_is_running = AsyncMock(return_value=False)
        assert await client.restart_service_if_running("svc1") is True
        restart_service_mock.assert_not_awaited()
    finally:
        await client.async_close()
