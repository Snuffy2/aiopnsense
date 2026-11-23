"""Shared Toggle behaviour, checked through every public Toggle method.

Each Toggle that takes a target is one row of ``TOGGLE_CASES``. A row describes the
Router endpoints the Toggle uses and the replies a healthy Router sends; every test
below runs against every row. To cover a new Toggle, add a row.
"""

from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import pytest

from aiopnsense import OPNsenseClient
from aiopnsense.exceptions import OPNsenseError, OPNsenseInvalidArgument
from tests.conftest import RouterReply, ScriptedRouter


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
        setup: Other replies a healthy Router sends, as ``(method, path) -> reply``,
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
    setup: Mapping[tuple[str, str], RouterReply] = field(default_factory=dict)
    target_setup: Mapping[str | None, Mapping[tuple[str, str], RouterReply]] = field(
        default_factory=dict
    )

    def script(self, router: ScriptedRouter, target: str | None) -> None:
        """Script a healthy Router for one request.

        Args:
            router (ScriptedRouter): Router to script.
            target (str | None): Normalised target: ``on``, ``off`` or ``None``.
        """
        for (method, path), reply in {**self.setup, **self.target_setup.get(target, {})}.items():
            router.reply(path, reply, method=method)
        router.reply(self.save_paths[target], self.save_ok[target])
        router.reply(self.apply_path, self.apply_ok)


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
        pytest.param("on", None, id="missing-response"),
        pytest.param("off", {"result": "failed"}, id="failed-response"),
    ],
)
async def test_toggle_unacknowledged_save_returns_false_without_apply(
    case: ToggleCase,
    target: str,
    save_response: RouterReply,
    scripted_router: ScriptedRouter,
) -> None:
    """A missing or failed save reply stops the Toggle before the apply.

    Args:
        case (ToggleCase): Toggle under test.
        target (str): Requested state.
        save_response (RouterReply): Missing or failed reply from the save endpoint.
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    case.script(scripted_router, target)
    scripted_router.reply(case.save_paths[target], save_response)
    client = scripted_router.client()

    assert await case.toggle(client, target) is False
    if case.name == "alias":
        assert not scripted_router.requested(_ALIAS_SET)
    assert not scripted_router.requested(case.apply_path)


@pytest.mark.parametrize("case", _CASE_PARAMS)
@pytest.mark.parametrize(
    ("toggle_on_off", "target"),
    [("on", "on"), ("off", "off"), (None, None), ("toggle", None)],
)
async def test_toggle_acknowledged_save_and_confirmed_apply_returns_true(
    case: ToggleCase,
    toggle_on_off: str | None,
    target: str | None,
    scripted_router: ScriptedRouter,
) -> None:
    """A Toggle succeeds when the Router acknowledges the save and confirms the apply.

    ``None`` and ``"toggle"`` both flip, using the flip endpoint.

    Args:
        case (ToggleCase): Toggle under test.
        toggle_on_off (str | None): Value passed to the public method.
        target (str | None): Target the value means: ``on``, ``off`` or ``None`` (flip).
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    case.script(scripted_router, target)
    client = scripted_router.client()

    assert await case.toggle(client, toggle_on_off) is True
    assert scripted_router.requested(case.save_paths[target])
    if case.name == "alias":
        assert scripted_router.requested(_ALIAS_SET)
    assert scripted_router.requested(case.apply_path)


