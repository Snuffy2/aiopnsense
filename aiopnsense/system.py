"""System and configuration methods for OPNsenseClient."""

from collections.abc import MutableMapping
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
import warnings

import aiohttp
from dateutil.parser import ParserError, UnknownTimezoneWarning, parse

from ._typing import PyOPNsenseClientProtocol
from .const import AMBIGUOUS_TZINFOS
from .helpers import _LOGGER, _log_errors, timestamp_to_datetime, try_to_int


class SystemMixin(PyOPNsenseClientProtocol):
    """System methods for OPNsenseClient."""

    def _get_local_timezone(self) -> tzinfo:
        """Return a local timezone fallback with fixed UTC offset.

        Returns:
            tzinfo: Resolved timezone object for OPNsense system data.
        """
        return timezone(datetime.now().astimezone().utcoffset() or timedelta())

    async def _get_opnsense_timezone(self, datetime_str: str | None = None) -> tzinfo:
        """Resolve timezone information from OPNsense system time data.

        Args:
            datetime_str (str | None, optional): Datetime string parsed from API output.

        Returns:
            tzinfo: Resolved timezone object for OPNsense system data.
        """
        if datetime_str is None:
            try:
                datetime_raw = (
                    await self._safe_dict_get("/api/diagnostics/system/system_time")
                ).get("datetime")
            except (aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.debug(
                    "Failed to fetch OPNsense system time for timezone resolution: %s: %s",
                    type(err).__name__,
                    err,
                )
                return self._get_local_timezone()
            datetime_str = datetime_raw if isinstance(datetime_raw, str) else None

        if datetime_str:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", UnknownTimezoneWarning)
                    parsed_time = parse(datetime_str, tzinfos=AMBIGUOUS_TZINFOS)
                if parsed_time.tzinfo is not None:
                    return parsed_time.tzinfo
                _LOGGER.debug(
                    "No timezone data in OPNsense datetime '%s', using local fallback",
                    datetime_str,
                )
            except (ValueError, TypeError, ParserError, UnknownTimezoneWarning) as err:
                _LOGGER.debug(
                    "Failed to parse OPNsense timezone from datetime '%s': %s: %s",
                    datetime_str,
                    type(err).__name__,
                    err,
                )
        return self._get_local_timezone()

    @_log_errors
    async def get_device_unique_id(self, expected_id: str | None = None) -> str | None:
        """Get the OPNsense Unique ID.

        Args:
            expected_id (str | None, optional): Identifier for the related expected entry.

        Returns:
            str | None: Normalized data returned by the related OPNsense endpoint.
        """
        instances = await self._safe_list_get("/api/interfaces/overview/export")
        mac_addresses: set[str] = set()
        for item in instances:
            if not isinstance(item, MutableMapping):
                continue
            mac = item.get("macaddr_hw")
            if item.get("is_physical") and isinstance(mac, str) and mac:
                mac_addresses.add(mac.replace(":", "_").strip())

        if not mac_addresses:
            _LOGGER.debug("[get_device_unique_id] device_unique_id: None")
            return None

        if expected_id and expected_id in mac_addresses:
            _LOGGER.debug(
                "[get_device_unique_id] device_unique_id (matched expected): %s", expected_id
            )
            return expected_id

        device_unique_id = sorted(mac_addresses)[0]
        _LOGGER.debug("[get_device_unique_id] device_unique_id (first): %s", device_unique_id)
        return device_unique_id

    @_log_errors
    async def get_system_info(self) -> dict[str, Any]:
        """Return the system info from OPNsense.

        Returns:
            dict[str, Any]: Normalized data returned by the related OPNsense endpoint.
        """
        system_info: dict[str, Any] = {}
        response = await self._safe_dict_get("/api/diagnostics/system/system_information")
        system_info["name"] = response.get("name", None)
        return system_info

    @_log_errors
    async def get_carp_status(self) -> bool:
        """Return the Carp status.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        response = await self._safe_dict_get("/api/diagnostics/interface/get_vip_status")
        # _LOGGER.debug(f"[get_carp_status] response: {response}")
        return response.get("carp", {}).get("allow", "0") == "1"

    @_log_errors
    async def get_carp_interfaces(self) -> list:
        """Return the interfaces used by Carp.

        Returns:
            list: Normalized data returned by the related OPNsense endpoint.
        """
        vip_settings_raw = await self._safe_dict_get("/api/interfaces/vip_settings/get")
        if not isinstance(vip_settings_raw.get("rows", None), list):
            vip_settings: list = []
        else:
            vip_settings = vip_settings_raw.get("rows", [])
        # _LOGGER.debug(f"[get_carp_interfaces] vip_settings: {vip_settings}")

        vip_status_raw = await self._safe_dict_get("/api/diagnostics/interface/get_vip_status")
        if not isinstance(vip_status_raw.get("rows", None), list):
            vip_status: list = []
        else:
            vip_status = vip_status_raw.get("rows", [])
        # _LOGGER.debug(f"[get_carp_interfaces] vip_status: {vip_status}")
        carp = []
        for vip in vip_settings:
            if vip.get("mode", "").lower() != "carp":
                continue

            for status in vip_status:
                if vip.get("interface", "").lower() == status.get("interface", "_").lower():
                    vip["status"] = status.get("status", None)
                    break
            if "status" not in vip or not vip.get("status"):
                vip["status"] = "DISABLED"

            carp.append(vip)
        _LOGGER.debug("[get_carp_interfaces] carp: %s", carp)
        return carp

    @_log_errors
    async def system_reboot(self) -> bool:
        """Reboot OPNsense.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        response = await self._safe_dict_post("/api/core/system/reboot")
        _LOGGER.debug("[system_reboot] response: %s", response)
        if response.get("status", "") == "ok":
            return True
        return False

    @_log_errors
    async def system_halt(self) -> None:
        """Shutdown OPNsense."""
        response = await self._safe_dict_post("/api/core/system/halt")
        _LOGGER.debug("[system_halt] response: %s", response)
        if response.get("status", "") == "ok":
            return
        return

    @_log_errors
    async def send_wol(self, interface: str, mac: str) -> bool:
        """Send a wake on lan packet to the specified MAC address.

        Args:
            interface (str): Interface identifier to reload or inspect.
            mac (str): MAC address to use for Wake-on-LAN.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        payload: dict[str, Any] = {"wake": {"interface": interface, "mac": mac}}
        _LOGGER.debug("[send_wol] payload: %s", payload)
        response = await self._safe_dict_post("/api/wol/wol/set", payload)
        _LOGGER.debug("[send_wol] response: %s", response)
        if response.get("status", "") == "ok":
            return True
        return False

    @_log_errors
    async def get_notices(self) -> dict[str, Any]:
        """Get active OPNsense notices.

        Returns:
            dict[str, Any]: Normalized data returned by the related OPNsense endpoint.
        """
        notices_info = await self._safe_dict_get("/api/core/system/status")
        # _LOGGER.debug(f"[get_notices] notices_info: {notices_info}")
        pending_notices_present = False
        pending_notices: list = []
        for key, notice in notices_info.items():
            if isinstance(notice, MutableMapping) and notice.get("statusCode", 2) != 2:
                pending_notices_present = True
                pending_notices.append(
                    {
                        "notice": notice.get("message", None),
                        "id": key,
                        "created_at": timestamp_to_datetime(
                            try_to_int(notice.get("timestamp", None))
                        ),
                    }
                )

        return {
            "pending_notices_present": pending_notices_present,
            "pending_notices": pending_notices,
        }

    @_log_errors
    async def close_notice(self, id: str) -> bool:
        """Close selected notices.

        Args:
            id (str): Identifier of the notice to close.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        dismiss_endpoint = "/api/core/system/dismiss_status"

        # id = "all" to close all notices
        success = True
        if id.lower() == "all":
            notices = await self._safe_dict_get("/api/core/system/status")
            # _LOGGER.debug(f"[close_notice] notices: {notices}")
            for key, notice in notices.items():
                if not isinstance(notice, MutableMapping):
                    continue
                if notice.get("statusCode", 2) != 2:
                    dismiss = await self._safe_dict_post(dismiss_endpoint, payload={"subject": key})
                    # _LOGGER.debug(f"[close_notice] id: {key}, dismiss: {dismiss}")
                    if dismiss.get("status", "failed") != "ok":
                        success = False
        else:
            dismiss = await self._safe_dict_post(dismiss_endpoint, payload={"subject": id})
            _LOGGER.debug("[close_notice] id: %s, dismiss: %s", id, dismiss)
            if dismiss.get("status", "failed") != "ok":
                success = False
        _LOGGER.debug("[close_notice] success: %s", success)
        return success

    @_log_errors
    async def reload_interface(self, if_name: str) -> bool:
        """Reload the specified interface.

        Args:
            if_name (str): Interface name to select rows for.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        reload = await self._safe_dict_post(f"/api/interfaces/overview/reload_interface/{if_name}")
        return reload.get("message", "").startswith("OK")

    @_log_errors
    async def get_certificates(self) -> dict[str, Any]:
        """Return the active encryption certificates.

        Returns:
            dict[str, Any]: Normalized data returned by the related OPNsense endpoint.
        """
        certs_raw = await self._safe_dict_get("/api/trust/cert/search")
        cert_rows = certs_raw.get("rows")
        if not isinstance(cert_rows, list):
            return {}
        certs: dict[str, Any] = {}
        for cert in cert_rows:
            if cert.get("descr", None):
                certs[cert.get("descr")] = {
                    "uuid": cert.get("uuid", None),
                    "issuer": cert.get("caref", None),
                    "purpose": cert.get("rfc3280_purpose", None),
                    "in_use": bool(cert.get("in_use", "0") == "1"),
                    "valid_from": timestamp_to_datetime(try_to_int(cert.get("valid_from", None))),
                    "valid_to": timestamp_to_datetime(try_to_int(cert.get("valid_to", None))),
                }
        # _LOGGER.debug("[get_certificates] certs: %s", certs)
        _LOGGER.debug("[get_certificates] certs length: %s", len(certs))
        return certs
