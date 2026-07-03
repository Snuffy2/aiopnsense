"""Tests for `aiopnsense.firewall`."""

from collections.abc import Callable
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]
AliasRowType = dict[str, str]
AliasSearchRowsType = list[AliasRowType]
AliasPostResponseType = dict[str, str]
AliasPostResultType = AliasPostResponseType | list[AliasPostResponseType] | None


@pytest.fixture
def toggle_alias_client(make_client: ClientType) -> tuple[OPNsenseClient, Any]:
    """Provide a preconfigured OPNsenseClient for toggle_alias tests.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        tuple[OPNsenseClient, Any]: Client/session tuple from ``make_mock_session_client``.
    """
    client, session = make_mock_session_client(make_client)
    client._use_snake_case = True
    return client, session


@pytest.mark.asyncio
async def test_get_firewall_aggregates_rest_payload(make_client) -> None:
    """`get_firewall` should aggregate all REST-native rule collections."""
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
async def test_get_firewall_rules_skips_invalid_rows(make_client) -> None:
    """Firewall search results should skip malformed, lockout, and automatic rows."""
    client = make_client()
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    "bad-row",
                    None,
                    {"enabled": "1"},
                    {"uuid": "lockout-1", "enabled": "1"},
                    {"uuid": "rule-ok", "enabled": "1", "descr": "Allow"},
                    {
                        "uuid": "automatic-rule",
                        "enabled": "1",
                        "descr": "Plugin generated",
                        "is_automatic": True,
                    },
                    {
                        "uuid": "automatic-string-rule",
                        "enabled": "1",
                        "descr": "Plugin generated string flag",
                        "is_automatic": "1",
                    },
                ]
            }
        )

        result = await client._get_firewall_rules()

        assert result == {
            "rule-ok": {
                "uuid": "rule-ok",
                "enabled": "1",
                "descr": "Allow",
            },
        }
        client._is_get_endpoint_available.assert_awaited_once_with(
            "/api/firewall/filter/search_rule"
        )
        client._safe_dict_get.assert_awaited_once_with("/api/firewall/filter/search_rule")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_nat_source_rules_labels_empty_interface_address_target(
    make_client: ClientType,
) -> None:
    """Source NAT rows with an empty target should expose an interface-address label."""
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
    make_client, method_name, api_endpoint, rows, expected, firmware_version
) -> None:
    """NAT rule helpers should return UUID-keyed mappings from REST search rows."""
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
async def test_uses_unified_nat_template_handles_invalid_firmware_string(
    make_client: Callable[..., Any], caplog: pytest.LogCaptureFixture
) -> None:
    """Legacy normalization fallback should be used when version comparison raises."""
    client = make_client()
    try:
        with caplog.at_level(logging.DEBUG):
            result = client._uses_unified_nat_template("foo")

        assert result is False
        assert (
            "Unable to compare firmware version foo for DNAT category normalization" in caplog.text
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_filters_automatic_source_nat_rules_handles_invalid_firmware_string(
    make_client: Callable[..., Any], caplog: pytest.LogCaptureFixture
) -> None:
    """Verify source NAT filtering is disabled when version comparison fails.

    Args:
        make_client (Callable[..., Any]): Fixture factory returning OPNsense clients.
        caplog (pytest.LogCaptureFixture): Fixture capturing log output.

    Returns:
        None: This test validates source NAT firmware fallback logging.
    """
    client = make_client()
    try:
        with caplog.at_level(logging.DEBUG):
            result = client._filters_automatic_source_nat_rules("foo")

        assert result is False
        assert (
            "Unable to compare firmware version foo for source NAT automatic rule filtering"
            in caplog.text
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "api_endpoint", "expected"),
    [
        ("_get_firewall_rules", "/api/firewall/filter/search_rule", {}),
        ("_get_nat_destination_rules", "/api/firewall/d_nat/search_rule", {}),
        ("_get_nat_one_to_one_rules", "/api/firewall/one_to_one/search_rule", {}),
        ("_get_nat_source_rules", "/api/firewall/source_nat/search_rule", {}),
        ("_get_nat_npt_rules", "/api/firewall/npt/search_rule", {}),
    ],
)
async def test_rule_helpers_return_empty_when_endpoint_unavailable(
    make_client: Callable[..., Any], method_name: str, api_endpoint: str, expected: Any
) -> None:
    """Rule helpers should short-circuit when the related endpoint is unavailable."""
    client = make_client()
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get = AsyncMock()
        if method_name == "_get_nat_destination_rules":
            client.get_host_firmware_version = AsyncMock(return_value="26.1.4")

        result = await getattr(client, method_name)()

        assert result == expected
        client._is_get_endpoint_available.assert_awaited_once_with(api_endpoint)
        client._safe_dict_get.assert_not_awaited()
        if method_name == "_get_nat_destination_rules":
            client.get_host_firmware_version.assert_awaited_once()
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
    """Source NAT should hide OPNsense 26.1.11 generated automatic rows only."""
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
@pytest.mark.parametrize(
    ("toggle_value", "toggle_response", "apply_response", "expected_url", "expected"),
    [
        (None, {"result": "ok"}, {"status": "OK"}, "/api/firewall/filter/toggle_rule/rule1", True),
        (
            "on",
            {"result": "ok"},
            {"status": "OK"},
            "/api/firewall/filter/toggle_rule/rule1/1",
            True,
        ),
        (
            "off",
            {"result": "ok"},
            {"status": "OK"},
            "/api/firewall/filter/toggle_rule/rule1/0",
            True,
        ),
        (
            "on",
            {"result": "failed"},
            {"status": "OK"},
            "/api/firewall/filter/toggle_rule/rule1/1",
            False,
        ),
        (
            "on",
            {"result": "ok"},
            {"status": "failed"},
            "/api/firewall/filter/toggle_rule/rule1/1",
            False,
        ),
    ],
)
async def test_toggle_firewall_rule(
    make_client, toggle_value, toggle_response, apply_response, expected_url, expected
) -> None:
    """Firewall rule toggles should use the right endpoint and require a successful apply."""
    client = make_client()
    try:
        client._safe_dict_post = AsyncMock(side_effect=[toggle_response, apply_response])

        result = await client.toggle_firewall_rule("rule1", toggle_value)

        assert result is expected
        assert client._safe_dict_post.await_args_list[0].args[0] == expected_url
        if toggle_response.get("result") == "failed":
            assert len(client._safe_dict_post.await_args_list) == 1
        else:
            assert client._safe_dict_post.await_args_list[1].args[0] == "/api/firewall/filter/apply"
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("nat_rule_type", "toggle_value", "expected_url"),
    [
        ("source_nat", None, "/api/firewall/source_nat/toggle_rule/rule1"),
        ("source_nat", "on", "/api/firewall/source_nat/toggle_rule/rule1/1"),
        ("source_nat", "off", "/api/firewall/source_nat/toggle_rule/rule1/0"),
        ("d_nat", "on", "/api/firewall/d_nat/toggle_rule/rule1/0"),
        ("d_nat", "off", "/api/firewall/d_nat/toggle_rule/rule1/1"),
    ],
)
async def test_toggle_nat_rule_uses_expected_url(
    make_client, nat_rule_type, toggle_value, expected_url
) -> None:
    """NAT toggles should target the correct REST endpoints, including d_nat inversion."""
    client = make_client()
    try:
        client._safe_dict_post = AsyncMock(side_effect=[{"result": "ok"}, {"status": "OK"}])

        result = await client.toggle_nat_rule(nat_rule_type, "rule1", toggle_value)

        assert result is True
        assert client._safe_dict_post.await_args_list[0].args[0] == expected_url
        assert (
            client._safe_dict_post.await_args_list[1].args[0]
            == f"/api/firewall/{nat_rule_type}/apply"
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_kill_states_returns_normalized_result(make_client) -> None:
    """`kill_states` should normalize the diagnostics response."""
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


@pytest.mark.asyncio
async def test_toggle_alias_flows(make_client: ClientType) -> None:
    """Validate `toggle_alias` failure and success control-flow branches.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates lookup/toggle failure handling and success flow.
    """
    client, _ = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._is_get_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get = AsyncMock()
        client._safe_dict_post = AsyncMock()
        assert await client.toggle_alias("alias1", "on") is False
        client._is_get_endpoint_available.assert_awaited_once_with(
            "/api/firewall/alias/search_item"
        )
        client._safe_dict_get.assert_not_awaited()
        client._safe_dict_post.assert_not_awaited()

        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(return_value={"rows": []})
        assert await client.toggle_alias("missing", "on") is False

        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={"rows": [{"name": "alias1", "uuid": "aid"}]}
        )
        client._safe_dict_post = AsyncMock(return_value={"result": "failed"})
        assert await client.toggle_alias("alias1", "on") is False

        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={"rows": [{"name": "alias1", "uuid": "aid"}]}
        )
        client._safe_dict_post = AsyncMock(
            side_effect=[{"result": "ok"}, {"result": "saved"}, {"status": "ok"}]
        )
        assert await client.toggle_alias("alias1", "off") is True
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_toggle_alias_returns_false_for_non_list_and_apply_failures(
    make_client: ClientType,
) -> None:
    """`toggle_alias` should fail for malformed listings and apply/reconfigure failures.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates malformed-list and apply/reconfigure failure paths.
    """
    client, _ = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(return_value={"rows": "bad"})
        client._safe_dict_post = AsyncMock()
        assert await client.toggle_alias("alias1", "on") is False
        client._safe_dict_post.assert_not_awaited()

        client._safe_dict_get = AsyncMock(
            return_value={"rows": [{"name": "alias1", "uuid": "aid"}]}
        )
        client._safe_dict_post = AsyncMock(side_effect=[{"result": "ok"}, {"result": "not-saved"}])
        assert await client.toggle_alias("alias1", "on") is False
        assert client._safe_dict_post.await_count == 2

        client._safe_dict_get = AsyncMock(
            return_value={"rows": [{"name": "alias1", "uuid": "aid"}]}
        )
        client._safe_dict_post = AsyncMock(
            side_effect=[{"result": "ok"}, {"result": "saved"}, {"status": "failed"}]
        )
        assert await client.toggle_alias("alias1", "on") is False
        assert client._safe_dict_post.await_count == 3
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("use_snake_case", "expected_search", "expected_toggle"),
    [
        (
            True,
            "/api/firewall/alias/search_item",
            "/api/firewall/alias/toggle_item/aid/1",
        ),
        (
            False,
            "/api/firewall/alias/searchItem",
            "/api/firewall/alias/toggleItem/aid/1",
        ),
    ],
)
async def test_firewall_switched_endpoints_follow_selected_case(
    make_client: ClientType,
    use_snake_case: bool,
    expected_search: str,
    expected_toggle: str,
) -> None:
    """Verify alias helpers choose snake_case or camelCase endpoints consistently.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        use_snake_case (bool): Whether the client should prefer snake_case endpoints.
        expected_search (str): Expected alias-search endpoint path.
        expected_toggle (str): Expected alias-toggle endpoint path.

    Returns:
        None: This test validates firewall endpoint selection behavior.
    """
    client, _ = make_mock_session_client(make_client)
    try:
        client._use_snake_case = use_snake_case
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            return_value={"rows": [{"name": "alias1", "uuid": "aid"}]}
        )
        client._safe_dict_post = AsyncMock(
            side_effect=[{"result": "ok"}, {"result": "saved"}, {"status": "ok"}]
        )

        assert await client.toggle_alias("alias1", "on") is True
        client._is_get_endpoint_available.assert_awaited_once_with(expected_search)
        client._safe_dict_get.assert_awaited_once_with(expected_search)
        assert client._safe_dict_post.await_args_list[0].args[0] == expected_toggle
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("safe_get_rows", "safe_post_result", "expected"),
    [
        ([], None, False),
        ([{"name": "a", "uuid": "u1"}], {"result": "failed"}, False),
        (
            [{"name": "a", "uuid": "u1"}],
            [{"result": "ok"}, {"result": "saved"}, {"status": "ok"}],
            True,
        ),
    ],
)
async def test_toggle_alias_scenarios(
    safe_get_rows: AliasSearchRowsType,
    safe_post_result: AliasPostResultType,
    expected: bool,
    toggle_alias_client: tuple[OPNsenseClient, Any],
) -> None:
    """Parametrized alias toggling scenarios should match the final success state.

    Args:
        safe_get_rows (AliasSearchRowsType): Alias rows returned by the search endpoint.
        safe_post_result (AliasPostResultType): Alias toggle/set/reconfigure response payload(s).
        expected (bool): Expected final toggle result.
        toggle_alias_client (tuple[OPNsenseClient, Any]): Preconfigured client/session fixture.

    Returns:
        None: This test validates alias toggle scenarios via assertions only.
    """
    client, _session = toggle_alias_client
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(return_value={"rows": safe_get_rows})

        if not safe_get_rows:
            assert await client.toggle_alias("nope", "on") is expected
            return

        if isinstance(safe_post_result, list):
            client._safe_dict_post = AsyncMock(side_effect=safe_post_result)
        else:
            client._safe_dict_post = AsyncMock(return_value=safe_post_result)

        assert await client.toggle_alias("a", "on") is expected
    finally:
        await client.async_close()
