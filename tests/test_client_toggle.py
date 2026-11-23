"""Shared Toggle behaviour, checked through every public Toggle method.

Each Toggle that takes a target is one row of ``TOGGLE_CASES``. A row describes the
Router endpoints the Toggle uses and the replies a healthy Router sends; every test
below runs against every row. To cover a new Toggle, add a row.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import aiohttp
import pytest

from aiopnsense import OPNsenseClient
from aiopnsense.exceptions import OPNsenseConnectionError, OPNsenseInvalidArgument
from tests.conftest import MakeClientFactory, make_router_client

SERVER_ERROR = 500
"""HTTP status of a Router reply that fails the request."""

type RouteValue = MutableMapping[str, Any] | list[Any] | int | BaseException
"""A fake Router route: a JSON body, an HTTP status or a transport error."""


@dataclass(frozen=True, slots=True)
class ToggleCase:
    """How one public Toggle method talks to the Router.

    Attributes:
        name: Test id.
        toggle: Calls the public Toggle method with a ``toggle_on_off`` value.
        save_paths: Save endpoint for each target: ``on``, ``off`` and ``None`` (flip).
        save_ok: Successful save reply for each target, keyed like ``save_paths``.
        apply_path: Endpoint of the final apply step.
        apply_ok: Successful reply of the final apply step.
        apply_failed: Reply of the final apply step when it is not confirmed.
        setup: Other routes a healthy Router serves, as ``(method, path) -> value``,
            such as pre-reads or intermediate steps.
        target_setup: Replies that depend on the target, keyed like ``save_paths``,
            such as a state pre-read that must not already match the target.
    """

    name: str
    toggle: Callable[[OPNsenseClient, str | None], Awaitable[bool]]
    save_paths: Mapping[str | None, str]
    save_ok: Mapping[str | None, MutableMapping[str, Any]]
    apply_path: str
    apply_ok: MutableMapping[str, Any]
    apply_failed: MutableMapping[str, Any]
    setup: Mapping[tuple[str, str], RouteValue] = field(default_factory=dict)
    target_setup: Mapping[str | None, Mapping[tuple[str, str], RouteValue]] = field(
        default_factory=dict
    )

    def routes(self, target: str | None) -> dict[tuple[str, str], RouteValue]:
        """Return the routes of a healthy Router for one request.

        Args:
            target (str | None): Normalised target: ``on``, ``off`` or ``None`` (flip).

        Returns:
            dict[tuple[str, str], RouteValue]: Routes keyed by ``(method, path)``.
        """
        routes = {**self.setup, **self.target_setup.get(target, {})}
        routes[("POST", self.save_paths[target])] = self.save_ok[target]
        routes[("POST", self.apply_path)] = self.apply_ok
        return routes

    def posts(self, target: str | None) -> list[str]:
        """Return the POST paths a successful Toggle sends, in order.

        Args:
            target (str | None): Normalised target: ``on``, ``off`` or ``None`` (flip).

        Returns:
            list[str]: The save, any commit in ``setup``, then the apply.
        """
        commits = [path for method, path in self.setup if method == "POST"]
        return [self.save_paths[target], *commits, self.apply_path]


_client_for_router = partial(make_router_client, firmware="26.7.6")
_WIREGUARD_RECONFIGURE = "/api/wireguard/service/reconfigure"


async def _toggle_wireguard(collection: str, client: OPNsenseClient, target: str | None) -> bool:
    """Toggle WireGuard instance ``uuid1`` in a collection.

    Args:
        collection (str): ``clients`` or ``servers``.
        client (OPNsenseClient): Client under test.
        target (str | None): ``toggle_on_off`` value.

    Returns:
        bool: Result of ``toggle_vpn_instance``.
    """
    return await client.toggle_vpn_instance("wireguard", collection, "uuid1", target)


TOGGLE_CASES: list[ToggleCase] = [
    ToggleCase(
        name="firewall_rule",
        toggle=lambda client, target: client.toggle_firewall_rule("rule1", target),
        save_paths={
            "on": "/api/firewall/filter/toggle_rule/rule1/1",
            "off": "/api/firewall/filter/toggle_rule/rule1/0",
            None: "/api/firewall/filter/toggle_rule/rule1",
        },
        save_ok={
            "on": {"result": "Enabled", "changed": True},
            "off": {"result": "Disabled", "changed": True},
            None: {"result": "Disabled", "changed": True},
        },
        apply_path="/api/firewall/filter/apply",
        apply_ok={"status": "OK\n"},
        apply_failed={"status": "failed"},
    ),
    ToggleCase(
        name="vpn_openvpn",
        toggle=lambda client, target: client.toggle_vpn_instance(
            "openvpn", "instances", "uuid1", target
        ),
        save_paths={
            "on": "/api/openvpn/instances/toggle/uuid1/1",
            "off": "/api/openvpn/instances/toggle/uuid1/0",
            None: "/api/openvpn/instances/toggle/uuid1",
        },
        save_ok={
            "on": {"result": "Enabled", "changed": True},
            "off": {"result": "Disabled", "changed": True},
            None: {"result": "Disabled", "changed": True},
        },
        apply_path="/api/openvpn/service/reconfigure",
        apply_ok={"result": "ok"},
        apply_failed={"result": "failed"},
    ),
    ToggleCase(
        name="vpn_ipsec",
        toggle=lambda client, target: client.toggle_vpn_instance(
            "ipsec", "connections", "uuid1", target
        ),
        save_paths={
            "on": "/api/ipsec/connections/toggle_connection/uuid1/1",
            "off": "/api/ipsec/connections/toggle_connection/uuid1/0",
            None: "/api/ipsec/connections/toggle_connection/uuid1",
        },
        save_ok={
            "on": {"result": "Enabled", "changed": True},
            "off": {"result": "Disabled", "changed": True},
            None: {"result": "Disabled", "changed": True},
        },
        apply_path="/api/ipsec/service/reconfigure",
        apply_ok={"status": "ok"},
        apply_failed={"status": "failed"},
    ),
    *(
        ToggleCase(
            name=f"vpn_wireguard_{collection}",
            toggle=partial(_toggle_wireguard, collection),
            save_paths={
                "on": f"/api/wireguard/{item}/toggle_{item}/uuid1/1",
                "off": f"/api/wireguard/{item}/toggle_{item}/uuid1/0",
                None: f"/api/wireguard/{item}/toggle_{item}/uuid1",
            },
            save_ok={
                "on": {"result": "Enabled", "changed": True},
                "off": {"result": "Disabled", "changed": True},
                None: {"result": "Disabled", "changed": True},
            },
            apply_path=_WIREGUARD_RECONFIGURE,
            apply_ok={"result": "ok"},
            apply_failed={"result": "failed"},
            target_setup={
                "on": {
                    ("GET", f"/api/wireguard/{item}/get_{item}/uuid1"): {item: {"enabled": "0"}}
                },
                "off": {
                    ("GET", f"/api/wireguard/{item}/get_{item}/uuid1"): {item: {"enabled": "1"}}
                },
            },
        )
        for collection, item in (("clients", "client"), ("servers", "server"))
    ),
    ToggleCase(
        name="nat_rule_source_nat",
        toggle=lambda client, target: client.toggle_nat_rule("source_nat", "rule1", target),
        save_paths={
            "on": "/api/firewall/source_nat/toggle_rule/rule1/1",
            "off": "/api/firewall/source_nat/toggle_rule/rule1/0",
            None: "/api/firewall/source_nat/toggle_rule/rule1",
        },
        save_ok={
            "on": {"result": "Enabled", "changed": True},
            "off": {"result": "Disabled", "changed": True},
            None: {"result": "Disabled", "changed": True},
        },
        apply_path="/api/firewall/source_nat/apply",
        apply_ok={"status": "OK\n"},
        apply_failed={"status": "failed"},
    ),
    ToggleCase(
        name="nat_rule_d_nat_inverted",
        toggle=lambda client, target: client.toggle_nat_rule("d_nat", "rule1", target),
        save_paths={
            "on": "/api/firewall/d_nat/toggle_rule/rule1/0",
            "off": "/api/firewall/d_nat/toggle_rule/rule1/1",
            None: "/api/firewall/d_nat/toggle_rule/rule1",
        },
        save_ok={
            "on": {"result": "Enabled", "changed": True},
            "off": {"result": "Disabled", "changed": True},
            None: {"result": "Disabled", "changed": True},
        },
        apply_path="/api/firewall/d_nat/apply",
        apply_ok={"status": "OK\n"},
        apply_failed={"status": "failed"},
    ),
    ToggleCase(
        name="alias",
        toggle=lambda client, target: client.toggle_alias("alias1", target),
        save_paths={
            "on": "/api/firewall/alias/toggle_item/aid/1",
            "off": "/api/firewall/alias/toggle_item/aid/0",
            None: "/api/firewall/alias/toggle_item/aid",
        },
        save_ok={
            "on": {"result": "Enabled", "changed": True},
            "off": {"result": "Disabled", "changed": True},
            None: {"result": "Disabled", "changed": True},
        },
        apply_path="/api/firewall/alias/reconfigure",
        apply_ok={"status": "ok"},
        apply_failed={"status": "failed"},
        setup={
            ("GET", "/api/firewall/alias/search_item"): {
                "rows": [{"name": "alias1", "uuid": "aid"}]
            },
            ("POST", "/api/firewall/alias/set"): {"result": "saved"},
        },
    ),
]

_CASE_PARAMS = [pytest.param(case, id=case.name) for case in TOGGLE_CASES]


@pytest.mark.parametrize("case", _CASE_PARAMS)
@pytest.mark.parametrize(
    ("target", "save_response"),
    [
        pytest.param("on", SERVER_ERROR, id="failed-request"),
        pytest.param("off", {"result": "failed"}, id="failed-response"),
    ],
)
async def test_toggle_unacknowledged_save_returns_false_without_apply(
    case: ToggleCase,
    target: str,
    save_response: RouteValue,
    make_client: MakeClientFactory,
) -> None:
    """A failed or unacknowledged save stops the Toggle before the apply.

    The save is the only POST the Router receives, so no apply and no alias commit follows it.

    Args:
        case (ToggleCase): Toggle under test.
        target (str): Requested state.
        save_response (RouteValue): Failing or failed reply of the save endpoint.
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = _client_for_router(
        make_client, {**case.routes(target), ("POST", case.save_paths[target]): save_response}
    )

    assert await case.toggle(client, target) is False
    assert router.paths("POST") == [case.save_paths[target]]


