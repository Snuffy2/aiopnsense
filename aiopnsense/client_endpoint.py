"""Endpoint selection and availability helpers for OPNsenseClient."""

from datetime import datetime
from typing import TYPE_CHECKING, cast

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
        _password: str
        _rest_api_query_count: int
        _session: aiohttp.ClientSession
        _throw_errors: bool
        _url: str
        _use_snake_case: bool | None
        _username: str
        _verify_ssl: bool

    async def set_use_snake_case(self) -> None:
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
            await self.set_use_snake_case()
        # _get_endpoint_path treats _use_snake_case as a three-state flag:
        # None means set_use_snake_case has not determined the endpoint style yet,
        # True selects snake_case, and False selects camelCase. Use "is not False"
        # so an indeterminate or newer-firmware value stays on the snake_case path.
        return snake_case_path if self._use_snake_case is not False else camel_case_path

    async def is_endpoint_available(self, path: str, force_refresh: bool = False) -> bool:
        """Return whether a specific API endpoint appears to be available.

        Args:
            path (str): API endpoint path to request.
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
            Increments the REST query counter for uncached probes and updates endpoint
            availability caches. On transient transport or HTTP response errors, cache
            entries for ``path`` are removed before returning ``False`` or re-raising
            in throw mode.
        """
        if not isinstance(path, str) or not path:
            return False

        now = datetime.now().astimezone()
        cache_is_fresh = (
            path in self._endpoint_checked_at
            and (now - self._endpoint_checked_at[path]).total_seconds()
            < self._endpoint_cache_ttl_seconds
        )

        if not force_refresh and cache_is_fresh and path in self._endpoint_availability:
            return self._endpoint_availability[path]

        self._rest_api_query_count += 1
        url: str = f"{self._url}{path}"
        _LOGGER.debug("[is_endpoint_available] url: %s", url)

        try:
            async with self._session.get(
                url,
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                ssl=self._verify_ssl,
            ) as response:
                if response.ok:
                    self._endpoint_availability[path] = True
                    self._endpoint_checked_at[path] = now
                    return True
                if response.status == 404:
                    self._endpoint_availability[path] = False
                    self._endpoint_checked_at[path] = now
                    return False

                self._endpoint_availability.pop(path, None)
                self._endpoint_checked_at.pop(path, None)
                if response.status == 403:
                    _LOGGER.error(
                        "Permission Error in is_endpoint_available. Path: %s. Ensure the OPNsense user connected to HA has appropriate access. Recommend full admin access",
                        url,
                    )
                else:
                    _LOGGER.warning(
                        "Transient endpoint check failure for %s. Response %s: %s. Not caching result.",
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
            self._endpoint_availability.pop(path, None)
            self._endpoint_checked_at.pop(path, None)
            _LOGGER.warning(
                "Endpoint availability check failed for %s. %s: %s. Not caching result.",
                path,
                type(e).__name__,
                e,
            )
            if self._throw_errors:
                raise
            return False
