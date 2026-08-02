"""Shared pytest fixtures for the AI Engineering Cockpit root test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

# Environment variables cockpit/config/settings.py reads (see
# cockpit/config/settings.py's Settings dataclass). Tests that build a
# Settings instance from a known baseline should depend on `clean_env` first
# so a developer's real .env file or shell exports don't leak into
# assertions about defaults or overrides.
SETTINGS_ENV_VARS: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OLLAMA_HOST",
    "COCKPIT_USE_CASE",
    "LOG_LEVEL",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clear all Settings-related environment variables for one test.

    Use together with ``monkeypatch.setenv`` inside the test body to set
    only the variables relevant to that specific test case, without
    interference from a real ``.env`` file or the developer's shell.

    Yields:
        None.
    """
    for var in SETTINGS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield
