"""Tests for authoritative optional-category result contracts."""

import asyncio
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


@pytest.mark.parametrize("value", [None, ("unknown", {"value": 1}), ("available",)])
def test_category_result_coerce_rejects_invalid_legacy_values(value: object) -> None:
    """Invalid legacy values become a non-authoritative malformed result."""
    assert CategoryResult.coerce(value) == CategoryResult({}, "malformed", False)


def test_category_result_comparison_with_unrelated_value_is_false() -> None:
    """An unrelated value does not compare equal to a category result."""
    assert CategoryResult({}, "available", True) != object()


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
async def test_dhcp_source_state_context_is_shared_between_clients(make_client) -> None:
    """Client instances use one module-scoped request-local DHCP state context."""
    first_client = make_client()
    second_client = make_client()
    try:
        assert first_client._dhcp_source_states_context is second_client._dhcp_source_states_context
    finally:
        await first_client.async_close()
        await second_client.async_close()


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

        client._check_optional_post_endpoint.return_value = CategoryResult({}, "available", True)
        assert await client.get_smart_result() == CategoryResult([], "malformed", False)
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

        client._check_optional_get_endpoint.return_value = CategoryResult({}, "available", True)
        assert await client.get_unbound_blocklist_result() == CategoryResult({}, "malformed", False)
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

        client._check_optional_get_endpoint.return_value = CategoryResult({}, "available", True)
        assert (await client.get_vnstat_result()).state == "malformed"

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
@pytest.mark.parametrize("payload", [{}, {"rows": "bad"}])
async def test_dhcp_provider_invalid_rows_are_schema_malformed(
    make_client, provider: str, payload: dict
) -> None:
    """Every DHCP provider requires an explicitly present list-valued rows field."""
    client: OPNsenseClient = make_client()
    state_token = client._dhcp_source_states_context.set([])
    try:
        if provider == "kea":
            client._check_optional_get_endpoint = AsyncMock(
                return_value=CategoryResult(payload, "available", True)
            )
            await client._get_kea_dhcp_leases("/api/kea/leases4/search", "Kea")
        elif provider == "dnsmasq":
            client._check_optional_get_endpoint = AsyncMock(
                return_value=CategoryResult(payload, "available", True)
            )
            await client._get_dnsmasq_leases()
        else:
            client._get_endpoint_path = AsyncMock(return_value=f"/{provider}")
            client._check_optional_get_endpoint = AsyncMock(
                return_value=CategoryResult(payload, "available", True)
            )
            method = (
                client._get_isc_dhcpv4_leases
                if provider == "isc_v4"
                else client._get_isc_dhcpv6_leases
            )
            await method(opnsense_tz=UTC)
        assert client._dhcp_source_states_context.get() == ["malformed"]
    finally:
        client._dhcp_source_states_context.reset(state_token)
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["kea_interfaces", "kea_leases", "dnsmasq"])
async def test_dhcp_provider_non_mapping_payload_is_schema_malformed(
    make_client, provider: str
) -> None:
    """Available DHCP payloads must be mappings before provider parsing."""
    client: OPNsenseClient = make_client()
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=CategoryResult("bad-response", "available", True)
        )

        if provider == "kea_interfaces":
            assert await client._get_kea_interfaces_result() == CategoryResult(
                {}, "malformed", False
            )
        elif provider == "kea_leases":
            assert await client._get_kea_dhcp_leases_result(
                "/api/kea/leases4/search", "Kea"
            ) == CategoryResult([], "malformed", False)
        else:
            assert await client._get_dnsmasq_leases_result() == CategoryResult(
                [], "malformed", False
            )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_kea_reservation_provider_requires_rows_key(make_client) -> None:
    """An available Kea reservation response without rows makes the source malformed."""
    client = make_client()
    state_token = client._dhcp_source_states_context.set([])
    try:
        client._get_endpoint_path = AsyncMock(return_value="/reservation")
        client._check_optional_get_endpoint = AsyncMock(
            side_effect=[
                CategoryResult({"rows": []}, "available", True),
                CategoryResult({}, "available", True),
            ]
        )
        await client._get_kea_dhcp_leases(
            "/api/kea/leases4/search",
            "Kea",
            "/reservation",
            "/reservationCamel",
        )
        assert client._dhcp_source_states_context.get() == ["malformed"]
    finally:
        client._dhcp_source_states_context.reset(state_token)
        await client.async_close()


