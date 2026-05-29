"""Tests for client request queueing and worker lifecycle behavior."""

import asyncio
from collections.abc import Callable, MutableMapping
import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from aiopnsense import (
    OPNsenseClient,
    client_queue as aiopnsense_client_queue,
)
from tests.conftest import FakeResponse, make_mock_session_client

MakeClientFactory = Callable[..., OPNsenseClient]
FakeStreamResponseFactory = Callable[[list[bytes]], FakeResponse]


class _TestClientSSLError(aiohttp.ClientSSLError):
    """Minimal ``ClientSSLError`` subclass used for validation tests."""

    def __init__(self) -> None:
        """Initialize the synthetic SSL error instance."""
        Exception.__init__(self, "ssl")

    def __str__(self) -> str:
        """Return a stable string for logging and assertion output.

        Returns:
            str: Constant error message for deterministic test behavior.
        """
        return "ssl"


def _client_response_error(status: int) -> aiohttp.ClientResponseError:
    """Build a minimal ``ClientResponseError`` for validation tests.

    Args:
        status (int): HTTP status code exposed by the error.

    Returns:
        aiohttp.ClientResponseError: Response error instance with the requested status.
    """
    return aiohttp.ClientResponseError(
        request_info=MagicMock(),
        history=(),
        status=status,
        message="boom",
        headers={},
    )



@pytest.mark.asyncio
async def test_opnsenseclient_async_close(make_client: MakeClientFactory) -> None:
    """Verify ``async_close`` cancels worker tasks and queue monitor resources.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts cancellation and cleanup behavior.
    """
    client, _session = make_mock_session_client(make_client, username="user", password="pass")
    try:
        initial_tasks = [t for t in [client._queue_monitor, *client._workers] if t is not None]
        for task in initial_tasks:
            task.cancel()
        if initial_tasks:
            await asyncio.gather(*initial_tasks, return_exceptions=True)

        loop = asyncio.get_running_loop()
        monitor = loop.create_task(asyncio.sleep(60))
        worker1 = loop.create_task(asyncio.sleep(60))
        worker2 = loop.create_task(asyncio.sleep(60))
        client._queue_monitor = monitor
        client._workers = [worker1, worker2]
        client._request_queue = asyncio.Queue()
        future = loop.create_future()
        await client._request_queue.put(("get", "/api/test", None, future, "test"))
        await client.async_close()
        assert monitor.cancelled()
        assert worker1.cancelled()
        assert worker2.cancelled()
        assert future.done()
        assert isinstance(future.exception(), asyncio.CancelledError)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_opnsenseclient_async_context_manager_closes_background_tasks(
    make_client: MakeClientFactory,
) -> None:
    """Verify ``async with`` cleanup delegates to ``async_close``.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts context manager cleanup behavior.
    """
    client, _session = make_mock_session_client(make_client, username="user", password="pass")
    client.validate = AsyncMock()
    try:
        loop = asyncio.get_running_loop()
        monitor = loop.create_task(asyncio.sleep(60))
        worker = loop.create_task(asyncio.sleep(60))
        client._queue_monitor = monitor
        client._workers = [worker]
        client._request_queue = asyncio.Queue()
        future = loop.create_future()
        await client._request_queue.put(("get", "/api/test", None, future, "test"))

        async with client as managed_client:
            assert managed_client is client
            client.validate.assert_awaited_once()

        assert monitor.cancelled()
        assert worker.cancelled()
        assert future.done()
        assert isinstance(future.exception(), asyncio.CancelledError)
    finally:
        await client.async_close()


