"""Tests for `aiopnsense.firewall`."""

from collections.abc import Callable
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
async def test_uses_unified_nat_template_handles_invalid_firmware_string(
    make_client: ClientType,
) -> None:
    """Legacy normalization fallback should be used when version comparison raises.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client = make_client()
    try:
        result = client._uses_unified_nat_template("foo")

        assert result is False
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_filters_automatic_source_nat_rules_handles_invalid_firmware_string(
    make_client: ClientType,
) -> None:
    """Verify source NAT filtering is disabled when version comparison fails.

    Args:
        make_client (ClientType): Fixture factory returning OPNsense clients.
    Returns:
        None: This test validates the source NAT firmware fallback.
    """
    client = make_client()
    try:
        result = client._filters_automatic_source_nat_rules("foo")

        assert result is False
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", list(RULE_HELPER_PATHS))
@pytest.mark.parametrize("route_value", FAILURE_CASES)
async def test_rule_helpers_return_empty_when_endpoint_unavailable(
    make_client: ClientType, method_name: str, route_value: Any
) -> None:
    """Rule helpers return an empty mapping for absent or failing endpoints in default mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        method_name (str): Rule helper invoked for the unavailable endpoint.
        route_value (Any): Response the Router gives for the helper endpoint.
    """
    result, router = await _read_rules(
        make_client, method_name, route_value, firmware_version="26.1.4"
    )

    assert result == {}
    assert router.count("GET", RULE_HELPER_PATHS[method_name]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", list(RULE_HELPER_PATHS))
@pytest.mark.parametrize(("route_value", "expected_error"), THROW_CASES)
async def test_rule_helpers_raise_in_throw_mode(
    make_client: ClientType,
    method_name: str,
    route_value: Any,
    expected_error: type[Exception],
) -> None:
    """Rule helpers raise a public error for permission and transient failures in throw mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        method_name (str): Rule helper invoked for the failing endpoint.
        route_value (Any): Response the Router gives for the helper endpoint.
        expected_error (type[Exception]): Public exception type expected from the call.
    """
    with pytest.raises(expected_error):
        await _read_rules(
            make_client,
            method_name,
            route_value,
            firmware_version="26.1.4",
            throw_errors=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", list(RULE_HELPER_PATHS))
async def test_rule_helpers_missing_endpoint_does_not_raise_in_throw_mode(
    make_client: ClientType, method_name: str
) -> None:
    """A missing rule endpoint (404) is an empty result, not an error, even in throw mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        method_name (str): Rule helper invoked for the missing endpoint.
    """
    result, _router = await _read_rules(
        make_client, method_name, 404, firmware_version="26.1.4", throw_errors=True
    )

    assert result == {}


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
    make_client: ClientType,
    search_body: Any,
    *,
    post_payloads: list[Any] | None = None,
    use_snake_case: bool = True,
    throw_errors: bool = False,
    toggle: str | None = "on",
    alias: str = "alias1",
) -> tuple[bool, FakeRouter]:
    """Run ``toggle_alias`` against a Router answering the alias search with ``search_body``.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        search_body (Any): Response the Router gives for the alias search endpoint.
        post_payloads (list[Any] | None): Ordered toggle, save and apply responses.
        use_snake_case (bool): Whether the client prefers snake_case endpoints.
        throw_errors (bool): Whether the client raises public errors.
        toggle (str | None): Target state passed to ``toggle_alias``.
        alias (str): Alias name passed to ``toggle_alias``.

    Returns:
        tuple[bool, FakeRouter]: Result and the fake Router.
    """
    client, session = make_mock_session_client(make_client)
    client._use_snake_case = use_snake_case
    search_path = ALIAS_SEARCH_PATH if use_snake_case else "/api/firewall/alias/searchItem"
    router = install_fake_router(
        client,
        session,
        post_payloads=post_payloads,
        routes={("GET", search_path): search_body},
    )
    if throw_errors:
        client.toggle_throwing_errors(True)
    try:
        return await client.toggle_alias(alias, toggle), router
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


@pytest.mark.asyncio
@pytest.mark.parametrize(("route_value", "expected_error"), THROW_CASES)
async def test_toggle_alias_raises_in_throw_mode(
    make_client: ClientType, route_value: Any, expected_error: type[Exception]
) -> None:
    """`toggle_alias` raises a public error for permission and transient lookup failures.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        route_value (Any): Response the Router gives for the alias search endpoint.
        expected_error (type[Exception]): Public exception type expected from the call.
    """
    with pytest.raises(expected_error):
        await _toggle_alias(make_client, route_value, throw_errors=True)


@pytest.mark.asyncio
async def test_toggle_alias_missing_lookup_does_not_raise_in_throw_mode(
    make_client: ClientType,
) -> None:
    """A missing alias endpoint (404) returns False even in throw mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    result, router = await _toggle_alias(make_client, 404, throw_errors=True)

    assert result is False
    assert router.count("POST") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("search_body", "post_payloads", "expected", "post_count"),
    [
        pytest.param({"rows": []}, [], False, 0, id="alias-not-found"),
        pytest.param({"rows": "bad"}, [], False, 0, id="rows-not-a-list"),
        pytest.param(
            {"rows": [{"name": "alias1", "uuid": "aid"}]},
            [{"result": "failed"}],
            False,
            1,
            id="toggle-failed",
        ),
        pytest.param(
            {"rows": [{"name": "alias1", "uuid": "aid"}]},
            [{"result": "ok"}, {"result": "not-saved"}],
            False,
            2,
            id="save-failed",
        ),
        pytest.param(
            {"rows": [{"name": "alias1", "uuid": "aid"}]},
            [{"result": "ok"}, {"result": "saved"}, {"status": "failed"}],
            False,
            3,
            id="apply-failed",
        ),
        pytest.param(
            {"rows": [{"name": "alias1", "uuid": "aid"}]},
            [{"result": "ok"}, {"result": "saved"}, {"status": "ok"}],
            True,
            3,
            id="success",
        ),
    ],
)
async def test_toggle_alias_flows(
    make_client: ClientType,
    search_body: Any,
    post_payloads: list[Any],
    expected: bool,
    post_count: int,
) -> None:
    """`toggle_alias` follows the lookup, toggle, save and apply flow with one lookup request.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        search_body (Any): Alias search response from the Router.
        post_payloads (list[Any]): Ordered toggle, save and apply responses.
        expected (bool): Expected final result.
        post_count (int): Number of POST calls expected.
    """
    result, router = await _toggle_alias(
        make_client, search_body, post_payloads=post_payloads, toggle="off"
    )

    assert result is expected
    assert router.count("GET", ALIAS_SEARCH_PATH) == 1
    post_paths = [
        path.removeprefix("http://localhost")
        for method, path, _payload in router.requests
        if method == "POST"
    ]
    expected_prefix = [
        "/api/firewall/alias/toggle_item/aid/0",
        "/api/firewall/alias/set",
        "/api/firewall/alias/reconfigure",
    ][:post_count]
    assert post_paths == expected_prefix


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
    """
    result, router = await _toggle_alias(
        make_client,
        {"rows": [{"name": "alias1", "uuid": "aid"}]},
        post_payloads=[{"result": "ok"}, {"result": "saved"}, {"status": "ok"}],
        use_snake_case=use_snake_case,
    )

    assert result is True
    assert router.count("GET", expected_search) == 1
    assert [
        path.removeprefix("http://localhost")
        for method, path, _payload in router.requests
        if method == "POST"
    ] == [expected_toggle, "/api/firewall/alias/set", "/api/firewall/alias/reconfigure"]
