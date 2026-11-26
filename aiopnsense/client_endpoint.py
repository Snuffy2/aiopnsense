"""Endpoint selection and availability helpers for OPNsenseClient."""

from collections.abc import Mapping, MutableMapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, cast
from warnings import deprecated

from ._endpoint_cache import _CachedAnswer, _EndpointCache
from ._endpoint_outcome import _EndpointOutcome
from ._error_policy import _raise_if_throwing, _throws_errors
from ._request_failure import _PROBE_PHASE, _REQUEST_PHASE
from ._styled_endpoint import _EndpointPath
from ._typing import AiopnsenseClientProtocol
from .client_transport import _public_caller_name, _RequestKind
from .const import SNAKE_CASE_ENDPOINTS_CAPABILITY
from .exceptions import OPNsenseUnknownFirmware
from .helpers import _LOGGER


class ClientEndpointMixin:
    """Endpoint selection and availability methods for OPNsenseClient."""

    if TYPE_CHECKING:
        _endpoint_cache: _EndpointCache
        _throw_errors: bool
        _url: str
        _use_snake_case: bool | None

        async def _do_get_outcome(
            self,
            path: str,
            caller: str = "Unknown",
            *,
            read_body: bool = True,
            response_format: Literal["json", "text"] = "json",
            phase: str = _REQUEST_PHASE,
        ) -> _EndpointOutcome:
            """Execute an immediate GET request and classify the response.

            Args:
                path (str): API endpoint path to request.
                caller (str): Calling method name used for diagnostics.
                read_body (bool): Whether to decode a successful response body.
                response_format (Literal["json", "text"], optional): Decoding mode for
                    a successful response body.
                phase (str): Request phase for outage logging.

            Returns:
                _EndpointOutcome: Classified transport response.
            """
            ...

        async def _queue_request(
            self,
            kind: _RequestKind,
            /,
            path: str,
            payload: MutableMapping[str, Any] | None = None,
            *,
            method: Literal["get", "post"] = "get",
            response_format: Literal["json", "text"] = "json",
        ) -> Any:
            """Queue one API request and return its result.

            Args:
                kind (_RequestKind): How the result is delivered.
                path (str): API endpoint path to request.
                payload (MutableMapping[str, Any] | None): Request body for POST requests.
                method (Literal["get", "post"]): HTTP method of the request.
                response_format (Literal["json", "text"]): Body format of an outcome GET.

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

        The mode is only stored once the firmware version is known and comparable.
        While the firmware is unknown the mode stays unresolved so a later call can
        resolve it.

        Args:
            initial (bool): Whether to preserve the legacy unknown-firmware raise behavior.

        Returns:
            None: This method updates internal client state only.

        Raises:
            OPNsenseUnknownFirmware: Raised when ``initial`` is ``True`` and the firmware
                version cannot be compared reliably.
        """
        uses_snake_case = await cast(
            AiopnsenseClientProtocol,
            self,
        )._firmware_capability(SNAKE_CASE_ENDPOINTS_CAPABILITY)
        if uses_snake_case is None:
            if initial:
                raise OPNsenseUnknownFirmware
            return
        self._use_snake_case = uses_snake_case
        _LOGGER.debug(
            "Using %s endpoints for OPNsense %s %s",
            "snake_case" if uses_snake_case else "camelCase",
            ">=" if uses_snake_case else "<",
            SNAKE_CASE_ENDPOINTS_CAPABILITY.minimum,
        )

    async def _resolve_endpoint(self, endpoint: _EndpointPath) -> str:
        """Return the firmware-appropriate path for an endpoint.

        Args:
            endpoint (_EndpointPath): A plain path, or a styled endpoint carrying both
                Endpoint Style spellings.

        Returns:
            str: A plain path unchanged, with no firmware read. For a styled endpoint,
                the spelling for the active firmware family. While the firmware is
                unknown this is the capability's unknown answer, which is not remembered.
        """
        if isinstance(endpoint, str):
            return endpoint
        if self._use_snake_case is None:
            await self._set_use_snake_case()
        # _use_snake_case is None until a known firmware version resolves it, so the
        # unknown-firmware answer is used per call without being stored.
        use_snake_case = (
            SNAKE_CASE_ENDPOINTS_CAPABILITY.unknown_answer
            if self._use_snake_case is None
            else self._use_snake_case
        )
        return endpoint.snake_case if use_snake_case else endpoint.camel_case

    def _reset_endpoint_state(self) -> None:
        """Discard the endpoint style and all cached endpoint availability.

        Called when the Router firmware version changes, because both were derived
        from the old version. Requests sent before the reset can no longer record or
        clear an answer.
        """
        self._use_snake_case = None
        self._endpoint_cache.reset()

    async def _fetch_endpoint_outcome(
        self,
        path: _EndpointPath,
        response_format: Literal["json", "text"] = "json",
        *,
        force_refresh: bool = False,
    ) -> _EndpointOutcome:
        """Fetch a GET endpoint once and report whether it returned data, is absent or failed.

        Args:
            path (_EndpointPath): API endpoint path to request, or a styled endpoint
                resolved here for the Router's firmware. The exact resolved path,
                including its query string, is used for the availability cache key.
            response_format (Literal["json", "text"], optional): ``"text"`` returns the
                body text in the ``DATA`` outcome; ``"json"`` returns the decoded body.
            force_refresh (bool): Whether to bypass a cached absent answer.

        Returns:
            _EndpointOutcome: The outcome described by ``_fetch_gated_outcome``. A 403 or a
                transient failure raises instead when ``self._throw_errors`` is ``True``.
        """
        # Resolve on the calling task, before the cache key, the cache generation and the
        # queue: resolving a styled endpoint can read the firmware, which may reset the
        # endpoint state and which must never wait on the request queue.
        return await self._fetch_gated_outcome(
            "get",
            await self._resolve_endpoint(path),
            response_format=response_format,
            force_refresh=force_refresh,
        )

    async def _fetch_post_endpoint_outcome(
        self,
        path: _EndpointPath,
        payload: MutableMapping[str, Any] | None = None,
    ) -> _EndpointOutcome:
        """Send one POST with its payload and report whether it returned data, is absent or failed.

        The payload is the real request body; no payload-less probe is sent.

        Args:
            path (_EndpointPath): API endpoint path to request, or a styled endpoint
                resolved here for the Router's firmware.
            payload (MutableMapping[str, Any] | None): Request body sent with the POST.

        Returns:
            _EndpointOutcome: The outcome described by ``_fetch_gated_outcome``. A 403 or a
                transient failure raises instead when ``self._throw_errors`` is ``True``.
        """
        return await self._fetch_gated_outcome("post", await self._resolve_endpoint(path), payload)

    async def _check_endpoint_status(
        self,
        path: str,
        *,
        force_refresh: bool = False,
    ) -> _EndpointOutcome:
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
            _EndpointOutcome: ``DATA`` (with no payload) when the endpoint answers
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
    ) -> _EndpointOutcome:
        """Send one availability-gated request and report whether it returned data, is absent or failed.

        A dispatched request both checks availability and returns the data. The
        availability status (never the payload) is cached for the endpoint cache
        TTL, which starts when the Router answers, so a known-absent endpoint is
        answered without a request. Status-only checks also reuse a fresh present
        answer. GET and POST share this logic; the HTTP method and exact path,
        including query, distinguish cache entries.

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
            _EndpointOutcome: ``DATA`` with the decoded payload or body text, ``ABSENT`` when the
                Router reports the endpoint missing (404) or not permitted (403), or
                ``TRANSIENT`` for any other failure. A 403 or a transient failure
                raises the mapped ``OPNsenseError`` instead when ``self._throw_errors``
                is ``True``; a 404 never raises.

        Side Effects:
            Updates endpoint availability caches. A 200 caches the endpoint as present
            and a 404 as absent. Outside throw mode a 403 also caches absence with the
            permission-denied marker. Transient failures and, in throw mode, a 403 clear
            the cache entry so the next call requests again. An undecodable 200 body
            propagates and leaves the cache unchanged. A reset of the endpoint cache
            while the request is in flight discards its answer.
        """
        cache_key = f"post:{path}" if method == "post" else path
        cached, ticket = self._endpoint_cache.lookup(cache_key, datetime.now().astimezone())
        if not force_refresh:
            if cached is _CachedAnswer.ABSENT or (
                cached is _CachedAnswer.PERMISSION_DENIED and not _throws_errors(self)
            ):
                return _EndpointOutcome.absent(
                    403 if cached is _CachedAnswer.PERMISSION_DENIED else 404
                )
            if cached is _CachedAnswer.PRESENT and status_only:
                return _EndpointOutcome.data(None)

        if status_only:
            outcome = await self._do_get_outcome(
                path, _public_caller_name(), read_body=False, phase=_PROBE_PHASE
            )
        else:
            outcome = await self._queue_request(
                _RequestKind.OUTCOME,
                path,
                payload,
                method=method,
                response_format=response_format,
            )

        # The TTL starts when the Router answers, so a slow request keeps its full TTL.
        checked_at = datetime.now().astimezone()
        if outcome.is_data:
            self._endpoint_cache.record(cache_key, _CachedAnswer.PRESENT, checked_at, ticket)
            return outcome
        if outcome.error is None:
            self._endpoint_cache.record(cache_key, _CachedAnswer.ABSENT, checked_at, ticket)
            return outcome
        if outcome.is_absent and not _throws_errors(self):
            self._endpoint_cache.record(
                cache_key, _CachedAnswer.PERMISSION_DENIED, checked_at, ticket
            )
            return outcome
        self._endpoint_cache.clear(cache_key, ticket)
        _raise_if_throwing(self, outcome.error)
        return outcome

    def _inventory_rows(
        self,
        outcome: _EndpointOutcome,
        *,
        field: str,
        unavailable_message: str,
        malformed_message: str,
    ) -> list[Any] | None:
        """Interpret the outcome of a read from a single inventory endpoint.

        Args:
            outcome (_EndpointOutcome): Result of the gated inventory request.
            field (str): Required list field in the decoded response.
            unavailable_message (str): Debug message for an unsuccessful request.
            malformed_message (str): Debug message for a response without the required list.

        Returns:
            list[Any] | None: Raw rows on success, an empty list for a missing
                endpoint (404) or a malformed reply in strict mode, and ``None``
                for suppressed failures or a malformed reply in default mode.
        """
        if not outcome.is_data:
            _LOGGER.debug(unavailable_message)
            return [] if outcome.http_status == 404 else None

        payload = outcome.payload
        rows = payload.get(field) if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            _LOGGER.debug(malformed_message)
            return [] if _throws_errors(self) else None
        return rows

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
