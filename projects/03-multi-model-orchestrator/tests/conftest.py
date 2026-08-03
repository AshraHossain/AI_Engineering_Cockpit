"""Test configuration: make ``src/`` importable without packaging the project."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture(autouse=True)
def _clear_model_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Unset model-id env vars so tests see the in-code defaults.

    Without this, a developer with ``GEMINI_MODEL``/``OPENAI_MODEL``
    exported in their shell would get different assertions than CI.

    Yields:
        ``None``; the environment is restored by ``monkeypatch`` teardown.
    """
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    yield