@pytest.mark.parametrize("case", _CASE_PARAMS)
@pytest.mark.parametrize(
    ("toggle_on_off", "target"),
    [("on", "on"), ("off", "off"), (None, None), ("toggle", None)],
)
async def test_toggle_acknowledged_save_and_confirmed_apply_returns_true(
    case: ToggleCase,
    toggle_on_off: str | None,
    target: str | None,
    make_client: MakeClientFactory,
) -> None:
    """A Toggle succeeds when the Router acknowledges the save and confirms the apply.

    ``None`` and ``"toggle"`` both flip, using the flip endpoint. The Router receives
    the save, any commit and the apply, in that order, once each.

    Args:
        case (ToggleCase): Toggle under test.
        toggle_on_off (str | None): Value passed to the public method.
        target (str | None): Target the value means: ``on``, ``off`` or ``None`` (flip).
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = _client_for_router(make_client, case.routes(target))

    assert await case.toggle(client, toggle_on_off) is True
    assert router.paths("POST") == case.posts(target)


@pytest.mark.parametrize("case", _CASE_PARAMS)
async def test_toggle_unconfirmed_apply_returns_false(
    case: ToggleCase, make_client: MakeClientFactory
) -> None:
    """A Toggle whose apply the Router does not confirm fails.

    Args:
        case (ToggleCase): Toggle under test.
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = _client_for_router(
        make_client, {**case.routes("on"), ("POST", case.apply_path): case.apply_failed}
    )

    assert await case.toggle(client, "on") is False
    assert router.paths("POST") == case.posts("on")


