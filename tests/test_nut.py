"""Tests for `aiopnsense.nut`."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseClient
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
async def test_get_nut_ups_status_parses_raw_status_response(make_client: ClientType) -> None:
    """NUT UPS status should parse raw colon-separated text responses.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates parsing of plugin string responses into status mappings.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={
                "response": (
                    "battery.charge: 100\n"
                    "ups.status: OL\n"
                    "input.L1-N.voltage: 120\n"
                    "input.L1-L2.voltage: 240\n"
                )
            }
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
        client._is_get_endpoint_available.assert_awaited_once_with("/api/nut/diagnostics/upsstatus")
        client._safe_dict_get.assert_awaited_once_with("/api/nut/diagnostics/upsstatus")
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
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={
                "status": {"ups.status": "OL"},
                "request_id": "abc-123",
                "response": "ups.status: OB",
            }
        )

        nut_status = await client.get_nut_ups_status()

        assert nut_status == {
            "status": {"ups.status": "OL"},
            "request_id": "abc-123",
            "response": "ups.status: OB",
        }
        client._is_get_endpoint_available.assert_awaited_once_with("/api/nut/diagnostics/upsstatus")
        client._safe_dict_get.assert_awaited_once_with("/api/nut/diagnostics/upsstatus")
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
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(return_value={"status": {}, "response": "ups.status: OL"})

        nut_status = await client.get_nut_ups_status()

        assert nut_status == {
            "response": "ups.status: OL",
            "status": {"ups.status": "OL"},
        }
        client._is_get_endpoint_available.assert_awaited_once_with("/api/nut/diagnostics/upsstatus")
        client._safe_dict_get.assert_awaited_once_with("/api/nut/diagnostics/upsstatus")
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
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={
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
            }
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
        client._is_get_endpoint_available.assert_awaited_once_with("/api/nut/diagnostics/upsstatus")
        client._safe_dict_get.assert_awaited_once_with("/api/nut/diagnostics/upsstatus")
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
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(return_value=response_payload)

        nut_status = await client.get_nut_ups_status()

        if expect_falsey:
            assert not nut_status
        assert nut_status == expected
        if expect_no_status:
            assert "status" not in nut_status
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
