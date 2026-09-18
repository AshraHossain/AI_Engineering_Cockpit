"""The one test that calls the real API. Skipped unless ANTHROPIC_API_KEY is set."""

from __future__ import annotations

import os
import time

import anthropic
import pytest
from cockpit.monitoring.cost_tracking import CostTracker
from cockpit.monitoring.performance_metrics import PerformanceTracker
from opentelemetry.trace import Tracer

from agent import CANARY, run_agent


@pytest.mark.live_provider
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_a_real_run_through_the_tool_runner(tracer: Tracer) -> None:
    record = run_agent(
        anthropic.Anthropic(),
        CANARY,
        "Where is my order ORD-10042?",
        request_id="live-0001",
        group="canary",
        tracer=tracer,
        perf=PerformanceTracker(),
        costs=CostTracker(),
        clock=time.time,
    )
    assert record.outcome == "ok"
    assert record.tool_calls >= 2
    assert "deliver" in record.answer.lower()
