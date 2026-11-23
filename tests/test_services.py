"""Tests for `aiopnsense.services`."""

from collections.abc import Callable
from functools import partial

import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import install_fake_router, make_mock_session_client

ClientType = Callable[..., OPNsenseClient]

SERVICE_SEARCH_PATH = "/api/core/service/search"
SERVICE_PATH_PREFIX = "/api/core/service"


@pytest.mark.asyncio
async def test_service_management_and_get_services(make_client: ClientType) -> None:
    """Verify service listing, state checks, and service control helpers.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates service normalization and control-path behavior.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client,
        session,
        routes={
            ("GET", SERVICE_SEARCH_PATH): {
                "rows": [
                    {"name": "svc1", "running": 1, "id": "svc1"},
                    "malformed",
                    123,
                    None,
                ]
            },
            ("POST", f"{SERVICE_PATH_PREFIX}/start/svc1"): {"result": "ok"},
            ("POST", f"{SERVICE_PATH_PREFIX}/stop/svc1"): {"result": "ok"},
            ("POST", f"{SERVICE_PATH_PREFIX}/restart/svc1"): {"result": "ok"},
        },
        explicit_routes=True,
    )

    services = await client.get_services()
    assert len(services) == 1
    assert services[0]["status"] is True
    assert await client.get_service_is_running("svc1") is True

    assert await client.start_service("svc1") is True
    assert await client.stop_service("svc1") is True
    assert await client.restart_service("svc1") is True
    for action in ("start", "stop", "restart"):
        assert router.count("POST", f"{SERVICE_PATH_PREFIX}/{action}/svc1") == 1


@pytest.mark.asyncio
async def test_manage_service_and_restart_if_running(make_client: ClientType) -> None:
    """Verify service-management helper behavior and restart-if-running branching.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates service-management endpoint and restart control flow.
    """
    client, session = make_mock_session_client(make_client)
    restart_path = f"{SERVICE_PATH_PREFIX}/restart/svc1"
    router = install_fake_router(
        client,
        session,
        routes={
            # Preserve the structural separator between a service name and instance
            # while URL-encoding unsafe characters within each path component.
            ("POST", f"{SERVICE_PATH_PREFIX}/restart/openvpn/vpn%20instance"): {"result": "ok"},
            # Instance service switches pass the composite identifier returned by
            # OPNsense and require the separator to remain part of the API route.
            ("POST", f"{SERVICE_PATH_PREFIX}/stop/openvpn/INSTANCE_ID"): {"result": "ok"},
            ("GET", SERVICE_SEARCH_PATH): {"rows": [{"name": "svc1", "id": "svc1", "running": 1}]},
            ("POST", restart_path): {"result": "ok"},
        },
        explicit_routes=True,
    )

    # An empty service name sends no request.
    assert await client.start_service("") is False
    assert router.count() == 0

    assert await client.restart_service("openvpn/vpn instance") is True
    assert router.count("POST", f"{SERVICE_PATH_PREFIX}/restart/openvpn/vpn%20instance") == 1

    assert await client.stop_service("openvpn/INSTANCE_ID") is True
    assert router.count("POST", f"{SERVICE_PATH_PREFIX}/stop/openvpn/INSTANCE_ID") == 1

    # A running service is restarted.
    assert await client.restart_service_if_running("svc1") is True
    assert router.count("POST", restart_path) == 1

    # A stopped service succeeds without a restart request.
    router.routes[("GET", SERVICE_SEARCH_PATH)] = {
        "rows": [{"name": "svc1", "id": "svc1", "running": 0}]
    }
    assert await client.restart_service_if_running("svc1") is True
    assert router.count("POST", restart_path) == 1


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
    client, session = make_mock_session_client(make_client)
    restart_path = f"{SERVICE_PATH_PREFIX}/restart/openvpn/INSTANCE_ID"
    router = install_fake_router(
        client,
        session,
        routes={
            ("GET", SERVICE_SEARCH_PATH): {
                "rows": [{"name": "openvpn", "id": "openvpn/INSTANCE_ID", "running": 1}]
            },
            ("POST", restart_path): {"result": "ok"},
        },
        explicit_routes=True,
    )

    assert await client.get_service_is_running("openvpn") is True
    assert await client.get_service_is_running("openvpn/INSTANCE_ID") is True
    assert await client.restart_service_if_running("openvpn/INSTANCE_ID") is True
    assert router.count("POST", restart_path) == 1
    assert router.count("POST") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_service_unknown_state_fails_closed(
    make_client: ClientType, throw_errors: bool
) -> None:
    """Unavailable service-search endpoint should produce fail-closed restart behavior.

    A missing endpoint is absent in either ``throw_errors`` mode, and the cached
    absence spares the later calls a request.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Whether the client raises instead of returning empty values.

    Returns:
        None: This test validates unknown service-state handling when endpoint probing fails.
    """
    client, session = make_mock_session_client(partial(make_client, throw_errors=throw_errors))
    router = install_fake_router(client, session, explicit_routes=True)

    assert await client.get_services() == []
    assert await client.get_service_is_running("svc1") is False
    assert await client.restart_service_if_running("svc1") is False

    assert router.count("GET", SERVICE_SEARCH_PATH) == 1
    assert router.count("POST") == 0


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
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client,
        session,
        routes={("GET", SERVICE_SEARCH_PATH): {"rows": "bad-shape"}},
        explicit_routes=True,
    )
    assert await client.get_services() == []

    router.routes[("GET", SERVICE_SEARCH_PATH)] = {
        "rows": [
            {"name": "svc1", "running": object(), "id": "svc1"},
            {"name": "svc2", "running": "1", "id": "svc2"},
        ]
    }
    services = await client.get_services()
    assert len(services) == 2
    assert services[0]["status"] is False
    assert services[1]["status"] is True

    assert await client.get_service_is_running("does-not-exist") is False
    # An unknown service is not a stopped one, so no restart request is sent.
    assert await client.restart_service_if_running("does-not-exist") is False
    assert router.count("POST") == 0
