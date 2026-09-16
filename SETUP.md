# Setup

Detailed installation instructions for Windows, Mac, and Linux. For the condensed
version, see the [README quick start](README.md#quick-start-5-minutes).

## Prerequisites (All Platforms)

- Git
- Windows: PowerShell (built in)
- Mac: Bash/Zsh (built in)
- Linux: Bash (built in); optional package manager (apt, dnf, pacman, apk)
- No local Python install required — [`uv`](https://docs.astral.sh/uv/)
  manages the Python toolchain itself.

## Platform Support Matrix

| Platform | Setup Script | Ollama Support | Cloud APIs | Status |
|---|---|---|---|---|
| Windows | `setup-windows.ps1` | ❌ Not supported | ✅ Yes | Fully supported |
| Mac (Intel) | `setup-mac.sh` | ✅ CPU-only | ✅ Yes | Fully supported |
| Mac (Apple Silicon) | `setup-mac.sh` | ✅ GPU-accelerated | ✅ Yes | Fully supported |
| Linux (Ubuntu/Debian) | `setup-linux.sh` | ✅ Supported | ✅ Yes | Fully supported |
| Linux (Fedora/RHEL) | `setup-linux.sh` | ✅ Supported | ✅ Yes | Fully supported |
| Linux (Arch) | `setup-linux.sh` | ✅ Supported | ✅ Yes | Fully supported |
| Linux (Alpine) | `setup-linux.sh` | ✅ Supported | ✅ Yes | Fully supported |

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

**Intel iMac**: Installs Ollama with CPU-only inference (1-5 tokens/sec on typical CPU).

**Apple Silicon (M1/M2/M3)**: Installs Ollama with native GPU acceleration (10-50+ tokens/sec).

## Linux (Bash)

```bash
git clone <this-repo-url> AI_Engineering_Cockpit
cd AI_Engineering_Cockpit
bash scripts/setup-linux.sh
```

Same steps as Mac, plus automatic detection of your package manager (apt, dnf,
pacman, apk) and optional Ollama install. Works on Ubuntu, Debian, Fedora,
RHEL, Arch, Alpine, and other distributions.

**Package Manager Detection**:
- **Ubuntu/Debian**: Uses `apt-get`
- **Fedora/RHEL**: Uses `dnf`
- **Arch**: Uses `pacman` (requires `sudo`)
- **Alpine**: Uses `apk`
- **Other**: Falls back to Ollama's official install script or cloud-only mode

**GPU Support** (Linux):
- **NVIDIA GPU**: Ollama detects CUDA automatically (ensure drivers are installed)
- **AMD GPU**: Ollama supports ROCm (requires setup; see `ollama.ai` docs)
- **CPU-only**: Works fine, similar performance to Intel iMac

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
