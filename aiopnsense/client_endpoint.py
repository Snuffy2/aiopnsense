"""Endpoint selection and availability helpers for OPNsenseClient."""

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any, Literal
from warnings import deprecated

from ._endpoint_outcome import _EndpointOutcome
from ._error_policy import _error_if_throwing, _throws_errors
from ._request_failure import _PERMISSION_GUIDANCE, _PROBE_PHASE, _REQUEST_PHASE, _request_log_url
from .const import SNAKE_CASE_ENDPOINTS_CAPABILITY, FirmwareCapability
from .exceptions import OPNsenseUnknownFirmware, _map_opnsense_exception
from .helpers import (
    _LOGGER,
    _log_request_failure,
    _log_request_recovery,
    _mark_request_error_logged,
    _redact_url_credentials,
)


@dataclass
class _EndpointObservation:
    """Transient publication state for concurrent requests to one endpoint."""

    generation: int
    active_count: int = 0
    cache_publication_id: int = 0
    phase_publication_ids: dict[str, int] = field(default_factory=dict)


class ClientEndpointMixin:
    """Endpoint selection and availability methods for OPNsenseClient."""

    if TYPE_CHECKING:
        _endpoint_availability: dict[str, bool]
        _endpoint_cache_generation: int
        _endpoint_cache_ttl_seconds: int
        _endpoint_checked_at: dict[str, datetime]
        _endpoint_permission_denied: set[str]
        _request_outages: dict[tuple[str, str, str], int | None]
        _throw_errors: bool
        _endpoint_observations: dict[str, _EndpointObservation]
        _url: str
        _use_snake_case: bool | None

        async def _firmware_capability(self, capability: FirmwareCapability) -> bool | None:
            """Evaluate a firmware capability against the detected firmware.

            Args:
                capability (FirmwareCapability): Capability to evaluate.

            Returns:
                bool | None: Whether the capability is supported, if known.
            """
            ...

        async def _do_get_outcome(
            self,
            path: str,
            caller: str = "Unknown",
            *,
            read_body: bool = True,
            response_format: Literal["json", "text"] = "json",
        ) -> _EndpointOutcome:
            """Execute an immediate GET request and classify the response.

            Args:
                path (str): API endpoint path to request.
                caller (str): Calling method name used for diagnostics.
                read_body (bool): Whether to decode a successful response body.
                response_format (Literal["json", "text"], optional): Decoding mode for
                    a successful response body.

            Returns:
                _EndpointOutcome: Classified transport response.
            """
            ...

        async def _queue_request(
            self,
            method: str,
            path: str,
            payload: MutableMapping[str, Any] | None = None,
        ) -> Any:
            """Queue one API request and return its result.

            Args:
                method (str): Internal request method handled by the queue worker.
                path (str): API endpoint path to request.
                payload (MutableMapping[str, Any] | None): Request body for POST requests.

            Returns:
                Any: Result set by the queue worker.
            """
            ...

    @deprecated("Endpoint style selection is internal. Direct calls are no longer needed.")
    async def set_use_snake_case(self, initial: bool = False) -> None:
        """Deprecated wrapper that preserves legacy ``initial`` compatibility.

        Args:
            initial (bool): Whether to preserve legacy initial-detection behavior.
        """
        await self._set_use_snake_case(initial=initial)

    async def _set_use_snake_case(self, initial: bool = False) -> None:
        """Set endpoint naming mode from the detected firmware version.

        The mode is only stored once the firmware version is known and comparable.
        While the firmware is unknown the mode stays unresolved so a later call can
        resolve it.

        Args:
            initial (bool): Whether to preserve the legacy unknown-firmware raise behavior.

        Returns:
            None: This method updates internal client state only.

        Raises:
            OPNsenseUnknownFirmware: Raised when ``initial`` is ``True`` and the firmware
                version cannot be compared reliably.
        """
        uses_snake_case = await self._firmware_capability(SNAKE_CASE_ENDPOINTS_CAPABILITY)
        if uses_snake_case is None:
            if initial:
                raise OPNsenseUnknownFirmware
            return
        self._use_snake_case = uses_snake_case
        _LOGGER.debug(
            "Using %s endpoints for OPNsense %s %s",
            "snake_case" if uses_snake_case else "camelCase",
            ">=" if uses_snake_case else "<",
            SNAKE_CASE_ENDPOINTS_CAPABILITY.minimum,
        )

    async def _get_endpoint_path(self, snake_case_path: str, camel_case_path: str) -> str:
        """Return the firmware-appropriate endpoint path.

        Args:
            snake_case_path (str): Endpoint path for newer snake_case firmware.
            camel_case_path (str): Endpoint path for older camelCase firmware.

        Returns:
            str: Selected endpoint path for the active firmware family. While the
                firmware is unknown this is the capability's unknown answer, which is
                not remembered.
        """
        if self._use_snake_case is None:
            await self._set_use_snake_case()
        # _use_snake_case is None until a known firmware version resolves it, so the
        # unknown-firmware answer is used per call without being stored.
        use_snake_case = (
            SNAKE_CASE_ENDPOINTS_CAPABILITY.unknown_answer
            if self._use_snake_case is None
            else self._use_snake_case
        )
        return snake_case_path if use_snake_case else camel_case_path

    def _reset_endpoint_state(self) -> None:
        """Discard the endpoint style and all cached endpoint availability.

        Called when the Router firmware version changes, because both were derived
        from the old version. The availability, check-time and permission-denied
        caches are cleared together so they cannot drift apart.
        """
        self._endpoint_cache_generation += 1
        self._use_snake_case = None
        self._endpoint_observations.clear()
        self._endpoint_availability.clear()
        self._endpoint_checked_at.clear()
        self._endpoint_permission_denied.clear()

    def _clear_endpoint_cache_entry(self, cache_key: str) -> None:
        """Clear all cached endpoint state for a method-aware key.

        Args:
            cache_key (str): Method-aware endpoint cache key to clear.
        """
        self._endpoint_permission_denied.discard(cache_key)
        self._endpoint_availability.pop(cache_key, None)
        self._endpoint_checked_at.pop(cache_key, None)

    async def _fetch_endpoint_outcome(
        self,
        path: str,
        response_format: Literal["json", "text"] = "json",
        *,
        force_refresh: bool = False,
    ) -> _EndpointOutcome:
        """Fetch a GET endpoint once and report whether it returned data, is absent or failed.

        Args:
            path (str): API endpoint path to request. The exact path, including
                its query string, is used for the availability cache key.
            response_format (Literal["json", "text"], optional): ``"text"`` returns the
                body text in the ``DATA`` outcome; ``"json"`` returns the decoded body.
            force_refresh (bool): Whether to bypass a cached absent answer.

        Returns:
            _EndpointOutcome: The outcome described by ``_fetch_gated_outcome``. A 403 or a
                transient failure raises instead when ``self._throw_errors`` is ``True``.
        """
        return await self._fetch_gated_outcome(
            "get", path, response_format=response_format, force_refresh=force_refresh
        )

    async def _fetch_post_endpoint_outcome(
        self,
        path: str,
        payload: MutableMapping[str, Any] | None = None,
    ) -> _EndpointOutcome:
        """Send one POST with its payload and report whether it returned data, is absent or failed.

        The payload is the real request body; no payload-less probe is sent.

        Args:
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None): Request body sent with the POST.

        Returns:
            _EndpointOutcome: The outcome described by ``_fetch_gated_outcome``. A 403 or a
                transient failure raises instead when ``self._throw_errors`` is ``True``.
        """
        return await self._fetch_gated_outcome("post", path, payload)

    async def _check_endpoint_status(
        self,
        path: str,
        *,
        force_refresh: bool = False,
    ) -> _EndpointOutcome:
        """Report whether a GET endpoint is present, absent or failed, without reading its payload.

        Used for reads that are not a plain bounded fetch, such as streams and
        state-changing actions, where the caller needs the availability answer
        but must not consume the response. A dispatched check is a GET with no
        payload. Outcomes and cache rules are the same as ``_fetch_endpoint_outcome``.

        Args:
            path (str): API endpoint path to check. The exact path, including
                its query string, is used for the availability cache key.
            force_refresh (bool): Whether to bypass cached present or absent answers.
                The result of a dispatched request is cached.

        Returns:
            _EndpointOutcome: ``DATA`` (with no payload) when the endpoint answers
                successfully, ``ABSENT`` for a 404 or a 403, or ``TRANSIENT`` for any
                other failure. A 403 or a transient failure raises the mapped
                ``OPNsenseError`` instead when ``self._throw_errors`` is ``True``.
        """
        return await self._fetch_gated_outcome(
            "get", path, status_only=True, force_refresh=force_refresh
        )

    async def _fetch_gated_outcome(
        self,
        method: Literal["get", "post"],
        path: str,
        payload: MutableMapping[str, Any] | None = None,
        *,
        response_format: Literal["json", "text"] = "json",
        status_only: bool = False,
        force_refresh: bool = False,
    ) -> _EndpointOutcome:
        """Send one availability-gated request and report whether it returned data, is absent or failed.

        A dispatched request both checks availability and returns the data. The
        availability status (never the payload) is cached for the endpoint cache
        TTL, so a known-absent endpoint is answered without a request. Status-only
        checks also reuse a fresh present answer. GET and POST share this logic;
        the HTTP method and exact path, including query, distinguish cache entries.

        Args:
            method (Literal["get", "post"]): HTTP method of the data request.
            path (str): API endpoint path to request. The exact path, including
                its query string, is used for the availability cache key.
            payload (MutableMapping[str, Any] | None): Request body, sent with POST requests.
            response_format (Literal["json", "text"]): ``"text"`` returns the body text of
                a GET in the ``DATA`` outcome; ``"json"`` returns the decoded body.
            status_only (bool): Whether to check availability without reading the
                payload (GET only). Status checks bypass the request queue. A fresh
                cached present answer skips a status-only request; data fetches
                dispatch a request unless a fresh absent answer can be reused.
            force_refresh (bool): Whether to bypass cached present and absent answers.

        Returns:
            _EndpointOutcome: ``DATA`` with the decoded payload or body text, ``ABSENT`` when the
                Router reports the endpoint missing (404) or not permitted (403), or
                ``TRANSIENT`` for any other failure. A 403 or a transient failure
                raises the mapped ``OPNsenseError`` instead when ``self._throw_errors``
                is ``True``; a 404 never raises.

        Raises:
            error: OPNsenseError raised for a 403 or a transient HTTP or transport
                failure when ``self._throw_errors`` is ``True``.
            decode_error: A decoding exception on a successful HTTP response.
                Bounded reads raise its mapped public error; probes propagate the
                original exception from the session validator.

        Side Effects:
            Updates endpoint availability caches. A 200 without a decoding error
            caches the endpoint as present and a 404 as absent. A 200 with a decoding
            error supersedes older responses and clears any cached absence. Outside
            throw mode a 403 also caches absence with the permission-denied marker.
            Transient failures and, in throw mode, a 403 clear the cache entry so the
            next call requests again.
        """
        cache_key = path
        if method == "post":
            cache_key = f"post:{cache_key}"
        now = datetime.now().astimezone()
        cache_is_fresh = (
            cache_key in self._endpoint_checked_at
            and (now - self._endpoint_checked_at[cache_key]).total_seconds()
            < self._endpoint_cache_ttl_seconds
        )
        if (
            not force_refresh
            and cache_is_fresh
            and self._endpoint_availability.get(cache_key) is False
            and not (_throws_errors(self) and cache_key in self._endpoint_permission_denied)
        ):
            if cache_key in self._endpoint_permission_denied:
                return _EndpointOutcome.from_http_status(403, "Forbidden")
            return _EndpointOutcome.absent()
        if (
            status_only
            and not force_refresh
            and cache_is_fresh
            and self._endpoint_availability.get(cache_key) is True
        ):
            return _EndpointOutcome.data(None)

        cache_generation = self._endpoint_cache_generation
        phase = _PROBE_PHASE if status_only else _REQUEST_PHASE
        operation = (
            "check_endpoint_status"
            if status_only
            else "fetch_post_endpoint_outcome"
            if method == "post"
            else "fetch_endpoint_outcome"
        )
        observation = self._endpoint_observations.get(cache_key)
        if observation is None or observation.generation != cache_generation:
            observation = _EndpointObservation(generation=cache_generation)
            self._endpoint_observations[cache_key] = observation
        observation.active_count += 1
        try:
            if status_only:
                outcome: _EndpointOutcome = await self._do_get_outcome(
                    path,
                    "_check_endpoint_status",
                    read_body=False,
                )
            else:
                queue_method = (
                    "get_text_outcome"
                    if method == "get" and response_format == "text"
                    else f"{method}_outcome"
                )
                outcome = await self._queue_request(queue_method, path, payload)
            observation_id = outcome.observation_id
            decode_error: Exception | None = outcome.decode_error
            if decode_error is not None and not status_only:
                mapped_error = _map_opnsense_exception(decode_error)
                mapped_error.__cause__ = decode_error
                decode_error = mapped_error
            error = _error_if_throwing(self, outcome.error) if outcome.error is not None else None
            checked_at = datetime.now().astimezone()
            current_generation = (
                self._endpoint_cache_generation == cache_generation
                and self._endpoint_observations.get(cache_key) is observation
            )
            cache_can_publish = (
                current_generation and observation.cache_publication_id < observation_id
            )
            phase_can_publish = (
                current_generation
                and decode_error is None
                and observation.phase_publication_ids.get(phase, 0) < observation_id
            )
            if not cache_can_publish and not phase_can_publish:
                if decode_error is not None:
                    raise decode_error
                if error is not None:
                    key = (method.upper(), path, phase)
                    failure_status = 403 if outcome.is_absent else None
                    if (
                        current_generation
                        and key in self._request_outages
                        and self._request_outages[key] == failure_status
                    ):
                        _mark_request_error_logged(error)
                    raise error
                return outcome
            if cache_can_publish:
                observation.cache_publication_id = observation_id
            if phase_can_publish:
                observation.phase_publication_ids[phase] = observation_id
            if decode_error is not None:
                if cache_can_publish and self._endpoint_availability.get(cache_key) is False:
                    self._clear_endpoint_cache_entry(cache_key)
                raise decode_error
            if outcome.is_data:
                if phase_can_publish:
                    _log_request_recovery(self, method, path, phase)
                if cache_can_publish:
                    self._endpoint_permission_denied.discard(cache_key)
                    self._endpoint_availability[cache_key] = True
                    self._endpoint_checked_at[cache_key] = checked_at
                return outcome

            if outcome.is_absent and outcome.http_status != 403:
                _LOGGER.debug("Endpoint %s not found", _redact_url_credentials(path))
                if cache_can_publish:
                    self._endpoint_permission_denied.discard(cache_key)
                    self._endpoint_availability[cache_key] = False
                    self._endpoint_checked_at[cache_key] = checked_at
                return outcome

            if outcome.is_absent:
                if phase_can_publish:
                    _log_request_failure(
                        self,
                        method,
                        path,
                        phase,
                        logging.ERROR,
                        "Permission Error in %s. Path: %s. " + _PERMISSION_GUIDANCE,
                        operation,
                        _request_log_url(f"{self._url}{path}"),
                        error=error,
                        failure_status=403,
                    )
                if error is not None:
                    if cache_can_publish:
                        self._clear_endpoint_cache_entry(cache_key)
                    raise error
                if cache_can_publish:
                    self._endpoint_permission_denied.add(cache_key)
                    self._endpoint_availability[cache_key] = False
                    self._endpoint_checked_at[cache_key] = checked_at
                return outcome

            if cache_can_publish:
                self._clear_endpoint_cache_entry(cache_key)
            if phase_can_publish:
                _log_request_failure(
                    self,
                    method,
                    path,
                    phase,
                    logging.WARNING,
                    "Transient %s failure for %s. %s. Not caching result.",
                    method.upper(),
                    _redact_url_credentials(path),
                    _redact_url_credentials(outcome.detail),
                    error=error,
                )
            if error is not None:
                raise error
            return outcome
        finally:
            self._finish_endpoint_observation(cache_key, observation)

    def _finish_endpoint_observation(
        self, cache_key: str, observation: _EndpointObservation
    ) -> None:
        """Release one active observation, deleting its transient key when idle.

        Args:
            cache_key (str): Method-aware endpoint key for the request.
            observation (_EndpointObservation): State captured when the request started.
        """
        if self._endpoint_observations.get(cache_key) is not observation:
            return
        observation.active_count -= 1
        if observation.active_count == 0:
            self._endpoint_observations.pop(cache_key, None)

    @deprecated("Endpoint availability probing is internal. Direct calls are no longer needed.")
    async def is_endpoint_available(self, path: str, force_refresh: bool = False) -> bool:
        """Backward-compatible alias that reports whether a GET endpoint is present.

        Args:
            path (str): API endpoint path to check.
            force_refresh (bool): Whether to bypass cached endpoint availability.

        Returns:
            bool: ``True`` only when the Router answers the endpoint successfully;
                ``False`` when it is absent or the check fails. In throw mode a 403
                or a transient failure raises instead of returning ``False``.
        """
        if not isinstance(path, str) or not path:
            return False
        outcome = await self._check_endpoint_status(path, force_refresh=force_refresh)
        return outcome.is_data
