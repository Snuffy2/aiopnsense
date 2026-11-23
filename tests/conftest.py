"""Shared pytest fixtures for aiopnsense."""

import asyncio
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Coroutine,
    Generator,
    Mapping,
    MutableMapping,
)
import contextlib
from dataclasses import dataclass
from functools import partial
import json
from types import TracebackType
from typing import Any, Literal, Never, Self, cast
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from yarl import URL

import aiopnsense
from aiopnsense import client_queue
from aiopnsense._endpoint_outcome import _EndpointOutcome

MakeClientFactory = Callable[..., aiopnsense.OPNsenseClient]


@pytest.fixture
def legacy_dnsbl_payload() -> dict[str, Any]:
    """Return a reusable legacy Unbound DNSBL payload.

    Returns:
        dict[str, Any]: Legacy Unbound DNSBL payload returned by older OPNsense
        firmware.
    """
    return {
        "unbound": {
            "dnsbl": {
                "enabled": "0",
                "safesearch": "0",
                "nxdomain": "1",
                "address": "0.0.0.0",
                "type": {},
                "lists": {},
                "whitelists": {},
                "blocklists": {},
                "wildcards": {},
            }
        }
    }


class FakeClientSession:
    """Minimal fake aiohttp session for unit tests."""

    async def __aenter__(self) -> Self:
        """Enter async context.

        Returns:
            Self: The context-managed instance.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """Exit async context.

        Args:
            exc_type (type[BaseException] | None): Exception type raised in
                async context teardown.
            exc (BaseException | None): Exception instance raised in async
                context teardown.
            tb (TracebackType | None): Traceback associated with async context
                teardown.

        Returns:
            bool: False so exceptions are not suppressed.
        """
        await self.close()
        return False

    async def close(self) -> bool:
        """Close the fake session.

        Returns:
            bool: True when close succeeds.
        """
        return True


@dataclass(slots=True)
class _FakeRequestInfo:
    """Minimal request_info replacement for aiohttp response stubs."""

    real_url: URL


class FakeStreamContent:
    """Stream payload iterator used by fake HTTP response objects."""

    def __init__(self, payload: list[bytes]) -> None:
        """Initialize the content iterator.

        Args:
            payload (list[bytes]): Chunk payload returned by ``iter_chunked``.
        """
        self._payload = payload

    async def iter_chunked(self, _chunk_size: int) -> AsyncGenerator[bytes]:
        """Yield payload chunks for stream parsing tests.

        Args:
            _chunk_size (int): Requested chunk size (unused by the fake implementation).

        Yields:
            bytes: Next payload chunk.
        """
        for chunk in self._payload:
            yield chunk


class FakeResponse:
    """Reusable aiohttp response context-manager stub for tests."""

    def __init__(
        self,
        *,
        status: int = 200,
        reason: str = "OK",
        ok: bool = True,
        json_payload: Any = None,
        text_payload: str = "",
        stream_chunks: list[bytes] | None = None,
        include_request_info: bool = False,
        request_url: str = "http://localhost",
        headers: MutableMapping[str, Any] | None = None,
        history: list[Any] | None = None,
    ) -> None:
        """Initialize a fake HTTP response.

        Args:
            status (int): HTTP status code.
            reason (str): HTTP reason phrase.
            ok (bool): Whether the response should be treated as successful.
            json_payload (Any): Payload emitted by the fake ``json()`` method.
            text_payload (str): Text emitted by the fake ``text()`` method.
            stream_chunks (list[bytes] | None): Payload yielded by ``content.iter_chunked``.
            include_request_info (bool): Whether to include minimal ``request_info`` metadata.
            request_url (str): URL exposed via ``request_info.real_url``.
            headers (MutableMapping[str, Any] | None): HTTP headers for error construction.
            history (list[Any] | None): Redirect history for error construction.
        """
        self.status = status
        self.reason = reason
        self.ok = ok
        self._json_payload = json_payload
        self._text_payload = text_payload
        self._stream_chunks = list(stream_chunks or [])
        self.headers = dict(headers or {})
        self.history = list(history or [])
        self.request_info: _FakeRequestInfo | None = None

        if include_request_info:
            self.request_info = _FakeRequestInfo(real_url=URL(request_url))

    async def __aenter__(self) -> Self:
        """Enter the asynchronous context.

        Returns:
            Self: The context-managed instance.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """Exit the asynchronous context.

        Args:
            exc_type (type[BaseException] | None): Exception type raised during context usage.
            exc (BaseException | None): Exception instance raised during context usage.
            tb (TracebackType | None): Traceback for any raised exception.

        Returns:
            bool: ``False`` so exceptions are not suppressed.
        """
        return False

    async def json(self, content_type: Any = None) -> Any:
        """Return a preconfigured JSON payload.

        Args:
            content_type (Any, optional): Requested content type (unused by the fake).

        Returns:
            Any: Mock JSON payload.
        """
        del content_type
        return self._json_payload

    async def text(self) -> str:
        """Return a preconfigured text payload.

        Returns:
            str: Mock text payload.
        """
        return self._text_payload

    @property
    def content(self) -> FakeStreamContent:
        """Return stream content iterator wrapper.

        Returns:
            FakeStreamContent: Content wrapper used by stream tests.
        """
        return FakeStreamContent(self._stream_chunks)