@pytest.mark.parametrize("case", _CASE_PARAMS)
@pytest.mark.parametrize("throw_errors", [False, True])
@pytest.mark.parametrize("bad_target", ["enable", "ON", 1])
async def test_toggle_rejects_unrecognised_target(
    case: ToggleCase, throw_errors: bool, bad_target: Any, make_client: MakeClientFactory
) -> None:
    """An unrecognised target is an error and never reaches the Router.

    Args:
        case (ToggleCase): Toggle under test.
        throw_errors (bool): Client ``throw_errors`` setting.
        bad_target (Any): Unrecognised ``toggle_on_off`` value.
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = _client_for_router(make_client, throw_errors=throw_errors)

    if throw_errors:
        with pytest.raises(OPNsenseInvalidArgument):
            await case.toggle(client, bad_target)
    else:
        assert await case.toggle(client, bad_target) is False
    assert not router.requests


@pytest.mark.parametrize("case", _CASE_PARAMS)
async def test_toggle_apply_transport_error_raises_with_throw_errors(
    case: ToggleCase, make_client: MakeClientFactory
) -> None:
    """With ``throw_errors`` on, an apply that fails at the transport raises.

    Args:
        case (ToggleCase): Toggle under test.
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, _router = _client_for_router(
        make_client,
        {
            **case.routes("on"),
            ("POST", case.apply_path): aiohttp.ClientConnectionError("apply failed"),
        },
        throw_errors=True,
    )

    async with asyncio.timeout(2):
        with pytest.raises(OPNsenseConnectionError):
            await case.toggle(client, "on")


