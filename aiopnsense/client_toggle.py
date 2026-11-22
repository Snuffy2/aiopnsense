"""Shared Toggle machinery for OPNsenseClient.

A Toggle saves an item's enabled state and then applies it, as one operation (see
ADR 0001). Domain mixins describe a Toggle as a :class:`ToggleSpec` and hand it to
``_run_toggle``; this module owns the target parsing, the save rule and the apply
rule, so each exists once. It also owns the ADR 0001 state of VPN Instance Toggles:
the per-service locks, the pending-apply set and the shutdown generation.
"""

import asyncio
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property, partial
import logging
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

VPN_TOGGLE_SERVICES: tuple[str, ...] = ("openvpn", "wireguard", "ipsec")
"""VPN services whose Toggles share one lock and one pending-apply set per service."""


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
        endpoint: API path to request (the snake_case path when the endpoint name
            depends on the firmware).
        response_key: Key of the response mapping that reports the outcome.
        accepted_results: Exact values that mean success, or ``None`` for the apply rule.
        method: HTTP method of the request.
        payload: POST body, if any.
        camel_case_endpoint: API path on older camelCase firmware, or ``None`` when
            ``endpoint`` is the same on every firmware.
        failure_log_level: Log level for an unconfirmed response.
    """

    endpoint: str
    response_key: str
    accepted_results: frozenset[str] | None = None
    method: Literal["GET", "POST"] = "POST"
    payload: MutableMapping[str, Any] | None = None
    camel_case_endpoint: str | None = None
    failure_log_level: int = logging.DEBUG

    @classmethod
    def save(
        cls,
        endpoint: str,
        accepted_results: frozenset[str],
        *,
        response_key: str = TOGGLE_RESULT_KEY,
        payload: MutableMapping[str, Any] | None = None,
        camel_case_endpoint: str | None = None,
        failure_log_level: int = logging.DEBUG,
    ) -> ToggleStep:
        """Describe a POST that saves a change.

        Args:
            endpoint (str): API path to POST (the snake_case path when
                ``camel_case_endpoint`` is given).
            accepted_results (frozenset[str]): Values of ``response_key`` that confirm
                the save.
            response_key (str): Key that reports the outcome.
            payload (MutableMapping[str, Any] | None): POST body, if any.
            camel_case_endpoint (str | None): API path on older camelCase firmware.
            failure_log_level (int): Log level for an unconfirmed response.

        Returns:
            ToggleStep: The save step.
        """
        return cls(
            endpoint,
            response_key,
            accepted_results,
            "POST",
            payload,
            camel_case_endpoint,
            failure_log_level,
        )

    @classmethod
    def apply(
        cls,
        endpoint: str,
        *,
        response_key: str = "status",
        method: Literal["GET", "POST"] = "POST",
        payload: MutableMapping[str, Any] | None = None,
        failure_log_level: int = logging.DEBUG,
    ) -> ToggleStep:
        """Describe a request that makes a saved change take effect.

        Args:
            endpoint (str): API path to request.
            response_key (str): Key that reports the outcome.
            method (Literal["GET", "POST"]): HTTP method.
            payload (MutableMapping[str, Any] | None): POST body, if any.
            failure_log_level (int): Log level for an unconfirmed response.

        Returns:
            ToggleStep: The apply step.
        """
        return cls(
            endpoint, response_key, None, method, payload, failure_log_level=failure_log_level
        )

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
        vpn: The VPN Instance for a VPN Toggle, which follows the ADR 0001 VPN
            rules and takes exactly one save step and one apply step; ``None``
            for every other Toggle.
    """

    label: str
    target: ToggleTarget
    save: tuple[ToggleStep, ...]
    apply: tuple[ToggleStep, ...]
    vpn: VpnInstance | None = None


@dataclass(frozen=True, slots=True)
class VpnInstance:
    """The VPN Instance a VPN Toggle changes.

    Attributes:
        service: VPN service (one of ``VPN_TOGGLE_SERVICES``) whose lock and
            pending-apply set the Toggle uses.
        item: Collection and instance UUID; the key of a pending unapplied save.
    """

    service: str
    item: tuple[str, str]


def _new_vpn_locks() -> dict[str, asyncio.Lock]:
    """Return one lock per VPN service.

    Returns:
        dict[str, asyncio.Lock]: Lock per service name.
    """
    return {service: asyncio.Lock() for service in VPN_TOGGLE_SERVICES}


def _new_vpn_pending_apply() -> dict[str, set[tuple[str, str]]]:
    """Return an empty pending-apply set per VPN service.

    Returns:
        dict[str, set[tuple[str, str]]]: Pending items per service name.
    """
    return {service: set() for service in VPN_TOGGLE_SERVICES}


@dataclass(slots=True)
class VpnToggleState:
    """ADR 0001 state of one client's VPN Toggles.

    Attributes:
        locks: One lock per service; serialises this client's Toggles of that service.
        pending_apply: Items per service that this client saved but has not applied.
        shutdown_generation: Bumped when the client closes; a VPN Toggle that began
            under an earlier generation raises ``asyncio.CancelledError``.
    """

    locks: dict[str, asyncio.Lock] = field(default_factory=_new_vpn_locks)
    pending_apply: dict[str, set[tuple[str, str]]] = field(default_factory=_new_vpn_pending_apply)
    shutdown_generation: int = 0


