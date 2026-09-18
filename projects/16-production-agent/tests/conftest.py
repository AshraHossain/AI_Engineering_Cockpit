"""Shared fixtures.

``pythonpath = ["src", "../.."]`` in ``pyproject.toml`` makes both this
project's ``src/`` and the repo root importable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from agent import RunRecord


@pytest.fixture
def make_record() -> Callable[..., RunRecord]:
    """Build a :class:`RunRecord` with harmless defaults for the fields a test doesn't care about."""

    def make(**overrides: Any) -> RunRecord:
        fields: dict[str, Any] = {
            "request_id": "req-0001",
            "version": "v1",
            "group": "stable",
            "outcome": "ok",
            "duration_s": 2.0,
            "cost_usd": 0.01,
            "tool_calls": 2,
            "tool_errors": 0,
            "input_tokens": 1000,
            "output_tokens": 200,
        }
        fields.update(overrides)
        return RunRecord(**fields)

    return make
