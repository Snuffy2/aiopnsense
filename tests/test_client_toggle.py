"""Shared Toggle behaviour, checked through every public Toggle method.

Each Toggle that takes a target is one row of ``TOGGLE_CASES``. A row describes the
Router endpoints the Toggle uses and the replies a healthy Router sends; every test
below runs against every row. To cover a new Toggle, add a row.
"""

from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
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
    """

    name: str
    toggle: Callable[[OPNsenseClient, str | None], Awaitable[bool | None]]
    save_paths: Mapping[str | None, str]
    save_ok: Mapping[str | None, MutableMapping[str, Any]]
    apply_path: str
    apply_ok: MutableMapping[str, Any]
    apply_failed: MutableMapping[str, Any]
    setup: Mapping[tuple[str, str], RouterReply] = field(default_factory=dict)

    def script(self, router: ScriptedRouter, target: str | None) -> None:
        """Script a healthy Router for one request.

        Args:
            router (ScriptedRouter): Router to script.
            target (str | None): Normalised target: ``on``, ``off`` or ``None``.
        """
        for (method, path), reply in self.setup.items():
            router.reply(path, reply, method=method)
        router.reply(self.save_paths[target], self.save_ok[target])
        router.reply(self.apply_path, self.apply_ok)


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
]

_CASE_PARAMS = [pytest.param(case, id=case.name) for case in TOGGLE_CASES]


@pytest.mark.parametrize("case", _CASE_PARAMS)
async def test_toggle_unacknowledged_save_returns_false_without_apply(
    case: ToggleCase, scripted_router: ScriptedRouter
) -> None:
    """A save the Router never acknowledged fails the Toggle and is not applied.

    Args:
        case (ToggleCase): Toggle under test.
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    case.script(scripted_router, "on")
    scripted_router.reply(case.save_paths["on"], None)
    client = scripted_router.client()

    assert await case.toggle(client, "on") is False
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
    assert scripted_router.requested(case.apply_path)


@pytest.mark.parametrize("case", _CASE_PARAMS)
async def test_toggle_failed_save_returns_false_without_apply(
    case: ToggleCase, scripted_router: ScriptedRouter
) -> None:
    """A save the Router reports as ``failed`` fails the Toggle and is not applied.

    Args:
        case (ToggleCase): Toggle under test.
        scripted_router (ScriptedRouter): Scripted Router fixture.
    """
    case.script(scripted_router, "off")
    scripted_router.reply(case.save_paths["off"], {"result": "failed"})
    client = scripted_router.client()

    assert await case.toggle(client, "off") is False
    assert not scripted_router.requested(case.apply_path)


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
    case.script(scripted_router, None)
    client = scripted_router.client(throw_errors=throw_errors)

    if throw_errors:
        with pytest.raises(OPNsenseInvalidArgument):
            await case.toggle(client, bad_target)
    else:
        assert await case.toggle(client, bad_target) is None
    assert not scripted_router.requested(case.save_paths[None])
    assert not scripted_router.requested(case.apply_path)


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
    case = TOGGLE_CASES[0]
    case.script(scripted_router, "on")
    scripted_router.reply(case.apply_path, apply_reply)
    client = scripted_router.client()

    assert await case.toggle(client, "on") is expected
