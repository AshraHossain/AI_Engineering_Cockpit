"""Routing, the judge and version registration (Task 9 replaces this file with the full test)."""

from __future__ import annotations

from typing import Any

import pytest
from cockpit.governance.model_versioning import ModelRegistry, ModelStage

from agent import CANARY, STABLE
from alerts import WindowStats
from canary import MODEL_NAME, READY_AFTER, Verdict, judge, register_versions, route


def stats(runs: int = 20, **overrides: Any) -> WindowStats:
    fields: dict[str, Any] = {
        "loops": 0,
        "failure_rate": 0.0,
        "tool_error_rate": 0.0,
        "p95_s": 5.0,
        "mean_cost": 0.04,
    }
    fields.update(overrides)
    return WindowStats(runs=runs, **fields)


# ---------------------------------------------------------------------- route


def test_routing_is_deterministic() -> None:
    assert all(route(f"req-{n:04d}", 10) == route(f"req-{n:04d}", 10) for n in range(200))


def test_about_the_requested_share_goes_to_the_canary() -> None:
    share = sum(route(f"req-{n:04d}", 10) for n in range(1000)) / 1000
    assert 0.05 <= share <= 0.15


@pytest.mark.parametrize(("percent", "expected"), [(0, False), (100, True)])
def test_zero_and_full_rollouts(percent: int, expected: bool) -> None:
    assert {route(f"req-{n}", percent) for n in range(100)} == {expected}


# ---------------------------------------------------------------------- judge


@pytest.mark.parametrize(
    ("canary", "stable", "total", "firing", "verdict"),
    [
        (stats(runs=1), stats(), 1, {"loop_detected"}, Verdict.ABORT),
        (stats(runs=9), stats(), 9, set(), Verdict.CONTINUE),
        (stats(failure_rate=0.11), stats(failure_rate=0.05), 12, set(), Verdict.ABORT),
        (stats(failure_rate=0.10), stats(failure_rate=0.05), 12, set(), Verdict.CONTINUE),
        (stats(p95_s=7.6), stats(p95_s=5.0), 12, set(), Verdict.ABORT),
        (stats(p95_s=7.5), stats(p95_s=5.0), 12, set(), Verdict.CONTINUE),
        (stats(mean_cost=0.049), stats(mean_cost=0.04), 12, set(), Verdict.ABORT),
        (stats(mean_cost=0.048), stats(mean_cost=0.04), 12, set(), Verdict.CONTINUE),
        (
            stats(p95_s=9.0, mean_cost=9.0),
            stats(p95_s=0.0, mean_cost=0.0),
            12,
            set(),
            Verdict.CONTINUE,
        ),
        (stats(failure_rate=0.9), stats(runs=9), 12, set(), Verdict.CONTINUE),
        (stats(), stats(), READY_AFTER, set(), Verdict.READY),
        (stats(), stats(), READY_AFTER - 1, set(), Verdict.CONTINUE),
    ],
    ids=[
        "alert-aborts-at-any-count",
        "below-minimum-runs",
        "failure-margin-exceeded",
        "failure-margin-met",
        "p95-ratio-exceeded",
        "p95-ratio-met",
        "cost-ratio-exceeded",
        "cost-ratio-met",
        "zero-stable-values-skip-ratios",
        "thin-stable-window-skips-comparison",
        "ready",
        "one-short-of-ready",
    ],
)
def test_judge(
    canary: WindowStats, stable: WindowStats, total: int, firing: set[str], verdict: Verdict
) -> None:
    assert judge(canary, stable, canary_total=total, canary_firing=firing).verdict is verdict


def test_an_abort_says_why() -> None:
    reason = judge(stats(runs=1), stats(), canary_total=1, canary_firing={"loop_detected"}).reason
    assert reason == "alert firing: loop_detected"


# ------------------------------------------------------------------ registry


def test_register_versions_puts_stable_in_production_and_canary_in_staging() -> None:
    registry = ModelRegistry()
    register_versions(registry, STABLE, CANARY)
    production = registry.get_production(MODEL_NAME)
    assert (production.version, production.provider_model_id) == ("v1", "claude-opus-5")
    assert production.config_fingerprint == STABLE.fingerprint()
    assert registry.get(MODEL_NAME, "v2").stage is ModelStage.STAGING
