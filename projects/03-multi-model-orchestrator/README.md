# 03 · Multi-Model Orchestrator

Run the same prompt against **Gemini** and **OpenAI** side by side, and
compare latency and response text.

## Why compare models on the same prompt?

No single model is uniformly best. Latency, tone, factuality, and cost
all vary by provider and by task. Before committing a product to one
vendor, it's worth running a small orchestrator like this one to see:

- **Latency** — which provider responds faster for your typical prompt
  length?
- **Quality trade-offs** — does one model hedge more, refuse more, or
  answer more concisely for your use case?
- **Resilience** — if one provider is down or rate-limited, can you fail
  over to the other without redesigning your integration?

This pattern is also the seed of more advanced routing: once you can run
two clients through one consistent interface, you can add cost-based
routing, A/B testing, or ensemble voting on top without touching the
client code itself.

## How it works

- `src/gemini_client.py` and `src/openai_client.py` are thin wrappers
  around each vendor's SDK. Both expose the same interface:
  `generate(prompt: str) -> str`. This is what makes them interchangeable
  to the orchestrator.
- Gemini goes through the current **`google-genai`** SDK: one
  `genai.Client` is built at construction time, and the model id is
  passed per request via
  `client.models.generate_content(model=..., contents=...)`. The legacy
  `google-generativeai` package (`genai.configure()` + `GenerativeModel`)
  is end-of-life and is not used here. Default model: `gemini-2.5-flash`
  — the `gemini-1.5-*` ids have been retired and now return HTTP 404.
- `src/orchestrator.py` takes a dict of `{name: client}`, runs `.generate()`
  on each one, and records latency and success/failure independently — one
  provider failing doesn't stop the others.
- `src/main.py` wires it together as a CLI: loads `.env`, builds whichever
  clients have API keys available, runs the prompt, and prints a
  comparison report with the fastest successful response called out.

## Usage

```bash
cp .env.example .env
# fill in GEMINI_API_KEY and/or OPENAI_API_KEY

uv sync
uv run python src/main.py "Explain the CAP theorem in two sentences."

# Compare only one provider:
uv run python src/main.py "..." --no-openai
uv run python src/main.py "..." --no-gemini
```

If a key is missing, that provider is skipped with a warning rather than
crashing the whole run — useful when you only have one API key on hand.

## Tests

```bash
uv run pytest
```

Both SDK clients are mocked (`unittest.mock.MagicMock`), so tests exercise
the orchestration, timing-capture, and error-isolation logic without any
network calls or live API keys.
