"""Tests for authoritative optional-category result contracts."""

from dataclasses import FrozenInstanceError
from datetime import UTC
from unittest.mock import AsyncMock

import pytest

from aiopnsense import CategoryResult, CategoryState, OPNsenseClient


def test_category_result_is_exported_immutable_and_generic() -> None:
    """The public result envelope is frozen, slotted, and carries typed state."""
    state: CategoryState = "available"
    result = CategoryResult([1], state, True)

    assert result.data == [1]
    assert result.state == "available"
    assert result.authoritative is True
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.state = "missing"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("state", "authoritative"),
    [("available", False), ("missing", True), ("malformed", True)],
)
def test_category_result_rejects_contradictory_authority(
    state: CategoryState, authoritative: bool
) -> None:
    """Authority is true exactly for available category data."""
    with pytest.raises(ValueError, match="authoritative"):
        CategoryResult({}, state, authoritative)


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        (["available", "missing"], ("available", True)),
        (["missing", "missing"], ("missing", False)),
        (["available", "pending"], ("pending", False)),
        (["available", "transient"], ("transient", False)),
        (["available", "malformed"], ("malformed", False)),
    ],
)
def test_dhcp_source_authority_ignores_only_confirmed_inapplicable_sources(
    states: list[CategoryState], expected: tuple[CategoryState, bool]
) -> None:
    """Confirmed missing providers are inapplicable; uncertain providers are not."""
    from aiopnsense.dhcp import DHCPMixin

    assert DHCPMixin._aggregate_dhcp_source_states(states) == expected


@pytest.mark.asyncio
async def test_smart_result_preserves_valid_rows_but_marks_mixed_schema_malformed(
    make_client,
) -> None:
    """SMART invalid rows make partial device data non-authoritative."""
    client = make_client()
    try:
        client._check_optional_post_endpoint = AsyncMock(
            return_value=CategoryResult(
                {"devices": [{"ident": "ada0"}, {"ident": ""}, "bad"]},
                "available",
                True,
            )
        )
        result = await client.get_smart_result()
        assert result == CategoryResult([{"ident": "ada0"}], "malformed", False)

        client._check_optional_post_endpoint.return_value = CategoryResult(
            {"devices": []}, "available", True
        )
        assert await client.get_smart_result() == CategoryResult([], "available", True)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_unbound_result_distinguishes_mixed_invalid_rows_from_empty(make_client) -> None:
    """Unbound retains valid rows while invalid rows make the result malformed."""
    client = make_client()
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.8")
        client._check_optional_get_endpoint = AsyncMock(
            return_value=CategoryResult(
                {"rows": [{"uuid": "valid"}, {"uuid": ""}, 1]}, "available", True
            )
        )
        result = await client.get_unbound_blocklist_result()
        assert result == CategoryResult({"valid": {"uuid": "valid"}}, "malformed", False)

        client._check_optional_get_endpoint.return_value = CategoryResult(
            {"rows": []}, "available", True
        )
        assert await client.get_unbound_blocklist_result() == CategoryResult({}, "available", True)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_vnstat_result_distinguishes_non_string_response_from_valid_empty(
    make_client,
) -> None:
    """vnStat requires textual output but accepts an empty text report."""
    client = make_client()
    try:
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)
        client._check_optional_get_endpoint = AsyncMock(
            return_value=CategoryResult({"response": 1}, "available", True)
        )
        result = await client.get_vnstat_result()
        assert result.state == "malformed"
        assert result.authoritative is False

        client._check_optional_get_endpoint.return_value = CategoryResult(
            {"response": ""}, "available", True
        )
        assert await client.get_vnstat_result() == CategoryResult(
            {"interfaces": {}, "interface_count": 0}, "available", True
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider",
    ["kea", "dnsmasq", "isc_v4", "isc_v6"],
)
async def test_dhcp_provider_invalid_rows_are_schema_malformed(make_client, provider: str) -> None:
    """Every applicable DHCP provider classifies a non-list rows field as malformed."""
    client: OPNsenseClient = make_client()
    try:
        client._dhcp_source_states = []
        if provider == "kea":
            client._is_get_endpoint_available = AsyncMock(return_value=True)
            client._safe_dict_get = AsyncMock(return_value={"rows": "bad"})
            await client._get_kea_dhcp_leases("/api/kea/leases4/search", "Kea")
        elif provider == "dnsmasq":
            client._is_get_endpoint_available = AsyncMock(return_value=True)
            client._safe_dict_get = AsyncMock(return_value={"rows": "bad"})
            await client._get_dnsmasq_leases()
        else:
            client._get_endpoint_path = AsyncMock(return_value=f"/{provider}")
            client._check_optional_get_endpoint = AsyncMock(
                return_value=CategoryResult({"rows": "bad"}, "available", True)
            )
            method = (
                client._get_isc_dhcpv4_leases
                if provider == "isc_v4"
                else client._get_isc_dhcpv6_leases
            )
            await method(opnsense_tz=UTC)
        assert client._dhcp_source_states == ["malformed"]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_dhcp_result_keeps_healthy_source_data_when_another_source_is_malformed(
    make_client,
) -> None:
    """Mixed provider validity retains healthy leases but is non-authoritative."""
    client = make_client()

    def source(state: CategoryState, leases: list[dict]):
        """Build a synthetic provider that records state and returns normalized rows."""

        async def provider(**_kwargs) -> list[dict]:
            """Record the provider state for this collection pass."""
            client._record_dhcp_source_state(state)
            return leases

        return provider

    try:
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)
        valid = [{"address": "192.0.2.1", "if_name": "lan", "if_descr": "LAN"}]
        client._get_kea_dhcpv4_leases = AsyncMock(side_effect=source("available", valid))
        client._get_kea_dhcpv6_leases = AsyncMock(side_effect=source("malformed", []))
        for name in (
            "_get_isc_dhcpv4_leases",
            "_get_isc_dhcpv6_leases",
            "_get_dnsmasq_leases",
        ):
            setattr(
                client,
                name,
                AsyncMock(side_effect=source("missing", [])),
            )
        client._get_kea_interfaces = AsyncMock(return_value={})

        result = await client.get_dhcp_leases_result()
        assert result.state == "malformed"
        assert result.authoritative is False
        assert result.data["leases"]["lan"][0]["address"] == "192.0.2.1"
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["pending", "missing", "transient", "malformed"])
async def test_arp_result_propagates_non_available_endpoint_states(
    make_client, state: CategoryState
) -> None:
    """ARP fallback empties retain their non-authoritative endpoint state."""
    client = make_client()
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=CategoryResult({}, state, False)
        )
        result = await client.get_arp_table_result()
        assert result == CategoryResult([], state, False)
        assert await client.get_arp_table() == []
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_arp_result_distinguishes_available_empty_and_malformed_partial_rows(
    make_client,
) -> None:
    """ARP empty success is authoritative while mixed invalid rows retain usable data."""
    client = make_client()
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=CategoryResult({"rows": []}, "available", True)
        )
        assert await client.get_arp_table_result() == CategoryResult([], "available", True)

        valid = {"ip-address": "192.0.2.1"}
        client._check_optional_get_endpoint.return_value = CategoryResult(
            {"rows": [valid, "bad"]}, "available", True
        )
        assert await client.get_arp_table_result() == CategoryResult([valid], "malformed", False)
    finally:
        await client.async_close()
