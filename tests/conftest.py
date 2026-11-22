"""Shared pytest fixtures for aiopnsense."""

import asyncio
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Coroutine,
    Generator,
    MutableMapping,
)
import contextlib
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self, cast
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from yarl import URL

import aiopnsense
from aiopnsense import client_queue

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

    def get(url: str, **kwargs: Any) -> FakeResponse:
        """Answer a GET request.

        Args:
            url (str): Requested URL.
            kwargs (Any): Request parameters passed by the HTTP transport.

        Returns:
            FakeResponse: Configured response.
        """
        return router.request("GET", url, **kwargs)

    def post(url: str, **kwargs: Any) -> FakeResponse:
        """Answer a POST request.

        Args:
            url (str): Requested URL.
            kwargs (Any): Request parameters passed by the HTTP transport.

        Returns:
            FakeResponse: Configured response.
        """
        return router.request("POST", url, **kwargs)

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
        """
        caller = "fake router"
        if method == "get":
            return await client._do_get(path, caller)
        if method == "get_text":
            return await client._do_get(path, caller, response_format="text")
        if method == "get_outcome":
            return await client._do_get_outcome(path, caller)
        if method == "get_text_outcome":
            return await client._do_get_outcome(path, caller, response_format="text")
        if method == "post":
            return await client._do_post(path, payload, caller)
        if method == "post_outcome":
            return await client._do_post_outcome(path, payload, caller)
        return await client._do_get_from_stream(path, caller)

    session.get = get
    session.post = post
    client._queue_request = queue_immediately
    return router


@pytest.fixture(autouse=True)
def _patch_asyncio_create_task(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Prevent background worker tasks from running in tests.

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
        client._is_get_endpoint_available = self._is_get_endpoint_available
        client._queue_post_with_cancellation = self._post_with_cancellation
        return client

    async def _is_get_endpoint_available(self, path: str, force_refresh: bool = False) -> bool:
        """Answer an endpoint availability probe from the script.

        An endpoint is available when the script has a GET reply for it, so a test makes
        an endpoint unavailable by leaving it unscripted.

        Args:
            path (str): API path being probed.
            force_refresh (bool): Ignored; the script has no cache.

        Returns:
            bool: ``True`` when a GET reply is scripted for ``path``.
        """
        return ("GET", path) in self._replies

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
