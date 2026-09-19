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

Only alerts that blame the version (``AlertRule.blames_version``) abort or roll
back. A support-API outage fires ``service_error_rate`` instead. That alert
never aborts, but while it fires, the canary is not declared ready and the
watch does not pass: runs during an outage are no evidence either way.

Every stage change goes through the cockpit ``ModelRegistry`` and every
approval through ``ApprovalWorkflow``, so both land on the governance trail.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, NamedTuple

from cockpit.governance.approval_workflow import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalWorkflow,
)
from cockpit.governance.model_versioning import ModelRegistry, ModelStage
from cockpit.monitoring.performance_metrics import CallOutcome, PerformanceTracker
from cockpit.security.output_security import mask_pii
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from agent import AgentVersion, RunRecord
from alerts import AlertMonitor, RunWindow, WindowStats
from tracing import to_ns

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
    canary_held: Collection[str] = (),
) -> Judgement:
    """Decide whether the canary continues, is aborted, or is ready to promote.

    Args:
        canary: The canary version's window stats.
        stable: The stable version's window stats.
        canary_total: Canary runs so far, across the whole rollout.
        canary_firing: Alert rules currently firing that blame the canary.
        canary_held: Alert rules currently firing that don't blame it, such as
            a support-API outage. They never abort; they keep a ready canary
            waiting until they resolve.

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
        if canary_held:
            return Judgement(
                Verdict.CONTINUE, "ready but holding: " + ", ".join(sorted(canary_held))
            )
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


class Phase(StrEnum):
    """Rollout phase."""

    CANARY = "canary"
    WATCH = "watch"
    DONE = "done"


RunFn = Callable[[AgentVersion, str, str, str], RunRecord]
"""``(version, question, request_id, group) -> RunRecord``."""

ApproveFn = Callable[[ApprovalWorkflow, ApprovalRequest], None]
"""Collects decisions on an open request; returns when it has no more to give."""


def _stats_line(label: str, stats: WindowStats) -> str:
    return (
        f"{label}: {stats.runs} recent runs, failure {stats.failure_rate:.0%}, "
        f"p95 {stats.p95_s:.1f}s, cost/run ${stats.mean_cost:.4f}"
    )


@dataclass
class RolloutController:
    """Routes each request, runs it under a root span, and moves the rollout on.

    Attributes:
        registry: Holds both versions; production is read from here.
        workflow: Where promotion approval requests are opened.
        stable: The version in production when the rollout starts.
        canary: The version being trialled.
        run: Runs one request (``run_agent`` with its dependencies bound).
        approve: Collects approval decisions (a prompt, or a script).
        tracer: Tracer for the root ``agent.run`` spans.
        monitor: Alert rules, evaluated after every run.
        perf: Receives ``agent.run[<version>]`` latencies.
        clock: Epoch-seconds clock.
        canary_percent: Share of requests routed to the canary.
        window: Recent runs per version.
        phase: Current phase.
        timeline: ``(request_id, event)`` pairs, in order.
    """

    registry: ModelRegistry
    workflow: ApprovalWorkflow
    stable: AgentVersion
    canary: AgentVersion
    run: RunFn
    approve: ApproveFn
    tracer: Tracer
    monitor: AlertMonitor
    perf: PerformanceTracker
    clock: Callable[[], float]
    canary_percent: int = DEFAULT_CANARY_PERCENT
    window: RunWindow = field(default_factory=RunWindow)
    phase: Phase = Phase.CANARY
    timeline: list[tuple[str, str]] = field(default_factory=list)
    _canary_total: int = 0
    _watch_runs: int = 0

    def handle(self, request_id: str, question: str) -> RunRecord:
        """Serve one request and apply its consequences.

        Args:
            request_id: Request identifier; also the routing key.
            question: The customer's question.

        Returns:
            The run's record.
        """
        use_canary = self.phase is Phase.CANARY and route(request_id, self.canary_percent)
        version = self.canary if use_canary else self._production()
        group = "canary" if use_canary else "stable"
        if not self.timeline:
            self._note(
                request_id,
                f"canary started: {self.canary.version} on {self.canary_percent}% of traffic",
            )

        span = self.tracer.start_span(
            "agent.run",
            start_time=to_ns(self.clock()),
            attributes={
                SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.AGENT.value,
                SpanAttributes.LLM_MODEL_NAME: version.model,
                SpanAttributes.INPUT_VALUE: mask_pii(question),
                "request.id": request_id,
                "canary.group": group,
                "agent.version": version.version,
            },
        )
        with trace.use_span(span, end_on_exit=False):
            record = self.run(version, question, request_id, group)

        self.perf.record(
            f"agent.run[{record.version}]",
            record.duration_s,
            outcome=CallOutcome.SUCCESS if record.outcome == "ok" else CallOutcome.ERROR,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
        )
        stats = self.window.add(record)
        for event in self.monitor.observe(
            record.version, stats, request_id=request_id, timestamp=self.clock()
        ):
            span.add_event(
                f"alert.{event.state}",
                {
                    "rule": event.rule,
                    "version": event.version,
                    "value": event.value,
                    "threshold": event.threshold,
                },
                timestamp=to_ns(event.timestamp),
            )
            self._note(
                request_id,
                f"alert {event.state}: {event.rule} for {event.version} ({event.value:.3g})",
            )
        self._advance(record, span)

        span.set_attribute("agent.outcome", record.outcome)
        span.set_attribute("agent.cost_usd", record.cost_usd)
        span.set_attribute(SpanAttributes.OUTPUT_VALUE, mask_pii(record.answer))
        if record.loop_reason:
            span.set_attribute("agent.loop.reason", record.loop_reason)
        if record.outcome != "ok":
            span.set_status(Status(StatusCode.ERROR, record.outcome))
        span.end(end_time=to_ns(self.clock()))
        return record

    def _production(self) -> AgentVersion:
        serving = self.registry.get_production(MODEL_NAME).version
        return self.canary if serving == self.canary.version else self.stable

    def _note(self, request_id: str, event: str) -> None:
        self.timeline.append((request_id, event))

    def _advance(self, record: RunRecord, span: Span) -> None:
        if self.phase is Phase.CANARY and record.group == "canary":
            self._canary_total += 1
            verdict, reason = judge(
                self.window.stats(self.canary.version),
                self.window.stats(self.stable.version),
                canary_total=self._canary_total,
                canary_firing=self.monitor.blaming(self.canary.version),
                canary_held=self.monitor.firing(self.canary.version)
                - self.monitor.blaming(self.canary.version),
            )
            if verdict is Verdict.ABORT:
                self._abort(record.request_id, reason, span)
            elif verdict is Verdict.READY:
                self._seek_approval(record.request_id, reason, span)
        elif self.phase is Phase.WATCH:
            self._watch_runs += 1
            blaming = self.monitor.blaming(self.canary.version)
            if blaming:
                self._rollback(record.request_id, ", ".join(sorted(blaming)), span)
            elif self._watch_runs >= WATCH_RUNS and not self.monitor.firing(self.canary.version):
                self.phase = Phase.DONE
                self._note(
                    record.request_id, f"watch passed: {self.canary.version} stays in production"
                )

    def _abort(self, request_id: str, reason: str, span: Span) -> None:
        self.registry.promote(
            MODEL_NAME, self.canary.version, ModelStage.DEVELOPMENT, actor_id=ACTOR
        )
        self.phase = Phase.DONE
        span.add_event("canary.aborted", {"reason": reason}, timestamp=to_ns(self.clock()))
        self._note(
            request_id, f"canary aborted ({reason}): {self.canary.version} back to development"
        )

    def _seek_approval(self, request_id: str, reason: str, span: Span) -> None:
        self._note(request_id, f"canary ready ({reason}); approval requested")
        description = "\n".join(
            [
                f"Promote {MODEL_NAME} {self.canary.version} ({self.canary.model}) to production.",
                _stats_line(
                    f"canary {self.canary.version}", self.window.stats(self.canary.version)
                ),
                _stats_line(
                    f"stable {self.stable.version}", self.window.stats(self.stable.version)
                ),
            ]
        )
        request = self.workflow.submit(ACTOR, description, required_approvals=REQUIRED_APPROVALS)
        self.approve(self.workflow, request)
        final = self.workflow.get(request.request_id)
        for decision in final.decisions:
            verb = "approved" if decision.approved else "rejected"
            self._note(request_id, f"{decision.principal_id} {verb}")
        if final.status is ApprovalStatus.APPROVED:
            self.registry.promote(
                MODEL_NAME, self.canary.version, ModelStage.PRODUCTION, actor_id=ACTOR
            )
            self.phase = Phase.WATCH
            span.add_event("canary.promoted", timestamp=to_ns(self.clock()))
            self._note(
                request_id,
                f"promoted: {self.canary.version} to production; watching {WATCH_RUNS} runs",
            )
        else:
            self._abort(request_id, f"approval {final.status}", span)

    def _rollback(self, request_id: str, reason: str, span: Span) -> None:
        restored = self.registry.rollback_production(MODEL_NAME, actor_id=ACTOR)
        self.phase = Phase.DONE
        span.add_event("canary.rolled_back", {"reason": reason}, timestamp=to_ns(self.clock()))
        self._note(request_id, f"rolled back ({reason}): {restored.version} back in production")
