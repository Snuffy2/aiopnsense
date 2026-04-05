"""Typing protocol contracts for aiopnsense mixins."""

from abc import abstractmethod
from collections.abc import MutableMapping
from datetime import datetime, tzinfo
from typing import Any, Protocol


class AiopnsenseClientProtocol(Protocol):
    """Structural typing contract used by split aiopnsense mixins."""

    _firmware_version: str | None
    _use_snake_case: bool | None
    _endpoint_availability: dict[str, bool]
    _endpoint_checked_at: dict[str, datetime]
    _endpoint_cache_ttl_seconds: int

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
    async def get_query_counts(self) -> int:
        """Return the current REST API query count.

        Returns:
            int: Current number of API queries performed by the client.
        """
        ...

    @abstractmethod
    async def get_host_firmware_version(self) -> str | None:
        """Return the host firmware version string.

        Returns:
            str | None: Normalized data returned by the related OPNsense endpoint.
        """
        ...

    @abstractmethod
    async def set_use_snake_case(self) -> None:
        """Set firmware-specific endpoint naming behavior.

        Returns:
            None: This method updates internal client state only.
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
    async def is_endpoint_available(self, path: str, force_refresh: bool = False) -> bool:
        """Return whether a specific API endpoint appears available.

        Args:
            path (str): API endpoint path to request.
            force_refresh (bool): Whether to bypass cached endpoint availability.

        Returns:
            bool: True if a specific api endpoint appears available; otherwise, False.
        """
        ...
