"""NUT plugin methods for OPNsenseClient."""

from collections.abc import Mapping
import re
from typing import Any

from ._error_policy import _log_errors, _throws_errors
from ._typing import AiopnsenseClientProtocol
from .helpers import _LOGGER

NUT_DIAGNOSTICS_UPS_STATUS_ENDPOINT = "/api/nut/diagnostics/upsstatus"
_NUT_DOT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")


class NutMixin(AiopnsenseClientProtocol):
    """NUT plugin methods for OPNsenseClient."""

    @_log_errors
    async def get_nut_ups_status(self) -> dict[str, Any] | None:
        """Return UPS status data from the OPNsense NUT plugin.

        Returns:
            dict[str, Any] | None: Decoded NUT UPS status payload, an empty
                dictionary when the endpoint is missing (HTTP 404), or ``None``
                when the request fails or the response body is unusable. With
                ``throw_errors`` enabled, request and decoding errors propagate,
                and a decoded non-mapping body returns an empty dictionary.
        """
        outcome = await self._fetch_endpoint_outcome(NUT_DIAGNOSTICS_UPS_STATUS_ENDPOINT)
        if not outcome.is_data:
            _LOGGER.debug("[get_nut_ups_status] NUT UPS status endpoint unavailable")
            return {} if outcome.http_status == 404 else None
        payload = outcome.payload
        if not isinstance(payload, Mapping):
            _LOGGER.debug(
                "[get_nut_ups_status] NUT UPS status payload is not a mapping (type=%s)",
                type(payload).__name__,
            )
            return {} if _throws_errors(self) else None
        return self._normalize_nut_ups_status_payload(payload)

    @staticmethod
    def _normalize_nut_ups_status_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Normalize raw NUT UPS status payloads to the expected status mapping.

        Args:
            payload (Mapping[str, Any]): Response payload from the NUT diagnostics endpoint.

        Returns:
            dict[str, Any]: Normalized NUT status payload in the shape
                ``{..., "status": mapping}`` when parsable status data exists,
                otherwise a shallow copy of the original mapping payload.
        """
        normalized_payload = dict(payload)
        status_value = payload.get("status")
        if isinstance(status_value, Mapping):
            status_mapping = dict(status_value)
            if status_mapping:
                normalized_payload["status"] = status_mapping
                return normalized_payload

        response_value = payload.get("response")
        if not isinstance(response_value, str):
            _LOGGER.debug(
                "NUT UPS status payload response is not a string (type=%s), returning unchanged",
                type(response_value).__name__,
            )
            return normalized_payload

        status: dict[str, str] = {}
        for line in response_value.splitlines():
            if not line.strip():
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            parsed_key = key.strip()
            if not parsed_key or not _NUT_DOT_KEY_PATTERN.match(parsed_key):
                continue
            status[parsed_key] = value.strip()

        if not status:
            _LOGGER.debug("NUT UPS status response did not contain parseable entries")
            return normalized_payload

        normalized_payload["status"] = status
        return normalized_payload
