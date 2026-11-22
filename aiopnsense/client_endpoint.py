"""Endpoint selection and availability helpers for OPNsenseClient."""

from datetime import datetime
from typing import TYPE_CHECKING, cast
from warnings import deprecated

import aiohttp

from ._error_policy import _raise_if_throwing, _throws_errors
from ._request_failure import _PROBE_PHASE, _report_http_failure, _report_transport_failure
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
            request = getattr(self._session, normalized_method)
            async with request(
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
