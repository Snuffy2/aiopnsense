"""Typing protocol contracts for pyopnsense mixins."""

from abc import abstractmethod
from collections.abc import MutableMapping
from datetime import datetime, tzinfo
from typing import Any, Protocol


class PyOPNsenseClientProtocol(Protocol):
    """Structural typing contract used by split pyopnsense mixins."""

    _firmware_version: str | None
    _endpoint_availability: dict[str, bool]
    _endpoint_checked_at: dict[str, datetime]
    _endpoint_cache_ttl_seconds: int

    @abstractmethod
    async def _get(self, path: str) -> MutableMapping[str, Any] | list | None:
        """Queue a GET request and return the decoded payload.

        Parameters
        ----------
        path : str
            Relative API path.

        Returns
        -------
        MutableMapping[str, Any] | list | None
            Decoded JSON payload, or ``None`` when unavailable.

        """
        ...

    @abstractmethod
    async def _post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> MutableMapping[str, Any] | list | None:
        """Queue a POST request and return the decoded payload.

        Parameters
        ----------
        path : str
            Relative API path.
        payload : MutableMapping[str, Any] | None
            Optional request body.

        Returns
        -------
        MutableMapping[str, Any] | list | None
            Decoded JSON payload, or ``None`` when unavailable.

        """
        ...

    @abstractmethod
    async def _get_from_stream(self, path: str) -> dict[str, Any]:
        """Queue a streaming GET request and parse the first data payload.

        Parameters
        ----------
        path : str
            Relative API path.

        Returns
        -------
        dict[str, Any]
            Parsed stream payload.

        """
        ...

    @abstractmethod
    async def _safe_dict_get(self, path: str) -> dict[str, Any]:
        """Fetch a GET payload and coerce non-mapping values to an empty mapping.

        Parameters
        ----------
        path : str
            Relative API path.

        Returns
        -------
        dict[str, Any]
            Dictionary payload.

        """
        ...

    @abstractmethod
    async def _safe_dict_get_with_timeout(
        self, path: str, timeout_seconds: float
    ) -> dict[str, Any]:
        """Fetch a GET payload with a custom timeout and coerce non-mapping values.

        Parameters
        ----------
        path : str
            Relative API path.
        timeout_seconds : int | float
            Total timeout window in seconds for the request.

        Returns
        -------
        dict[str, Any]
            Dictionary payload.

        """
        ...

    @abstractmethod
    async def _safe_list_get(self, path: str) -> list:
        """Fetch a GET payload and coerce non-list values to an empty list.

        Parameters
        ----------
        path : str
            Relative API path.

        Returns
        -------
        list
            List payload.

        """
        ...

    @abstractmethod
    async def _safe_dict_post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fetch a POST payload and coerce non-mapping values to an empty mapping.

        Parameters
        ----------
        path : str
            Relative API path.
        payload : MutableMapping[str, Any] | None
            Optional request body.

        Returns
        -------
        dict[str, Any]
            Dictionary payload.

        """
        ...

    @abstractmethod
    async def _safe_list_post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> list:
        """Fetch a POST payload and coerce non-list values to an empty list.

        Parameters
        ----------
        path : str
            Relative API path.
        payload : MutableMapping[str, Any] | None
            Optional request body.

        Returns
        -------
        list
            List payload.

        """
        ...

    @abstractmethod
    async def _get_opnsense_timezone(self, datetime_str: str | None = None) -> tzinfo:
        """Resolve timezone information from OPNsense system time data.

        Parameters
        ----------
        datetime_str : str | None
            Optional datetime string from OPNsense ``system_time`` output.

        Returns
        -------
        tzinfo
            Parsed timezone when available, otherwise a local fixed-offset fallback.

        """
        ...

    @abstractmethod
    async def get_query_counts(self) -> int:
        """Return the current REST API query count.

        Returns
        -------
        int
            Total REST API query count recorded by the client.

        """
        ...

    @abstractmethod
    async def get_host_firmware_version(self) -> str | None:
        """Return the host firmware version string.

        Returns
        -------
        str | None
            Parsed firmware version, if available.

        """
        ...

    @abstractmethod
    async def is_endpoint_available(self, path: str, force_refresh: bool = False) -> bool:
        """Return whether a specific API endpoint appears available.

        Parameters
        ----------
        path : str
            API path to probe.
        force_refresh : bool
            Whether to bypass cached probe results.

        Returns
        -------
        bool
            ``True`` when endpoint probe succeeds.

        Notes
        -----
        Implementations cache per-endpoint availability using a TTL window.
        Successful probes and HTTP 404 "not found" probes are cached until TTL
        expiry.
        Other probe failures are treated as transient and retried on the next
        check.
        ``force_refresh=True`` bypasses cache freshness checks.

        """
        ...
