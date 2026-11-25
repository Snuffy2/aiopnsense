"""Unbound DNS blocklist, Query Stats and Query Log methods for OPNsenseClient."""

from collections.abc import Mapping, MutableMapping
import contextlib
from dataclasses import dataclass
from datetime import datetime
import logging
import math
from typing import Any

from ._error_policy import _log_boolean_errors, _log_errors, _throws_errors
from ._styled_endpoint import _StyledEndpoint
from ._typing import AiopnsenseClientProtocol
from .client_toggle import ToggleSpec, ToggleStep, ToggleTarget
from .const import EXTENDED_UNBOUND_BLOCKLIST_CAPABILITY
from .helpers import (
    _LOGGER,
    api_value_matches,
    dict_get,
    timestamp_to_datetime,
    try_to_float,
    try_to_int,
)

QUERY_LOG_SIZE = 1000
TOP_DOMAINS_SIZE = 10

UNBOUND_SETTINGS_GET_ENDPOINT = "/api/unbound/settings/get"
UNBOUND_SETTINGS_SET_ENDPOINT = "/api/unbound/settings/set"
UNBOUND_SERVICE_DNSBL_ENDPOINT = "/api/unbound/service/dnsbl"
UNBOUND_SERVICE_RESTART_ENDPOINT = "/api/unbound/service/restart"
UNBOUND_SETTINGS_SEARCH_DNSBL_ENDPOINT = "/api/unbound/settings/search_dnsbl"
UNBOUND_SETTINGS_TOGGLE_DNSBL_ENDPOINT_PREFIX = "/api/unbound/settings/toggle_dnsbl/"
UNBOUND_OVERVIEW_ROLLING_HOUR_ENDPOINT = "/api/unbound/overview/rolling/1"
UNBOUND_OVERVIEW_ROLLING_DAY_ENDPOINT = "/api/unbound/overview/rolling/24"
UNBOUND_OVERVIEW_TOTALS_ENDPOINT = f"/api/unbound/overview/totals/{TOP_DOMAINS_SIZE}"
UNBOUND_OVERVIEW_SEARCH_QUERIES_ENDPOINT = _StyledEndpoint(
    snake_case="/api/unbound/overview/search_queries",
    camel_case="/api/unbound/overview/searchQueries",
)


def _optional_text(value: object) -> str | None:
    """Return a Router field as text, or None when it is absent or empty.

    Args:
        value (object): Field value from the Router.

    Returns:
        str | None: The value as text, or None when it is falsy.
    """
    return str(value) if value else None


def _domain_name(value: object) -> str:
    """Return a domain from the Router without its trailing dot.

    Args:
        value (object): Fully qualified domain as the Router reports it.

    Returns:
        str: The domain without the trailing dot, or an empty string when absent.
    """
    return str(value or "").rstrip(".")


