"""Tests for `aiopnsense.services`."""

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import install_fake_router, make_mock_session_client

ClientType = Callable[..., OPNsenseClient]
SERVICE_SEARCH_PATH = "/api/core/service/search"


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
        install_fake_router(
            client,
            _session,
            routes={
                ("GET", SERVICE_SEARCH_PATH): {
                    "rows": [
                        {"name": "svc1", "running": 1, "id": "svc1"},
                        "malformed",
                        123,
                        None,
                    ]
                }
            },
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

        # Preserve the structural separator between a service name and instance
        # while URL-encoding unsafe characters within each path component.
        assert await client._manage_service("restart", "openvpn/vpn instance") is True
        assert (
            client._safe_dict_post.await_args.args[0]
            == "/api/core/service/restart/openvpn/vpn%20instance"
        )

        # Instance service switches pass the composite identifier returned by
        # OPNsense and require the separator to remain part of the API route.
        assert await client.stop_service("openvpn/INSTANCE_ID") is True
        assert (
            client._safe_dict_post.await_args.args[0]
            == "/api/core/service/stop/openvpn/INSTANCE_ID"
        )

        # restart_service_if_running uses _get_service_running_state; test branch behavior
        restart_service_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(client, "restart_service", restart_service_mock, raising=False)
        client._get_service_running_state = AsyncMock(return_value=True)
        assert await client.restart_service_if_running("svc1") is True
        restart_service_mock.assert_awaited_once_with("svc1")

        restart_service_mock.reset_mock()
        client._get_service_running_state = AsyncMock(return_value=False)
        assert await client.restart_service_if_running("svc1") is True
        restart_service_mock.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_restart_service_if_running_matches_composite_service_identifier(
    make_client: ClientType,
) -> None:
    """Restart a running instance service identified by its composite name and ID.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates composite service lookup and restart behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        install_fake_router(
            client,
            _session,
            routes={
                ("GET", SERVICE_SEARCH_PATH): {
                    "rows": [{"name": "openvpn", "id": "openvpn/INSTANCE_ID", "running": 1}]
                }
            },
        )
        client._safe_dict_post = AsyncMock(return_value={"result": "ok"})

        assert await client._get_service_running_state("openvpn") is True
        assert await client._get_service_running_state("openvpn/INSTANCE_ID") is True
        assert await client.restart_service_if_running("openvpn/INSTANCE_ID") is True
        client._safe_dict_post.assert_awaited_once_with(
            "/api/core/service/restart/openvpn/INSTANCE_ID"
        )
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("route_value", "expected_requests"),
    [
        pytest.param(404, 1, id="missing-endpoint-is-cached"),
        pytest.param(403, 1, id="permission-denied-is-cached"),
        pytest.param(500, 4, id="transient-failure-is-retried"),
    ],
)
@pytest.mark.asyncio
async def test_service_unknown_state_fails_closed(
    make_client: ClientType, route_value: int, expected_requests: int
) -> None:
    """Unavailable service-search endpoint should produce fail-closed restart behavior.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        route_value (int): HTTP status the Router returns for the service search endpoint.
        expected_requests (int): Requests the Router should see across the four reads.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client, session, routes={("GET", SERVICE_SEARCH_PATH): route_value}
    )
    try:
        client.restart_service = AsyncMock(return_value=True)

        assert await client.get_services() == []
        assert await client._fetch_normalized_services(return_none_when_unavailable=True) is None
        assert await client.get_service_is_running("svc1") is False
        assert await client.restart_service_if_running("svc1") is False
        client.restart_service.assert_not_awaited()
        assert router.count("GET", SERVICE_SEARCH_PATH) == expected_requests
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_services_handles_non_list_rows_and_running_coercion(
    make_client: ClientType,
) -> None:
    """Service parsing should handle malformed rows and non-int running values.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates service-row normalization and status coercion.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        router = install_fake_router(
            client, _session, routes={("GET", SERVICE_SEARCH_PATH): {"rows": "bad-shape"}}
        )
        assert await client.get_services() == []

        router.routes[("GET", SERVICE_SEARCH_PATH)] = {
            "rows": [
                {"name": "svc1", "running": 1.5, "id": "svc1"},
                {"name": "svc2", "running": "1", "id": "svc2"},
            ]
        }
        services = await client.get_services()
        assert len(services) == 2
        assert services[0]["status"] is False
        assert services[1]["status"] is True

        assert await client.get_service_is_running("does-not-exist") is False
        assert await client._get_service_running_state("does-not-exist") is None
        client.restart_service = AsyncMock(return_value=True)
        assert await client.restart_service_if_running("does-not-exist") is False
        client.restart_service.assert_not_awaited()
    finally:
        await client.async_close()
