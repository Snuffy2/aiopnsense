"""Typing protocol contracts for aiopnsense mixins."""

from collections.abc import AsyncGenerator, MutableMapping
from datetime import tzinfo
from typing import Any, Protocol


class AiopnsenseClientProtocol(Protocol):
    """Structural typing contract used by split aiopnsense mixins."""

    _throw_errors: bool

    async def _get(self, path: str) -> MutableMapping[str, Any] | list | None: ...

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

    async def _get_resolved_opnsense_timezone(
        self, datetime_str: str | None = None
    ) -> tzinfo | None: ...

    async def _get_opnsense_timezone(self, datetime_str: str | None = None) -> tzinfo: ...

    async def get_host_firmware_version(self) -> str | None: ...

    async def _get_endpoint_path(self, snake_case_path: str, camel_case_path: str) -> str: ...

    async def is_endpoint_available(self, path: str, force_refresh: bool = False) -> bool: ...

    async def _is_get_endpoint_available(self, path: str, force_refresh: bool = False) -> bool: ...

    async def _is_post_endpoint_available(
        self, path: str, force_refresh: bool = False
    ) -> bool | None: ...