@pytest.mark.parametrize(
    ("apply_reply", "expected"),
    [
        ({"status": "OK"}, True),
        ({"status": " ok\n"}, True),
        ({"status": "OK applied"}, True),
        ({"status": "failed"}, False),
        ({"status": 1}, False),
        ({"result": "ok"}, False),
        ({}, False),
    ],
)
async def test_toggle_apply_rule(
    apply_reply: MutableMapping[str, Any], expected: bool, make_client: MakeClientFactory
) -> None:
    """An apply counts when its response key holds a string starting with ``ok``.

    The rule is shared by every Toggle, so one Toggle exercises it.

    Args:
        apply_reply (MutableMapping[str, Any]): Reply of the apply endpoint.
        expected (bool): Expected Toggle result.
        make_client (MakeClientFactory): Client factory fixture.
    """
    case = next(case for case in TOGGLE_CASES if case.name == "firewall_rule")
    client, _router = _client_for_router(
        make_client, {**case.routes("on"), ("POST", case.apply_path): apply_reply}
    )

    assert await case.toggle(client, "on") is expected


_VPN_CASE_PARAMS = [
    pytest.param(case, id=case.name) for case in TOGGLE_CASES if case.name.startswith("vpn_")
]


@pytest.mark.parametrize("case", _VPN_CASE_PARAMS)
async def test_vpn_toggle_save_in_wrong_state_returns_false_without_apply(
    case: ToggleCase, make_client: MakeClientFactory
) -> None:
    """A VPN save that leaves the item in the other state fails and is not applied.

    Args:
        case (ToggleCase): VPN Toggle under test.
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = _client_for_router(
        make_client,
        {
            **case.routes("on"),
            ("POST", case.save_paths["on"]): {"result": "Disabled", "changed": True},
        },
    )

    assert await case.toggle(client, "on") is False
    assert router.count("POST", case.apply_path) == 0


@pytest.mark.parametrize(
    ("vpn_type", "collection"),
    [("pptp", "servers"), ("ipsec", "servers"), ("ipsec", "clients")],
)
async def test_vpn_toggle_unknown_type_or_collection_returns_false(
    vpn_type: str, collection: str, make_client: MakeClientFactory
) -> None:
    """An unknown VPN type or collection returns ``False`` and sends nothing.

    Args:
        vpn_type (str): VPN type passed to the public method.
        collection (str): Collection passed to the public method.
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = _client_for_router(make_client)

    assert await client.toggle_vpn_instance(vpn_type, collection, "uuid1", "on") is False
    assert router.requests == []


_WG_GET = "/api/wireguard/client/get_client/uuid1"
_WG_TOGGLE_ON = "/api/wireguard/client/toggle_client/uuid1/1"


