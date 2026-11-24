"""Tests for the pure server-sent-event decoder."""

import json

import pytest

from aiopnsense._sse_decoder import (
    _SseDecoder,
    _SseItem,
    _SseMessage,
    _SseRejection,
    _SseRejectionReason,
)


def _decode(chunks: list[bytes]) -> list[_SseItem]:
    """Feed every chunk to a fresh decoder, then finish it.

    Args:
        chunks (list[bytes]): Chunks to feed in order.

    Returns:
        list[_SseItem]: Every item returned by ``feed`` and ``finish``, in order.
    """
    decoder = _SseDecoder()
    items: list[_SseItem] = []
    for chunk in chunks:
        items.extend(decoder.feed(chunk))
    items.extend(decoder.finish())
    return items


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        pytest.param([b'data: {"a": 1}\n\n'], [{"a": 1}], id="single"),
        pytest.param(
            [b': keep-alive\nevent: update\nid: 7\ndata: {"a": 1}\n\n'],
            [{"a": 1}],
            id="non-data-lines-ignored",
        ),
        pytest.param(
            [b": keep-alive\nevent: ping\n\n", b'data: {"a": 2}\n\n'],
            [{"a": 2}],
            id="comment-only-frame-skipped",
        ),
        pytest.param(
            [b'data: {"a": 1,\r\n', b'data: "b": 2\r\n', b"data: }\r\n\r\n"],
            [{"a": 1, "b": 2}],
            id="multi-line-event-split-across-chunks",
        ),
        pytest.param(
            [b'data: {"a": 1,\r', b'\ndata: "b": 2}\r', b"\n\r\n"],
            [{"a": 1, "b": 2}],
            id="crlf-split-across-chunks",
        ),
        pytest.param(
            [b'data: {"a": 1}\r', b'\rdata: {"a": 2}\r\r'],
            [{"a": 1}, {"a": 2}],
            id="bare-cr-after-chunk-ending-cr",
        ),
        pytest.param(
            [b'data: {"a": "x\xc3', b'\xa9"}\n\n'],
            [{"a": "xé"}],
            id="multibyte-character-split-across-chunks",
        ),
        pytest.param(
            [b'data:   {"a": "  x"}\n\n'],
            [{"a": "  x"}],
            id="leading-spaces-in-json-kept-in-values",
        ),
        pytest.param(
            [b'data: {"a": 1}\n\r'],
            [{"a": 1}],
            id="pending-cr-completes-event-at-end-of-stream",
        ),
        pytest.param(
            [b'data: {"a"', b": 1}\n\n", b'data: {"b": 2}\n\n'],
            [{"a": 1}, {"b": 2}],
            id="partial-chunks-accumulate-into-one-event",
        ),
        pytest.param([b'data: {"a": 1}\n'], [], id="unterminated-event-dropped"),
    ],
)
def test_decoder_emits_messages(chunks: list[bytes], expected: list[dict[str, object]]) -> None:
    """The decoder returns each completed JSON mapping event once, in order.

    Args:
        chunks (list[bytes]): Chunks fed to the decoder.
        expected (list[dict[str, object]]): Payloads of the decoded messages.
    """
    items = _decode(chunks)

    assert items == [_SseMessage(payload) for payload in expected]


def test_decoder_rejects_malformed_json() -> None:
    """Malformed JSON is a rejection that carries the text and the decoding exception."""
    [item] = _decode([b"data: not-json\n\n"])

    assert isinstance(item, _SseRejection)
    assert item.reason is _SseRejectionReason.MALFORMED_JSON
    assert item.text == "not-json"
    assert isinstance(item.error, json.JSONDecodeError)


@pytest.mark.parametrize(
    ("data", "json_type"),
    [(b"[1, 2]", "list"), (b"1", "int"), (b'"x"', "str"), (b"null", "NoneType")],
)
def test_decoder_rejects_non_mapping_json(data: bytes, json_type: str) -> None:
    """Non-mapping JSON is a rejection that names the JSON type.

    Args:
        data (bytes): The event's JSON value.
        json_type (str): Expected Python type name of the decoded value.
    """
    [item] = _decode([b"data: " + data + b"\n\n"])

    assert item == _SseRejection(
        _SseRejectionReason.NON_MAPPING_JSON, text=data.decode(), json_type=json_type
    )


def test_decoder_recovers_after_invalid_utf8_and_discards_buffered_text() -> None:
    """Invalid UTF-8 returns one rejection, drops buffered text and the next chunk decodes."""
    decoder = _SseDecoder()
    assert decoder.feed(b'data: {"a": 1}\n\ndata: {"half"') == [_SseMessage({"a": 1})]

    [rejection] = decoder.feed(b"\xff\xfe")

    assert isinstance(rejection, _SseRejection)
    assert rejection.reason is _SseRejectionReason.INVALID_UTF8
    assert rejection.text is None
    assert isinstance(rejection.error, UnicodeDecodeError)
    assert decoder.feed(b'data: {"a": 2}\n\n') == [_SseMessage({"a": 2})]
    assert decoder.finish() == []


def test_decoder_rejects_truncated_utf8_at_end_of_stream() -> None:
    """A character cut off at end of stream is rejected first, keeping earlier messages."""
    decoder = _SseDecoder()
    assert decoder.feed(b'data: {"a": 1}\n\n') == [_SseMessage({"a": 1})]
    assert decoder.feed(b'data: {"a": "x\xc3') == []

    [item] = decoder.finish()

    assert isinstance(item, _SseRejection)
    assert item.reason is _SseRejectionReason.TRUNCATED_UTF8
    assert isinstance(item.error, UnicodeDecodeError)


def test_decoder_preserves_item_order() -> None:
    """Messages and rejections come back in stream order."""
    items = _decode([b'data: {"a": 1}\n\ndata: nope\n\ndata: {"a": 3}\n\n'])

    assert [type(item).__name__ for item in items] == [
        "_SseMessage",
        "_SseRejection",
        "_SseMessage",
    ]
    assert [item.payload for item in items if isinstance(item, _SseMessage)] == [{"a": 1}, {"a": 3}]
