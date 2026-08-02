# 04 · Streaming Responses

Stream a Gemini response to stdout chunk-by-chunk as it arrives, instead
of blocking until the full completion is ready.

## Streaming vs. non-streaming

A normal (non-streaming) call blocks until the model has generated the
entire response, then returns it all at once. A streaming call returns an
iterator: the SDK yields chunks of text as the model produces them, so
your program can start displaying output within a few hundred
milliseconds instead of waiting for the whole thing.

**UX benefits:**

- **Perceived latency** — users see the response start appearing almost
  immediately, even if total generation time is unchanged. This is the
  difference between a chat UI that feels instant and one that feels
  frozen.
- **Early cancellation** — if the response is clearly going off the
  rails, you can stop consuming the stream (and stop paying for tokens
  you don't want) instead of waiting for a full generation you'll discard.
- **Progressive rendering** — long-form output (code, essays, structured
  data) can be rendered as it arrives, which is far more usable than a
  loading spinner followed by a wall of text.

**Trade-offs:** streaming code is a bit more complex (you're consuming an
iterator instead of reading one value), and you generally can't easily
retry or post-process the whole response until it's fully assembled.

## How it works

`src/main.py` is split into small, composable pieces:

- `build_model()` — constructs the real Gemini SDK model (only touched at
  runtime).
- `start_stream(model, prompt)` — calls `model.generate_content(prompt,
  stream=True)` and returns the raw iterator of SDK chunk objects.
- `iter_chunks(chunks)` — normalizes SDK chunks (each with a `.text`
  attribute) into plain strings, skipping empty ones.
- `consume_stream(chunks, on_chunk)` — drains the text stream, forwarding
  each chunk to a callback (e.g. printing it immediately) and returning
  the fully assembled response for logging.

This separation is what makes the module testable without a real network
stream: tests hand `iter_chunks`/`consume_stream` a plain Python iterator
of mock chunks and assert on the assembled output.

## Usage

```bash
cp .env.example .env
# fill in GEMINI_API_KEY

uv sync
uv run python src/main.py "Write a haiku about distributed systems."
```

Output appears incrementally in your terminal as each chunk arrives.

## Tests

```bash
uv run pytest
```

Tests simulate the SDK's streaming iterator with plain mock objects — no
network calls or live API key required.