@pytest.mark.parametrize("case", _CASE_PARAMS)
async def test_toggle_unconfirmed_apply_returns_false(
    case: ToggleCase, scripted_router: ScriptedRouter
) -> None:
    """A Toggle whose apply the Router does not confirm fails.

    Args:
        case (ToggleCase): Toggle under test.
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    case.script(scripted_router, "on")
    scripted_router.reply(case.apply_path, case.apply_failed)
    client = scripted_router.client()

    assert await case.toggle(client, "on") is False


@pytest.mark.parametrize("case", _CASE_PARAMS)
@pytest.mark.parametrize("throw_errors", [False, True])
@pytest.mark.parametrize("bad_target", ["enable", "ON", 1])
async def test_toggle_rejects_unrecognised_target(
    case: ToggleCase, throw_errors: bool, bad_target: Any, scripted_router: ScriptedRouter
) -> None:
    """An unrecognised target is an error and never reaches the Router.

    Args:
        case (ToggleCase): Toggle under test.
        throw_errors (bool): Client ``throw_errors`` setting.
        bad_target (Any): Unrecognised ``toggle_on_off`` value.
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    client = scripted_router.client(throw_errors=throw_errors)

    if throw_errors:
        with pytest.raises(OPNsenseInvalidArgument):
            await case.toggle(client, bad_target)
    else:
        assert await case.toggle(client, bad_target) is False
    assert not scripted_router.requests


@pytest.mark.parametrize("case", _CASE_PARAMS)
async def test_toggle_apply_transport_error_raises_with_throw_errors(
    case: ToggleCase, scripted_router: ScriptedRouter
) -> None:
    """With ``throw_errors`` on, an apply that fails at the transport raises.

    Args:
        case (ToggleCase): Toggle under test.
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    case.script(scripted_router, "on")
    scripted_router.reply(case.apply_path, OPNsenseError("apply failed"))
    client = scripted_router.client(throw_errors=True)

    with pytest.raises(OPNsenseError):
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
    apply_reply: MutableMapping[str, Any], expected: bool, scripted_router: ScriptedRouter
) -> None:
    """An apply counts when its response key holds a string starting with ``ok``.

    The rule is shared by every Toggle, so one Toggle exercises it.

    Args:
        apply_reply (MutableMapping[str, Any]): Reply of the apply endpoint.
        expected (bool): Expected Toggle result.
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    case = next(case for case in TOGGLE_CASES if case.name == "firewall_rule")
    case.script(scripted_router, "on")
    scripted_router.reply(case.apply_path, apply_reply)
    client = scripted_router.client()

    assert await case.toggle(client, "on") is expected


_VPN_CASE_PARAMS = [
    pytest.param(case, id=case.name) for case in TOGGLE_CASES if case.name.startswith("vpn_")
]


@pytest.mark.parametrize("case", _VPN_CASE_PARAMS)
async def test_vpn_toggle_save_in_wrong_state_returns_false_without_apply(
    case: ToggleCase, scripted_router: ScriptedRouter
) -> None:
    """A VPN save that leaves the item in the other state fails and is not applied.

    Args:
        case (ToggleCase): VPN Toggle under test.
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    case.script(scripted_router, "on")
    scripted_router.reply(case.save_paths["on"], {"result": "Disabled", "changed": True})
    client = scripted_router.client()

    assert await case.toggle(client, "on") is False
    assert not scripted_router.requested(case.apply_path)


@pytest.mark.parametrize(
    ("vpn_type", "collection"),
    [("pptp", "servers"), ("ipsec", "servers"), ("ipsec", "clients")],
)
async def test_vpn_toggle_unknown_type_or_collection_returns_false(
    vpn_type: str, collection: str, scripted_router: ScriptedRouter
) -> None:
    """An unknown VPN type or collection returns ``False`` and sends nothing.

    Args:
        vpn_type (str): VPN type passed to the public method.
        collection (str): Collection passed to the public method.
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    client = scripted_router.client()

    assert await client.toggle_vpn_instance(vpn_type, collection, "uuid1", "on") is False
    assert scripted_router.requests == []


_WG_GET = "/api/wireguard/client/get_client/uuid1"
_WG_TOGGLE_ON = "/api/wireguard/client/toggle_client/uuid1/1"


def _wireguard_posts(router: ScriptedRouter) -> list[str]:
    """Return the paths of the WireGuard POSTs the client sent, in order.

    Args:
        router (ScriptedRouter): Scripted Router fixture.

    Returns:
        list[str]: POST paths.
    """
    return [r.path for r in router.requests if r.method == "POST"]


