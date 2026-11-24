"""Tests for client transport helpers and HTTP response handling."""

from collections.abc import Callable, MutableMapping
import contextlib
from dataclasses import dataclass
import json
import logging
from types import TracebackType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from aiopnsense.client_transport import _STREAM_JSON_EVENT_RESET_KEY
from aiopnsense.const import DEFAULT_REQUEST_TIMEOUT_SECONDS
from aiopnsense.exceptions import (
    OPNsenseConnectionError,
    OPNsenseError,
    OPNsenseInvalidArgument,
    OPNsenseInvalidAuth,
    OPNsensePrivilegeMissing,
    OPNsenseTimeoutError,
)
from aiopnsense.helpers import _is_request_error_logged
from tests.conftest import (
    FakeResponse,
    FakeStreamResponseFactory,
    MakeClientFactory,
    MalformedJSONResponse,
    make_mock_session_client,
    make_router_client,
)


@pytest.mark.asyncio
async def test_safe_dict_get(
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

        monkeypatch.setattr(client, "_get", AsyncMock(return_value=None), raising=False)
        result_empty_dict = await client._safe_dict_get("/fake")
        assert result_empty_dict == {}
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
        # The test module is outside the package, so no public client method is found.
        result_dict = await client._safe_dict_get_with_timeout("/fake", timeout_seconds=180)
        assert result_dict == {"foo": "bar"}
        do_get_mock.assert_awaited_once_with(
            path="/fake",
            caller="Unknown",
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
        ("bad", float(DEFAULT_REQUEST_TIMEOUT_SECONDS)),
        (object(), float(DEFAULT_REQUEST_TIMEOUT_SECONDS)),
        (0, float(DEFAULT_REQUEST_TIMEOUT_SECONDS)),
        (-5, float(DEFAULT_REQUEST_TIMEOUT_SECONDS)),
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
@pytest.mark.parametrize(
    ("method_name", "session_method", "args", "kwargs", "expected_exception"),
    [
        ("_do_get", "get", ("/api/x",), {"caller": "tst"}, OPNsensePrivilegeMissing),
        ("_do_post", "post", ("/api/x",), {"payload": {}}, OPNsenseConnectionError),
    ],
)
async def test_do_get_post_error_initial_behavior(
    method_name: str,
    session_method: str,
    args: tuple[str],
    kwargs: dict[str, Any],
    expected_exception: type[OPNsenseConnectionError],
    make_client: MakeClientFactory,
) -> None:
    """Verify thrown request errors raise public OPNsense exceptions.

    Args:
        method_name (str): Client method to invoke (``_do_get`` or ``_do_post``).
        session_method (str): Session method to override (``get`` or ``post``).
        args (tuple[str]): Positional arguments passed to the client method.
        kwargs (dict[str, Any]): Keyword arguments passed to the client method.
        expected_exception (type[OPNsenseConnectionError]): Expected public exception type.
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
        with pytest.raises(expected_exception):
            await getattr(client, method_name)(*args, **kwargs)
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("method_name", "session_method", "args", "kwargs"),
    [
        ("_do_get_from_stream", "get", ("/stream",), {"caller": "tst"}),
        ("_stream_json_events", "get", ("/stream",), {}),
        ("_do_get", "get", ("/api/x",), {"caller": "tst"}),
        ("_do_post", "post", ("/api/x",), {"payload": {}, "caller": "tst"}),
    ],
)
@pytest.mark.parametrize(
    ("transport_error", "expected_exception"),
    [
        (aiohttp.ClientConnectionError("connection failed"), OPNsenseConnectionError),
        (TimeoutError("request timed out"), OPNsenseTimeoutError),
    ],
)
@pytest.mark.asyncio
async def test_transport_errors_map_when_throwing(
    method_name: str,
    session_method: str,
    args: tuple[str, ...],
    kwargs: dict[str, Any],
    transport_error: Exception,
    expected_exception: type[OPNsenseError],
    make_client: MakeClientFactory,
) -> None:
    """Verify transport failures map to public exceptions for each low-level request helper.

    Args:
        method_name (str): Client transport method to invoke.
        session_method (str): Session method overridden to raise the transport failure.
        args (tuple[str, ...]): Positional arguments passed to the client method.
        kwargs (dict[str, Any]): Keyword arguments passed to the client method.
        transport_error (Exception): Transport failure raised by the session.
        expected_exception (type[OPNsenseError]): Public exception expected from the transport
            mapper.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts mapped exception type and source exception chaining.
    """
    session = MagicMock()

    def raise_transport_error(*_args: Any, **_kwargs: Any) -> Any:
        """Raise the configured transport failure when the request starts.

        Args:
            _args (Any): Positional arguments accepted by `raise_transport_error`.
            _kwargs (Any): Keyword arguments accepted by `raise_transport_error`.

        Returns:
            Any: This synchronous test double only raises the configured failure.

        Raises:
            transport_error: The configured transport failure.
        """
        raise transport_error

    setattr(session, session_method, raise_transport_error)
    client = make_client(session=session, throw_errors=True)
    try:
        with pytest.raises(expected_exception) as exc_info:
            if method_name == "_stream_json_events":
                async for _event in getattr(client, method_name)(*args, **kwargs):
                    pass
            else:
                await getattr(client, method_name)(*args, **kwargs)

        assert exc_info.value.__cause__ is transport_error
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
@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        pytest.param(
            [b"data: not-json\n\n", b'data: {"a": 2}\n\n'],
            {"a": 2},
            id="first-malformed-is-counted",
        ),
        pytest.param(
            [b"data: [1]\n\n", b'data: {"a": 2}\n\n'],
            {"a": 2},
            id="first-non-mapping-is-counted",
        ),
        pytest.param(
            [b'data: {"a": 1}\n\n', b'data: {"a": 2}\n\n', b"\xff"],
            {"a": 2},
            id="stops-reading-at-second-message",
        ),
        pytest.param(
            [b'data: {"a": 1}\n\n', b"data: [2]\n\n"], {}, id="second-non-mapping-returns-empty"
        ),
        pytest.param([b'data: {"a": 1}\n\n'], {}, id="stream-ends-before-second-message"),
        pytest.param(
            [b'data: {"a": 1}\n\n', b"data: nope\n\n"], json.JSONDecodeError, id="second-malformed"
        ),
        pytest.param([b"\xff\xfe"], UnicodeDecodeError, id="invalid-utf8"),
        pytest.param(
            [b'data: {"a": 1}\n\n', b'data: {"a": "x\xc3'],
            UnicodeDecodeError,
            id="truncated-utf8",
        ),
    ],
)
async def test_do_get_from_stream_keeps_corrupt_input_policy(
    make_client: MakeClientFactory,
    fake_stream_response_factory: FakeStreamResponseFactory,
    chunks: list[bytes],
    expected: dict[str, Any] | type[Exception],
) -> None:
    """Verify the CPU read counts rejected messages and fails on corrupt second messages.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        fake_stream_response_factory (FakeStreamResponseFactory): Fixture function building stream
            response stubs.
        chunks (list[bytes]): Chunks the Router sends.
        expected (dict[str, Any] | type[Exception]): Returned mapping, or the exception raised.
    """
    client, session = make_mock_session_client(make_client)
    session.get = lambda *a, **k: fake_stream_response_factory(chunks)
    try:
        if isinstance(expected, dict):
            assert await client._do_get_from_stream("/stream", caller="tst") == expected
        else:
            with pytest.raises(expected):
                await client._do_get_from_stream("/stream", caller="tst")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        pytest.param(
            [b'data: {"a":1}\r\n\r\ndata: {"a":2}\r\n\r\n'], {"a": 2}, id="crlf-line-endings"
        ),
        pytest.param([b'data: {"a":1}\r\rdata: {"a":2}\r\r'], {"a": 2}, id="bare-cr-line-endings"),
        pytest.param(
            [b'data: {"a":1}\r', b'\n\r\ndata: {"a":2}\r\n\r\n'],
            {"a": 2},
            id="crlf-split-across-chunks",
        ),
        pytest.param(
            [b'data: {"a":1}\n\ndata: {"a":2}\n\r'], {"a": 2}, id="final-event-ended-by-cr"
        ),
        pytest.param(
            [b'data: {"a":"x"}\n\ndata: {"a":"na\xc3', b'\xafve"}\n\n'],
            {"a": "naïve"},
            id="multibyte-character-split-across-chunks",
        ),
        pytest.param(
            [b'data: {"a":\ndata: 1}\n\ndata: {"a":2}\n\n'],
            {"a": 2},
            id="json-across-two-data-lines",
        ),
        pytest.param(
            [b'data: {"a":1}\ndata: {"a":2}\n\ndata: {"a":3}\n\n'],
            {"a": 3},
            id="messages-counted-per-event",
        ),
    ],
)
async def test_do_get_from_stream_frames_events_like_the_traffic_stream(
    make_client: MakeClientFactory,
    fake_stream_response_factory: FakeStreamResponseFactory,
    chunks: list[bytes],
    expected: dict[str, Any],
) -> None:
    """Verify the CPU read frames and decodes events as the traffic stream does.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        fake_stream_response_factory (FakeStreamResponseFactory): Fixture function building stream
            response stubs.
        chunks (list[bytes]): Chunks the Router sends.
        expected (dict[str, Any]): The second message.
    """
    client, session = make_mock_session_client(make_client)
    session.get = lambda *a, **k: fake_stream_response_factory(chunks)
    try:
        assert await client._do_get_from_stream("/stream", caller="tst") == expected
    finally:
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
            args (Any): Positional arguments accepted by `fake_get`.
            kwargs (Any): Keyword arguments accepted by `fake_get`.

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
        with pytest.raises(OPNsensePrivilegeMissing):
            await client._do_get_from_stream("/bad", caller="t")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_do_get_and_post_success_paths(
    make_client: MakeClientFactory,
) -> None:
    """Verify GET, and POST helpers return successful response bodies.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts successful low-level request parsing.
    """
    client, session = make_mock_session_client(make_client)

    def fake_get(*args: Any, **kwargs: Any) -> Any:
        """Fake get.

        Args:
            args (Any): Positional arguments accepted by `fake_get`.
            kwargs (Any): Keyword arguments accepted by `fake_get`.

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
            args (Any): Positional arguments accepted by `fake_post`.
            kwargs (Any): Keyword arguments accepted by `fake_post`.

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
async def test_do_get_post_and_stream_permission_errors(
    make_client: MakeClientFactory,
) -> None:
    """Verify GET, POST, and stream permission failures do not raise when disabled.

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
async def test_stream_json_events_yields_each_valid_data_message(
    make_client: Callable[..., Any],
    fake_stream_response_factory: Callable[..., FakeResponse],
) -> None:
    """Direct stream iterator should yield every JSON SSE data message.

    Args:
        make_client (Callable[..., Any]): Factory used to configure the SSE stream client.
        fake_stream_response_factory (Callable[..., FakeResponse]): Factory supplying the SSE byte chunks.
    """
    session = MagicMock()
    session.get = lambda *a, **k: fake_stream_response_factory(
        chunks=[
            b'data: {"time": 1, "interfaces": {}}\n\n',
            b'data: {"time": 2, "interfaces": {"wan": {"rx_bytes": 10}}}\n\n',
        ]
    )
    client = make_client(session=session)
    try:
        events: list[dict[str, Any]] = []
        async for event in client._stream_json_events("/api/diagnostics/traffic/stream/1"):
            events.append(event)
            if len(events) == 2:
                break
        assert events == [
            {"time": 1, "interfaces": {}},
            {"time": 2, "interfaces": {"wan": {"rx_bytes": 10}}},
        ]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_json_events_returns_empty_sequence_for_non_ok_response_when_not_throwing(
    make_client: Callable[..., Any],
) -> None:
    """Non-OK stream responses should yield no events when errors are disabled.

    Args:
        make_client (Callable[..., Any]): Factory used to configure the non-throwing SSE client.
    """
    session = MagicMock()
    session.get = lambda *a, **k: FakeResponse(
        stream_chunks=[b'data: {"time": 1}\n\n'],
        status=500,
        reason="Internal Server Error",
        ok=False,
    )
    client = make_client(session=session)
    try:
        client._throw_errors = False
        events = [
            event async for event in client._stream_json_events("/api/diagnostics/traffic/stream/1")
        ]

        assert events == []
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_json_events_sets_connect_and_read_timeouts(
    make_client: Callable[..., Any],
    fake_stream_response_factory: Callable[..., FakeResponse],
) -> None:
    """Direct stream iterator should not wait forever for silent connections.

    Args:
        make_client (Callable[..., Any]): Factory used to configure the timed SSE stream client.
        fake_stream_response_factory (Callable[..., FakeResponse]): Factory supplying the response used to capture timeouts.
    """
    captured_timeout: aiohttp.ClientTimeout | None = None

    def fake_get(*_args: Any, **kwargs: Any) -> FakeResponse:
        nonlocal captured_timeout
        captured_timeout = kwargs["timeout"]
        return fake_stream_response_factory(chunks=[b'data: {"time": 1, "interfaces": {}}\n\n'])

    session = MagicMock()
    session.get = fake_get
    client = make_client(session=session)
    try:
        events = [
            event async for event in client._stream_json_events("/api/diagnostics/traffic/stream/1")
        ]

        assert events == [{"time": 1, "interfaces": {}}]
        assert captured_timeout is not None
        assert captured_timeout.total is None
        assert captured_timeout.sock_connect == DEFAULT_REQUEST_TIMEOUT_SECONDS
        assert captured_timeout.sock_read == DEFAULT_REQUEST_TIMEOUT_SECONDS
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_json_events_allows_read_timeout_override(
    make_client: Callable[..., Any],
    fake_stream_response_factory: Callable[..., FakeResponse],
) -> None:
    """Stream caller should be able to override the socket read timeout.

    Args:
        make_client (Callable[..., Any]): Factory used to configure the timeout-override client.
        fake_stream_response_factory (Callable[..., FakeResponse]): Factory supplying the response used to capture timeouts.
    """
    captured_timeout: aiohttp.ClientTimeout | None = None

    def fake_get(*_args: Any, **kwargs: Any) -> FakeResponse:
        nonlocal captured_timeout
        captured_timeout = kwargs["timeout"]
        return fake_stream_response_factory(chunks=[b'data: {"time": 1, "interfaces": {}}\n\n'])

    session = MagicMock()
    session.get = fake_get
    client = make_client(session=session)
    try:
        events = [
            event
            async for event in client._stream_json_events(
                "/api/diagnostics/traffic/stream/1", sock_read_timeout_seconds=120
            )
        ]

        assert events == [{"time": 1, "interfaces": {}}]
        assert captured_timeout is not None
        assert captured_timeout.sock_read == 120
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_chunk",
    [
        pytest.param(b"data: not-json\n\n", id="malformed-json"),
        pytest.param(b"data: [1, 2]\n\n", id="non-mapping-json"),
        pytest.param(b"\xff\xfe\xfd", id="invalid-utf8"),
    ],
)
@pytest.mark.parametrize("yield_reset_events", [False, True])
async def test_stream_json_events_reset_events_only_when_requested(
    make_client: Callable[..., Any],
    fake_stream_response_factory: FakeStreamResponseFactory,
    bad_chunk: bytes,
    yield_reset_events: bool,
) -> None:
    """Rejected input yields a reset event only on request and never stops the stream.

    Args:
        make_client (Callable[..., Any]): Factory used to configure the SSE stream client.
        fake_stream_response_factory (FakeStreamResponseFactory): Factory supplying the SSE chunks.
        bad_chunk (bytes): A chunk the decoder rejects.
        yield_reset_events (bool): Whether reset events are requested.
    """
    session = MagicMock()
    session.get = lambda *a, **k: fake_stream_response_factory(
        [b'data: {"time": 1}\n\n', bad_chunk, b'data: {"time": 2}\n\n']
    )
    client = make_client(session=session)
    try:
        events = [
            event
            async for event in client._stream_json_events(
                "/api/diagnostics/traffic/stream/1", yield_reset_events=yield_reset_events
            )
        ]
        reset: dict[str, Any] = {_STREAM_JSON_EVENT_RESET_KEY: True}
        expected_resets = [reset] if yield_reset_events else []
        assert events == [{"time": 1}, *expected_resets, {"time": 2}]
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("yield_reset_events", [False, True])
async def test_stream_json_events_truncated_utf8_at_eof_yields_no_reset_event(
    make_client: Callable[..., Any],
    fake_stream_response_factory: FakeStreamResponseFactory,
    yield_reset_events: bool,
) -> None:
    """A UTF-8 sequence cut off at end of stream is dropped without a reset event.

    Args:
        make_client (Callable[..., Any]): Factory used to configure the SSE stream client.
        fake_stream_response_factory (FakeStreamResponseFactory): Factory supplying the SSE chunks.
        yield_reset_events (bool): Whether reset events are requested.
    """
    session = MagicMock()
    session.get = lambda *a, **k: fake_stream_response_factory(
        [b'data: {"time": 1}\n\n', b'data: {"a": "x\xc3']
    )
    client = make_client(session=session)
    try:
        events = [
            event
            async for event in client._stream_json_events(
                "/api/diagnostics/traffic/stream/1", yield_reset_events=yield_reset_events
            )
        ]
        assert events == [{"time": 1}]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_json_events_close_on_iterator_break(
    make_client: Callable[..., Any],
) -> None:
    """Canceling iteration early should still close the response context.

    Args:
        make_client (Callable[..., Any]): Factory used to configure the iterator-closure client.
    """

    class _TrackedResponse(FakeResponse):
        """Fake response that tracks async context manager exit."""

        def __init__(self, **kwargs: Any) -> None:
            """Initialize a tracked response.

            Args:
                kwargs (Any): Keyword arguments forwarded to ``FakeResponse``.
            """
            self.exited = False
            self.closed = False
            super().__init__(**kwargs)

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> bool:
            self.exited = True
            self.closed = True
            return await super().__aexit__(exc_type, exc, tb)

    response = _TrackedResponse(
        status=200,
        reason="OK",
        ok=True,
        stream_chunks=[
            b'data: {"time": 5}\n\n',
            b'data: {"time": 6}\n\n',
        ],
        include_request_info=True,
    )
    session = MagicMock()
    session.get = lambda *a, **k: response

    client = make_client(session=session)
    try:
        stream_iterator = client._stream_json_events(
            "/api/diagnostics/traffic/stream/1"
        ).__aiter__()
        await stream_iterator.__anext__()
        await stream_iterator.aclose()
        assert response.exited is True
        assert response.closed is True
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_json_events_raises_when_throw_errors_enabled(
    make_client: Callable[..., Any],
    fake_response_factory: Callable[..., FakeResponse],
) -> None:
    """Direct stream iterator should honor throw_errors for non-OK responses.

    Args:
        make_client (Callable[..., Any]): Factory used to enable errors on the SSE client.
        fake_response_factory (Callable[..., FakeResponse]): Factory supplying the forbidden HTTP response.
    """
    session = MagicMock()
    session.get = lambda *a, **k: fake_response_factory(
        status=403,
        reason="Forbidden",
        ok=False,
        chunks=[],
        include_request_info=True,
    )
    client = make_client(session=session, throw_errors=True)
    try:
        with pytest.raises(OPNsensePrivilegeMissing):
            async for _event in client._stream_json_events("/api/diagnostics/traffic/stream/1"):
                pass
    finally:
        await client.async_close()


_LADDER_PATHS: dict[str, tuple[str, Any]] = {
    "get_json": ("get", None),
    "post": ("post", None),
    "stream_once": ("get", {}),
    "stream_events": ("get", []),
    "probe": ("get", False),
}
"""Request path name mapped to its session method and non-throwing empty value."""


async def _call_ladder_path(client: Any, path_name: str) -> Any:
    """Invoke one low-level request path against a fixed API path.

    Args:
        client (Any): Client whose transport method is invoked.
        path_name (str): Key of ``_LADDER_PATHS`` selecting the request path.

    Returns:
        Any: Value returned by the selected request path.
    """
    if path_name == "get_json":
        return await client._do_get("/api/x", caller="t")
    if path_name == "post":
        return await client._do_post("/api/x", {}, caller="t")
    if path_name == "stream_once":
        return await client._do_get_from_stream("/api/x", caller="t")
    if path_name == "stream_events":
        return [event async for event in client._stream_json_events("/api/x")]
    with pytest.warns(DeprecationWarning, match="Endpoint availability probing is internal"):
        return await client.is_endpoint_available("/api/x", force_refresh=True)  # type: ignore[deprecated]


@pytest.mark.parametrize("path_name", list(_LADDER_PATHS))
@pytest.mark.parametrize(
    ("failure", "expected_exception", "probe_level"),
    [
        (401, OPNsenseInvalidAuth, logging.WARNING),
        (403, OPNsensePrivilegeMissing, logging.ERROR),
        (500, OPNsenseConnectionError, logging.WARNING),
        (aiohttp.ClientConnectionError("down"), OPNsenseConnectionError, logging.WARNING),
        (TimeoutError("slow"), OPNsenseTimeoutError, logging.WARNING),
    ],
    ids=["401", "403", "500", "connection-error", "timeout"],
)
@pytest.mark.parametrize("throw_errors", [False, True], ids=["empty", "throw"])
@pytest.mark.asyncio
async def test_request_failure_ladder_is_identical_across_request_paths(
    path_name: str,
    failure: int | Exception,
    expected_exception: type[OPNsenseError],
    probe_level: int,
    throw_errors: bool,
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Classify each request failure and log it once without configured URL credentials.

    Args:
        path_name (str): Key of ``_LADDER_PATHS`` selecting the request path.
        failure (int | Exception): HTTP status served by the session, or the transport
            error the session raises.
        expected_exception (type[OPNsenseError]): Public exception expected in throw mode.
        probe_level (int): Level of the first failure record for the availability probe,
            which logs transient failures below ERROR.
        throw_errors (bool): Whether the client raises public errors instead of returning
            the documented empty value.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Fixture capturing the client's log records.
    """
    session_method, empty_value = _LADDER_PATHS[path_name]
    session = MagicMock()
    if isinstance(failure, Exception):
        setattr(session, session_method, MagicMock(side_effect=failure))
    else:
        setattr(
            session,
            session_method,
            lambda *_a, **_k: FakeResponse(
                status=failure,
                reason="Failure",
                ok=False,
                stream_chunks=[b""],
                include_request_info=True,
            ),
        )
    client = make_client(
        session=session,
        throw_errors=throw_errors,
        url="https://synthetic-user:synthetic-password@opnsense.invalid",
    )
    try:
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            if throw_errors:
                with pytest.raises(expected_exception) as exc_info:
                    await _call_ladder_path(client, path_name)
                if isinstance(failure, Exception):
                    assert exc_info.value.__cause__ is failure
            else:
                assert await _call_ladder_path(client, path_name) == empty_value

        failures = [record for record in caplog.records if record.levelno >= logging.WARNING]
        expected_level = probe_level if path_name == "probe" else logging.ERROR
        assert len(failures) == 1
        assert failures[0].levelno == expected_level
        message = failures[0].getMessage()
        assert "/api/x" in message
        # Transient probe warnings identify the endpoint by path alone.
        if isinstance(failure, int) and (path_name != "probe" or failure == 403):
            assert "opnsense.invalid" in message
        assert "synthetic-user" not in caplog.text
        assert "synthetic-password" not in caplog.text
    finally:
        await client.async_close()


_SUCCESS_RESPONSES: dict[str, Callable[[], FakeResponse]] = {
    "get_json": lambda: FakeResponse(json_payload={"ok": True}),
    "post": lambda: FakeResponse(json_payload={"ok": True}),
    "stream_once": lambda: FakeResponse(stream_chunks=[b'data: {"a": 1}\n\ndata: {"a": 2}\n\n']),
    "stream_events": lambda: FakeResponse(stream_chunks=[b'data: {"a": 1}\n\n']),
    "probe": lambda: FakeResponse(),
}
"""Request path name mapped to a factory for a successful response."""


@pytest.mark.parametrize("path_name", list(_LADDER_PATHS))
@pytest.mark.asyncio
async def test_repeated_request_failures_log_once_per_outage_until_recovery(
    path_name: str,
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A repeated failure stays quiet, and a recovered path reports the next outage again.

    Args:
        path_name (str): Key of ``_LADDER_PATHS`` selecting the request path.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Fixture capturing the client's log records.
    """
    session_method, _empty_value = _LADDER_PATHS[path_name]
    failed_response = FakeResponse(status=500, reason="Failure", ok=False, stream_chunks=[b""])

    session = MagicMock()
    setattr(
        session,
        session_method,
        MagicMock(
            side_effect=[
                failed_response,
                failed_response,
                failed_response,
                _SUCCESS_RESPONSES[path_name](),
                failed_response,
            ]
        ),
    )
    client = make_client(session=session)
    try:
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            for _attempt in range(3):
                await _call_ladder_path(client, path_name)
            assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1

            caplog.clear()
            await _call_ladder_path(client, path_name)
            recovered = [r for r in caplog.records if r.levelno == logging.INFO]
            assert len(recovered) == 1
            assert "/api/x" in recovered[0].getMessage()

            caplog.clear()
            await _call_ladder_path(client, path_name)
            assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_outage_does_not_suppress_data_call_failure_on_same_path(
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stream outage and a data call outage on one path are reported independently.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Fixture capturing the client's log records.
    """
    session = MagicMock()
    session.get = lambda *_a, **_k: FakeResponse(status=500, reason="Failure", ok=False)
    client = make_client(session=session)
    try:
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            await _call_ladder_path(client, "stream_once")
            await _call_ladder_path(client, "get_json")
            await _call_ladder_path(client, "get_json")

        errors = [record for record in caplog.records if record.levelno >= logging.WARNING]
        assert [record.levelno for record in errors] == [logging.ERROR, logging.ERROR]
    finally:
        await client.async_close()


@pytest.mark.parametrize("path_name", ["stream_once", "stream_events"])
@pytest.mark.parametrize(("initial_failure", "auth_status"), [(500, 401), (TimeoutError(), 403)])
@pytest.mark.asyncio
async def test_stream_outage_reports_new_auth_failure_once(
    path_name: str,
    initial_failure: int | TimeoutError,
    auth_status: int,
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Report an authentication or privilege failure during a stream outage.

    Args:
        path_name (str): Streaming request path to exercise.
        initial_failure (int | TimeoutError): Transient HTTP or transport failure.
        auth_status (int): Authentication or authorization response status.
        make_client (MakeClientFactory): Fixture factory returning clients.
        caplog (pytest.LogCaptureFixture): Fixture capturing client logs.
    """
    first_response = (
        FakeResponse(status=initial_failure, ok=False)
        if isinstance(initial_failure, int)
        else initial_failure
    )
    auth_failure = FakeResponse(status=auth_status, ok=False)
    session = MagicMock()
    session.get.side_effect = [first_response, auth_failure, auth_failure]
    client = make_client(session=session)
    try:
        with caplog.at_level(logging.ERROR, logger="aiopnsense"):
            for expected_count in (1, 1, 0):
                caplog.clear()
                await _call_ladder_path(client, path_name)
                assert [record.levelno for record in caplog.records] == [
                    logging.ERROR
                ] * expected_count
    finally:
        await client.async_close()


class _StalledChunks:
    """Async chunk iterator whose first read times out."""

    __anext__ = AsyncMock(side_effect=TimeoutError("stalled"))

    def __aiter__(self) -> _StalledChunks:
        """Return the iterator itself.

        Returns:
            _StalledChunks: This iterator.
        """
        return self


@pytest.mark.parametrize("path_name", ["stream_once", "stream_events"])
@pytest.mark.asyncio
async def test_stream_that_connects_then_stalls_logs_one_error_per_outage(
    path_name: str,
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stream answering OK but failing before any message never reports a recovery.

    Args:
        path_name (str): Key of ``_LADDER_PATHS`` selecting the streaming path.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Fixture capturing the client's log records.
    """
    stalled = _StalledChunks()
    stalled_response = MagicMock(
        status=200,
        reason="OK",
        ok=True,
        content=MagicMock(iter_chunked=MagicMock(return_value=stalled)),
    )
    stalled_response.__aenter__.return_value = stalled_response
    session = MagicMock()
    session.get = lambda *_a, **_k: stalled_response
    client = make_client(session=session)
    try:
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            for _attempt in range(3):
                await _call_ladder_path(client, path_name)

        assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1
        assert not any(r.levelno == logging.INFO for r in caplog.records)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_reset_event_does_not_clear_outage(
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed events are emitted without reporting stream recovery.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Fixture capturing the client's log records.
    """
    stalled = _StalledChunks()
    stalled_response = MagicMock(
        status=200,
        reason="OK",
        ok=True,
        content=MagicMock(iter_chunked=MagicMock(return_value=stalled)),
    )
    stalled_response.__aenter__.return_value = stalled_response
    session = MagicMock()
    session.get = MagicMock(
        side_effect=[
            stalled_response,
            FakeResponse(stream_chunks=[b"data: not-json\n\n"]),
            stalled_response,
        ]
    )
    client = make_client(session=session)
    try:
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            await _call_ladder_path(client, "stream_events")
            events = [
                event
                async for event in client._stream_json_events("/api/x", yield_reset_events=True)
            ]
            await _call_ladder_path(client, "stream_events")

        assert len(events) == 1
        assert events[0].get(_STREAM_JSON_EVENT_RESET_KEY) is True
        assert len([record for record in caplog.records if record.levelno >= logging.WARNING]) == 1
        assert not any(record.levelno == logging.INFO for record in caplog.records)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_one_shot_non_mapping_stream_payload_does_not_report_recovery(
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-mapping one-shot payload returns empty without clearing an outage.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Fixture capturing the client's log records.
    """
    session = MagicMock()
    session.get = MagicMock(
        side_effect=[
            FakeResponse(status=500, reason="Failure", ok=False),
            FakeResponse(stream_chunks=[b"data: ignored\n\ndata: []\n\n"]),
            FakeResponse(status=500, reason="Failure", ok=False),
        ]
    )
    client = make_client(session=session)
    try:
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            await _call_ladder_path(client, "stream_once")
            assert await client._do_get_from_stream("/api/x", caller="t") == {}
            await _call_ladder_path(client, "stream_once")

        assert len([record for record in caplog.records if record.levelno >= logging.WARNING]) == 1
        assert not any(record.levelno == logging.INFO for record in caplog.records)
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("path_name", "level"),
    [("get_json", logging.ERROR), ("probe", logging.WARNING)],
    ids=["get-timeout", "probe-timeout"],
)
@pytest.mark.asyncio
async def test_timeout_log_omits_exception_url_credentials(
    path_name: str,
    level: int,
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep the endpoint and host while redacting URL credentials in a timeout.

    Args:
        path_name (str): Request path to exercise.
        level (int): Expected failure log level.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Fixture capturing the client's log records.
    """
    session = MagicMock()
    session.get.side_effect = TimeoutError(
        "Timed out reading https://error-user:error-password@opnsense.invalid"
    )
    client = make_client(session=session)
    try:
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            await _call_ladder_path(client, path_name)

        failures = [record for record in caplog.records if record.levelno >= logging.WARNING]
        assert [record.levelno for record in failures] == [level]
        message = failures[0].getMessage()
        assert "/api/x" in message
        assert "opnsense.invalid" in message
        assert "error-user" not in caplog.text
        assert "error-password" not in caplog.text
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_invalid_url_transport_logs_omit_credentials(
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep URL credentials out of probe and request logs at every level.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Fixture capturing the client's log records.
    """
    async with aiohttp.ClientSession() as session:
        client = make_client(
            session=session,
            url="https://synthetic-user:synthetic-secret@opnsense.invalid:bad",
        )
        try:
            with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
                assert (
                    await client._check_endpoint_status("/api/x", force_refresh=True)
                ).is_transient
                assert await client._do_get("/api/x", caller="test") is None

            failures = [record for record in caplog.records if record.levelno >= logging.WARNING]
            assert [record.levelno for record in failures] == [logging.WARNING, logging.ERROR]
            assert all("/api/x" in record.getMessage() for record in failures)
            messages = "\n".join(record.getMessage() for record in caplog.records)
            assert "synthetic-user" not in messages
            assert "synthetic-secret" not in messages
        finally:
            await client.async_close()


@pytest.mark.asyncio
async def test_status_check_does_not_read_a_non_json_success_body(
    make_client: MakeClientFactory,
) -> None:
    """A status-only check of a successful non-JSON response leaves its body unread.

    Args:
        make_client (MakeClientFactory): Fixture factory returning clients.
    """
    client, session = make_mock_session_client(make_client)
    body_reads: list[str] = []

    class EventStreamResponse(FakeResponse):
        """Successful response whose body is an event stream, not JSON."""

        async def json(self, content_type: Any = None) -> Any:
            """Record a JSON read and fail as aiohttp does for a non-JSON body.

            Args:
                content_type (Any, optional): Requested content type (unused by the fake).

            Returns:
                Any: Never returns.

            Raises:
                json.JSONDecodeError: Always.
            """
            del content_type
            body_reads.append("json")
            raise json.JSONDecodeError("Expecting value", "data: tick", 0)

        async def text(self) -> str:
            """Record a text read and return the event-stream body.

            Returns:
                str: Event-stream body.
            """
            body_reads.append("text")
            return "data: tick\n\n"

    session.get.return_value = EventStreamResponse()
    try:
        outcome = await client._check_endpoint_status("/api/stream", force_refresh=True)
        assert outcome.is_data
        assert body_reads == []
    finally:
        await client.async_close()


_PATH = "/api/x"
_BLOCKED_PATH = "/api/core/firmware/info"
_OK_BODY = {"ok": True}
_PERMISSION_MESSAGE = (
    "Ensure the OPNsense user connected to HA has appropriate access. Recommend full admin access"
)


@dataclass(frozen=True)
class _RouterReply:
    """What the Router does for one request and what each request form does with it.

    Attributes:
        route (Any): Fake Router route value for the request.
        path (str): Requested API path.
        plain_error (type[OPNsenseError] | None): Public error a plain request raises
            with ``throw_errors`` on, or ``None`` when the request succeeds.
        status (int | None): HTTP status of the raised connection error, when it has one.
        outcome (str): Outcome status of an outcome request: ``data``, ``absent`` or
            ``transient``.
        outcome_error (type[OPNsenseError] | None): Error the outcome carries.
        plain_log (str | None): Failure message of a plain request, with ``{op}`` for
            the operation name, or ``None`` when nothing is logged.
        outcome_log (str | None): Failure message of an outcome request.
        has_cause (bool): Whether the original transport error is the ``__cause__``.
    """

    route: Any
    path: str = _PATH
    plain_error: type[OPNsenseError] | None = None
    status: int | None = None
    outcome: str = "data"
    outcome_error: type[OPNsenseError] | None = None
    plain_log: str | None = None
    outcome_log: str | None = None
    has_cause: bool = False


_HTTP_LOG = "Error in {op} (called by t). Path: http://localhost/api/x. Response %d: ERR"
_TRANSPORT_LOG = "Client error in {op} (called by t). Path: /api/x. %s"
_DOWN = aiohttp.ClientConnectionError("down")
_SLOW = TimeoutError("slow")

_REPLIES: dict[str, _RouterReply] = {
    "2xx": _RouterReply(_OK_BODY),
    "401": _RouterReply(
        401,
        plain_error=OPNsenseInvalidAuth,
        status=401,
        outcome="transient",
        outcome_error=OPNsenseInvalidAuth,
        plain_log=_HTTP_LOG % 401,
        outcome_log=_HTTP_LOG % 401,
    ),
    "403": _RouterReply(
        403,
        plain_error=OPNsensePrivilegeMissing,
        status=403,
        outcome="absent",
        outcome_error=OPNsensePrivilegeMissing,
        plain_log="Permission Error in {op} (called by t). Path: http://localhost/api/x. "
        + _PERMISSION_MESSAGE,
        outcome_log="Permission Error in {op} (called by t). Path: http://localhost/api/x. "
        + _PERMISSION_MESSAGE,
    ),
    "404": _RouterReply(
        404,
        plain_error=OPNsenseConnectionError,
        status=404,
        outcome="absent",
        plain_log=_HTTP_LOG % 404,
    ),
    "500": _RouterReply(
        500,
        plain_error=OPNsenseConnectionError,
        status=500,
        outcome="transient",
        outcome_error=OPNsenseConnectionError,
        plain_log=_HTTP_LOG % 500,
        outcome_log=_HTTP_LOG % 500,
    ),
    "client-error": _RouterReply(
        _DOWN,
        plain_error=OPNsenseConnectionError,
        outcome="transient",
        outcome_error=OPNsenseConnectionError,
        plain_log=_TRANSPORT_LOG % "ClientConnectionError: down",
        outcome_log=_TRANSPORT_LOG % "ClientConnectionError: down",
        has_cause=True,
    ),
    "timeout": _RouterReply(
        _SLOW,
        plain_error=OPNsenseTimeoutError,
        outcome="transient",
        outcome_error=OPNsenseTimeoutError,
        plain_log=_TRANSPORT_LOG % "TimeoutError: slow",
        outcome_log=_TRANSPORT_LOG % "TimeoutError: slow",
        has_cause=True,
    ),
    "adr-0002-guard": _RouterReply(
        _OK_BODY,
        path=_BLOCKED_PATH,
        plain_error=OPNsenseInvalidArgument,
        outcome="transient",
        outcome_error=OPNsenseInvalidArgument,
        plain_log="Client error in {op} (called by t). Path: /api/core/firmware/info. "
        "_BlockedFirmwareRequest: Blocked ",
        outcome_log="Client error in {op} (called by t). Path: /api/core/firmware/info. "
        "_BlockedFirmwareRequest: Blocked ",
        has_cause=True,
    ),
}
"""Response matrix of the transport spec, keyed by Router behavior."""


async def _send(client: Any, form: str, method: str, path: str) -> Any:
    """Send one immediate request in the plain or outcome form.

    Args:
        client (Any): Client whose transport method is invoked.
        form (str): ``plain`` or ``outcome``.
        method (str): ``get`` or ``post``.
        path (str): API path to request.

    Returns:
        Any: Payload of a plain request, or the ``_EndpointOutcome`` of an outcome request.
    """
    request = getattr(client, f"_do_{method}" + ("_outcome" if form == "outcome" else ""))
    if method == "get":
        return await request(path, "t")
    return await request(path, {"a": 1}, "t")


def _failure_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Return the warning and error records the client logged.

    Args:
        caplog (pytest.LogCaptureFixture): Fixture capturing the client's log records.

    Returns:
        list[logging.LogRecord]: Records at WARNING or above.
    """
    return [record for record in caplog.records if record.levelno >= logging.WARNING]


@pytest.mark.parametrize("initial_throw", [False, True], ids=["enable", "disable"])
async def test_plain_http_failure_decides_throw_mode_before_response_cleanup(
    initial_throw: bool, make_client: MakeClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """Keep response cleanup from changing the transport's error decision or log.

    Args:
        initial_throw (bool): Throw mode when the Router responds.
        make_client (MakeClientFactory): Client factory fixture.
        caplog (pytest.LogCaptureFixture): Captures the client's logs.
    """

    class _ModeChangingResponse(FakeResponse):
        """Model a mode change while aiohttp is finishing the request."""

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> bool:
            """Change the client's throw mode during response cleanup.

            Args:
                exc_type (type[BaseException] | None): Exception type from the response body.
                exc (BaseException | None): Exception from the response body.
                tb (TracebackType | None): Traceback from the response body.

            Returns:
                bool: False so the response does not suppress an exception.
            """
            client.toggle_throwing_errors(not initial_throw)
            return await super().__aexit__(exc_type, exc, tb)

    path = "/api/wol/wol/set"
    response = _ModeChangingResponse(status=404, reason="Not Found", ok=False)
    client, router = make_router_client(
        make_client, {("POST", path): response}, throw_errors=initial_throw
    )

    result = await client.send_wol("lan", "02:00:00:00:00:01")

    # The public wrapper still uses the current mode if the transport raised.
    assert result is (None if initial_throw else False)
    assert router.count("POST", path) == 1
    failures = _failure_records(caplog)
    assert [record.getMessage() for record in failures] == [
        f"Error in do_post (called by send_wol). Path: http://localhost{path}. Response 404: Not Found"
    ]


@pytest.mark.parametrize("throw_errors", [False, True], ids=["empty", "throw"])
@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("form", ["plain", "outcome"])
@pytest.mark.parametrize("reply_name", list(_REPLIES))
async def test_response_matrix_is_the_same_for_every_request_form(
    reply_name: str,
    form: str,
    method: str,
    throw_errors: bool,
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the result, failure log and outage state of each Router response.

    Args:
        reply_name (str): Key of ``_REPLIES`` selecting the Router behavior.
        form (str): ``plain`` or ``outcome`` request.
        method (str): ``get`` or ``post``.
        throw_errors (bool): Whether the client raises public errors.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Fixture capturing the client's log records.
    """
    reply = _REPLIES[reply_name]
    client, router = make_router_client(
        make_client, {(method.upper(), reply.path): reply.route}, throw_errors=throw_errors
    )
    await client.reset_query_counts()
    expected_log = reply.plain_log if form == "plain" else reply.outcome_log
    operation = f"do_{method}" + ("_outcome" if form == "outcome" else "")

    with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
        if form == "plain" and throw_errors and reply.plain_error is not None:
            with pytest.raises(reply.plain_error) as raised:
                await _send(client, form, method, reply.path)
            if reply.status is not None:
                assert getattr(raised.value, "status", None) == reply.status
            if reply.has_cause:
                assert raised.value.__cause__ is not None
                assert type(raised.value.__cause__).__name__ in {
                    "ClientConnectionError",
                    "TimeoutError",
                    "_BlockedFirmwareRequest",
                }
            assert _is_request_error_logged(raised.value)
        else:
            result = await _send(client, form, method, reply.path)
            if form == "plain":
                assert result == (_OK_BODY if reply.plain_error is None else None)
            else:
                assert result.status.value == reply.outcome
                assert (type(result.error) if result.error else None) is reply.outcome_error
                if reply.outcome == "data":
                    assert result.payload == _OK_BODY
                if reply.has_cause:
                    assert result.error is not None
                    assert result.error.__cause__ is not None

    failures = _failure_records(caplog)
    if expected_log is None:
        assert failures == []
        assert client._request_outages == {}
    else:
        assert [record.levelno for record in failures] == [logging.ERROR]
        message = failures[0].getMessage()
        expected = expected_log.format(op=operation)
        if reply_name == "adr-0002-guard":
            assert message.startswith(expected)
            assert router.count() == 0
        else:
            assert message == expected
        assert client._request_outages.keys() == {(method.upper(), reply.path, "request")}
    assert await client.get_query_counts() == 1


@pytest.mark.parametrize("throw_errors", [False, True], ids=["empty", "throw"])
@pytest.mark.parametrize("form", ["plain", "outcome"])
@pytest.mark.parametrize("method", ["get", "post"])
async def test_undecodable_success_body_propagates_in_every_request_form(
    method: str,
    form: str,
    throw_errors: bool,
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 2xx response with a body that is not JSON raises the decode error and reports nothing.

    Args:
        method (str): ``get`` or ``post``.
        form (str): ``plain`` or ``outcome`` request.
        throw_errors (bool): Whether the client raises public errors.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Fixture capturing the client's log records.
    """
    client, _router = make_router_client(
        make_client, {(method.upper(), _PATH): MalformedJSONResponse()}, throw_errors=throw_errors
    )

    with caplog.at_level(logging.DEBUG, logger="aiopnsense"), pytest.raises(json.JSONDecodeError):
        await _send(client, form, method, _PATH)

    assert _failure_records(caplog) == []
    assert client._request_outages == {}


def _scripted(*replies: Any) -> Callable[..., Any]:
    """Build a fake Router route that answers successive requests in turn.

    Args:
        replies (Any): Route values, used in order. The last one repeats.

    Returns:
        Callable[..., Any]: Route callable for ``FakeRouter``.
    """
    remaining = list(replies)

    def reply(**_kwargs: Any) -> Any:
        """Return the next scripted reply.

        Args:
            _kwargs (Any): Request parameters, unused.

        Returns:
            Any: Next route value.
        """
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return reply


@pytest.mark.parametrize("throw_errors", [False, True], ids=["empty", "throw"])
@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    ("form", "failure"),
    [
        pytest.param("plain", 500, id="plain-500"),
        pytest.param("outcome", 500, id="outcome-500"),
        pytest.param("plain", 404, id="plain-404"),
    ],
)
async def test_repeated_failure_logs_at_debug_and_recovery_is_reported(
    form: str,
    failure: int,
    method: str,
    throw_errors: bool,
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A repeated failure is suppressed to DEBUG, then a success reports recovery.

    Args:
        form (str): ``plain`` or ``outcome`` request.
        failure (int): HTTP status of the failing replies.
        method (str): ``get`` or ``post``.
        throw_errors (bool): Whether the client raises public errors.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Fixture capturing the client's log records.
    """
    client, _router = make_router_client(
        make_client,
        {(method.upper(), _PATH): _scripted(failure, failure, _OK_BODY, failure)},
        throw_errors=throw_errors,
    )
    levels: list[int] = []
    with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
        for _attempt in range(4):
            caplog.clear()
            with contextlib.suppress(OPNsenseError):
                await _send(client, form, method, _PATH)
            levels.extend(
                record.levelno
                for record in caplog.records
                if record.getMessage().startswith("Error in")
                or " recovered for " in record.getMessage()
            )

    assert levels == [logging.ERROR, logging.DEBUG, logging.INFO, logging.ERROR]


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    ("steps", "recovered", "outage_remains"),
    [
        pytest.param([("plain", 500), ("outcome", _OK_BODY)], True, False, id="plain-then-outcome"),
        pytest.param([("outcome", 500), ("plain", _OK_BODY)], True, False, id="outcome-then-plain"),
        pytest.param(
            [("plain", 500), ("outcome", 404)], False, True, id="outcome-404-keeps-outage"
        ),
        pytest.param([("outcome", 404)], False, False, id="outcome-404-sets-no-outage"),
    ],
)
async def test_plain_and_outcome_requests_share_one_outage_per_method_path_and_phase(
    steps: list[tuple[str, Any]],
    recovered: bool,
    outage_remains: bool,
    method: str,
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A plain failure and an outcome success clear each other, and an outcome 404 does not.

    Args:
        steps (list[tuple[str, Any]]): Request form and Router reply of each request in turn.
        recovered (bool): Whether a recovery message is logged.
        outage_remains (bool): Whether an outage is still recorded at the end.
        method (str): ``get`` or ``post``.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Fixture capturing the client's log records.
    """
    client, _router = make_router_client(
        make_client, {(method.upper(), _PATH): _scripted(*(reply for _form, reply in steps))}
    )

    with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
        for form, _reply in steps:
            await _send(client, form, method, _PATH)

    assert len([r for r in caplog.records if " recovered for " in r.getMessage()]) == int(recovered)
    assert bool(client._request_outages) is outage_remains


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("form", ["plain", "outcome"])
async def test_request_shape_is_the_same_for_every_request_form(
    form: str, method: str, make_client: MakeClientFactory
) -> None:
    """Each request carries the URL, authentication, TLS setting, default timeout and JSON body.

    Args:
        form (str): ``plain`` or ``outcome`` request.
        method (str): ``get`` or ``post``.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    sent: list[dict[str, Any]] = []

    def reply(**kwargs: Any) -> Any:
        """Record the request parameters and answer successfully.

        Args:
            kwargs (Any): Request parameters passed to the session.

        Returns:
            Any: A JSON body.
        """
        sent.append(kwargs)
        return _OK_BODY

    client, router = make_router_client(
        make_client,
        {(method.upper(), _PATH): reply},
        username="user",
        password="secret",
        opts={"verify_ssl": False},
    )

    await _send(client, form, method, _PATH)

    assert router.count(method, _PATH) == 1
    [kwargs] = sent
    assert (kwargs["auth"].login, kwargs["auth"].password) == ("user", "secret")
    assert kwargs["ssl"] is False
    assert kwargs["timeout"].total == DEFAULT_REQUEST_TIMEOUT_SECONDS
    if method == "post":
        assert kwargs["json"] == {"a": 1}
    else:
        assert "json" not in kwargs


async def test_post_without_payload_passes_json_none(
    make_client: MakeClientFactory,
) -> None:
    """A POST with no payload still passes ``json=None`` to the session.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    sent: list[dict[str, Any]] = []

    def reply(**kwargs: Any) -> Any:
        """Record the request parameters and answer successfully.

        Args:
            kwargs (Any): Request parameters passed to the session.

        Returns:
            Any: A JSON body.
        """
        sent.append(kwargs)
        return _OK_BODY

    client, _router = make_router_client(make_client, {("POST", _PATH): reply})

    await client._do_post(_PATH)
    await client._do_post_outcome(_PATH)

    assert [kwargs["json"] for kwargs in sent] == [None, None]


async def test_per_call_timeout_reaches_the_request_and_bypasses_the_queue(
    make_client: MakeClientFactory,
) -> None:
    """The timeout of ``_safe_dict_get_with_timeout`` is the request's total timeout, sent directly.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    timeouts: list[float | None] = []

    def reply(**kwargs: Any) -> Any:
        """Record the request timeout and answer successfully.

        Args:
            kwargs (Any): Request parameters passed to the session.

        Returns:
            Any: A JSON body.
        """
        timeouts.append(kwargs["timeout"].total)
        return _OK_BODY

    client, _router = make_router_client(make_client, {("GET", _PATH): reply})

    assert await client._safe_dict_get_with_timeout(_PATH, 180) == _OK_BODY
    assert client._workers == []
    assert await client._safe_dict_get(_PATH) == _OK_BODY

    assert timeouts == [180, DEFAULT_REQUEST_TIMEOUT_SECONDS]
    assert client._workers != []
