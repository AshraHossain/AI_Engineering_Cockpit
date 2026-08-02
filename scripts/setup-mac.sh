#!/usr/bin/env bash
#
# Bootstrap AI_Engineering_Cockpit on macOS.
#
# Installs uv (if missing), optionally installs Ollama via Homebrew (skipped
# gracefully if Homebrew isn't present), syncs all dependency groups, and runs
# the test suite. Safe to re-run at any time.
#
# Usage: bash scripts/setup-mac.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
ok()   { printf '    \033[1;32m%s\033[0m\n' "$1"; }
warn() { printf '    \033[1;33m%s\033[0m\n' "$1"; }

# --- 1. Ensure uv is installed -------------------------------------------------
step "Checking for uv"
if command -v uv >/dev/null 2>&1; then
    ok "uv already installed: $(uv --version)"
else
    warn "uv not found. Installing via the official installer..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # uv's installer places the binary under ~/.local/bin (or ~/.cargo/bin on
    # older installers) — pick it up for this session without a new shell.
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    if ! command -v uv >/dev/null 2>&1; then
        echo "uv was installed but is not on PATH for this session. Restart your terminal and re-run this script." >&2
        exit 1
    fi
    ok "uv installed: $(uv --version)"
fi

# --- 2. Ensure the pinned Python version is available --------------------------
step "Ensuring Python 3.11+ is available via uv"
uv python install 3.11
ok "Python 3.11 toolchain ready"

# --- 3. Optionally install Ollama for local model inference ---------------------
step "Checking for Ollama (optional, enables local models)"
if command -v ollama >/dev/null 2>&1; then
    ok "Ollama already installed: $(ollama --version 2>/dev/null || echo 'version unknown')"
elif command -v brew >/dev/null 2>&1; then
    warn "Ollama not found. Installing via Homebrew..."
    if brew install --cask ollama; then
        ok "Ollama installed"
    else
        warn "Homebrew install of Ollama failed — continuing without local models."
        warn "Install manually later from https://ollama.com if you want local inference."
    fi
else
    warn "Homebrew not found — skipping Ollama install."
    warn "Cloud-only mode is fine; install Homebrew (https://brew.sh) later for local models."
fi

# --- 4. Sync dependencies (root) ------------------------------------------------
step "Syncing dependencies (uv sync --all-groups)"
uv sync --all-groups
ok "Dependencies synced"

# --- 5. Set up .env if missing ---------------------------------------------------
step "Checking for .env"
if [ -f "$REPO_ROOT/.env" ]; then
    ok ".env already exists — leaving it untouched"
elif [ -f "$REPO_ROOT/.env.example" ]; then
    cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
    ok "Created .env from .env.example — fill in your API keys before running examples"
else
    warn ".env.example not found — skipping .env creation"
fi

# --- 6. Run the test suite --------------------------------------------------------
step "Running test suite (uv run pytest)"
if uv run pytest -c config/pytest.ini --rootdir=.; then
    ok "Tests passed"
else
    warn "Tests failed or pytest is not yet configured. This is non-fatal during initial setup."
    warn "Re-run manually with: uv run pytest -c config/pytest.ini --rootdir=. -v"
fi

# --- 7. Verification instructions -------------------------------------------------
step "Setup complete"
cat <<'EOF'

Verify your environment with:
  uv run python -c "import sys; print(sys.version)"
  uv run pytest -v
  uv run python projects/01-hello-world/src/main.py

Next steps:
  1. Edit .env and add your API keys (GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY)
  2. See SETUP.md for the full walkthrough and troubleshooting
  3. Re-run this script any time — it is safe to run repeatedly
EOF
