# Makefile for AI_Engineering_Cockpit.
#
# Thin wrappers around scripts/*.sh and uv commands. On Windows, prefer the
# PowerShell scripts directly (scripts/setup-windows.ps1) or run this Makefile
# under Git Bash / WSL where `make` is available.

.DEFAULT_GOAL := help
.PHONY: help install setup test test-all lint format typecheck security precommit clean

help: ## Show this help message
	@echo "AI_Engineering_Cockpit — available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies (uv sync --all-groups)
	uv sync --all-groups

setup: ## Full environment bootstrap (installer + Ollama on Mac + tests) — macOS/Linux
	bash scripts/setup-mac.sh

test: ## Run the root test suite with coverage
	uv run pytest -c config/pytest.ini

test-all: ## Run root + every projects/*/ test suite with coverage
	bash scripts/run-tests.sh

lint: ## Run ruff checks (no fixes applied)
	uv run ruff check --config config/ruff.toml .

format: ## Auto-format with black + ruff --fix (root + projects/*)
	bash scripts/format-code.sh

typecheck: ## Run mypy against cockpit/
	uv run mypy --config-file config/mypy.ini cockpit

security: ## Run bandit against cockpit/
	uv run bandit -r cockpit -ll

precommit: ## Run all pre-commit hooks against all files
	uv run pre-commit run --all-files -c config/.pre-commit-config.yaml

clean: ## Remove caches, coverage artifacts, and build output
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build dist *.egg-info
	find . -type d -name "__pycache__" -not -path "*/.venv/*" -exec rm -rf {} +
