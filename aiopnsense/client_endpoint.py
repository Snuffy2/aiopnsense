"""Endpoint selection and availability helpers for OPNsenseClient."""

from collections.abc import MutableMapping
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any, Literal, cast
from warnings import deprecated

import aiohttp

from ._endpoint_outcome import EndpointOutcome
from ._error_policy import _error_if_throwing, _throws_errors
from ._typing import AiopnsenseClientProtocol
from .const import LEGACY_CAMELCASE_ENDPOINT_FIRMWARE
from .exceptions import OPNsenseUnknownFirmware
from .helpers import _LOGGER, _log_request_failure, _log_request_recovery, firmware_is_at_least


class ClientEndpointMixin:
    """Endpoint selection and availability methods for OPNsenseClient."""

    if TYPE_CHECKING:
        _endpoint_availability: dict[str, bool]
        _endpoint_cache_ttl_seconds: int
        _endpoint_checked_at: dict[str, datetime]
        _endpoint_permission_denied: set[str]
        _request_outages: set[tuple[str, str]]
        _password: str
        _rest_api_query_count: int
        _session: aiohttp.ClientSession
        _throw_errors: bool
        _url: str
        _use_snake_case: bool | None
        _username: str
        _verify_ssl: bool

        async def _do_get_outcome(
            self,
            path: str,
            caller: str = "Unknown",
            *,
            read_body: bool = True,
            response_format: Literal["json", "text"] = "json",
        ) -> EndpointOutcome:
            """Execute an immediate GET request and classify the response.

            Args:
                path (str): API endpoint path to request.
                caller (str): Calling method name used for diagnostics.
                read_body (bool): Whether to decode a successful response body.
                response_format (Literal["json", "text"], optional): Decoding mode for
                    a successful response body.

            Returns:
                EndpointOutcome: Classified transport response.
            """
            ...

        async def _queue_request(
            self,
            method: str,
            path: str,
            payload: MutableMapping[str, Any] | None = None,
        ) -> Any:
            """Queue one API request and return its result.

            Args:
                method (str): Internal request method handled by the queue worker.
                path (str): API endpoint path to request.
                payload (MutableMapping[str, Any] | None): Request body for POST requests.

            Returns:
                Any: Result set by the queue worker.
            """
            ...

    @deprecated("Endpoint style selection is internal. Direct calls are no longer needed.")
    async def set_use_snake_case(self, initial: bool = False) -> None:
        """Deprecated wrapper that preserves legacy ``initial`` compatibility.

        Args:
            initial (bool): Whether to preserve legacy initial-detection behavior.
        """
        await self._set_use_snake_case(initial=initial)

    async def _set_use_snake_case(self, initial: bool = False) -> None:
        """Set endpoint naming mode from the detected firmware version.

        Args:
            initial (bool): Whether to preserve the legacy unknown-firmware raise behavior.

        Returns:
            None: This method updates internal client state only.

        Raises:
            OPNsenseUnknownFirmware: Raised when ``initial`` is ``True`` and the firmware
                version cannot be compared reliably.
        """
        firmware_version = await cast(
            AiopnsenseClientProtocol,
            self,
        ).get_host_firmware_version()
        self._use_snake_case = True
        if firmware_version is None:
            _LOGGER.debug("Using snake_case endpoints because firmware version is unavailable")
            if initial:
                raise OPNsenseUnknownFirmware
            return
        uses_snake_case = firmware_is_at_least(firmware_version, LEGACY_CAMELCASE_ENDPOINT_FIRMWARE)
        if uses_snake_case is False:
            _LOGGER.debug(
                "Using camelCase endpoints for OPNsense < %s",
                LEGACY_CAMELCASE_ENDPOINT_FIRMWARE,
            )
            self._use_snake_case = False
        elif uses_snake_case is True:
            _LOGGER.debug(
                "Using snake_case endpoints for OPNsense >= %s",
                LEGACY_CAMELCASE_ENDPOINT_FIRMWARE,
            )
        else:
            _LOGGER.debug(
                "Unable to compare firmware version %s for endpoint style",
                firmware_version,
            )
            if initial:
                raise OPNsenseUnknownFirmware

    async def _get_endpoint_path(self, snake_case_path: str, camel_case_path: str) -> str:
        """Return the firmware-appropriate endpoint path.

        Args:
            snake_case_path (str): Endpoint path for newer snake_case firmware.
            camel_case_path (str): Endpoint path for older camelCase firmware.

        Returns:
            str: Selected endpoint path for the active firmware family.
        """
        if self._use_snake_case is None:
            await self._set_use_snake_case()
        # _get_endpoint_path treats _use_snake_case as a three-state flag:
        # None means _set_use_snake_case has not determined the endpoint style yet,
        # True selects snake_case, and False selects camelCase. Use "is not False"
        # so an indeterminate or newer-firmware value stays on the snake_case path.
        return snake_case_path if self._use_snake_case is not False else camel_case_path

    def _clear_endpoint_cache_entry(self, cache_key: str) -> None:
        """Clear all cached endpoint state for a method-aware key.

        Args:
            cache_key (str): Method-aware endpoint cache key to clear.
        """
        self._endpoint_permission_denied.discard(cache_key)
        self._endpoint_availability.pop(cache_key, None)
        self._endpoint_checked_at.pop(cache_key, None)

    async def _fetch_endpoint_outcome(
        self,
        path: str,
        response_format: Literal["json", "text"] = "json",
    ) -> EndpointOutcome:
        """Fetch a GET endpoint once and report whether it returned data, is absent or failed.

        Args:
            path (str): API endpoint path to request. The exact path, including
                its query string, is used for the availability cache key.
            response_format (Literal["json", "text"], optional): ``"text"`` returns the
                body text in the ``DATA`` outcome; ``"json"`` returns the decoded body.

        Returns:
            EndpointOutcome: The outcome described by ``_fetch_gated_outcome``. A 403 or a
                transient failure raises instead when ``self._throw_errors`` is ``True``.
        """
        return await self._fetch_gated_outcome("get", path, response_format=response_format)

    async def _fetch_post_endpoint_outcome(
        self,
        path: str,
        payload: MutableMapping[str, Any] | None = None,
    ) -> EndpointOutcome:
        """Send one POST with its payload and report whether it returned data, is absent or failed.

        The payload is the real request body; no payload-less probe is sent.

        Args:
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None): Request body sent with the POST.

        Returns:
            EndpointOutcome: The outcome described by ``_fetch_gated_outcome``. A 403 or a
                transient failure raises instead when ``self._throw_errors`` is ``True``.
        """
        return await self._fetch_gated_outcome("post", path, payload)

    async def _check_endpoint_status(
        self,
        path: str,
        *,
        force_refresh: bool = False,
    ) -> EndpointOutcome:
        """Report whether a GET endpoint is present, absent or failed, without reading its payload.

        Used for reads that are not a plain bounded fetch, such as streams and
        state-changing actions, where the caller needs the availability answer
        but must not consume the response. A dispatched check is a GET with no
        payload. Outcomes and cache rules are the same as ``_fetch_endpoint_outcome``.

        Args:
            path (str): API endpoint path to check. The exact path, including
                its query string, is used for the availability cache key.
            force_refresh (bool): Whether to bypass cached present or absent answers.
                The result of a dispatched request is cached.

        Returns:
            EndpointOutcome: ``DATA`` (with no payload) when the endpoint answers
                successfully, ``ABSENT`` for a 404 or a 403, or ``TRANSIENT`` for any
                other failure. A 403 or a transient failure raises the mapped
                ``OPNsenseError`` instead when ``self._throw_errors`` is ``True``.
        """
        return await self._fetch_gated_outcome(
            "get", path, status_only=True, force_refresh=force_refresh
        )

    async def _fetch_gated_outcome(
        self,
        method: Literal["get", "post"],
        path: str,
        payload: MutableMapping[str, Any] | None = None,
        *,
        response_format: Literal["json", "text"] = "json",
        status_only: bool = False,
        force_refresh: bool = False,
    ) -> EndpointOutcome:
        """Send one availability-gated request and report whether it returned data, is absent or failed.

        A dispatched request both checks availability and returns the data. The
        availability status (never the payload) is cached for the endpoint cache
        TTL, so a known-absent endpoint is answered without a request. Status-only
        checks also reuse a fresh present answer. GET and POST share this logic;
        the HTTP method and exact path, including query, distinguish cache entries.

        Args:
            method (Literal["get", "post"]): HTTP method of the data request.
            path (str): API endpoint path to request. The exact path, including
                its query string, is used for the availability cache key.
            payload (MutableMapping[str, Any] | None): Request body, sent with POST requests.
            response_format (Literal["json", "text"]): ``"text"`` returns the body text of
                a GET in the ``DATA`` outcome; ``"json"`` returns the decoded body.
            status_only (bool): Whether to check availability without reading the
                payload (GET only). Status checks bypass the request queue. A fresh
                cached present answer skips a status-only request; data fetches
                dispatch a request unless a fresh absent answer can be reused.
            force_refresh (bool): Whether to bypass cached present and absent answers.

        Returns:
            EndpointOutcome: ``DATA`` with the decoded payload or body text, ``ABSENT`` when the
                Router reports the endpoint missing (404) or not permitted (403), or
                ``TRANSIENT`` for any other failure. A 403 or a transient failure
                raises the mapped ``OPNsenseError`` instead when ``self._throw_errors``
                is ``True``; a 404 never raises.

        Raises:
            error: OPNsenseError raised for a 403 or a transient HTTP or transport
                failure when ``self._throw_errors`` is ``True``.

        Side Effects:
            Updates endpoint availability caches. A 200 caches the endpoint as present
            and a 404 as absent. Outside throw mode a 403 also caches the endpoint as
            absent with the permission-denied marker. Transient failures and, in throw
            mode, a 403 also clears the cache entry so the next call requests again.
        """
        cache_key = path
        if method == "post":
            cache_key = f"post:{cache_key}"
        now = datetime.now().astimezone()
        cache_is_fresh = (
            cache_key in self._endpoint_checked_at
            and (now - self._endpoint_checked_at[cache_key]).total_seconds()
            < self._endpoint_cache_ttl_seconds
        )
        if (
            not force_refresh
            and cache_is_fresh
            and self._endpoint_availability.get(cache_key) is False
            and not (_throws_errors(self) and cache_key in self._endpoint_permission_denied)
        ):
            return EndpointOutcome.absent()
        if (
            status_only
            and not force_refresh
            and cache_is_fresh
            and self._endpoint_availability.get(cache_key) is True
        ):
            return EndpointOutcome.data(None)

        if status_only:
            outcome: EndpointOutcome = await self._do_get_outcome(
                path,
                "_check_endpoint_status",
                read_body=False,
            )
        else:
            queue_method = (
                "get_text_outcome"
                if method == "get" and response_format == "text"
                else f"{method}_outcome"
            )
            outcome = await self._queue_request(queue_method, path, payload)

        if outcome.is_data:
            _log_request_recovery(self, method, path)
            self._endpoint_permission_denied.discard(cache_key)
            self._endpoint_availability[cache_key] = True
            self._endpoint_checked_at[cache_key] = now
            return outcome

        if outcome.is_absent and outcome.http_status != 403:
            _LOGGER.debug("Endpoint %s not found", path)
            self._endpoint_permission_denied.discard(cache_key)
            self._endpoint_availability[cache_key] = False
            self._endpoint_checked_at[cache_key] = now
            return outcome

        error = _error_if_throwing(self, outcome.error) if outcome.error is not None else None
        if outcome.is_absent:
            _log_request_failure(
                self,
                method,
                path,
                logging.ERROR,
                "Permission Error in fetch_endpoint_outcome. Path: %s. Ensure the OPNsense user connected to HA has appropriate access. Recommend full admin access",
                f"{self._url}{path}",
                error=error,
            )
            if error is not None:
                self._clear_endpoint_cache_entry(cache_key)
                raise error
            self._endpoint_permission_denied.add(cache_key)
            self._endpoint_availability[cache_key] = False
            self._endpoint_checked_at[cache_key] = now
            return outcome

        self._clear_endpoint_cache_entry(cache_key)
        _log_request_failure(
            self,
            method,
            path,
            logging.WARNING,
            "Transient %s failure for %s. %s. Not caching result.",
            method.upper(),
            path,
            outcome.detail,
            error=error,
        )
        if error is not None:
            raise error
        return outcome

    @deprecated("Endpoint availability probing is internal. Direct calls are no longer needed.")
    async def is_endpoint_available(self, path: str, force_refresh: bool = False) -> bool:
        """Backward-compatible alias that reports whether a GET endpoint is present.

        Args:
            path (str): API endpoint path to check.
            force_refresh (bool): Whether to bypass cached endpoint availability.

        Returns:
            bool: ``True`` only when the Router answers the endpoint successfully;
                ``False`` when it is absent or the check fails. In throw mode a 403
                or a transient failure raises instead of returning ``False``.
        """
        if not isinstance(path, str) or not path:
            return False
        outcome = await self._check_endpoint_status(path, force_refresh=force_refresh)
        return outcome.is_data
