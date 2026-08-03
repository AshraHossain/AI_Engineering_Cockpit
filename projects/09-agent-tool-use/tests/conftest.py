"""Shared fixtures.

``pythonpath = ["src", "../.."]`` in ``pyproject.toml`` already makes both
this project's ``src/`` and the repo root importable, so nothing here has to
touch ``sys.path``.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable

import pytest
from cockpit.monitoring.performance_metrics import PerformanceTracker

from instrumented import AgentInstrumentation

STEP_SECONDS = 0.5


def make_stepping_clock(step: float = STEP_SECONDS) -> Callable[[], float]:
    """Build a fake monotonic clock that advances a fixed amount per read.

    ``PerformanceTracker.measure`` reads the clock exactly twice, so every
    measured block comes out at exactly ``step`` seconds. That makes latency
    assertions exact without a single ``sleep``.

    Args:
        step: Seconds to advance on each read.

    Returns:
        A zero-argument callable returning monotonically increasing floats.
    """
    counter = itertools.count()
    return lambda: next(counter) * step


@pytest.fixture
def instrumentation() -> AgentInstrumentation:
    """An :class:`AgentInstrumentation` whose timings are deterministic.

    Returns:
        Instrumentation wired to a stepping clock, so each measured block
        records exactly ``STEP_SECONDS``.
    """
    return AgentInstrumentation(
        performance=PerformanceTracker(clock=make_stepping_clock()),
    )
