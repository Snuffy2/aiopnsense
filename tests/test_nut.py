"""Tests for `aiopnsense.nut`."""

from collections.abc import Callable
from functools import partial
from typing import Any

import aiohttp
import pytest

from aiopnsense import OPNsenseClient
from aiopnsense.exceptions import OPNsenseConnectionError, OPNsensePrivilegeMissing
from tests.conftest import install_fake_router, make_mock_session_client

ClientType = Callable[..., OPNsenseClient]

NUT_PATH = "/api/nut/diagnostics/upsstatus"


@pytest.mark.asyncio
async def test_get_nut_ups_status_preserves_nested_status_payload(
    make_client: ClientType,
) -> None:
    """NUT UPS status should preserve already structured status payloads.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates compatibility with already mapped responses.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client,
        session,
        routes={
            ("GET", NUT_PATH): {
                "status": {
                    "ups.status": "OL",
                    "battery.charge": "100",
                    "ups.load": "12",
                }
            }
        },
        explicit_routes=True,
    )

    nut_status = await client.get_nut_ups_status()

    assert nut_status == {
        "status": {
            "ups.status": "OL",
            "battery.charge": "100",
            "ups.load": "12",
        }
    }
    assert router.count("GET", NUT_PATH) == 1


@pytest.mark.asyncio
async def test_get_nut_ups_status_parses_raw_status_response(make_client: ClientType) -> None:
    """NUT UPS status should parse raw colon-separated text responses.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates parsing of plugin string responses into status mappings.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client,
        session,
        routes={
            ("GET", NUT_PATH): {
                "response": (
                    "battery.charge: 100\n"
                    "ups.status: OL\n"
                    "input.L1-N.voltage: 120\n"
                    "input.L1-L2.voltage: 240\n"
                )
            }
        },
        explicit_routes=True,
    )

    nut_status = await client.get_nut_ups_status()

    assert nut_status == {
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
    }
    assert router.count("GET", NUT_PATH) == 1


@pytest.mark.asyncio
async def test_get_nut_ups_status_prefers_mapped_status_when_available(
    make_client: ClientType,
) -> None:
    """NUT UPS status should prefer non-empty mapped status over raw response parsing.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test verifies precedence and coexistence semantics.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client,
        session,
        routes={
            ("GET", NUT_PATH): {
                "status": {"ups.status": "OL"},
                "request_id": "abc-123",
                "response": "ups.status: OB",
            }
        },
        explicit_routes=True,
    )

    nut_status = await client.get_nut_ups_status()

    assert nut_status == {
        "status": {"ups.status": "OL"},
        "request_id": "abc-123",
        "response": "ups.status: OB",
    }
    assert router.count("GET", NUT_PATH) == 1


@pytest.mark.asyncio
async def test_get_nut_ups_status_uses_raw_response_when_mapped_status_is_empty(
    make_client: ClientType,
) -> None:
    """NUT UPS status should parse raw response data when mapped status is empty.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates fallback behavior.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client,
        session,
        routes={("GET", NUT_PATH): {"status": {}, "response": "ups.status: OL"}},
        explicit_routes=True,
    )

    nut_status = await client.get_nut_ups_status()

    assert nut_status == {
        "response": "ups.status: OL",
        "status": {"ups.status": "OL"},
    }
    assert router.count("GET", NUT_PATH) == 1


@pytest.mark.asyncio
async def test_get_nut_ups_status_parses_colon_in_value_and_ignores_invalid_lines(
    make_client: ClientType,
) -> None:
    """NUT UPS status parsing should preserve colons in values and ignore malformed lines.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates robust line parsing behavior.
    """
    client, session = make_mock_session_client(make_client)
    raw_response = """\
battery.charge: 100
\x20\x20
Error: UPS unavailable
ups.message: on battery: replace battery
this-line-is-invalid
ups.load: 12"""
    router = install_fake_router(
        client,
        session,
        routes={("GET", NUT_PATH): {"response": raw_response}},
        explicit_routes=True,
    )

    nut_status = await client.get_nut_ups_status()

    assert nut_status == {
        "response": raw_response,
        "status": {
            "battery.charge": "100",
            "ups.message": "on battery: replace battery",
            "ups.load": "12",
        },
    }
    assert router.count("GET", NUT_PATH) == 1


