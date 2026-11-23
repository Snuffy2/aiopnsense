"""Tests for `aiopnsense.firewall`."""

from collections.abc import Callable
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseClient, OPNsenseConnectionError, OPNsensePrivilegeMissing
from tests.conftest import FakeRouter, install_fake_router, make_mock_session_client

ClientType = Callable[..., OPNsenseClient]

RULES_DOWNLOAD_PATH = "/api/firewall/filter/download_rules"
ALIAS_SEARCH_PATH = "/api/firewall/alias/search_item"
RULE_HELPER_PATHS = {
    "_get_firewall_rules": RULES_DOWNLOAD_PATH,
    "_get_nat_destination_rules": "/api/firewall/d_nat/search_rule",
    "_get_nat_one_to_one_rules": "/api/firewall/one_to_one/search_rule",
    "_get_nat_source_rules": "/api/firewall/source_nat/search_rule",
    "_get_nat_npt_rules": "/api/firewall/npt/search_rule",
}
RULE_HELPER_GROUPS = {
    "_get_firewall_rules": ("rules", None),
    "_get_nat_destination_rules": ("nat", "d_nat"),
    "_get_nat_one_to_one_rules": ("nat", "one_to_one"),
    "_get_nat_source_rules": ("nat", "source_nat"),
    "_get_nat_npt_rules": ("nat", "npt"),
}
FAILURE_CASES = [
    pytest.param(404, id="missing"),
    pytest.param(403, id="permission-denied"),
    pytest.param(500, id="server-error"),
    pytest.param(TimeoutError("timed out"), id="timeout"),
]
THROW_CASES = [
    pytest.param(403, OPNsensePrivilegeMissing, id="permission-denied"),
    pytest.param(500, OPNsenseConnectionError, id="server-error"),
    pytest.param(TimeoutError("timed out"), OPNsenseConnectionError, id="timeout"),
]


async def _read_rules(
    make_client: ClientType,
    method_name: str,
    body: Any,
    *,
    firmware_version: str | None = None,
    throw_errors: bool = False,
) -> tuple[Any, FakeRouter]:
    """Call a rule helper against a Router answering its endpoint with ``body``.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        method_name (str): Rule helper invoked on the client.
        body (Any): Response the Router gives for the helper endpoint.
        firmware_version (str | None): Firmware version returned by the firmware lookup.
        throw_errors (bool): Whether the client raises public errors.

    Returns:
        tuple[Any, FakeRouter]: Helper result and the fake Router that served it.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client, session, routes={("GET", RULE_HELPER_PATHS[method_name]): body}
    )
    client.get_host_firmware_version = AsyncMock(return_value=firmware_version)
    if throw_errors:
        client.toggle_throwing_errors(True)
    try:
        return await getattr(client, method_name)(), router
    finally:
        await client.async_close()


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
    result, router = await _read_rules(
        make_client,
        "_get_firewall_rules",
        "@uuid;enabled;description\n"
        'rule-ok;1;"Allow; DNS"\n'
        'rule-extra;1;"Has; extra";extra\n'
        ";1;Missing UUID\n"
        "lockout-1;1;Lockout\n",
    )

    assert result == {
        "rule-ok": {
            "uuid": "rule-ok",
            "enabled": "1",
            "description": "Allow; DNS",
        },
    }
    assert router.count("GET", RULES_DOWNLOAD_PATH) == 1


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
    result, _router = await _read_rules(make_client, "_get_firewall_rules", response)

    assert result == {expected["uuid"]: expected}


@pytest.mark.asyncio
async def test_get_nat_source_rules_labels_empty_interface_address_target(
    make_client: ClientType,
) -> None:
    """Source NAT rows with an empty target should expose an interface-address label.

    Args:
        make_client (ClientType): Fixture factory used to configure the source-NAT response.
    """
    result, _router = await _read_rules(
        make_client,
        "_get_nat_source_rules",
        {
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
        },
        firmware_version="26.7",
    )

    assert result["src-interface-address"]["target"] == ""
    assert result["src-interface-address"]["%target"] == "WAN address"


@pytest.mark.asyncio
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
    result, router = await _read_rules(
        make_client, method_name, {"rows": rows}, firmware_version=firmware_version
    )

    assert result == expected
    assert router.count("GET", api_endpoint) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", list(RULE_HELPER_PATHS))
@pytest.mark.parametrize("route_value", FAILURE_CASES)
async def test_rule_helpers_return_empty_when_endpoint_unavailable(
    make_client: ClientType, method_name: str, route_value: Any
) -> None:
    """Public aggregation returns an empty rule group for unavailable endpoints.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        method_name (str): Rule helper invoked for the unavailable endpoint.
        route_value (Any): Response the Router gives for the helper endpoint.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client, session, routes={("GET", RULE_HELPER_PATHS[method_name]): route_value}
    )
    client._use_snake_case = True
    client.get_host_firmware_version = AsyncMock(return_value="26.1.4")
    group = RULE_HELPER_GROUPS[method_name]
    try:
        result = await client.get_firewall()
        rules = result[group[0]] if group[1] is None else result[group[0]][group[1]]
        assert rules == {}
        assert router.count("GET", RULE_HELPER_PATHS[method_name]) == 1
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
    result, _router = await _read_rules(
        make_client,
        "_get_nat_source_rules",
        {
            "rows": [
                {"uuid": "manual-src", "description": "Manual", "enabled": "1"},
                {
                    "uuid": "auto-src",
                    "description": "Automatic",
                    "enabled": "1",
                    "is_automatic": True,
                },
            ]
        },
        firmware_version=firmware_version,
    )

    assert result == expected


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


