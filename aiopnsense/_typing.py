"""Typing protocol contracts for aiopnsense mixins."""

import asyncio
from collections.abc import AsyncGenerator, MutableMapping
from datetime import tzinfo
from typing import Any, Literal, Protocol


class AiopnsenseClientProtocol(Protocol):
    """Structural typing contract used by split aiopnsense mixins."""

    _throw_errors: bool
    _endpoint_availability: dict[tuple[Literal["get", "post"], str], bool]
    _endpoint_checked_at: dict[tuple[Literal["get", "post"], str], float]
    _endpoint_locks: dict[tuple[Literal["get", "post"], str], asyncio.Lock]
    _optional_endpoint_missing_pending_confirmation: set[tuple[Literal["get", "post"], str]]

    async def _get(self, path: str) -> MutableMapping[str, Any] | list | None: ...

    async def _get_optional(
        self, path: str
    ) -> tuple[Literal["available", "malformed", "missing", "unavailable"], object]: ...

    async def _post_optional(
        self,
        path: str,
        payload: MutableMapping[str, Any] | None = None,
    ) -> tuple[Literal["available", "malformed", "missing", "unavailable"], object]: ...

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
        *,
        force_refresh: bool = False,
    ) -> tuple[Literal["available", "malformed", "missing", "unavailable"], object]: ...

    async def _check_optional_post_endpoint(
        self,
        path: str,
        payload: MutableMapping[str, Any] | None = None,
        cache_path: str | None = None,
        *,
        force_refresh: bool = False,
    ) -> tuple[Literal["available", "malformed", "missing", "unavailable"], object]: ...
