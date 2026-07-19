"""Typing protocol contracts for aiopnsense mixins."""

import asyncio
from collections.abc import AsyncGenerator, MutableMapping
from dataclasses import dataclass
from datetime import tzinfo
from typing import Any, Iterator, Literal, Protocol


type CategoryState = Literal["available", "pending", "missing", "transient", "malformed"]
EndpointAvailabilityState = Literal["available", "missing", "pending"]


@dataclass(frozen=True, slots=True)
class CategoryResult[T]:
    """Immutable data and availability result for an optional API category."""

    data: T
    state: CategoryState
    authoritative: bool

    def __post_init__(self) -> None:
        """Reject envelopes whose authority contradicts their state."""
        if self.authoritative is not (self.state == "available"):
            raise ValueError("authoritative must be true exactly when state is 'available'")

    @staticmethod
    def coerce(value: object) -> "CategoryResult[object]":
        """Normalize legacy internal tuple results during the contract migration."""
        if isinstance(value, CategoryResult):
            return value
        if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
            state: CategoryState | str = "transient" if value[0] == "unavailable" else value[0]
            if state in {"available", "pending", "missing", "transient", "malformed"}:
                typed_state: CategoryState = state
                return CategoryResult(value[1], typed_state, typed_state == "available")
        return CategoryResult({}, "malformed", False)

    def __iter__(self) -> Iterator[object]:
        """Yield legacy state/data tuple values for internal compatibility."""
        yield self.state
        yield self.data

    def __eq__(self, other: object) -> bool:
        """Compare result objects, with temporary support for legacy tuples."""
        if isinstance(other, CategoryResult):
            return (
                self.data == other.data
                and self.state == other.state
                and self.authoritative == other.authoritative
            )
        if isinstance(other, tuple) and len(other) == 2:
            legacy_state = "transient" if other[0] == "unavailable" else other[0]
            return self.state == legacy_state and self.data == other[1]
        return NotImplemented


class AiopnsenseClientProtocol(Protocol):
    """Structural typing contract used by split aiopnsense mixins."""

    _throw_errors: bool
    _use_snake_case: bool | None
    _endpoint_availability: dict[tuple[Literal["get", "post"], str], EndpointAvailabilityState]
    _endpoint_checked_at: dict[tuple[Literal["get", "post"], str], float]
    _endpoint_locks: dict[tuple[Literal["get", "post"], str], asyncio.Lock]
    _optional_endpoint_missing_pending_confirmation: set[tuple[Literal["get", "post"], str]]
    _dhcp_source_states: list[CategoryState]

    async def _get(self, path: str) -> MutableMapping[str, Any] | list | None: ...

    async def _get_optional(self, path: str) -> CategoryResult[object]: ...

    async def _post_optional(
        self,
        path: str,
        payload: MutableMapping[str, Any] | None = None,
    ) -> CategoryResult[object]: ...

    async def _get_text(self, path: str) -> str | None: ...

    async def _post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> MutableMapping[str, Any] | list | None: ...

    async def _get_from_stream(self, path: str) -> dict[str, Any]: ...

    def _stream_json_events(
        self,
        path: str,
        *,
        yield_reset_events: bool = False,
        sock_read_timeout_seconds: float | None = None,
    ) -> AsyncGenerator[dict[str, Any]]: ...

    async def _safe_dict_get(self, path: str) -> dict[str, Any]: ...

    async def _safe_dict_get_with_timeout(
        self, path: str, timeout_seconds: float
    ) -> dict[str, Any]: ...

    async def _safe_list_get(self, path: str) -> list: ...

    async def _safe_dict_post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> dict[str, Any]: ...

    async def _safe_list_post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> list: ...

    async def _get_opnsense_timezone(self, datetime_str: str | None = None) -> tzinfo: ...

    async def get_host_firmware_version(self) -> str | None: ...

    async def _get_endpoint_path(self, snake_case_path: str, camel_case_path: str) -> str: ...

    async def is_endpoint_available(self, path: str, force_refresh: bool = False) -> bool: ...

    async def _is_get_endpoint_available(self, path: str, force_refresh: bool = False) -> bool: ...

    async def _is_post_endpoint_available(
        self, path: str, force_refresh: bool = False
    ) -> bool | None: ...

    async def _check_optional_get_endpoint(
        self,
        path: str,
        cache_path: str | None = None,
        *,
        force_refresh: bool = False,
    ) -> CategoryResult[object]: ...

    async def _check_optional_post_endpoint(
        self,
        path: str,
        payload: MutableMapping[str, Any] | None = None,
        cache_path: str | None = None,
        *,
        force_refresh: bool = False,
    ) -> CategoryResult[object]: ...

    async def get_smart_result(self) -> CategoryResult[list[dict[str, Any]]]: ...

    async def get_vnstat_result(self) -> CategoryResult[MutableMapping[str, Any]]: ...

    async def get_unbound_blocklist_result(self) -> CategoryResult[dict[str, Any]]: ...

    async def get_dhcp_leases_result(
        self, opnsense_tz: tzinfo | None = None
    ) -> CategoryResult[dict[str, Any]]: ...

    async def get_arp_table_result(
        self, resolve_hostnames: bool = False
    ) -> CategoryResult[list[dict[str, Any]]]: ...
