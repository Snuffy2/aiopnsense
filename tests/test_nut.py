"""Tests for `aiopnsense.nut`."""

from collections.abc import Callable
from typing import Any

import pytest

from aiopnsense import OPNsenseClient, OPNsenseConnectionError, OPNsensePrivilegeMissing
from tests.conftest import install_fake_router, make_mock_session_client

ClientType = Callable[..., OPNsenseClient]
NUT_PATH = "/api/nut/diagnostics/upsstatus"


async def _status_for(make_client: ClientType, payload: Any) -> tuple[dict[str, Any], int]:
    """Return the UPS status and request count for a Router answering with ``payload``.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        payload (Any): JSON body the Router returns for the UPS status endpoint.

    Returns:
        tuple[dict[str, Any], int]: Public result and number of requests the Router saw.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): payload})
    try:
        return await client.get_nut_ups_status(), router.count("GET", NUT_PATH)
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    "payload, expected",
    [
        pytest.param(
            {"status": {"ups.status": "OL", "battery.charge": "100", "ups.load": "12"}},
            {"status": {"ups.status": "OL", "battery.charge": "100", "ups.load": "12"}},
            id="preserves-nested-status",
        ),
        pytest.param(
            {
                "response": (
                    "battery.charge: 100\n"
                    "ups.status: OL\n"
                    "input.L1-N.voltage: 120\n"
                    "input.L1-L2.voltage: 240\n"
                )
            },
            {
                "response": (
                    "battery.charge: 100\n"
                    "ups.status: OL\n"
                    "input.L1-N.voltage: 120\n"
                    "input.L1-L2.voltage: 240\n"
                ),
                "status": {
                    "battery.charge": "100",
                    "ups.status": "OL",
                    "input.L1-N.voltage": "120",
                    "input.L1-L2.voltage": "240",
                },
            },
            id="parses-raw-response",
        ),
        pytest.param(
            {
                "status": {"ups.status": "OL"},
                "request_id": "abc-123",
                "response": "ups.status: OB",
            },
            {
                "status": {"ups.status": "OL"},
                "request_id": "abc-123",
                "response": "ups.status: OB",
            },
            id="prefers-mapped-status",
        ),
        pytest.param(
            {"status": {}, "response": "ups.status: OL"},
            {"response": "ups.status: OL", "status": {"ups.status": "OL"}},
            id="raw-response-when-status-empty",
        ),
        pytest.param(
            {
                "response": (
                    "battery.charge: 100\n  \nError: UPS unavailable\n"
                    "ups.message: on battery: replace battery\nthis-line-is-invalid\n"
                    "ups.load: 12"
                )
            },
            {
                "response": (
                    "battery.charge: 100\n  \nError: UPS unavailable\n"
                    "ups.message: on battery: replace battery\nthis-line-is-invalid\n"
                    "ups.load: 12"
                ),
                "status": {
                    "battery.charge": "100",
                    "ups.message": "on battery: replace battery",
                    "ups.load": "12",
                },
            },
            id="colon-in-value-and-invalid-lines",
        ),
        pytest.param(None, {}, id="null-body"),
        pytest.param({}, {}, id="empty-body"),
        pytest.param({"response": None}, {"response": None}, id="null-response"),
        pytest.param({"response": 123}, {"response": 123}, id="numeric-response"),
        pytest.param(
            {"status": "not-a-mapping"}, {"status": "not-a-mapping"}, id="status-not-mapping"
        ),
        pytest.param(
            {"response": "Error: UPS unavailable", "request_id": "abc"},
            {"response": "Error: UPS unavailable", "request_id": "abc"},
            id="error-response-has-no-status",
        ),
    ],
)
@pytest.mark.asyncio
async def test_get_nut_ups_status_normalizes_payloads_with_one_request(
    make_client: ClientType, payload: Any, expected: dict[str, Any]
) -> None:
    """NUT UPS status normalizes the Router payload using a single request.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        payload (Any): JSON body returned by the Router.
        expected (dict[str, Any]): Normalized status the public method should return.
    """
    status, requests = await _status_for(make_client, payload)

    assert status == expected
    assert requests == 1


@pytest.mark.parametrize(
    "route_value",
    [
        pytest.param(404, id="missing-plugin"),
        pytest.param(403, id="permission-denied"),
        pytest.param(500, id="server-error"),
        pytest.param(TimeoutError("timed out"), id="timeout"),
    ],
)
@pytest.mark.asyncio
async def test_get_nut_ups_status_returns_empty_dict_when_endpoint_unavailable(
    make_client: ClientType, route_value: Any
) -> None:
    """NUT UPS status fails closed to an empty mapping for absent or failing endpoints.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        route_value (Any): Response the Router gives for the UPS status endpoint.
    """
    status, requests = await _status_for(make_client, route_value)

    assert status == {}
    assert requests == 1


@pytest.mark.parametrize(
    "route_value, expected_error",
    [
        pytest.param(403, OPNsensePrivilegeMissing, id="permission-denied"),
        pytest.param(500, OPNsenseConnectionError, id="server-error"),
        pytest.param(TimeoutError("timed out"), OPNsenseConnectionError, id="timeout"),
    ],
)
@pytest.mark.asyncio
async def test_get_nut_ups_status_raises_in_throw_mode(
    make_client: ClientType, route_value: Any, expected_error: type[Exception]
) -> None:
    """NUT UPS status raises a public error for permission and transient failures in throw mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        route_value (Any): Response the Router gives for the UPS status endpoint.
        expected_error (type[Exception]): Public exception type expected from the call.
    """
    client, session = make_mock_session_client(make_client)
    install_fake_router(client, session, routes={("GET", NUT_PATH): route_value})
    client.toggle_throwing_errors(True)
    try:
        with pytest.raises(expected_error):
            await client.get_nut_ups_status()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_nut_ups_status_missing_plugin_does_not_raise_in_throw_mode(
    make_client: ClientType,
) -> None:
    """A missing NUT plugin (404) is an empty result, not an error, even in throw mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    install_fake_router(client, session, routes={("GET", NUT_PATH): 404})
    client.toggle_throwing_errors(True)
    try:
        assert await client.get_nut_ups_status() == {}
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    "payload, expected",
    [
        ("not-a-mapping", {}),
        ({"response": 123, "status": {}}, {"response": 123, "status": {}}),
        (
            {"response": "invalid-status-line-without-colon"},
            {"response": "invalid-status-line-without-colon"},
        ),
    ],
)
def test_normalize_nut_ups_status_payload_fallback_branches(
    payload: Any,
    expected: dict[str, Any],
) -> None:
    """Cover fallback return values for NUT payload normalization.

    Args:
        payload (Any): Input payload passed to the normalization helper.
        expected (dict[str, Any]): Expected normalized payload for the branch.
    """
    assert OPNsenseClient._normalize_nut_ups_status_payload(payload) == expected
