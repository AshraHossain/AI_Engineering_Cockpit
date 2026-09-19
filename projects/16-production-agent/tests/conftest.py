"""Shared fixtures.

``pythonpath = ["src", "../.."]`` in ``pyproject.toml`` makes both this
project's ``src/`` and the repo root importable. The cockpit registry and
approval workflow always write to the process-wide default governance trail,
so every test starts and ends with that trail empty, as in project 12.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from cockpit.governance.audit_trail import get_default_trail
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer

from agent import RunRecord
from fake_model import FakeClock
from tools import use_support_api
from tracing import build_tracer_provider


@pytest.fixture(autouse=True)
def _fresh_governance_trail() -> Iterator[None]:
    trail = get_default_trail()
    trail.reset()
    yield
    trail.reset()


@pytest.fixture(autouse=True)
def _sample_data_tools() -> Iterator[None]:
    """Put the tools back on the sample data, so no test leaks a support API."""
    yield
    use_support_api(None)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def tracer(exporter: InMemorySpanExporter) -> Tracer:
    return build_tracer_provider(exporter, batch=False).get_tracer("tests")


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
