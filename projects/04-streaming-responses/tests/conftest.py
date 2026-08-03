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
    """Unset ``GEMINI_MODEL`` so tests see the in-code default.

    Tests that need a specific value set it themselves via
    ``monkeypatch.setenv``; this stops a developer's exported shell
    variable from changing the assertions.

    Yields:
        ``None``; the environment is restored by ``monkeypatch`` teardown.
    """
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    yield
