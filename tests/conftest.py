"""Shared pytest fixtures for aiopnsense."""

import asyncio
from collections.abc import AsyncGenerator, Callable, Coroutine, Generator
import contextlib
from types import TracebackType
from typing import Any, cast

import aiohttp
import pytest

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
def fake_stream_response_factory() -> Callable[..., Any]:
    """Return a factory that constructs a fake streaming response."""

    def _make(chunks: list[bytes], status: int = 200, reason: str = "OK", ok: bool = True) -> Any:
        """Make.

        Args:
            chunks (list[bytes]): Mock stream chunks returned by iter_chunked.
            status (int): Status value to evaluate or normalize.
            reason (str): Expected failure reason text.
            ok (bool): Mock HTTP success flag for test responses.

        Returns:
            Any: Value produced by this helper method.
        """

        class _Resp:
            def __init__(self) -> None:
                """Initialize the _Resp instance."""
                self.status = status
                self.reason = reason
                self.ok = ok

            async def __aenter__(self) -> "_Resp":
                """Enter the asynchronous context.

                Returns:
                    _Resp: The context-managed instance.
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
                    exc_type (type[BaseException] | None): Exception type
                        raised in async context teardown.
                    exc (BaseException | None): Exception instance raised in
                        async context teardown.
                    tb (TracebackType | None): Traceback associated with async
                        context teardown.

                Returns:
                    bool: False so exceptions are not suppressed.
                """
                return False

            @property
            def content(self) -> Any:
                """Content.

                Returns:
                    Any: Value produced by this helper method.
                """

                class _Content:
                    def __init__(self, payload: list[bytes]) -> None:
                        """Initialize the _Content instance.

                        Args:
                            payload (list[bytes]): Request payload sent to the API endpoint.
                        """
                        self._payload = payload

                    async def iter_chunked(self, _n: int) -> AsyncGenerator[bytes, None]:
                        """Iter chunked.

                        Args:
                            _n (int): Numeric chunk size parameter used by
                                async stream iterators.

                        Returns:
                            AsyncGenerator[bytes, None]: Async iterator of
                                streaming payload chunks.
                        """
                        for chunk in self._payload:
                            yield chunk

                return _Content(list(chunks))

        return _Resp()

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
