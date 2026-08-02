# Recipe: Streaming Responses

How to stream tokens back to a caller as they're generated, instead of
blocking until the full response is ready. See
`projects/04-streaming-responses` for a runnable implementation.

## The pattern

```python
"""Minimal token-streaming pattern, provider-agnostic."""

from __future__ import annotations

import logging
from collections.abc import Iterator

logger = logging.getLogger(__name__)


def stream_completion(prompt: str) -> Iterator[str]:
    """Yield response chunks as they arrive from the model.

    A real implementation delegates to the provider's streaming API, e.g.:

        import google.generativeai as genai
        model = genai.GenerativeModel("gemini-1.5-flash")
        for chunk in model.generate_content(prompt, stream=True):
            yield chunk.text

    This stub simulates streaming by yielding word-by-word.
    """
    placeholder_response = "This is a simulated streamed response."
    for word in placeholder_response.split():
        yield word + " "


def print_stream(prompt: str) -> str:
    """Consume a stream, printing chunks as they arrive, and return the full text.

    Returns:
        The fully assembled response text.
    """
    full_response = ""
    try:
        for chunk in stream_completion(prompt):
            print(chunk, end="", flush=True)
            full_response += chunk
    except Exception:
        logger.exception("Streaming failed partway through response")
        raise
    print()  # trailing newline
    return full_response
```

## Why this shape

- **`Iterator[str]` keeps it provider-agnostic.** Whatever SDK you call
  underneath, the caller only depends on "an iterator of string chunks" —
  swapping providers doesn't change the consuming code.
- **Accumulate as you go.** Callers that need the full text (for logging,
  evaluation, or storage) should build it up chunk-by-chunk rather than
  re-requesting a non-streamed call afterward.
- **Wrap consumption in try/except.** A dropped connection mid-stream should
  surface as a clear error (and get logged with `logger.exception` for a
  full traceback), not fail silently with a truncated response that looks
  complete.

## Streaming in a web handler (e.g. FastAPI)

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()


@app.post("/chat")
def chat(prompt: str) -> StreamingResponse:
    return StreamingResponse(stream_completion(prompt), media_type="text/plain")
```

## Next steps

- `projects/04-streaming-responses/src/main.py` — full working example
  against a real provider
- Combine with the [multi-turn chat recipe](multi-turn-chat.md) to stream
  each assistant turn while still appending the complete text to history
  once the stream finishes.
