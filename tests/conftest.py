"""Shared pytest fixtures for aiopnsense."""

import asyncio
import contextlib
from typing import cast

import aiohttp
import pytest

import aiopnsense


class FakeClientSession:
    """Minimal fake aiohttp session for unit tests."""

    async def __aenter__(self):
        """Enter async context."""
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """Exit async context."""
        await self.close()
        return False

    async def close(self):
        """Close the fake session."""
        return True


@pytest.fixture(autouse=True)
def _patch_asyncio_create_task(monkeypatch, request):
    """Prevent background worker tasks from running in tests."""

    if request.node.fspath and request.node.fspath.basename == "test_client_base.py":
        return

    original_create_task = asyncio.create_task

    class _DummyTask:
        def cancel(self):
            return True

        def done(self):
            return False

        def __await__(self):
            if False:
                yield
            return None

    def _fake_create_task(coro, *args, **kwargs):
        qualname = getattr(coro, "__qualname__", "")
        module = getattr(coro, "__module__", "")
        if "aiopnsense" in module or "_process_queue" in qualname or "_monitor_queue" in qualname:
            with contextlib.suppress(Exception):
                coro.close()
            return _DummyTask()
        return original_create_task(coro, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_task", _fake_create_task)


@pytest.fixture
def fake_stream_response_factory():
    """Return a factory that constructs a fake streaming response."""

    def _make(chunks: list[bytes], status: int = 200, reason: str = "OK", ok: bool = True):
        class _Resp:
            def __init__(self):
                self.status = status
                self.reason = reason
                self.ok = ok

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            @property
            def content(self):
                class _Content:
                    def __init__(self, payload: list[bytes]):
                        self._payload = payload

                    async def iter_chunked(self, _n):
                        for chunk in self._payload:
                            yield chunk

                return _Content(list(chunks))

        return _Resp()

    return _make


@pytest.fixture
async def make_client():
    """Return a factory that constructs an OPNsenseClient for tests."""

    clients: list[aiopnsense.OPNsenseClient] = []

    def _make(
        session: aiohttp.ClientSession | None = None,
        username: str = "u",
        password: str = "p",
        url: str = "http://localhost",
    ) -> aiopnsense.OPNsenseClient:
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
