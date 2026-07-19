"""NUT plugin methods for OPNsenseClient."""

from collections.abc import Mapping
from typing import Any

from ._typing import AiopnsenseClientProtocol
from .helpers import _LOGGER, _log_errors

NUT_DIAGNOSTICS_UPS_STATUS_ENDPOINT = "/api/nut/diagnostics/upsstatus"


class NutMixin(AiopnsenseClientProtocol):
    """NUT plugin methods for OPNsenseClient."""

    @_log_errors
    async def get_nut_ups_status(self) -> dict[str, Any]:
        """Return UPS status data from the OPNsense NUT plugin.

        Returns:
            dict[str, Any]: Decoded NUT UPS status payload, or an empty
                dictionary when the NUT diagnostics endpoint is unavailable.
        """
        if not await self._is_get_endpoint_available(NUT_DIAGNOSTICS_UPS_STATUS_ENDPOINT):
            _LOGGER.debug("NUT UPS status endpoint unavailable")
            return {}
        raw_payload = await self._safe_dict_get(NUT_DIAGNOSTICS_UPS_STATUS_ENDPOINT)
        return self._normalize_nut_ups_status_payload(raw_payload)

    @staticmethod
    def _normalize_nut_ups_status_payload(payload: Any) -> dict[str, Any]:
        """Normalize raw NUT UPS status payloads to the expected status mapping.

        Args:
            payload (Any): Raw response payload from the NUT diagnostics endpoint.

        Returns:
            dict[str, Any]: Normalized NUT status payload in the shape
                ``{"status": mapping}`` when possible, otherwise
                ``{"status": {}}``.
        """
        if not isinstance(payload, Mapping):
            return {"status": {}}

        status_value = payload.get("status")
        if isinstance(status_value, Mapping):
            return {"status": dict(status_value)}

        response_value = payload.get("response")
        if not isinstance(response_value, str):
            return {"status": {}}

        status: dict[str, str] = {}
        for line in response_value.splitlines():
            if not line.strip():
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            parsed_key = key.strip()
            if not parsed_key:
                continue
            status[parsed_key] = value.strip()

        return {"status": status}
