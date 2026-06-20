"""Service management methods for OPNsenseClient."""

from collections.abc import MutableMapping
from typing import Any
from urllib.parse import quote

from ._typing import AiopnsenseClientProtocol
from .helpers import _LOGGER, _log_errors, api_value_matches

CORE_SERVICE_SEARCH_ENDPOINT = "/api/core/service/search"
CORE_SERVICE_ENDPOINT_PREFIX = "/api/core/service/"


class ServicesMixin(AiopnsenseClientProtocol):
    """Service management methods for OPNsenseClient."""

    def _normalize_services_rows(self, rows: object) -> list[dict[str, Any]]:
        """Normalize service rows and derive boolean status values.

        Args:
            rows (object): Raw ``rows`` payload from the service-search endpoint.

        Returns:
            list[dict[str, Any]]: List of normalized service mappings.
        """
        services: list[dict[str, Any]] = rows if isinstance(rows, list) else []
        normalized_services: list[dict[str, Any]] = []
        for service in services:
            if not isinstance(service, MutableMapping):
                continue
            running = service.get("running", 0)
            service["status"] = api_value_matches(running, "1")
            normalized_services.append(service)
        return normalized_services

    async def _fetch_normalized_services(
        self,
        *,
        return_none_when_unavailable: bool,
    ) -> list[dict[str, Any]] | None:
        """Fetch and normalize services with configurable unavailable-endpoint behavior.

        Args:
            return_none_when_unavailable (bool): Whether to return ``None`` instead of an empty
                list when the endpoint probe fails.

        Returns:
            list[dict[str, Any]] | None: Normalized service payload, or ``None`` when endpoint
                probing fails and ``return_none_when_unavailable`` is ``True``.
        """
        if not await self.is_get_endpoint_available(CORE_SERVICE_SEARCH_ENDPOINT):
            _LOGGER.debug("Service search endpoint unavailable")
            if return_none_when_unavailable:
                return None
            return []

        response = await self._safe_dict_get(CORE_SERVICE_SEARCH_ENDPOINT)
        return self._normalize_services_rows(response.get("rows") or [])

    @_log_errors
    async def get_services(self) -> list[dict[str, Any]]:
        """Return all manageable OPNsense services.

        Returns:
            list[dict[str, Any]]: Service rows from the core service search
                endpoint with a derived boolean ``status`` field indicating
                whether each service is currently running.
        """
        return await self._fetch_normalized_services(return_none_when_unavailable=False) or []

    async def _get_service_running_state(self, service: str) -> bool | None:
        """Return service running state with support for unknown endpoint state.

        Args:
            service (str): Service name or id as reported by OPNsense.

        Returns:
            bool | None: ``True`` when running, ``False`` when known not running,
                or ``None`` when status cannot be determined.
        """
        services: list[dict[str, Any]] | None = await self._fetch_normalized_services(
            return_none_when_unavailable=True
        )
        if services is None or not isinstance(services, list):
            return None
        for svc in services:
            if svc.get("name", None) == service or svc.get("id", None) == service:
                return bool(svc.get("status", False))
        return None

    @_log_errors
    async def get_service_is_running(self, service: str) -> bool:
        """Return whether a named OPNsense service is running.

        Args:
            service (str): Service name or service id as reported by OPNsense.

        Returns:
            bool: ``True`` when the service exists and is running; ``False``
                when it is stopped, unavailable, or cannot be found.
        """
        state = await self._get_service_running_state(service)
        return state is True

    async def _manage_service(self, action: str, service: str) -> bool:
        """Run a service control action for a named service.

        Args:
            action (str): Core service action to perform, such as ``start``,
                ``stop``, or ``restart``.
            service (str): Service name or id to encode into the control
                endpoint path.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        if not service:
            return False
        encoded_service = quote(service, safe="")
        api_addr: str = f"{CORE_SERVICE_ENDPOINT_PREFIX}{action}/{encoded_service}"
        response = await self._safe_dict_post(api_addr)
        _LOGGER.debug("[%s_service] service: %s, response: %s", action, service, response)
        return response.get("result", "failed") == "ok"

    @_log_errors
    async def start_service(self, service: str) -> bool:
        """Start an OPNsense service.

        Args:
            service (str): Service name as reported by OPNsense.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        return await self._manage_service("start", service)

    @_log_errors
    async def stop_service(self, service: str) -> bool:
        """Stop an OPNsense service.

        Args:
            service (str): Service name as reported by OPNsense.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        return await self._manage_service("stop", service)

    @_log_errors
    async def restart_service(self, service: str) -> bool:
        """Restart an OPNsense service.

        Args:
            service (str): Service name as reported by OPNsense.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        return await self._manage_service("restart", service)

    @_log_errors
    async def restart_service_if_running(self, service: str) -> bool:
        """Restart an OPNsense service only when it is currently running.

        Args:
            service (str): Service name as reported by OPNsense.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        state = await self._get_service_running_state(service)
        if state is None:
            _LOGGER.debug(
                "Service state unknown; refusing restart_service_if_running for %s", service
            )
            return False
        if state:
            return await self.restart_service(service)
        return True
