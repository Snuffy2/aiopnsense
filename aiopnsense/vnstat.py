"""vnStat collection and parsing methods for OPNsenseClient."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from datetime import date, datetime, timedelta, tzinfo
import re
from typing import Any

from ._typing import AiopnsenseClientProtocol
from .helpers import _LOGGER, _log_errors, normalize_lookup_token, try_to_float

_VSTAT_HEADER_RE = re.compile(
    r"^\s*(?P<interface>[^\s]+)\s*/\s*(?P<period>hourly|daily|monthly|yearly)\s*$",
    re.IGNORECASE,
)
_VSTAT_ROW_RE = re.compile(
    r"^\s*(?P<label>.+?)\s+"
    r"(?P<rx_value>[\d.]+)\s+(?P<rx_unit>[KMGTP]?i?B)\s+\|\s+"
    r"(?P<tx_value>[\d.]+)\s+(?P<tx_unit>[KMGTP]?i?B)\s+\|\s+"
    r"(?P<total_value>[\d.]+)\s+(?P<total_unit>[KMGTP]?i?B)\s+\|\s+"
    r"(?P<rate_value>[\d.]+)\s+(?P<rate_unit>[KMGTP]?bit/s)\s*$",
    re.IGNORECASE,
)
_VSTAT_HOURLY_DAY_RE = re.compile(r"^\d{2}/\d{2}/\d{2}$")
_VSTAT_HOURLY_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
_VSTAT_PERIODS: tuple[str, ...] = ("hourly", "daily", "monthly", "yearly")
_BYTE_FACTORS = {
    "B": 1,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
    "PIB": 1024**5,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "PB": 1000**5,
}
_RATE_FACTORS = {
    "BIT/S": 1,
    "KBIT/S": 1000,
    "MBIT/S": 1000**2,
    "GBIT/S": 1000**3,
    "TBIT/S": 1000**4,
    "PBIT/S": 1000**5,
}


class VnstatMixin(AiopnsenseClientProtocol):
    """vnStat methods for OPNsenseClient."""

    async def _fetch_vnstat_for(self, endpoint: str, expected_period: str) -> dict[str, Any]:
        """Fetch and parse vnStat payload for a specific endpoint and period.

        Args:
            endpoint (str): API endpoint path to request.
            expected_period (str): Expected period label for parser validation.

        Returns:
            dict[str, Any]: Parsed payload or fallback empty mapping when endpoint is unavailable.
        """
        if not await self.is_endpoint_available(endpoint):
            _LOGGER.debug("vnStat %s endpoint unavailable", expected_period)
            return {"period": expected_period, "interfaces": {}}

        return self._parse_vnstat_payload(
            await self._safe_dict_get(endpoint),
            expected_period=expected_period,
        )

    @_log_errors
    async def get_vnstat_metrics(self, period: str) -> dict[str, Any]:
        """Return parsed vnStat traffic rows for one supported reporting period.

        Args:
            period (str): Requested vnStat period identifier. Supported values
                are ``hourly``, ``daily``, ``monthly``, and ``yearly``.

        Returns:
            dict[str, Any]: Parsed vnStat payload containing the selected
                ``period`` and an ``interfaces`` mapping of interface names to
                traffic rows. Returns an empty mapping for unsupported periods
                or when no interface rows are available.
        """
        requested_period = normalize_lookup_token(period)
        if requested_period not in _VSTAT_PERIODS:
            return {}

        endpoint = f"/api/vnstat/service/{requested_period}"
        payload = await self._fetch_vnstat_for(endpoint, requested_period)
        if not payload.get("interfaces"):
            return {}
        return payload

    @_log_errors
    async def get_vnstat(self) -> MutableMapping[str, Any]:
        """Collect summarized vnStat usage data across available interfaces.

        Returns:
            MutableMapping[str, Any]: Mapping with ``interfaces`` keyed by
                interface name and ``interface_count``. Each interface contains
                parsed ``hourly``, ``daily``, and ``monthly`` rows plus
                convenience byte counters for today, this month, yesterday,
                last month, and the last complete hour.
        """
        if not await self.is_endpoint_available("/api/vnstat/service/hourly"):
            _LOGGER.debug("vnStat not installed")
            return {"interfaces": {}, "interface_count": 0}

        opnsense_tz = await self._get_opnsense_timezone()
        hourly = self._parse_vnstat_payload(
            await self._safe_dict_get("/api/vnstat/service/hourly"),
            expected_period="hourly",
        )
        daily = await self._fetch_vnstat_for("/api/vnstat/service/daily", "daily")
        monthly = await self._fetch_vnstat_for("/api/vnstat/service/monthly", "monthly")
        interface_names = self._collect_vnstat_interfaces(hourly, daily, monthly)
        interface_data: dict[str, Any] = {}

        for interface in interface_names:
            rows_hourly = self._interface_rows(hourly, interface)
            rows_daily = self._interface_rows(daily, interface)
            rows_monthly = self._interface_rows(monthly, interface)
            selected_rows = {
                "vnstat_today": self._pick_daily_row(
                    rows_daily, days_ago=0, current_tz=opnsense_tz
                ),
                "vnstat_this_month": self._pick_monthly_row(
                    rows_monthly, months_ago=0, current_tz=opnsense_tz
                ),
                "vnstat_yesterday": self._pick_daily_row(
                    rows_daily, days_ago=1, current_tz=opnsense_tz
                ),
                "vnstat_last_month": self._pick_monthly_row(
                    rows_monthly, months_ago=1, current_tz=opnsense_tz
                ),
                "vnstat_last_hour": self._pick_last_hour_row(rows_hourly, current_tz=opnsense_tz),
            }
            metrics: dict[str, dict[str, int | None]] = {}
            for metric_name, metric_row in selected_rows.items():
                metric_values = self._metric_values(metric_row)
                metrics[metric_name] = {
                    "total_bytes": metric_values["total_bytes"] if metric_values else None,
                    "rx_bytes": metric_values["rx_bytes"] if metric_values else None,
                    "tx_bytes": metric_values["tx_bytes"] if metric_values else None,
                }

            interface_data[interface] = {
                "hourly": rows_hourly,
                "daily": rows_daily,
                "monthly": rows_monthly,
                "metrics": metrics,
            }
        return {
            "interfaces": interface_data,
            "interface_count": len(interface_names),
        }

    def _parse_vnstat_payload(
        self, payload: MutableMapping[str, Any], expected_period: str
    ) -> dict[str, Any]:
        """Parse a vnStat endpoint payload into normalized rows.

        Args:
            payload (MutableMapping[str, Any]): Raw vnStat API payload whose
                ``response`` field contains the textual vnStat table.
            expected_period (str): Period label expected in the table header.

        Returns:
            dict[str, Any]: Mapping with the parsed ``period`` and an
                ``interfaces`` dictionary of interface names to normalized row
                dictionaries.
        """
        response_text = payload.get("response", "")
        if not isinstance(response_text, str):
            return {"period": expected_period, "interfaces": {}}

        parsed_period = expected_period
        current_interface: str | None = None
        current_hourly_day: str | None = None
        interfaces: dict[str, list[dict[str, Any]]] = {}
        for line in response_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            header_match = _VSTAT_HEADER_RE.match(stripped)
            if header_match:
                current_interface = header_match.group("interface")
                parsed_period = header_match.group("period").lower()
                interfaces.setdefault(current_interface, [])
                current_hourly_day = None
                continue
            if (
                expected_period == "hourly"
                and current_interface
                and _VSTAT_HOURLY_DAY_RE.match(stripped)
            ):
                current_hourly_day = stripped
                continue
            if current_interface is None:
                continue
            row = self._parse_vnstat_row(stripped)
            if row is not None:
                if expected_period == "hourly":
                    row_label = row.get("label")
                    if (
                        isinstance(row_label, str)
                        and _VSTAT_HOURLY_TIME_RE.match(row_label)
                        and current_hourly_day
                    ):
                        row["hour"] = row_label
                        row["day"] = current_hourly_day
                        row["label"] = f"{current_hourly_day} {row_label}"
                interfaces[current_interface].append(row)

        if parsed_period != expected_period:
            _LOGGER.debug(
                "vnStat period mismatch. expected=%s parsed=%s",
                expected_period,
                parsed_period,
            )

        return {
            "period": parsed_period,
            "interfaces": interfaces,
        }

    def _parse_vnstat_row(self, line: str) -> dict[str, Any] | None:
        """Parse a single vnStat data row from fixed-width text output.

        Args:
            line (str): Single table row from vnStat output.

        Returns:
            dict[str, Any] | None: Normalized row containing ``label``,
                ``rx_bytes``, ``tx_bytes``, ``total_bytes``, and
                ``avg_rate_bits_per_second``. Returns ``None`` for separators,
                estimated rows, and malformed values.
        """
        lowered = line.lower()
        if lowered.startswith(("-", "estimated")):
            return None

        match = _VSTAT_ROW_RE.match(line)
        if not match:
            return None

        rx_bytes = self._to_bytes(match.group("rx_value"), match.group("rx_unit"))
        tx_bytes = self._to_bytes(match.group("tx_value"), match.group("tx_unit"))
        total_bytes = self._to_bytes(match.group("total_value"), match.group("total_unit"))
        avg_rate = self._to_bits_per_second(match.group("rate_value"), match.group("rate_unit"))

        if rx_bytes is None or tx_bytes is None or total_bytes is None or avg_rate is None:
            return None

        return {
            "label": match.group("label").strip(),
            "rx_bytes": rx_bytes,
            "tx_bytes": tx_bytes,
            "total_bytes": total_bytes,
            "avg_rate_bits_per_second": avg_rate,
        }

    def _to_bytes(self, value: str, unit: str) -> int | None:
        """Convert vnStat byte strings into integer bytes.

        Args:
            value (str): Numeric byte value parsed from vnStat text.
            unit (str): Byte unit suffix such as ``B``, ``KiB``, ``MiB``, or
                ``GB``.

        Returns:
            int | None: Converted byte count, or ``None`` when the value or
                unit cannot be parsed.
        """
        parsed_value = try_to_float(value)
        factor = _BYTE_FACTORS.get(unit.upper())
        if parsed_value is None or factor is None:
            return None
        return int(round(parsed_value * factor))

    def _to_bits_per_second(self, value: str, unit: str) -> int | None:
        """Convert vnStat rate strings into integer bits-per-second.

        Args:
            value (str): Numeric rate value parsed from vnStat text.
            unit (str): Rate unit suffix such as ``bit/s``, ``kbit/s``, or
                ``Mbit/s``.

        Returns:
            int | None: Converted bits-per-second value, or ``None`` when the
                value or unit cannot be parsed.
        """
        parsed_value = try_to_float(value)
        factor = _RATE_FACTORS.get(unit.upper())
        if parsed_value is None or factor is None:
            return None
        return int(round(parsed_value * factor))

    def _pick_daily_row(
        self, rows: Sequence[dict[str, Any]], days_ago: int, current_tz: tzinfo
    ) -> dict[str, Any] | None:
        """Select a daily row by matching day label or falling back by position.

        Args:
            rows (Sequence[dict[str, Any]]): Collection of parsed table rows.
            days_ago (int): Day offset used for fallback selection.
            current_tz (tzinfo): Timezone used to determine the current date.

        Returns:
            dict[str, Any] | None: Daily row matching ``days_ago`` in
                ``current_tz``. Falls back to the latest row for today and the
                second-latest row for yesterday when labels cannot be parsed.
        """
        target_day = datetime.now(tz=current_tz).date() - timedelta(days=days_ago)
        for row in rows:
            parsed_day = self._parse_daily_label(row.get("label"))
            if parsed_day == target_day:
                return row
        if days_ago == 0 and rows:
            return rows[-1]
        if days_ago == 1 and len(rows) >= 2:
            return rows[-2]
        return None

    def _pick_monthly_row(
        self, rows: Sequence[dict[str, Any]], months_ago: int, current_tz: tzinfo
    ) -> dict[str, Any] | None:
        """Select a monthly row by matching month label or fallback position.

        Args:
            rows (Sequence[dict[str, Any]]): Collection of parsed table rows.
            months_ago (int): Month offset used for fallback selection.
            current_tz (tzinfo): Timezone used to determine the current month.

        Returns:
            dict[str, Any] | None: Monthly row matching ``months_ago`` in
                ``current_tz``. Falls back to the latest row for this month and
                the second-latest row for last month when labels cannot be
                parsed.
        """
        now = datetime.now(tz=current_tz).date()
        target_year = now.year
        target_month = now.month - months_ago
        while target_month <= 0:
            target_month += 12
            target_year -= 1

        for row in rows:
            parsed_month = self._parse_month_label(row.get("label"))
            if parsed_month == (target_year, target_month):
                return row
        if months_ago == 0 and rows:
            return rows[-1]
        if months_ago == 1 and len(rows) >= 2:
            return rows[-2]
        return None

    def _pick_last_hour_row(
        self, rows: Sequence[dict[str, Any]], current_tz: tzinfo
    ) -> dict[str, Any] | None:
        """Select the last complete hour row from parsed hourly rows.

        Args:
            rows (Sequence[dict[str, Any]]): Collection of parsed table rows.
            current_tz (tzinfo): Timezone used to determine the current and
                previous hour.

        Returns:
            dict[str, Any] | None: Row for the last complete hour, or the most
                recent completed row when exact timestamp matching is
                unavailable.
        """
        now = datetime.now(tz=current_tz)
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        target_hour = current_hour - timedelta(hours=1)
        for row in rows:
            parsed_hour = self._parse_hourly_label(row.get("label"), current_tz)
            if parsed_hour == target_hour:
                return row
        if rows:
            latest_row_hour = self._parse_hourly_label(rows[-1].get("label"), current_tz)
            if latest_row_hour == current_hour and len(rows) >= 2:
                return rows[-2]
            return rows[-1]
        return None

    def _metric_values(self, row: Mapping[str, Any] | None) -> dict[str, int] | None:
        """Extract metric values from a parsed row.

        Args:
            row (Mapping[str, Any] | None): Parsed vnStat row to extract byte
                counters from.

        Returns:
            dict[str, int] | None: Mapping with integer ``total_bytes``,
                ``rx_bytes``, and ``tx_bytes`` counters, or ``None`` when the
                row is missing any required counter.
        """
        if not isinstance(row, Mapping):
            return None
        total = row.get("total_bytes")
        rx = row.get("rx_bytes")
        tx = row.get("tx_bytes")
        if isinstance(total, int) and isinstance(rx, int) and isinstance(tx, int):
            return {"total_bytes": total, "rx_bytes": rx, "tx_bytes": tx}
        return None

    def _collect_vnstat_interfaces(
        self, *payloads: Mapping[str, Any] | MutableMapping[str, Any]
    ) -> list[str]:
        """Collect interface names present across parsed vnStat payloads.

        Args:
            *payloads (Mapping[str, Any] | MutableMapping[str, Any]): Parsed
                vnStat payload mappings whose ``interfaces`` keys should be
                merged.

        Returns:
            list[str]: Sorted unique interface names found across all supplied
                payloads.
        """
        interfaces: set[str] = set()
        for payload in payloads:
            by_interface = payload.get("interfaces", {})
            if not isinstance(by_interface, Mapping):
                continue
            for interface in by_interface:
                if isinstance(interface, str):
                    interfaces.add(interface)
        return sorted(interfaces)

    def _interface_rows(self, payload: Mapping[str, Any], interface: str) -> list[dict[str, Any]]:
        """Return parsed rows for a specific interface from a payload.

        Args:
            payload (Mapping[str, Any]): Parsed vnStat payload containing an
                ``interfaces`` mapping.
            interface (str): Interface name whose rows should be returned.

        Returns:
            list[dict[str, Any]]: Parsed rows for a specific interface from a payload.
        """
        by_interface = payload.get("interfaces", {})
        if not isinstance(by_interface, Mapping):
            return []
        rows = by_interface.get(interface)
        return rows if isinstance(rows, list) else []

    def _parse_daily_label(self, label: Any) -> date | None:
        """Parse daily row labels into ``date`` values.

        Args:
            label (Any): Daily row label parsed from vnStat output.

        Returns:
            date | None: Parsed date for ``MM/DD/YY`` or ``YYYY-MM-DD`` labels,
                or ``None`` when the label is not recognized.
        """
        if not isinstance(label, str):
            return None
        for fmt in ("%m/%d/%y", "%Y-%m-%d"):
            try:
                return datetime.strptime(label, fmt).date()
            except ValueError:
                continue
        return None

    def _parse_month_label(self, label: Any) -> tuple[int, int] | None:
        """Parse monthly row labels into year/month tuples.

        Args:
            label (Any): Monthly row label parsed from vnStat output.

        Returns:
            tuple[int, int] | None: ``(year, month)`` parsed from labels such
                as ``YYYY-MM``, ``Jan '26``, or ``January '26``. Returns
                ``None`` for unrecognized labels.
        """
        if not isinstance(label, str):
            return None
        for fmt in ("%Y-%m", "%b '%y", "%B '%y"):
            try:
                parsed = datetime.strptime(label, fmt)
            except ValueError:
                continue
            else:
                return parsed.year, parsed.month
        return None

    def _parse_hourly_label(self, label: Any, current_tz: tzinfo) -> datetime | None:
        """Parse hourly row labels into minute-precision datetimes.

        Args:
            label (Any): Hourly row label parsed from vnStat output.
            current_tz (tzinfo): Timezone assigned to parsed hourly labels.

        Returns:
            datetime | None: Parsed hour timestamp for ``MM/DD/YY HH:MM`` or
                ``YYYY-MM-DD HH:MM`` labels, or ``None`` when parsing fails.
        """
        if not isinstance(label, str):
            return None
        for fmt in ("%m/%d/%y %H:%M", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(label, fmt).replace(tzinfo=current_tz)
            except ValueError:
                continue
        return None
