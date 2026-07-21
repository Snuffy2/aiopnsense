"""Speedtest methods for OPNsenseClient."""

# Endpoints (GET):
# /api/speedtest/service/showstat
# /api/speedtest/service/showlog
# /api/speedtest/service/showrecent
# /api/speedtest/service/version
# /api/speedtest/service/serverlist
# /api/speedtest/service/run/[$serverid]
#
# Sources:
# https://github.com/mimugmail/opn-repo/blob/main/net-mgmt/speedtest-community/src/opnsense/mvc/app/controllers/OPNsense/Speedtest/Api/ServiceController.php
# https://github.com/mihakralj/opnsense-speedtest/blob/main/src/opnsense/mvc/app/controllers/OPNsense/Speedtest/Api/ServiceController.php

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from ._typing import AiopnsenseClientProtocol
from .helpers import _LOGGER, _log_errors, normalize_datetime, try_to_float, try_to_int

SPEEDTEST_SHOW_LOG_ENDPOINT = "/api/speedtest/service/showlog"
SPEEDTEST_SHOW_STAT_ENDPOINT = "/api/speedtest/service/showstat"
SPEEDTEST_RUN_ENDPOINT = "/api/speedtest/service/run"


class SpeedtestMixin(AiopnsenseClientProtocol):
    """Speedtest methods for OPNsenseClient."""

    @_log_errors
    async def get_speedtest(self) -> dict[str, Any]:
        """Return the latest and average Speedtest result summaries.

        Returns:
            dict[str, Any]: Mapping with ``available`` plus ``last`` and
                ``average`` sections for download, upload, and latency. Latest
                results include server, date, and URL metadata; averages
                include min, max, sample count, and period bounds. Returns
                ``{"available": False}`` when the plugin endpoint is missing.
        """
        if not await self._is_get_endpoint_available(SPEEDTEST_SHOW_LOG_ENDPOINT):
            _LOGGER.debug("Speedtest not installed")
            return {"available": False}

        latest_result = self._parse_showlog_latest(
            await self._safe_list_get(SPEEDTEST_SHOW_LOG_ENDPOINT)
        )
        if await self._is_get_endpoint_available(SPEEDTEST_SHOW_STAT_ENDPOINT):
            show_stat = await self._safe_dict_get(SPEEDTEST_SHOW_STAT_ENDPOINT)
        else:
            _LOGGER.debug("Speedtest statistics endpoint unavailable")
            show_stat = {}

        server_id = latest_result.get("server_id")
        if not isinstance(server_id, str):
            server_id = None
        server_name = latest_result.get("server")
        if not isinstance(server_name, str):
            server_name = None
        opnsense_tz = await self._get_opnsense_timezone()
        date = normalize_datetime(latest_result.get("date"), opnsense_tz)
        url = latest_result.get("url") if isinstance(latest_result.get("url"), str) else None

        samples = try_to_int(show_stat.get("samples"))
        period = show_stat.get("period", {})
        oldest = normalize_datetime(
            period.get("oldest") if isinstance(period, MutableMapping) else None,
            opnsense_tz,
        )
        youngest = normalize_datetime(
            period.get("youngest") if isinstance(period, MutableMapping) else None,
            opnsense_tz,
        )

        output: dict[str, Any] = {
            "available": True,
            "last": {},
            "average": {},
        }
        for metric in ("download", "upload", "latency"):
            recent_value = try_to_float(latest_result.get(metric))
            stat_metric = show_stat.get(metric, {})

            output["last"][metric] = {
                "value": recent_value,
                "date": date,
                "server_id": server_id,
                "server": server_name,
                "url": url,
            }
            output["average"][metric] = {
                "value": try_to_float(
                    stat_metric.get("avg") if isinstance(stat_metric, MutableMapping) else None
                ),
                "min": try_to_float(
                    stat_metric.get("min") if isinstance(stat_metric, MutableMapping) else None
                ),
                "max": try_to_float(
                    stat_metric.get("max") if isinstance(stat_metric, MutableMapping) else None
                ),
                "oldest": oldest,
                "youngest": youngest,
                "samples": samples,
            }
        return output

    def _parse_showlog_latest(self, show_log: object) -> dict[str, Any]:
        """Normalize the newest row returned by the Speedtest ``showlog`` endpoint.

        Args:
            show_log (object): Raw Speedtest history payload, ordered newest
                first.

        Returns:
            dict[str, Any]: Latest result using the legacy ``showrecent`` field
                names, or an empty mapping when no valid row is available.
        """
        if not isinstance(show_log, list) or not show_log:
            return {}
        latest = show_log[0]
        if not isinstance(latest, list) or len(latest) < 9:
            return {}

        raw_server_id = latest[2].strip() if isinstance(latest[2], str) else latest[2]
        if isinstance(raw_server_id, bool) or not isinstance(raw_server_id, int | str):
            server_id = None
        else:
            server_id = str(raw_server_id)
            if not server_id:
                server_id = None

        raw_server = latest[3].strip() if isinstance(latest[3], str) else latest[3]
        if not isinstance(raw_server, str):
            server = None
        else:
            server = raw_server or None
        return {
            "date": latest[0],
            "server_id": server_id,
            "server": server,
            "download": latest[5],
            "upload": latest[6],
            "latency": latest[7],
            "url": latest[8],
        }

    @_log_errors
    async def run_speedtest(self) -> dict[str, Any]:
        """Start a Speedtest run and return the raw run response.

        Returns:
            dict[str, Any]: Response mapping from the Speedtest ``run``
                endpoint, or an empty mapping when the plugin endpoint is
                unavailable or returns a malformed payload.
        """
        if not await self._is_get_endpoint_available(SPEEDTEST_SHOW_LOG_ENDPOINT):
            _LOGGER.debug("Speedtest not installed")
            return {}

        response = await self._safe_dict_get_with_timeout(
            SPEEDTEST_RUN_ENDPOINT,
            timeout_seconds=180,
        )
        if not isinstance(response, MutableMapping):
            return {}
        return dict(response)
