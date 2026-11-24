"""Typing protocol contracts for aiopnsense mixins."""

import asyncio
from collections.abc import AsyncGenerator, MutableMapping
from datetime import tzinfo
from typing import TYPE_CHECKING, Any, Literal, Protocol

import aiohttp

from ._endpoint_outcome import _EndpointOutcome
from ._styled_endpoint import _EndpointPath
from .const import FirmwareCapability

if TYPE_CHECKING:
    from .client_toggle import ToggleSpec, ToggleTarget


class AiopnsenseClientProtocol(Protocol):
    """Structural typing contract used by split aiopnsense mixins."""

    _firmware_install_active: bool
    _firmware_action_lock: asyncio.Lock
    _firmware_check_requested_at: float | None
    _active_close_count: int
    _throw_errors: bool

    def _open_request(
        self,
        method: Literal["get", "post"],
        path: str,
        *,
        timeout: aiohttp.ClientTimeout,
        payload: MutableMapping[str, Any] | None = None,
    ) -> Any: ...

    async def _get(self, path: str) -> MutableMapping[str, Any] | list | None: ...

    async def _post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> MutableMapping[str, Any] | list | None: ...

    async def _do_post(
        self, path: str, payload: MutableMapping[str, Any] | None = None, caller: str = "Unknown"
    ) -> MutableMapping[str, Any] | list | str | None: ...

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

    async def _safe_dict_post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> dict[str, Any]: ...

    async def _safe_dict_post_with_cancellation(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> tuple[dict[str, Any], asyncio.CancelledError | None]: ...

    async def _safe_list_post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> list: ...

    async def _get_resolved_opnsense_timezone(
        self, datetime_str: str | None = None
    ) -> tzinfo | None: ...

    async def _get_opnsense_timezone(self, datetime_str: str | None = None) -> tzinfo: ...

    async def get_host_firmware_version(self) -> str | None: ...

    async def _firmware_capability(self, capability: FirmwareCapability) -> bool | None: ...

    def _reset_endpoint_state(self) -> None: ...

    async def _firmware_capability_enabled(self, capability: FirmwareCapability) -> bool: ...

    async def _resolve_endpoint(self, endpoint: _EndpointPath) -> str: ...

    async def _fetch_endpoint_outcome(
        self,
        path: _EndpointPath,
        response_format: Literal["json", "text"] = "json",
        *,
        force_refresh: bool = False,
    ) -> _EndpointOutcome: ...

    async def _fetch_post_endpoint_outcome(
        self, path: _EndpointPath, payload: MutableMapping[str, Any] | None = None
    ) -> _EndpointOutcome: ...

    async def _check_endpoint_status(
        self, path: str, *, force_refresh: bool = False
    ) -> _EndpointOutcome: ...

    @staticmethod
    def _parse_toggle_target(toggle_on_off: object) -> ToggleTarget: ...

    async def _run_toggle(self, spec: ToggleSpec) -> bool: ...
