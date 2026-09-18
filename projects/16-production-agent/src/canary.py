"""Canary rollout: routing, the judge, and the controller that acts on it.

The phases run ``CANARY -> (WATCH) -> DONE``:

* **CANARY:** a fixed share of requests goes to the canary version. After
  each canary run, :func:`judge` compares both versions' windows.
* **Ready:** traffic pauses while the controller opens an approval request
  that needs :data:`REQUIRED_APPROVALS` distinct humans. Only ``APPROVED``
  promotes; anything else aborts.
* **WATCH:** the promoted version serves everything for :data:`WATCH_RUNS`
  runs. Its first alert rolls production back automatically, with no
  approval, the same as project 12: a rollback has to work at 3am.

Every stage change goes through the cockpit ``ModelRegistry`` and every
approval through ``ApprovalWorkflow``, so both land on the governance trail.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection
from enum import StrEnum
from typing import Final, NamedTuple

from cockpit.governance.model_versioning import ModelRegistry, ModelStage

from agent import AgentVersion
from alerts import WindowStats

MODEL_NAME: Final = "order-support-agent"
ACTOR: Final = "canary-judge"
DEFAULT_CANARY_PERCENT: Final = 10
MIN_CANARY_RUNS: Final = 10
MIN_STABLE_RUNS: Final = 10
READY_AFTER: Final = 30
FAILURE_MARGIN: Final = 0.05
P95_RATIO: Final = 1.5
COST_RATIO: Final = 1.2
REQUIRED_APPROVALS: Final = 2
WATCH_RUNS: Final = 30


def route(request_id: str, percent: int) -> bool:
    """Whether a request belongs to the canary group.

    Args:
        request_id: Request identifier; the same ID always gets the same answer.
        percent: Share of requests routed to the canary, 0-100.

    Returns:
        True for the canary group.
    """
    return int(hashlib.sha256(request_id.encode()).hexdigest()[:8], 16) % 100 < percent


class Verdict(StrEnum):
    """What the judge decided after a canary run."""

    CONTINUE = "continue"
    ABORT = "abort"
    READY = "ready"


class Judgement(NamedTuple):
    """A verdict and the reason for it."""

    verdict: Verdict
    reason: str


def judge(
    canary: WindowStats,
    stable: WindowStats,
    *,
    canary_total: int,
    canary_firing: Collection[str],
) -> Judgement:
    """Decide whether the canary continues, is aborted, or is ready to promote.

    Args:
        canary: The canary version's window stats.
        stable: The stable version's window stats.
        canary_total: Canary runs so far, across the whole rollout.
        canary_firing: Alert rules currently firing for the canary.

    Returns:
        The judgement. Ratio checks are skipped when the stable value is 0.
    """
    if canary_firing:
        return Judgement(Verdict.ABORT, "alert firing: " + ", ".join(sorted(canary_firing)))
    if canary_total < MIN_CANARY_RUNS:
        return Judgement(Verdict.CONTINUE, f"{canary_total}/{MIN_CANARY_RUNS} canary runs")
    if stable.runs >= MIN_STABLE_RUNS:
        if canary.failure_rate > stable.failure_rate + FAILURE_MARGIN:
            return Judgement(
                Verdict.ABORT,
                f"failure rate {canary.failure_rate:.0%} vs stable {stable.failure_rate:.0%}",
            )
        if stable.p95_s > 0 and canary.p95_s > P95_RATIO * stable.p95_s:
            return Judgement(
                Verdict.ABORT, f"p95 {canary.p95_s:.1f}s vs stable {stable.p95_s:.1f}s"
            )
        if stable.mean_cost > 0 and canary.mean_cost > COST_RATIO * stable.mean_cost:
            return Judgement(
                Verdict.ABORT,
                f"cost/run ${canary.mean_cost:.4f} vs stable ${stable.mean_cost:.4f}",
            )
    if canary_total >= READY_AFTER:
        return Judgement(Verdict.READY, f"{canary_total} canary runs, within margins")
    return Judgement(Verdict.CONTINUE, "within margins")


def register_versions(registry: ModelRegistry, stable: AgentVersion, canary: AgentVersion) -> None:
    """Put ``stable`` in production and ``canary`` in staging.

    Args:
        registry: The registry to populate; must not hold these versions yet.
        stable: Version to serve production.
        canary: Version to trial.
    """
    for version in (stable, canary):
        registry.register(
            MODEL_NAME,
            version.version,
            provider_model_id=version.model,
            config_fingerprint=version.fingerprint(),
            actor_id=ACTOR,
        )
        registry.promote(MODEL_NAME, version.version, ModelStage.STAGING, actor_id=ACTOR)
    registry.promote(MODEL_NAME, stable.version, ModelStage.PRODUCTION, actor_id=ACTOR)
