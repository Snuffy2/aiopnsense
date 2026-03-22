"""Tests for `aiopnsense.client_base` request, queue, and transport helpers."""

import asyncio
from collections.abc import MutableMapping
import contextlib
from datetime import datetime, timedelta
from types import TracebackType
from typing import Any, Self
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from yarl import URL

import aiopnsense as pyopnsense
from aiopnsense import client_base as pyopnsense_client_base


@pytest.mark.asyncio
async def test_safe_dict_get_and_list_get(monkeypatch, make_client) -> None:
    """Ensure safe getters coerce None to empty dict/list as expected."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = make_client(session=session, username="user", password="pass")
    # Patch _get to return dict or list using pytest's monkeypatch
    monkeypatch.setattr(client, "_get", AsyncMock(return_value={"foo": "bar"}), raising=False)
    result_dict = await client._safe_dict_get("/fake")
    assert result_dict == {"foo": "bar"}

    monkeypatch.setattr(client, "_get", AsyncMock(return_value=[1, 2, 3]), raising=False)
    result_list = await client._safe_list_get("/fake")
    assert result_list == [1, 2, 3]

    monkeypatch.setattr(client, "_get", AsyncMock(return_value=None), raising=False)
    result_empty_dict = await client._safe_dict_get("/fake")
    assert result_empty_dict == {}
    result_empty_list = await client._safe_list_get("/fake")
    assert result_empty_list == []
    await client.async_close()


@pytest.mark.asyncio
async def test_safe_dict_post_and_list_post(monkeypatch, make_client) -> None:
    """Ensure safe post helpers coerce None to empty dict/list as expected."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = make_client(session=session, username="user", password="pass")
    try:
        monkeypatch.setattr(client, "_post", AsyncMock(return_value={"foo": "bar"}), raising=False)
        result_dict = await client._safe_dict_post("/fake")
        assert result_dict == {"foo": "bar"}

        monkeypatch.setattr(client, "_post", AsyncMock(return_value=[1, 2, 3]), raising=False)
        result_list = await client._safe_list_post("/fake")
        assert result_list == [1, 2, 3]

        monkeypatch.setattr(client, "_post", AsyncMock(return_value=None), raising=False)
        result_empty_dict = await client._safe_dict_post("/fake")
        assert result_empty_dict == {}
        result_empty_list = await client._safe_list_post("/fake")
        assert result_empty_list == []
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_safe_dict_get_with_timeout(monkeypatch, make_client) -> None:
    """Ensure custom-timeout safe getter coerces None to empty dict as expected."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = make_client(session=session, username="user", password="pass")
    try:
        monkeypatch.setattr(
            client, "_do_get", AsyncMock(return_value={"foo": "bar"}), raising=False
        )
        result_dict = await client._safe_dict_get_with_timeout("/fake", timeout_seconds=180)
        assert result_dict == {"foo": "bar"}
        client._do_get.assert_awaited_once_with(
            path="/fake",
            caller="_safe_dict_get_with_timeout",
            timeout_seconds=180,
        )

        monkeypatch.setattr(client, "_do_get", AsyncMock(return_value=None), raising=False)
        result_empty_dict = await client._safe_dict_get_with_timeout("/fake", timeout_seconds=180)
        assert result_empty_dict == {}
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("timeout_seconds", "expected"),
    [
        ("bad", float(pyopnsense_client_base.DEFAULT_REQUEST_TIMEOUT_SECONDS)),
        (object(), float(pyopnsense_client_base.DEFAULT_REQUEST_TIMEOUT_SECONDS)),
        (0, float(pyopnsense_client_base.DEFAULT_REQUEST_TIMEOUT_SECONDS)),
        (-5, float(pyopnsense_client_base.DEFAULT_REQUEST_TIMEOUT_SECONDS)),
        (1.75, 1.75),
    ],
)
@pytest.mark.asyncio
async def test_normalize_timeout_seconds(
    timeout_seconds: Any, expected: float, make_client
) -> None:
    """_normalize_timeout_seconds should coerce invalid values to the default timeout."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = make_client(session=session)
    try:
        assert client._normalize_timeout_seconds(timeout_seconds) == expected
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_endpoint_available_caches_success(make_client) -> None:
    """Endpoint probe should cache positive results and avoid repeated calls."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = make_client(session=session)
    calls = 0

    class FakeResp:
        def __init__(self, status: int, ok: bool) -> None:
            """Initialize the FakeResp instance.

            Args:
                status (int): Status value to evaluate or normalize.
                ok (bool): Mock HTTP success flag for test responses.
            """
            self.status = status
            self.reason = "OK"
            self.ok = ok

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
                exc_type (type[BaseException] | None): Exception type raised in async context teardown.
                exc (BaseException | None): Exception instance raised in async context teardown.
                tb (TracebackType | None): Traceback associated with async context teardown.

            Returns:
                bool: False so exceptions are not suppressed.
            """
            return False

    def _get(*args: Any, **kwargs: Any) -> Any:
        """Get.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Decoded response payload returned by the GET request.
        """
        nonlocal calls
        calls += 1
        return FakeResp(status=200, ok=True)

    session.get = _get
    try:
        assert await client.is_endpoint_available("/api/test/endpoint") is True
        assert await client.is_endpoint_available("/api/test/endpoint") is True
        assert calls == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_endpoint_available_cache_false_by_ttl_and_force_refresh(make_client) -> None:
    """Endpoint probe should cache False results until TTL expiry or force refresh."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = make_client(session=session)
    calls = 0

    class FakeResp:
        def __init__(self, status: int, ok: bool) -> None:
            """Initialize the FakeResp instance.

            Args:
                status (int): Status value to evaluate or normalize.
                ok (bool): Mock HTTP success flag for test responses.
            """
            self.status = status
            self.reason = "ERR"
            self.ok = ok

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
                exc_type (type[BaseException] | None): Exception type raised in async context teardown.
                exc (BaseException | None): Exception instance raised in async context teardown.
                tb (TracebackType | None): Traceback associated with async context teardown.

            Returns:
                bool: False so exceptions are not suppressed.
            """
            return False

    def _get(*args: Any, **kwargs: Any) -> Any:
        """Get.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Decoded response payload returned by the GET request.
        """
        nonlocal calls
        calls += 1
        if calls == 1:
            return FakeResp(status=404, ok=False)
        return FakeResp(status=200, ok=True)

    session.get = _get
    try:
        path = "/api/test/endpoint"
        assert await client.is_endpoint_available(path) is False
        assert await client.is_endpoint_available(path) is False
        assert calls == 1
        assert path in client._endpoint_checked_at
        client._endpoint_checked_at[path] = datetime.now().astimezone() - timedelta(
            seconds=client._endpoint_cache_ttl_seconds + 1
        )
        assert await client.is_endpoint_available(path) is True
        assert calls == 2
        assert await client.is_endpoint_available(path) is True
        assert calls == 2
        assert await client.is_endpoint_available(path, force_refresh=True) is True
        assert calls == 3
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_endpoint_available_handles_timeout(make_client) -> None:
    """Endpoint probe should return False and retry after timeout failures."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = make_client(session=session)
    calls = 0

    def _get(*args: Any, **kwargs: Any) -> Any:
        """Get.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Decoded response payload returned by the GET request.
        """
        nonlocal calls
        calls += 1
        raise TimeoutError("timeout")

    session.get = _get
    try:
        assert await client.is_endpoint_available("/api/test/endpoint") is False
        assert await client.is_endpoint_available("/api/test/endpoint") is False
        assert calls == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_endpoint_available_does_not_cache_non_404_http_errors(make_client) -> None:
    """Endpoint probe should retry when non-404 HTTP status codes are returned."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = make_client(session=session)
    calls = 0

    class FakeResp:
        def __init__(self, status: int, ok: bool) -> None:
            """Initialize the FakeResp instance.

            Args:
                status (int): Status value to evaluate or normalize.
                ok (bool): Mock HTTP success flag for test responses.
            """
            self.status = status
            self.reason = "ERR"
            self.ok = ok

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
                exc_type (type[BaseException] | None): Exception type raised in async context teardown.
                exc (BaseException | None): Exception instance raised in async context teardown.
                tb (TracebackType | None): Traceback associated with async context teardown.

            Returns:
                bool: False so exceptions are not suppressed.
            """
            return False

    def _get(*args: Any, **kwargs: Any) -> Any:
        """Get.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Decoded response payload returned by the GET request.
        """
        nonlocal calls
        calls += 1
        return FakeResp(status=500, ok=False)

    session.get = _get
    try:
        path = "/api/test/endpoint"
        assert await client.is_endpoint_available(path) is False
        assert await client.is_endpoint_available(path) is False
        assert calls == 2
        assert path not in client._endpoint_checked_at
        assert path not in client._endpoint_availability
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_opnsenseclient_async_close(make_client) -> None:
    """Verify async_close cancels workers and queue monitor as expected."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost",
        username="user",
        password="pass",
        session=session,
    )
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
@pytest.mark.parametrize(
    "method_name, session_method, args, kwargs",
    [
        ("_do_get", "get", ("/api/x",), {"caller": "tst"}),
        ("_do_post", "post", ("/api/x",), {"payload": {}}),
    ],
)
async def test_do_get_post_error_initial_behavior(
    method_name, session_method, args, kwargs, make_client
) -> None:
    """When client._initial is True, non-ok responses should raise ClientResponseError for _do_get/_do_post."""
    session = MagicMock(spec=aiohttp.ClientSession)

    # create a fake response context manager
    class FakeResp:
        def __init__(self, status: int = 500, ok: bool = False) -> None:
            """Initialize the FakeResp instance.

            Args:
                status (Any): Status value to evaluate or normalize.
                ok (Any): Mock HTTP success flag for test responses.
            """
            self.status = status
            self.reason = "Err"
            self.ok = ok

            # Provide a minimal request_info with real_url to satisfy logging
            class RI:
                real_url = URL("http://localhost")

            self.request_info = RI()
            self.history: list[Any] = []
            self.headers: dict[str, Any] = {}

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
                exc_type (type[BaseException] | None): Exception type raised in async context teardown.
                exc (BaseException | None): Exception instance raised in async context teardown.
                tb (TracebackType | None): Traceback associated with async context teardown.

            Returns:
                bool: False so exceptions are not suppressed.
            """
            return False

        async def json(self, content_type: Any = None) -> Any:
            """Json.

            Args:
                content_type (Any, optional): Mock response content type.

            Returns:
                Any: Value produced by this helper method.
            """
            return {"x": 1}

        async def text(self) -> str:
            """Text.

            Returns:
                Any: Value produced by this helper method.
            """
            return "raw response text"

        @property
        def content(self) -> Any:
            """Content.

            Returns:
                Any: Value produced by this helper method.
            """

            class C:
                async def iter_chunked(self, n: int) -> Any:
                    """Iter chunked.

                    Args:
                        n (Any): Numeric value to normalize.

                    Returns:
                        Any: Value produced by this helper method.
                    """
                    yield b"data: {}\n\n"

            return C()

    if session_method == "get":
        session.get = lambda *a, **k: FakeResp(status=403, ok=False)
    else:
        session.post = lambda *a, **k: FakeResp(status=500, ok=False)

    client = make_client(session=session)
    client._initial = True
    try:
        with pytest.raises(aiohttp.ClientResponseError):
            await getattr(client, method_name)(*args, **kwargs)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_from_stream_parsing(make_client, fake_stream_response_factory) -> None:
    """Simulate SSE-like stream with two messages and assert parsing returns dict."""
    session = MagicMock(spec=aiohttp.ClientSession)

    # use shared factory to construct a fake streaming response
    session.get = lambda *a, **k: fake_stream_response_factory(
        [b'data: {"a": 1}\n\n', b'data: {"b": 2}\n\n']
    )
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        res = await client._do_get_from_stream("/stream", caller="tst")
        # implementation returns the second 'data' message parsed as JSON
        assert isinstance(res, MutableMapping)
        assert res.get("b") == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_from_stream_ignores_first_message(
    make_client, fake_stream_response_factory
) -> None:
    """Ensure the parser ignores the first data message and returns the second."""
    session = MagicMock(spec=aiohttp.ClientSession)

    session.get = lambda *a, **k: fake_stream_response_factory(
        [
            b'data: {"id": "first", "body": "ignore me"}\n\n',
            b'data: {"id": "second", "body": "keep me"}\n\n',
        ]
    )
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        res = await client._do_get_from_stream("/stream", caller="tst")
        assert isinstance(res, MutableMapping)
        # ensure the second message was selected
        assert res.get("id") == "second"
        assert res.get("body") == "keep me"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_from_stream_partial_chunks_accumulates_buffer(
    make_client, fake_stream_response_factory
) -> None:
    """Simulate a stream where a JSON message is split across chunks to exercise buffer accumulation."""
    session = MagicMock(spec=aiohttp.ClientSession)

    session.get = lambda *a, **k: fake_stream_response_factory(
        [b'data: {"a"', b": 1}\n\n", b'data: {"b": 2}\n\n']
    )
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        res = await client._do_get_from_stream("/stream2", caller="tst")
        assert isinstance(res, MutableMapping)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_process_queue_unknown_method_sets_future_exception(make_client) -> None:
    """Putting an unknown method into the request queue should set an exception on the future."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
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


