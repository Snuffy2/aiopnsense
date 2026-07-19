"""SMART plugin methods for OPNsenseClient."""

from collections.abc import MutableMapping
from typing import Any

from ._typing import AiopnsenseClientProtocol
from .helpers import _LOGGER, _log_errors

SMART_SERVICE_LIST_ENDPOINT = "/api/smart/service/list"
SMART_SERVICE_DETAIL_ENDPOINT = f"{SMART_SERVICE_LIST_ENDPOINT}/1"
SMART_SERVICE_INFO_ENDPOINT = "/api/smart/service/info"


class SmartMixin(AiopnsenseClientProtocol):
    """SMART plugin methods for OPNsenseClient."""

    @_log_errors
    async def get_smart(self) -> list[dict[str, Any]]:
        """Return SMART device data from the OPNsense SMART plugin.

        Returns:
            list[dict[str, Any]]: SMART device rows returned by the detailed API.
        """
        list_status, smart_info = await self._check_optional_post_endpoint(
            SMART_SERVICE_DETAIL_ENDPOINT,
            cache_path=SMART_SERVICE_LIST_ENDPOINT,
        )
        if list_status != "available" or not isinstance(smart_info, MutableMapping):
            _LOGGER.debug("SMART plugin unavailable")
            return []
        devices = smart_info.get("devices", [])
        if not isinstance(devices, list):
            _LOGGER.debug(
                "Discarding SMART devices payload because devices is not a list: %r", devices
            )
            return []
        smart_devices: list[dict[str, Any]] = []
        for device in devices:
            if not isinstance(device, MutableMapping):
                _LOGGER.debug(
                    "Discarding SMART device row because item is not a mapping: %r", device
                )
                continue
            ident = device.get("ident", "")
            if not isinstance(ident, str) or not ident.strip():
                _LOGGER.debug(
                    "Discarding SMART device row because ident is missing or invalid: %r", device
                )
                continue
            smart_devices.append(dict(device))
        return smart_devices

    @_log_errors
    async def get_smart_info(self, device: str, info_type: str = "a") -> dict[str, Any]:
        """Return SMART detail data for a single device.

        Args:
            device (str): SMART device name, such as ``nvme0`` or ``ada0``.
            info_type (str): SMART info selector supported by the plugin.
                Valid values are:
                - ``i``: Device info
                - ``H``: Health
                - ``c``: SMART capabilities
                - ``A``: Attributes
                - ``a``: All (default)
                - ``x``: Extended

        Returns:
            dict[str, Any]: Decoded SMART detail payload. Non-mapping outputs
                are wrapped under ``output`` to preserve a stable mapping API.
        """
        info_payload = {
            "device": device,
            "type": info_type,
            "json": True,
        }
        info_status, response = await self._check_optional_post_endpoint(
            SMART_SERVICE_INFO_ENDPOINT,
            payload=info_payload,
        )
        if info_status != "available" or not isinstance(response, MutableMapping):
            _LOGGER.debug("SMART plugin unavailable")
            return {}
        output = response.get("output", {})
        return dict(output) if isinstance(output, MutableMapping) else {"output": output}
