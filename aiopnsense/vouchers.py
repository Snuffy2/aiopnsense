"""Captive portal voucher methods for OPNsenseClient."""

from collections.abc import MutableMapping
import aiohttp
from typing import Any
from urllib.parse import quote

from ._typing import AiopnsenseClientProtocol
from .exceptions import OPNsenseVoucherServerError
from .helpers import _LOGGER, human_friendly_duration, timestamp_to_datetime, try_to_int

VOUCHER_LIST_PROVIDERS_ENDPOINT = "/api/captiveportal/voucher/list_providers"
VOUCHER_LIST_PROVIDERS_CAMELCASE_ENDPOINT = "/api/captiveportal/voucher/listProviders"
VOUCHER_GENERATE_VOUCHERS_ENDPOINT = "/api/captiveportal/voucher/generate_vouchers"
VOUCHER_GENERATE_VOUCHERS_CAMELCASE_ENDPOINT = "/api/captiveportal/voucher/generateVouchers"


class VouchersMixin(AiopnsenseClientProtocol):
    """Captive portal voucher methods for OPNsenseClient."""

    async def generate_vouchers(self, data: MutableMapping[str, Any]) -> list[dict[str, Any]]:
        """Generate captive portal vouchers from a voucher server.

        Args:
            data (MutableMapping[str, Any]): Voucher generation options passed
                to OPNsense. Include ``voucher_server`` to choose a specific
                server when more than one provider exists.

        Returns:
            list[dict[str, Any]]: Generated vouchers with keys ordered for
                consumers and with ``expirytime`` converted to a timestamped
                datetime plus ``expiry_timestamp`` and human-readable
                ``validity_str`` when those values are available.
        """
        list_providers_endpoint = await self._get_endpoint_path(
            snake_case_path=VOUCHER_LIST_PROVIDERS_ENDPOINT,
            camel_case_path=VOUCHER_LIST_PROVIDERS_CAMELCASE_ENDPOINT,
        )
        generate_vouchers_endpoint = await self._get_endpoint_path(
            snake_case_path=VOUCHER_GENERATE_VOUCHERS_ENDPOINT,
            camel_case_path=VOUCHER_GENERATE_VOUCHERS_CAMELCASE_ENDPOINT,
        )
        if data.get("voucher_server", None):
            server = data.get("voucher_server")
        else:
            if not await self._is_get_endpoint_available(list_providers_endpoint):
                _LOGGER.debug("Voucher provider endpoint unavailable")
                return []
            servers = await self._safe_list_get(list_providers_endpoint)
            if len(servers) == 0:
                raise OPNsenseVoucherServerError("No voucher servers exist")
            if len(servers) != 1:
                raise OPNsenseVoucherServerError(
                    "More than one voucher server. Must specify voucher server name"
                )
            server = servers[0]
        server_slug = quote(str(server), safe="")
        payload: dict[str, Any] = dict(data)
        payload.pop("voucher_server", None)
        generate_vouchers_url = f"{generate_vouchers_endpoint}/{server_slug}/"
        _LOGGER.debug("[generate_vouchers] url: %s, payload: %s", generate_vouchers_url, payload)
        try:
            vouchers = await self._safe_list_post(
                generate_vouchers_url,
                payload=payload,
            )
        except aiohttp.ClientResponseError as err:
            if err.status == 404:
                _LOGGER.debug(
                    "Voucher generation endpoint unavailable: %s",
                    generate_vouchers_url,
                )
                return []
            raise
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