@pytest.mark.asyncio
async def test_do_get_from_stream_error_initial_raises(make_client) -> None:
    """When response.ok is False and client._initial True, _do_get_from_stream should raise."""
    session = MagicMock(spec=aiohttp.ClientSession)

    class FakeBadResp:
        def __init__(self, status: int = 403) -> None:
            """Initialize the FakeBadResp instance.

            Args:
                status (Any): Status value to evaluate or normalize.
            """
            self.status = status
            self.reason = "Forbidden"
            self.ok = False

            class RI:
                real_url = URL("http://localhost")

            self.request_info = RI()
            self.history: list[Any] = []
            self.headers: dict[str, Any] = {}

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
                exc_type (type[BaseException] | None): Exception type raised in async context teardown.
                exc (BaseException | None): Exception instance raised in async context teardown.
                tb (TracebackType | None): Traceback associated with async context teardown.

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

            class C:
                async def iter_chunked(self, n: int) -> Any:
                    """Iter chunked.

                    Args:
                        n (Any): Numeric value to normalize.

                    Returns:
                        Any: Value produced by this helper method.
                    """
                    yield b""

            return C()

    def fake_get(*args: Any, **kwargs: Any) -> Any:
        """Fake get.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Mock value returned to support test behavior.
        """
        return FakeBadResp()

    session.get = fake_get
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        client._initial = True
        with pytest.raises(aiohttp.ClientResponseError):
            await client._do_get_from_stream("/bad", caller="t")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_process_queue_handles_requests(make_client) -> None:
    """Run a single iteration of _process_queue processing several request types."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
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
async def test_get_enqueues_and_processes(returned, make_client) -> None:
    """Ensure `_get` enqueues a request and `_process_queue` calls `_do_get` and returns value.

    Parameterized to cover mapping, list and None return types.
    """
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
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
async def test_get_uses_unknown_when_inspect_stack_raises(monkeypatch, make_client) -> None:
    """If inspect.stack() raises, `_get` should set caller to 'Unknown'."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    task: asyncio.Task | None = None
    try:
        # Replace client_base.inspect.stack to raise an IndexError
        class _BadInspect:
            @staticmethod
            def stack() -> Any:
                """Stack.

                Returns:
                    Any: Value produced by this helper method.
                """
                raise IndexError("no stack")

        monkeypatch.setattr(pyopnsense_client_base, "inspect", _BadInspect)

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
async def test_post_enqueues_and_processes(returned, make_client) -> None:
    """Ensure `_post` enqueues a request and `_process_queue` calls `_do_post` and returns value.

    Parameterized to cover mapping, list and None return types. Also verify payload is forwarded.
    """
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
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
async def test_post_uses_unknown_when_inspect_stack_raises(monkeypatch, make_client) -> None:
    """If inspect.stack() raises, `_post` should set caller to 'Unknown'."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
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

        monkeypatch.setattr(pyopnsense_client_base, "inspect", _BadInspect)

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
async def test_do_get_and_do_post_success_paths() -> None:
    """_do_get/_do_post should return parsed JSON when response.ok is True."""
    session = MagicMock(spec=aiohttp.ClientSession)

    class FakeOKResp:
        def __init__(self, payload: Any) -> None:
            """Initialize the FakeOKResp instance.

            Args:
                payload (Any): Request payload sent to the API endpoint.
            """
            self.status = 200
            self.reason = "OK"
            self.ok = True
            self._payload = payload

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
                exc_type (type[BaseException] | None): Exception type raised in async context teardown.
                exc (BaseException | None): Exception instance raised in async context teardown.
                tb (TracebackType | None): Traceback associated with async context teardown.

            Returns:
                bool: False so exceptions are not suppressed.
            """
            return False

        async def json(self, content_type: Any = None) -> Any:
            """Json.

            Args:
                content_type (Any, optional): Mock response content type.

            Returns:
                Any: Value produced by this helper method.
            """
            return self._payload

        @property
        def content(self) -> Any:
            """Content.

            Returns:
                Any: Value produced by this helper method.
            """

            class C:
                async def iter_chunked(self, n: int) -> Any:
                    """Iter chunked.

                    Args:
                        n (Any): Numeric value to normalize.

                    Returns:
                        Any: Value produced by this helper method.
                    """
                    yield b""  # not used here

            return C()

    def fake_get(*args: Any, **kwargs: Any) -> Any:
        """Fake get.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Mock value returned to support test behavior.
        """
        return FakeOKResp({"a": 1})

    def fake_post(*args: Any, **kwargs: Any) -> Any:
        """Fake post.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Mock value returned to support test behavior.
        """
        return FakeOKResp([1, 2, 3])

    session.get = fake_get
    session.post = fake_post
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        got = await client._do_get("/api/x", caller="t")
        assert isinstance(got, MutableMapping) and got.get("a") == 1

        posted = await client._do_post("/api/x", payload={"x": 1}, caller="t")
        assert isinstance(posted, list) and posted[0] == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_process_queue_exception_sets_future_exception() -> None:
    """If a worker raises, the future should get_exception set."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
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
async def test_process_queue_cancelled_sets_future_cancelled_error() -> None:
    """Ensure cancelling _process_queue resolves in-flight futures with CancelledError."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
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
async def test_monitor_queue_handles_qsize_exception() -> None:
    """If queue.qsize() raises, monitor should catch and continue (task runs)."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
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
async def test_client_base_workers_start_lazily_on_first_queued_request() -> None:
    """Ensure loop/workers are initialized on first queued API request, not in __init__."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
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


