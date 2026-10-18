"""Tests for client request queueing and worker lifecycle behavior."""

import asyncio
from collections.abc import MutableMapping
import contextlib
import json
from typing import Any, Never
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseError, client_queue as aiopnsense_client_queue, vpn as aiopnsense_vpn
from aiopnsense.exceptions import OPNsenseConnectionError
from tests.conftest import (
    FakeResponse,
    FakeStreamResponseFactory,
    MakeClientFactory,
    make_mock_session_client,
)


@pytest.fixture(autouse=True)
def no_queue_request_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the production pause between queued requests to keep tests fast.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(aiopnsense_client_queue, "QUEUE_REQUEST_INTERVAL_SECONDS", 0)


@pytest.mark.asyncio
async def test_opnsenseclient_async_close(make_client: MakeClientFactory) -> None:
    """Verify ``async_close`` cancels worker tasks and clears queued requests.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts cancellation and cleanup behavior.
    """
    client, _session = make_mock_session_client(make_client, username="user", password="pass")
    try:
        loop = asyncio.get_running_loop()
        worker1 = loop.create_task(asyncio.sleep(60))
        worker2 = loop.create_task(asyncio.sleep(60))
        client._workers = [worker1, worker2]
        client._request_queue = asyncio.Queue()
        future = loop.create_future()
        await client._request_queue.put(("get", "/api/test", None, future, "test"))
        await client.async_close()
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
        worker = loop.create_task(asyncio.sleep(60))
        client._workers = [worker]
        client._request_queue = asyncio.Queue()
        future = loop.create_future()
        await client._request_queue.put(("get", "/api/test", None, future, "test"))

        async with client as managed_client:
            assert managed_client is client
            client.validate.assert_awaited_once()

        assert worker.cancelled()
        assert future.done()
        assert isinstance(future.exception(), asyncio.CancelledError)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_active_loop_raises_when_worker_startup_leaves_loop_unset(
    make_client: MakeClientFactory,
) -> None:
    """Raise a public error when worker startup does not retain its event loop.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts the missing-loop failure contract.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        await client._ensure_workers_started()
        assert client._workers

        client._loop = None
        client._ensure_workers_started = AsyncMock()

        with pytest.raises(OPNsenseError, match="^Event loop is not initialized$"):
            await client._get_active_loop()
        client._ensure_workers_started.assert_awaited_once()
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
        assert isinstance(exc, OPNsenseError)
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
        async def fake_do_get(path: Any, caller: str = "x", response_format: str = "json") -> Any:
            """Fake ``_do_get`` with response format support.

            Args:
                path (Any): API endpoint path.
                caller (str): Caller name used for diagnostics.
                response_format (str): Expected response format.

            Returns:
                Any: JSON mapping for JSON format, text marker for text format.
            """
            if response_format == "text":
                return "text"
            return {"g": 1}

        client._do_get = AsyncMock(side_effect=fake_do_get)
        client._do_post = AsyncMock(return_value={"p": 2})
        client._do_get_from_stream = AsyncMock(return_value={"s": 3})

        # replace request queue with a real one
        q: asyncio.Queue = asyncio.Queue()
        client._request_queue = q

        # start the queue processor as a real task on the running loop (bypass patched asyncio.create_task)
        task = asyncio.get_running_loop().create_task(client._process_queue())

        loop = asyncio.get_running_loop()
        fut_get = loop.create_future()
        fut_get_text = loop.create_future()
        fut_post = loop.create_future()
        fut_stream = loop.create_future()

        await q.put(("get", "/g", None, fut_get, "t"))
        await q.put(("get_text", "/gt", None, fut_get_text, "t"))
        await q.put(("post", "/p", {"x": 1}, fut_post, "t"))
        await q.put(("get_from_stream", "/s", None, fut_stream, "t"))

        res1 = await asyncio.wait_for(fut_get, timeout=2)
        res_text = await asyncio.wait_for(fut_get_text, timeout=2)
        res2 = await asyncio.wait_for(fut_post, timeout=2)
        res3 = await asyncio.wait_for(fut_stream, timeout=2)

        assert res1 == {"g": 1}
        assert res_text == "text"
        assert res2 == {"p": 2}
        assert res3 == {"s": 3}
        assert client._do_get.await_count == 2
        client._do_get.assert_any_await("/g", "t")
        client._do_get.assert_any_await("/gt", "t", response_format="text")

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
async def test_process_queue_does_not_relog_transport_request_failure(
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Avoid duplicating a transport failure log in the queue worker.

    Args:
        make_client (MakeClientFactory): Factory for clients using a mocked HTTP session.
        caplog (pytest.LogCaptureFixture): Captured records used to inspect log count.
    """
    client, session = make_mock_session_client(make_client)
    client._throw_errors = True
    session.get.return_value = FakeResponse(status=500, reason="Server Error", ok=False)
    task: asyncio.Task | None = None
    try:
        client._request_queue = asyncio.Queue()
        task = asyncio.get_running_loop().create_task(client._process_queue())

        with caplog.at_level("ERROR"), pytest.raises(OPNsenseConnectionError):
            await client._get("/api/test/request")

        messages = [record.getMessage() for record in caplog.records]
        assert sum(message.startswith("Error in do_get") for message in messages) == 1
        assert not any(
            message.startswith("Exception in request queue processor") for message in messages
        )
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
        None: This test asserts queue integration for ``_get``.
    """
    client, _session = make_mock_session_client(make_client)
    task: asyncio.Task | None = None
    try:
        # replace request queue with a real one so _process_queue can run
        q: asyncio.Queue = asyncio.Queue()
        client._request_queue = q

        client._do_get = AsyncMock(return_value=returned)

        # start the real processor task
        task = asyncio.get_running_loop().create_task(client._process_queue())

        # call the high-level _get which will create a future and wait for processing
        res = await client._get("/testpath")

        assert res == returned
        client._do_get.assert_awaited_once()
        assert client._do_get.await_args.args[0] == "/testpath"
    finally:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await client.async_close()


@pytest.mark.asyncio
async def test_get_text_rejects_unexpected_type(make_client: MakeClientFactory) -> None:
    """Raise a public error when text-mode queue responses are not text.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts unexpected response types raise ``OPNsenseError``.
    """
    client, _session = make_mock_session_client(make_client)
    task: asyncio.Task | None = None
    try:
        q: asyncio.Queue = asyncio.Queue()
        client._request_queue = q

        client._do_get = AsyncMock(return_value={"ok": 1})

        task = asyncio.get_running_loop().create_task(client._process_queue())

        with pytest.raises(OPNsenseError, match=r"Expected text response"):
            await client._get_text("/text")
    finally:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [ValueError, AttributeError])
async def test_get_uses_unknown_when_caller_frame_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    make_client: MakeClientFactory,
    error_type: type[ValueError | AttributeError],
) -> None:
    """Verify ``_get`` uses ``Unknown`` caller when frame lookup fails.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for patching frame lookup.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        error_type (type[ValueError | AttributeError]): Frame lookup error to raise.

    Returns:
        None: This test asserts caller fallback behavior.
    """
    client, _session = make_mock_session_client(make_client)
    task: asyncio.Task | None = None
    try:

        class _BadSys:
            """Provide a ``sys`` substitute whose frame lookup always fails."""

            @staticmethod
            def _getframe(_depth: int) -> Never:
                """Raise an error because no caller frame is available.

                Args:
                    _depth (int): Requested frame depth.

                Returns:
                    Never: This method always raises the parameterized error.

                Raises:
                    error_type: The parameterized frame lookup error.
                """
                raise error_type("no frame")

        monkeypatch.setattr(aiopnsense_client_queue, "sys", _BadSys)

        q: asyncio.Queue = asyncio.Queue()
        client._request_queue = q

        captured: dict[str, Any] = {}

        async def fake_do_get(path: Any, caller: str = "x") -> Any:
            """Fake do get.

            Args:
                path (Any): API endpoint path to request.
                caller (str): Caller name used for diagnostics and logging.

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

        client._do_post = AsyncMock(return_value=returned)

        task = asyncio.get_running_loop().create_task(client._process_queue())

        payload = {"a": 1}
        res = await client._post("/postpath", payload=payload)

        assert res == returned
        client._do_post.assert_awaited_once()
        assert client._do_post.await_args.args[:2] == ("/postpath", payload)
    finally:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [ValueError, AttributeError])
