#!/usr/bin/env bash
#
# Run the root test suite and every projects/*/ test suite, each with coverage.
#
# Usage: scripts/run-tests.sh [pytest-args...]
# Example: scripts/run-tests.sh -k rag -v

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

EXTRA_ARGS=("$@")
FAILURES=()

run_suite() {
    local label="$1"
    local dir="$2"
    shift 2
    # Remaining args are extra pytest config flags specific to this suite
    # (e.g. the root suite needs -c config/pytest.ini --rootdir=. since its
    # pytest.ini lives outside the repo root; project suites configure
    # themselves via their own pyproject.toml and need none of this).
    local -a config_args=("$@")

    if [ ! -f "$dir/pyproject.toml" ]; then
        echo "Skipping $label — no pyproject.toml found at $dir"
        return 0
    fi

    echo ""
    echo "==================================================================="
    echo "==> Running tests: $label"
    echo "==================================================================="

    (
        cd "$dir"
        uv sync --all-groups --quiet
        # ${arr[@]+"${arr[@]}"} rather than "${arr[@]}": macOS ships bash 3.2, where
        # expanding an empty array under `set -u` aborts with "unbound variable".
        # Project suites pass no config args, and `make test-all` passes no extras.
        uv run pytest ${config_args[@]+"${config_args[@]}"} --cov --cov-report=term-missing ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
    ) || FAILURES+=("$label")
}

# --- Root cockpit test suite -------------------------------------------------------
run_suite "root (cockpit/, tests/)" "$REPO_ROOT" -c config/pytest.ini --rootdir=.

# --- Each example project's own isolated test suite ------------------------------
if [ -d "$REPO_ROOT/projects" ]; then
    for project_dir in "$REPO_ROOT"/projects/*/; do
        [ -d "$project_dir" ] || continue
        project_name="$(basename "$project_dir")"
        # Skip anything this repository ignores, such as a separate repository
        # checked out under projects/ for convenience -- its tests are not ours.
        # Outside a git checkout, check-ignore fails and nothing is skipped.
        if git -C "$REPO_ROOT" check-ignore -q "$project_dir" 2>/dev/null; then
            echo "Skipping projects/$project_name — ignored by this repository"
            continue
        fi
        run_suite "projects/$project_name" "${project_dir%/}"
    done
fi

echo ""
echo "==================================================================="
if [ ${#FAILURES[@]} -eq 0 ]; then
    echo "All test suites passed."
    exit 0
else
    echo "The following suites FAILED:"
    for f in "${FAILURES[@]}"; do
        echo "  - $f"
    done
    exit 1
fi
