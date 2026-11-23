"""Endpoint selection and availability helpers for OPNsenseClient."""

from collections.abc import MutableMapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, cast
from warnings import deprecated

import aiohttp

from ._endpoint_outcome import _EndpointOutcome
from ._error_policy import _raise_if_throwing, _throws_errors
from ._firmware_request_guard import _send_request
from ._request_failure import (
    _PROBE_PHASE,
    _REQUEST_PHASE,
    _report_http_failure,
    _report_transport_failure,
)
from ._typing import AiopnsenseClientProtocol
from .const import DEFAULT_REQUEST_TIMEOUT_SECONDS, SNAKE_CASE_ENDPOINTS_CAPABILITY
from .exceptions import OPNsenseUnknownFirmware
from .helpers import _LOGGER, _log_request_recovery


class ClientEndpointMixin:
    """Endpoint selection and availability methods for OPNsenseClient."""

    if TYPE_CHECKING:
        _endpoint_availability: dict[str, bool]
        _endpoint_cache_generation: int
        _endpoint_cache_ttl_seconds: int
        _endpoint_checked_at: dict[str, datetime]
        _endpoint_permission_denied: set[str]
        _request_outages: dict[tuple[str, str, str], int | None]
        _unsafe_post_endpoint_probe_paths: frozenset[str] | set[str]
        _unsafe_post_endpoint_probe_prefixes: tuple[str, ...]
        _unsafe_post_endpoint_probe_segments: frozenset[str] | set[str]
        _password: str
        _rest_api_query_count: int
        _session: aiohttp.ClientSession
        _throw_errors: bool
        _url: str
        _use_snake_case: bool | None
        _username: str
        _verify_ssl: bool

        async def _do_get_outcome(
            self,
            path: str,
            caller: str = "Unknown",
            *,
            read_body: bool = True,
            response_format: Literal["json", "text"] = "json",
            phase: str = _REQUEST_PHASE,
        ) -> _EndpointOutcome:
            """Execute an immediate GET request and classify the response.

            Args:
                path (str): API endpoint path to request.
                caller (str): Calling method name used for diagnostics.
                read_body (bool): Whether to decode a successful response body.
                response_format (Literal["json", "text"], optional): Decoding mode for
                    a successful response body.
                phase (str): Request phase for outage logging.

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

    _UNSAFE_POST_ENDPOINT_PROBE_PATHS: frozenset[str] = frozenset()
    _UNSAFE_POST_ENDPOINT_PROBE_PREFIXES: tuple[str, ...] = ()
    _UNSAFE_POST_ENDPOINT_PROBE_SEGMENTS: frozenset[str] = frozenset(
        {
            "apply",
            "check",
            "dismiss_status",
            "generate_vouchers",
            "halt",
            "kill_states",
            "reboot",
            "reconfigure",
            "reload_interface",
            "restart",
            "set",
            "start",
            "stop",
            "toggle",
            "update",
            "upgrade",
        }
    )

    @staticmethod
    def _normalize_endpoint_segment(segment: str) -> str:
        """Return a snake_case-ish endpoint segment for token matching.

        Args:
            segment (str): Raw path segment from an API endpoint.

        Returns:
            str: Lowercase segment with camelCase boundaries converted to underscores.
        """
        normalized: list[str] = []
        for index, char in enumerate(segment):
            if (
                char.isupper()
                and index > 0
                and (segment[index - 1].islower() or segment[index - 1].isdigit())
            ):
                normalized.append("_")
            normalized.append(char.lower())
        return "".join(normalized)

    def _is_post_endpoint_probe_blocked(self, path: str) -> bool:
        """Return whether a POST path should not be availability-probed.

        Args:
            path (str): Candidate API endpoint path.

        Returns:
            bool: ``True`` if probing the endpoint could trigger a known side effect.
        """
        blocked_paths = getattr(
            self,
            "_unsafe_post_endpoint_probe_paths",
            self._UNSAFE_POST_ENDPOINT_PROBE_PATHS,
        )
        blocked_prefixes = getattr(
            self,
            "_unsafe_post_endpoint_probe_prefixes",
            self._UNSAFE_POST_ENDPOINT_PROBE_PREFIXES,
        )
        if path in blocked_paths or path.startswith(blocked_prefixes):
            return True

        blocked_segments = getattr(
            self,
            "_unsafe_post_endpoint_probe_segments",
            self._UNSAFE_POST_ENDPOINT_PROBE_SEGMENTS,
        )
        for segment in path.strip("/").split("/"):
            normalized_segment = self._normalize_endpoint_segment(segment)
            if any(
                normalized_segment == blocked_segment
                or normalized_segment.startswith(f"{blocked_segment}_")
                for blocked_segment in blocked_segments
            ):
                return True
        return False

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
        uses_snake_case = await cast(
            AiopnsenseClientProtocol,
            self,
        )._firmware_capability(SNAKE_CASE_ENDPOINTS_CAPABILITY)
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

    async def _is_endpoint_available(
        self,
        path: str,
        *,
        method: str,
        force_refresh: bool = False,
    ) -> bool:
        """Return whether a specific API endpoint appears to be available.

        Args:
            path (str): API endpoint path to request.
            method (str): HTTP method used to probe the endpoint.
            force_refresh (bool): Whether to bypass cached endpoint availability.

        Returns:
            bool: True if a specific api endpoint appears to be available; otherwise, False.
                When ``throw_errors`` is enabled, a reported HTTP or transport failure
                raises the mapped public ``OPNsenseError``.

        Side Effects:
            Increments the REST query counter for uncached probes and updates
            endpoint availability caches. Missing endpoints and permission
            failures are cached as unavailable outside throw mode. On transient
            transport or HTTP response errors, cache entries for the method-aware
            key are removed before returning ``False`` or re-raising in throw mode.
        """
        if not isinstance(path, str) or not path:
            return False
        normalized_method = method.lower()
        if normalized_method not in {"get", "post"}:
            return False
        cache_key = path if normalized_method == "get" else f"{normalized_method}:{path}"

        now = datetime.now().astimezone()
        cache_is_fresh = (
            cache_key in self._endpoint_checked_at
            and (now - self._endpoint_checked_at[cache_key]).total_seconds()
            < self._endpoint_cache_ttl_seconds
        )

        if not force_refresh and cache_is_fresh and cache_key in self._endpoint_availability:
            cached_availability = self._endpoint_availability[cache_key]
            if not (_throws_errors(self) and cache_key in self._endpoint_permission_denied):
                return cached_availability

        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[is_%s_endpoint_available] path: %s", normalized_method, path)
        cache_generation = self._endpoint_cache_generation

        try:
            async with _send_request(
                self._session,
                normalized_method,
                url,
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                ssl=self._verify_ssl,
            ) as response:
                available = response.ok
                if available or response.status == 404:
                    if available:
                        _log_request_recovery(self, normalized_method, path, _PROBE_PHASE)
                    if self._endpoint_cache_generation == cache_generation:
                        self._endpoint_permission_denied.discard(cache_key)
                        self._endpoint_availability[cache_key] = available
                        self._endpoint_checked_at[cache_key] = now
                    return available

                http_error = _report_http_failure(
                    self,
                    method=normalized_method,
                    path=path,
                    phase=_PROBE_PHASE,
                    operation=f"is_{normalized_method}_endpoint_available",
                    url=url,
                    status=response.status,
                    reason=response.reason,
                )
                if response.status == 403 and not _throws_errors(self):
                    if self._endpoint_cache_generation == cache_generation:
                        self._endpoint_permission_denied.add(cache_key)
                        self._endpoint_availability[cache_key] = False
                        self._endpoint_checked_at[cache_key] = now
                    return False
                if self._endpoint_cache_generation == cache_generation:
                    self._clear_endpoint_cache_entry(cache_key)
                _raise_if_throwing(self, http_error)
                return False
        except (aiohttp.ClientError, TimeoutError) as e:
            if self._endpoint_cache_generation == cache_generation:
                self._clear_endpoint_cache_entry(cache_key)
            transport_error = _report_transport_failure(
                self,
                method=normalized_method,
                path=path,
                phase=_PROBE_PHASE,
                operation=f"is_{normalized_method}_endpoint_available",
                exc=e,
            )
            _raise_if_throwing(self, transport_error, e)
            return False

    async def _is_get_endpoint_available(self, path: str, force_refresh: bool = False) -> bool:
        """Return whether a specific GET-probed API endpoint appears available.

        Args:
            path (str): API endpoint path to request.
            force_refresh (bool): Whether to bypass cached endpoint availability.

        Returns:
            bool: ``True`` when the GET probe succeeds; otherwise, ``False``.
        """
        return await self._is_endpoint_available(path, method="get", force_refresh=force_refresh)

    async def _is_post_endpoint_available(
        self,
        path: str,
        force_refresh: bool = False,
    ) -> bool | None:
        """Return whether a specific POST-probed API endpoint appears available.

        Args:
            path (str): API endpoint path to request.
            force_refresh (bool): Whether to bypass cached endpoint availability.

        Returns:
            bool | None: ``True`` when the POST probe succeeds, ``False`` when
                the probe runs and the endpoint appears unavailable, or ``None``
                when the endpoint is not probed because the path is invalid or
                probing it could mutate state.
        """
        if not isinstance(path, str) or not path:
            return None
        if self._is_post_endpoint_probe_blocked(path):
            _LOGGER.debug("POST endpoint availability probe blocked for unsafe path: %s", path)
            return None
        return await self._is_endpoint_available(path, method="post", force_refresh=force_refresh)

    def _set_endpoint_cache(
        self,
        cache_key: str,
        *,
        available: bool,
        checked_at: datetime,
        permission_denied: bool = False,
    ) -> None:
        """Store one availability answer for the endpoint cache TTL.

        Args:
            cache_key (str): Method-aware endpoint cache key.
            available (bool): Whether the endpoint answered successfully.
            checked_at (datetime): Time the request was started, which starts the TTL.
            permission_denied (bool): Whether the answer is a 403 that is cached outside
                throw mode.
        """
        if permission_denied:
            self._endpoint_permission_denied.add(cache_key)
        else:
            self._endpoint_permission_denied.discard(cache_key)
        self._endpoint_availability[cache_key] = available
        self._endpoint_checked_at[cache_key] = checked_at

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

        Side Effects:
            Updates endpoint availability caches. A 200 caches the endpoint as present
            and a 404 as absent. Outside throw mode a 403 also caches absence with the
            permission-denied marker. Transient failures and, in throw mode, a 403 clear
            the cache entry so the next call requests again. An undecodable 200 body
            propagates and leaves the cache unchanged. A reset of the endpoint cache
            while the request is in flight discards its answer.
        """
        cache_key = f"post:{path}" if method == "post" else path
        now = datetime.now().astimezone()
        cache_is_fresh = (
            cache_key in self._endpoint_checked_at
            and (now - self._endpoint_checked_at[cache_key]).total_seconds()
            < self._endpoint_cache_ttl_seconds
        )
        if not force_refresh and cache_is_fresh:
            cached_available = self._endpoint_availability.get(cache_key)
            if cached_available is False and not (
                _throws_errors(self) and cache_key in self._endpoint_permission_denied
            ):
                denied = cache_key in self._endpoint_permission_denied
                return _EndpointOutcome.absent(403 if denied else 404)
            if cached_available is True and status_only:
                return _EndpointOutcome.data(None)

        cache_generation = self._endpoint_cache_generation
        if status_only:
            outcome = await self._do_get_outcome(
                path, "_check_endpoint_status", read_body=False, phase=_PROBE_PHASE
            )
        else:
            queue_method = (
                "get_text_outcome"
                if method == "get" and response_format == "text"
                else f"{method}_outcome"
            )
            outcome = await self._queue_request(queue_method, path, payload)

        current = self._endpoint_cache_generation == cache_generation
        # The TTL starts when the Router answers, so a slow request keeps its full TTL.
        checked_at = datetime.now().astimezone()
        if outcome.is_data:
            if current:
                self._set_endpoint_cache(cache_key, available=True, checked_at=checked_at)
            return outcome
        if outcome.error is None:
            if current:
                self._set_endpoint_cache(cache_key, available=False, checked_at=checked_at)
            return outcome
        if outcome.is_absent and not _throws_errors(self):
            if current:
                self._set_endpoint_cache(
                    cache_key, available=False, checked_at=checked_at, permission_denied=True
                )
            return outcome
        if current:
            self._clear_endpoint_cache_entry(cache_key)
        _raise_if_throwing(self, outcome.error)
        return outcome

    @deprecated("Endpoint availability probing is internal. Direct calls are no longer needed.")
    async def is_endpoint_available(self, path: str, force_refresh: bool = False) -> bool:
        """Backward-compatible alias for GET endpoint availability probing.

        Args:
            path (str): API endpoint path to request.
            force_refresh (bool): Whether to bypass cached endpoint availability.

        Returns:
            bool: ``True`` when the GET probe succeeds; otherwise, ``False``.
        """
        return await self._is_get_endpoint_available(path, force_refresh=force_refresh)
