"""Telemetry and interface statistics methods for OPNsenseClient."""

from collections.abc import MutableMapping
from datetime import datetime, timedelta
import re
from typing import Any

from dateutil.parser import ParserError, UnknownTimezoneWarning, parse

from ._typing import AiopnsenseClientProtocol
from .const import AMBIGUOUS_TZINFOS
from .helpers import _LOGGER, _log_errors, dict_get, try_to_float, try_to_int


class TelemetryMixin(AiopnsenseClientProtocol):
    """Telemetry methods for OPNsenseClient."""

    @staticmethod
    def _usage_percent(
        used: int | None,
        total: int | None,
        default: int | None = None,
    ) -> int | None:
        """Calculate a rounded usage percentage for valid integer counters.

        Args:
            used (int | None): Used amount.
            total (int | None): Total capacity.
            default (int | None, optional): Value returned for invalid or zero totals.

        Returns:
            int | None: Rounded percentage, or ``default`` when counters are invalid.
        """
        if isinstance(used, int) and isinstance(total, int) and total > 0:
            return round(used / total * 100)
        return default

    @_log_errors
    async def get_telemetry(self) -> MutableMapping[str, Any]:
        """Return consolidated system telemetry from OPNsense.

        Returns:
            MutableMapping[str, Any]: Mapping containing ``mbuf``,
                ``pfstate``, ``memory``, ``system``, ``cpu``,
                ``filesystems``, and ``temps`` sections populated from the
                corresponding diagnostics endpoints.
        """
        telemetry: dict[str, Any] = {}
        telemetry["mbuf"] = await self._get_telemetry_mbuf()
        telemetry["pfstate"] = await self._get_telemetry_pfstate()
        telemetry["memory"] = await self._get_telemetry_memory()
        telemetry["system"] = await self._get_telemetry_system()
        telemetry["cpu"] = await self._get_telemetry_cpu()
        telemetry["filesystems"] = await self._get_telemetry_filesystems()
        telemetry["temps"] = await self._get_telemetry_temps()
        return telemetry

    @_log_errors
    async def get_interfaces(self) -> MutableMapping[str, Any]:
        """Return normalized interface status and counters.

        Returns:
            MutableMapping[str, Any]: Mapping keyed by OPNsense interface
                identifier. Each interface includes packet and byte counters,
                error counters, status, addresses, media, gateway and route
                metadata, MAC address, enabled flag, and VLAN tag when
                available.
        """
        interfaces_endpoint = "/api/interfaces/overview/export"
        if not await self.is_endpoint_available(interfaces_endpoint):
            _LOGGER.debug("Interface overview endpoint unavailable")
            return {}
        interface_info = await self._safe_list_get(interfaces_endpoint)
        if not len(interface_info) > 0:
            return {}
        interfaces: dict[str, Any] = {}
        for ifinfo in interface_info:
            interface: dict[str, Any] = {}
            if not isinstance(ifinfo, MutableMapping) or ifinfo.get("identifier", "") == "":
                continue
            statistics = ifinfo.get("statistics", {})
            if not isinstance(statistics, MutableMapping):
                statistics = {}
            packets_received = try_to_int(statistics.get("packets received"))
            packets_transmitted = try_to_int(statistics.get("packets transmitted"))
            bytes_received = try_to_int(statistics.get("bytes received"))
            bytes_transmitted = try_to_int(statistics.get("bytes transmitted"))
            interface["inpkts"] = packets_received
            interface["outpkts"] = packets_transmitted
            interface["inbytes"] = bytes_received
            interface["outbytes"] = bytes_transmitted
            interface["inbytes_frmt"] = bytes_received
            interface["outbytes_frmt"] = bytes_transmitted
            interface["inerrs"] = try_to_int(statistics.get("input errors"))
            interface["outerrs"] = try_to_int(statistics.get("output errors"))
            interface["collisions"] = try_to_int(statistics.get("collisions"))
            interface["interface"] = ifinfo.get("identifier", "")
            interface["name"] = ifinfo.get("description", "")
            interface["status"] = ""
            if ifinfo.get("status", "") in {"down", "no carrier", "up"}:
                interface["status"] = ifinfo.get("status", "")
            elif ifinfo.get("status", "") == "associated":
                interface["status"] = "up"
            interface["ipv4"] = ifinfo.get("addr4", None)
            interface["ipv6"] = ifinfo.get("addr6", None)
            interface["media"] = ifinfo.get("media", None)
            interface["gateways"] = ifinfo.get("gateways", [])
            interface["routes"] = ifinfo.get("routes", [])
            interface["device"] = ifinfo.get("device", None)
            if ifinfo.get("macaddr", None) and ifinfo.get("macaddr", None) != "00:00:00:00:00:00":
                interface["mac"] = ifinfo.get("macaddr", None)
            interface["enabled"] = ifinfo.get("enabled", None)
            interface["vlan_tag"] = ifinfo.get("vlan_tag", None)
            interfaces[ifinfo.get("identifier", "")] = interface
        return interfaces

    @_log_errors
    async def _get_telemetry_mbuf(self) -> MutableMapping[str, Any]:
        """Collect mbuf usage telemetry.

        Returns:
            MutableMapping[str, Any]: Mapping containing current and total mbuf
                counts plus ``used_percent``.
        """
        mbuf_endpoint = "/api/diagnostics/system/system_mbuf"
        if not await self.is_endpoint_available(mbuf_endpoint):
            _LOGGER.debug("Telemetry mbuf endpoint unavailable")
            return {}
        mbuf_info = await self._safe_dict_get(mbuf_endpoint)
        mbuf: dict[str, Any] = {}
        mbuf["used"] = try_to_int(dict_get(mbuf_info, "mbuf-statistics.mbuf-current"))
        mbuf["total"] = try_to_int(dict_get(mbuf_info, "mbuf-statistics.mbuf-total"))
        mbuf["used_percent"] = self._usage_percent(mbuf["used"], mbuf["total"])
        return mbuf

    @_log_errors
    async def _get_telemetry_pfstate(self) -> MutableMapping[str, Any]:
        """Collect PF state table telemetry.

        Returns:
            MutableMapping[str, Any]: Mapping containing current and maximum PF
                state counts plus ``used_percent``.
        """
        pfstate_endpoint = "/api/diagnostics/firewall/pf_states"
        if not await self.is_endpoint_available(pfstate_endpoint):
            _LOGGER.debug("Telemetry pfstate endpoint unavailable")
            return {}
        pfstate_info = await self._safe_dict_get(pfstate_endpoint)
        pfstate: dict[str, Any] = {}
        pfstate["used"] = try_to_int(pfstate_info.get("current", None))
        pfstate["total"] = try_to_int(pfstate_info.get("limit", None))
        pfstate["used_percent"] = self._usage_percent(pfstate["used"], pfstate["total"])
        return pfstate

    @_log_errors
    async def _get_telemetry_memory(self) -> MutableMapping[str, Any]:
        """Collect memory and swap telemetry.

        Returns:
            MutableMapping[str, Any]: Mapping containing physical memory,
                memory used, and ``used_percent``. Adds swap totals and usage
                percentage when swap telemetry is available.
        """
        memory_endpoint = await self._get_endpoint_path(
            snake_case_path="/api/diagnostics/system/system_resources",
            camel_case_path="/api/diagnostics/system/systemResources",
        )
        if not await self.is_endpoint_available(memory_endpoint):
            _LOGGER.debug("Telemetry memory endpoint unavailable")
            return {
                "physmem": None,
                "used": None,
                "used_percent": None,
            }
        memory_info = await self._safe_dict_get(memory_endpoint)
        memory: dict[str, Any] = {}
        memory["physmem"] = try_to_int(dict_get(memory_info, "memory.total"))
        memory["used"] = try_to_int(dict_get(memory_info, "memory.used"))
        memory["used_percent"] = self._usage_percent(memory["used"], memory["physmem"])
        swap_endpoint = "/api/diagnostics/system/system_swap"
        if not await self.is_endpoint_available(swap_endpoint):
            _LOGGER.debug("Telemetry swap endpoint unavailable")
            return memory

        swap_info = await self._safe_dict_get(swap_endpoint)
        swap_rows = swap_info.get("swap")
        if not isinstance(swap_rows, list) or not swap_rows:
            return memory
        swap_row = swap_rows[0]
        if not isinstance(swap_row, MutableMapping):
            return memory
        memory["swap_total"] = try_to_int(swap_row.get("total"))
        memory["swap_reserved"] = try_to_int(swap_row.get("used"))
        memory["swap_used_percent"] = self._usage_percent(
            memory["swap_reserved"], memory["swap_total"], default=0
        )
        return memory

    @_log_errors
    async def _get_telemetry_system(self) -> MutableMapping[str, Any]:
        """Collect system time, uptime, boottime, and load telemetry.

        Returns:
            MutableMapping[str, Any]: Mapping containing boot time, uptime, and
                one-, five-, and fifteen-minute load averages when parseable.
        """
        time_endpoint = await self._get_endpoint_path(
            snake_case_path="/api/diagnostics/system/system_time",
            camel_case_path="/api/diagnostics/system/systemTime",
        )
        if not await self.is_endpoint_available(time_endpoint):
            _LOGGER.debug("Telemetry system time endpoint unavailable")
            return {}
        time_info = await self._safe_dict_get(time_endpoint)
        system: dict[str, Any] = {}
        opnsense_tz = await self._get_opnsense_timezone(time_info.get("datetime"))

        try:
            systemtime: datetime = parse(time_info["datetime"], tzinfos=AMBIGUOUS_TZINFOS)
            if systemtime.tzinfo is None:
                systemtime = systemtime.replace(tzinfo=opnsense_tz)
        except (KeyError, ValueError, TypeError, ParserError, UnknownTimezoneWarning) as e:
            _LOGGER.warning(
                "Failed to parse opnsense system time (aka. datetime), using HA system time instead: %s. %s: %s",
                time_info.get("datetime"),
                type(e).__name__,
                e,
            )
            systemtime = datetime.now().astimezone()

        pattern = re.compile(r"^(?:(\d+)\s+days?,\s+)?(\d{2}):(\d{2}):(\d{2})$")
        match = pattern.match(time_info.get("uptime", ""))
        if match:
            days_str, hours_str, minutes_str, seconds_str = match.groups()
            days = try_to_int(days_str, 0) or 0
            hours = try_to_int(hours_str, 0) or 0
            minutes = try_to_int(minutes_str, 0) or 0
            seconds = try_to_int(seconds_str, 0) or 0

            uptime = days * 86400 + hours * 3600 + minutes * 60 + seconds

        boottime: datetime | None = None
        if "boottime" in time_info:
            try:
                boottime = parse(time_info["boottime"], tzinfos=AMBIGUOUS_TZINFOS)
                if boottime and boottime.tzinfo is None:
                    boottime = boottime.replace(tzinfo=opnsense_tz)
            except (ValueError, TypeError, ParserError, UnknownTimezoneWarning) as e:
                _LOGGER.info(
                    "Failed to parse opnsense boottime: %s. %s: %s",
                    time_info["boottime"],
                    type(e).__name__,
                    e,
                )

        if boottime:
            system["boottime"] = boottime.timestamp()
            if match:
                system["uptime"] = uptime
            else:
                system["uptime"] = int((systemtime - boottime).total_seconds())
        elif match:
            system["uptime"] = uptime
            boottime = systemtime - timedelta(seconds=system["uptime"])
            system["boottime"] = boottime.timestamp()
        else:
            _LOGGER.warning("Invalid uptime format")

        load_str: str = time_info.get("loadavg", "")
        load_list: list[str] = load_str.split(", ")
        if len(load_list) == 3:
            system["load_average"] = {
                "one_minute": try_to_float(load_list[0]),
                "five_minute": try_to_float(load_list[1]),
                "fifteen_minute": try_to_float(load_list[2]),
            }
        else:
            system["load_average"] = {
                "one_minute": None,
                "five_minute": None,
                "fifteen_minute": None,
            }
        return system

    @_log_errors
    async def _get_telemetry_cpu(self) -> MutableMapping[str, Any]:
        """Collect CPU core count and usage telemetry.

        Returns:
            MutableMapping[str, Any]: Mapping containing CPU core count and
                total/user/nice/system/interrupt/idle usage percentages when
                available.
        """
        cpu_type_endpoint = await self._get_endpoint_path(
            snake_case_path="/api/diagnostics/cpu_usage/get_c_p_u_type",
            camel_case_path="/api/diagnostics/cpu_usage/getCPUType",
        )
        if not await self.is_endpoint_available(cpu_type_endpoint):
            _LOGGER.debug("Telemetry CPU type endpoint unavailable")
            return {}
        cputype_info = await self._safe_list_get(cpu_type_endpoint)
        if not len(cputype_info) > 0:
            return {}
        cpu: dict[str, Any] = {}
        cores_match = re.search(r"\((\d+) cores", cputype_info[0])
        cpu["count"] = try_to_int(cores_match.group(1)) if cores_match else 0

        cpu_stream_endpoint = "/api/diagnostics/cpu_usage/stream"
        if not await self.is_endpoint_available(cpu_stream_endpoint):
            _LOGGER.debug("Telemetry CPU stream endpoint unavailable")
            return cpu
        cpustream_info = await self._get_from_stream(cpu_stream_endpoint)
        # {"total":29,"user":2,"nice":0,"sys":27,"intr":0,"idle":70}
        cpu["usage_total"] = try_to_int(cpustream_info.get("total", None))
        cpu["usage_user"] = try_to_int(cpustream_info.get("user", None))
        cpu["usage_nice"] = try_to_int(cpustream_info.get("nice", None))
        cpu["usage_system"] = try_to_int(cpustream_info.get("sys", None))
        cpu["usage_interrupt"] = try_to_int(cpustream_info.get("intr", None))
        cpu["usage_idle"] = try_to_int(cpustream_info.get("idle", None))
        return cpu

    @_log_errors
    async def _get_telemetry_filesystems(self) -> list:
        """Collect filesystem telemetry entries from diagnostics.

        Returns:
            list: Filesystem device rows from OPNsense disk diagnostics, or an
                empty list when the endpoint is unavailable.
        """
        filesystems_endpoint = await self._get_endpoint_path(
            snake_case_path="/api/diagnostics/system/system_disk",
            camel_case_path="/api/diagnostics/system/systemDisk",
        )
        if not await self.is_endpoint_available(filesystems_endpoint):
            _LOGGER.debug("Telemetry filesystem endpoint unavailable")
            return []
        filesystems_info = await self._safe_dict_get(filesystems_endpoint)
        filesystems: list = filesystems_info.get("devices", [])
        return filesystems

    @_log_errors
    async def get_gateways(self) -> MutableMapping[str, Any]:
        """Return OPNsense gateway status details.

        Returns:
            MutableMapping[str, Any]: Mapping keyed by gateway name, with each
                gateway row preserved from OPNsense and ``status`` normalized
                from the translated status when available.
        """
        gateway_endpoint = "/api/routes/gateway/status"
        if not await self.is_endpoint_available(gateway_endpoint):
            _LOGGER.debug("Gateway status endpoint unavailable")
            return {}
        gateways_info = await self._safe_dict_get(gateway_endpoint)
        gateways: dict[str, Any] = {}
        for gw_info in gateways_info.get("items", []):
            if isinstance(gw_info, MutableMapping) and "name" in gw_info:
                gateways[gw_info["name"]] = gw_info
        for gateway in gateways.values():
            gateway["status"] = gateway.pop("status_translated", gateway.get("status", "")).lower()
        return gateways

    @_log_errors
    async def _get_telemetry_temps(self) -> MutableMapping[str, Any]:
        """Collect temperature sensor telemetry.

        Returns:
            MutableMapping[str, Any]: Mapping keyed by temperature device id,
                with each value containing the numeric temperature, display
                name, and original device id.
        """
        temperature_endpoint = await self._get_endpoint_path(
            snake_case_path="/api/diagnostics/system/system_temperature",
            camel_case_path="/api/diagnostics/system/systemTemperature",
        )
        if not await self.is_endpoint_available(temperature_endpoint):
            _LOGGER.debug("Telemetry temperature endpoint unavailable")
            return {}
        temps_info = await self._safe_list_get(temperature_endpoint)
        if not len(temps_info) > 0:
            return {}
        temps: dict[str, Any] = {}
        for i, temp_info in enumerate(temps_info):
            temp: dict[str, Any] = {}
            temp["temperature"] = try_to_float(temp_info.get("temperature", 0), 0)
            temp["name"] = (
                f"{temp_info.get('type_translated', 'Num')} {temp_info.get('device_seq', i)}"
            )
            temp["device_id"] = temp_info.get("device", str(i))
            temps[temp_info.get("device", str(i)).replace(".", "_")] = temp
        return temps
