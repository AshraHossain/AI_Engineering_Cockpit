# 05 · Hybrid Orchestrator (Local Ollama + Cloud Gemini)

A hybrid router that prefers a local Ollama model when one is available
and falls back to cloud Gemini otherwise — with the fallback being the
*normal*, expected path on platforms where Ollama isn't installed.

## Mac vs. Windows

- **Mac**: Ollama ships a native background service. If it's installed
  and running, this project routes short/simple prompts to it (free,
  private, no network round trip) and sends longer/complex prompts to
  Gemini in the cloud.
- **Windows**: Ollama has no first-class native Windows service in this
  setup (this repo treats it as Mac-only local inference). The
  availability probe in `src/local_ollama.py` always fails fast on a
  connection error, so every prompt transparently routes to cloud Gemini.
  There's no separate "Windows mode" — it's the same code path, just with
  `is_available()` returning `False`.

This means the exact same `HybridRouter` runs unmodified on both
platforms; behavior differs only because of what's actually reachable at
`OLLAMA_HOST`.

## Cloud backend SDK

`src/cloud_gemini.py` uses the current **`google-genai`** SDK: one
`genai.Client` is built at construction time, and the model id is passed
per request via `client.models.generate_content(model=..., contents=...)`.
The legacy `google-generativeai` package (`genai.configure()` +
`GenerativeModel`) is end-of-life and is not used. Default model:
`gemini-2.5-flash` — the `gemini-1.5-*` ids have been retired and now
return HTTP 404.

## How routing decides

`src/hybrid_router.py` implements this policy, in order:

1. **Availability first.** `LocalOllamaClient.is_available()` does a
   short-timeout `GET /api/tags` against `OLLAMA_HOST`. Any failure
   (connection refused, DNS failure, timeout) is treated as "local is not
   available" — not as an error to propagate. If local isn't available,
   every prompt routes straight to cloud.
2. **Complexity heuristic, when local is available.** Prompts at or under
   `HYBRID_LOCAL_MAX_PROMPT_CHARS` (default 400) route to local, on the
   assumption that a small local model handles short/simple prompts fine;
   longer prompts route to cloud.
3. **Runtime fallback.** Even if local was chosen and passed the
   availability check, if the actual generation call still fails (model
   not pulled, Ollama crashed between the check and the call, etc.), the
   router catches `OllamaUnavailableError` and retries the same prompt
   against cloud rather than raising to the caller.

Every call returns a `HybridResult` recording which backend actually
answered (`backend_used`), whether a fallback occurred
(`fallback_occurred`), and why (`reason`) — useful for logging/debugging
which path a given request took.

## Fallback is graceful, not exceptional

The interesting design point of this project: **an unreachable Ollama
never crashes the program.** `LocalOllamaClient` converts every
connection failure into `False` (from `is_available()`) or a caught
`OllamaUnavailableError` (from `generate()`), and `HybridRouter` treats
both as "use cloud" rather than letting the exception propagate. The only
way `HybridRouter.generate()` raises is if the *cloud* backend also
fails — at that point there's genuinely nowhere left to fall back to.

## Usage

```bash
cp .env.example .env
# fill in GEMINI_API_KEY
# OLLAMA_HOST defaults to http://localhost:11434 (Mac only; harmless if unset elsewhere)

uv sync
uv run python src/main.py "What's a good default HTTP timeout?"
```

On Windows (or any machine without Ollama running), you'll see a log line
like `backend=cloud fallback=True reason=ollama_unavailable` — that's
expected, not an error.

## Tests

```bash
uv run pytest
```

`LocalOllamaClient` is tested with a mocked `requests` session (no real
HTTP calls), and `HybridRouter` is tested with mock local/cloud clients.
Dedicated tests cover the Ollama-unavailable fallback path specifically,
plus the mid-generation-failure fallback and the pure complexity-based
routing case — no live API keys or network access required.
