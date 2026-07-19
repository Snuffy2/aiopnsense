"""Endpoint selection and availability helpers for OPNsenseClient."""

import asyncio
from collections.abc import MutableMapping
from time import monotonic
from typing import TYPE_CHECKING, Any, Literal, cast
from warnings import deprecated

import aiohttp

from ._typing import AiopnsenseClientProtocol
from .const import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    LEGACY_CAMELCASE_ENDPOINT_FIRMWARE,
)
from .exceptions import OPNsenseUnknownFirmware, _map_opnsense_exception, _opnsense_http_error
from .helpers import _LOGGER, firmware_is_at_least


class ClientEndpointMixin:
    """Endpoint selection and availability methods for OPNsenseClient."""

    if TYPE_CHECKING:
        _endpoint_availability: dict[tuple[Literal["get", "post"], str], bool]
        _endpoint_cache_ttl_seconds: int
        _endpoint_negative_cache_ttl_seconds: int
        _endpoint_locks: dict[tuple[Literal["get", "post"], str], asyncio.Lock]
        _optional_endpoint_missing_pending_confirmation: set[tuple[Literal["get", "post"], str]]
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
        _endpoint_checked_at: dict[tuple[Literal["get", "post"], str], float]

        async def _get_optional(
            self, path: str
        ) -> tuple[Literal["available", "malformed", "missing", "unavailable"], object]: ...

        async def _post_optional(
            self,
            path: str,
            payload: MutableMapping[str, Any] | None = None,
        ) -> tuple[Literal["available", "malformed", "missing", "unavailable"], object]: ...

        async def _safe_dict_get(self, path: str) -> dict[str, Any]: ...

    _CORE_FIRMWARE_STATUS_ENDPOINT = "/api/core/firmware/status"
    _OPTIONAL_GET_ENDPOINTS: frozenset[str] = frozenset(
        {
            "/api/speedtest/service/showlog",
            "/api/speedtest/service/showstat",
            "/api/nut/diagnostics/upsstatus",
            "/api/unbound/settings/search_dnsbl",
            "/api/vnstat/service/hourly",
            "/api/vnstat/service/daily",
            "/api/vnstat/service/monthly",
            "/api/vnstat/service/yearly",
        }
    )
    _OPTIONAL_POST_ENDPOINTS: frozenset[str] = frozenset(
        {
            "/api/smart/service/list",
            "/api/smart/service/info",
        }
    )
    _OPTIONAL_POST_CACHE_PATHS: dict[str, str] = {
        "/api/smart/service/list/1": "/api/smart/service/list",
    }
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

    def _get_endpoint_cache_key(self, method: str, path: str) -> tuple[Literal["get", "post"], str]:
        """Build an internal endpoint cache key for method and path."""
        if method == "post":
            return ("post", path)
        return ("get", path)

    def _is_optional_endpoint(self, method: str, path: str) -> bool:
        """Return whether a method/path pair is an explicit optional capability."""
        if method == "post":
            return path in self._OPTIONAL_POST_ENDPOINTS
        return path in self._OPTIONAL_GET_ENDPOINTS

    def _get_endpoint_cache_ttl_seconds(
        self,
        method: str,
        path: str,
        is_available: bool,
    ) -> int:
        """Return the endpoint cache TTL based on method/path and probe outcome."""
        if is_available is False and self._is_optional_endpoint(method, path):
            return self._endpoint_negative_cache_ttl_seconds
        return self._endpoint_cache_ttl_seconds

    def _is_endpoint_cache_fresh(
        self,
        method: str,
        path: str,
        is_available: bool,
        checked_at: float,
    ) -> bool:
        """Return whether a cached availability result is still fresh."""
        ttl_seconds = self._get_endpoint_cache_ttl_seconds(method, path, is_available)
        return monotonic() - checked_at < ttl_seconds

    def _get_cached_endpoint_availability(
        self,
        method: str,
        path: str,
        force_refresh: bool,
    ) -> bool | None:
        """Return cached optional endpoint state when valid and not expired.

        Args:
            method: HTTP method for the cache key.
            path: Optional endpoint cache path.
            force_refresh (bool): Whether to bypass cached availability.

        Returns:
            bool | None: Cached availability state when cache is fresh, or ``None``
                when cache is missing, stale, or bypassed.
        """
        if force_refresh:
            return None

        cache_key = self._get_endpoint_cache_key(method, path)
        cached_is_available = self._endpoint_availability.get(cache_key)
        cached_at = self._endpoint_checked_at.get(cache_key)
        if cached_is_available is None or cached_at is None:
            return None

        if self._is_endpoint_cache_fresh(method, path, cached_is_available, cached_at):
            return cached_is_available

        return None

    def _log_endpoint_transition(
        self,
        cache_key: tuple[Literal["get", "post"], str],
        new_state: str,
        reason: str,
    ) -> None:
        """Log a concise optional endpoint cache transition."""
        old_value = self._endpoint_availability.get(cache_key)
        old_state = (
            "available" if old_value is True else "missing" if old_value is False else "unknown"
        )
        _LOGGER.debug(
            "Optional endpoint cache transition %s %s: %s -> %s (%s)",
            cache_key[0].upper(),
            cache_key[1],
            old_state,
            new_state,
            reason,
        )

    def _refresh_positive_endpoint_observation(
        self,
        cache_key: tuple[Literal["get", "post"], str],
        reason: str,
    ) -> None:
        """Refresh a registered positive optional endpoint observation."""
        self._log_endpoint_transition(cache_key, "available", reason)
        self._endpoint_availability[cache_key] = True
        self._endpoint_checked_at[cache_key] = monotonic()
        self._optional_endpoint_missing_pending_confirmation.discard(cache_key)

    def _invalidate_endpoint_observation(
        self,
        cache_key: tuple[Literal["get", "post"], str],
        reason: str,
    ) -> None:
        """Invalidate an exact optional observation and mark it pending confirmation."""
        self._log_endpoint_transition(cache_key, "pending", reason)
        self._endpoint_availability.pop(cache_key, None)
        self._endpoint_checked_at.pop(cache_key, None)
        self._optional_endpoint_missing_pending_confirmation.add(cache_key)

    def _store_confirmed_negative_endpoint_observation(
        self,
        cache_key: tuple[Literal["get", "post"], str],
        reason: str,
    ) -> None:
        """Store a confirmed optional endpoint absence with the negative TTL."""
        self._log_endpoint_transition(cache_key, "missing", reason)
        self._endpoint_availability[cache_key] = False
        self._endpoint_checked_at[cache_key] = monotonic()
        self._optional_endpoint_missing_pending_confirmation.discard(cache_key)

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
                When no exception is raised, this method always returns a ``bool``.

        Raises:
            OPNsenseError: Raised when an HTTP response or transport error occurs and
                ``self._throw_errors`` is ``True``.

        Side Effects:
            Increments the REST query counter for uncached probes and updates
            endpoint availability caches.
        """
        if not isinstance(path, str) or not path:
            return False
        normalized_method = method.lower()
        if normalized_method not in {"get", "post"}:
            return False

        cache_key = self._get_endpoint_cache_key(normalized_method, path)
        cached_is_available = self._endpoint_availability.get(cache_key)
        cached_at = self._endpoint_checked_at.get(cache_key)
        if (
            not force_refresh
            and cached_is_available is not None
            and cached_at is not None
            and self._is_endpoint_cache_fresh(
                normalized_method, path, cached_is_available, cached_at
            )
        ):
            return cached_is_available

        cache_lock = self._endpoint_locks.setdefault(cache_key, asyncio.Lock())
        async with cache_lock:
            cached_is_available = self._endpoint_availability.get(cache_key)
            cached_at = self._endpoint_checked_at.get(cache_key)
            if (
                not force_refresh
                and cached_is_available is not None
                and cached_at is not None
                and self._is_endpoint_cache_fresh(
                    normalized_method, path, cached_is_available, cached_at
                )
            ):
                return cached_is_available

            self._rest_api_query_count += 1
            url = f"{self._url}{path}"
            _LOGGER.debug("[is_%s_endpoint_available] url: %s", normalized_method, url)

            try:
                request = getattr(self._session, normalized_method)
                async with request(
                    url,
                    auth=aiohttp.BasicAuth(self._username, self._password),
                    timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                    ssl=self._verify_ssl,
                ) as response:
                    checked_at = monotonic()
                    if response.ok:
                        self._endpoint_availability[cache_key] = True
                        self._endpoint_checked_at[cache_key] = checked_at
                        return True
                    if response.status == 404:
                        self._endpoint_availability[cache_key] = False
                        self._endpoint_checked_at[cache_key] = checked_at
                        return False

                    if response.status == 403:
                        _LOGGER.error(
                            "Permission Error in is_%s_endpoint_available. Path: %s. Ensure the OPNsense user connected to HA has appropriate access. Recommend full admin access",
                            normalized_method,
                            url,
                        )
                    else:
                        _LOGGER.warning(
                            "Transient %s endpoint check failure for %s. Response %s: %s. Not caching result.",
                            normalized_method.upper(),
                            path,
                            response.status,
                            response.reason,
                        )
                    if self._throw_errors:
                        raise _opnsense_http_error(response.status, response.reason)
                    return False
            except (aiohttp.ClientError, TimeoutError) as e:
                _LOGGER.warning(
                    "%s endpoint availability check failed for %s. %s: %s. Not caching result.",
                    normalized_method.upper(),
                    path,
                    type(e).__name__,
                    e,
                )
                if self._throw_errors:
                    raise _map_opnsense_exception(e) from e
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

    async def _check_optional_get_endpoint(
        self,
        path: str,
        *,
        force_refresh: bool = False,
    ) -> tuple[Literal["available", "malformed", "missing", "unavailable"], object]:
        """Request an explicitly optional GET and reconcile its cache observation."""
        return await self._check_optional_endpoint(
            method="get",
            path=path,
            cache_path=path,
            payload=None,
            force_refresh=force_refresh,
        )

    async def _check_optional_post_endpoint(
        self,
        path: str,
        payload: MutableMapping[str, Any] | None = None,
        cache_path: str | None = None,
        *,
        force_refresh: bool = False,
    ) -> tuple[Literal["available", "malformed", "missing", "unavailable"], object]:
        """Request an explicitly read-only optional POST and reconcile its cache."""
        resolved_cache_path = cache_path or path
        expected_cache_path = self._OPTIONAL_POST_CACHE_PATHS.get(path, path)
        if resolved_cache_path != expected_cache_path:
            _LOGGER.debug(
                "Rejected optional POST cache mapping %s -> %s (expected %s)",
                path,
                resolved_cache_path,
                expected_cache_path,
            )
            return "unavailable", {}
        return await self._check_optional_endpoint(
            method="post",
            path=path,
            cache_path=resolved_cache_path,
            payload=payload,
            force_refresh=force_refresh,
        )

    async def _check_optional_endpoint(
        self,
        *,
        method: Literal["get", "post"],
        path: str,
        cache_path: str,
        payload: MutableMapping[str, Any] | None,
        force_refresh: bool,
    ) -> tuple[Literal["available", "malformed", "missing", "unavailable"], object]:
        """Run one real optional request and reconcile its registered cache key."""
        if not path or not self._is_optional_endpoint(method, cache_path):
            _LOGGER.debug("Unregistered optional endpoint: %s %s", method.upper(), path)
            return "unavailable", {}

        cache_key = self._get_endpoint_cache_key(method, cache_path)
        cache_lock = self._endpoint_locks.setdefault(cache_key, asyncio.Lock())
        async with cache_lock:
            had_confirmed_negative = self._endpoint_availability.get(cache_key) is False
            cached_state = self._get_cached_endpoint_availability(method, cache_path, force_refresh)
            if cached_state is False:
                return "missing", {}

            was_pending = cache_key in self._optional_endpoint_missing_pending_confirmation
            if method == "post":
                optional_state, response_payload = await self._post_optional(path, payload)
            else:
                optional_state, response_payload = await self._get_optional(path)

            if optional_state in {"available", "malformed"}:
                self._refresh_positive_endpoint_observation(cache_key, f"real_{method}_success")
                return optional_state, response_payload

            if optional_state == "unavailable":
                return "unavailable", {}

            if not was_pending and not had_confirmed_negative:
                self._invalidate_endpoint_observation(cache_key, f"real_{method}_404")

            if not await self._is_core_firmware_endpoint_healthy():
                _LOGGER.debug(
                    "Skipping optional endpoint confirmation because firmware status endpoint is unavailable: %s",
                    cache_path,
                )
                return "unavailable", {}

            if was_pending or had_confirmed_negative:
                self._store_confirmed_negative_endpoint_observation(
                    cache_key, f"confirmed_{method}_404"
                )
            return "missing", {}

    async def _is_core_firmware_endpoint_healthy(self) -> bool:
        """Return whether a fresh control request proves the router API is healthy.

        Returns:
            bool: ``True`` only when the required firmware status endpoint
                succeeds. This health check never changes endpoint cache state.
        """
        self._rest_api_query_count += 1
        url = f"{self._url}{self._CORE_FIRMWARE_STATUS_ENDPOINT}"
        _LOGGER.debug("[optional_endpoint_core_health] url: %s", url)
        try:
            async with self._session.get(
                url,
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                ssl=self._verify_ssl,
            ) as response:
                if response.ok:
                    return True
                _LOGGER.debug(
                    "Optional endpoint core health check returned %s: %s",
                    response.status,
                    response.reason,
                )
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug(
                "Optional endpoint core health check failed. %s: %s",
                type(err).__name__,
                err,
            )
        return False

    @deprecated("Endpoint style selection is internal. Direct calls are no longer needed.")
    async def set_use_snake_case(self, initial: bool = False) -> None:
        """Deprecated wrapper that preserves legacy ``initial`` compatibility."""
        await self._set_use_snake_case(initial=initial)

    async def _set_use_snake_case(self, initial: bool = False) -> None:
        """Set endpoint naming mode from the detected firmware version.

        Args:
            initial (bool): Whether to preserve the legacy unknown-firmware raise behavior.

        Returns:
            None: This method updates internal client state only.

        Raises:
            OPNsenseUnknownFirmware: Raised when ``initial`` is ``True`` and the firmware
                version cannot be compared reliably.
        """
        firmware_version = await cast(
            AiopnsenseClientProtocol,
            self,
        ).get_host_firmware_version()
        self._use_snake_case = True
        if firmware_version is None:
            _LOGGER.debug("Using snake_case endpoints because firmware version is unavailable")
            if initial:
                raise OPNsenseUnknownFirmware
            return
        uses_snake_case = firmware_is_at_least(firmware_version, LEGACY_CAMELCASE_ENDPOINT_FIRMWARE)
        if uses_snake_case is False:
            _LOGGER.debug(
                "Using camelCase endpoints for OPNsense < %s",
                LEGACY_CAMELCASE_ENDPOINT_FIRMWARE,
            )
            self._use_snake_case = False
        elif uses_snake_case is True:
            _LOGGER.debug(
                "Using snake_case endpoints for OPNsense >= %s",
                LEGACY_CAMELCASE_ENDPOINT_FIRMWARE,
            )
        else:
            _LOGGER.debug(
                "Unable to compare firmware version %s for endpoint style",
                firmware_version,
            )
            if initial:
                raise OPNsenseUnknownFirmware

    async def _get_endpoint_path(self, snake_case_path: str, camel_case_path: str) -> str:
        """Return the firmware-appropriate endpoint path.

        Args:
            snake_case_path (str): Endpoint path for newer snake_case firmware.
            camel_case_path (str): Endpoint path for older camelCase firmware.

        Returns:
            str: Selected endpoint path for the active firmware family.
        """
        if self._use_snake_case is None:
            await self._set_use_snake_case()
        # _get_endpoint_path treats _use_snake_case as a three-state flag:
        # None means _set_use_snake_case has not determined the endpoint style yet,
        # True selects snake_case, and False selects camelCase. Use "is not False"
        # so an indeterminate or newer-firmware value stays on the snake_case path.
        return snake_case_path if self._use_snake_case is not False else camel_case_path

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