class MalformedJSONResponse(FakeResponse):
    """Successful response whose body is not valid JSON."""

    async def json(self, content_type: Any = None) -> Never:
        """Raise the error aiohttp raises for a body that is not valid JSON.

        Args:
            content_type (Any, optional): Requested content type (unused by the fake).

        Returns:
            Never: This method always raises.

        Raises:
            json.JSONDecodeError: Always.
        """
        del content_type
        raise json.JSONDecodeError("Expecting value", "<html>", 0)


FakeStreamResponseFactory = Callable[[list[bytes]], FakeResponse]


def make_mock_session_client(
    make_client: Callable[..., aiopnsense.OPNsenseClient],
    *,
    username: str = "u",
    password: str = "p",
    url: str = "http://localhost",
) -> tuple[aiopnsense.OPNsenseClient, MagicMock]:
    """Build a client backed by a shared MagicMock aiohttp session.

    Args:
        make_client (Callable[..., aiopnsense.OPNsenseClient]): ``make_client`` fixture factory.
        username (str): Client username.
        password (str): Client password.
        url (str): Base URL of the OPNsense instance.

    Returns:
        tuple[aiopnsense.OPNsenseClient, MagicMock]: Created client and mocked session.
    """
    session = MagicMock(spec=aiohttp.ClientSession)
    return make_client(session=session, username=username, password=password, url=url), session


FIRMWARE_STATUS_PATH = "/api/core/firmware/status"


class FakeRouter:
    """Fake Router that answers HTTP requests at the aiohttp session boundary.

    Responses come from ``routes`` (keyed by method and request path, query
    included) or, when no route matches, from the ordered ``get_payloads`` and
    ``post_payloads`` queues. A route or queue value is turned into a response
    this way: an exception instance is raised by the session call, an ``int`` is
    an HTTP error status, a ``str`` is a 200 text body, a ``FakeResponse`` is used
    as is, and anything else is a 200 JSON body. With nothing configured the
    Router answers 200 with an empty JSON object. Tests may change ``routes``
    between calls to model the Router changing.
    """

    def __init__(
        self,
        client: aiopnsense.OPNsenseClient,
        *,
        get_payloads: list[Any] | None = None,
        post_payloads: list[Any] | None = None,
        routes: MutableMapping[tuple[str, str], Any] | None = None,
    ) -> None:
        """Initialize the fake Router.

        Args:
            client (aiopnsense.OPNsenseClient): Client whose requests this Router answers.
            get_payloads (list[Any] | None): Ordered values answering GET requests without a route.
            post_payloads (list[Any] | None): Ordered values answering POST requests without a route.
            routes (MutableMapping[tuple[str, str], Any] | None): Values keyed by
                ``(method, path)``.
        """
        self._client = client
        self.requests: list[tuple[str, str, Any]] = []
        self.routes: dict[tuple[str, str], Any] = {
            (method.upper(), path): value for (method, path), value in (routes or {}).items()
        }
        self._queues: dict[str, list[Any]] = {
            "GET": list(get_payloads or []),
            "POST": list(post_payloads or []),
        }

    def count(self, method: str | None = None, path: str | None = None) -> int:
        """Count recorded requests.

        Args:
            method (str | None): HTTP method to match, or ``None`` for any method.
            path (str | None): Request path (query included) to match, or ``None`` for any path.

        Returns:
            int: Number of matching requests.
        """
        return sum(
            1
            for request_method, url, _body in self.requests
            if (method is None or request_method == method.upper())
            and (path is None or url.removeprefix(self._client._url) == path)
        )

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        """Record one request and return the configured response.

        Args:
            method (str): HTTP method of the request.
            url (str): Full requested URL.
            kwargs (Any): Request parameters passed by the HTTP transport.

        Returns:
            FakeResponse: Configured response for the request.

        Raises:
            value: Configured transport error for this request.
        """
        self.requests.append((method, url, kwargs.get("json")))
        path = url.removeprefix(self._client._url)
        queue = self._queues[method]
        if (method, path) in self.routes:
            value = self.routes[(method, path)]
        else:
            value = queue.pop(0) if queue else {}
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, FakeResponse):
            return value
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, str):
                return FakeResponse(text_payload=value)
            return FakeResponse(json_payload=value)
        return FakeResponse(status=value, reason="ERR", ok=value < 400)


