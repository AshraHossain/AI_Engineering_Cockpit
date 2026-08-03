"""Tests for the streaming assembly logic.

The SDK is mocked at the ``google-genai`` client boundary: a mock client
whose ``.models.generate_content_stream(model=..., contents=...)``
returns a plain Python iterator of mock chunk objects. No network calls
or API keys are involved.

Real chunks can carry ``text=None`` (metadata-only chunks), so that case
is covered explicitly rather than assumed away.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from main import (
    DEFAULT_GEMINI_MODEL,
    StreamingError,
    consume_stream,
    iter_chunks,
    resolve_model_name,
    start_stream,
)


def make_chunk(text: str | None) -> MagicMock:
    """Build a mock SDK chunk object exposing a ``.text`` attribute.

    Args:
        text: The chunk's text payload. ``None`` models a real
            metadata-only chunk.

    Returns:
        A :class:`~unittest.mock.MagicMock` shaped like an SDK chunk.
    """
    chunk = MagicMock()
    chunk.text = text
    return chunk


def make_genai_client(chunks=None, error: Exception | None = None) -> MagicMock:
    """Build a mock ``google.genai.Client`` for the streaming path.

    Args:
        chunks: Iterable of mock chunk objects the stream should yield.
        error: Exception ``models.generate_content_stream`` should raise
            instead of returning a stream.

    Returns:
        A :class:`~unittest.mock.MagicMock` shaped like a genai client.
    """
    client = MagicMock()
    if error is not None:
        client.models.generate_content_stream.side_effect = error
    else:
        client.models.generate_content_stream.return_value = iter(chunks or [])
    return client


def test_iter_chunks_yields_text_in_order() -> None:
    chunks = [make_chunk("Hello"), make_chunk(", "), make_chunk("world!")]
    assert list(iter_chunks(chunks)) == ["Hello", ", ", "world!"]


def test_iter_chunks_skips_empty_text() -> None:
    chunks = [make_chunk("A"), make_chunk(None), make_chunk(""), make_chunk("B")]
    assert list(iter_chunks(chunks)) == ["A", "B"]


def test_iter_chunks_handles_chunk_with_none_text() -> None:
    """Confirmed live SDK behavior: some chunks carry no text at all."""
    assert list(iter_chunks([make_chunk(None)])) == []


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


def test_start_stream_calls_generate_content_stream_with_model_and_contents() -> None:
    client = make_genai_client(chunks=[make_chunk("hi")])

    start_stream(client, "hello")

    client.models.generate_content_stream.assert_called_once_with(
        model=DEFAULT_GEMINI_MODEL,
        contents="hello",
    )


def test_start_stream_passes_explicit_model_name() -> None:
    client = make_genai_client(chunks=[make_chunk("hi")])

    start_stream(client, "hello", model_name="gemini-2.5-flash-lite")

    client.models.generate_content_stream.assert_called_once_with(
        model="gemini-2.5-flash-lite",
        contents="hello",
    )


def test_resolve_model_name_prefers_env_over_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
    assert resolve_model_name() == "gemini-2.5-pro"


def test_resolve_model_name_falls_back_to_default() -> None:
    assert resolve_model_name() == DEFAULT_GEMINI_MODEL


def test_default_model_is_not_a_retired_1_5_id() -> None:
    """``gemini-1.5-*`` ids now 404 -- guard against regressing to one."""
    assert not DEFAULT_GEMINI_MODEL.startswith("gemini-1.5")


def test_start_stream_rejects_empty_prompt() -> None:
    with pytest.raises(StreamingError):
        start_stream(make_genai_client(), "   ")


def test_start_stream_wraps_sdk_errors() -> None:
    client = make_genai_client(error=RuntimeError("connection reset"))

    with pytest.raises(StreamingError):
        start_stream(client, "hello")


def test_end_to_end_stream_to_assembled_text() -> None:
    """Full pipeline: SDK-shaped chunks -> iter_chunks -> consume_stream."""
    client = make_genai_client(
        chunks=[make_chunk("Once "), make_chunk("upon "), make_chunk("a time.")]
    )

    chunks = start_stream(client, "Tell me a story.")
    full_text = consume_stream(iter_chunks(chunks))

    assert full_text == "Once upon a time."


def test_end_to_end_stream_survives_none_text_chunk() -> None:
    """A metadata-only chunk mid-stream must not break assembly or the sink."""
    client = make_genai_client(
        chunks=[make_chunk("Once "), make_chunk(None), make_chunk("upon a time.")]
    )
    received: list[str] = []

    chunks = start_stream(client, "Tell me a story.")
    full_text = consume_stream(iter_chunks(chunks), on_chunk=received.append)

    assert full_text == "Once upon a time."
    assert received == ["Once ", "upon a time."]  # the None chunk never reaches the sink
