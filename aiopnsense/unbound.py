"""Unbound DNS blocklist methods for OPNsenseClient."""

from collections.abc import MutableMapping
from typing import Any

import aiohttp

from ._typing import PyOPNsenseClientProtocol
from .helpers import _LOGGER, _log_errors


class UnboundMixin(PyOPNsenseClientProtocol):
    """Unbound DNS blocklist methods for OPNsenseClient."""

    @_log_errors
    async def get_unbound_blocklist(self) -> dict[str, Any]:
        """Return the Unbound Blocklist details.

        Returns:
            dict[str, Any]: Normalized data returned by the related OPNsense endpoint.
        """
        dnsbl_endpoint = "/api/unbound/settings/search_dnsbl"
        if not await self.is_endpoint_available(dnsbl_endpoint):
            _LOGGER.debug("Unbound DNSBL endpoint unavailable")
            return {}

        dnsbl_raw = await self._safe_dict_get(dnsbl_endpoint)
        if not isinstance(dnsbl_raw, MutableMapping):
            return {}
        dnsbl_rows = dnsbl_raw.get("rows", [])
        if not isinstance(dnsbl_rows, list) or not len(dnsbl_rows) > 0:
            return {}
        dnsbl_full: dict[str, Any] = {}
        for dnsbl in dnsbl_rows:
            if not isinstance(dnsbl, MutableMapping):
                continue
            # _LOGGER.debug("[get_unbound_blocklist] dnsbl: %s", dnsbl)
            if dnsbl.get("uuid"):
                dnsbl_full.update({dnsbl["uuid"]: dnsbl})
        # _LOGGER.debug("[get_unbound_blocklist] dnsbl_full: %s", dnsbl_full)
        _LOGGER.debug("[get_unbound_blocklist] dnsbl_full length: %s", len(dnsbl_full))
        return dnsbl_full

    async def _toggle_unbound_blocklist(self, set_state: bool, uuid: str | None) -> bool:
        """Enable or disable the unbound blocklist.

        Args:
            set_state (bool): Target alias enabled state.
            uuid (str | None): Unique identifier of the target OPNsense resource.

        Returns:
            bool: True when the toggle operation completes successfully; otherwise, False.
        """
        if not uuid:
            _LOGGER.error("Blocklist uuid must be provided for Unbound Extended Blocklists")
            return False
        endpoint = f"/api/unbound/settings/toggle_dnsbl/{uuid}/{'1' if set_state else '0'}"
        response = await self._safe_dict_post(endpoint)
        result = response.get("result")
        if (set_state and result == "Enabled") or (not set_state and result == "Disabled"):
            try:
                dnsbl_resp = await self._get("/api/unbound/service/dnsbl")
                _LOGGER.debug(
                    "[_toggle_unbound_blocklist] uuid: %s, set_state: %s, response: %s, dnsbl_resp: %s",
                    uuid,
                    "On" if set_state else "Off",
                    response,
                    dnsbl_resp,
                )
                if isinstance(dnsbl_resp, MutableMapping) and dnsbl_resp.get(
                    "status", "failed"
                ).startswith("OK"):
                    return True
            except (TimeoutError, aiohttp.ClientError, ValueError, TypeError) as e:
                _LOGGER.error(
                    "Error applying unbound blocklist change for uuid %s. %s: %s",
                    uuid,
                    type(e).__name__,
                    e,
                )
        return False

    @_log_errors
    async def enable_unbound_blocklist(self, uuid: str | None = None) -> bool:
        """Enable the unbound blocklist.

        Args:
            uuid (str | None, optional): Unique identifier of the target OPNsense resource.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        return await self._toggle_unbound_blocklist(set_state=True, uuid=uuid)

    @_log_errors
    async def disable_unbound_blocklist(self, uuid: str | None = None) -> bool:
        """Disable the unbound blocklist.

        Args:
            uuid (str | None, optional): Unique identifier of the target OPNsense resource.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        return await self._toggle_unbound_blocklist(set_state=False, uuid=uuid)