@pytest.mark.parametrize("pending", [False, True], ids=["nothing_pending", "pending_apply"])
async def test_wireguard_toggle_matching_target_skips_save_and_applies_only_pending(
    pending: bool, scripted_router: ScriptedRouter
) -> None:
    """A WireGuard target that already matches sends no toggle; only a pending save is applied.

    Args:
        pending (bool): Whether this client tracks an earlier unapplied save.
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    scripted_router.reply(_WG_GET, {"client": {"enabled": "0"}}, method="GET")
    scripted_router.reply(_WG_TOGGLE_ON, {"result": "Enabled", "changed": True})
    scripted_router.reply(_WIREGUARD_RECONFIGURE, {"result": "ok"})
    client = scripted_router.client()
    if pending:
        scripted_router.reply(_WIREGUARD_RECONFIGURE, {"result": "failed"})
        assert await client.toggle_vpn_instance("wireguard", "clients", "uuid1", "on") is False
        scripted_router.reply(_WIREGUARD_RECONFIGURE, {"result": "ok"})
    scripted_router.reply(_WG_GET, {"client": {"enabled": "1"}}, method="GET")
    scripted_router.requests.clear()

    assert await client.toggle_vpn_instance("wireguard", "clients", "uuid1", "on") is True
    assert _wireguard_posts(scripted_router) == ([_WIREGUARD_RECONFIGURE] if pending else [])
    # A confirmed apply clears the pending save, so the next matching request is a no-op.
    scripted_router.requests.clear()
    assert await client.toggle_vpn_instance("wireguard", "clients", "uuid1", "on") is True
    assert _wireguard_posts(scripted_router) == []


async def test_wireguard_toggle_wrong_direction_is_corrected_once_and_kept_pending(
    scripted_router: ScriptedRouter,
) -> None:
    """A WireGuard flip away from the target gets exactly one corrective flip.

    The Router here keeps answering with the wrong state, so the Toggle fails without
    applying. The item was marked pending before the correction, so a later request
    that matches the saved state applies it. (The corrected-to-target case is covered
    in ``tests/test_vpn.py``.)

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    scripted_router.reply(_WG_GET, {"client": {"enabled": "0"}}, method="GET")
    scripted_router.reply(_WG_TOGGLE_ON, {"result": "Disabled", "changed": True})
    scripted_router.reply(_WIREGUARD_RECONFIGURE, {"result": "ok"})
    client = scripted_router.client()

    assert await client.toggle_vpn_instance("wireguard", "clients", "uuid1", "on") is False
    assert _wireguard_posts(scripted_router) == [_WG_TOGGLE_ON, _WG_TOGGLE_ON]

    scripted_router.requests.clear()
    assert await client.toggle_vpn_instance("wireguard", "clients", "uuid1", "off") is True
    assert _wireguard_posts(scripted_router) == [_WIREGUARD_RECONFIGURE]


@pytest.mark.parametrize(
    "read",
    [None, {}, {"client": "x"}, {"client": {"enabled": 1}}, {"client": {"enabled": "yes"}}],
    ids=["transport_failure", "missing", "malformed_item", "non_string", "unknown_value"],
)
async def test_wireguard_toggle_unreadable_item_returns_false_without_posting(
    read: MutableMapping[str, Any] | None, scripted_router: ScriptedRouter
) -> None:
    """A WireGuard item whose state cannot be read is neither toggled nor applied.

    Args:
        read (MutableMapping[str, Any] | None): Router reply to the state read.
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    scripted_router.reply(_WG_GET, read, method="GET")
    client = scripted_router.client()

    assert await client.toggle_vpn_instance("wireguard", "clients", "uuid1", "on") is False
    assert _wireguard_posts(scripted_router) == []


async def test_wireguard_flip_does_not_read_state(scripted_router: ScriptedRouter) -> None:
    """A WireGuard flip request toggles without reading the item first.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    case = next(case for case in TOGGLE_CASES if case.name == "vpn_wireguard_clients")
    case.script(scripted_router, None)
    client = scripted_router.client()

    assert await case.toggle(client, None) is True
    assert not scripted_router.requested(_WG_GET, method="GET")