async def _toggle_alias(
    make_client: ClientType, search_body: Any, *, throw_errors: bool = False
) -> tuple[bool, FakeRouter]:
    """Run ``toggle_alias`` against a Router answering the alias search with ``search_body``.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        search_body (Any): Response the Router gives for the alias search endpoint.
        throw_errors (bool): Whether the client raises public errors.

    Returns:
        tuple[bool, FakeRouter]: Result and the fake Router.
    """
    client, session = make_mock_session_client(make_client)
    client._use_snake_case = True
    router = install_fake_router(client, session, routes={("GET", ALIAS_SEARCH_PATH): search_body})
    if throw_errors:
        client.toggle_throwing_errors(True)
    try:
        return await client.toggle_alias("alias1", "on"), router
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("route_value", FAILURE_CASES)
async def test_toggle_alias_returns_false_when_alias_lookup_unavailable(
    make_client: ClientType, route_value: Any
) -> None:
    """`toggle_alias` returns False without posting when the alias lookup is absent or failing.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        route_value (Any): Response the Router gives for the alias search endpoint.
    """
    result, router = await _toggle_alias(make_client, route_value)

    assert result is False
    assert router.count("GET", ALIAS_SEARCH_PATH) == 1
    assert router.count("POST") == 0


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
    firmware_router: tuple[OPNsenseClient, Any], firmware: str | None, automatic_rows_hidden: bool
) -> None:
    """Source NAT hides generated rows only on known firmware that generates them.

    Args:
        firmware_router (tuple[OPNsenseClient, Any]): Client and the Router that scripts it.
        firmware (str | None): Firmware version reported by the Router.
        automatic_rows_hidden (bool): Whether generated rows are expected to be hidden.
    """
    client, router = firmware_router
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
    firmware_router: tuple[OPNsenseClient, Any], firmware: str | None, categories_mirrored: bool
) -> None:
    """Destination NAT mirrors category keys only on known unified-NAT firmware.

    Args:
        firmware_router (tuple[OPNsenseClient, Any]): Client and the Router that scripts it.
        firmware (str | None): Firmware version reported by the Router.
        categories_mirrored (bool): Whether plural category keys are expected.
    """
    client, router = firmware_router
    router.firmware = firmware
    router.endpoints[DNAT_PATH] = DNAT_ROWS

    rule = (await client.get_firewall())["nat"]["d_nat"]["dnat-1"]

    assert ("categories" in rule) is categories_mirrored
    assert ("%categories" in rule) is categories_mirrored


@pytest.mark.asyncio
async def test_unknown_firmware_for_nat_is_logged_once_per_condition(
    firmware_router: tuple[OPNsenseClient, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """An uncomparable firmware version is logged once at error level, not per query.

    Args:
        firmware_router (tuple[OPNsenseClient, Any]): Client and the Router that scripts it.
        caplog (pytest.LogCaptureFixture): Log capture fixture.
    """
    client, router = firmware_router
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
