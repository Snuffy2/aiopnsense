"""Captive portal voucher methods for OPNsenseClient."""

from collections.abc import MutableMapping
from typing import Any
from urllib.parse import quote

from ._typing import PyOPNsenseClientProtocol
from .exceptions import VoucherServerError
from .helpers import _LOGGER, human_friendly_duration, timestamp_to_datetime, try_to_int


class VouchersMixin(PyOPNsenseClientProtocol):
    """Captive portal voucher methods for OPNsenseClient."""

    async def generate_vouchers(self, data: MutableMapping[str, Any]) -> list[dict[str, Any]]:
        """Generate vouchers from the Voucher Server.

        Args:
            data (MutableMapping[str, Any]): Configuration data used to
                generate vouchers.

        Returns:
            list[dict[str, Any]]: List of normalized entries produced by this method.
        """
        list_providers_endpoint = "/api/captiveportal/voucher/list_providers"
        generate_vouchers_endpoint = "/api/captiveportal/voucher/generate_vouchers"
        if data.get("voucher_server", None):
            server = data.get("voucher_server")
        else:
            if not await self.is_endpoint_available(list_providers_endpoint):
                _LOGGER.debug("Voucher provider endpoint unavailable")
                return []
            servers = await self._safe_list_get(list_providers_endpoint)
            if len(servers) == 0:
                raise VoucherServerError("No voucher servers exist")
            if len(servers) != 1:
                raise VoucherServerError(
                    "More than one voucher server. Must specify voucher server name"
                )
            server = servers[0]
        if not await self.is_endpoint_available(generate_vouchers_endpoint):
            _LOGGER.debug("Voucher generation endpoint unavailable")
            return []
        server_slug = quote(str(server), safe="")
        payload: dict[str, Any] = dict(data).copy()
        payload.pop("voucher_server", None)
        generate_vouchers_url = f"{generate_vouchers_endpoint}/{server_slug}/"
        _LOGGER.debug("[generate_vouchers] url: %s, payload: %s", generate_vouchers_url, payload)
        vouchers = await self._safe_list_post(
            generate_vouchers_url,
            payload=payload,
        )
        ordered_keys: list[str] = [
            "username",
            "password",
            "vouchergroup",
            "starttime",
            "expirytime",
            "expiry_timestamp",
            "validity_str",
            "validity",
        ]
        for voucher in vouchers:
            validity = try_to_int(voucher.get("validity"))
            if validity is not None:
                voucher["validity_str"] = human_friendly_duration(validity)

            expiry_timestamp = try_to_int(voucher.get("expirytime"))
            if expiry_timestamp is not None:
                voucher["expiry_timestamp"] = expiry_timestamp
                voucher["expirytime"] = timestamp_to_datetime(expiry_timestamp)

            rearranged_voucher: dict[str, Any] = {
                key: voucher[key] for key in ordered_keys if key in voucher
            }
            voucher.clear()
            voucher.update(rearranged_voucher)

        _LOGGER.debug("[generate_vouchers] vouchers: %s", vouchers)
        return vouchers
