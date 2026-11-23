"""Pure decoder for the Router's server-sent-event JSON streams.

``_SseDecoder`` turns bytes chunks into decoded messages and typed rejections. It holds no HTTP
or client state, does not log and never raises for stream content, so each stream read applies
its own policy to the items it returns.
"""

import codecs
from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Any


class _SseRejectionReason(StrEnum):
    """Why the decoder could not turn stream input into a message."""

    INVALID_UTF8 = "invalid_utf8"
    TRUNCATED_UTF8 = "truncated_utf8"
    MALFORMED_JSON = "malformed_json"
    NON_MAPPING_JSON = "non_mapping_json"


@dataclass(frozen=True, slots=True)
class _SseMessage:
    """A decoded event whose ``data:`` lines hold a JSON mapping.

    Attributes:
        payload (dict[str, Any]): The decoded JSON mapping.
    """

    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _SseRejection:
    """Stream input that did not decode into a message.

    Attributes:
        reason (_SseRejectionReason): Why the input was rejected.
        text (str | None): The rejected event text, when there is any.
        json_type (str | None): JSON type name of a non-mapping value.
        error (UnicodeDecodeError | json.JSONDecodeError | None): The original decoding
            exception, when there is one.
    """

    reason: _SseRejectionReason
    text: str | None = None
    json_type: str | None = None
    error: UnicodeDecodeError | json.JSONDecodeError | None = None


type _SseItem = _SseMessage | _SseRejection


class _SseDecoder:
    """Incrementally decode a bytes stream into JSON messages and rejections."""

    def __init__(self) -> None:
        """Start with an empty decoder."""
        self._reset()

    def _reset(self) -> None:
        """Discard the UTF-8 decoder state, buffered text and any pending CR."""
        self._utf8 = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""
        self._pending_cr = False

    def feed(self, chunk: bytes) -> list[_SseItem]:
        """Decode one bytes chunk.

        Args:
            chunk (bytes): The next bytes of the stream.

        Returns:
            list[_SseItem]: Items completed by this chunk, in order. Invalid UTF-8 returns one
                rejection and discards all buffered state.
        """
        try:
            text = self._utf8.decode(chunk)
        except UnicodeDecodeError as err:
            self._reset()
            return [_SseRejection(_SseRejectionReason.INVALID_UTF8, error=err)]
        if self._pending_cr:
            text = f"\r{text}"
            self._pending_cr = False
        if text.endswith("\r"):
            text = text[:-1]
            self._pending_cr = True
        self._buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        return self._drain()

    def finish(self) -> list[_SseItem]:
        """Flush end-of-stream state.

        Returns:
            list[_SseItem]: A truncated UTF-8 rejection first when the stream ended inside a
                character, then the events completed by treating a pending CR as a line ending.
                Text with no terminating blank line is dropped.
        """
        items: list[_SseItem] = []
        try:
            self._utf8.decode(b"", final=True)
        except UnicodeDecodeError as err:
            items.append(_SseRejection(_SseRejectionReason.TRUNCATED_UTF8, error=err))
        if self._pending_cr:
            self._buffer += "\n"
            self._pending_cr = False
        items.extend(self._drain())
        return items

    def _drain(self) -> list[_SseItem]:
        """Decode every complete event in the buffer.

        Returns:
            list[_SseItem]: One item per event that has a ``data:`` line, in order.
        """
        items: list[_SseItem] = []
        while "\n\n" in self._buffer:
            event, self._buffer = self._buffer.split("\n\n", 1)
            data_lines = [
                line[len("data:") :].removeprefix(" ")
                for line in event.splitlines()
                if line.startswith("data:")
            ]
            if data_lines:
                items.append(_decode_data("\n".join(data_lines)))
        return items


def _decode_data(data: str) -> _SseItem:
    """Decode the joined ``data:`` text of one event.

    Args:
        data (str): The event's ``data:`` values joined with newlines.

    Returns:
        _SseItem: A message for a JSON mapping, otherwise a rejection.
    """
    try:
        value = json.loads(data)
    except json.JSONDecodeError as err:
        return _SseRejection(_SseRejectionReason.MALFORMED_JSON, text=data, error=err)
    if not isinstance(value, dict):
        return _SseRejection(
            _SseRejectionReason.NON_MAPPING_JSON, text=data, json_type=type(value).__name__
        )
    return _SseMessage(value)