@pytest.mark.parametrize("pending", [False, True], ids=["nothing_pending", "pending_apply"])
async def test_wireguard_toggle_matching_target_skips_save_and_applies_only_pending(
    pending: bool, make_client: MakeClientFactory
) -> None:
    """A WireGuard target that already matches sends no toggle; only a pending save is applied.

    Args:
        pending (bool): Whether this client tracks an earlier unapplied save.
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = _client_for_router(
        make_client,
        {
            ("GET", _WG_GET): {"client": {"enabled": "0"}},
            ("POST", _WG_TOGGLE_ON): {"result": "Enabled", "changed": True},
            ("POST", _WIREGUARD_RECONFIGURE): {"result": "ok"},
        },
    )
    if pending:
        router.routes[("POST", _WIREGUARD_RECONFIGURE)] = {"result": "failed"}
        assert await client.toggle_vpn_instance("wireguard", "clients", "uuid1", "on") is False
        router.routes[("POST", _WIREGUARD_RECONFIGURE)] = {"result": "ok"}
    router.routes[("GET", _WG_GET)] = {"client": {"enabled": "1"}}
    router.requests.clear()

    assert await client.toggle_vpn_instance("wireguard", "clients", "uuid1", "on") is True
    assert router.paths("POST") == ([_WIREGUARD_RECONFIGURE] if pending else [])
    # A confirmed apply clears the pending save, so the next matching request is a no-op.
    router.requests.clear()
    assert await client.toggle_vpn_instance("wireguard", "clients", "uuid1", "on") is True
    assert router.paths("POST") == []


async def test_wireguard_toggle_wrong_direction_is_corrected_once_and_kept_pending(
    make_client: MakeClientFactory,
) -> None:
    """A WireGuard flip away from the target gets exactly one corrective flip.

    The Router here keeps answering with the wrong state, so the Toggle fails without
    applying. The item was marked pending before the correction, so a later request
    that matches the saved state applies it. (The corrected-to-target case is covered
    in ``tests/test_vpn.py``.)

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = _client_for_router(
        make_client,
        {
            ("GET", _WG_GET): {"client": {"enabled": "0"}},
            ("POST", _WG_TOGGLE_ON): {"result": "Disabled", "changed": True},
            ("POST", _WIREGUARD_RECONFIGURE): {"result": "ok"},
        },
    )

    assert await client.toggle_vpn_instance("wireguard", "clients", "uuid1", "on") is False
    assert router.paths("POST") == [_WG_TOGGLE_ON, _WG_TOGGLE_ON]

    router.requests.clear()
    assert await client.toggle_vpn_instance("wireguard", "clients", "uuid1", "off") is True
    assert router.paths("POST") == [_WIREGUARD_RECONFIGURE]


@pytest.mark.parametrize(
    "read",
    [SERVER_ERROR, {}, {"client": "x"}, {"client": {"enabled": 1}}, {"client": {"enabled": "yes"}}],
    ids=["transport_failure", "missing", "malformed_item", "non_string", "unknown_value"],
)
async def test_wireguard_toggle_unreadable_item_returns_false_without_posting(
    read: RouteValue, make_client: MakeClientFactory
) -> None:
    """A WireGuard item whose state cannot be read is neither toggled nor applied.

    Args:
        read (RouteValue): Router reply to the state read.
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = _client_for_router(make_client, {("GET", _WG_GET): read})

    assert await client.toggle_vpn_instance("wireguard", "clients", "uuid1", "on") is False
    assert router.paths("POST") == []


async def test_wireguard_flip_does_not_read_state(make_client: MakeClientFactory) -> None:
    """A WireGuard flip request toggles without reading the item first.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    case = next(case for case in TOGGLE_CASES if case.name == "vpn_wireguard_clients")
    client, router = _client_for_router(make_client, case.routes(None))

    assert await case.toggle(client, None) is True
    assert router.count("GET", _WG_GET) == 0


