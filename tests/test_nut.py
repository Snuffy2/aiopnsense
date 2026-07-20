"""Tests for `aiopnsense.nut`."""

import logging
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiopnsense import CategoryResult, CategoryState, OPNsenseClient
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


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
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=(
                "available",
                {
                    "status": {
                        "ups.status": "OL",
                        "battery.charge": "100",
                        "ups.load": "12",
                    }
                },
            )
        )

        nut_status = await client.get_nut_ups_status()

        assert nut_status == {
            "status": {
                "ups.status": "OL",
                "battery.charge": "100",
                "ups.load": "12",
            }
        }
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/nut/diagnostics/upsstatus"
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_nut_ups_status_parses_raw_status_response(make_client: ClientType) -> None:
    """NUT UPS status should parse raw colon-separated text responses.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates parsing of plugin string responses into status mappings.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=(
                "available",
                {
                    "response": (
                        "battery.charge: 100\n"
                        "ups.status: OL\n"
                        "input.L1-N.voltage: 120\n"
                        "input.L1-L2.voltage: 240\n"
                    )
                },
            )
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
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/nut/diagnostics/upsstatus"
        )
    finally:
        await client.async_close()


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
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=(
                "available",
                {
                    "status": {"ups.status": "OL"},
                    "request_id": "abc-123",
                    "response": "ups.status: OB",
                },
            )
        )

        nut_status = await client.get_nut_ups_status()

        assert nut_status == {
            "status": {"ups.status": "OL"},
            "request_id": "abc-123",
            "response": "ups.status: OB",
        }
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/nut/diagnostics/upsstatus"
        )
    finally:
        await client.async_close()


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
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=("available", {"status": {}, "response": "ups.status: OL"})
        )

        nut_status = await client.get_nut_ups_status()

        assert nut_status == {
            "response": "ups.status: OL",
            "status": {"ups.status": "OL"},
        }
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/nut/diagnostics/upsstatus"
        )
    finally:
        await client.async_close()


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
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=(
                "available",
                {
                    "response": "\n".join(
                        [
                            "battery.charge: 100",
                            "  ",
                            "Error: UPS unavailable",
                            "ups.message: on battery: replace battery",
                            "this-line-is-invalid",
                            "ups.load: 12",
                        ]
                    )
                },
            )
        )

        nut_status = await client.get_nut_ups_status()

        assert nut_status == {
            "response": "\n".join(
                [
                    "battery.charge: 100",
                    "  ",
                    "Error: UPS unavailable",
                    "ups.message: on battery: replace battery",
                    "this-line-is-invalid",
                    "ups.load: 12",
                ]
            ),
            "status": {
                "battery.charge": "100",
                "ups.message": "on battery: replace battery",
                "ups.load": "12",
            },
        }
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/nut/diagnostics/upsstatus"
        )
    finally:
        await client.async_close()


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

    Returns:
        None: This test validates defensive handling of malformed responses.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=("available", response_payload)
        )

        nut_status = await client.get_nut_ups_status()

        if expect_falsey:
            assert not nut_status
        assert nut_status == expected
        if expect_no_status:
            assert "status" not in nut_status
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/nut/diagnostics/upsstatus"
        )
    finally:
        await client.async_close()


@pytest.mark.parametrize("state", ["missing", "unavailable"])
@pytest.mark.asyncio
async def test_get_nut_ups_status_returns_empty_dict_when_endpoint_unavailable(
    make_client: ClientType,
    state: str,
) -> None:
    """NUT UPS status queries should fail closed when the endpoint is unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates fail-closed behavior for unavailable NUT APIs.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(return_value=(state, {}))

        nut_status = await client.get_nut_ups_status()

        assert nut_status == {}
        client._check_optional_get_endpoint.assert_awaited_once_with(
            "/api/nut/diagnostics/upsstatus"
        )
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    "payload, expected, expected_debug",
    [
        (
            "not-a-mapping",
            {},
            "NUT UPS status payload is not a mapping (type=str), returning {}",
        ),
        (
            {"response": 123, "status": {}},
            {"response": 123, "status": {}},
            "NUT UPS status payload response is not a string (type=int), returning unchanged",
        ),
        (
            {"response": "invalid-status-line-without-colon"},
            {"response": "invalid-status-line-without-colon"},
            "NUT UPS status response did not contain parseable entries",
        ),
    ],
)
def test_normalize_nut_ups_status_payload_logs_fallback_branches(
    caplog: pytest.LogCaptureFixture,
    payload: Any,
    expected: dict[str, Any],
    expected_debug: str,
) -> None:
    """Cover fallback-branch logging and return values for NUT payload normalization.

    Args:
        caplog (pytest.LogCaptureFixture): Captured logging fixture for debug assertions.
        payload (Any): Input payload passed to the normalization helper.
        expected (dict[str, Any]): Expected normalized payload for the branch.
        expected_debug (str): Exact debug message that must be emitted.

    Returns:
        None: This test validates all fallback branches via parametrized cases.
    """
    with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
        normalized_payload = OPNsenseClient._normalize_nut_ups_status_payload(payload)

    assert normalized_payload == expected
    assert expected_debug in caplog.text


@pytest.mark.asyncio
async def test_get_nut_ups_status_result_distinguishes_empty_and_malformed(
    make_client: ClientType,
) -> None:
    """NUT result metadata should distinguish explicit empty and invalid schemas."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=CategoryResult({}, "available", True)
        )
        assert await client.get_nut_ups_status_result() == CategoryResult({}, "available", True)

        client._check_optional_get_endpoint.return_value = CategoryResult(
            {"status": {}}, "available", True
        )
        assert await client.get_nut_ups_status_result() == CategoryResult(
            {"status": {}}, "available", True
        )

        client._check_optional_get_endpoint.return_value = CategoryResult(
            {"response": 123}, "available", True
        )
        assert await client.get_nut_ups_status_result() == CategoryResult(
            {"response": 123}, "malformed", False
        )

        client._check_optional_get_endpoint.return_value = CategoryResult("bad", "available", True)
        assert await client.get_nut_ups_status_result() == CategoryResult({}, "malformed", False)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_nut_ups_status_result_prefers_valid_status_over_invalid_response(
    make_client: ClientType,
) -> None:
    """A valid structured NUT status is authoritative regardless of response metadata."""
    client, _session = make_mock_session_client(make_client)
    try:
        payload = {"status": {"ups.status": "OL"}, "response": 123}
        client._check_optional_get_endpoint = AsyncMock(
            return_value=CategoryResult(payload, "available", True)
        )

        assert await client.get_nut_ups_status_result() == CategoryResult(
            payload, "available", True
        )
    finally:
        await client.async_close()


@pytest.mark.parametrize("state", ["pending", "missing", "transient"])
@pytest.mark.asyncio
async def test_get_nut_ups_status_result_preserves_transport_state_and_wrapper(
    make_client: ClientType, state: CategoryState
) -> None:
    """NUT result states survive while the compatibility getter returns data only."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            side_effect=[
                CategoryResult({}, state, False),
                CategoryResult({"status": {"ups.status": "OL"}}, "available", True),
            ]
        )
        assert await client.get_nut_ups_status_result() == CategoryResult({}, state, False)
        assert await client.get_nut_ups_status() == {"status": {"ups.status": "OL"}}
    finally:
        await client.async_close()