_VPN_TARGET_RESULTS: dict[ToggleTarget, str] = {
    ToggleTarget.ON: "Enabled",
    ToggleTarget.OFF: "Disabled",
}
"""Save result a VPN toggle reports when the item ends in each explicit target."""


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
        endpoint = await self._resolve_toggle_endpoint(step)
        if step.method == "GET":
            return await self._safe_dict_get(endpoint)
        return await self._safe_dict_post(endpoint, payload=step.payload)

    async def _run_toggle(self, spec: ToggleSpec) -> bool:
        """Save and then apply a Toggle, stopping at the first unconfirmed step.

        Transport errors propagate when ``throw_errors`` is on; otherwise they arrive
        as an empty response and fail the step.

        Args:
            spec (ToggleSpec): Toggle to run.

        Returns:
            bool: ``True`` when every save and apply step was confirmed, otherwise
                ``False``. No apply step is sent unless every save step succeeded.
                A VPN Toggle follows the ADR 0001 VPN rules instead (see
                ``_run_vpn_toggle``).
        """
        if spec.vpn is not None:
            return await self._run_vpn_toggle(spec, spec.vpn)
        for phase, steps in (("save", spec.save), ("apply", spec.apply)):
            for step in steps:
                response = await self._send_toggle_step(step)
                if not step.succeeded(response):
                    _LOGGER.log(
                        step.failure_log_level,
                        "[%s] %s step %s not confirmed for target %s",
                        spec.label,
                        phase,
                        step.endpoint,
                        spec.target,
                    )
                    return False
        return True

    async def _resolve_toggle_endpoint(self, step: ToggleStep) -> str:
        """Return the step's endpoint for the Router's firmware.

        Args:
            step (ToggleStep): Step whose endpoint to resolve.

        Returns:
            str: ``step.endpoint``, or its camelCase form on older firmware.
        """
        if step.camel_case_endpoint is None:
            return step.endpoint
        return await self._get_endpoint_path(
            snake_case_path=step.endpoint, camel_case_path=step.camel_case_endpoint
        )

    # VPN Toggles (ADR 0001): skip a matching apply, retry a known unapplied save,
    # serialise per service, and stop when the client closes.

    @cached_property
    def _vpn_toggle_state(self) -> VpnToggleState:
        """Return this client's VPN Toggle state, created on first use.

        Returns:
            VpnToggleState: Locks, pending-apply sets and shutdown generation.
        """
        return VpnToggleState()

    def _signal_toggle_shutdown(self) -> None:
        """Invalidate in-flight VPN Toggles because the client is closing."""
        self._vpn_toggle_state.shutdown_generation += 1

    def _ensure_vpn_toggle_current(self, shutdown_generation: int) -> None:
        """Cancel VPN Toggle work that crossed a client shutdown boundary.

        Args:
            shutdown_generation (int): Generation captured when the Toggle began.

        Raises:
            asyncio.CancelledError: If client shutdown began after the Toggle started.
        """
        if (
            self._active_close_count > 0
            or shutdown_generation != self._vpn_toggle_state.shutdown_generation
        ):
            raise asyncio.CancelledError("OPNsenseClient closed during VPN operation")

    async def _with_vpn_service_lock(
        self, service: str, operation: Callable[[int], Awaitable[bool]]
    ) -> bool:
        """Run a VPN Toggle under its service lock unless the client is closing.

        Args:
            service (str): VPN service whose lock serialises the Toggle.
            operation (Callable[[int], Awaitable[bool]]): Toggle body; receives the
                shutdown generation captured before the lock was awaited.

        Returns:
            bool: The operation's result, or ``False`` for a service with no lock.

        Raises:
            asyncio.CancelledError: If the client is closing, or closes while the
                Toggle waits for the lock.
        """
        state = self._vpn_toggle_state
        lock = state.locks.get(service)
        if lock is None:
            return False
        if self._active_close_count > 0:
            raise asyncio.CancelledError("OPNsenseClient is closing")
        shutdown_generation = state.shutdown_generation
        async with lock:
            self._ensure_vpn_toggle_current(shutdown_generation)
            return await operation(shutdown_generation)

    async def _run_vpn_toggle(self, spec: ToggleSpec, vpn: VpnInstance) -> bool:
        """Run a VPN Toggle under its service lock.

        Args:
            spec (ToggleSpec): Toggle with one save step and one apply step.
            vpn (VpnInstance): VPN Instance the Toggle changes.

        Returns:
            bool: Whether the change, or a pending apply it retried, was applied; or
                ``True`` when an explicit target already matched with nothing pending.
        """
        return await self._with_vpn_service_lock(
            vpn.service, partial(self._run_vpn_toggle_locked, spec, vpn)
        )

    async def _run_vpn_toggle_locked(
        self, spec: ToggleSpec, vpn: VpnInstance, shutdown_generation: int
    ) -> bool:
        """Save a VPN Toggle and apply its service when ADR 0001 calls for it.

        Args:
            spec (ToggleSpec): Toggle with one save step and one apply step.
            vpn (VpnInstance): VPN Instance the Toggle changes.
            shutdown_generation (int): Generation captured when the Toggle began.

        Returns:
            bool: See ``_run_vpn_toggle``.
        """
        (save_step,) = spec.save
        (apply_step,) = spec.apply
        pending = self._vpn_toggle_state.pending_apply[vpn.service]
        endpoint = await self._resolve_toggle_endpoint(save_step)
        response = await self._post_vpn_toggle_save(
            save_step, endpoint, spec.target, vpn, shutdown_generation
        )
        _LOGGER.debug(
            "[%s] %s toggle response for %s: %s", spec.label, vpn.service, vpn.item, response
        )
        outcome = self._vpn_toggle_save_outcome(save_step, response, spec.target)
        if outcome is not None:
            if outcome and vpn.item in pending:
                return await self._apply_vpn_toggle(spec.label, apply_step, vpn)
            return outcome
        pending.add(vpn.item)
        return await self._apply_vpn_toggle(spec.label, apply_step, vpn)

    async def _post_vpn_toggle_save(
        self,
        step: ToggleStep,
        endpoint: str,
        target: ToggleTarget,
        vpn: VpnInstance,
        shutdown_generation: int,
    ) -> dict[str, Any]:
        """POST a VPN toggle and track an acknowledged save the caller may not see.

        Args:
            step (ToggleStep): Save step.
            endpoint (str): Resolved save endpoint.
            target (ToggleTarget): Requested target.
            vpn (VpnInstance): VPN Instance the Toggle changes.
            shutdown_generation (int): Generation captured when the Toggle began.

        Returns:
            dict[str, Any]: Save response; empty after a transport failure.

        Raises:
            cancellation: The caller's ``asyncio.CancelledError``, re-raised after the
                dispatched response was processed.
        """
        self._ensure_vpn_toggle_current(shutdown_generation)
        response, cancellation = await self._safe_dict_post_with_cancellation(
            endpoint, step.payload
        )
        if self._vpn_toggle_save_acknowledged(step, response, target) and (
            cancellation is not None
            or shutdown_generation != self._vpn_toggle_state.shutdown_generation
        ):
            self._vpn_toggle_state.pending_apply[vpn.service].add(vpn.item)
        if cancellation is not None:
            raise cancellation
        self._ensure_vpn_toggle_current(shutdown_generation)
        return response

    @staticmethod
    def _vpn_toggle_save_acknowledged(
        step: ToggleStep, response: MutableMapping[str, Any], target: ToggleTarget
    ) -> bool:
        """Return whether a VPN toggle response confirms a saved state change.

        Args:
            step (ToggleStep): Save step.
            response (MutableMapping[str, Any]): Save response.
            target (ToggleTarget): Requested target.

        Returns:
            bool: ``True`` when the Router reports a changed, saved item.
        """
        if response.get("changed") is not True:
            return False
        if step.response_key not in response:
            return target is ToggleTarget.FLIP
        return step.succeeded(response)

    @staticmethod
    def _vpn_toggle_save_outcome(
        step: ToggleStep, response: MutableMapping[str, Any], target: ToggleTarget
    ) -> bool | None:
        """Classify a VPN toggle response before the service apply.

        ``changed: false`` alone does not prove the item is in the target state, so an
        explicit target also needs the matching save result.

        Args:
            step (ToggleStep): Save step.
            response (MutableMapping[str, Any]): Save response.
            target (ToggleTarget): Requested target.

        Returns:
            bool | None: ``None`` when the change needs the service applied, ``True``
                when the item already matches the explicit target, ``False`` when the
                toggle failed.
        """
        changed = response.get("changed")
        if target is ToggleTarget.FLIP:
            return None if changed else False
        if not isinstance(changed, bool) or not step.succeeded(response):
            return False
        if response[step.response_key] != _VPN_TARGET_RESULTS[target]:
            return False
        return None if changed else True

    async def _apply_vpn_toggle(self, label: str, step: ToggleStep, vpn: VpnInstance) -> bool:
        """Apply a VPN service and clear its pending saves when confirmed.

        Args:
            label (str): Public method name, for log messages.
            step (ToggleStep): Apply step.
            vpn (VpnInstance): VPN Instance whose service to apply.

        Returns:
            bool: Whether the Router confirmed the apply.

        Raises:
            cancellation: The caller's ``asyncio.CancelledError``, re-raised after the
                dispatched apply response was processed.
        """
        response, cancellation = await self._safe_dict_post_with_cancellation(
            step.endpoint, step.payload
        )
        succeeded = step.succeeded(response)
        if succeeded:
            self._vpn_toggle_state.pending_apply[vpn.service].clear()
        else:
            _LOGGER.debug(
                "[%s] %s apply %s not confirmed. response: %s",
                label,
                vpn.service,
                step.endpoint,
                response,
            )
        if cancellation is not None:
            raise cancellation
        return succeeded