@pytest.mark.parametrize(
    "response_payload, expected, expect_falsey, expect_no_status",
    [
        (None, {}, True, False),
        ({}, {}, True, False),
        ({"response": None}, {"response": None}, False, False),
        ({"response": 123}, {"response": 123}, False, False),
        ({"status": "not-a-mapping"}, {"status": "not-a-mapping"}, False, False),
        (
            {"response": "Error: UPS unavailable", "request_id": "abc"},
            {"response": "Error: UPS unavailable", "request_id": "abc"},
            False,
            True,
        ),
    ],
)
@pytest.mark.asyncio
async def test_get_nut_ups_status_handles_invalid_payloads(
    make_client: ClientType,
    response_payload: Any,
    expected: dict[str, Any],
    expect_falsey: bool,
    expect_no_status: bool,
) -> None:
    """NUT UPS status should fail gracefully for malformed payload shapes.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        response_payload (Any): Invalid payloads to exercise normalization safety.
        expected (dict[str, Any]): Parsed result expected for the invalid payload.
        expect_falsey (bool): Whether the parsed result should be falsey.
        expect_no_status (bool): Whether the parsed result must omit UPS status.

    Returns:
        None: This test validates defensive handling of malformed responses.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client, session, routes={("GET", NUT_PATH): response_payload}, explicit_routes=True
    )

    nut_status = await client.get_nut_ups_status()

    if expect_falsey:
        assert not nut_status
    assert nut_status == expected
    if expect_no_status:
        assert "status" not in nut_status
    assert router.count("GET", NUT_PATH) == 1


@pytest.mark.parametrize("throw_errors", [False, True])
@pytest.mark.parametrize(
    "route, raised",
    [
        (404, None),
        (403, OPNsensePrivilegeMissing),
        (503, OPNsenseConnectionError),
        (aiohttp.ClientConnectionError("connection reset"), OPNsenseConnectionError),
    ],
    ids=["missing", "forbidden", "server-error", "transport-error"],
)
@pytest.mark.asyncio
async def test_get_nut_ups_status_returns_empty_dict_when_endpoint_unavailable(
    make_client: ClientType,
    route: int | Exception,
    raised: type[Exception] | None,
    throw_errors: bool,
) -> None:
    """NUT UPS status queries should fail closed when the endpoint is unavailable.

    A missing endpoint is empty in either mode. A permission or transient failure
    is empty with errors off and raises the documented error with errors on.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        route (int | Exception): Router reply for the NUT status endpoint.
        raised (type[Exception] | None): Error expected with ``throw_errors``, or ``None``
            when the reply is empty in either mode.
        throw_errors (bool): Whether the client raises instead of returning empty values.

    Returns:
        None: This test validates fail-closed behavior for unavailable NUT APIs.
    """
    client, session = make_mock_session_client(partial(make_client, throw_errors=throw_errors))
    router = install_fake_router(
        client, session, routes={("GET", NUT_PATH): route}, explicit_routes=True
    )

    if throw_errors and raised is not None:
        with pytest.raises(raised):
            await client.get_nut_ups_status()
    else:
        assert await client.get_nut_ups_status() == {}
    assert router.count("GET", NUT_PATH) == 1


@pytest.mark.parametrize(
    "payload, expected",
    [
        (
            "not-a-mapping",
            {},
        ),
        (
            {"response": 123, "status": {}},
            {"response": 123, "status": {}},
        ),
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

    Returns:
        None: This test validates all fallback branches via parametrized cases.
    """
    assert OPNsenseClient._normalize_nut_ups_status_payload(payload) == expected
