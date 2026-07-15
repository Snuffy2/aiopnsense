"""Core transport and queue plumbing for OPNsenseClient."""

import asyncio
from collections.abc import MutableMapping
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
import warnings

import aiohttp

from .client_endpoint import ClientEndpointMixin
from .client_queue import ClientQueueMixin
from .client_transport import ClientTransportMixin
from .const import DEFAULT_CACHE_TTL_SECONDS
from .exceptions import OPNsenseInvalidArgument

_UNSET: object = object()


class ClientBaseMixin(ClientEndpointMixin, ClientQueueMixin, ClientTransportMixin):
    """ClientBase methods for OPNsenseClient."""

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        opts: MutableMapping[str, Any] | None = None,
        initial: bool | object = _UNSET,
        name: str = "OPNsense",
        *,
        throw_errors: bool | object = _UNSET,
    ) -> None:
        """Initialize the OPNsense client.

        Args:
            url (str): Base URL of the OPNsense instance.
            username (str): Username for API authentication.
            password (str): Password for API authentication.
            session (aiohttp.ClientSession): HTTP client session used for API requests.
            opts (MutableMapping[str, Any] | None, optional): Optional client configuration values
                (e.g. ``opts={"verify_ssl": True}``).
            initial (bool | object): Deprecated alias for ``throw_errors``. When provided,
                a ``DeprecationWarning`` is emitted. Ignored when ``throw_errors`` is also set.
            throw_errors (bool | object): Whether request and decorator errors should be
                re-raised instead of logged and suppressed. Defaults to ``False``.
            name (str): Display name for the client instance.

        Raises:
            OPNsenseInvalidArgument: Raised when ``initial`` or ``throw_errors`` is not a ``bool``.
        """

        self._username: str = username
        self._password: str = password
        self._name: str = name

        self._opts: dict[str, Any] = dict(opts or {})
        self._verify_ssl: bool = self._opts.get("verify_ssl", True)
        parts = urlparse(url.rstrip("/"))
        self._url: str = f"{parts.scheme}://{parts.netloc}"
        self._session: aiohttp.ClientSession = session
        self._throw_errors: bool = False
        if throw_errors is not _UNSET:
            if not isinstance(throw_errors, bool):
                raise OPNsenseInvalidArgument("`throw_errors` must be a bool.")
            self._throw_errors = throw_errors
        if initial is not _UNSET:
            if not isinstance(initial, bool):
                raise OPNsenseInvalidArgument("`initial` must be a bool.")
            warnings.warn(
                "In OPNsenseClient, `initial` is deprecated and will be removed in a future release. "
                "Use `throw_errors` instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            if throw_errors is _UNSET:
                self._throw_errors = initial
        self._firmware_version: str | None = None
        self._use_snake_case: bool | None = None
        self._endpoint_availability: dict[str, bool] = {}
        self._endpoint_checked_at: dict[str, datetime] = {}
        self._endpoint_cache_ttl_seconds = DEFAULT_CACHE_TTL_SECONDS
        self._rest_api_query_count = 0
        self._request_queue: asyncio.Queue = asyncio.Queue()
        self._workers: list[asyncio.Task[Any]] = []
        # Number of parallel workers to process the queue
        self._max_workers = 2
        # Don't use directly. Use await self._get_active_loop() instead
        self._loop: asyncio.AbstractEventLoop | None = None

    def toggle_throwing_errors(self, throw_errors: bool | None = None) -> bool:
        """Set or toggle request error propagation.

        Args:
            throw_errors (bool | None): Explicit error propagation state. When ``None``,
                the current state is inverted.

        Returns:
            bool: Updated error propagation state.

        Raises:
            OPNsenseInvalidArgument: Raised when ``throw_errors`` is not a ``bool`` or ``None``.
        """
        if throw_errors is None:
            self._throw_errors = not self._throw_errors
        else:
            if not isinstance(throw_errors, bool):
                raise OPNsenseInvalidArgument("`throw_errors` must be a bool or None.")
            self._throw_errors = throw_errors
        return self._throw_errors

    @property
    def name(self) -> str:
        """Return the name of the client.

        Returns:
            str: The name of the client.
        """
        return self._name

    async def reset_query_counts(self) -> None:
        """Reset API query counters to zero."""
        self._rest_api_query_count = 0

    async def get_query_counts(self) -> int:
        """Return current API query counts.

        Returns:
            int: Current number of API queries performed by the client.
        """
        return self._rest_api_query_count