@pytest.mark.asyncio
async def test_do_get_post_and_stream_permission_errors(make_client) -> None:
    """_do_get/_do_post/_do_get_from_stream should not raise when 403 and initial False."""
    session = MagicMock(spec=aiohttp.ClientSession)

    class Fake403:
        def __init__(self) -> None:
            """Initialize the Fake403 instance."""
            self.status = 403
            self.reason = "Forbidden"
            self.ok = False

            class RI:  # minimal request_info
                real_url = URL("http://localhost")

            self.request_info = RI()
            self.history: list[Any] = []
            self.headers: dict[str, Any] = {}

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
                exc_type (type[BaseException] | None): Exception type raised in async context teardown.
                exc (BaseException | None): Exception instance raised in async context teardown.
                tb (TracebackType | None): Traceback associated with async context teardown.

            Returns:
                bool: False so exceptions are not suppressed.
            """
            return False

        async def json(self, content_type: Any = None) -> Any:
            """Json.

            Args:
                content_type (Any, optional): Mock response content type.

            Returns:
                Any: Value produced by this helper method.
            """
            return {"err": 1}

        @property
        def content(self) -> Any:  # for stream variant
            """Content.

            Returns:
                Any: Value produced by this helper method.
            """

            class C:
                async def iter_chunked(self, n: int) -> Any:
                    """Iter chunked.

                    Args:
                        n (Any): Numeric value to normalize.

                    Returns:
                        Any: Value produced by this helper method.
                    """
                    if False:  # pragma: no cover
                        yield b""  # never executed; placeholder
                        return
                    yield b""  # empty stream

            return C()

    session.get = lambda *a, **k: Fake403()
    session.post = lambda *a, **k: Fake403()
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        client._initial = False
        assert await client._do_get("/x", caller="t") is None
        assert await client._do_post("/x", payload={}, caller="t") is None
        assert await client._do_get_from_stream("/x", caller="t") == {}
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_client_name_property() -> None:
    """Ensure client reports a composed name property correctly."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost",
        username="user",
        password="pass",
        session=session,
    )
    try:
        assert client.name == "OPNsense"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_reset_and_get_query_counts() -> None:
    """Reset and retrieve client query counters."""
    session = MagicMock(spec=aiohttp.ClientSession)

    class FakeOKResp:
        def __init__(self, payload: Any) -> None:
            """Initialize the FakeOKResp instance.

            Args:
                payload (Any): Request payload sent to the API endpoint.
            """
            self.status = 200
            self.reason = "OK"
            self.ok = True
            self._payload = payload

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
                exc_type (type[BaseException] | None): Exception type raised in async context teardown.
                exc (BaseException | None): Exception instance raised in async context teardown.
                tb (TracebackType | None): Traceback associated with async context teardown.

            Returns:
                bool: False so exceptions are not suppressed.
            """
            return False

        async def json(self, content_type: Any = None) -> Any:
            """Json.

            Args:
                content_type (Any, optional): Mock response content type.

            Returns:
                Any: Value produced by this helper method.
            """
            return self._payload

    session.get = lambda *args, **kwargs: FakeOKResp({"ok": True})
    client = pyopnsense.OPNsenseClient(
        url="http://localhost",
        username="user",
        password="pass",
        session=session,
    )
    try:
        await client._do_get("/api/test", caller="test_reset_and_get_query_counts")
        count = await client.get_query_counts()
        assert count > 0

        await client.reset_query_counts()
        count = await client.get_query_counts()
        assert count == 0
    finally:
        await client.async_close()
