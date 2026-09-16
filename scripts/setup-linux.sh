#!/usr/bin/env bash
#
# Bootstrap AI_Engineering_Cockpit on Linux.
#
# Installs uv (if missing), optionally installs Ollama (skipped gracefully if
# package manager or curl isn't present), syncs all dependency groups, and runs
# the test suite. Safe to re-run at any time.
#
# Supports: Ubuntu/Debian (apt), Fedora/RHEL (dnf), Arch (pacman), Alpine (apk).
# Falls back to cloud-only mode if Ollama install fails.
#
# Usage: bash scripts/setup-linux.sh

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
else
    # Detect package manager and attempt install
    if command -v apt-get >/dev/null 2>&1; then
        warn "Ollama not found. Installing via apt..."
        # Ollama on Linux requires download from ollama.com or compilation
        # Most distributions don't have Ollama in their default repos yet
        if curl -fsSL https://ollama.ai/install.sh | sh 2>/dev/null; then
            ok "Ollama installed"
        else
            warn "Ollama install failed — continuing without local models."
            warn "Install manually from https://ollama.ai if you want local inference."
        fi
    elif command -v dnf >/dev/null 2>&1; then
        warn "Ollama not found. Installing via dnf..."
        if sudo dnf install -y ollama 2>/dev/null || \
           (curl -fsSL https://ollama.ai/install.sh | sh 2>/dev/null); then
            ok "Ollama installed"
        else
            warn "Ollama install failed — continuing without local models."
            warn "Install manually from https://ollama.ai if you want local inference."
        fi
    elif command -v pacman >/dev/null 2>&1; then
        warn "Ollama not found. Installing via pacman..."
        if sudo pacman -S --noconfirm ollama 2>/dev/null || \
           (curl -fsSL https://ollama.ai/install.sh | sh 2>/dev/null); then
            ok "Ollama installed"
        else
            warn "Ollama install failed — continuing without local models."
            warn "Install manually from https://ollama.ai if you want local inference."
        fi
    elif command -v apk >/dev/null 2>&1; then
        warn "Ollama not found. Installing via apk..."
        if sudo apk add ollama 2>/dev/null || \
           (curl -fsSL https://ollama.ai/install.sh | sh 2>/dev/null); then
            ok "Ollama installed"
        else
            warn "Ollama install failed — continuing without local models."
            warn "Install manually from https://ollama.ai if you want local inference."
        fi
    else
        warn "No package manager found (apt, dnf, pacman, apk). Skipping Ollama install."
        warn "Cloud-only mode is fine; install Ollama manually from https://ollama.ai for local models."
    fi
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

Linux-specific notes:
  • Ollama on Linux may require elevated permissions (sudo) for installation
  • If Ollama install fails, you can still run cloud-only mode (Gemini/OpenAI/Anthropic)
  • For GPU acceleration, ensure your NVIDIA drivers are installed (if using NVIDIA GPU)
EOF