@pytest.mark.parametrize("collection", ["clients", "servers", "peers"])
async def test_wireguard_toggle_empty_uuid_or_unknown_collection_sends_nothing(
    collection: str, scripted_router: ScriptedRouter
) -> None:
    """An empty UUID or an unknown collection fails before any request.

    Args:
        collection (str): WireGuard collection passed to the public method.
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    client = scripted_router.client()
    uuid = "uuid1" if collection == "peers" else ""

    assert await client.toggle_vpn_instance("wireguard", collection, uuid, "on") is False
    assert scripted_router.requests == []


@pytest.mark.parametrize("case", _CASE_PARAMS)
async def test_toggle_apply_rule_reads_the_endpoint_key(
    case: ToggleCase, scripted_router: ScriptedRouter
) -> None:
    """Every Toggle applies the shared rule to its own apply endpoint's response key.

    Args:
        case (ToggleCase): Toggle under test.
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    (apply_key,) = case.apply_ok
    case.script(scripted_router, "on")
    scripted_router.reply(case.apply_path, {apply_key: " OK applied\n"})
    client = scripted_router.client()

    assert await case.toggle(client, "on") is True


_ALIAS_SEARCH = "/api/firewall/alias/search_item"
_ALIAS_SET = "/api/firewall/alias/set"
_ALIAS_RECONFIGURE = "/api/firewall/alias/reconfigure"


async def test_toggle_alias_unavailable_search_endpoint_returns_false(
    scripted_router: ScriptedRouter,
) -> None:
    """An alias Toggle sends only the listing read when the alias listing is unavailable.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    client = scripted_router.client()

    assert await client.toggle_alias("alias1", "on") is False
    assert [r.path for r in scripted_router.requests] == [_ALIAS_SEARCH]


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
    listing: MutableMapping[str, Any], scripted_router: ScriptedRouter
) -> None:
    """An alias Toggle fails without saving when the alias cannot be resolved.

    Args:
        listing (MutableMapping[str, Any]): Alias listing the Router returns.
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    scripted_router.reply(_ALIAS_SEARCH, listing, method="GET")
    client = scripted_router.client()

    assert await client.toggle_alias("alias1", "on") is False
    assert not any(r.method == "POST" for r in scripted_router.requests)


@pytest.mark.parametrize("set_reply", [None, {"result": "failed"}, {"result": "not-saved"}])
async def test_toggle_alias_unacknowledged_commit_returns_false_without_reconfigure(
    set_reply: MutableMapping[str, Any] | None, scripted_router: ScriptedRouter
) -> None:
    """An alias Toggle is not reconfigured when its commit was not acknowledged.

    Args:
        set_reply (MutableMapping[str, Any] | None): Router reply to the commit.
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    alias_case = next(case for case in TOGGLE_CASES if case.name == "alias")
    alias_case.script(scripted_router, "on")
    scripted_router.reply(_ALIAS_SET, set_reply)
    client = scripted_router.client()

    assert await client.toggle_alias("alias1", "on") is False
    assert not scripted_router.requested(_ALIAS_RECONFIGURE)


async def test_toggle_alias_uses_camel_case_endpoints_on_older_firmware(
    scripted_router: ScriptedRouter,
) -> None:
    """An alias Toggle lists and toggles through camelCase paths on older firmware.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    scripted_router.reply(
        "/api/firewall/alias/searchItem",
        {"rows": [{"name": "alias1", "uuid": "aid"}]},
        method="GET",
    )
    scripted_router.reply("/api/firewall/alias/toggleItem/aid/1", {"result": "Enabled"})
    scripted_router.reply(_ALIAS_SET, {"result": "saved"})
    scripted_router.reply(_ALIAS_RECONFIGURE, {"status": "ok"})
    client = scripted_router.client()
    client._use_snake_case = False

    assert await client.toggle_alias("alias1", "on") is True
    assert scripted_router.requested("/api/firewall/alias/toggleItem/aid/1")