async def test_post_uses_unknown_when_caller_frame_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    make_client: MakeClientFactory,
    error_type: type[ValueError | AttributeError],
) -> None:
    """Verify ``_post`` uses ``Unknown`` caller when frame lookup fails.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for patching frame lookup.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        error_type (type[ValueError | AttributeError]): Frame lookup error to raise.

    Returns:
        None: This test asserts caller fallback behavior for ``_post``.
    """
    client, _session = make_mock_session_client(make_client)
    task: asyncio.Task | None = None
    try:

        class _BadSys:
            """Provide a ``sys`` substitute whose frame lookup always fails."""

            @staticmethod
            def _getframe(_depth: int) -> Never:
                """Raise an error because no caller frame is available.

                Args:
                    _depth (int): Requested frame depth.

                Returns:
                    Never: This method always raises the parameterized error.

                Raises:
                    error_type: The parameterized frame lookup error.
                """
                raise error_type("no frame")

        monkeypatch.setattr(aiopnsense_client_queue, "sys", _BadSys)

        q: asyncio.Queue = asyncio.Queue()
        client._request_queue = q

        captured: dict[str, Any] = {}

        async def fake_do_post(path: Any, payload: Any = None, caller: str = "x") -> Any:
            """Fake do post.

            Args:
                path (Any): API endpoint path to request.
                payload (Any): Request payload sent to the API endpoint.
                caller (str): Caller name used for diagnostics and logging.

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
@pytest.mark.parametrize(
    ("source_error", "expect_cause"),
    [
        pytest.param(ValueError("boom"), True, id="mapped-error"),
        pytest.param(OPNsenseError("boom"), False, id="public-error"),
    ],
)
async def test_process_queue_exception_sets_future_exception(
    source_error: Exception,
    expect_cause: bool,
    make_client: MakeClientFactory,
) -> None:
    """Verify worker exceptions are propagated to request futures.

    Args:
        source_error (Exception): Error raised by the queued request handler.
        expect_cause (bool): Whether the queue maps and chains the source error.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts queue future error propagation.
    """
    client, _session = make_mock_session_client(make_client)
    task: asyncio.Task | None = None
    try:
        client._do_get = AsyncMock(side_effect=source_error)

        q: asyncio.Queue = asyncio.Queue()
        client._request_queue = q

        loop = asyncio.get_running_loop()
        task = loop.create_task(client._process_queue())

        fut = loop.create_future()
        await q.put(("get", "/g", None, fut, "t"))

        with pytest.raises(OPNsenseError, match="boom") as exc_info:
            await asyncio.wait_for(fut, timeout=2)
        if expect_cause:
            assert exc_info.value.__cause__ is source_error
        else:
            assert exc_info.value is source_error
            assert exc_info.value.__cause__ is None

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
            """Block a queued GET request until the test releases it.

            Args:
                _path (str): Queued API path passed by the queue worker.
                _caller (str): Caller name passed by the queue worker.

            Returns:
                MutableMapping[str, Any]: Empty response payload after the
                    release event is set.
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
        assert not hasattr(client, "_queue_monitor")
        assert client._workers == []

        client._do_get = AsyncMock(return_value={"ok": True})
        result = await client._get("/api/test")

        assert result == {"ok": True}
        assert client._loop is asyncio.get_running_loop()
        assert not hasattr(client, "_queue_monitor")
        assert len(client._workers) == client._max_workers
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_interface_traffic_does_not_enqueue_request(
    make_client: MakeClientFactory,
    fake_stream_response_factory: FakeStreamResponseFactory,
) -> None:
    """Live traffic stream should bypass the queued request worker.

    Args:
        make_client (MakeClientFactory): Fixture factory used to configure the stream client.
        fake_stream_response_factory (FakeStreamResponseFactory): Factory supplying the streamed traffic samples.
    """

    client, session = make_mock_session_client(make_client)
    client._is_get_endpoint_available = AsyncMock(return_value=True)
    client._queue_request = AsyncMock(side_effect=AssertionError("stream should not be queued"))
    session.get = lambda *_args, **_kwargs: fake_stream_response_factory(
        [
            b'data: {"time": 1, "interfaces": {"wan": {"bytes received": 0, "bytes transmitted": 0}}}\n\n',
            b'data: {"time": 2, "interfaces": {"wan": {"bytes received": 100, "bytes transmitted": 200}}}\n\n',
        ]
    )

    samples: list[dict[str, Any]] = []
    try:
        async for sample in client.stream_interface_traffic(poll_interval=1):
            samples.append(sample)
            break

        assert samples[0]["interfaces"]["wan"]["rx_bytes_per_second"] == 100.0
        client._queue_request.assert_not_called()
        assert client._workers == []
        assert not hasattr(client, "_queue_monitor")
    finally:
        await client.async_close()