@pytest.mark.asyncio
async def test_kea_reservation_provider_rejects_non_mapping_payload(make_client) -> None:
    """A non-mapping reservation payload is malformed while valid leases survive."""
    client: OPNsenseClient = make_client()
    try:
        client._get_endpoint_path = AsyncMock(return_value="/reservation")
        client._check_optional_get_endpoint = AsyncMock(
            side_effect=[
                CategoryResult(
                    {
                        "rows": [
                            {
                                "state": "0",
                                "hwaddr": "aa:bb:cc:dd:ee:ff",
                                "address": "192.0.2.10",
                                "if_name": "em0",
                            }
                        ]
                    },
                    "available",
                    True,
                ),
                CategoryResult("bad-response", "available", True),
            ]
        )

        result = await client._get_kea_dhcp_leases_result(
            "/api/kea/leases4/search",
            "Kea",
            "/reservation",
            "/reservationCamel",
        )

        assert result.state == "malformed"
        assert result.authoritative is False
        assert len(result.data) == 1
        assert result.data[0]["type"] == "unknown"
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
@pytest.mark.parametrize(
    ("interface_state", "expected_state"),
    [
        ("available", "missing"),
        ("pending", "pending"),
        ("transient", "transient"),
        ("malformed", "malformed"),
        ("missing", "missing"),
    ],
)
async def test_dhcp_result_treats_kea_interfaces_as_metadata_only(
    make_client, interface_state: CategoryState, expected_state: CategoryState
) -> None:
    """Kea interface metadata cannot make missing lease providers authoritative."""
    client = make_client()

    async def missing_provider(**_kwargs) -> list[dict]:
        """Record a missing lease provider."""
        client._record_dhcp_source_state("missing")
        return []

    try:
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)
        for name in (
            "_get_kea_dhcpv4_leases",
            "_get_kea_dhcpv6_leases",
            "_get_isc_dhcpv4_leases",
            "_get_isc_dhcpv6_leases",
            "_get_dnsmasq_leases",
        ):
            setattr(client, name, AsyncMock(side_effect=missing_provider))
        client._get_kea_interfaces_result = AsyncMock(
            return_value=CategoryResult({}, interface_state, interface_state == "available")
        )

        result = await client.get_dhcp_leases_result()

        assert result.state == expected_state
        assert result.authoritative is False
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

        client._check_optional_get_endpoint.return_value = CategoryResult({}, "available", True)
        assert await client.get_arp_table_result() == CategoryResult([], "malformed", False)

        valid = {"ip-address": "192.0.2.1"}
        client._check_optional_get_endpoint.return_value = CategoryResult(
            {"rows": [valid, "bad"]}, "available", True
        )
        assert await client.get_arp_table_result() == CategoryResult([valid], "malformed", False)
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("period", ["hourly", "daily", "monthly", "yearly"])
async def test_vnstat_period_result_requires_response_key(make_client, period: str) -> None:
    """Every vnStat period requires an explicitly present response string."""
    client = make_client()
    try:
        client._check_optional_get_endpoint = AsyncMock(
            return_value=CategoryResult({}, "available", True)
        )
        result = await client._fetch_vnstat_for_result(f"/vnstat/{period}", period)
        assert result.state == "malformed"
        assert result.authoritative is False
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_concurrent_dhcp_results_keep_provider_states_request_local(make_client) -> None:
    """Interleaved DHCP collections on one client cannot contaminate authority state."""
    client = make_client()
    available_started = asyncio.Event()
    malformed_recorded = asyncio.Event()

    async def first_provider(**_kwargs) -> list[dict]:
        """Interleave the first provider based on the current task name."""
        task = asyncio.current_task()
        if task is not None and task.get_name() == "available-collection":
            client._record_dhcp_source_state("available")
            available_started.set()
            await malformed_recorded.wait()
        else:
            await available_started.wait()
            client._record_dhcp_source_state("malformed")
            malformed_recorded.set()
        return []

    async def missing_provider(**_kwargs) -> list[dict]:
        """Record a confirmed inapplicable provider for the current request."""
        client._record_dhcp_source_state("missing")
        await asyncio.sleep(0)
        return []

    try:
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)
        client._get_kea_dhcpv4_leases = AsyncMock(side_effect=first_provider)
        for name in (
            "_get_kea_dhcpv6_leases",
            "_get_isc_dhcpv4_leases",
            "_get_isc_dhcpv6_leases",
            "_get_dnsmasq_leases",
        ):
            setattr(client, name, AsyncMock(side_effect=missing_provider))
        client._get_kea_interfaces = AsyncMock(return_value={})

        available_task = asyncio.create_task(
            client.get_dhcp_leases_result(), name="available-collection"
        )
        malformed_task = asyncio.create_task(
            client.get_dhcp_leases_result(), name="malformed-collection"
        )
        available_result, malformed_result = await asyncio.gather(available_task, malformed_task)

        assert available_result.state == "available"
        assert available_result.authoritative is True
        assert malformed_result.state == "malformed"
        assert malformed_result.authoritative is False
    finally:
        await client.async_close()