async def test_process_queue_unknown_method_sets_future_exception(
    make_client: MakeClientFactory,
) -> None:
    """Verify unknown queue methods set a runtime exception on the future.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts unknown-method queue error propagation.
    """
    client, _session = make_mock_session_client(make_client)
    task: asyncio.Task | None = None
    try:
        q: asyncio.Queue = asyncio.Queue()
        client._request_queue = q

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await q.put(("unknown", "/x", None, future, "tst"))

        task = loop.create_task(client._process_queue())
        await asyncio.sleep(0)  # allow the task to process the queue
        # cancel background task and await it so the CancelledError is retrieved
        task.cancel()
        # await the cancelled task so the CancelledError is retrieved and suppressed
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # future should have an exception
        exc = future.exception()
        assert isinstance(exc, RuntimeError)
    finally:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await client.async_close()


async def test_process_queue_handles_requests(make_client: MakeClientFactory) -> None:
    """Verify queue worker dispatches get/post/stream requests to the right handlers.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts request queue dispatch and result propagation.
    """
    client, _session = make_mock_session_client(make_client)
    task: asyncio.Task | None = None
    try:
        # patch the do_* methods
        client._do_get = AsyncMock(return_value={"g": 1})
        client._do_post = AsyncMock(return_value={"p": 2})
        client._do_get_from_stream = AsyncMock(return_value={"s": 3})

        # replace request queue with a real one
        q: asyncio.Queue = asyncio.Queue()
        client._request_queue = q

        # start the queue processor as a real task on the running loop (bypass patched asyncio.create_task)
        task = asyncio.get_running_loop().create_task(client._process_queue())

        loop = asyncio.get_running_loop()
        fut_get = loop.create_future()
        fut_post = loop.create_future()
        fut_stream = loop.create_future()

        await q.put(("get", "/g", None, fut_get, "t"))
        await q.put(("post", "/p", {"x": 1}, fut_post, "t"))
        await q.put(("get_from_stream", "/s", None, fut_stream, "t"))

        res1 = await asyncio.wait_for(fut_get, timeout=2)
        res2 = await asyncio.wait_for(fut_post, timeout=2)
        res3 = await asyncio.wait_for(fut_stream, timeout=2)

        assert res1 == {"g": 1}
        assert res2 == {"p": 2}
        assert res3 == {"s": 3}

        # cancel the processor task
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
    finally:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await asyncio.wait_for(client.async_close(), timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("returned", [{"ok": 1}, [1, 2, 3], None])
async def test_get_enqueues_and_processes(returned: Any, make_client: MakeClientFactory) -> None:
    """Verify ``_get`` enqueues requests and returns queue-processed results.

    Args:
        returned (Any): Mock response returned by ``_do_get``.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts queue integration and caller propagation for ``_get``.
    """
    client, _session = make_mock_session_client(make_client)
    task: asyncio.Task | None = None
    try:
        # replace request queue with a real one so _process_queue can run
        q: asyncio.Queue = asyncio.Queue()
        client._request_queue = q

        called = {}

        async def fake_do_get(path: Any, caller: str = "x") -> Any:
            # capture the caller name supplied by _get
            """Fake do get.

            Args:
                path (Any): API endpoint path to request.
                caller (Any): Caller name used for diagnostics and logging.

            Returns:
                Any: Mock value returned to support test behavior.
            """
            called["caller"] = caller
            return returned

        client._do_get = AsyncMock(side_effect=fake_do_get)

        # start the real processor task
        task = asyncio.get_running_loop().create_task(client._process_queue())

        # call the high-level _get which will create a future and wait for processing
        res = await client._get("/testpath")

        assert res == returned
        # caller should be the test function name when inspect.stack works
        assert called.get("caller") is not None
    finally:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await client.async_close()


@pytest.mark.asyncio
async def test_get_uses_unknown_when_inspect_stack_raises(
    monkeypatch: pytest.MonkeyPatch,
    make_client: MakeClientFactory,
) -> None:
    """Verify ``_get`` uses ``Unknown`` caller when stack inspection fails.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for patching ``inspect`` helpers.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts caller fallback behavior.
    """
    client, _session = make_mock_session_client(make_client)
    task: asyncio.Task | None = None
    try:
        # Replace the queue helper's inspect.stack to raise an IndexError
        class _BadInspect:
            @staticmethod
            def stack() -> Any:
                """Stack.

                Returns:
                    Any: Value produced by this helper method.
                """
                raise IndexError("no stack")

        monkeypatch.setattr(aiopnsense_client_queue, "inspect", _BadInspect)

        q: asyncio.Queue = asyncio.Queue()
        client._request_queue = q

        captured: dict[str, Any] = {}

        async def fake_do_get(path: Any, caller: str = "x") -> Any:
            """Fake do get.

            Args:
                path (Any): API endpoint path to request.
                caller (Any): Caller name used for diagnostics and logging.

            Returns:
                Any: Mock value returned to support test behavior.
            """
            captured["caller"] = caller
            return {"ok": True}

        client._do_get = AsyncMock(side_effect=fake_do_get)

        task = asyncio.get_running_loop().create_task(client._process_queue())

        res = await client._get("/other")
        assert res == {"ok": True}
        assert captured.get("caller") == "Unknown"
    finally:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("returned", [{"ok": 1}, [1, 2, 3], None])
async def test_post_enqueues_and_processes(returned: Any, make_client: MakeClientFactory) -> None:
    """Verify ``_post`` enqueues requests and forwards payload through processing.

    Args:
        returned (Any): Mock response returned by ``_do_post``.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts queue integration and payload forwarding for ``_post``.
    """
    client, _session = make_mock_session_client(make_client)
    task: asyncio.Task | None = None
    try:
        q: asyncio.Queue = asyncio.Queue()
        client._request_queue = q

        captured: dict[str, Any] = {}

        async def fake_do_post(path: Any, payload: Any = None, caller: str = "x") -> Any:
            """Fake do post.

            Args:
                path (Any): API endpoint path to request.
                payload (Any, optional): Request payload sent to the API endpoint.
                caller (Any): Caller name used for diagnostics and logging.

            Returns:
                Any: Mock value returned to support test behavior.
            """
            captured["caller"] = caller
            captured["payload"] = payload
            return returned

        client._do_post = AsyncMock(side_effect=fake_do_post)

        task = asyncio.get_running_loop().create_task(client._process_queue())

        payload = {"a": 1}
        res = await client._post("/postpath", payload=payload)

        assert res == returned
        assert captured.get("payload") == payload
        assert captured.get("caller") is not None
    finally:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await client.async_close()


@pytest.mark.asyncio
async def test_post_uses_unknown_when_inspect_stack_raises(
    monkeypatch: pytest.MonkeyPatch,
    make_client: MakeClientFactory,
) -> None:
    """Verify ``_post`` uses ``Unknown`` caller when stack inspection fails.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for patching ``inspect`` helpers.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts caller fallback behavior for ``_post``.
    """
    client, _session = make_mock_session_client(make_client)
    task: asyncio.Task | None = None
    try:

        class _BadInspect:
            @staticmethod
            def stack() -> Any:
                """Stack.

                Returns:
                    Any: Value produced by this helper method.
                """
                raise IndexError("no stack")

        monkeypatch.setattr(aiopnsense_client_queue, "inspect", _BadInspect)

        q: asyncio.Queue = asyncio.Queue()
        client._request_queue = q

        captured: dict[str, Any] = {}

        async def fake_do_post(path: Any, payload: Any = None, caller: str = "x") -> Any:
            """Fake do post.

            Args:
                path (Any): API endpoint path to request.
                payload (Any, optional): Request payload sent to the API endpoint.
                caller (Any): Caller name used for diagnostics and logging.

            Returns:
                Any: Mock value returned to support test behavior.
            """
            captured["caller"] = caller
            captured["payload"] = payload
            return {"ok": True}

        client._do_post = AsyncMock(side_effect=fake_do_post)

        task = asyncio.get_running_loop().create_task(client._process_queue())

        payload = {"b": 2}
        res = await client._post("/otherpost", payload=payload)
        assert res == {"ok": True}
        assert captured.get("caller") == "Unknown"
        assert captured.get("payload") == payload
    finally:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await client.async_close()


@pytest.mark.asyncio
async def test_process_queue_exception_sets_future_exception(
    make_client: MakeClientFactory,
) -> None:
    """Verify worker exceptions are propagated to request futures.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts queue future error propagation.
    """
    client, _session = make_mock_session_client(make_client)
    task: asyncio.Task | None = None
    try:
        client._do_get = AsyncMock(side_effect=ValueError("boom"))

        q: asyncio.Queue = asyncio.Queue()
        client._request_queue = q

        loop = asyncio.get_running_loop()
        task = loop.create_task(client._process_queue())

        fut = loop.create_future()
        await q.put(("get", "/g", None, fut, "t"))

        with pytest.raises(ValueError):
            await asyncio.wait_for(fut, timeout=2)

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await client.async_close()


@pytest.mark.asyncio
async def test_process_queue_cancelled_sets_future_cancelled_error(
    make_client: MakeClientFactory,
) -> None:
    """Verify cancelling queue processing cancels in-flight request futures.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts cancellation propagation semantics.
    """
    client, _session = make_mock_session_client(make_client)
    task: asyncio.Task | None = None
    try:
        for worker in client._workers:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        client._workers.clear()

        started = asyncio.Event()
        release = asyncio.Event()

        async def _blocked_get(_path: str, _caller: str) -> MutableMapping[str, Any]:
            """Blocked get.

            Args:
                _path (str):  path used by this operation.
                _caller (str):  caller used by this operation.

            Returns:
                MutableMapping[str, Any]: Mapping containing normalized fields for downstream use.
            """
            started.set()
            await release.wait()
            return {}

        client._do_get = AsyncMock(side_effect=_blocked_get)

        q: asyncio.Queue = asyncio.Queue()
        client._request_queue = q

        loop = asyncio.get_running_loop()
        task = loop.create_task(client._process_queue())

        fut = loop.create_future()
        await q.put(("get", "/g", None, fut, "t"))

        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert fut.done()
        with pytest.raises(asyncio.CancelledError):
            await fut
    finally:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await client.async_close()


@pytest.mark.asyncio
async def test_monitor_queue_handles_qsize_exception(make_client: MakeClientFactory) -> None:
    """Verify queue monitor tolerates ``qsize`` exceptions and remains cancellable.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts monitor-loop exception handling.
    """
    client, _session = make_mock_session_client(make_client)
    task: asyncio.Task | None = None
    try:
        # make qsize raise
        class BadQ:
            def qsize(self) -> int:
                """Qsize.

                Returns:
                    Any: Value produced by this helper method.
                """
                raise RuntimeError("boom")

            def empty(self) -> bool:
                # indicate there are no queued items so async_close won't attempt to
                # drain a non-standard queue object; this keeps the test focused on
                # the qsize exception handling in _monitor_queue.
                """Empty.

                Returns:
                    Any: Value produced by this helper method.
                """
                return True

        client._request_queue = BadQ()  # type: ignore[assignment]

        loop = asyncio.get_running_loop()
        task = loop.create_task(client._monitor_queue())

        # yield control so task runs once and hits exception
        await asyncio.sleep(0)

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await client.async_close()


@pytest.mark.asyncio
async def test_client_base_workers_start_lazily_on_first_queued_request(
    make_client: MakeClientFactory,
) -> None:
    """Verify worker/loop resources are initialized lazily on first request.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts deferred startup of queue worker infrastructure.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        assert client._loop is None
        assert client._queue_monitor is None
        assert client._workers == []

        client._do_get = AsyncMock(return_value={"ok": True})
        result = await client._get("/api/test")

        assert result == {"ok": True}
        assert client._loop is asyncio.get_running_loop()
        assert client._queue_monitor is not None
        assert len(client._workers) == client._max_workers
    finally:
        await client.async_close()

