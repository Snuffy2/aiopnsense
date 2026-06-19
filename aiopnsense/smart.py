"""SMART plugin methods for OPNsenseClient."""

from collections.abc import MutableMapping
from typing import Any

from ._typing import AiopnsenseClientProtocol
from .helpers import _LOGGER, _log_errors


class SmartMixin(AiopnsenseClientProtocol):
    """SMART plugin methods for OPNsenseClient."""

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
        smart_endpoint = "/api/smart/service/list/1" if details else "/api/smart/service/list"
        if not await self.is_post_endpoint_available(smart_endpoint):
            _LOGGER.debug("SMART plugin unavailable")
            return []
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
        smart_info_endpoint = "/api/smart/service/info"
        if not await self.is_post_endpoint_available(smart_info_endpoint):
            _LOGGER.debug("SMART plugin unavailable")
            return {}
        response = await self._safe_dict_post(
            smart_info_endpoint,
            {"device": device, "type": info_type, "json": True},
        )
        output = response.get("output", {})
        return dict(output) if isinstance(output, MutableMapping) else {"output": output}
