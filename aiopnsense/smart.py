"""SMART plugin methods for OPNsenseClient."""

from collections.abc import MutableMapping
from typing import Any

from ._typing import AiopnsenseClientProtocol, CategoryResult
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
        return (await self.get_smart_result()).data

    async def get_smart_result(self) -> CategoryResult[list[dict[str, Any]]]:
        """Return SMART device data with authoritative availability metadata."""
        result = CategoryResult.coerce(
            await self._check_optional_post_endpoint(
                SMART_SERVICE_DETAIL_ENDPOINT,
                cache_path=SMART_SERVICE_LIST_ENDPOINT,
            )
        )
        if result.state != "available":
            _LOGGER.debug("SMART plugin unavailable")
            return CategoryResult([], result.state, result.authoritative)
        smart_info = result.data
        if not isinstance(smart_info, MutableMapping):
            return CategoryResult([], "malformed", False)
        if "devices" not in smart_info or not isinstance(smart_info["devices"], list):
            _LOGGER.debug(
                "Discarding SMART devices payload because devices is missing or not a list: %r",
                smart_info.get("devices"),
            )
            return CategoryResult([], "malformed", False)
        devices = smart_info["devices"]
        smart_devices: list[dict[str, Any]] = []
        malformed = False
        for device in devices:
            if not isinstance(device, MutableMapping):
                malformed = True
                _LOGGER.debug(
                    "Discarding SMART device row because item is not a mapping: %r", device
                )
                continue
            ident = device.get("ident", "")
            if not isinstance(ident, str) or not ident.strip():
                malformed = True
                _LOGGER.debug(
                    "Discarding SMART device row because ident is missing or invalid: %r", device
                )
                continue
            smart_devices.append(dict(device))
        if malformed:
            return CategoryResult(smart_devices, "malformed", False)
        return CategoryResult(smart_devices, "available", True)

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
        return (await self.get_smart_info_result(device, info_type=info_type)).data

    async def get_smart_info_result(
        self, device: str, info_type: str = "a"
    ) -> CategoryResult[dict[str, Any]]:
        """Return SMART detail data with authoritative availability metadata.

        Args:
            device (str): SMART device name, such as ``nvme0`` or ``ada0``.
            info_type (str): SMART info selector supported by the plugin.

        Returns:
            CategoryResult[dict[str, Any]]: Normalized detail data and its
                authoritative availability state.
        """
        info_payload = {
            "device": device,
            "type": info_type,
            "json": True,
        }
        result = CategoryResult.coerce(
            await self._check_optional_post_endpoint(
                SMART_SERVICE_INFO_ENDPOINT,
                payload=info_payload,
            )
        )
        if result.state != "available":
            _LOGGER.debug("SMART plugin unavailable")
            return CategoryResult({}, result.state, result.authoritative)
        response = result.data
        if not isinstance(response, MutableMapping) or "output" not in response:
            _LOGGER.debug("SMART info response is missing a valid output envelope")
            return CategoryResult({}, "malformed", False)
        output = response["output"]
        if isinstance(output, MutableMapping):
            return CategoryResult(dict(output), "available", True)
        normalized = {"output": output}
        if isinstance(output, str):
            return CategoryResult(normalized, "available", True)
        return CategoryResult(normalized, "malformed", False)
