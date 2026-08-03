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

This project uses the current **`google-genai`** SDK. The legacy
`google-generativeai` package (`genai.configure()` + a per-model
`GenerativeModel` with `generate_content(..., stream=True)`) is
end-of-life and is not used. Default model: `gemini-2.5-flash` — the
`gemini-1.5-*` ids have been retired and now return HTTP 404.

`src/main.py` is split into small, composable pieces:

- `build_client()` — constructs the real `genai.Client` (only touched at
  runtime). The model id is *not* bound here; it travels per request.
- `resolve_model_name(model_name)` — picks the model id from the
  argument, then `GEMINI_MODEL`, then the in-code default.
- `start_stream(client, prompt, model_name)` — calls
  `client.models.generate_content_stream(model=..., contents=prompt)` and
  returns the raw iterator of SDK chunk objects.
- `iter_chunks(chunks)` — normalizes SDK chunks into plain strings,
  skipping empty ones. **A chunk's `.text` can legitimately be `None`**
  (the SDK emits metadata-only chunks), so this filter is load-bearing:
  without it, assembling the response would fail on a `None`.
- `consume_stream(chunks, on_chunk)` — drains the text stream, forwarding
  each chunk to a callback (e.g. printing it immediately) and returning
  the fully assembled response for logging.

This separation is what makes the module testable without a real network
stream: tests hand `start_stream` a mock client whose
`.models.generate_content_stream(...)` returns a plain Python iterator of
mock chunks — including a `text=None` chunk — and assert on the assembled
output.

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
