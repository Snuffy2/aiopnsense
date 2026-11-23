"""SMART plugin methods for OPNsenseClient."""

from collections.abc import MutableMapping
from typing import Any

from ._error_policy import _log_errors
from ._typing import AiopnsenseClientProtocol
from .helpers import _LOGGER

SMART_SERVICE_LIST_ENDPOINT = "/api/smart/service/list"
SMART_SERVICE_DETAIL_ENDPOINT = f"{SMART_SERVICE_LIST_ENDPOINT}/1"
SMART_SERVICE_INFO_ENDPOINT = "/api/smart/service/info"


class SmartMixin(AiopnsenseClientProtocol):
    """SMART plugin methods for OPNsenseClient."""

    @_log_errors
    async def get_smart(self) -> list[dict[str, Any]] | None:
        """Return SMART device data from the OPNsense SMART plugin.

        Returns:
            list[dict[str, Any]] | None: SMART device rows, or an empty list when the
                endpoint returns 404. With ``throw_errors`` off, 403 and transient HTTP
                or transport failures return an empty list, and an undecodable response
                body returns ``None``. With ``throw_errors`` on, those failures raise
                ``OPNsenseError``.
        """
        outcome = await self._fetch_post_endpoint_outcome(SMART_SERVICE_DETAIL_ENDPOINT)
        if not outcome.is_data:
            _LOGGER.debug("SMART plugin unavailable")
            return []
        devices = outcome.mapping().get("devices", [])
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
    async def get_smart_info(self, device: str, info_type: str = "a") -> dict[str, Any] | None:
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
            dict[str, Any] | None: Decoded SMART details, with non-mapping outputs
                wrapped under ``output``. A 404 returns an empty dictionary. With
                ``throw_errors`` off, 403 and transient HTTP or transport failures return
                an empty dictionary, and an undecodable response body returns ``None``.
                With ``throw_errors`` on, those failures raise ``OPNsenseError``.
        """
        outcome = await self._fetch_post_endpoint_outcome(
            SMART_SERVICE_INFO_ENDPOINT,
            {"device": device, "type": info_type, "json": True},
        )
        if not outcome.is_data:
            _LOGGER.debug("SMART plugin unavailable")
            return {}
        output = outcome.mapping().get("output", {})
        return dict(output) if isinstance(output, MutableMapping) else {"output": output}
