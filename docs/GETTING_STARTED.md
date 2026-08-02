# Getting Started

Five minutes from a fresh clone to your first working example.

## 1. Clone the repo

```bash
git clone <your-fork-or-repo-url> AI_Engineering_Cockpit
cd AI_Engineering_Cockpit
```

## 2. Run the setup script

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
```

**Mac (Bash):**

```bash
bash scripts/setup-mac.sh
```

Either script will:

1. Install [`uv`](https://docs.astral.sh/uv/) if it isn't already on your `PATH`.
2. Install the pinned Python toolchain (3.11+).
3. Run `uv sync --all-groups` to create `.venv` and install dependencies.
4. Copy `.env.example` to `.env` if you don't already have one.
5. Run the test suite as a sanity check.

Both scripts are idempotent — re-run them any time without side effects.

## 3. Add your API keys

Open the `.env` file created in step 2 and fill in whichever providers you
plan to use:

```bash
GEMINI_API_KEY=your-key-here
OPENAI_API_KEY=your-key-here
ANTHROPIC_API_KEY=your-key-here
```

You only need a key for the provider(s) the example you're running actually
calls — `01-hello-world` only needs `GEMINI_API_KEY`, for instance. See each
project's own `README.md` for specifics.

## 4. Verify your environment

```bash
uv run python -c "import sys; print(sys.version)"
uv run pytest -c config/pytest.ini --rootdir=. -v
```

Both commands should complete without errors. If `uv run pytest` reports
failures around missing API keys, that's expected until step 3 is done —
platform-level tests (`tests/unit/test_cockpit.py`) don't need any keys and
should always pass.

## 5. Run your first example

Every example under `projects/` is self-contained with its own dependencies:

```bash
cd projects/01-hello-world
uv sync
uv run python src/main.py
```

You should see a response printed from the Gemini API. From here:

- Read `projects/01-hello-world/README.md` to see how it works.
- Move on to `projects/02-rag-chatbot` for a minimal retrieval-augmented
  generation example, or jump straight to the [recipes](recipes/) for
  focused code patterns.

## Next steps

- [ARCHITECTURE.md](ARCHITECTURE.md) — how `cockpit/` and `projects/` relate
- [WINDOWS_VS_MAC.md](WINDOWS_VS_MAC.md) — if you're on a Mac and want local
  models via Ollama
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — if anything in this guide didn't
  work as described