@pytest.mark.parametrize("collection", ["clients", "servers", "peers"])
async def test_wireguard_toggle_empty_uuid_or_unknown_collection_sends_nothing(
    collection: str, make_client: MakeClientFactory
) -> None:
    """An empty UUID or an unknown collection fails before any request.

    Args:
        collection (str): WireGuard collection passed to the public method.
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = _client_for_router(make_client)
    uuid = "uuid1" if collection == "peers" else ""

    assert await client.toggle_vpn_instance("wireguard", collection, uuid, "on") is False
    assert router.requests == []


@pytest.mark.parametrize("case", _CASE_PARAMS)
async def test_toggle_apply_rule_reads_the_endpoint_key(
    case: ToggleCase, make_client: MakeClientFactory
) -> None:
    """Every Toggle applies the shared rule to its own apply endpoint's response key.

    Args:
        case (ToggleCase): Toggle under test.
        make_client (MakeClientFactory): Client factory fixture.
    """
    (apply_key,) = case.apply_ok
    client, _router = _client_for_router(
        make_client, {**case.routes("on"), ("POST", case.apply_path): {apply_key: " OK applied\n"}}
    )

    assert await case.toggle(client, "on") is True


_ALIAS_SEARCH = "/api/firewall/alias/search_item"
_ALIAS_SET = "/api/firewall/alias/set"
_ALIAS_RECONFIGURE = "/api/firewall/alias/reconfigure"


async def test_toggle_alias_unavailable_search_endpoint_returns_false(
    make_client: MakeClientFactory,
) -> None:
    """An alias Toggle sends only the listing read when the alias listing is unavailable.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = _client_for_router(make_client)

    assert await client.toggle_alias("alias1", "on") is False
    assert router.count("GET", _ALIAS_SEARCH) == 1
    assert router.paths("POST") == []


@pytest.mark.parametrize(
    "listing",
    [
        {"rows": "bad"},
        {"rows": []},
        {"rows": ["junk", {"name": "other", "uuid": "x"}]},
        {"rows": [{"name": "alias1"}]},
    ],
    ids=["malformed", "empty", "unknown_alias", "missing_uuid"],
)
async def test_toggle_alias_lookup_failure_returns_false(
    listing: MutableMapping[str, Any], make_client: MakeClientFactory
) -> None:
    """An alias Toggle fails without saving when the alias cannot be resolved.

    Args:
        listing (MutableMapping[str, Any]): Alias listing the Router returns.
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = _client_for_router(make_client, {("GET", _ALIAS_SEARCH): listing})

    assert await client.toggle_alias("alias1", "on") is False
    assert router.paths("POST") == []


@pytest.mark.parametrize("set_reply", [SERVER_ERROR, {"result": "failed"}, {"result": "not-saved"}])
async def test_toggle_alias_unacknowledged_commit_returns_false_without_reconfigure(
    set_reply: RouteValue, make_client: MakeClientFactory
) -> None:
    """An alias Toggle is not reconfigured when its commit was not acknowledged.

    Args:
        set_reply (RouteValue): Router reply to the commit.
        make_client (MakeClientFactory): Client factory fixture.
    """
    alias_case = next(case for case in TOGGLE_CASES if case.name == "alias")
    client, router = _client_for_router(
        make_client, {**alias_case.routes("on"), ("POST", _ALIAS_SET): set_reply}
    )

    assert await client.toggle_alias("alias1", "on") is False
    assert router.count("POST", _ALIAS_SET) == 1
    assert router.count("POST", _ALIAS_RECONFIGURE) == 0


async def test_toggle_alias_uses_camel_case_endpoints_on_older_firmware(
    make_client: MakeClientFactory,
) -> None:
    """An alias Toggle lists and toggles through camelCase paths on older firmware.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, router = _client_for_router(
        make_client,
        {
            ("GET", "/api/firewall/alias/searchItem"): {
                "rows": [{"name": "alias1", "uuid": "aid"}]
            },
            ("POST", "/api/firewall/alias/toggleItem/aid/1"): {"result": "Enabled"},
            ("POST", _ALIAS_SET): {"result": "saved"},
            ("POST", _ALIAS_RECONFIGURE): {"status": "ok"},
        },
        firmware="25.1.0",
    )

    assert await client.toggle_alias("alias1", "on") is True
    assert router.paths("POST") == [
        "/api/firewall/alias/toggleItem/aid/1",
        _ALIAS_SET,
        _ALIAS_RECONFIGURE,
    ]
    assert router.count("GET", _ALIAS_SEARCH) == 0
