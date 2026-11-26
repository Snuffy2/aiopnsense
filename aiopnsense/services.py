"""Service management methods for OPNsenseClient."""

from collections.abc import MutableMapping
from typing import Any
from urllib.parse import quote

from ._error_policy import _log_errors
from ._typing import AiopnsenseClientProtocol
from .helpers import _LOGGER, api_value_matches

CORE_SERVICE_SEARCH_ENDPOINT = "/api/core/service/search"
CORE_SERVICE_ENDPOINT_PREFIX = "/api/core/service/"


class ServicesMixin(AiopnsenseClientProtocol):
    """Service management methods for OPNsenseClient."""

    def _normalize_services_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize service rows and derive boolean status values.

        Args:
            rows (list[dict[str, Any]]): Raw ``rows`` list from the service-search endpoint.

        Returns:
            list[dict[str, Any]]: List of normalized service mappings.
        """
        normalized_services: list[dict[str, Any]] = []
        for service in rows:
            if not isinstance(service, MutableMapping):
                continue
            running = service.get("running", 0)
            service["status"] = api_value_matches(running, "1")
            normalized_services.append(service)
        return normalized_services

    async def _fetch_normalized_services(self) -> list[dict[str, Any]] | None:
        """Fetch and normalize services from the Router.

        Returns:
            list[dict[str, Any]] | None: Normalized rows from a mapping with a
                list-valued ``rows`` field, or an empty list on HTTP 404. A decoded
                response without that shape yields ``None`` in suppressed mode and
                an empty list in strict mode. Suppressed request failures yield
                ``None``. Strict mode propagates request errors.
        """
        outcome = await self._fetch_endpoint_outcome(CORE_SERVICE_SEARCH_ENDPOINT)
        rows = self._inventory_rows(
            outcome,
            field="rows",
            unavailable_message="Service search endpoint unavailable",
            malformed_message="[get_services] Discarding response without a service rows list",
        )
        return None if rows is None else self._normalize_services_rows(rows)

    @_log_errors
    async def get_services(self) -> list[dict[str, Any]] | None:
        """Return all manageable OPNsense services.

        Returns:
            list[dict[str, Any]] | None: Service rows from a mapping with a
                list-valued ``rows`` field, with a derived boolean ``status`` field
                indicating whether each service is running. Valid empty rows and
                HTTP 404 yield an empty list. With ``throw_errors=False``, a decoded
                response without the required shape, HTTP 403, other read failures,
                and undecodable responses yield ``None``. With ``throw_errors=True``,
                a decoded response without the required shape yields an empty list
                and request or decoding failures raise an ``OPNsenseError``.
        """
        return await self._fetch_normalized_services()

    async def _get_service_running_state(self, service: str) -> bool | None:
        """Return service running state with support for unknown endpoint state.

        Args:
            service (str): Service name or id as reported by OPNsense.

        Returns:
            bool | None: ``True`` when running, ``False`` when known not running,
                or ``None`` when status cannot be determined.
        """
        services = await self._fetch_normalized_services()
        if services is None:
            return None
        for svc in services:
            if svc.get("name") == service or svc.get("id") == service:
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
            service (str): Service name or composite ``name/id`` identifier to
                encode into the control endpoint path.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        if not service:
            return False
        service_name, separator, service_id = service.partition("/")
        encoded_service = quote(service_name, safe="")
        if separator:
            encoded_service = f"{encoded_service}/{quote(service_id, safe='')}"
        api_addr: str = f"{CORE_SERVICE_ENDPOINT_PREFIX}{action}/{encoded_service}"
        response = await self._safe_dict_post(api_addr)
        _LOGGER.debug("[%s_service] service: %s, response: %s", action, service, response)
        return response.get("result", "failed") == "ok"

    @_log_errors
    async def start_service(self, service: str) -> bool:
        """Start an OPNsense service.

        Args:
            service (str): Service name or composite ``name/id`` identifier as
                reported by OPNsense.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        return await self._manage_service("start", service)

    @_log_errors
    async def stop_service(self, service: str) -> bool:
        """Stop an OPNsense service.

        Args:
            service (str): Service name or composite ``name/id`` identifier as
                reported by OPNsense.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        return await self._manage_service("stop", service)

    @_log_errors
    async def restart_service(self, service: str) -> bool:
        """Restart an OPNsense service.

        Args:
            service (str): Service name or composite ``name/id`` identifier as
                reported by OPNsense.

        Returns:
            bool: True when the operation succeeds; otherwise, False.
        """
        return await self._manage_service("restart", service)

    @_log_errors
    async def restart_service_if_running(self, service: str) -> bool:
        """Restart an OPNsense service only when it is currently running.

        Args:
            service (str): Service name or composite ``name/id`` identifier as
                reported by OPNsense.

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
