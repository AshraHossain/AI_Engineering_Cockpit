#!/usr/bin/env bash
#
# Format and auto-fix the codebase: black + ruff --fix, across the root
# project and every projects/*/ subproject.
#
# Usage: scripts/format-code.sh [--check]
#   --check   Run in check-only mode (no files modified); exits non-zero on
#             what would have changed. Useful in CI.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CHECK_ONLY=false
if [ "${1:-}" = "--check" ]; then
    CHECK_ONLY=true
fi

format_dir() {
    local label="$1"
    local dir="$2"
    shift 2
    # Remaining args are extra ruff flags specific to this suite (the root
    # suite's ruff.toml lives at config/ruff.toml, a non-standard location
    # ruff won't auto-discover, so it needs an explicit --config; projects
    # have no config of their own and correctly use ruff's defaults).
    local -a ruff_config_args=("$@")

    if [ ! -f "$dir/pyproject.toml" ]; then
        echo "Skipping $label — no pyproject.toml found at $dir"
        return 0
    fi

    echo ""
    echo "==> Formatting: $label"

    (
        cd "$dir"
        uv sync --all-groups --quiet

        if [ "$CHECK_ONLY" = true ]; then
            uv run black --line-length 100 --check .
            uv run ruff check "${ruff_config_args[@]}" .
        else
            uv run black --line-length 100 .
            uv run ruff check --fix "${ruff_config_args[@]}" .
        fi
    )
}

format_dir "root (cockpit/, tests/, scripts checked separately)" "$REPO_ROOT" --config config/ruff.toml

if [ -d "$REPO_ROOT/projects" ]; then
    for project_dir in "$REPO_ROOT"/projects/*/; do
        [ -d "$project_dir" ] || continue
        format_dir "projects/$(basename "$project_dir")" "${project_dir%/}"
    done
fi

echo ""
echo "Done."
