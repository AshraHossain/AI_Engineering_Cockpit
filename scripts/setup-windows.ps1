<#
.SYNOPSIS
    Bootstrap AI_Engineering_Cockpit on Windows.

.DESCRIPTION
    Installs uv (if missing), syncs all dependency groups, and runs the test suite.
    Safe to re-run at any time: every step checks current state before acting.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
#>

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "    $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "    $Message" -ForegroundColor Yellow
}

# --- 1. Ensure uv is installed -------------------------------------------------
Write-Step "Checking for uv"

$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCommand) {
    $uvVersion = (uv --version)
    Write-Ok "uv already installed: $uvVersion"
} else {
    Write-Warn "uv not found. Installing via the official installer..."
    try {
        Invoke-RestMethod -Uri "https://astral.sh/uv/install.ps1" | Invoke-Expression
    } catch {
        Write-Host "Failed to install uv automatically. Install manually: https://docs.astral.sh/uv/getting-started/installation/" -ForegroundColor Red
        throw
    }

    # uv's installer places the binary under %USERPROFILE%\.local\bin (or %USERPROFILE%\.cargo\bin
    # on older installers) — make sure this session's PATH picks it up without requiring a new shell.
    $uvLocalBin = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path $uvLocalBin) {
        $env:Path = "$uvLocalBin;$env:Path"
    }

    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uvCommand) {
        Write-Host "uv was installed but is not on PATH for this session. Restart your terminal and re-run this script." -ForegroundColor Red
        exit 1
    }
    Write-Ok "uv installed: $(uv --version)"
}

# --- 2. Ensure the pinned Python version is available --------------------------
Write-Step "Ensuring Python 3.11+ is available via uv"
uv python install 3.11
Write-Ok "Python 3.11 toolchain ready"

# --- 3. Sync dependencies (root) ------------------------------------------------
Write-Step "Syncing dependencies (uv sync --all-groups)"
uv sync --all-groups
Write-Ok "Dependencies synced"

# --- 4. Set up .env if missing ---------------------------------------------------
Write-Step "Checking for .env"
if (Test-Path (Join-Path $RepoRoot ".env")) {
    Write-Ok ".env already exists — leaving it untouched"
} elseif (Test-Path (Join-Path $RepoRoot ".env.example")) {
    Copy-Item (Join-Path $RepoRoot ".env.example") (Join-Path $RepoRoot ".env")
    Write-Ok "Created .env from .env.example — fill in your API keys before running examples"
} else {
    Write-Warn ".env.example not found — skipping .env creation"
}

# --- 5. Run the test suite --------------------------------------------------------
Write-Step "Running test suite (uv run pytest)"
try {
    uv run pytest -c config/pytest.ini --rootdir=.
    Write-Ok "Tests passed"
} catch {
    Write-Warn "Tests failed or pytest is not yet configured. This is non-fatal during initial setup."
    Write-Warn "Re-run manually with: uv run pytest -c config/pytest.ini --rootdir=. -v"
}

# --- 6. Verification instructions -------------------------------------------------
Write-Step "Setup complete"
Write-Host ""
Write-Host "Verify your environment with:" -ForegroundColor White
Write-Host "  uv run python -c `"import sys; print(sys.version)`"" -ForegroundColor Gray
Write-Host "  uv run pytest -v" -ForegroundColor Gray
Write-Host "  uv run python projects\01-hello-world\src\main.py" -ForegroundColor Gray
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. Edit .env and add your API keys (GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY)" -ForegroundColor Gray
Write-Host "  2. See SETUP.md for the full walkthrough and troubleshooting" -ForegroundColor Gray
Write-Host "  3. Re-run this script any time — it is safe to run repeatedly" -ForegroundColor Gray
Write-Host ""
