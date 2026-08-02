"""Tests for the streaming assembly logic.

Uses a plain generator of mock chunk objects to simulate the SDK's
streaming response, so no network calls or API keys are involved.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from main import StreamingError, consume_stream, iter_chunks, start_stream


def make_chunk(text: str | None) -> MagicMock:
    """Build a mock SDK chunk object exposing a ``.text`` attribute."""
    chunk = MagicMock()
    chunk.text = text
    return chunk


def test_iter_chunks_yields_text_in_order() -> None:
    chunks = [make_chunk("Hello"), make_chunk(", "), make_chunk("world!")]
    assert list(iter_chunks(chunks)) == ["Hello", ", ", "world!"]


def test_iter_chunks_skips_empty_text() -> None:
    chunks = [make_chunk("A"), make_chunk(None), make_chunk(""), make_chunk("B")]
    assert list(iter_chunks(chunks)) == ["A", "B"]


def test_consume_stream_assembles_full_response() -> None:
    result = consume_stream(iter(["The ", "quick ", "fox"]))
    assert result == "The quick fox"


def test_consume_stream_invokes_callback_per_chunk() -> None:
    received: list[str] = []
    result = consume_stream(iter(["a", "b", "c"]), on_chunk=received.append)

    assert received == ["a", "b", "c"]
    assert result == "abc"


def test_consume_stream_with_no_chunks_returns_empty_string() -> None:
    assert consume_stream(iter([])) == ""


def test_start_stream_calls_generate_content_with_stream_true() -> None:
    model = MagicMock()
    model.generate_content.return_value = iter([make_chunk("hi")])

    start_stream(model, "hello")

    model.generate_content.assert_called_once_with("hello", stream=True)


def test_start_stream_rejects_empty_prompt() -> None:
    with pytest.raises(StreamingError):
        start_stream(MagicMock(), "   ")


def test_start_stream_wraps_sdk_errors() -> None:
    model = MagicMock()
    model.generate_content.side_effect = RuntimeError("connection reset")

    with pytest.raises(StreamingError):
        start_stream(model, "hello")


def test_end_to_end_stream_to_assembled_text() -> None:
    """Full pipeline: SDK-shaped chunks -> iter_chunks -> consume_stream."""
    model = MagicMock()
    model.generate_content.return_value = iter(
        [make_chunk("Once "), make_chunk("upon "), make_chunk("a time.")]
    )

    chunks = start_stream(model, "Tell me a story.")
    full_text = consume_stream(iter_chunks(chunks))

    assert full_text == "Once upon a time."
