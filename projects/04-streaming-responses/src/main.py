"""Stream a Gemini response chunk-by-chunk instead of waiting for the full completion.

Split into small, independently testable pieces:

- :func:`build_model` constructs the real SDK model (only called at
  runtime, never during tests).
- :func:`iter_chunks` normalizes a stream of SDK chunk objects (each
  exposing ``.text``) into plain strings.
- :func:`consume_stream` drains an iterable of text chunks, forwarding
  each to a sink callback and returning the fully assembled response.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable, Iterable, Iterator
from typing import Any, Protocol

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"


class StreamingError(RuntimeError):
    """Raised when a streaming request cannot be started or completed."""


class TextChunk(Protocol):
    """Structural type for one chunk of a streaming SDK response."""

    text: str


def configure_logging(level: str | None = None) -> None:
    """Configure root logging once for CLI usage.

    Args:
        level: Log level name (e.g. ``"INFO"``). Falls back to the
            ``LOG_LEVEL`` environment variable, then ``"INFO"``.
    """
    level_name = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        stream=sys.stderr,
    )


def build_model(api_key: str | None = None, model_name: str | None = None) -> Any:
    """Construct a real ``google.generativeai`` model for streaming.

    Imported lazily so the SDK is only required at runtime, not at
    test-collection time.

    Args:
        api_key: Gemini API key. Falls back to ``GEMINI_API_KEY``.
        model_name: Model id. Falls back to ``GEMINI_MODEL``, then
            :data:`DEFAULT_GEMINI_MODEL`.

    Returns:
        A configured ``genai.GenerativeModel`` instance.

    Raises:
        StreamingError: If no API key is available.
    """
    resolved_key = api_key or os.getenv("GEMINI_API_KEY")
    if not resolved_key:
        raise StreamingError("GEMINI_API_KEY is not set.")

    import google.generativeai as genai

    genai.configure(api_key=resolved_key)
    return genai.GenerativeModel(model_name or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL))


def start_stream(model: Any, prompt: str) -> Iterable[TextChunk]:
    """Start a streaming generation call against ``model``.

    Args:
        model: An object exposing ``generate_content(prompt, stream=True)``,
            e.g. a ``genai.GenerativeModel`` or a test double.
        prompt: The prompt to send.

    Returns:
        An iterable of chunk objects, each exposing a ``.text`` attribute.

    Raises:
        StreamingError: If the prompt is empty or the SDK call fails to
            start.
    """
    if not prompt or not prompt.strip():
        raise StreamingError("Prompt must be a non-empty string.")

    try:
        return model.generate_content(prompt, stream=True)
    except Exception as exc:
        logger.error("Failed to start Gemini stream: %s", exc)
        raise StreamingError(f"Failed to start stream: {exc}") from exc


def iter_chunks(chunks: Iterable[TextChunk]) -> Iterator[str]:
    """Yield the ``.text`` of each chunk, skipping chunks with no text.

    Args:
        chunks: An iterable of SDK chunk objects (or mocks) each exposing
            a ``.text`` attribute.

    Yields:
        Non-empty text fragments in arrival order.
    """
    for chunk in chunks:
        text = getattr(chunk, "text", None)
        if text:
            yield text


def consume_stream(chunks: Iterable[str], on_chunk: Callable[[str], None] | None = None) -> str:
    """Drain a stream of text chunks, forwarding each to ``on_chunk``.

    Args:
        chunks: An iterable of text fragments, in arrival order.
        on_chunk: Optional callback invoked once per chunk as it arrives
            (e.g. to write it to stdout). Defaults to a no-op so this
            function is safe to call purely for assembly in tests.

    Returns:
        The full response, formed by concatenating every chunk in order.
    """
    sink = on_chunk or (lambda _text: None)
    assembled: list[str] = []
    for text in chunks:
        assembled.append(text)
        sink(text)
    return "".join(assembled)


def _print_chunk(text: str) -> None:
    """Write a chunk to stdout immediately, without a trailing newline."""
    print(text, end="", flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Stream a Gemini response to stdout as it arrives."
    )
    parser.add_argument("prompt", help="Prompt to send to Gemini.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        Process exit code: 0 on success, 1 on failure to stream.
    """
    load_dotenv()
    configure_logging()
    args = parse_args(argv)

    try:
        model = build_model()
        chunks = start_stream(model, args.prompt)
        full_text = consume_stream(iter_chunks(chunks), on_chunk=_print_chunk)
        print()  # trailing newline after the streamed output
    except StreamingError as exc:
        logger.error("Streaming failed: %s", exc)
        return 1

    logger.info("Streamed %d characters.", len(full_text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
