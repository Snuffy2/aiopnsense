"""Endpoint selection and availability helpers for OPNsenseClient."""

from collections.abc import MutableMapping
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any, Literal
from warnings import deprecated

import aiohttp

from ._endpoint_outcome import _EndpointOutcome
from ._error_policy import _error_if_throwing, _throws_errors
from ._request_failure import _PROBE_PHASE, _REQUEST_PHASE, _request_log_url
from .const import SNAKE_CASE_ENDPOINTS_CAPABILITY, FirmwareCapability
from .exceptions import OPNsenseUnknownFirmware
from .helpers import (
    _LOGGER,
    _log_request_failure,
    _log_request_recovery,
    _redact_url_credentials,
)


class ClientEndpointMixin:
    """Endpoint selection and availability methods for OPNsenseClient."""

    if TYPE_CHECKING:
        _endpoint_availability: dict[str, bool]
        _endpoint_cache_generation: int
        _endpoint_cache_ttl_seconds: int
        _endpoint_checked_at: dict[str, datetime]
        _endpoint_permission_denied: set[str]
        _request_outages: dict[tuple[str, str, str], int | None]
        _password: str
        _rest_api_query_count: int
        _session: aiohttp.ClientSession
        _throw_errors: bool
        _url: str
        _use_snake_case: bool | None
        _username: str
        _verify_ssl: bool

        async def _firmware_capability(self, capability: FirmwareCapability) -> bool | None:
            """Evaluate a firmware capability against the detected firmware."""
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

        Side Effects:
            Updates endpoint availability caches. A 200 caches the endpoint as present
            and a 404 as absent. Outside throw mode a 403 also caches the endpoint as
            absent with the permission-denied marker. Transient failures and, in throw
            mode, a 403 also clears the cache entry so the next call requests again.
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
            return _EndpointOutcome.absent()
        if (
            status_only
            and not force_refresh
            and cache_is_fresh
            and self._endpoint_availability.get(cache_key) is True
        ):
            return _EndpointOutcome.data(None)

        cache_generation = self._endpoint_cache_generation
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
        checked_at = datetime.now().astimezone()
        cache_is_current = self._endpoint_cache_generation == cache_generation
        phase = _PROBE_PHASE if status_only else _REQUEST_PHASE

        if outcome.is_data:
            _log_request_recovery(self, method, path, phase)
            if cache_is_current:
                self._endpoint_permission_denied.discard(cache_key)
                self._endpoint_availability[cache_key] = True
                self._endpoint_checked_at[cache_key] = checked_at
            return outcome

        if outcome.is_absent and outcome.http_status != 403:
            _LOGGER.debug("Endpoint %s not found", path)
            if cache_is_current:
                self._endpoint_permission_denied.discard(cache_key)
                self._endpoint_availability[cache_key] = False
                self._endpoint_checked_at[cache_key] = checked_at
            return outcome

        error = _error_if_throwing(self, outcome.error) if outcome.error is not None else None
        if outcome.is_absent:
            _log_request_failure(
                self,
                method,
                path,
                phase,
                logging.ERROR,
                "Permission Error in fetch_endpoint_outcome. Path: %s. Ensure the OPNsense user connected to HA has appropriate access. Recommend full admin access",
                _request_log_url(f"{self._url}{path}"),
                error=error,
            )
            if error is not None:
                if cache_is_current:
                    self._clear_endpoint_cache_entry(cache_key)
                raise error
            if cache_is_current:
                self._endpoint_permission_denied.add(cache_key)
                self._endpoint_availability[cache_key] = False
                self._endpoint_checked_at[cache_key] = checked_at
            return outcome

        if cache_is_current:
            self._clear_endpoint_cache_entry(cache_key)
        _log_request_failure(
            self,
            method,
            path,
            phase,
            logging.WARNING,
            "Transient %s failure for %s. %s. Not caching result.",
            method.upper(),
            path,
            _redact_url_credentials(outcome.detail),
            error=error,
        )
        if error is not None:
            raise error
        return outcome

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
