.PHONY: help install test lint format typecheck security check

help:
	@echo "install    Install deps (uv sync --all-groups)"
	@echo "test       Run tests with coverage"
	@echo "lint       ruff check"
	@echo "format     black + ruff --fix"
	@echo "typecheck  mypy src"
	@echo "security   bandit -r src"
	@echo "check      Everything CI runs"

install:
	uv sync --all-groups

test:
	uv run pytest --cov --cov-report=term-missing

lint:
	uv run ruff check .

format:
	uv run black .
	uv run ruff check --fix .

typecheck:
	uv run mypy src

security:
	uv run bandit -r src -ll

# Same commands, same order as .github/workflows/ci.yml. Keep them in sync:
# a local check that passes while CI fails is worse than no local check.
check: lint typecheck security test
	uv run black --check .