class _MalformedJSONResponse(FakeResponse):
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


@pytest.mark.asyncio
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_get_ipsec_malformed_phase2_json_through_queue(
    make_client: MakeClientFactory, throw_errors: bool
) -> None:
    """Keep IPsec phase 1 data when a queued phase 2 reply is not valid JSON.

    The phase 2 POST runs through ``_post``, the request queue and ``_do_post``,
    so the decode error reaches ``get_ipsec`` as the queue's mapped error.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Client ``throw_errors`` setting.
    """
    client, session = make_mock_session_client(make_client)
    task: asyncio.Task | None = None
    try:
        client._use_snake_case = True
        client._throw_errors = throw_errors
        client._request_queue = asyncio.Queue()
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        sessions = {"rows": [{"ikeid": "ike-1", "name": "ike-1", "connected": True}]}
        client._safe_dict_get = AsyncMock(
            side_effect=lambda path: sessions if "search_phase1" in path else {"rows": []}
        )
        session.post = lambda *a, **k: _MalformedJSONResponse()
        task = asyncio.get_running_loop().create_task(client._process_queue())

        if throw_errors:
            with pytest.raises(OPNsenseError):
                await client.get_ipsec()
        else:
            ipsec = await client.get_ipsec()

            assert ipsec is not None
            assert set(ipsec["connections"]) == {"ike-1"}
            assert "children" not in ipsec
    finally:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_get_openvpn_malformed_routes_json_through_queue(
    make_client: MakeClientFactory, throw_errors: bool
) -> None:
    """Keep OpenVPN session status when a queued routes reply is not valid JSON.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Client ``throw_errors`` setting.

    Returns:
        None: This test asserts partial-result and strict-error behavior.
    """
    client, session = make_mock_session_client(make_client)
    sessions_endpoint = aiopnsense_vpn.OPENVPN_SESSIONS_SNAKE_ENDPOINT
    routes_endpoint = aiopnsense_vpn.OPENVPN_ROUTES_SNAKE_ENDPOINT

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        """Return a connected session and malformed routes at the HTTP boundary.

        Args:
            url (str): Requested URL.
            kwargs (Any): Unused HTTP options.

        Returns:
            FakeResponse: Queued HTTP response for the requested endpoint.
        """
        del kwargs
        if url.endswith(routes_endpoint):
            return _MalformedJSONResponse()
        if url.endswith(sessions_endpoint):
            return FakeResponse(
                json_payload={"rows": [{"id": "vpn-1_1", "type": "server", "status": "connected"}]}
            )
        return FakeResponse(json_payload={})

    try:
        client._use_snake_case = True
        client._throw_errors = throw_errors
        client._is_get_endpoint_available = AsyncMock(
            side_effect=lambda path: path in {sessions_endpoint, routes_endpoint}
        )
        client._endpoint_availability.update(
            {
                aiopnsense_vpn.OPENVPN_PROVIDERS_ENDPOINT: False,
                aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT: False,
            }
        )
        session.get = fake_get
        if throw_errors:
            with pytest.raises(OPNsenseError):
                await client.get_openvpn()
        else:
            openvpn = await client.get_openvpn()

            assert openvpn is not None
            assert openvpn["servers"]["vpn-1"]["status"] == "up"
            assert "clients" not in openvpn["servers"]["vpn-1"]
            assert "connected_clients" not in openvpn["servers"]["vpn-1"]
            assert openvpn["clients"] == {}
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("vpn", ["openvpn", "wireguard", "ipsec"])
@pytest.mark.parametrize(
    "reply", ["empty", "missing", "wrong_type", "http", "timeout", "decode", "unavailable"]
)
async def test_vpn_status_unavailable_vs_empty(
    make_client: MakeClientFactory, vpn: str, reply: str
) -> None:
    """Distinguish unavailable observations from empty sessions through the real queue.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        vpn (str): VPN getter to exercise.
        reply (str): Primary status response or failure to simulate.
    """
    client, session = make_mock_session_client(make_client)
    client._use_snake_case = True
    endpoints = {
        "openvpn": aiopnsense_vpn.OPENVPN_SESSIONS_SNAKE_ENDPOINT,
        "wireguard": aiopnsense_vpn.WIREGUARD_SUMMARY_ENDPOINT,
        "ipsec": aiopnsense_vpn.IPSEC_PHASE1_SESSIONS_SNAKE_ENDPOINT,
    }
    status_endpoint = endpoints[vpn]
    client._is_get_endpoint_available = AsyncMock(return_value=reply != "unavailable")
    config = {
        "uuid": "configured",
        "role": "server",
        "enabled": "1",
        "name": "configured",
        "instance": "1",
        "peers": {"peer": {"selected": "1", "value": "peer"}},
    }

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        """Return a status reply or configured inventory at the HTTP boundary.

        Args:
            url (str): Requested URL.
            kwargs (Any): Unused HTTP options.

        Returns:
            FakeResponse: Response for the requested endpoint.

        Raises:
            TimeoutError: Simulated primary status timeout.
        """
        if url.endswith(status_endpoint):
            if reply == "timeout":
                raise TimeoutError("status unavailable")
            payloads = {
                "empty": {"rows": []},
                "missing": {},
                "wrong_type": {"rows": {}},
            }
            response = FakeResponse(
                status=503 if reply == "http" else 200,
                ok=reply != "http",
                json_payload=payloads.get(reply),
            )
            if reply == "decode":
                response.json = AsyncMock(side_effect=json.JSONDecodeError("invalid", "", 0))
            return response
        if url.endswith(aiopnsense_vpn.OPENVPN_PROVIDERS_ENDPOINT):
            return FakeResponse(json_payload={"configured": {"name": "configured"}})
        if url.endswith(aiopnsense_vpn.OPENVPN_ROUTES_SNAKE_ENDPOINT):
            return FakeResponse(json_payload={"rows": []})
        return FakeResponse(
            json_payload={
                "rows": [config],
                "client": {"clients": {"client": {"peer": {"name": "peer"}}}},
                "server": {"servers": {"server": {"configured": config}}},
            }
        )

    session.get = fake_get
    try:
        result = await getattr(client, f"get_{vpn}")()
        if reply != "empty":
            assert result is None
        else:
            assert result is not None
            if vpn == "ipsec":
                assert result["connections"]["configured"]["connected"] is False
                assert result["children"]["configured"]["status"] == "disconnected"
                assert result["children"]["configured"]["total_bytes_recv"] == 0
            else:
                server = result["servers"]["configured"]
                assert server["connected_clients"] == 0
                assert server["total_bytes_recv"] == 0
                if vpn == "wireguard":
                    assert server["clients"][0]["connected"] is False
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("has_session", "reply"),
    [
        pytest.param(False, "empty", id="empty_without_session"),
        pytest.param(True, "empty", id="empty_with_session"),
        pytest.param(False, "malformed", id="malformed_without_session"),
        pytest.param(True, "malformed", id="malformed_with_session"),
        pytest.param(True, "http", id="http_with_session"),
        pytest.param(True, "timeout", id="timeout_with_session"),
        pytest.param(True, "decode", id="decode_with_session"),
    ],
)
async def test_ipsec_connection_inventory_failure_and_recovery(
    make_client: MakeClientFactory, has_session: bool, reply: str
) -> None:
    """Keep failed configuration reads distinct from empty inventories through the queue.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        has_session (bool): Whether phase 1 reports an active session.
        reply (str): Connections configuration response to simulate before recovery.
    """
    client, session = make_mock_session_client(make_client)
    client._use_snake_case = True
    client._is_get_endpoint_available = AsyncMock(return_value=True)
    recovered = False

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        """Return successful status and a failing or recovered configuration response.

        Args:
            url (str): Requested URL.
            kwargs (Any): Unused HTTP options.

        Returns:
            FakeResponse: Response for the requested endpoint.

        Raises:
            TimeoutError: Simulated connections configuration timeout.
        """
        if url.endswith(aiopnsense_vpn.IPSEC_PHASE1_SESSIONS_SNAKE_ENDPOINT):
            return FakeResponse(
                json_payload={
                    "rows": [{"ikeid": "active", "name": "active", "connected": True}]
                    if has_session
                    else []
                }
            )
        if url.endswith(aiopnsense_vpn.IPSEC_CONNECTIONS_SNAKE_ENDPOINT):
            if recovered:
                return FakeResponse(json_payload={"rows": [{"uuid": "disabled", "enabled": "0"}]})
            if reply == "timeout":
                raise TimeoutError("connections unavailable")
            response = FakeResponse(
                status=503 if reply == "http" else 200,
                ok=reply != "http",
                json_payload={"rows": [] if reply == "empty" else None},
            )
            if reply == "decode":
                response.json = AsyncMock(side_effect=json.JSONDecodeError("invalid", "", 0))
            return response
        return FakeResponse(json_payload={"rows": []})

    session.get = fake_get
    try:
        result = await client.get_ipsec()
        if reply == "empty":
            assert result is not None
            assert set(result["connections"]) == ({"active"} if has_session else set())
            if has_session:
                assert result["connections"]["active"]["enabled"] is None
        else:
            assert result is None

        recovered = True
        result = await client.get_ipsec()
        assert result is not None
        assert result["connections"]["disabled"]["enabled"] is False
        assert result["connections"]["disabled"]["status"] == "disconnected"
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("vpn", ["openvpn", "wireguard", "ipsec"])
@pytest.mark.parametrize("failure", ["http", "decode", "cancel"])
async def test_vpn_status_propagates_errors(
    make_client: MakeClientFactory, vpn: str, failure: str
) -> None:
    """Preserve strict request errors and cancellation through the real queue.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        vpn (str): VPN getter to exercise.
        failure (str): HTTP, JSON decoding, or cancellation failure.
    """
    client, session = make_mock_session_client(make_client)
    client._throw_errors = True
    client._use_snake_case = True
    client._is_get_endpoint_available = AsyncMock(return_value=True)
    response = FakeResponse(status=503, ok=False)
    if failure != "http":
        response = FakeResponse()
        error = (
            asyncio.CancelledError() if failure == "cancel" else json.JSONDecodeError("bad", "", 0)
        )
        response.json = AsyncMock(side_effect=error)
    session.get = lambda *args, **kwargs: response
    try:
        expected = asyncio.CancelledError if failure == "cancel" else OPNsenseError
        with pytest.raises(expected):
            await getattr(client, f"get_{vpn}")()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("vpn", "endpoint", "collection", "has_session", "reply"),
    [
        pytest.param(
            "openvpn",
            endpoint,
            "servers",
            has_session,
            reply,
            id=f"openvpn_{source}_{reply}_{'with' if has_session else 'without'}_session",
        )
        for source, endpoint in (
            ("instances", aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT),
            ("providers", aiopnsense_vpn.OPENVPN_PROVIDERS_ENDPOINT),
        )
        for has_session, reply in (
            (False, "empty"),
            (True, "empty"),
            (False, "malformed"),
            (True, "malformed"),
            (True, "http"),
            (True, "timeout"),
            (True, "decode"),
            (True, "missing"),
            (True, "probe"),
            (True, "forbidden"),
        )
    ]
    + [
        pytest.param(
            "wireguard",
            endpoint,
            collection,
            False,
            reply,
            id=f"wireguard_{source}_{reply}",
        )
        for source, endpoint, collection in (
            ("clients", aiopnsense_vpn.WIREGUARD_CLIENTS_ENDPOINT, "clients"),
            ("servers", aiopnsense_vpn.WIREGUARD_SERVERS_ENDPOINT, "servers"),
        )
        for reply in ("http", "timeout", "decode", "missing", "malformed", "empty")
    ],
)
async def test_vpn_configuration_inventory_failure_and_recovery(
    make_client: MakeClientFactory,
    vpn: str,
    endpoint: str,
    collection: str,
    has_session: bool,
    reply: str,
) -> None:
    """Reject incomplete inventories and recover disabled entries through the real queue.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        vpn (str): VPN getter to exercise.
        endpoint (str): Configuration endpoint that fails before recovery.
        collection (str): Inventory collection populated on recovery.
        has_session (bool): Whether status reports an active session.
        reply (str): Configuration response or probe failure to simulate.
    """
    client, session = make_mock_session_client(make_client)
    client._use_snake_case = True
    recovered = False
    if reply == "forbidden":
        client._endpoint_availability[endpoint] = False
    client._is_get_endpoint_available = AsyncMock(
        side_effect=lambda path: (
            recovered or path != endpoint or reply not in {"probe", "forbidden"}
        )
    )
    config = {"uuid": "disabled", "role": "server", "enabled": "0", "name": "disabled"}
    populated = {
        aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT: {"rows": [config]},
        aiopnsense_vpn.OPENVPN_PROVIDERS_ENDPOINT: {"disabled": {"name": "disabled"}},
        aiopnsense_vpn.WIREGUARD_CLIENTS_ENDPOINT: {
            "client": {"clients": {"client": {"disabled": config}}}
        },
        aiopnsense_vpn.WIREGUARD_SERVERS_ENDPOINT: {
            "server": {"servers": {"server": {"disabled": config}}}
        },
    }
    empty: dict[str, Any] = {
        aiopnsense_vpn.OPENVPN_INSTANCES_SEARCH_ENDPOINT: {"rows": []},
        aiopnsense_vpn.OPENVPN_PROVIDERS_ENDPOINT: [],
        aiopnsense_vpn.WIREGUARD_CLIENTS_ENDPOINT: {"client": {"clients": {"client": {}}}},
        aiopnsense_vpn.WIREGUARD_SERVERS_ENDPOINT: {"server": {"servers": {"server": {}}}},
    }

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        """Return a configuration failure, valid empty inventory, or recovered inventory.

        Args:
            url (str): Requested URL.
            kwargs (Any): Unused HTTP options.

        Returns:
            FakeResponse: Response for the requested endpoint.

        Raises:
            TimeoutError: Simulated configuration timeout.
        """
        if url.endswith(endpoint):
            if recovered:
                return FakeResponse(json_payload=populated[endpoint])
            if reply == "timeout":
                raise TimeoutError("configuration unavailable")
            payload: Any = empty[endpoint] if reply == "empty" else None
            if reply == "missing":
                # An empty providers mapping is valid; other inventories require their container.
                payload = None if endpoint == aiopnsense_vpn.OPENVPN_PROVIDERS_ENDPOINT else {}
            if reply == "malformed":
                payload = {"rows": None} if vpn == "openvpn" else {"client": None, "server": None}
                if endpoint == aiopnsense_vpn.OPENVPN_PROVIDERS_ENDPOINT:
                    payload = ["invalid"]
            response = FakeResponse(
                status=503 if reply == "http" else 200,
                ok=reply != "http",
                json_payload=payload,
            )
            if reply == "decode":
                response.json = AsyncMock(side_effect=json.JSONDecodeError("invalid", "", 0))
            return response
        for path, payload in empty.items():
            if url.endswith(path):
                return FakeResponse(json_payload=payload)
        return FakeResponse(
            json_payload={
                "rows": [{"id": "active_1", "type": "server", "status": "connected"}]
                if has_session
                else []
            }
        )

    session.get = fake_get
    try:
        result = await getattr(client, f"get_{vpn}")()
        if reply in {"empty", "forbidden"}:
            assert result is not None
            assert "disabled" not in result[collection]
            if vpn == "openvpn" and has_session:
                assert result["servers"]["active"]["status"] == "up"
        else:
            assert result is None
        recovered = True
        result = await getattr(client, f"get_{vpn}")()
        assert result is not None
        assert "disabled" in result[collection]
        if endpoint != aiopnsense_vpn.OPENVPN_PROVIDERS_ENDPOINT:
            assert result[collection]["disabled"]["enabled"] is False
    finally:
        await client.async_close()
