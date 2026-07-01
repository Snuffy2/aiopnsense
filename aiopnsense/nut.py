"""NUT plugin methods for OPNsenseClient."""

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
        return await self._safe_dict_get(NUT_DIAGNOSTICS_UPS_STATUS_ENDPOINT)
