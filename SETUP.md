# Setup

Detailed installation instructions for Windows and Mac. For the condensed
version, see the [README quick start](README.md#quick-start-5-minutes).

## Prerequisites

- Git
- Windows: PowerShell (built in). Mac: Bash/Zsh (built in).
- No local Python install required — [`uv`](https://docs.astral.sh/uv/)
  manages the Python toolchain itself.

## Windows (PowerShell)

```powershell
git clone <this-repo-url> AI_Engineering_Cockpit
cd AI_Engineering_Cockpit
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
```

This script:

1. Installs `uv` if it isn't already on `PATH`.
2. Installs the Python 3.11 toolchain via `uv python install 3.11`.
3. Runs `uv sync --all-groups` to install root dependencies + dev tools.
4. Copies `.env.example` to `.env` if `.env` doesn't already exist.
5. Runs the root test suite (`uv run pytest -c config/pytest.ini --rootdir=.`)
   — this needs no API keys, so a fresh clone should pass immediately.

Safe to re-run any time; every step checks current state before acting.

Windows runs cloud-only (Gemini/OpenAI/Anthropic APIs) — there's no local
Ollama step, since Ollama doesn't run natively on Windows in this setup. See
[docs/WINDOWS_VS_MAC.md](docs/WINDOWS_VS_MAC.md).

## Mac (Bash)

```bash
git clone <this-repo-url> AI_Engineering_Cockpit
cd AI_Engineering_Cockpit
bash scripts/setup-mac.sh
```

Same steps as Windows, plus an optional Ollama install via Homebrew (skipped
gracefully if Homebrew isn't installed — cloud-only mode still works fine).

## Verify your installation

```bash
uv run python -c "import sys; print(sys.version)"   # should print 3.11.x
uv run pytest -c config/pytest.ini --rootdir=. -v    # root suite, no API keys needed
```

Then run your first example:

```bash
cd projects/01-hello-world
cp .env.example .env
# edit .env and add GEMINI_API_KEY=your-key-here
uv sync
uv run python src/main.py
```

## Running everything

```bash
bash scripts/run-tests.sh     # root suite + every projects/0X-*/ suite, with coverage
bash scripts/format-code.sh   # black + ruff --fix, root + every project
make test                     # same as scripts/run-tests.sh via the Makefile
make lint                     # ruff check (no fixes)
make typecheck                # mypy against cockpit/
make security                 # bandit against cockpit/
```

`config/pytest.ini`, `config/ruff.toml`, and `config/mypy.ini` all live under
`config/` rather than the repo root — pytest, ruff, and mypy don't
auto-discover config files there, so commands run outside these scripts need
explicit flags. Copy the invocations above (`-c config/pytest.ini
--rootdir=.` for pytest, `--config config/ruff.toml` for ruff,
`--config-file config/mypy.ini` for mypy) rather than running the bare tool.

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for common issues:
missing API keys, `uv` not found after install, stale/corrupted virtual
environments, and tests failing immediately on a fresh clone.
