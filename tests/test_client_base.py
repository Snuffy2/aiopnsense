"""Tests for `aiopnsense.client_base` request, queue, and transport helpers."""

import asyncio
from collections.abc import Callable, MutableMapping
import contextlib
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest

from aiopnsense import OPNsenseClient, client_base as pyopnsense_client_base
from tests.conftest import FakeClientSession, FakeResponse, make_mock_session_client

MakeClientFactory = Callable[..., OPNsenseClient]
FakeStreamResponseFactory = Callable[[list[bytes]], FakeResponse]


@pytest.mark.asyncio
async def test_safe_dict_get_and_list_get(
    monkeypatch: pytest.MonkeyPatch,
    make_client: MakeClientFactory,
) -> None:
    """Ensure safe getters coerce ``None`` into empty containers.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for overriding client methods.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts side effects and normalized return payloads.
    """
    client, _session = make_mock_session_client(make_client, username="user", password="pass")
    try:
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
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_safe_dict_post_and_list_post(
    monkeypatch: pytest.MonkeyPatch,
    make_client: MakeClientFactory,
) -> None:
    """Ensure safe post helpers coerce ``None`` into empty containers.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for overriding client methods.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts side effects and normalized return payloads.
    """
    client, _session = make_mock_session_client(make_client, username="user", password="pass")
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
async def test_safe_dict_get_with_timeout(
    monkeypatch: pytest.MonkeyPatch,
    make_client: MakeClientFactory,
) -> None:
    """Ensure timeout-specific safe getter normalizes ``None`` to ``{}``.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for overriding client methods.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test verifies timeout helper delegation and normalization.
    """
    client, _session = make_mock_session_client(make_client, username="user", password="pass")
    try:
        do_get_mock = AsyncMock(return_value={"foo": "bar"})
        monkeypatch.setattr(client, "_do_get", do_get_mock, raising=False)
        result_dict = await client._safe_dict_get_with_timeout("/fake", timeout_seconds=180)
        assert result_dict == {"foo": "bar"}
        do_get_mock.assert_awaited_once_with(
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
    timeout_seconds: Any,
    expected: float,
    make_client: MakeClientFactory,
) -> None:
    """Verify timeout normalization behavior for valid and invalid values.

    Args:
        timeout_seconds (Any): Candidate timeout input under test.
        expected (float): Expected normalized timeout value.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts normalized timeout conversion behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        assert client._normalize_timeout_seconds(timeout_seconds) == expected
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_endpoint_available_caches_success(make_client: MakeClientFactory) -> None:
    """Verify endpoint availability results are cached after a successful probe.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts positive endpoint cache behavior.
    """
    client, session = make_mock_session_client(make_client)
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
        return FakeResponse(status=200, ok=True)

    session.get = _get
    try:
        assert await client.is_endpoint_available("/api/test/endpoint") is True
        assert await client.is_endpoint_available("/api/test/endpoint") is True
        assert calls == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_endpoint_available_cache_false_by_ttl_and_force_refresh(
    make_client: MakeClientFactory,
) -> None:
    """Verify negative endpoint results are cached until TTL expiry or force refresh.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts TTL and force-refresh behavior.
    """
    client, session = make_mock_session_client(make_client)
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
        if calls == 1:
            return FakeResponse(status=404, reason="ERR", ok=False)
        return FakeResponse(status=200, reason="ERR", ok=True)

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
async def test_is_endpoint_available_handles_timeout(make_client: MakeClientFactory) -> None:
    """Verify endpoint probing returns ``False`` and retries after timeouts.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts timeout handling and retry behavior.
    """
    client, session = make_mock_session_client(make_client)
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
async def test_is_endpoint_available_does_not_cache_non_404_http_errors(
    make_client: MakeClientFactory,
) -> None:
    """Verify non-404 HTTP failures are not cached for endpoint availability.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts retry behavior for non-404 failures.
    """
    client, session = make_mock_session_client(make_client)
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
        return FakeResponse(status=500, reason="ERR", ok=False)

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
@pytest.mark.parametrize(
    "method_name, session_method, args, kwargs",
    [
        ("_do_get", "get", ("/api/x",), {"caller": "tst"}),
        ("_do_post", "post", ("/api/x",), {"payload": {}}),
    ],
)
async def test_do_get_post_error_initial_behavior(
    method_name: str,
    session_method: str,
    args: tuple[str],
    kwargs: dict[str, Any],
    make_client: MakeClientFactory,
) -> None:
    """Verify thrown request errors raise ``ClientResponseError``.

    Args:
        method_name (str): Client method to invoke (``_do_get`` or ``_do_post``).
        session_method (str): Session method to override (``get`` or ``post``).
        args (tuple[str]): Positional arguments passed to the client method.
        kwargs (dict[str, Any]): Keyword arguments passed to the client method.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts exception behavior when thrown errors are enabled.
    """
    client, session = make_mock_session_client(make_client)

    if session_method == "get":
        session.get = lambda *a, **k: FakeResponse(
            status=403,
            reason="Err",
            ok=False,
            json_payload={"x": 1},
            text_payload="raw response text",
            stream_chunks=[b"data: {}\n\n"],
            include_request_info=True,
        )
    else:
        session.post = lambda *a, **k: FakeResponse(
            status=500,
            reason="Err",
            ok=False,
            json_payload={"x": 1},
            text_payload="raw response text",
            stream_chunks=[b"data: {}\n\n"],
            include_request_info=True,
        )

    client._throw_errors = True
    try:
        with pytest.raises(aiohttp.ClientResponseError):
            await getattr(client, method_name)(*args, **kwargs)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_from_stream_parsing(
    make_client: MakeClientFactory,
    fake_stream_response_factory: FakeStreamResponseFactory,
) -> None:
    """Verify stream parsing returns the second valid SSE payload as mapping.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        fake_stream_response_factory (FakeStreamResponseFactory): Fixture function building stream
            response stubs.

    Returns:
        None: This test asserts stream payload parsing behavior.
    """
    client, session = make_mock_session_client(make_client)

    # use shared factory to construct a fake streaming response
    session.get = lambda *a, **k: fake_stream_response_factory(
        [b'data: {"a": 1}\n\n', b'data: {"b": 2}\n\n']
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
    make_client: MakeClientFactory,
    fake_stream_response_factory: FakeStreamResponseFactory,
) -> None:
    """Verify stream parser ignores first SSE data message and returns the second.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        fake_stream_response_factory (FakeStreamResponseFactory): Fixture function building stream
            response stubs.

    Returns:
        None: This test asserts SSE message selection behavior.
    """
    client, session = make_mock_session_client(make_client)

    session.get = lambda *a, **k: fake_stream_response_factory(
        [
            b'data: {"id": "first", "body": "ignore me"}\n\n',
            b'data: {"id": "second", "body": "keep me"}\n\n',
        ]
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
    make_client: MakeClientFactory,
    fake_stream_response_factory: FakeStreamResponseFactory,
) -> None:
    """Verify stream parser accumulates partial chunked payload fragments.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        fake_stream_response_factory (FakeStreamResponseFactory): Fixture function building stream
            response stubs.

    Returns:
        None: This test asserts chunk-buffer accumulation behavior.
    """
    client, session = make_mock_session_client(make_client)

    session.get = lambda *a, **k: fake_stream_response_factory(
        [b'data: {"a"', b": 1}\n\n", b'data: {"b": 2}\n\n']
    )
    try:
        res = await client._do_get_from_stream("/stream2", caller="tst")
        assert isinstance(res, MutableMapping)
    finally:
        await client.async_close()


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_do_get_from_stream_error_initial_raises(
    make_client: MakeClientFactory,
) -> None:
    """Verify thrown stream request errors raise on non-OK HTTP responses.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts stream exception behavior when errors are thrown.
    """
    client, session = make_mock_session_client(make_client)

    def fake_get(*args: Any, **kwargs: Any) -> Any:
        """Fake get.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Mock value returned to support test behavior.
        """
        return FakeResponse(
            status=403,
            reason="Forbidden",
            ok=False,
            stream_chunks=[b""],
            include_request_info=True,
        )

    session.get = fake_get
    try:
        client._throw_errors = True
        with pytest.raises(aiohttp.ClientResponseError):
            await client._do_get_from_stream("/bad", caller="t")
    finally:
        await client.async_close()


@pytest.mark.asyncio
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
async def test_do_get_and_do_post_success_paths(make_client: MakeClientFactory) -> None:
    """Verify ``_do_get`` and ``_do_post`` return parsed JSON for successful responses.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts successful low-level request parsing.
    """
    client, session = make_mock_session_client(make_client)

    def fake_get(*args: Any, **kwargs: Any) -> Any:
        """Fake get.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Mock value returned to support test behavior.
        """
        return FakeResponse(
            status=200,
            reason="OK",
            ok=True,
            json_payload={"a": 1},
            stream_chunks=[b""],
        )

    def fake_post(*args: Any, **kwargs: Any) -> Any:
        """Fake post.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Mock value returned to support test behavior.
        """
        return FakeResponse(
            status=200,
            reason="OK",
            ok=True,
            json_payload=[1, 2, 3],
            stream_chunks=[b""],
        )

    session.get = fake_get
    session.post = fake_post
    try:
        got = await client._do_get("/api/x", caller="t")
        assert isinstance(got, MutableMapping) and got.get("a") == 1

        posted = await client._do_post("/api/x", payload={"x": 1}, caller="t")
        assert isinstance(posted, list) and posted[0] == 1
    finally:
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


@pytest.mark.asyncio
async def test_do_get_post_and_stream_permission_errors(
    make_client: MakeClientFactory,
) -> None:
    """Verify permission failures do not raise when error throwing is disabled.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts non-raising behavior for 403 permission responses.
    """
    client, session = make_mock_session_client(make_client)
    session.get = lambda *a, **k: FakeResponse(
        status=403,
        reason="Forbidden",
        ok=False,
        json_payload={"err": 1},
        stream_chunks=[b""],
        include_request_info=True,
    )
    session.post = lambda *a, **k: FakeResponse(
        status=403,
        reason="Forbidden",
        ok=False,
        json_payload={"err": 1},
        stream_chunks=[b""],
        include_request_info=True,
    )
    try:
        client._throw_errors = False
        assert await client._do_get("/x", caller="t") is None
        assert await client._do_post("/x", payload={}, caller="t") is None
        assert await client._do_get_from_stream("/x", caller="t") == {}
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expect_warning", "expected_throw_errors"),
    [
        ({}, False, False),
        ({"throw_errors": True}, False, True),
        ({"initial": True}, True, True),
        ({"initial": False}, True, False),
        ({"initial": True, "throw_errors": False}, True, False),
    ],
)
async def test_client_constructor_throw_errors_configuration(
    kwargs: dict[str, bool],
    expect_warning: bool,
    expected_throw_errors: bool,
    make_client: MakeClientFactory,
) -> None:
    """Verify constructor error-propagation configuration and deprecation handling."""
    client: OPNsenseClient | None = None
    try:
        warning_context = (
            pytest.warns(DeprecationWarning, match="`initial` is deprecated")
            if expect_warning
            else contextlib.nullcontext()
        )
        with warning_context:
            client = make_client(**kwargs)
        assert client._throw_errors is expected_throw_errors
    finally:
        if client is not None:
            await client.async_close()


@pytest.mark.asyncio
async def test_client_constructor_invalid_throw_errors_raises_type_error(
    make_client: MakeClientFactory,
) -> None:
    """Verify invalid ``throw_errors`` values raise ``TypeError``."""
    with pytest.raises(TypeError, match="`throw_errors` must be a bool"):
        make_client(throw_errors="false")


@pytest.mark.asyncio
async def test_client_constructor_invalid_initial_raises_type_error(
    make_client: MakeClientFactory,
) -> None:
    """Verify invalid deprecated ``initial`` values raise ``TypeError``."""
    with pytest.raises(TypeError, match="`initial` must be a bool"):
        make_client(initial="false")


@pytest.mark.asyncio
async def test_client_constructor_allows_positional_name_after_initial() -> None:
    """Verify legacy positional ``name`` remains valid after ``initial``."""
    with pytest.warns(DeprecationWarning, match="`initial` is deprecated"):
        client = OPNsenseClient(
            "http://localhost",
            "u",
            "p",
            FakeClientSession(),  # type: ignore[arg-type]
            None,
            False,
            "Custom",
        )
    try:
        assert client.name == "Custom"
        assert client._throw_errors is False
    finally:
        await client.async_close()


def test_client_constructor_rejects_positional_throw_errors() -> None:
    """Verify ``throw_errors`` can no longer be passed positionally."""
    args: tuple[Any, ...] = (
        "http://localhost",
        "u",
        "p",
        FakeClientSession(),  # type: ignore[arg-type]
        None,
        False,
        "Custom",
        True,
    )
    with pytest.raises(TypeError):
        OPNsenseClient(*args)


@pytest.mark.asyncio
async def test_toggle_throwing_errors_updates_state(make_client: MakeClientFactory) -> None:
    """Verify ``toggle_throwing_errors`` toggles and sets the error mode."""
    client = make_client()
    try:
        assert client.toggle_throwing_errors() is True
        assert client._throw_errors is True
        assert client.toggle_throwing_errors(False) is False
        assert client._throw_errors is False
        assert client.toggle_throwing_errors(True) is True
        assert client._throw_errors is True
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_toggle_throwing_errors_invalid_value_raises_type_error(
    make_client: MakeClientFactory,
) -> None:
    """Verify invalid toggle values raise ``TypeError``."""
    client = make_client()
    try:
        with pytest.raises(TypeError, match="`throw_errors` must be a bool or None"):
            client.toggle_throwing_errors("false")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_client_name_property(make_client: MakeClientFactory) -> None:
    """Verify the client reports the expected human-readable name.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts the client name property value.
    """
    client, _session = make_mock_session_client(make_client, username="user", password="pass")
    try:
        assert client.name == "OPNsense"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_reset_and_get_query_counts(make_client: MakeClientFactory) -> None:
    """Verify query counter retrieval and reset behavior.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts query counter increment and reset semantics.
    """
    client, session = make_mock_session_client(make_client, username="user", password="pass")
    session.get = lambda *args, **kwargs: FakeResponse(
        status=200,
        reason="OK",
        ok=True,
        json_payload={"ok": True},
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