class UnboundMixin(AiopnsenseClientProtocol):
    """Unbound DNS blocklist, Query Stats and Query Log methods for OPNsenseClient."""

    @dataclass(frozen=True, slots=True)
    class BucketCounts:
        """Query counters for one Complete Bucket or a sum of Complete Buckets.

        Attributes:
            queries (int): All queries.
            blocked (int): Blocked queries. Never includes Dropped.
            dropped (int): Dropped queries.
        """

        queries: int
        blocked: int
        dropped: int

        @property
        def blocked_share(self) -> float | None:
            """Return the percentage of queries that were Blocked.

            Returns:
                float | None: Blocked share in percent, or None when there were no queries.
            """
            if not self.queries:
                return None
            return self.blocked / self.queries * 100

    @dataclass(frozen=True, slots=True)
    class TopDomain:
        """One entry of a top-domain list over the Retention Window.

        Attributes:
            domain (str): Queried domain without the trailing dot.
            count (int): Queries for the domain in the Retention Window.
            percent (float): Share of the list's queries, in percent, as the Router reports it.
            blocklist (str | None): Blocklist that matched, for top Blocked domains only.
        """

        domain: str
        count: int
        percent: float
        blocklist: str | None

    @dataclass(frozen=True, slots=True)
    class QueryStats:
        """Unbound Query Stats built from Complete Buckets and Retention Window totals.

        Attributes:
            last_minute (UnboundMixin.BucketCounts | None): Counters of the last Complete
                Bucket of the last hour, or None when the hour has no Complete Bucket.
            last_24h (UnboundMixin.BucketCounts): Counters summed over the Complete Buckets
                of the last 24 hours.
            blocklist_size (int | None): Number of domains on the active blocklists.
            since (datetime | None): Start of the Retention Window.
            top_domains (tuple[UnboundMixin.TopDomain, ...]): Most queried domains in the
                Retention Window, at most 10.
            top_blocked_domains (tuple[UnboundMixin.TopDomain, ...]): Most Blocked domains
                in the Retention Window, at most 10.
        """

        last_minute: UnboundMixin.BucketCounts | None
        last_24h: UnboundMixin.BucketCounts
        blocklist_size: int | None
        since: datetime | None
        top_domains: tuple[UnboundMixin.TopDomain, ...]
        top_blocked_domains: tuple[UnboundMixin.TopDomain, ...]

    @dataclass(frozen=True, slots=True)
    class QueryLogEntry:
        """One query from the Unbound Query Log.

        Attributes:
            time (datetime): When the query was received.
            client (str): Client hostname from DHCP, or its IP address.
            domain (str): Queried domain without the trailing dot.
            action (str): Action the Router reports, such as ``Pass``, ``Block`` or ``Drop``.
            blocklist (str | None): Blocklist that matched, if any.
            policy (str | None): Blocklist policy that matched, if any. OPNsense reports
                policies from 26.1.
        """

        time: datetime
        client: str
        domain: str
        action: str
        blocklist: str | None
        policy: str | None

    @staticmethod
    def _complete_buckets(rolling: Mapping[str, Any]) -> list[UnboundMixin.BucketCounts]:
        """Return the counters of each Complete Bucket, oldest first.

        The newest Bucket is still filling and is dropped. Buckets whose key is not an
        epoch, or whose counters are not integers, are skipped.

        Args:
            rolling (Mapping[str, Any]): Epoch-keyed Buckets from ``overview/rolling``.

        Returns:
            list[UnboundMixin.BucketCounts]: Counters per Complete Bucket.
        """
        keyed = sorted(
            (
                (start, bucket)
                for key, bucket in rolling.items()
                if (start := try_to_float(key)) is not None
            ),
            key=lambda item: item[0],
        )
        complete: list[UnboundMixin.BucketCounts] = []
        for _start, bucket in keyed[:-1]:
            if not isinstance(bucket, Mapping):
                continue
            queries = try_to_int(bucket.get("total"))
            blocked = try_to_int(bucket.get("blocked"))
            dropped = try_to_int(bucket.get("dropped"))
            if queries is None or blocked is None or dropped is None:
                continue
            complete.append(
                UnboundMixin.BucketCounts(queries=queries, blocked=blocked, dropped=dropped)
            )
        return complete

    @staticmethod
    def _top_domains(entries: object) -> tuple[UnboundMixin.TopDomain, ...]:
        """Parse a top-domain mapping from ``overview/totals``, keeping Router order.

        Entries that are not mappings or lack a numeric count are skipped, and at most
        10 entries are kept.

        Args:
            entries (object): Domain-keyed mapping of ``{total, pcnt[, blocklist]}``.

        Returns:
            tuple[UnboundMixin.TopDomain, ...]: Parsed entries.
        """
        if not isinstance(entries, Mapping):
            return ()
        parsed: list[UnboundMixin.TopDomain] = []
        for domain, entry in entries.items():
            if len(parsed) >= TOP_DOMAINS_SIZE:
                break
            if not isinstance(entry, Mapping):
                continue
            count = try_to_int(entry.get("total"))
            if count is None:
                continue
            parsed.append(
                UnboundMixin.TopDomain(
                    domain=_domain_name(domain),
                    count=count,
                    percent=try_to_float(entry.get("pcnt")) or 0.0,
                    blocklist=_optional_text(entry.get("blocklist")),
                )
            )
        return tuple(parsed)

    @staticmethod
    def _query_log_entry(row: object) -> UnboundMixin.QueryLogEntry | None:
        """Parse one ``overview/search_queries`` row.

        Args:
            row (object): Query Log row from the Router.

        Returns:
            UnboundMixin.QueryLogEntry | None: The parsed entry, or None when the row is not
            a mapping or has no finite numeric time.
        """
        if not isinstance(row, Mapping):
            return None
        seconds = try_to_float(row.get("time"))
        time: datetime | None = None
        if seconds is not None and math.isfinite(seconds):
            with contextlib.suppress(OverflowError, OSError, ValueError):
                time = timestamp_to_datetime(int(seconds))
        if time is None:
            return None
        return UnboundMixin.QueryLogEntry(
            time=time,
            client=str(row.get("client") or ""),
            domain=_domain_name(row.get("domain")),
            action=str(row.get("action") or ""),
            blocklist=_optional_text(row.get("blocklist")),
            policy=_optional_text(row.get("policy")),
        )

    async def get_unbound_query_stats(self) -> QueryStats | None:
        """Return Unbound Query Stats for the last minute, 24 hours and Retention Window.

        Reads ``overview/rolling/1``, ``overview/rolling/24`` and ``overview/totals/10``.
        Only Complete Buckets are counted; the newest, still-filling Bucket is dropped.

        Unlike most reads, a transient failure raises whatever ``throw_errors`` is, so
        callers can tell a failed read from an absent endpoint.

        Returns:
            QueryStats | None: The Query Stats, or None when any of the three endpoints is
            absent (404, or 403 with errors disabled).

        Raises:
            outcome.error: The mapped ``OPNsenseError`` for a transient or decoding
                failure. With errors enabled, a 403 also raises its mapped error.
        """
        payloads: list[Mapping[str, Any]] = []
        for endpoint in (
            UNBOUND_OVERVIEW_ROLLING_HOUR_ENDPOINT,
            UNBOUND_OVERVIEW_ROLLING_DAY_ENDPOINT,
            UNBOUND_OVERVIEW_TOTALS_ENDPOINT,
        ):
            outcome = await self._fetch_endpoint_outcome(endpoint)
            if outcome.is_transient and outcome.error is not None:
                raise outcome.error
            if not outcome.is_data:
                _LOGGER.debug("[get_unbound_query_stats] %s absent", endpoint)
                return None
            payloads.append(outcome.mapping())
        hour, day, totals = payloads

        hour_buckets = self._complete_buckets(hour)
        day_buckets = self._complete_buckets(day)
        return self.QueryStats(
            last_minute=hour_buckets[-1] if hour_buckets else None,
            last_24h=self.BucketCounts(
                queries=sum(bucket.queries for bucket in day_buckets),
                blocked=sum(bucket.blocked for bucket in day_buckets),
                dropped=sum(bucket.dropped for bucket in day_buckets),
            ),
            blocklist_size=try_to_int(totals.get("blocklist_size")),
            since=timestamp_to_datetime(try_to_int(totals.get("start_time"))),
            top_domains=self._top_domains(totals.get("top")),
            top_blocked_domains=self._top_domains(totals.get("top_blocked")),
        )

    async def get_unbound_queries(
        self,
        action: str | None = None,
        client: str | None = None,
        search: str | None = None,
        limit: int = 30,
    ) -> list[QueryLogEntry] | None:
        """Return the newest queries from the Unbound Query Log.

        The Router keeps only its last 1,000 queries. Its search phrase matches any column,
        so ``action`` and ``client`` are applied here as exact, case-insensitive filters.

        Unlike most reads, a transient failure raises whatever ``throw_errors`` is, so
        callers can tell a failed read from an absent endpoint.

        Args:
            action (str | None): Only return queries with this action (``Pass``, ``Block``
                or ``Drop``).
            client (str | None): Only return queries from this client hostname or IP address.
            search (str | None): Free-text phrase the Router matches against every column.
            limit (int): Maximum number of entries to return, at most 1,000.

        Returns:
            list[QueryLogEntry] | None: Matching entries, newest first, empty when nothing
            matches or the payload has no rows. None when the endpoint is absent (404, or
            403 with errors disabled).

        Raises:
            outcome.error: The mapped ``OPNsenseError`` for a transient or decoding
                failure. With errors enabled, a 403 also raises its mapped error.
        """
        # The Router takes one free-text phrase. Send the most selective one available to
        # shrink the reply; action and client are then matched exactly below.
        phrase = search or action or client or ""
        payload = {
            "current": 1,
            "rowCount": QUERY_LOG_SIZE,
            "searchPhrase": phrase,
            "sort": {"time": "desc"},
        }
        outcome = await self._fetch_post_endpoint_outcome(
            UNBOUND_OVERVIEW_SEARCH_QUERIES_ENDPOINT, payload
        )
        if outcome.is_transient and outcome.error is not None:
            raise outcome.error
        if not outcome.is_data:
            _LOGGER.debug("[get_unbound_queries] Query Log absent")
            return None
        rows = outcome.mapping().get("rows")
        if not isinstance(rows, list):
            return []
        entries: list[UnboundMixin.QueryLogEntry] = []
        for row in rows:
            if len(entries) >= min(limit, QUERY_LOG_SIZE):
                break
            entry = self._query_log_entry(row)
            if entry is None:
                continue
            if action is not None and entry.action.casefold() != action.casefold():
                continue
            if client is not None and entry.client.casefold() != client.casefold():
                continue
            entries.append(entry)
        _LOGGER.debug("[get_unbound_queries] %s matching entries", len(entries))
        return entries

    @staticmethod
    def _normalize_unbound_blocklist_legacy(
        response: dict[str, Any], *, require_complete: bool = False
    ) -> dict[str, Any]:
        """Normalize legacy Unbound DNSBL settings.

        Args:
            response (dict[str, Any]): Decoded Unbound settings response.
            require_complete (bool): Require every field sent by the legacy settings write.

        Returns:
            dict[str, Any]: Normalized legacy DNSBL settings returned by older
            OPNsense firmware, or an empty mapping if the required settings are missing.
        """
        unbound_settings = response.get("unbound", {})
        if not isinstance(unbound_settings, MutableMapping):
            return {}

        dnsbl_settings = unbound_settings.get("dnsbl", {})
        if not isinstance(dnsbl_settings, MutableMapping):
            return {}

        scalar_fields = ("enabled", "safesearch", "nxdomain", "address")
        selector_fields = ("type", "lists", "whitelists", "blocklists", "wildcards")
        if require_complete and any(
            field not in dnsbl_settings for field in (*scalar_fields, *selector_fields)
        ):
            return {}

        dnsbl: dict[str, Any] = {}
        for attr in scalar_fields:
            dnsbl[attr] = dnsbl_settings.get(attr, "")
        for attr in selector_fields:
            if isinstance(dnsbl_settings.get(attr), MutableMapping):
                dnsbl[attr] = ",".join(
                    key
                    for key, value in dnsbl_settings[attr].items()
                    if isinstance(value, MutableMapping)
                    and api_value_matches(value.get("selected", 0), "1")
                )
            else:
                dnsbl[attr] = ""
        return dnsbl

    async def _set_unbound_blocklist_legacy(self, set_state: bool) -> bool:
        """Enable or disable legacy Unbound DNSBL settings.

        Args:
            set_state (bool): Desired enabled state to apply.

        Returns:
            bool: ``True`` when the settings save, DNSBL apply, and service
            restart all succeed; otherwise, ``False``. Incomplete settings
            prevent the save. When ``throw_errors`` is enabled, a failed
            read, save, apply or restart request raises an
            ``OPNsenseError`` subclass instead of returning ``False``.
        """
        response = await self._safe_dict_get(UNBOUND_SETTINGS_GET_ENDPOINT)
        dnsbl = self._normalize_unbound_blocklist_legacy(response, require_complete=True)
        if not dnsbl:
            _LOGGER.error("Unable to get Unbound Blocklist Status")
            return False

        dnsbl["enabled"] = "1" if set_state else "0"
        payload = {"unbound": {"dnsbl": dnsbl}}
        return await self._run_toggle(
            ToggleSpec(
                label="enable_unbound_blocklist" if set_state else "disable_unbound_blocklist",
                target=ToggleTarget.ON if set_state else ToggleTarget.OFF,
                save=(
                    ToggleStep.save(
                        UNBOUND_SETTINGS_SET_ENDPOINT,
                        frozenset({"saved"}),
                        payload=payload,
                        failure_log_level=logging.ERROR,
                    ),
                ),
                apply=(
                    ToggleStep.apply(
                        UNBOUND_SERVICE_DNSBL_ENDPOINT,
                        method="GET",
                        failure_log_level=logging.ERROR,
                    ),
                    ToggleStep.apply(UNBOUND_SERVICE_RESTART_ENDPOINT, response_key="response"),
                ),
            )
        )

    @_log_errors
    async def get_unbound_blocklist(self) -> dict[str, Any] | None:
        """Return Unbound DNS blocklist configuration.

        Returns:
            dict[str, Any] | None: A UUID-keyed mapping of extended DNSBL rows,
                or the regular DNSBL settings under ``legacy`` on older firmware.
                A missing endpoint (404) returns an empty mapping. With errors
                disabled, a denied or failed read, undecodable JSON, or a wrongly
                shaped inventory returns ``None``. With errors enabled, decoded
                payloads use the existing normalization rules, and the mapped
                ``OPNsenseError`` propagates for request or decoding failures,
                including a legacy endpoint's 404.
        """
        extended_supported = await self._firmware_capability_enabled(
            EXTENDED_UNBOUND_BLOCKLIST_CAPABILITY
        )
        if not extended_supported:
            _LOGGER.debug(
                "Getting Unbound regular blocklists for OPNsense < %s or unknown firmware",
                EXTENDED_UNBOUND_BLOCKLIST_CAPABILITY.minimum,
            )
            if _throws_errors(self):
                # Keep the legacy plain request's strict-mode handling of a 404.
                response = await self._safe_dict_get(UNBOUND_SETTINGS_GET_ENDPOINT)
                return {"legacy": self._normalize_unbound_blocklist_legacy(response)}

        endpoint = (
            UNBOUND_SETTINGS_SEARCH_DNSBL_ENDPOINT
            if extended_supported
            else UNBOUND_SETTINGS_GET_ENDPOINT
        )
        dnsbl_outcome = await self._fetch_endpoint_outcome(endpoint)
        if not dnsbl_outcome.is_data:
            return {} if dnsbl_outcome.http_status == 404 else None

        dnsbl_raw = dnsbl_outcome.mapping()
        if not extended_supported:
            enabled = dict_get(dnsbl_raw, "unbound.dnsbl.enabled")
            if not (api_value_matches(enabled, "0") or api_value_matches(enabled, "1")):
                _LOGGER.debug("[get_unbound_blocklist] Invalid legacy DNSBL settings")
                return None
            return {"legacy": self._normalize_unbound_blocklist_legacy(dnsbl_raw)}

        throw_errors = _throws_errors(self)
        dnsbl_rows = dnsbl_raw.get("rows")
        if not isinstance(dnsbl_rows, list):
            _LOGGER.debug("[get_unbound_blocklist] Invalid DNSBL row list")
            return {} if throw_errors else None
        dnsbl_full: dict[str, Any] = {}
        for dnsbl in dnsbl_rows:
            uuid = dnsbl.get("uuid") if isinstance(dnsbl, MutableMapping) else None
            if not throw_errors and (not isinstance(uuid, str) or not uuid):
                _LOGGER.debug("[get_unbound_blocklist] DNSBL row has no valid UUID")
                return None
            if uuid:
                dnsbl_full[uuid] = dnsbl
        _LOGGER.debug("[get_unbound_blocklist] dnsbl_full length: %s", len(dnsbl_full))
        return dnsbl_full

    async def _toggle_unbound_blocklist(self, set_state: bool, uuid: str | None) -> bool:
        """Enable or disable one extended Unbound DNSBL entry.

        Args:
            set_state (bool): Desired enabled state for the DNSBL entry.
            uuid (str | None): UUID of the extended DNSBL entry to toggle.

        Returns:
            bool: ``True`` when OPNsense reports the toggle result matching the
                requested state and the DNSBL apply step succeeds; otherwise,
                ``False``. Transport errors raise when ``throw_errors`` is enabled.
        """
        if not uuid:
            _LOGGER.error("Blocklist uuid must be provided for Unbound Extended Blocklists")
            return False
        endpoint = (
            f"{UNBOUND_SETTINGS_TOGGLE_DNSBL_ENDPOINT_PREFIX}{uuid}/{'1' if set_state else '0'}"
        )
        return await self._run_toggle(
            ToggleSpec(
                label="enable_unbound_blocklist" if set_state else "disable_unbound_blocklist",
                target=ToggleTarget.ON if set_state else ToggleTarget.OFF,
                save=(
                    ToggleStep.save(endpoint, frozenset({"Enabled" if set_state else "Disabled"})),
                ),
                apply=(ToggleStep.apply(UNBOUND_SERVICE_DNSBL_ENDPOINT),),
            )
        )

    async def _set_unbound_blocklist(self, set_state: bool, uuid: str | None = None) -> bool:
        """Route an Unbound blocklist state change to the correct backend mode.

        Args:
            set_state (bool): Desired enabled state.
            uuid (str | None, optional): UUID of an extended DNSBL entry. Omit
                for legacy firmware where regular DNSBL has no per-entry UUID.

        Returns:
            bool: True when the operation succeeds. Otherwise, False. Transport
                errors during the save or apply raise when ``throw_errors`` is
                enabled.
        """
        supported = await self._firmware_capability(EXTENDED_UNBOUND_BLOCKLIST_CAPABILITY)
        state_name = "enable" if set_state else "disable"
        if supported is False:
            if uuid is not None:
                _LOGGER.error(
                    "Blocklist uuid %s is unsupported when trying to %s legacy Unbound blocklists on OPNsense < %s",
                    uuid,
                    state_name,
                    EXTENDED_UNBOUND_BLOCKLIST_CAPABILITY.minimum,
                )
                return False
            _LOGGER.debug(
                "Using Unbound regular blocklists for OPNsense < %s",
                EXTENDED_UNBOUND_BLOCKLIST_CAPABILITY.minimum,
            )
            return await self._set_unbound_blocklist_legacy(set_state=set_state)
        if supported is True:
            _LOGGER.debug(
                "Using Unbound extended blocklists for OPNsense >= %s",
                EXTENDED_UNBOUND_BLOCKLIST_CAPABILITY.minimum,
            )
            return await self._toggle_unbound_blocklist(set_state=set_state, uuid=uuid)

        if uuid is not None:
            return await self._toggle_unbound_blocklist(set_state=set_state, uuid=uuid)
        return await self._set_unbound_blocklist_legacy(set_state=set_state)

    @_log_boolean_errors
    async def enable_unbound_blocklist(self, uuid: str | None = None) -> bool:
        """Enable Unbound DNS blocklist filtering.

        Args:
            uuid (str | None, optional): UUID of an extended DNSBL entry. Omit
                when enabling the legacy regular DNSBL setting.

        Returns:
            bool: True when the operation succeeds. Otherwise, False. Transport
                errors raise ``OPNsenseError`` when ``throw_errors`` is enabled.
        """
        return await self._set_unbound_blocklist(set_state=True, uuid=uuid)

    @_log_boolean_errors
    async def disable_unbound_blocklist(self, uuid: str | None = None) -> bool:
        """Disable Unbound DNS blocklist filtering.

        Args:
            uuid (str | None, optional): UUID of an extended DNSBL entry. Omit
                when disabling the legacy regular DNSBL setting.

        Returns:
            bool: True when the operation succeeds; otherwise, False. Transport
                errors raise ``OPNsenseError`` when ``throw_errors`` is enabled.
        """
        return await self._set_unbound_blocklist(set_state=False, uuid=uuid)
