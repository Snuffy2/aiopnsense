"""Focused tests for status-aware optional DHCP endpoint reads."""

import asyncio
from collections.abc import Callable, Iterator
from datetime import UTC
from unittest.mock import AsyncMock

import pytest

from aiopnsense import CategoryResult, CategoryState, OPNsenseClient
from aiopnsense.dhcp import (
    DNSMASQ_LEASES_SEARCH_ENDPOINT,
    KEA_DHCPV4_GET_ENDPOINT,
    KEA_DHCPV4_SEARCH_RESERVATION_CAMELCASE_ENDPOINT,
    KEA_DHCPV4_SEARCH_RESERVATION_ENDPOINT,
    KEA_LEASES4_SEARCH_ENDPOINT,
    KEA_LEASES6_SEARCH_ENDPOINT,
)
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]

DHCP_OPTIONAL_GET_ENDPOINTS = {
    KEA_DHCPV4_GET_ENDPOINT,
    KEA_DHCPV4_SEARCH_RESERVATION_ENDPOINT,
    KEA_DHCPV4_SEARCH_RESERVATION_CAMELCASE_ENDPOINT,
    KEA_LEASES4_SEARCH_ENDPOINT,
    KEA_LEASES6_SEARCH_ENDPOINT,
    DNSMASQ_LEASES_SEARCH_ENDPOINT,
}


def test_dhcp_optional_get_endpoints_are_registered() -> None:
    """Every status-aware DHCP GET path is explicitly registered as optional."""
    assert DHCP_OPTIONAL_GET_ENDPOINTS <= OPNsenseClient._OPTIONAL_GET_ENDPOINTS


@pytest.mark.asyncio
async def test_kea_result_uses_real_optional_helper_through_disappearance_and_recovery(
    make_client: ClientType,
) -> None:
    """Kea result reads progress from healthy to pending, missing, then recovered."""
    client, _session = make_mock_session_client(make_client)
    cache_key = ("get", KEA_LEASES6_SEARCH_ENDPOINT)
    client._get_optional = AsyncMock(
        side_effect=[
            CategoryResult({"rows": []}, "available", True),
            CategoryResult({}, "missing", False),
            CategoryResult({}, "missing", False),
            CategoryResult({"rows": []}, "available", True),
        ]
    )
    client._is_core_firmware_endpoint_healthy = AsyncMock(return_value=True)
    try:
        assert (await client._get_kea_dhcpv6_leases_result()).state == "available"
        assert (await client._get_kea_dhcpv6_leases_result()).state == "pending"
        assert (await client._get_kea_dhcpv6_leases_result()).state == "missing"

        client._endpoint_checked_at[cache_key] = 0.0
        recovered = await client._get_kea_dhcpv6_leases_result()

        assert recovered == CategoryResult([], "available", True)
        assert client._endpoint_availability[cache_key] == "available"
        assert cache_key not in client._optional_endpoint_missing_pending_confirmation
        assert client._get_optional.await_count == 4
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_dnsmasq_result_executes_registered_optional_request(
    make_client: ClientType,
) -> None:
    """The dnsmasq result seam reaches the real optional helper and returns data."""
    client, _session = make_mock_session_client(make_client)
    client._get_optional = AsyncMock(
        return_value=CategoryResult(
            {
                "rows": [
                    {
                        "address": "192.0.2.20",
                        "if": "lan",
                        "expire": "never",
                    }
                ]
            },
            "available",
            True,
        )
    )
    try:
        result = await client._get_dnsmasq_leases_result()

        assert result.state == "available"
        assert result.data[0]["address"] == "192.0.2.20"
        client._get_optional.assert_awaited_once_with(DNSMASQ_LEASES_SEARCH_ENDPOINT)
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["pending", "missing", "transient", "malformed"])
async def test_optional_lease_results_preserve_endpoint_state(
    make_client: ClientType, state: str
) -> None:
    """Lease result helpers preserve every unavailable endpoint state."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=CategoryResult({}, state, False)
        )

        assert (await client._get_kea_dhcpv4_leases_result()).state == state
        assert (await client._get_kea_dhcpv6_leases_result()).state == state
        assert (await client._get_dnsmasq_leases_result()).state == state
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_kea_reservation_failure_preserves_healthy_lease_data(
    make_client: ClientType,
) -> None:
    """A reservation failure makes Kea non-authoritative without dropping leases."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._check_optional_get_endpoint = AsyncMock(
            side_effect=[
                CategoryResult(
                    {
                        "rows": [
                            {
                                "state": "0",
                                "address": "192.0.2.1",
                                "hwaddr": "aa:bb",
                                "if_name": "lan",
                            }
                        ]
                    },
                    "available",
                    True,
                ),
                CategoryResult({}, "transient", False),
            ]
        )

        result = await client._get_kea_dhcpv4_leases_result()

        assert result.state == "transient"
        assert result.authoritative is False
        assert result.data[0]["type"] == "unknown"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_missing_kea_reservations_do_not_poison_provider(
    make_client: ClientType,
) -> None:
    """A confirmed absent reservation endpoint remains an optional capability."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._check_optional_get_endpoint = AsyncMock(
            side_effect=[
                CategoryResult({"rows": []}, "available", True),
                CategoryResult({}, "missing", False),
            ]
        )

        assert await client._get_kea_dhcpv4_leases_result() == CategoryResult([], "available", True)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_kea_interfaces_return_partial_malformed_data(make_client: ClientType) -> None:
    """Valid interface rows survive alongside malformed configuration rows."""
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=CategoryResult(
                {
                    "dhcpv4": {
                        "general": {
                            "enabled": "1",
                            "interfaces": {
                                "lan": {"selected": "1", "value": "LAN"},
                                "bad": "not-a-mapping",
                            },
                        }
                    }
                },
                "available",
                True,
            )
        )

        assert await client._get_kea_interfaces_result() == CategoryResult(
            {"lan": "LAN"}, "malformed", False
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_concurrent_dhcp_results_keep_source_states_isolated(
    make_client: ClientType,
) -> None:
    """Concurrent aggregate calls do not share provider state lists."""
    client, _session = make_mock_session_client(make_client)
    try:
        states: Iterator[CategoryState] = iter(["transient", "available"])

        async def kea_v4(**_kwargs: object) -> list[dict[str, object]]:
            """Record a distinct state in each task-local aggregation context."""
            state = next(states)
            await asyncio.sleep(0)
            client._record_dhcp_source_state(state)
            return []

        client._get_kea_dhcpv4_leases = AsyncMock(side_effect=kea_v4)
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)
        client._get_kea_dhcpv6_leases = AsyncMock(return_value=[])
        client._get_isc_dhcpv4_leases = AsyncMock(return_value=[])
        client._get_isc_dhcpv6_leases = AsyncMock(return_value=[])
        client._get_dnsmasq_leases = AsyncMock(return_value=[])
        client._get_kea_interfaces = AsyncMock(return_value={})

        results = await asyncio.gather(
            client.get_dhcp_leases_result(), client.get_dhcp_leases_result()
        )

        assert {result.state for result in results} == {"available", "transient"}
    finally:
        await client.async_close()
