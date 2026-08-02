#!/usr/bin/env bash
#
# Scaffold a new example project under projects/NN-name/.
#
# Follows the pattern of the existing example projects (01-hello-world,
# 02-rag-chatbot, ...): isolated pyproject.toml, src/main.py, tests/, and a
# .env.example. Idempotent — re-running with the same name will not overwrite
# existing files.
#
# Usage:
#   scripts/new-project.sh <name> [number]
#
# Examples:
#   scripts/new-project.sh function-calling
#   scripts/new-project.sh function-calling 06

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECTS_DIR="$REPO_ROOT/projects"

usage() {
    echo "Usage: $0 <project-name> [number]" >&2
    echo "  project-name : lowercase, hyphen-separated (e.g. function-calling)" >&2
    echo "  number       : optional 2-digit prefix (e.g. 06). Auto-detected if omitted." >&2
    exit 1
}

if [ $# -lt 1 ]; then
    usage
fi

RAW_NAME="$1"
# Normalize: lowercase, spaces/underscores -> hyphens, strip anything not [a-z0-9-].
NAME="$(echo "$RAW_NAME" | tr '[:upper:]' '[:lower:]' | tr ' _' '-' | tr -cd 'a-z0-9-')"
if [ -z "$NAME" ]; then
    echo "Error: project name resolved to empty string after normalization." >&2
    exit 1
fi

if [ $# -ge 2 ]; then
    NUMBER="$2"
else
    mkdir -p "$PROJECTS_DIR"
    LAST=$(find "$PROJECTS_DIR" -maxdepth 1 -mindepth 1 -type d -name '[0-9][0-9]-*' -printf '%f\n' 2>/dev/null \
        | sed -E 's/^([0-9]{2})-.*/\1/' | sort -n | tail -1)
    if [ -z "${LAST:-}" ]; then
        NUMBER="01"
    else
        NUMBER=$(printf '%02d' $((10#$LAST + 1)))
    fi
fi

PROJECT_SLUG="${NUMBER}-${NAME}"
PROJECT_DIR="$PROJECTS_DIR/$PROJECT_SLUG"

if [ -d "$PROJECT_DIR" ]; then
    echo "Project directory already exists: $PROJECT_DIR"
    echo "Nothing to do (idempotent) — remove it first if you want to regenerate."
    exit 0
fi

echo "==> Scaffolding new project: $PROJECT_SLUG"
mkdir -p "$PROJECT_DIR/src" "$PROJECT_DIR/tests"

MODULE_NAME="$(echo "$NAME" | tr '-' '_')"

cat > "$PROJECT_DIR/pyproject.toml" <<EOF
[project]
name = "${PROJECT_SLUG}"
version = "0.1.0"
description = "TODO: describe what this example demonstrates."
authors = [{ name = "Ash", email = "ashrafuzzmanhossain@gmail.com" }]
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
dependencies = [
    "python-dotenv>=1.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
]

[tool.uv]
package = false
EOF

echo "3.11" > "$PROJECT_DIR/.python-version"

cat > "$PROJECT_DIR/.env.example" <<'EOF'
# Copy this file to .env and fill in your keys. Never commit .env.
GEMINI_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
EOF

cat > "$PROJECT_DIR/README.md" <<EOF
# ${PROJECT_SLUG}

TODO: one or two paragraphs explaining the concept this project demonstrates
and why it matters.

## Run it

\`\`\`bash
cd projects/${PROJECT_SLUG}
uv sync --all-groups
cp .env.example .env   # then fill in your API key(s)
uv run python src/main.py
\`\`\`

## Test it

\`\`\`bash
uv run pytest
\`\`\`
EOF

cat > "$PROJECT_DIR/src/main.py" <<EOF
"""${PROJECT_SLUG}: TODO one-line summary.

TODO: expand with a short module-level description of what this example
demonstrates and how to run it (see README.md for full instructions).
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def main() -> None:
    """Entry point for the ${PROJECT_SLUG} example.

    Raises:
        RuntimeError: If required environment variables are missing.
    """
    load_dotenv()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

    logger.info("TODO: implement ${PROJECT_SLUG}")


if __name__ == "__main__":
    main()
EOF

cat > "$PROJECT_DIR/tests/test_main.py" <<EOF
"""Tests for ${PROJECT_SLUG}."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import main as project_main  # noqa: E402


def test_main_runs_without_error() -> None:
    """Smoke test: main() should execute without raising."""
    project_main.main()
EOF

touch "$PROJECT_DIR/tests/__init__.py" 2>/dev/null || true

echo "==> Created $PROJECT_DIR"
echo ""
echo "Next steps:"
echo "  cd projects/${PROJECT_SLUG}"
echo "  uv sync --all-groups"
echo "  Fill in src/main.py, README.md, and tests/test_main.py"
