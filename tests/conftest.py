"""Shared pytest fixtures for aiopnsense."""

import asyncio
from collections.abc import AsyncGenerator, Callable, Coroutine, Generator, MutableMapping
import contextlib
from dataclasses import dataclass
from types import TracebackType
from typing import Any, cast
from unittest.mock import MagicMock

import aiohttp
import pytest
from yarl import URL

import aiopnsense


class FakeClientSession:
    """Minimal fake aiohttp session for unit tests."""

    async def __aenter__(self) -> "FakeClientSession":
        """Enter async context.

        Returns:
            FakeClientSession: The context-managed instance.
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

    async def iter_chunked(self, _chunk_size: int) -> AsyncGenerator[bytes, None]:
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
            json_payload (Any): Value returned by ``json()``.
            text_payload (str): Value returned by ``text()``.
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

        if include_request_info:
            self.request_info = _FakeRequestInfo(real_url=URL(request_url))

    async def __aenter__(self) -> "FakeResponse":
        """Enter the asynchronous context.

        Returns:
            FakeResponse: The context-managed instance.
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


@pytest.fixture(autouse=True)
def _patch_asyncio_create_task(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Prevent background worker tasks from running in tests."""

    if request.node.fspath and request.node.fspath.basename == "test_client_base.py":
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

        def __await__(self) -> Generator[None, None, None]:
            """Await.

            Returns:
                Generator[None, None, None]: Iterator used by the await
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
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            asyncio.Task[Any] | _DummyTask: Real task for non-aiopnsense
                coroutines, otherwise a dummy task.
        """
        qualname = getattr(coro, "__qualname__", "")
        module = getattr(coro, "__module__", "")
        if "aiopnsense" in module or "_process_queue" in qualname or "_monitor_queue" in qualname:
            with contextlib.suppress(Exception):
                coro.close()
            return _DummyTask()
        return original_create_task(coro, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_task", _fake_create_task)


@pytest.fixture
def fake_response_factory() -> Callable[..., FakeResponse]:
    """Return a factory that constructs reusable fake HTTP responses."""

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
    """Return a factory that constructs fake streaming responses."""

    def _make(chunks: list[bytes], status: int = 200, reason: str = "OK", ok: bool = True) -> Any:
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
async def make_client() -> AsyncGenerator[Callable[..., aiopnsense.OPNsenseClient], None]:
    """Return a factory that constructs an OPNsenseClient for tests."""

    clients: list[aiopnsense.OPNsenseClient] = []

    def _make(
        session: aiohttp.ClientSession | None = None,
        username: str = "u",
        password: str = "p",
        url: str = "http://localhost",
    ) -> aiopnsense.OPNsenseClient:
        """Make.

        Args:
            session (aiohttp.ClientSession | None, optional): HTTP client session used for API requests.
            username (str): Username for API authentication.
            password (str): Password for API authentication.
            url (str): Base URL of the OPNsense instance.

        Returns:
            aiopnsense.OPNsenseClient: Value produced by this method.
        """
        if session is None:
            session = cast("aiohttp.ClientSession", FakeClientSession())
        client = aiopnsense.OPNsenseClient(
            url=url, username=username, password=password, session=session
        )
        clients.append(client)
        return client

    try:
        yield _make
    finally:
        for client in clients:
            with contextlib.suppress(Exception):
                await client.async_close()
