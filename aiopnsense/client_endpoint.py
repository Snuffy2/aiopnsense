"""Endpoint selection and availability helpers for OPNsenseClient."""

from datetime import datetime
from typing import TYPE_CHECKING, cast
from warnings import deprecated

import aiohttp
import awesomeversion

from ._typing import AiopnsenseClientProtocol
from .const import DEFAULT_REQUEST_TIMEOUT_SECONDS, LEGACY_CAMELCASE_ENDPOINT_FIRMWARE
from .helpers import _LOGGER


class ClientEndpointMixin:
    """Endpoint selection and availability methods for OPNsenseClient."""

    if TYPE_CHECKING:
        _endpoint_availability: dict[str, bool]
        _endpoint_cache_ttl_seconds: int
        _endpoint_checked_at: dict[str, datetime]
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

    async def _set_use_snake_case(self) -> None:
        """Set the endpoint naming mode based on the detected firmware version.

        Returns:
            None: This method updates internal client state only.
        """
        firmware_version = await cast(
            AiopnsenseClientProtocol,
            self,
        ).get_host_firmware_version()
        self._use_snake_case = True
        if firmware_version is None:
            _LOGGER.debug("Using snake_case endpoints because firmware version is unavailable")
            return
        try:
            if awesomeversion.AwesomeVersion(firmware_version) < awesomeversion.AwesomeVersion(
                LEGACY_CAMELCASE_ENDPOINT_FIRMWARE
            ):
                _LOGGER.debug(
                    "Using camelCase endpoints for OPNsense < %s",
                    LEGACY_CAMELCASE_ENDPOINT_FIRMWARE,
                )
                self._use_snake_case = False
            else:
                _LOGGER.debug(
                    "Using snake_case endpoints for OPNsense >= %s",
                    LEGACY_CAMELCASE_ENDPOINT_FIRMWARE,
                )
        except (
            awesomeversion.exceptions.AwesomeVersionCompareException,
            TypeError,
            ValueError,
        ) as err:
            _LOGGER.debug(
                "Unable to compare firmware version %s for endpoint style. %s: %s",
                firmware_version,
                type(err).__name__,
                err,
            )

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
            aiohttp.ClientError: Raised when an HTTP response or transport client error occurs and
                ``self._throw_errors`` is ``True``.
            TimeoutError: Raised when endpoint probing times out and ``self._throw_errors``
                is ``True``.

        Side Effects:
            Increments the REST query counter for uncached probes and updates
            endpoint availability caches. On transient transport or HTTP
            response errors, cache entries for the method-aware key are removed
            before returning ``False`` or re-raising in throw mode.
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
            return self._endpoint_availability[cache_key]

        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[is_%s_endpoint_available] url: %s", normalized_method, url)

        try:
            request = getattr(self._session, normalized_method)
            async with request(
                url,
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                ssl=self._verify_ssl,
            ) as response:
                if response.ok:
                    self._endpoint_availability[cache_key] = True
                    self._endpoint_checked_at[cache_key] = now
                    return True
                if response.status == 404:
                    self._endpoint_availability[cache_key] = False
                    self._endpoint_checked_at[cache_key] = now
                    return False

                self._endpoint_availability.pop(cache_key, None)
                self._endpoint_checked_at.pop(cache_key, None)
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
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=f"HTTP Status Error: {response.status} {response.reason}",
                        headers=response.headers,
                    )
                return False
        except (aiohttp.ClientError, TimeoutError) as e:
            self._endpoint_availability.pop(cache_key, None)
            self._endpoint_checked_at.pop(cache_key, None)
            _LOGGER.warning(
                "%s endpoint availability check failed for %s. %s: %s. Not caching result.",
                normalized_method.upper(),
                path,
                type(e).__name__,
                e,
            )
            if self._throw_errors:
                raise
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
