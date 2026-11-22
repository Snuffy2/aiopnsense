"""Shared Toggle machinery for OPNsenseClient.

A Toggle saves an item's enabled state and then applies it, as one operation (see
ADR 0001). Domain mixins describe a Toggle as a :class:`ToggleSpec` and hand it to
``_run_toggle``; this module owns the target parsing, the save rule and the apply
rule, so each exists once.
"""

from collections.abc import MutableMapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from ._typing import AiopnsenseClientProtocol
from .exceptions import OPNsenseInvalidArgument
from .helpers import _LOGGER

TOGGLE_RESULT_KEY = "result"
"""Response key a save endpoint reports its outcome in."""

TOGGLE_ITEM_RESULTS: frozenset[str] = frozenset({"Enabled", "Disabled"})
"""Save results of OPNsense's shared item toggle (``ApiMutableModelControllerBase::toggleBase``).

It reports ``failed`` for a missing item or rejected target.
"""


class ToggleTarget(StrEnum):
    """The state a Toggle asks for."""

    ON = "on"
    OFF = "off"
    FLIP = "toggle"

    def endpoint_suffix(self, *, inverted: bool = False) -> str:
        """Return the toggle URL suffix OPNsense item toggles use for this target.

        Args:
            inverted (bool): Whether the endpoint stores a *disabled* flag, so ``on``
                maps to ``/0`` (for example NAT port forwards).

        Returns:
            str: ``/1``, ``/0``, or ``""`` for a flip.
        """
        if self is ToggleTarget.FLIP:
            return ""
        return "/1" if (self is ToggleTarget.ON) != inverted else "/0"


@dataclass(frozen=True, slots=True)
class ToggleStep:
    """One request in a Toggle and the rule that decides whether it succeeded.

    A step with ``accepted_results`` succeeds when ``response_key`` holds exactly one
    of those strings (a save). A step without them uses the shared apply rule: the
    value is a string that, stripped and lower-cased, starts with ``ok``.

    Attributes:
        endpoint: API path to request.
        response_key: Key of the response mapping that reports the outcome.
        accepted_results: Exact values that mean success, or ``None`` for the apply rule.
        method: HTTP method of the request.
        payload: POST body, if any.
    """

    endpoint: str
    response_key: str
    accepted_results: frozenset[str] | None = None
    method: Literal["GET", "POST"] = "POST"
    payload: MutableMapping[str, Any] | None = None

    @classmethod
    def save(
        cls,
        endpoint: str,
        accepted_results: frozenset[str],
        *,
        response_key: str = TOGGLE_RESULT_KEY,
        payload: MutableMapping[str, Any] | None = None,
    ) -> ToggleStep:
        """Describe a POST that saves a change.

        Args:
            endpoint (str): API path to POST.
            accepted_results (frozenset[str]): Values of ``response_key`` that confirm
                the save.
            response_key (str): Key that reports the outcome.
            payload (MutableMapping[str, Any] | None): POST body, if any.

        Returns:
            ToggleStep: The save step.
        """
        return cls(endpoint, response_key, accepted_results, "POST", payload)

    @classmethod
    def apply(
        cls,
        endpoint: str,
        *,
        response_key: str = "status",
        method: Literal["GET", "POST"] = "POST",
        payload: MutableMapping[str, Any] | None = None,
    ) -> ToggleStep:
        """Describe a request that makes a saved change take effect.

        Args:
            endpoint (str): API path to request.
            response_key (str): Key that reports the outcome.
            method (Literal["GET", "POST"]): HTTP method.
            payload (MutableMapping[str, Any] | None): POST body, if any.

        Returns:
            ToggleStep: The apply step.
        """
        return cls(endpoint, response_key, None, method, payload)

    def succeeded(self, response: MutableMapping[str, Any]) -> bool:
        """Return whether the Router's response confirms this step.

        Args:
            response (MutableMapping[str, Any]): Response mapping; empty after a
                transport failure.

        Returns:
            bool: ``True`` when the response confirms the step.
        """
        value = response.get(self.response_key)
        if not isinstance(value, str):
            return False
        if self.accepted_results is not None:
            return value in self.accepted_results
        return value.strip().lower().startswith("ok")


@dataclass(frozen=True, slots=True)
class ToggleSpec:
    """Everything needed to run one Toggle.

    Attributes:
        label: Public method name, used in log messages.
        target: Requested target.
        save: Ordered save steps; each must succeed before the next is sent.
        apply: Ordered apply steps, sent only after every save step succeeded.
    """

    label: str
    target: ToggleTarget
    save: tuple[ToggleStep, ...]
    apply: tuple[ToggleStep, ...]


class ClientToggleMixin(AiopnsenseClientProtocol):
    """Toggle machinery shared by the domain mixins."""

    @staticmethod
    def _parse_toggle_target(toggle_on_off: object) -> ToggleTarget:
        """Turn a public ``toggle_on_off`` argument into a target.

        Args:
            toggle_on_off (object): ``on``, ``off``, or ``None`` or ``toggle`` to flip.

        Returns:
            ToggleTarget: The requested target.

        Raises:
            OPNsenseInvalidArgument: The value is not one of the accepted values.
        """
        if toggle_on_off is None:
            return ToggleTarget.FLIP
        if isinstance(toggle_on_off, str):
            try:
                return ToggleTarget(toggle_on_off)
            except ValueError:
                pass
        raise OPNsenseInvalidArgument("`toggle_on_off` must be 'on', 'off', 'toggle', or None.")

    async def _send_toggle_step(self, step: ToggleStep) -> MutableMapping[str, Any]:
        """Send one Toggle step to the Router.

        Args:
            step (ToggleStep): Step to send.

        Returns:
            MutableMapping[str, Any]: Response mapping; empty after a transport failure.
        """
        if step.method == "GET":
            return await self._safe_dict_get(step.endpoint)
        return await self._safe_dict_post(step.endpoint, payload=step.payload)

    async def _run_toggle(self, spec: ToggleSpec) -> bool:
        """Save and then apply a Toggle, stopping at the first unconfirmed step.

        Transport errors propagate when ``throw_errors`` is on; otherwise they arrive
        as an empty response and fail the step.

        Args:
            spec (ToggleSpec): Toggle to run.

        Returns:
            bool: ``True`` when every save and apply step was confirmed, otherwise
                ``False``. No apply step is sent unless every save step succeeded.
        """
        for phase, steps in (("save", spec.save), ("apply", spec.apply)):
            for step in steps:
                response = await self._send_toggle_step(step)
                if not step.succeeded(response):
                    _LOGGER.debug(
                        "[%s] %s step %s not confirmed for target %s. response: %s",
                        spec.label,
                        phase,
                        step.endpoint,
                        spec.target,
                        response,
                    )
                    return False
        return True
