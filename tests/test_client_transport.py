"""Tests for client transport helpers and HTTP response handling."""

from collections.abc import MutableMapping
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock
from types import TracebackType
import json

import aiohttp
import pytest

from aiopnsense.const import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
)
from tests.conftest import (
    FakeResponse,
    FakeStreamResponseFactory,
    MakeClientFactory,
    make_mock_session_client,
)


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
async def test_stream_json_events_yields_each_valid_data_message(
    make_client: Callable[..., Any],
    fake_stream_response_factory: Callable[..., FakeResponse],
) -> None:
    """Direct stream iterator should yield every JSON SSE data message."""
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
async def test_stream_json_events_skips_malformed_json(
    make_client: Callable[..., Any],
    fake_stream_response_factory: Callable[..., FakeResponse],
) -> None:
    """Malformed data messages should be skipped while the stream continues."""
    session = MagicMock()
    session.get = lambda *a, **k: fake_stream_response_factory(
        chunks=[
            b"data: not-json\n\n",
            b'data: {"time": 3, "interfaces": {}}\n\n',
        ]
    )
    client = make_client(session=session)
    try:
        events: list[dict[str, Any]] = []
        async for event in client._stream_json_events("/api/diagnostics/traffic/stream/1"):
            events.append(event)
            break

        assert events == [{"time": 3, "interfaces": {}}]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_json_events_reassembles_split_multiline_json_event(
    make_client: Callable[..., Any],
    fake_stream_response_factory: FakeStreamResponseFactory,
) -> None:
    """Split and multiline SSE data lines should reassemble into one JSON object."""
    session = MagicMock()
    session.get = lambda *a, **k: fake_stream_response_factory(
        [
            b'data: {"time": 10,\r\n',
            b'data: "interfaces": {"wan": {"rx_bytes": 11}}\r\n',
            b"data: }\r\n\r\n",
        ]
    )
    client = make_client(session=session)
    try:
        events: list[dict[str, Any]] = []
        async for event in client._stream_json_events("/api/diagnostics/traffic/stream/1"):
            events.append(event)
            break
        assert events == [{"time": 10, "interfaces": {"wan": {"rx_bytes": 11}}}]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_json_events_reassembles_split_multibyte_utf8_event(
    make_client: Callable[..., Any],
    fake_stream_response_factory: FakeStreamResponseFactory,
) -> None:
    """Split UTF-8 multibyte sequences across chunks should decode without error."""
    event_json = json.dumps(
        {
            "time": 11,
            "interfaces": {
                "wan": {
                    "interface": "wlan-é",
                    "name": None,
                    "description": "Café",
                    "bytes received": 120,
                    "bytes transmitted": 240,
                }
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    event_payload = f"data: {event_json.decode('utf-8')}\n\n".encode("utf-8")
    accent_index = event_payload.index("é".encode("utf-8")) + 1
    chunks = [
        b'data: {"time": 9, "interfaces": {"wan": {"bytes received": 0, "bytes transmitted": 0}}}\n\n',
        event_payload[:accent_index],
        event_payload[accent_index:],
    ]
    session = MagicMock()
    session.get = lambda *a, **k: fake_stream_response_factory(chunks)
    client = make_client(session=session)
    try:
        events: list[dict[str, Any]] = []
        async for event in client._stream_json_events("/api/diagnostics/traffic/stream/1"):
            events.append(event)
            if len(events) == 2:
                break

        assert len(events) == 2
        assert events[1]["interfaces"] == {
            "wan": {
                "interface": "wlan-é",
                "name": None,
                "description": "Café",
                "bytes received": 120,
                "bytes transmitted": 240,
            }
        }
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_json_events_ignores_trailing_incomplete_utf8_chunk(
    make_client: Callable[..., Any],
    fake_stream_response_factory: FakeStreamResponseFactory,
) -> None:
    """Trailing incomplete UTF-8 bytes should not raise and should drop only partial event."""
    incomplete_event_json = json.dumps(
        {"time": 12, "interfaces": {"wan": {"description": "Café"}}},
        ensure_ascii=False,
    ).encode("utf-8")
    incomplete_payload = f"data: {incomplete_event_json.decode('utf-8')}\n\n".encode("utf-8")
    leading_byte_index = incomplete_payload.index("é".encode("utf-8"))
    leading_byte_index += 1

    session = MagicMock()
    session.get = lambda *a, **k: fake_stream_response_factory(
        [
            b'data: {"time": 11}\n\n',
            incomplete_payload[:leading_byte_index],
        ]
    )
    client = make_client(session=session)
    try:
        events: list[dict[str, Any]] = []
        async for event in client._stream_json_events("/api/diagnostics/traffic/stream/1"):
            events.append(event)

        assert events == [{"time": 11}]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_json_events_reassembles_split_crlf_boundary_multiline_json_event(
    make_client: Callable[..., Any],
    fake_stream_response_factory: FakeStreamResponseFactory,
) -> None:
    """Split CRLF frame separators across chunks should still reassemble a valid event."""
    session = MagicMock()
    session.get = lambda *a, **k: fake_stream_response_factory(
        [
            b'data: {"time": 11,\r',
            b'\ndata: "interfaces": {"wan": {"rx_bytes": 12}}\r',
            b"data: }\r",
            b"\n\r\n",
        ]
    )
    client = make_client(session=session)
    try:
        events: list[dict[str, Any]] = []
        async for event in client._stream_json_events("/api/diagnostics/traffic/stream/1"):
            events.append(event)
            break
        assert events == [{"time": 11, "interfaces": {"wan": {"rx_bytes": 12}}}]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_json_events_skips_non_mapping_json_events(
    make_client: Callable[..., Any],
    fake_stream_response_factory: FakeStreamResponseFactory,
) -> None:
    """Non-mapping JSON SSE events should be skipped and later mapping events yielded."""
    session = MagicMock()
    session.get = lambda *a, **k: fake_stream_response_factory(
        [
            b"data: [1, 2, 3]\n\n",
            b"data: 1\n\n",
            b'data: {"time": 9, "interfaces": {}}\n\n',
        ]
    )
    client = make_client(session=session)
    try:
        events: list[dict[str, Any]] = []
        async for event in client._stream_json_events("/api/diagnostics/traffic/stream/1"):
            events.append(event)
            if len(events) == 1:
                break
        assert events == [{"time": 9, "interfaces": {}}]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_stream_json_events_close_on_iterator_break(
    make_client: Callable[..., Any],
) -> None:
    """Canceling iteration early should still close the response context."""

    class _TrackedResponse(FakeResponse):
        """Fake response that tracks async context manager exit."""

        def __init__(self, **kwargs: Any) -> None:
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
    """Direct stream iterator should honor throw_errors for non-OK responses."""
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
        with pytest.raises(aiohttp.ClientResponseError):
            async for _event in client._stream_json_events("/api/diagnostics/traffic/stream/1"):
                pass
    finally:
        await client.async_close()
