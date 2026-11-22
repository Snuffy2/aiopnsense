"""Tests for `aiopnsense.firewall`."""

from collections.abc import Callable
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseClient

ClientType = Callable[..., OPNsenseClient]
AliasRowType = dict[str, str]
AliasSearchRowsType = list[AliasRowType]
AliasPostResponseType = dict[str, str]
AliasPostResultType = AliasPostResponseType | list[AliasPostResponseType] | None


@pytest.mark.asyncio
async def test_get_firewall_aggregates_rest_payload(make_client: ClientType) -> None:
    """`get_firewall` should aggregate all REST-native rule collections.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client = make_client()
    try:
        client._get_firewall_rules = AsyncMock(return_value={"rule1": {"uuid": "rule1"}})
        client._get_nat_destination_rules = AsyncMock(return_value={"dst1": {"uuid": "dst1"}})
        client._get_nat_one_to_one_rules = AsyncMock(return_value={"oto1": {"uuid": "oto1"}})
        client._get_nat_source_rules = AsyncMock(return_value={"src1": {"uuid": "src1"}})
        client._get_nat_npt_rules = AsyncMock(return_value={"npt1": {"uuid": "npt1"}})

        result = await client.get_firewall()

        assert result == {
            "rules": {"rule1": {"uuid": "rule1"}},
            "nat": {
                "d_nat": {"dst1": {"uuid": "dst1"}},
                "one_to_one": {"oto1": {"uuid": "oto1"}},
                "source_nat": {"src1": {"uuid": "src1"}},
                "npt": {"npt1": {"uuid": "npt1"}},
            },
        }
        client._get_firewall_rules.assert_awaited_once()
        client._get_nat_destination_rules.assert_awaited_once()
        client._get_nat_one_to_one_rules.assert_awaited_once()
        client._get_nat_source_rules.assert_awaited_once()
        client._get_nat_npt_rules.assert_awaited_once()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_firewall_rules_skips_invalid_rows(
    make_client: ClientType,
) -> None:
    """Firewall rule downloads should parse editable rows and skip invalid rows.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client = make_client()
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._get_text = AsyncMock(
            return_value=(
                "@uuid;enabled;description\n"
                'rule-ok;1;"Allow; DNS"\n'
                'rule-extra;1;"Has; extra";extra\n'
                ";1;Missing UUID\n"
                "lockout-1;1;Lockout\n"
            )
        )

        result = await client._get_firewall_rules()

        assert result == {
            "rule-ok": {
                "uuid": "rule-ok",
                "enabled": "1",
                "description": "Allow; DNS",
            },
        }
        client._is_get_endpoint_available.assert_awaited_once_with(
            "/api/firewall/filter/download_rules"
        )
        client._get_text.assert_awaited_once_with("/api/firewall/filter/download_rules")
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            '@uuid,enabled,tcpflags2,categories,description\nrule-2611,1,SA,web,"Allow, web"\n',
            {
                "uuid": "rule-2611",
                "enabled": "1",
                "tcpflags2": "SA",
                "categories": "web",
                "description": "Allow, web",
            },
        ),
        (
            (
                "@uuid;enabled;tcpflags2;tcpflags_any;categories;description\n"
                'rule-2612;1;SA;1;web;"Allow; web"\n'
            ),
            {
                "uuid": "rule-2612",
                "enabled": "1",
                "tcpflags2": "SA",
                "tcpflags_any": "1",
                "categories": "web",
                "description": "Allow; web",
            },
        ),
    ],
    ids=["opnsense-26.1.1", "opnsense-26.1.2-and-newer"],
)
@pytest.mark.asyncio
async def test_get_firewall_rules_supports_opnsense_csv_versions(
    make_client: ClientType,
    response: str,
    expected: dict[str, str],
) -> None:
    """Firewall rule downloads should support all released CSV formats.

    Args:
        make_client (ClientType): Fixture factory used to configure the CSV response client.
        response (str): Firewall CSV payload for the OPNsense version case.
        expected (dict[str, str]): Parsed rule mapping expected from the payload.
    """
    client = make_client()
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._get_text = AsyncMock(return_value=response)

        result = await client._get_firewall_rules()

        assert result == {expected["uuid"]: expected}
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_nat_source_rules_labels_empty_interface_address_target(
    make_client: ClientType,
) -> None:
    """Source NAT rows with an empty target should expose an interface-address label.

    Args:
        make_client (ClientType): Fixture factory used to configure the source-NAT response.
    """
    client = make_client()
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client.get_host_firmware_version = AsyncMock(return_value="26.7")
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {
                        "uuid": "src-interface-address",
                        "description": "Interface address target",
                        "enabled": "1",
                        "interface": "wan",
                        "%interface": "WAN",
                        "target": "",
                    }
                ]
            }
        )

        result = await client._get_nat_source_rules()

        assert result["src-interface-address"]["target"] == ""
        assert result["src-interface-address"]["%target"] == "WAN address"
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("method_name", "api_endpoint", "rows", "expected", "firmware_version"),
    [
        (
            "_get_nat_destination_rules",
            "/api/firewall/d_nat/search_rule",
            [
                {"uuid": "dst1", "descr": "DNAT rule", "disabled": 0},
                {"uuid": "lockout-1", "descr": "ignored", "disabled": "0"},
            ],
            {"dst1": {"uuid": "dst1", "description": "DNAT rule", "enabled": "1"}},
            "26.1.1",
        ),
        (
            "_get_nat_destination_rules",
            "/api/firewall/d_nat/search_rule",
            [
                {
                    "uuid": "dst1",
                    "descr": "DNAT legacy category",
                    "disabled": 0,
                    "category": "cat1",
                    "%category": "WAN",
                },
            ],
            {
                "dst1": {
                    "uuid": "dst1",
                    "description": "DNAT legacy category",
                    "enabled": "1",
                    "category": "cat1",
                    "%category": "WAN",
                }
            },
            "26.1.3",
        ),
        (
            "_get_nat_destination_rules",
            "/api/firewall/d_nat/search_rule",
            [
                {
                    "uuid": "dst1",
                    "descr": "DNAT rule",
                    "enabled": "0",
                    "category": "cat1",
                    "%category": "WAN",
                },
            ],
            {
                "dst1": {
                    "uuid": "dst1",
                    "description": "DNAT rule",
                    "enabled": "0",
                    "category": "cat1",
                    "%category": "WAN",
                    "categories": "cat1",
                    "%categories": "WAN",
                }
            },
            "26.1.4",
        ),
        (
            "_get_nat_one_to_one_rules",
            "/api/firewall/one_to_one/search_rule",
            [{"uuid": "oto1", "description": "1:1 rule", "enabled": "1"}],
            {"oto1": {"uuid": "oto1", "description": "1:1 rule", "enabled": "1"}},
            None,
        ),
        (
            "_get_nat_source_rules",
            "/api/firewall/source_nat/search_rule",
            [{"uuid": "src1", "description": "SNAT rule", "enabled": "0"}],
            {"src1": {"uuid": "src1", "description": "SNAT rule", "enabled": "0"}},
            None,
        ),
        (
            "_get_nat_npt_rules",
            "/api/firewall/npt/search_rule",
            [{"uuid": "npt1", "description": "NPT rule", "enabled": "1"}],
            {"npt1": {"uuid": "npt1", "description": "NPT rule", "enabled": "1"}},
            None,
        ),
    ],
)
async def test_nat_rule_helpers_parse_rows(
    make_client: ClientType,
    method_name: str,
    api_endpoint: str,
    rows: list[dict[str, object]],
    expected: dict[str, dict[str, str]],
    firmware_version: str | None,
) -> None:
    """NAT rule helpers should return UUID-keyed mappings from REST search rows.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        method_name (str): Client helper method name.
        api_endpoint (str): REST endpoint expected from the helper.
        rows (list[dict[str, object]]): REST search rows to normalize.
        expected (dict[str, dict[str, str]]): Expected UUID-keyed rule mapping.
        firmware_version (str | None): Optional mocked firmware version.
    """
    client = make_client()
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(return_value={"rows": rows})
        if firmware_version is not None:
            client.get_host_firmware_version = AsyncMock(return_value=firmware_version)

        result = await getattr(client, method_name)()

        assert result == expected
        client._is_get_endpoint_available.assert_awaited_once_with(api_endpoint)
        client._safe_dict_get.assert_awaited_once_with(api_endpoint)
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "api_endpoint", "expected"),
    [
        ("_get_firewall_rules", "/api/firewall/filter/download_rules", {}),
        ("_get_nat_destination_rules", "/api/firewall/d_nat/search_rule", {}),
        ("_get_nat_one_to_one_rules", "/api/firewall/one_to_one/search_rule", {}),
        ("_get_nat_source_rules", "/api/firewall/source_nat/search_rule", {}),
        ("_get_nat_npt_rules", "/api/firewall/npt/search_rule", {}),
    ],
)
async def test_rule_helpers_return_empty_when_endpoint_unavailable(
    make_client: ClientType,
    method_name: str,
    api_endpoint: str,
    expected: dict[str, object],
) -> None:
    """Rule helpers should short-circuit when the related endpoint is unavailable.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        method_name (str): Rule helper invoked for the unavailable endpoint.
        api_endpoint (str): Endpoint that the helper must check before fetching.
        expected (dict[str, object]): Expected empty rule mapping.
    """
    client = make_client()
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get = AsyncMock()
        firmware_version_mock = AsyncMock(return_value="26.1.4")
        if method_name == "_get_nat_destination_rules":
            client.get_host_firmware_version = firmware_version_mock

        result = await getattr(client, method_name)()

        assert result == expected
        client._is_get_endpoint_available.assert_awaited_once_with(api_endpoint)
        client._safe_dict_get.assert_not_awaited()
        if method_name == "_get_nat_destination_rules":
            firmware_version_mock.assert_awaited_once()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("firmware_version", "expected"),
    [
        (
            "26.1.10",
            {
                "manual-src": {
                    "uuid": "manual-src",
                    "description": "Manual",
                    "enabled": "1",
                },
                "auto-src": {
                    "uuid": "auto-src",
                    "description": "Automatic",
                    "enabled": "1",
                    "is_automatic": True,
                },
            },
        ),
        (
            "26.1.11",
            {
                "manual-src": {
                    "uuid": "manual-src",
                    "description": "Manual",
                    "enabled": "1",
                }
            },
        ),
        (
            "26.1.11_4",
            {
                "manual-src": {
                    "uuid": "manual-src",
                    "description": "Manual",
                    "enabled": "1",
                }
            },
        ),
        (
            "not-a-version",
            {
                "manual-src": {
                    "uuid": "manual-src",
                    "description": "Manual",
                    "enabled": "1",
                },
                "auto-src": {
                    "uuid": "auto-src",
                    "description": "Automatic",
                    "enabled": "1",
                    "is_automatic": True,
                },
            },
        ),
    ],
)
async def test_get_nat_source_rules_filters_automatic_rows_for_opnsense_26_1_11(
    make_client: ClientType, firmware_version: str, expected: dict[str, Any]
) -> None:
    """Source NAT should hide OPNsense 26.1.11 generated automatic rows only.

    Args:
        make_client (ClientType): Fixture factory used to configure the source-NAT response.
        firmware_version (str): OPNsense version controlling automatic-row filtering.
        expected (dict[str, Any]): Rule mapping expected after version-specific filtering.
    """
    client = make_client()
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client.get_host_firmware_version = AsyncMock(return_value=firmware_version)
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {"uuid": "manual-src", "description": "Manual", "enabled": "1"},
                    {
                        "uuid": "auto-src",
                        "description": "Automatic",
                        "enabled": "1",
                        "is_automatic": True,
                    },
                ]
            }
        )

        assert await client._get_nat_source_rules() == expected
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_kill_states_returns_normalized_result(make_client: ClientType) -> None:
    """`kill_states` should normalize the diagnostics response.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client = make_client()
    try:
        client._safe_dict_post = AsyncMock(return_value={"result": "ok", "dropped_states": 7})

        result = await client.kill_states("192.0.2.10")

        assert result == {"success": True, "dropped_states": 7}
        client._safe_dict_post.assert_awaited_once_with(
            "/api/diagnostics/firewall/kill_states/",
            payload={"filter": "192.0.2.10"},
        )
    finally:
        await client.async_close()


SOURCE_NAT_PATH = "/api/firewall/source_nat/search_rule"
DNAT_PATH = "/api/firewall/d_nat/search_rule"
SOURCE_NAT_ROWS = {
    "rows": [
        {"uuid": "manual-src", "description": "Manual", "enabled": "1"},
        {"uuid": "auto-src", "description": "Automatic", "enabled": "1", "is_automatic": True},
    ]
}
DNAT_ROWS = {"rows": [{"uuid": "dnat-1", "descr": "Web", "category": "c1", "%category": "Web"}]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("firmware", "automatic_rows_hidden"),
    [
        pytest.param("26.1.10", False, id="below-threshold"),
        pytest.param("26.1.11", True, id="at-threshold"),
        pytest.param("26.2", True, id="above-threshold"),
        pytest.param(None, False, id="missing"),
        pytest.param("not-a-version", False, id="not-comparable"),
    ],
)
async def test_get_firewall_source_nat_filters_automatic_rows_by_firmware(
    scripted_router: tuple[OPNsenseClient, Any], firmware: str | None, automatic_rows_hidden: bool
) -> None:
    """Source NAT hides generated rows only on known firmware that generates them.

    Args:
        scripted_router (tuple[OPNsenseClient, Any]): Client and the Router that scripts it.
        firmware (str | None): Firmware version reported by the Router.
        automatic_rows_hidden (bool): Whether generated rows are expected to be hidden.
    """
    client, router = scripted_router
    router.firmware = firmware
    router.endpoints[SOURCE_NAT_PATH] = SOURCE_NAT_ROWS

    firewall = await client.get_firewall()

    assert set(firewall["nat"]["source_nat"]) == (
        {"manual-src"} if automatic_rows_hidden else {"manual-src", "auto-src"}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("firmware", "categories_mirrored"),
    [
        pytest.param("26.1.3", False, id="below-threshold"),
        pytest.param("26.1.4", True, id="at-threshold"),
        pytest.param("26.2", True, id="above-threshold"),
        pytest.param(None, False, id="missing"),
        pytest.param("not-a-version", False, id="not-comparable"),
    ],
)
async def test_get_firewall_dnat_mirrors_categories_by_firmware(
    scripted_router: tuple[OPNsenseClient, Any], firmware: str | None, categories_mirrored: bool
) -> None:
    """Destination NAT mirrors category keys only on known unified-NAT firmware.

    Args:
        scripted_router (tuple[OPNsenseClient, Any]): Client and the Router that scripts it.
        firmware (str | None): Firmware version reported by the Router.
        categories_mirrored (bool): Whether plural category keys are expected.
    """
    client, router = scripted_router
    router.firmware = firmware
    router.endpoints[DNAT_PATH] = DNAT_ROWS

    rule = (await client.get_firewall())["nat"]["d_nat"]["dnat-1"]

    assert ("categories" in rule) is categories_mirrored
    assert ("%categories" in rule) is categories_mirrored


@pytest.mark.asyncio
async def test_unknown_firmware_for_nat_is_logged_once_per_condition(
    scripted_router: tuple[OPNsenseClient, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """An uncomparable firmware version is logged once at error level, not per query.

    Args:
        scripted_router (tuple[OPNsenseClient, Any]): Client and the Router that scripts it.
        caplog (pytest.LogCaptureFixture): Log capture fixture.
    """
    client, router = scripted_router
    router.firmware = "not-a-version"
    router.endpoints[SOURCE_NAT_PATH] = SOURCE_NAT_ROWS
    router.endpoints[DNAT_PATH] = DNAT_ROWS
    caplog.set_level(logging.DEBUG, logger="aiopnsense")

    await client.get_firewall()
    await client.get_firewall()

    errors = [
        record
        for record in caplog.records
        if record.levelno == logging.ERROR and "cannot be compared" in record.getMessage()
    ]
    assert len(errors) == 2  # one per capability (source NAT, unified NAT), not per call
