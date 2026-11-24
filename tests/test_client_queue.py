"""Tests for client request queueing and worker lifecycle behavior."""

import asyncio
from collections.abc import MutableMapping
import contextlib
import json
import logging
import re
from typing import Any, Literal, Never
from unittest.mock import AsyncMock

import pytest

from aiopnsense import (
    OPNsenseError,
    OPNsenseInvalidArgument,
    client_transport as aiopnsense_client_transport,
)
from aiopnsense.client_queue import _QueueEntry
from aiopnsense.exceptions import OPNsenseConnectionError
from tests.conftest import (
    FakeResponse,
    FakeStreamResponseFactory,
    MakeClientFactory,
    MalformedJSONResponse,
    VpnQueueTasks,
    make_mock_session_client,
)

IPSEC_PHASE1_SESSIONS_SNAKE_PATH = "/api/ipsec/sessions/search_phase1"
OPENVPN_PROVIDERS_PATH = "/api/openvpn/export/providers"
OPENVPN_INSTANCES_SEARCH_PATH = "/api/openvpn/instances/search"
OPENVPN_ROUTES_SNAKE_PATH = "/api/openvpn/service/search_routes"
OPENVPN_SESSIONS_SNAKE_PATH = "/api/openvpn/service/search_sessions"
UNBOUND_SERVICE_DNSBL_PATH = "/api/unbound/service/dnsbl"
WIREGUARD_CLIENTS_PATH = "/api/wireguard/client/get"
WIREGUARD_SERVERS_PATH = "/api/wireguard/server/get"


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
        await client._request_queue.put(_QueueEntry("get", "/api/test", None, future, "test"))
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
        await client._request_queue.put(_QueueEntry("get", "/api/test", None, future, "test"))

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

        # start the queue processor as a real task on the running loop
        task = asyncio.get_running_loop().create_task(client._process_queue())

        loop = asyncio.get_running_loop()
        fut_get = loop.create_future()
        fut_post = loop.create_future()
        fut_stream = loop.create_future()

        await q.put(_QueueEntry("get", "/g", None, fut_get, "t"))
        await q.put(_QueueEntry("post", "/p", {"x": 1}, fut_post, "t"))
        await q.put(_QueueEntry("get_from_stream", "/s", None, fut_stream, "t"))

        res1 = await asyncio.wait_for(fut_get, timeout=2)
        res2 = await asyncio.wait_for(fut_post, timeout=2)
        res3 = await asyncio.wait_for(fut_stream, timeout=2)

        assert res1 == {"g": 1}
        assert res2 == {"p": 2}
        assert res3 == {"s": 3}
        assert client._do_get.await_count == 1
        client._do_get.assert_any_await("/g", "t")

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
async def test_failed_stream_read_logs_once_through_queue_and_public_wrapper(
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Report a failed stream read once with ``throw_errors`` enabled.

    The failure passes through the real request queue and the ``_log_errors`` wrapper of
    the public telemetry reader, and neither of them may repeat the transport's log.

    Args:
        make_client (MakeClientFactory): Factory for clients using a mocked HTTP session.
        caplog (pytest.LogCaptureFixture): Captured records used to inspect log count.
    """
    client, session = make_mock_session_client(make_client)
    client._throw_errors = True
    session.get.return_value = FakeResponse(status=500, reason="Server Error", ok=False)
    client._use_snake_case = True
    try:
        with caplog.at_level("ERROR"), pytest.raises(OPNsenseConnectionError):
            await client._get_telemetry_cpu()

        assert [record.levelname for record in caplog.records] == ["ERROR"]
    finally:
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


async def test_unbound_toggle_identifies_failed_save_in_queue_diagnostics(
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """Identify the public Unbound operation and endpoint when a queued save fails.

    Args:
        make_client (MakeClientFactory): Fixture factory returning client instances.
        caplog (pytest.LogCaptureFixture): Captured transport diagnostics.
        legacy_dnsbl_payload (dict[str, Any]): Complete settings that allow the save request.

    Returns:
        None: This test asserts the operation label and request path in the diagnostics.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client.get_host_firmware_version = AsyncMock(return_value="25.7.7")
        client._safe_dict_get = AsyncMock(return_value=legacy_dnsbl_payload)
        session.post.return_value = FakeResponse(
            status=500, reason="Internal Server Error", ok=False
        )

        with caplog.at_level(logging.ERROR, logger="aiopnsense"):
            result = await client.enable_unbound_blocklist()

        assert result is False
        assert any(
            "[enable_unbound_blocklist] save" in record.message
            and "/api/unbound/settings/set" in record.message
            for record in caplog.records
        )
    finally:
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

        monkeypatch.setattr(aiopnsense_client_transport, "sys", _BadSys)

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

        monkeypatch.setattr(aiopnsense_client_transport, "sys", _BadSys)

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
        await q.put(_QueueEntry("get", "/g", None, fut, "t"))

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
        await q.put(_QueueEntry("get", "/g", None, fut, "t"))

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


@pytest.mark.parametrize("throw_errors", [False, True])
async def test_legacy_unbound_apply_http_failure_through_queue(
    make_client: MakeClientFactory,
    legacy_dnsbl_payload: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    throw_errors: bool,
) -> None:
    """Log the DNSBL apply URL without credentials and skip the service restart.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        legacy_dnsbl_payload (dict[str, Any]): Complete legacy DNSBL settings.
        caplog (pytest.LogCaptureFixture): Captured request logs.
        throw_errors (bool): Whether the client raises public request errors.
    """
    base_url = "https://router.example.invalid"
    url_username, url_password = "fake-url-user", "fake-url-password"
    configured_url = f"https://{url_username}:{url_password}@router.example.invalid"
    username, password = "fake-api-key", "fake-api-secret"
    client, session = make_mock_session_client(
        make_client, username=username, password=password, url=configured_url
    )
    client.toggle_throwing_errors(throw_errors)
    client._firmware_version = "25.7.7"
    apply_url = f"{base_url}{UNBOUND_SERVICE_DNSBL_PATH}"
    session.get.side_effect = [
        FakeResponse(json_payload=legacy_dnsbl_payload),
        FakeResponse(status=503, reason="Service Unavailable", ok=False),
    ]
    session.post.side_effect = [
        FakeResponse(json_payload={"result": "saved"}),
        FakeResponse(json_payload={"response": "OK"}),
    ]

    try:
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            if throw_errors:
                with pytest.raises(OPNsenseConnectionError, match="503"):
                    await client.enable_unbound_blocklist()
            else:
                assert await client.enable_unbound_blocklist() is False

        assert [call.args[0] for call in session.get.call_args_list] == [
            f"{configured_url}/api/unbound/settings/get",
            f"{configured_url}{UNBOUND_SERVICE_DNSBL_PATH}",
        ]
        assert [call.args[0] for call in session.post.call_args_list] == [
            f"{configured_url}/api/unbound/settings/set"
        ]
        http_failures = [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.WARNING
            and "503" in record.getMessage()
            and "Service Unavailable" in record.getMessage()
        ]
        assert len(http_failures) == 1
        assert apply_url in http_failures[0]
        assert username not in caplog.text
        assert password not in caplog.text
        assert url_username not in caplog.text
        assert url_password not in caplog.text
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
        sessions = {"rows": [{"ikeid": "ike-1", "name": "ike-1", "connected": True}]}
        session.get = lambda url, **kwargs: FakeResponse(
            json_payload=sessions if url.endswith("search_phase1") else {"rows": []}
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
    sessions_endpoint = OPENVPN_SESSIONS_SNAKE_PATH
    routes_endpoint = OPENVPN_ROUTES_SNAKE_PATH

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
        if url.endswith(
            (
                OPENVPN_PROVIDERS_PATH,
                OPENVPN_INSTANCES_SEARCH_PATH,
            )
        ):
            # Providers and instances are not provided by this Router.
            return FakeResponse(status=404, reason="Not Found", ok=False)
        return FakeResponse(json_payload={})

    try:
        client._use_snake_case = True
        client._throw_errors = throw_errors
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


@pytest.mark.parametrize("throw_errors", [False, True])
async def test_get_openvpn_keeps_connected_session_when_inventory_probe_returns_404(
    make_client: MakeClientFactory, throw_errors: bool
) -> None:
    """Preserve primary OpenVPN session data when provider inventory is absent.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        throw_errors (bool): Whether the client raises public request errors.
    """
    base_url = "https://router.example.invalid"
    client, session = make_mock_session_client(make_client, url=base_url)
    client.toggle_throwing_errors(throw_errors)
    client._use_snake_case = True
    providers_url = f"{base_url}{OPENVPN_PROVIDERS_PATH}"
    responses = {
        f"{base_url}{OPENVPN_SESSIONS_SNAKE_PATH}": FakeResponse(
            json_payload={"rows": [{"id": "vpn-1_1", "type": "server", "status": "connected"}]}
        ),
        f"{base_url}{OPENVPN_ROUTES_SNAKE_PATH}": FakeResponse(json_payload={"rows": []}),
        providers_url: FakeResponse(status=404, reason="Not Found", ok=False),
        f"{base_url}{OPENVPN_INSTANCES_SEARCH_PATH}": FakeResponse(json_payload={"rows": []}),
        f"{base_url}/api/openvpn/instances/get/vpn-1": FakeResponse(json_payload={"instance": {}}),
    }
    session.get.side_effect = lambda url, **kwargs: responses[url]

    try:
        openvpn = await client.get_openvpn()

        assert openvpn is not None
        assert set(openvpn["servers"]) == {"vpn-1"}
        assert openvpn["servers"]["vpn-1"]["status"] == "up"
        assert openvpn["servers"]["vpn-1"]["connected_clients"] == 0
        assert [call.args[0] for call in session.get.call_args_list].count(providers_url) == 1
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
        "openvpn": OPENVPN_SESSIONS_SNAKE_PATH,
        "wireguard": "/api/wireguard/service/show",
        "ipsec": IPSEC_PHASE1_SESSIONS_SNAKE_PATH,
    }
    status_endpoint = endpoints[vpn]
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
        if url.endswith(OPENVPN_PROVIDERS_PATH):
            return FakeResponse(json_payload={"configured": {"name": "configured"}})
        if url.endswith(OPENVPN_ROUTES_SNAKE_PATH):
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
        if url.endswith(IPSEC_PHASE1_SESSIONS_SNAKE_PATH):
            return FakeResponse(
                json_payload={
                    "rows": [{"ikeid": "active", "name": "active", "connected": True}]
                    if has_session
                    else []
                }
            )
        if url.endswith("/api/ipsec/connections/search_connection"):
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
            ("instances", OPENVPN_INSTANCES_SEARCH_PATH),
            ("providers", OPENVPN_PROVIDERS_PATH),
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
            ("clients", WIREGUARD_CLIENTS_PATH, "clients"),
            ("servers", WIREGUARD_SERVERS_PATH, "servers"),
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
    config = {"uuid": "disabled", "role": "server", "enabled": "0", "name": "disabled"}
    populated = {
        OPENVPN_INSTANCES_SEARCH_PATH: {"rows": [config]},
        OPENVPN_PROVIDERS_PATH: {"disabled": {"name": "disabled"}},
        WIREGUARD_CLIENTS_PATH: {"client": {"clients": {"client": {"disabled": config}}}},
        WIREGUARD_SERVERS_PATH: {"server": {"servers": {"server": {"disabled": config}}}},
    }
    empty: dict[str, Any] = {
        OPENVPN_INSTANCES_SEARCH_PATH: {"rows": []},
        OPENVPN_PROVIDERS_PATH: [],
        WIREGUARD_CLIENTS_PATH: {"client": {"clients": {"client": {}}}},
        WIREGUARD_SERVERS_PATH: {"server": {"servers": {"server": {}}}},
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
            if reply == "forbidden":
                return FakeResponse(status=403, reason="Forbidden", ok=False)
            payload: Any = empty[endpoint] if reply == "empty" else None
            if reply == "missing":
                # An empty providers mapping is valid; other inventories require their container.
                payload = None if endpoint == OPENVPN_PROVIDERS_PATH else {}
            if reply == "malformed":
                payload = {"rows": None} if vpn == "openvpn" else {"client": None, "server": None}
                if endpoint == OPENVPN_PROVIDERS_PATH:
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
        # A forbidden answer is cached outside throw mode; drop it as the cache TTL would.
        client._clear_endpoint_cache_entry(endpoint)
        result = await getattr(client, f"get_{vpn}")()
        assert result is not None
        assert "disabled" in result[collection]
        if endpoint != OPENVPN_PROVIDERS_PATH:
            assert result[collection]["disabled"]["enabled"] is False
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_wireguard_cancelled_dispatched_toggle_retains_ack_and_lock(
    make_client: MakeClientFactory, vpn_queue_tasks: VpnQueueTasks
) -> None:
    """Retain a save acknowledgement delivered after caller cancellation.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        vpn_queue_tasks (VpnQueueTasks): Tracks test tasks for cleanup.
    """
    client = make_client()
    client._use_snake_case = True
    client._do_get = AsyncMock(return_value={"client": {"enabled": "0"}})
    started, release = asyncio.Event(), asyncio.Event()
    toggle = "/api/wireguard/client/toggle_client/uuid/1"
    apply = "/api/wireguard/service/reconfigure"

    async def post(path: str, *_request_args: object) -> dict[str, object]:
        """Pause the legacy firmware toggle until its caller has cancelled.

        Args:
            path (str): Requested endpoint.
            *_request_args (object): Unused POST payload and caller.

        Returns:
            dict[str, object]: Acknowledgement for the dispatched request.
        """
        if path == toggle:
            started.set()
            await release.wait()
            return {"changed": True, "result": "Disabled"}
        return {"result": "ok"}

    client._do_post = AsyncMock(side_effect=post)
    try:
        cancelled = vpn_queue_tasks.start(
            client.toggle_vpn_instance("wireguard", "clients", "uuid", "on")
        )
        await asyncio.wait_for(started.wait(), timeout=3)
        cancelled.cancel()
        retry = vpn_queue_tasks.start(
            client.toggle_vpn_instance("wireguard", "clients", "uuid", "off"), eager_start=True
        )
        await asyncio.sleep(0)
        assert not cancelled.done()
        client._do_get.assert_awaited_once()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(cancelled, timeout=3)
        assert await asyncio.wait_for(retry, timeout=3) is True
        assert [call.args[0] for call in client._do_post.await_args_list] == [toggle, apply]
    finally:
        release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("vpn_type", "collection", "toggle", "apply", "apply_ok"),
    [
        pytest.param(
            "openvpn",
            "instances",
            "/api/openvpn/instances/toggle/uuid/1",
            "/api/openvpn/service/reconfigure",
            {"result": "ok"},
            id="openvpn",
        ),
        pytest.param(
            "ipsec",
            "connections",
            "/api/ipsec/connections/toggle_connection/uuid/1",
            "/api/ipsec/service/reconfigure",
            {"status": "ok"},
            id="ipsec",
        ),
    ],
)
async def test_cancelled_dispatched_vpn_save_is_applied_by_next_matching_toggle(
    make_client: MakeClientFactory,
    vpn_queue_tasks: VpnQueueTasks,
    vpn_type: str,
    collection: str,
    toggle: str,
    apply: str,
    apply_ok: dict[str, object],
) -> None:
    """A caller cancelled after its save was dispatched waits, then re-raises.

    The acknowledged save is tracked, so a later request for the same target, which
    the Router reports as already matching, still applies the service.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        vpn_queue_tasks (VpnQueueTasks): Tracks test tasks for cleanup.
        vpn_type (str): VPN service under test.
        collection (str): Collection containing the instance.
        toggle (str): Save endpoint for target ``on``.
        apply (str): Service apply endpoint.
        apply_ok (dict[str, object]): Confirmed apply reply.
    """
    client = make_client()
    client._use_snake_case = True
    started, release = asyncio.Event(), asyncio.Event()

    async def post(path: str, *_request_args: object) -> dict[str, object]:
        """Hold the first save until its caller has cancelled.

        Args:
            path (str): Requested endpoint.
            *_request_args (object): Unused POST payload and caller.

        Returns:
            dict[str, object]: Router reply.
        """
        if path == apply:
            return apply_ok
        if not started.is_set():
            started.set()
            await release.wait()
            return {"changed": True, "result": "Enabled"}
        return {"changed": False, "result": "Enabled"}

    client._do_post = AsyncMock(side_effect=post)
    try:
        cancelled = vpn_queue_tasks.start(
            client.toggle_vpn_instance(vpn_type, collection, "uuid", "on")
        )
        await asyncio.wait_for(started.wait(), timeout=3)
        cancelled.cancel()
        await asyncio.sleep(0)
        assert not cancelled.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(cancelled, timeout=3)
        assert [call.args[0] for call in client._do_post.await_args_list] == [toggle]

        assert await client.toggle_vpn_instance(vpn_type, collection, "uuid", "on") is True
        assert [call.args[0] for call in client._do_post.await_args_list] == [
            toggle,
            toggle,
            apply,
        ]
    finally:
        release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_result", "retry_applies"),
    [
        pytest.param("ok", False, id="acknowledged"),
        pytest.param("failed", True, id="retry-needed"),
    ],
)
async def test_cancelled_service_apply_stays_serialized_until_acknowledged(
    make_client: MakeClientFactory,
    vpn_queue_tasks: VpnQueueTasks,
    first_result: str,
    retry_applies: bool,
) -> None:
    """Keep the service lock until the cancelled apply receives its response.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        vpn_queue_tasks (VpnQueueTasks): Tracks test tasks for cleanup.
        first_result (str): Response from the first apply.
        retry_applies (bool): Whether the next caller must reapply the saved state.
    """
    client = make_client()
    started, release = asyncio.Event(), asyncio.Event()
    toggle = "/api/openvpn/instances/toggle/uuid/1"
    apply = "/api/openvpn/service/reconfigure"
    dispatched: list[str] = []

    async def post(path: str, *_request_args: object) -> dict[str, object]:
        """Delay the first apply response while preserving later responses.

        Args:
            path (str): Requested endpoint.
            *_request_args (object): Unused POST payload and caller.

        Returns:
            dict[str, object]: Acknowledgement for the dispatched request.
        """
        dispatched.append(path)
        if path == apply:
            if not started.is_set():
                started.set()
                await release.wait()
                return {"result": first_result}
            return {"result": "ok"}
        return {"changed": not started.is_set(), "result": "Enabled"}

    client._do_post = AsyncMock(side_effect=post)
    try:
        cancelled = vpn_queue_tasks.start(
            client.toggle_vpn_instance("openvpn", "servers", "uuid", "on")
        )
        await asyncio.wait_for(started.wait(), timeout=3)
        cancelled.cancel()
        retry = vpn_queue_tasks.start(
            client.toggle_vpn_instance("openvpn", "servers", "uuid", "on"), eager_start=True
        )
        await asyncio.sleep(0)
        assert not cancelled.done()
        assert dispatched == [toggle, apply]

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(cancelled, timeout=3)
        assert await asyncio.wait_for(retry, timeout=3) is True
        expected = [toggle, apply, toggle]
        if retry_applies:
            expected.append(apply)
        assert dispatched == expected
    finally:
        release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cancellation_phase",
    ["queued-save", "queued-apply"],
)
async def test_cancelled_firewall_rule_toggle_skips_queued_post(
    make_client: MakeClientFactory,
    vpn_queue_tasks: VpnQueueTasks,
    cancellation_phase: str,
) -> None:
    """Skip a queued Firewall Rule Toggle POST when its caller is cancelled.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        vpn_queue_tasks (VpnQueueTasks): Tracks test tasks for cleanup.
        cancellation_phase (str): Whether cancellation occurs before save or apply dispatch.
    """
    client, session = make_mock_session_client(make_client)
    client._max_workers = 1
    get_started = asyncio.Event()
    release_get = asyncio.Event()
    save_started = asyncio.Event()
    release_save = asyncio.Event()

    async def blocked_json(*, content_type: str | None = None) -> dict[str, str]:
        """Hold the worker while it reads a public status response.

        Args:
            content_type (str | None): Unused response decoding option.

        Returns:
            dict[str, str]: Controlled firmware upgrade status.
        """
        get_started.set()
        await release_get.wait()
        return {"status": "running"}

    async def blocked_save_json(*, content_type: str | None = None) -> dict[str, str]:
        """Hold the save response until a public read is queued behind it.

        Args:
            content_type (str | None): Unused response decoding option.

        Returns:
            dict[str, str]: Acknowledged Firewall Rule save.
        """
        save_started.set()
        await release_save.wait()
        return {"result": "Enabled"}

    blocked_response = FakeResponse()
    blocked_response.json = AsyncMock(side_effect=blocked_json)
    session.get.return_value = blocked_response
    save_response = FakeResponse()
    save_response.json = AsyncMock(side_effect=blocked_save_json)
    session.post.return_value = save_response
    try:
        if cancellation_phase == "queued-save":
            read = vpn_queue_tasks.start(client.upgrade_status())
            await asyncio.wait_for(get_started.wait(), timeout=3)
            toggle = vpn_queue_tasks.start(
                client.toggle_firewall_rule("uuid", "on"), eager_start=True
            )
            # Ensure the public Toggle has queued its save before cancelling.
            assert client._request_queue.qsize() == 1
        else:
            toggle = vpn_queue_tasks.start(
                client.toggle_firewall_rule("uuid", "on"), eager_start=True
            )
            await asyncio.wait_for(save_started.wait(), timeout=3)
            read = vpn_queue_tasks.start(client.upgrade_status(), eager_start=True)
            assert client._request_queue.qsize() == 1
            release_save.set()
            await asyncio.wait_for(get_started.wait(), timeout=3)
            # The worker resolves the save before it begins this queued public read.
            # The Toggle then queues its apply behind the read.
            assert client._request_queue.qsize() == 1

        toggle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(toggle, timeout=3)
        release_save.set()
        release_get.set()
        assert await asyncio.wait_for(read, timeout=3) == {"status": "running"}
        session.get.return_value = FakeResponse(json_payload={"status": "done"})
        assert await asyncio.wait_for(client.upgrade_status(), timeout=3) == {"status": "done"}
        if cancellation_phase == "queued-save":
            session.post.assert_not_called()
        else:
            session.post.assert_called_once()
            assert session.post.call_args.args[0].endswith(
                "/api/firewall/filter/toggle_rule/uuid/1"
            )
    finally:
        release_save.set()
        release_get.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("firmware", "item"),
    [("25.7.7", None), ("25.7.8", "blocklist-id")],
    ids=["legacy", "extended"],
)
async def test_unbound_toggle_returns_false_on_malformed_apply_response(
    make_client: MakeClientFactory,
    firmware: str,
    item: str | None,
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """Return False when an HTTP 200 apply reply cannot be parsed.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        firmware (str): Firmware selecting the Unbound backend.
        item (str | None): Extended blocklist identifier, if applicable.
        legacy_dnsbl_payload (dict[str, Any]): Complete settings for the legacy save.
    """
    client, session = make_mock_session_client(make_client)
    client.get_host_firmware_version = AsyncMock(return_value=firmware)
    malformed_apply = FakeResponse()
    malformed_apply.json = AsyncMock(side_effect=json.JSONDecodeError("invalid", "<html>", 0))

    base_url = "http://localhost/api/unbound"
    apply_url = f"{base_url}/service/dnsbl"
    get_responses: dict[str, FakeResponse] = {}
    post_responses: dict[str, FakeResponse] = {}
    if item is None:
        get_responses[f"{base_url}/settings/get"] = FakeResponse(json_payload=legacy_dnsbl_payload)
        get_responses[apply_url] = malformed_apply
        post_responses[f"{base_url}/settings/set"] = FakeResponse(json_payload={"result": "saved"})
    else:
        post_responses[f"{base_url}/settings/toggle_dnsbl/{item}/1"] = FakeResponse(
            json_payload={"result": "Enabled"}
        )
        post_responses[apply_url] = malformed_apply
    session.get.side_effect = lambda url, **_: get_responses[url]
    session.post.side_effect = lambda url, **_: post_responses[url]

    assert await client.enable_unbound_blocklist(item) is False
    apply_request = session.get if item is None else session.post
    assert any(call.args[0] == apply_url for call in apply_request.call_args_list)
    assert all(args[0] != f"{base_url}/service/restart" for args, _ in session.post.call_args_list)


@pytest.mark.asyncio
async def test_cancelled_queued_public_toggle_is_not_dispatched(
    make_client: MakeClientFactory, vpn_queue_tasks: VpnQueueTasks
) -> None:
    """Skip a public targeted toggle cancelled while queued behind another POST.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        vpn_queue_tasks (VpnQueueTasks): Tracks test tasks for cleanup.
    """
    client = make_client()
    client._max_workers = 1
    blocking_post_started = asyncio.Event()
    release_blocking_post = asyncio.Event()
    dispatched: list[str] = []

    async def do_post(
        path: str, payload: MutableMapping[str, Any] | None = None, caller: str = "Unknown"
    ) -> MutableMapping[str, Any]:
        """Block the worker, then record requests dispatched after it resumes.

        Args:
            path (str): Endpoint path.
            payload (MutableMapping[str, Any] | None): Optional request body.
            caller (str): Queue caller label.

        Returns:
            MutableMapping[str, Any]: Simulated success response.
        """
        dispatched.append(path)
        if path == "/block":
            blocking_post_started.set()
            await release_blocking_post.wait()
        return {"result": "ok"}

    client._do_post = AsyncMock(side_effect=do_post)
    try:
        blocker = vpn_queue_tasks.start(client._post("/block"))
        await asyncio.wait_for(blocking_post_started.wait(), timeout=3)
        cancelled_toggle = vpn_queue_tasks.start(
            client.toggle_vpn_instance("openvpn", "servers", "uuid", "on"), eager_start=True
        )
        cancelled_toggle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(cancelled_toggle, timeout=3)
        release_blocking_post.set()
        await asyncio.wait_for(blocker, timeout=3)
        # A later request confirms the worker has passed the cancelled write.
        await asyncio.wait_for(client._post("/barrier"), timeout=3)
        assert dispatched == ["/block", "/barrier"]
    finally:
        release_blocking_post.set()


@pytest.mark.asyncio
async def test_close_cancels_vpn_service_lock_waiter_and_allows_reuse(
    make_client: MakeClientFactory, vpn_queue_tasks: VpnQueueTasks
) -> None:
    """Cancel a VPN call waiting on its service lock when the client closes.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        vpn_queue_tasks (VpnQueueTasks): Tracks test tasks for cleanup.
    """
    client = make_client()
    toggle_started = asyncio.Event()
    close_started = asyncio.Event()
    release_cancelled_workers = asyncio.Event()
    dispatched: list[str] = []

    async def do_post(
        path: str, payload: MutableMapping[str, Any] | None = None, caller: str = "Unknown"
    ) -> MutableMapping[str, Any]:
        """Hold the first VPN mutation and record dispatched POSTs.

        Args:
            path (str): Endpoint path.
            payload (MutableMapping[str, Any] | None): Optional request body.
            caller (str): Queue caller label.

        Returns:
            MutableMapping[str, Any]: Simulated response.

        Raises:
            asyncio.CancelledError: When shutdown cancels the worker.
        """
        dispatched.append(path)
        if path.endswith("/first/1"):
            toggle_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                close_started.set()
                await release_cancelled_workers.wait()
                raise
        return {"changed": True, "result": "Enabled"} if "/toggle/" in path else {"result": "ok"}

    client._do_post = AsyncMock(side_effect=do_post)
    try:
        active = vpn_queue_tasks.start(
            client.toggle_vpn_instance("openvpn", "servers", "first", "on")
        )
        await asyncio.wait_for(toggle_started.wait(), timeout=3)
        waiter = vpn_queue_tasks.start(
            client.toggle_vpn_instance("openvpn", "servers", "second", "on"), eager_start=True
        )
        assert not waiter.done()

        close_task = vpn_queue_tasks.start(client.async_close())
        await asyncio.wait_for(close_started.wait(), timeout=3)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(
                client.toggle_vpn_instance("openvpn", "servers", "during-close", "on"), timeout=3
            )
        release_cancelled_workers.set()
        await asyncio.wait_for(close_task, timeout=3)
        results = await asyncio.wait_for(
            asyncio.gather(active, waiter, return_exceptions=True), timeout=3
        )
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        assert dispatched == ["/api/openvpn/instances/toggle/first/1"]

        assert (
            await asyncio.wait_for(
                client.toggle_vpn_instance("openvpn", "servers", "reused", "on"), timeout=3
            )
            is True
        )
        assert dispatched[1:] == [
            "/api/openvpn/instances/toggle/reused/1",
            "/api/openvpn/service/reconfigure",
        ]
    finally:
        release_cancelled_workers.set()


@pytest.mark.asyncio
async def test_close_after_vpn_save_ack_skips_follow_up_apply(
    make_client: MakeClientFactory,
) -> None:
    """Do not begin a VPN service apply after shutdown follows a save ACK.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    client, _session = make_mock_session_client(make_client)
    dispatched: list[str] = []
    close_task: asyncio.Task[None] | None = None

    async def do_post(
        path: str, payload: MutableMapping[str, Any] | None = None, caller: str = "Unknown"
    ) -> MutableMapping[str, Any]:
        """Return a saved toggle response while scheduling client shutdown.

        Args:
            path (str): Endpoint path.
            payload (MutableMapping[str, Any] | None): Optional request body.
            caller (str): Queue caller label.

        Returns:
            MutableMapping[str, Any]: Simulated save acknowledgement or apply result.
        """
        nonlocal close_task
        dispatched.append(path)
        if path.endswith("/uuid/1") and close_task is None:
            close_task = asyncio.create_task(client.async_close())
            return {"changed": True, "result": "Enabled"}
        if path.endswith("/uuid/1"):
            return {"changed": False, "result": "Enabled"}
        return {"changed": True, "result": "Enabled"} if "/toggle/" in path else {"result": "ok"}

    client._do_post = AsyncMock(side_effect=do_post)
    try:
        result = await asyncio.gather(
            client.toggle_vpn_instance("openvpn", "servers", "uuid", "on"),
            return_exceptions=True,
        )
        if close_task is not None:
            await asyncio.wait_for(close_task, timeout=3)
        assert isinstance(result[0], asyncio.CancelledError)
        toggle_path = "/api/openvpn/instances/toggle/uuid/1"
        assert dispatched == [toggle_path]

        assert (
            await asyncio.wait_for(
                client.toggle_vpn_instance("openvpn", "servers", "uuid", "on"), timeout=3
            )
            is True
        )
        assert dispatched[1:] == [toggle_path, "/api/openvpn/service/reconfigure"]
    finally:
        if close_task is not None and not close_task.done():
            await asyncio.gather(close_task, return_exceptions=True)
        await asyncio.wait_for(client.async_close(), timeout=3)


@pytest.mark.asyncio
async def test_cancelled_close_releases_queued_vpn_call(
    make_client: MakeClientFactory, vpn_queue_tasks: VpnQueueTasks
) -> None:
    """Settle a queued VPN call when the caller cancels client shutdown.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        vpn_queue_tasks (VpnQueueTasks): Tracks test tasks for cleanup.
    """
    client = make_client()
    client._max_workers = 1
    started = asyncio.Event()
    cancelling, release = asyncio.Event(), asyncio.Event()
    dispatched: list[str] = []

    async def do_post(
        path: str, payload: MutableMapping[str, Any] | None = None, caller: str = "Unknown"
    ) -> MutableMapping[str, Any]:
        """Hold the worker through the first cancellation.

        Args:
            path (str): Endpoint path.
            payload (MutableMapping[str, Any] | None): Optional request body.
            caller (str): Queue caller label.

        Returns:
            MutableMapping[str, Any]: Simulated response from OPNsense.

        Raises:
            asyncio.CancelledError: When shutdown cancels the worker.
        """
        dispatched.append(path)
        if path == "/occupy":
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelling.set()
                await release.wait()
                raise
        return {"changed": True, "result": "Enabled"} if "/toggle/" in path else {"result": "ok"}

    client._do_post = AsyncMock(side_effect=do_post)
    try:
        vpn_queue_tasks.start(client._post("/occupy"))
        await asyncio.wait_for(started.wait(), timeout=3)
        # The restart race occurs after this worker exits, while close still owns the queue.
        worker = client._workers[0]
        queued = vpn_queue_tasks.start(
            client.toggle_vpn_instance("openvpn", "servers", "old", "on"), eager_start=True
        )

        async def read_notices_after_worker_stops() -> dict[str, Any]:
            """Submit a public read as soon as the original queue worker stops.

            Returns:
                dict[str, Any]: Dashboard notices if the read is accepted.
            """
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            return await client.get_notices()

        read = vpn_queue_tasks.start(read_notices_after_worker_stops(), eager_start=True)
        close = vpn_queue_tasks.start(client.async_close())
        await asyncio.wait_for(cancelling.wait(), timeout=3)
        close.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(close, timeout=3)
        for task in (queued, read):
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), timeout=3)
        assert (
            await asyncio.wait_for(
                client.toggle_vpn_instance("openvpn", "servers", "fresh", "on"), timeout=3
            )
            is True
        )
        assert dispatched == [
            "/occupy",
            "/api/openvpn/instances/toggle/fresh/1",
            "/api/openvpn/service/reconfigure",
        ]
    finally:
        release.set()


@pytest.mark.parametrize(
    ("response_format", "response", "expected"),
    [
        pytest.param("json", FakeResponse(json_payload={"ok": True}), {"ok": True}, id="json"),
        pytest.param("text", FakeResponse(text_payload="ok"), "ok", id="text"),
    ],
)
@pytest.mark.asyncio
async def test_get_outcome_read_returns_decoded_body_through_queue(
    make_client: MakeClientFactory,
    response_format: Literal["json", "text"],
    response: FakeResponse,
    expected: Any,
) -> None:
    """A GET outcome read queued through the worker returns its decoded JSON or text body.

    Args:
        make_client (MakeClientFactory): Factory for clients using a mocked HTTP session.
        response_format (Literal["json", "text"]): Decoding mode of the read.
        response (FakeResponse): Response the mocked session returns.
        expected (Any): Payload the outcome must carry.
    """
    client, session = make_mock_session_client(make_client)
    session.get.return_value = response
    try:
        outcome = await client._fetch_endpoint_outcome("/api/x", response_format)
        assert outcome.is_data
        assert outcome.payload == expected
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_post_outcome_read_returns_the_queued_post_response(
    make_client: MakeClientFactory,
) -> None:
    """A POST outcome read queued through the worker returns its decoded response.

    Args:
        make_client (MakeClientFactory): Factory for clients using a mocked HTTP session.
    """
    client, session = make_mock_session_client(make_client)
    session.post.return_value = FakeResponse(json_payload={"output": {"model": "test"}})
    try:
        outcome = await client._fetch_post_endpoint_outcome("/api/x", {"device": "ada0"})
        assert outcome.is_data
        assert outcome.mapping() == {"output": {"model": "test"}}
    finally:
        await client.async_close()


@pytest.mark.parametrize("throw_errors", [False, True])
@pytest.mark.asyncio
async def test_malformed_json_outcome_read_follows_public_error_policy(
    make_client: MakeClientFactory,
    throw_errors: bool,
) -> None:
    """Malformed JSON from a queued GET outcome read follows the public error policy.

    Args:
        make_client (MakeClientFactory): Factory for clients using a mocked HTTP session.
        throw_errors (bool): Whether the client raises mapped errors.
    """
    client, session = make_mock_session_client(make_client)
    client.toggle_throwing_errors(throw_errors)
    endpoint = "/api/nut/diagnostics/upsstatus"
    requests: list[str] = []

    def fake_response(url: str, **_kwargs: Any) -> MalformedJSONResponse:
        """Return malformed JSON for the endpoint under test.

        Args:
            url (str): Requested URL.
            _kwargs (Any): HTTP options supplied by aiohttp callers.

        Returns:
            MalformedJSONResponse: Response whose JSON body cannot be decoded.
        """
        requests.append(url)
        return MalformedJSONResponse()

    session.get = fake_response
    try:
        if throw_errors:
            with pytest.raises(OPNsenseError) as error:
                await client.get_nut_ups_status()
            assert isinstance(error.value.__cause__, json.JSONDecodeError)
        else:
            assert await client.get_nut_ups_status() is None
        assert requests == [f"http://localhost{endpoint}"]
    finally:
        await client.async_close()


@pytest.mark.parametrize("action", ["info", "status"])
@pytest.mark.parametrize("throw_errors", [True, False])
async def test_queued_public_request_cannot_reach_forbidden_firmware_action(
    make_client: MakeClientFactory,
    action: str,
    throw_errors: bool,
) -> None:
    """Block a changelog version that resolves to a forbidden firmware action.

    The block raises when errors are thrown. Otherwise the call returns an empty dict.

    Args:
        make_client (MakeClientFactory): Client factory.
        action (str): Forbidden firmware action reached through a dot segment.
        throw_errors (bool): Whether the client raises public errors.
    """
    client, session = make_mock_session_client(make_client)
    client.toggle_throwing_errors(throw_errors)
    try:
        if throw_errors:
            with pytest.raises(OPNsenseInvalidArgument, match="Blocked"):
                await asyncio.wait_for(client.firmware_changelog(f"../{action}"), timeout=3)
        else:
            result = await asyncio.wait_for(client.firmware_changelog(f"../{action}"), timeout=3)
            assert result == {}
        session.post.assert_not_called()
    finally:
        await client.async_close()


async def _enable_blocklist_with_unknown_firmware(client: Any) -> bool:
    """Call a public method whose first step is the public firmware version read.

    Args:
        client (Any): Client under test.

    Returns:
        bool: Result of ``enable_unbound_blocklist``.
    """
    client._firmware_version = None
    return await client.enable_unbound_blocklist()


_CALLER_NAMES_PATHS = {
    "speedtest_show": "/api/speedtest/service/showlog",
    "firmware_status": "/api/core/firmware/status",
    "firmware_running": "/api/core/firmware/running",
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "routes", "expected_caller"),
    [
        pytest.param(
            lambda client: client.get_gateways(),
            {},
            "get_gateways",
            id="outcome-read",
        ),
        pytest.param(
            lambda client: client.toggle_vpn_instance("openvpn", "clients", "u-1", "on"),
            {},
            "toggle_vpn_instance",
            id="vpn-toggle",
        ),
        pytest.param(
            lambda client: client.run_speedtest(),
            {_CALLER_NAMES_PATHS["speedtest_show"]: {"rows": []}},
            "run_speedtest",
            id="timed-get",
        ),
        pytest.param(
            lambda client: client.get_firmware_update_info(),
            {
                _CALLER_NAMES_PATHS["firmware_status"]: {"status": "error"},
                _CALLER_NAMES_PATHS["firmware_running"]: {"status": "ready"},
            },
            "get_firmware_update_info",
            id="automatic-firmware-check",
        ),
        pytest.param(
            _enable_blocklist_with_unknown_firmware,
            {},
            {"get_host_firmware_version", "enable_unbound_blocklist"},
            id="nearest-public-method-wins",
        ),
        pytest.param(
            lambda client: client.run_speedtest(),
            {_CALLER_NAMES_PATHS["speedtest_show"]: 403},
            "run_speedtest",
            id="status-check-403",
        ),
    ],
)
async def test_failure_logs_name_the_public_client_method(
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
    operation: Any,
    routes: dict[str, Any],
    expected_caller: str | set[str],
) -> None:
    """Name the public method the application called in every request failure log.

    The call goes through the public method, the real queue worker and a fake
    aiohttp session that answers with HTTP 500 unless a route says otherwise.

    Args:
        make_client (MakeClientFactory): Factory for clients using a mocked HTTP session.
        caplog (pytest.LogCaptureFixture): Captured request failure logs.
        operation (Any): Calls the public client method under test.
        routes (dict[str, Any]): Replies by path: a JSON body or an HTTP error status.
        expected_caller (str | set[str]): Public method name, or every public method
            name, the failure logs must carry. The nested case logs the firmware read
            under its own name and the outer method's later failure under its own.
    """
    client, session = make_mock_session_client(make_client)
    client._firmware_version = "26.1"

    def answer(url: str, **_kwargs: Any) -> FakeResponse:
        """Answer one request from the routes, or with HTTP 500.

        Args:
            url (str): Full requested URL.
            _kwargs (Any): Unused request options.

        Returns:
            FakeResponse: Scripted reply for the path.
        """
        reply = routes.get(url.removeprefix(client._url), 500)
        if isinstance(reply, int):
            return FakeResponse(status=reply, reason="Failed", ok=False)
        return FakeResponse(json_payload=reply)

    session.get = session.post = answer
    try:
        with caplog.at_level(logging.WARNING, logger="aiopnsense"):
            await operation(client)

        callers = {
            name
            for record in caplog.records
            for name in re.findall(r"called by (\w+)", record.getMessage())
        }
        assert callers == (
            {expected_caller} if isinstance(expected_caller, str) else expected_caller
        )
    finally:
        await asyncio.wait_for(client.async_close(), timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "is_sent"),
    [
        ("get", True),
        ("get_from_stream", True),
        ("get_outcome", False),
        ("get_text_outcome", False),
        ("post", False),
        ("post_outcome", False),
    ],
)
async def test_request_cancelled_before_dispatch_is_skipped_by_kind(
    make_client: MakeClientFactory,
    vpn_queue_tasks: VpnQueueTasks,
    kind: str,
    is_sent: bool,
) -> None:
    """Skip POST and outcome requests cancelled while queued; still send plain GET and streams.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        vpn_queue_tasks (VpnQueueTasks): Tracks test tasks for cleanup.
        kind (str): Queued request kind.
        is_sent (bool): Whether the cancelled request is still sent.
    """
    client, session = make_mock_session_client(make_client)
    reply = FakeResponse(json_payload={}, text_payload="", stream_chunks=[b"data: {}\n\n"])
    session.get.return_value = session.post.return_value = reply
    # An eager start queues the request before the worker runs; cancelling right
    # away cancels it while it is still queued.
    target = vpn_queue_tasks.start(
        client._queue_request(kind, "/target", {"x": 1}), eager_start=True
    )
    target.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(target, timeout=3)
    # A later request confirms the worker has passed the cancelled one.
    await asyncio.wait_for(client._get("/barrier"), timeout=3)
    sent = [c.args[0] for m in (session.get, session.post) for c in m.call_args_list]
    assert any(url.endswith("/target") for url in sent) is is_sent


@pytest.mark.asyncio
async def test_unknown_request_kind_logs_once_and_fails_the_waiting_caller(
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Report an unknown queued request kind once and raise it to the waiting caller.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        caplog (pytest.LogCaptureFixture): Captures the logged error.
    """
    client, session = make_mock_session_client(make_client)
    try:
        with caplog.at_level("ERROR"), pytest.raises(OPNsenseError) as raised:
            await asyncio.wait_for(client._queue_request("bogus", "/x"), timeout=3)
        assert str(raised.value) == "Unknown method to add to Queue: bogus"
        assert [r.getMessage() for r in caplog.records] == ["Unknown method to add to Queue: bogus"]
        session.get.assert_not_called()
        session.post.assert_not_called()
    finally:
        await client.async_close()
