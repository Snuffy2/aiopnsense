"""SMART plugin methods for OPNsenseClient."""

from collections.abc import MutableMapping
from datetime import datetime
from typing import Any
from typing import TYPE_CHECKING

import aiohttp

from ._typing import AiopnsenseClientProtocol
from .const import DEFAULT_REQUEST_TIMEOUT_SECONDS
from .helpers import _LOGGER, _log_errors


class SmartMixin(AiopnsenseClientProtocol):
    """SMART plugin methods for OPNsenseClient."""

    if TYPE_CHECKING:
        _endpoint_availability: dict[str, bool]
        _endpoint_cache_ttl_seconds: int
        _endpoint_checked_at: dict[str, datetime]
        _password: str
        _rest_api_query_count: int
        _session: aiohttp.ClientSession
        _throw_errors: bool
        _url: str
        _username: str
        _verify_ssl: bool

    @staticmethod
    def _normalize_smart_devices(
        devices: object,
        *,
        allow_string_rows: bool,
    ) -> list[dict[str, Any]]:
        """Normalize SMART device rows into dictionaries with a device key.

        Args:
            devices (object): Raw SMART device payload returned by OPNsense.
            allow_string_rows (bool): Whether plain device-name strings should
                be converted into SMART device mappings.

        Returns:
            list[dict[str, Any]]: Normalized SMART device rows.
        """
        smart_devices: list[dict[str, Any]] = []
        for device in devices if isinstance(devices, list) else []:
            if allow_string_rows and isinstance(device, str) and device:
                smart_devices.append({"device": device})
                continue
            if not isinstance(device, MutableMapping):
                continue

            normalized_device = dict(device)
            device_name = normalized_device.get("device") or normalized_device.get("dev")
            if isinstance(device_name, str) and device_name:
                normalized_device["device"] = device_name
                smart_devices.append(normalized_device)
        return smart_devices

    async def _is_smart_plugin_available(self) -> bool:
        """Return whether the SMART plugin list endpoint is available via POST.

        Returns:
            bool: ``True`` when the SMART plugin responds to the list endpoint;
                otherwise, ``False``. A 404 result is cached to avoid repeated
                missing-plugin POST attempts.
        """
        endpoint = "/api/smart/service/list"
        cache_key = f"post:{endpoint}"
        now = datetime.now().astimezone()
        cache_is_fresh = (
            cache_key in self._endpoint_checked_at
            and (now - self._endpoint_checked_at[cache_key]).total_seconds()
            < self._endpoint_cache_ttl_seconds
        )

        if cache_is_fresh and cache_key in self._endpoint_availability:
            return self._endpoint_availability[cache_key]

        self._rest_api_query_count += 1
        try:
            async with self._session.post(
                f"{self._url}{endpoint}",
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
                _LOGGER.warning(
                    "Transient SMART plugin availability check failure for %s. Response %s: %s. Not caching result.",
                    endpoint,
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
        except (aiohttp.ClientError, TimeoutError) as err:
            self._endpoint_availability.pop(cache_key, None)
            self._endpoint_checked_at.pop(cache_key, None)
            _LOGGER.warning(
                "SMART plugin availability check failed for %s. %s: %s. Not caching result.",
                endpoint,
                type(err).__name__,
                err,
            )
            if self._throw_errors:
                raise
        return False

    @_log_errors
    async def get_smart(self, details: bool = True) -> list[dict[str, Any]]:
        """Return SMART device data from the OPNsense SMART plugin.

        Args:
            details (bool): Whether to request the detailed SMART device list.

        Returns:
            list[dict[str, Any]]: SMART device rows normalized to dictionaries
                that always include a ``device`` key when a usable device name
                is available.
        """
        if not await self._is_smart_plugin_available():
            _LOGGER.debug("SMART plugin unavailable")
            return []
        smart_endpoint = "/api/smart/service/list/1" if details else "/api/smart/service/list"
        smart_info = await self._safe_dict_post(smart_endpoint)
        return self._normalize_smart_devices(
            smart_info.get("devices", []),
            allow_string_rows=not details,
        )

    @_log_errors
    async def get_smart_info(self, device: str, info_type: str = "a") -> dict[str, Any]:
        """Return SMART detail data for a single device.

        Args:
            device (str): SMART device name, such as ``nvme0`` or ``ada0``.
            info_type (str): SMART info selector supported by the plugin.

        Returns:
            dict[str, Any]: Decoded SMART detail payload. Non-mapping outputs
                are wrapped under ``output`` to preserve a stable mapping API.
        """
        if not await self._is_smart_plugin_available():
            _LOGGER.debug("SMART plugin unavailable")
            return {}
        smart_info_endpoint = "/api/smart/service/info"
        response = await self._safe_dict_post(
            smart_info_endpoint,
            {"device": device, "type": info_type, "json": True},
        )
        output = response.get("output", {})
        return dict(output) if isinstance(output, MutableMapping) else {"output": output}