def install_fake_router(
    client: aiopnsense.OPNsenseClient,
    session: Any,
    *,
    get_payloads: list[Any] | None = None,
    post_payloads: list[Any] | None = None,
    routes: MutableMapping[tuple[str, str], Any] | None = None,
) -> FakeRouter:
    """Back a client with a fake Router and run its queued requests immediately.

    Queue worker tasks are stubbed in most test modules, so queued requests are
    dispatched straight to the transport methods the worker would call. The test
    then exercises the real transport, endpoint cache and public methods against
    the fake session.

    Args:
        client (aiopnsense.OPNsenseClient): Client to configure.
        session (Any): Mock aiohttp session returned by ``make_mock_session_client``.
        get_payloads (list[Any] | None): Ordered values answering GET requests without a route.
        post_payloads (list[Any] | None): Ordered values answering POST requests without a route.
        routes (MutableMapping[tuple[str, str], Any] | None): Values keyed by ``(method, path)``.

    Returns:
        FakeRouter: Fake Router recording every request the client sends.
    """
    router = FakeRouter(
        client, get_payloads=get_payloads, post_payloads=post_payloads, routes=routes
    )

    get = partial(router.request, "GET")
    post = partial(router.request, "POST")

    async def queue_immediately(
        method: str, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> Any:
        """Run a queued request through its transport method without a worker.

        Args:
            method (str): Internal request method handled by the queue worker.
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None): Request body for POST requests.

        Returns:
            Any: Result the queue worker would set.

        Raises:
            ValueError: If the requested operation is not supported by the fake router.
        """
        caller = "fake router"
        if method == "get":
            return await client._do_get(path, caller)
        if method == "get_outcome":
            return await client._do_get_outcome(path, caller)
        if method == "get_text_outcome":
            return await client._do_get_outcome(path, caller, response_format="text")
        if method == "post":
            return await client._do_post(path, payload, caller)
        if method == "post_outcome":
            return await client._do_post_outcome(path, payload, caller)
        if method == "get_from_stream":
            return await client._do_get_from_stream(path, caller)
        raise ValueError(f"Unknown fake-router queue method: {method}")

    session.get = get
    session.post = post
    client._queue_request = queue_immediately
    return router


def stub_endpoint_outcome(
    client: aiopnsense.OPNsenseClient,
    outcome: _EndpointOutcome,
    *,
    read: Literal["get", "get_text", "post", "status"] = "get",
) -> AsyncMock:
    """Replace one endpoint outcome read on a client with a stub returning ``outcome``.

    Use this in place of stubbing the probe and the fetch separately. The stub answers
    the read the public method makes, so ``outcome`` decides the result: ``_EndpointOutcome.data(payload)``
    for data, ``_EndpointOutcome.absent()`` for a missing endpoint, or
    ``_EndpointOutcome.transient(error)`` for a transient failure.

    Args:
        client (aiopnsense.OPNsenseClient): Client whose read is stubbed.
        outcome (_EndpointOutcome): Outcome that the stubbed read returns.
        read (Literal["get", "get_text", "post", "status"]): Read to stub. ``get`` and
            ``get_text`` replace ``_fetch_endpoint_outcome``, ``post`` replaces
            ``_fetch_post_endpoint_outcome`` and ``status`` replaces ``_check_endpoint_status``.

    Returns:
        AsyncMock: The stub, so a test can assert the path it was awaited with.
    """
    attributes = {
        "get": "_fetch_endpoint_outcome",
        "get_text": "_fetch_endpoint_outcome",
        "post": "_fetch_post_endpoint_outcome",
        "status": "_check_endpoint_status",
    }
    stub = AsyncMock(return_value=outcome)
    setattr(client, attributes[read], stub)
    return stub


class FirmwareRouter:
    """Router stand-in that scripts firmware status and endpoint responses for a client.

    Probes go to the mocked session and data requests go to the client's transport
    methods, so public client methods run unchanged against one scripted Router.

    Attributes:
        firmware (str | None): Version reported by the firmware status endpoint, or
            ``None`` for a status payload without a version.
        firmware_reachable (bool): Whether the firmware status endpoint answers.
        endpoints (dict[str, Any]): JSON payload served for each path that exists.
        get_paths (list[str]): Paths requested with GET, in order.
        post_paths (list[str]): Paths requested with POST, in order.
        probe_paths (list[str]): Paths probed for availability, in order.
    """

    def __init__(self, client: aiopnsense.OPNsenseClient, session: MagicMock) -> None:
        """Wire the client and session to this scripted Router.

        Args:
            client (aiopnsense.OPNsenseClient): Client whose transport is scripted.
            session (MagicMock): Mocked aiohttp session used for availability probes.
        """
        self._base_url = client._url
        self.firmware: str | None = None
        self.firmware_reachable = True
        self.endpoints: dict[str, Any] = {}
        self.get_paths: list[str] = []
        self.post_paths: list[str] = []
        self.probe_paths: list[str] = []
        session.get.side_effect = self._probe
        client._get = self._get  # type: ignore[method-assign]
        client._post = self._post  # type: ignore[method-assign]
        client._do_post = self._do_post  # type: ignore[method-assign]
        client._queue_request = self._queue_outcome_read  # type: ignore[method-assign]

    def _payload(self, path: str) -> Any:
        """Return the scripted JSON payload for a path.

        Args:
            path (str): Request path.

        Returns:
            Any: Firmware status payload, the configured endpoint payload, or ``None``.
        """
        if path == FIRMWARE_STATUS_PATH:
            if not self.firmware_reachable:
                return None
            return {"product": {"product_version": self.firmware}} if self.firmware else {}
        return self.endpoints.get(path)

    def _probe(self, url: str, **_kwargs: Any) -> FakeResponse:
        """Answer an availability probe.

        Args:
            url (str): Full probe URL.
            **_kwargs (Any): Ignored aiohttp request options.

        Returns:
            FakeResponse: ``200`` when the path exists, otherwise ``404``.

        Raises:
            aiohttp.ClientConnectionError: The firmware status endpoint is unreachable.
        """
        path = url.removeprefix(self._base_url)
        self.probe_paths.append(path)
        if path == FIRMWARE_STATUS_PATH and not self.firmware_reachable:
            raise aiohttp.ClientConnectionError("router unreachable")
        exists = path == FIRMWARE_STATUS_PATH or path in self.endpoints
        return FakeResponse(
            status=200 if exists else 404, reason="OK" if exists else "Not Found", ok=exists
        )

    async def _get(self, path: str) -> Any:
        """Serve a GET request.

        Args:
            path (str): Request path.

        Returns:
            Any: Scripted payload for the path.
        """
        self.get_paths.append(path)
        return self._payload(path)

    async def _post(self, path: str, payload: MutableMapping[str, Any] | None = None) -> Any:
        """Serve a POST request.

        Args:
            path (str): Request path.
            payload (MutableMapping[str, Any] | None): Ignored request body.

        Returns:
            Any: Scripted payload for the path.
        """
        del payload
        self.post_paths.append(path)
        return self._payload(path)

    async def _queue_outcome_read(
        self, method: str, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> _EndpointOutcome:
        """Answer a queued endpoint read from the scripted endpoints.

        Args:
            method (str): Queue operation requested by the client.
            path (str): API endpoint path requested.
            payload (MutableMapping[str, Any] | None): Ignored request body.

        Returns:
            _EndpointOutcome: Scripted payload for the path, or an absent endpoint.

        Raises:
            ValueError: The client requests a queued operation other than a GET outcome.
        """
        if method not in ("get_outcome", "get_text_outcome"):
            raise ValueError(f"Unexpected scripted Router operation: {method}")
        del payload
        reply = await self._get(path)
        return _EndpointOutcome.absent() if reply is None else _EndpointOutcome.data(reply)

    async def _do_post(
        self, path: str, payload: MutableMapping[str, Any] | None = None, caller: str = "Unknown"
    ) -> Any:
        """Serve a direct POST request that bypasses the request queue.

        Args:
            path (str): Request path.
            payload (MutableMapping[str, Any] | None): Ignored request body.
            caller (str): Ignored caller label.

        Returns:
            Any: Scripted payload for the path.
        """
        del caller
        return await self._post(path, payload)


@pytest.fixture
def firmware_router(
    make_client: Callable[..., aiopnsense.OPNsenseClient],
) -> tuple[aiopnsense.OPNsenseClient, FirmwareRouter]:
    """Return a client wired to a scripted Router.

    Args:
        make_client (Callable[..., aiopnsense.OPNsenseClient]): ``make_client`` fixture factory.

    Returns:
        tuple[aiopnsense.OPNsenseClient, FirmwareRouter]: Client and the Router that scripts it.
    """
    client, session = make_mock_session_client(make_client)
    return client, FirmwareRouter(client, session)


@dataclass(frozen=True, slots=True)
class FirmwareVersionShape:
    """A firmware version as a Router reports it, with its expected handling.

    Expected values are written by hand so that they do not depend on the code under test.

    Attributes:
        id (str): Readable pytest case name.
        product_version (str | None): ``product.product_version`` in the status payload.
        product_series (str | None): ``product.product_series`` in the status payload.
        trimmed (str | None): Version left after removing the hotfix suffix.
        at_least (Mapping[str, bool | None]): Expected ``firmware_is_at_least`` result
            for ``product_version`` against each minimum, ``None`` when not comparable.
        lookup_version (str | None): Version returned by the firmware lookup.
        cached (bool): Whether the lookup keeps that version for later calls.
    """

    id: str
    product_version: str | None
    product_series: str | None
    trimmed: str | None
    at_least: Mapping[str, bool | None]
    lookup_version: str | None
    cached: bool

    @property
    def version_is_comparable(self) -> bool:
        """Return whether the reported product version itself can be compared.

        Returns:
            bool: ``True`` when no expected minimum comparison is ``None``.
        """
        return all(result is not None for result in self.at_least.values())


def _minimums(
    at_least_26_1_11: bool | None,
    at_least_26_7: bool | None,
    at_least_26_7_6: bool | None,
    at_least_27_1: bool | None,
) -> dict[str, bool | None]:
    """Label expected results with the minimum versions they were written against.

    Args:
        at_least_26_1_11 (bool | None): Expected result against ``26.1.11``.
        at_least_26_7 (bool | None): Expected result against ``26.7``.
        at_least_26_7_6 (bool | None): Expected result against ``26.7.6``.
        at_least_27_1 (bool | None): Expected result against ``27.1``.

    Returns:
        dict[str, bool | None]: Expected result for each minimum version.
    """
    return {
        "26.1.11": at_least_26_1_11,
        "26.7": at_least_26_7,
        "26.7.6": at_least_26_7_6,
        "27.1": at_least_27_1,
    }


_NOT_COMPARABLE = _minimums(None, None, None, None)

# Release and hotfix forms come from a live Router and its OPNsense changelogs. The beta is
# the `opnsense-devel` package version, and its series is inferred from that version. A beta
# cannot be compared with a release, so the lookup uses its product series. A release
# candidate compares as older than its final release. The last entry is the error status
# OPNsense returns without product data.
FIRMWARE_VERSION_SHAPES: tuple[FirmwareVersionShape, ...] = (
    FirmwareVersionShape(
        id="series-release",
        product_version="26.7",
        product_series="26.7",
        trimmed="26.7",
        at_least=_minimums(True, True, False, False),
        lookup_version="26.7",
        cached=True,
    ),
    FirmwareVersionShape(
        id="patch-release",
        product_version="26.7.6",
        product_series="26.7",
        trimmed="26.7.6",
        at_least=_minimums(True, True, True, False),
        lookup_version="26.7.6",
        cached=True,
    ),
    FirmwareVersionShape(
        id="series-release-hotfix",
        product_version="26.1_4",
        product_series="26.1",
        trimmed="26.1",
        at_least=_minimums(False, False, False, False),
        lookup_version="26.1_4",
        cached=True,
    ),
    FirmwareVersionShape(
        id="patch-hotfix",
        product_version="26.7.6_3",
        product_series="26.7",
        trimmed="26.7.6",
        at_least=_minimums(True, True, True, False),
        lookup_version="26.7.6_3",
        cached=True,
    ),
    FirmwareVersionShape(
        id="double-digit-hotfix",
        product_version="26.1.11_10",
        product_series="26.1",
        trimmed="26.1.11",
        at_least=_minimums(True, False, False, False),
        lookup_version="26.1.11_10",
        cached=True,
    ),
    FirmwareVersionShape(
        id="release-candidate",
        product_version="26.7.r1",
        product_series="26.7",
        trimmed="26.7.r1",
        at_least=_minimums(True, False, False, False),
        lookup_version="26.7.r1",
        cached=True,
    ),
    FirmwareVersionShape(
        id="release-candidate-hotfix",
        product_version="26.7.r2_3",
        product_series="26.7",
        trimmed="26.7.r2",
        at_least=_minimums(True, False, False, False),
        lookup_version="26.7.r2_3",
        cached=True,
    ),
    FirmwareVersionShape(
        id="beta-build",
        product_version="27.1.b_21",
        product_series="27.1",
        trimmed="27.1.b",
        at_least=_NOT_COMPARABLE,
        lookup_version="27.1",
        cached=True,
    ),
    FirmwareVersionShape(
        id="missing-product",
        product_version=None,
        product_series=None,
        trimmed=None,
        at_least=_NOT_COMPARABLE,
        lookup_version=None,
        cached=False,
    ),
)

# Comparable versions from oldest to newest, so every later entry is newer than every
# earlier one. Hotfixes 2, 8 and 11 check that revisions sort as numbers.
FIRMWARE_RELEASE_ORDER: tuple[str, ...] = (
    "26.1.r2",
    "26.1.r2_2",
    "26.1",
    "26.1_4",
    "26.1.1",
    "26.1.11",
    "26.1.11_10",
    "26.7.r1",
    "26.7.r2",
    "26.7.r2_3",
    "26.7",
    "26.7.1_1",
    "26.7.3_2",
    "26.7.3_8",
    "26.7.3_11",
    "26.7.4_1",
    "26.7.6",
    "26.7.6_3",
    "26.7.7",
    "26.7.9_3",
)


@pytest.fixture(autouse=True)
def _patch_asyncio_create_task(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Stub background workers and reject requests that would wait for them.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture used to replace ``asyncio.create_task``.
        request (pytest.FixtureRequest): Current test request used to select queue-specific tests.
    """

    if request.node.fspath and request.node.fspath.basename in {
        "test_client_base.py",
        "test_client_queue.py",
    }:
        return

    original_create_task = asyncio.create_task

    class _DummyTask:
        def cancel(self) -> bool:
            """Cancel.

            Returns:
                bool: True when cancellation succeeds.
            """
            return True

        def done(self) -> bool:
            """Done.

            Returns:
                bool: Whether the dummy task is complete.
            """
            return False

        def __await__(self) -> Generator[None]:
            """Await.

            Returns:
                Generator[None]: Iterator used by the await
                    protocol.
            """
            if False:
                yield

    def _fake_create_task(
        coro: Coroutine[Any, Any, Any], *args: Any, **kwargs: Any
    ) -> asyncio.Task[Any] | _DummyTask:
        """Fake create task.

        Args:
            coro (Coroutine[Any, Any, Any]): Coroutine wrapped by the logging
                decorator.
            args (Any): Positional arguments accepted by `_fake_create_task`.
            kwargs (Any): Keyword arguments accepted by `_fake_create_task`.

        Returns:
            asyncio.Task[Any] | _DummyTask: Real task for non-aiopnsense
                coroutines, otherwise a dummy task.
        """
        qualname = getattr(coro, "__qualname__", "")
        module = getattr(coro, "__module__", "")
        if "aiopnsense" in module or "_process_queue" in qualname:
            with contextlib.suppress(Exception):
                coro.close()
            return _DummyTask()
        return original_create_task(coro, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_task", _fake_create_task)

    queue_request = aiopnsense.OPNsenseClient._queue_request
    queue_post = aiopnsense.OPNsenseClient._queue_post_with_cancellation

    def workers_are_stubbed(client: aiopnsense.OPNsenseClient) -> bool:
        """Detect tests that restore task creation or start a real worker explicitly.

        Args:
            client (aiopnsense.OPNsenseClient): Client sending the request.

        Returns:
            bool: Whether the fixture prevents the client from getting a worker.
        """
        return asyncio.create_task is _fake_create_task and not any(
            isinstance(worker, asyncio.Task) and not worker.done() for worker in client._workers
        )

    def fail_unanswered_request(method: str, path: str) -> Never:
        """Report an unanswered request without invoking production error handling.

        Args:
            method (str): Queued operation name.
            path (str): API endpoint path to request.

        Returns:
            Never: Always fails the test.
        """
        http_method = method.partition("_")[0].upper()
        pytest.fail(
            f"queued {http_method} {path} was not answered: stub the request or use a fake Router"
        )

    async def guard_queued_request(
        self: aiopnsense.OPNsenseClient,
        method: str,
        path: str,
        payload: MutableMapping[str, Any] | None = None,
    ) -> Any:
        """Fail a request that reached the queue without a test double.

        Args:
            self (aiopnsense.OPNsenseClient): Client sending the request.
            method (str): Queued operation name.
            path (str): API endpoint path.
            payload (MutableMapping[str, Any] | None): Request body, if any.

        Returns:
            Any: The queued response when the test restores real worker tasks.
        """
        if not workers_are_stubbed(self):
            return await queue_request(self, method, path, payload)
        fail_unanswered_request(method, path)

    async def guard_queued_post(
        self: aiopnsense.OPNsenseClient,
        path: str,
        payload: MutableMapping[str, Any] | None = None,
    ) -> tuple[MutableMapping[str, Any] | list | None, asyncio.CancelledError | None]:
        """Apply the same guard to the separate cancellable POST entry point.

        Args:
            self (aiopnsense.OPNsenseClient): Client sending the request.
            path (str): API endpoint path.
            payload (MutableMapping[str, Any] | None): Request body, if any.

        Returns:
            tuple[MutableMapping[str, Any] | list | None, asyncio.CancelledError | None]:
                The queued response and cancellation when real workers are restored.
        """
        if not workers_are_stubbed(self):
            return await queue_post(self, path, payload)
        fail_unanswered_request("post", path)

    monkeypatch.setattr(aiopnsense.OPNsenseClient, "_queue_request", guard_queued_request)
    monkeypatch.setattr(
        aiopnsense.OPNsenseClient, "_queue_post_with_cancellation", guard_queued_post
    )


@pytest.fixture
def fake_response_factory() -> Callable[..., FakeResponse]:
    """Return a factory that constructs reusable fake HTTP responses.

    Returns:
        Callable[..., FakeResponse]: Factory for configurable fake HTTP responses.
    """

    def _make(
        *,
        status: int = 200,
        reason: str = "OK",
        ok: bool = True,
        json_payload: Any = None,
        text_payload: str = "",
        chunks: list[bytes] | None = None,
        include_request_info: bool = False,
    ) -> FakeResponse:
        """Build a reusable fake HTTP response object.

        Args:
            status (int): Status value to evaluate or normalize.
            reason (str): Expected failure reason text.
            ok (bool): Mock HTTP success flag for test responses.
            json_payload (Any): JSON payload returned by ``response.json()``.
            text_payload (str): Text payload returned by ``response.text()``.
            chunks (list[bytes] | None): Stream payload for ``response.content.iter_chunked``.
            include_request_info (bool): Whether to include minimal request metadata.

        Returns:
            FakeResponse: Mock response object.
        """
        return FakeResponse(
            status=status,
            reason=reason,
            ok=ok,
            json_payload=json_payload,
            text_payload=text_payload,
            stream_chunks=chunks,
            include_request_info=include_request_info,
        )

    return _make


@pytest.fixture
def fake_stream_response_factory(
    fake_response_factory: Callable[..., FakeResponse],
) -> Callable[..., FakeResponse]:
    """Return a factory that constructs fake streaming responses.

    Args:
        fake_response_factory (Callable[..., FakeResponse]): Base response factory wrapped for stream tests.

    Returns:
        Callable[..., FakeResponse]: Factory for fake responses with byte-stream content.
    """

    def _make(
        chunks: list[bytes],
        status: int = 200,
        reason: str = "OK",
        *,
        ok: bool = True,
    ) -> FakeResponse:
        """Build a fake streaming response.

        Args:
            chunks (list[bytes]): Mock stream chunks returned by iter_chunked.
            status (int): Status value to evaluate or normalize.
            reason (str): Expected failure reason text.
            ok (bool): Mock HTTP success flag for test responses.

        Returns:
            FakeResponse: Mock response object.
        """
        return fake_response_factory(status=status, reason=reason, ok=ok, chunks=chunks)

    return _make


@pytest.fixture
async def make_client() -> AsyncGenerator[Callable[..., aiopnsense.OPNsenseClient]]:
    """Return a factory that constructs an OPNsenseClient for tests.

    Yields:
        Callable[..., aiopnsense.OPNsenseClient]: Factory that creates clients and registers them for cleanup.
    """

    clients: list[aiopnsense.OPNsenseClient] = []

    def _make(
        session: aiohttp.ClientSession | None = None,
        username: str = "u",
        password: str = "p",
        url: str = "http://localhost",
        **client_kwargs: Any,
    ) -> aiopnsense.OPNsenseClient:
        """Create an OPNsense test client with a fake session by default.

        Args:
            session (aiohttp.ClientSession | None): HTTP client session used for API requests.
            username (str): Username for API authentication.
            password (str): Password for API authentication.
            url (str): Base URL of the OPNsense instance.
            client_kwargs (Any): Keyword arguments accepted by `_make`.

        Returns:
            aiopnsense.OPNsenseClient: Configured client instance using the
                supplied credentials, URL, session, and extra constructor
                options.
        """
        if session is None:
            session = cast("aiohttp.ClientSession", FakeClientSession())
        client = aiopnsense.OPNsenseClient(
            url=url,
            username=username,
            password=password,
            session=session,
            **client_kwargs,
        )
        clients.append(client)
        return client

    try:
        yield _make
    finally:
        for client in clients:
            with contextlib.suppress(Exception):
                await client.async_close()


type RouterReply = MutableMapping[str, Any] | list | BaseException | None
"""A scripted Router reply: a JSON body, ``None`` for a transport failure, or an error to raise."""


@dataclass(frozen=True, slots=True)
class RouterRequest:
    """One request the client sent to the scripted Router."""

    method: str
    path: str
    payload: MutableMapping[str, Any] | None = None


class ScriptedRouter:
    """Router stand-in that replaces a client's ``_post`` and ``_get`` transport.

    The cancellation-aware queued POST that VPN Toggles use is answered from the same
    script. Replies are scripted per ``(method, path)``. An unscripted request is recorded and
    answered with ``None``, which is what the transport returns for a failed request
    when ``throw_errors`` is off. A scripted exception is raised from the transport, as
    a failed request does when ``throw_errors`` is on.
    """

    def __init__(self, make_client: MakeClientFactory) -> None:
        """Initialize an empty script.

        Args:
            make_client (MakeClientFactory): Client factory used by :meth:`client`.
        """
        self._make_client = make_client
        self._replies: dict[tuple[str, str], RouterReply] = {}
        self.requests: list[RouterRequest] = []

    def reply(self, path: str, response: RouterReply, *, method: str = "POST") -> None:
        """Script the Router's reply to one request.

        Args:
            path (str): API path the reply answers.
            response (RouterReply): JSON body, ``None`` for a transport failure, or an
                exception for the transport to raise.
            method (str): HTTP method, ``POST`` or ``GET``.
        """
        self._replies[(method, path)] = response

    def client(self, **client_kwargs: Any) -> aiopnsense.OPNsenseClient:
        """Create a client whose transport talks to this Router.

        Args:
            client_kwargs (Any): Keyword arguments forwarded to ``make_client``.

        Returns:
            aiopnsense.OPNsenseClient: Client with ``_post`` and ``_get`` scripted.
        """
        client = self._make_client(**client_kwargs)
        client._use_snake_case = True
        client._post = self._post
        client._get = self._get
        client._queue_request = self._queue_outcome_read
        client._queue_post_with_cancellation = self._post_with_cancellation
        return client

    async def _queue_outcome_read(
        self, method: str, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> _EndpointOutcome:
        """Answer a queued outcome GET from the script, reporting an unscripted path as absent.

        Args:
            method (str): Queue operation requested by the client.
            path (str): API path being read.
            payload (MutableMapping[str, Any] | None): Unused body for the GET request.

        Returns:
            _EndpointOutcome: Scripted payload, or an absent endpoint when none is scripted.

        Raises:
            ValueError: The client requests a queued operation other than a GET outcome.
        """
        if method != "get_outcome":
            raise ValueError(f"Unexpected scripted Router operation: {method}")
        del payload
        reply = await self._get(path)
        return _EndpointOutcome.absent() if reply is None else _EndpointOutcome.data(reply)

    def requested(self, path: str, *, method: str = "POST") -> bool:
        """Return whether the client sent a request.

        Args:
            path (str): API path to look for.
            method (str): HTTP method to look for.

        Returns:
            bool: ``True`` when at least one matching request was sent.
        """
        return any(r.method == method and r.path == path for r in self.requests)

    async def _answer(self, request: RouterRequest) -> MutableMapping[str, Any] | list | None:
        """Record a request and return or raise its scripted reply.

        Args:
            request (RouterRequest): Request the client sent.

        Returns:
            MutableMapping[str, Any] | list | None: Scripted JSON body, or ``None``.
        """
        self.requests.append(request)
        reply = self._replies.get((request.method, request.path))
        if isinstance(reply, BaseException):
            return await AsyncMock(side_effect=reply)()
        return reply

    async def _post(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> MutableMapping[str, Any] | list | None:
        """Answer a POST from the script.

        Args:
            path (str): API path.
            payload (MutableMapping[str, Any] | None): Request body.

        Returns:
            MutableMapping[str, Any] | list | None: Scripted JSON body, or ``None``.
        """
        return await self._answer(RouterRequest("POST", path, payload))

    async def _post_with_cancellation(
        self, path: str, payload: MutableMapping[str, Any] | None = None
    ) -> tuple[MutableMapping[str, Any] | list | None, None]:
        """Answer a cancellation-aware POST (used by VPN Toggles) from the script.

        Args:
            path (str): API path.
            payload (MutableMapping[str, Any] | None): Request body.

        Returns:
            tuple[MutableMapping[str, Any] | list | None, None]: Scripted JSON body, or
                ``None``, and no caller cancellation.
        """
        return await self._post(path, payload), None

    async def _get(self, path: str) -> MutableMapping[str, Any] | list | None:
        """Answer a GET from the script.

        Args:
            path (str): API path.

        Returns:
            MutableMapping[str, Any] | list | None: Scripted JSON body, or ``None``.
        """
        return await self._answer(RouterRequest("GET", path))


@pytest.fixture
def scripted_router(make_client: MakeClientFactory) -> ScriptedRouter:
    """Return a Router whose replies the test scripts per request path.

    Args:
        make_client (MakeClientFactory): Client factory fixture.

    Returns:
        ScriptedRouter: Empty script; call ``reply`` then ``client``.
    """
    return ScriptedRouter(make_client)


class VpnQueueTasks:
    """Track tasks started by a VPN queue test for fixture cleanup."""

    def __init__(self) -> None:
        """Initialize the task list."""
        self.tasks: list[asyncio.Task[Any]] = []

    def start[T](
        self, coroutine: Coroutine[Any, Any, T], *, eager_start: bool = False
    ) -> asyncio.Task[T]:
        """Start and track a test task.

        Args:
            coroutine (Coroutine[Any, Any, T]): Public operation or queue blocker.
            eager_start (bool): Run to first suspension before returning.

        Returns:
            asyncio.Task[T]: Task running the operation.
        """
        task = asyncio.create_task(coroutine, eager_start=eager_start)
        self.tasks.append(task)
        return task


@pytest.fixture
async def vpn_queue_tasks(make_client: MakeClientFactory) -> AsyncIterator[VpnQueueTasks]:
    """Cancel and join any test tasks left running before clients close.

    Args:
        make_client (MakeClientFactory): Provides clients and closes them after task cleanup.

    Yields:
        VpnQueueTasks: Task starter for the test.
    """
    tasks = VpnQueueTasks()
    yield tasks
    for task in tasks.tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks.tasks, return_exceptions=True)


@pytest.fixture
async def vpn_toggle_client(
    make_client: MakeClientFactory, monkeypatch: pytest.MonkeyPatch
) -> aiopnsense.OPNsenseClient:
    """Use the real request queue with mocked VPN transport responses.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        monkeypatch (pytest.MonkeyPatch): Fixture for restoring queue task creation.

    Returns:
        aiopnsense.OPNsenseClient: Client configured for snake_case endpoints by default.
    """
    monkeypatch.setattr(asyncio, "create_task", asyncio.get_running_loop().create_task)
    monkeypatch.setattr(client_queue, "QUEUE_REQUEST_INTERVAL_SECONDS", 0)
    client = make_client()
    client._use_snake_case = True
    return client
