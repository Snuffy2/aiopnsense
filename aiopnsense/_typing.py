"""Typing protocol contracts for aiopnsense mixins."""

from abc import abstractmethod
from collections.abc import AsyncGenerator, MutableMapping
from datetime import tzinfo
from typing import Any, Protocol
from warnings import deprecated


class AiopnsenseClientProtocol(Protocol):
    """Structural typing contract used by split aiopnsense mixins."""

    _throw_errors: bool

    @abstractmethod
    async def _get(self, path: str) -> MutableMapping[str, Any] | list | None:
        """Queue a GET request and return the decoded payload.

        Args:
            path (str): API endpoint path to request.

        Returns:
            MutableMapping[str, Any] | list | None: Decoded response payload returned by the GET request.
        """
        ...

    @abstractmethod
    async def _post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> MutableMapping[str, Any] | list | None:
        """Queue a POST request and return the decoded payload.

        Args:
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None, optional): Request payload sent to the API endpoint.

        Returns:
            MutableMapping[str, Any] | list | None: Decoded response payload returned by the POST request.
        """
        ...

    @abstractmethod
    async def _get_from_stream(self, path: str) -> dict[str, Any]:
        """Queue a streaming GET request and parse the first data payload.

        Args:
            path (str): API endpoint path to request.

        Returns:
            dict[str, Any]: Decoded payload extracted from the streaming API response.
        """
        ...

    @abstractmethod
    def _stream_json_events(
        self,
        path: str,
        *,
        yield_reset_events: bool = False,
        sock_read_timeout_seconds: float | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield decoded JSON objects from a server-sent event stream."""
        ...

    @abstractmethod
    async def _safe_dict_get(self, path: str) -> dict[str, Any]:
        """Fetch a GET payload and coerce non-mapping values to an empty mapping.

        Args:
            path (str): API endpoint path to request.

        Returns:
            dict[str, Any]: Response payload coerced to a dictionary.
        """
        ...

    @abstractmethod
    async def _safe_dict_get_with_timeout(
        self, path: str, timeout_seconds: float
    ) -> dict[str, Any]:
        """Fetch a GET payload with a custom timeout and coerce non-mapping values.

        Args:
            path (str): API endpoint path to request.
            timeout_seconds (float): Request timeout in seconds for this call.

        Returns:
            dict[str, Any]: Response payload coerced to a dictionary.
        """
        ...

    @abstractmethod
    async def _safe_list_get(self, path: str) -> list:
        """Fetch a GET payload and coerce non-list values to an empty list.

        Args:
            path (str): API endpoint path to request.

        Returns:
            list: Response payload coerced to a list.
        """
        ...

    @abstractmethod
    async def _safe_dict_post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fetch a POST payload and coerce non-mapping values to an empty mapping.

        Args:
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None, optional): Request payload sent to the API endpoint.

        Returns:
            dict[str, Any]: Response payload coerced to a dictionary.
        """
        ...

    @abstractmethod
    async def _safe_list_post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> list:
        """Fetch a POST payload and coerce non-list values to an empty list.

        Args:
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None, optional): Request payload sent to the API endpoint.

        Returns:
            list: Response payload coerced to a list.
        """
        ...

    @abstractmethod
    async def _get_opnsense_timezone(self, datetime_str: str | None = None) -> tzinfo:
        """Resolve timezone information from OPNsense system time data.

        Args:
            datetime_str (str | None, optional): Datetime string parsed from API output.

        Returns:
            tzinfo: Resolved timezone object for OPNsense system data.
        """
        ...

    @abstractmethod
    async def get_host_firmware_version(self) -> str | None:
        """Return the cached or fetched host firmware version string.

        Returns:
            str | None: Installed OPNsense firmware version or product series,
                or ``None`` when the version cannot be determined.
        """
        ...

    @abstractmethod
    async def _get_endpoint_path(self, snake_case_path: str, camel_case_path: str) -> str:
        """Return the selected endpoint path for the current firmware family.

        Args:
            snake_case_path (str): Endpoint path for newer snake_case firmware.
            camel_case_path (str): Endpoint path for older camelCase firmware.

        Returns:
            str: Selected endpoint path for the current firmware family.
        """
        ...

    @abstractmethod
    @deprecated("Endpoint availability probing is internal. Direct calls are no longer needed.")
    async def is_endpoint_available(self, path: str, force_refresh: bool = False) -> bool:
        """Return whether a specific GET-probed API endpoint appears available.

        Args:
            path (str): API endpoint path to request.
            force_refresh (bool): Whether to bypass cached endpoint availability.

        Returns:
            bool: True if the GET probe succeeds; otherwise, False.
        """
        ...

    @abstractmethod
    async def _is_get_endpoint_available(self, path: str, force_refresh: bool = False) -> bool:
        """Return whether a specific GET-probed API endpoint appears available.

        Args:
            path (str): API endpoint path to request.
            force_refresh (bool): Whether to bypass cached endpoint availability.

        Returns:
            bool: True if the GET probe succeeds; otherwise, False.
        """
        ...

    @abstractmethod
    async def _is_post_endpoint_available(
        self, path: str, force_refresh: bool = False
    ) -> bool | None:
        """Return whether a specific POST-probed API endpoint appears available.

        Args:
            path (str): API endpoint path to request.
            force_refresh (bool): Whether to bypass cached endpoint availability.

        Returns:
            bool | None: True if the POST probe succeeds, False if it runs and
                appears unavailable, or None when the probe is skipped because
                the path is invalid or the endpoint could mutate state.
        """
        ...
