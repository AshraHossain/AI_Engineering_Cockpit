# 01 - Hello World (Gemini API)

The simplest possible working call to the Gemini API: read a key from the
environment, send one prompt, print the response. This is the "does my key
work and is the SDK wired up correctly" sanity check before building
anything more complex.

Uses the current `google-genai` SDK and the `gemini-2.5-flash` model. (The
older `google-generativeai` package is end-of-life, and the `gemini-1.5-*`
model ids it was usually paired with now return HTTP 404.)

## What this demonstrates

- Loading a secret (`GEMINI_API_KEY`) from a `.env` file via `python-dotenv`
  instead of hardcoding it.
- Constructing an authenticated client with the current `google-genai`
  SDK (`genai.Client(api_key=...)`).
- Sending a single prompt with
  `client.models.generate_content(model=..., contents=...)` and reading
  `.text` off the response.
- Basic, explicit error handling: a clear message when the key is missing,
  and a wrapped error when the API call itself fails.
- Logging via the stdlib `logging` module (no `print()`) so output is
  timestamped and leveled.

## Project layout

```
01-hello-world/
├── pyproject.toml      # isolated deps: google-genai, python-dotenv, pytest
├── .python-version      # 3.11
├── src/main.py           # the example
├── tests/test_main.py    # mocked-SDK tests, no network/API key needed
└── .env.example
```

This project is fully standalone -- it has its own `pyproject.toml` and does
not import anything from the rest of the `AI_Engineering_Cockpit` repo.

## How to run

```bash
cd projects/01-hello-world
uv sync
cp .env.example .env   # then fill in GEMINI_API_KEY
uv run python src/main.py
```

### Expected output

```
2026-08-01 12:00:00,000 | INFO     | main | Prompt: In one sentence, explain what makes the Gemini API useful for developers.
2026-08-01 12:00:00,000 | INFO     | main | Gemini response: <model's answer here>
```

If `GEMINI_API_KEY` is missing, you'll instead see a single error line and
the process exits with code 1:

```
2026-08-01 12:00:00,000 | ERROR    | main | Configuration error: GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.
```

## Running the tests

No API key or network access is required -- the SDK is mocked out.

```bash
cd projects/01-hello-world
uv sync
uv run pytest
```

## Getting a Gemini API key

Create a free key at [Google AI Studio](https://aistudio.google.com/app/apikey).
