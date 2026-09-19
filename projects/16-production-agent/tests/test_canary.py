"""Routing, the judge, and the rollout controller (with a fake run function)."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from cockpit.governance.approval_workflow import ApprovalRequest, ApprovalWorkflow
from cockpit.governance.audit_trail import verify_trail_integrity
from cockpit.governance.model_versioning import ModelRegistry, ModelStage
from cockpit.monitoring.cost_tracking import CostTracker
from cockpit.monitoring.performance_metrics import PerformanceTracker
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode, Tracer

import tools
from agent import CANARY, STABLE, AgentVersion, RunRecord, run_agent
from alerts import AlertMonitor, WindowStats
from canary import (
    MODEL_NAME,
    READY_AFTER,
    WATCH_RUNS,
    Phase,
    RolloutController,
    Verdict,
    judge,
    register_versions,
    route,
)
from fake_model import FakeClock, ModelProfile, scripted_client
from scenarios import support_policy


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


def test_a_held_canary_waits_instead_of_becoming_ready() -> None:
    verdict, reason = judge(
        stats(),
        stats(),
        canary_total=READY_AFTER,
        canary_firing=set(),
        canary_held={"service_error_rate"},
    )
    assert (verdict, reason) == (Verdict.CONTINUE, "ready but holding: service_error_rate")


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


# ---------------------------------------------------------------- controller


class FakeRuns:
    """Stands in for run_agent: returns records whose outcome the test controls."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.outcomes: dict[str, str] = {"v1": "ok", "v2": "ok"}
        self.calls: list[tuple[str, str]] = []

    def __call__(
        self, version: AgentVersion, question: str, request_id: str, group: str
    ) -> RunRecord:
        self.clock.advance(2.0)
        self.calls.append((version.version, group))
        outcome = self.outcomes[version.version]
        return RunRecord(
            request_id=request_id,
            version=version.version,
            group=group,
            outcome=outcome,
            duration_s=2.0,
            cost_usd=0.02,
            tool_calls=2,
            tool_errors=0,
            input_tokens=1000,
            output_tokens=200,
            answer="Delivered.",
            loop_reason="repeat:track_shipment" if outcome == "loop" else None,
        )


def approvals(*decisions: tuple[str, bool]) -> Callable[[ApprovalWorkflow, ApprovalRequest], None]:
    def approve(workflow: ApprovalWorkflow, request: ApprovalRequest) -> None:
        for principal, approved in decisions:
            workflow.decide(request.request_id, approved, principal)

    return approve


APPROVED = approvals(("alice", True), ("bob", True))


def controller(
    clock: FakeClock,
    tracer: Tracer,
    runs: FakeRuns,
    *,
    approve: Callable[[ApprovalWorkflow, ApprovalRequest], None] = APPROVED,
    percent: int = 100,
) -> RolloutController:
    registry = ModelRegistry()
    register_versions(registry, STABLE, CANARY)
    return RolloutController(
        registry=registry,
        workflow=ApprovalWorkflow(),
        stable=STABLE,
        canary=CANARY,
        run=runs,
        approve=approve,
        tracer=tracer,
        monitor=AlertMonitor(),
        perf=PerformanceTracker(clock=clock),
        clock=clock,
        canary_percent=percent,
    )


def serve(ctl: RolloutController, count: int, start: int = 1) -> None:
    for n in range(start, start + count):
        ctl.handle(f"req-{n:04d}", "Where is my order ORD-10042?")


def stage(ctl: RolloutController, version: str) -> ModelStage:
    return ctl.registry.get(MODEL_NAME, version).stage


def test_the_canary_gets_its_share_and_stable_the_rest(clock: FakeClock, tracer: Tracer) -> None:
    runs = FakeRuns(clock)
    ctl = controller(clock, tracer, runs, percent=10)
    serve(ctl, 100)
    groups = [group for _, group in runs.calls]
    assert groups.count("canary") == sum(route(f"req-{n:04d}", 10) for n in range(1, 101))
    assert set(runs.calls) == {("v1", "stable"), ("v2", "canary")}


def test_a_canary_alert_aborts_the_rollout(clock: FakeClock, tracer: Tracer) -> None:
    runs = FakeRuns(clock)
    runs.outcomes["v2"] = "loop"
    ctl = controller(clock, tracer, runs)
    serve(ctl, 3)
    assert ctl.phase is Phase.DONE
    assert stage(ctl, "v2") is ModelStage.DEVELOPMENT
    assert runs.calls == [("v2", "canary"), ("v1", "stable"), ("v1", "stable")]
    assert ctl.timeline[-1] == (
        "req-0001",
        "canary aborted (alert firing: loop_detected): v2 back to development",
    )


def test_ready_asks_two_humans_then_promotes(clock: FakeClock, tracer: Tracer) -> None:
    seen: list[ApprovalRequest] = []

    def approve(workflow: ApprovalWorkflow, request: ApprovalRequest) -> None:
        seen.append(request)
        APPROVED(workflow, request)

    ctl = controller(clock, tracer, FakeRuns(clock), approve=approve)
    serve(ctl, READY_AFTER)
    [request] = seen
    assert request.required_approvals == 2
    assert request.requested_by == "canary-judge"
    assert request.description.startswith(
        "Promote order-support-agent v2 (claude-sonnet-5) to production."
    )
    assert ctl.phase is Phase.WATCH
    assert ctl.registry.get_production(MODEL_NAME).version == "v2"
    assert [event for _, event in ctl.timeline[-3:]] == [
        "alice approved",
        "bob approved",
        "promoted: v2 to production; watching 30 runs",
    ]


@pytest.mark.parametrize(
    "approve",
    [approvals(("alice", True), ("bob", False)), approvals(("alice", True))],
    ids=["rejected", "still-pending"],
)
def test_anything_short_of_approval_aborts(
    clock: FakeClock, tracer: Tracer, approve: Callable[[ApprovalWorkflow, ApprovalRequest], None]
) -> None:
    ctl = controller(clock, tracer, FakeRuns(clock), approve=approve)
    serve(ctl, READY_AFTER)
    assert ctl.phase is Phase.DONE
    assert ctl.registry.get_production(MODEL_NAME).version == "v1"
    assert stage(ctl, "v2") is ModelStage.DEVELOPMENT


def test_an_alert_during_the_watch_rolls_back(clock: FakeClock, tracer: Tracer) -> None:
    runs = FakeRuns(clock)
    ctl = controller(clock, tracer, runs)
    serve(ctl, READY_AFTER)
    runs.outcomes["v2"] = "loop"
    serve(ctl, 1, start=READY_AFTER + 1)
    assert ctl.phase is Phase.DONE
    assert ctl.registry.get_production(MODEL_NAME).version == "v1"
    assert stage(ctl, "v2") is ModelStage.STAGING
    assert ctl.timeline[-1] == ("req-0031", "rolled back (loop_detected): v1 back in production")
    assert verify_trail_integrity()


def test_a_clean_watch_keeps_the_new_version(clock: FakeClock, tracer: Tracer) -> None:
    ctl = controller(clock, tracer, FakeRuns(clock))
    serve(ctl, READY_AFTER + WATCH_RUNS)
    assert ctl.phase is Phase.DONE
    assert ctl.registry.get_production(MODEL_NAME).version == "v2"
    assert ctl.timeline[-1][1] == "watch passed: v2 stays in production"


def test_alerts_after_the_rollout_only_notify(clock: FakeClock, tracer: Tracer) -> None:
    runs = FakeRuns(clock)
    ctl = controller(clock, tracer, runs)
    serve(ctl, READY_AFTER + WATCH_RUNS)
    runs.outcomes["v2"] = "loop"
    serve(ctl, 1, start=READY_AFTER + WATCH_RUNS + 1)
    assert ctl.registry.get_production(MODEL_NAME).version == "v2"
    assert ctl.timeline[-1][1] == "alert fired: loop_detected for v2 (1)"


# ---------------------------------------------------------------- root spans


def test_each_request_gets_a_root_span(
    clock: FakeClock, tracer: Tracer, exporter: InMemorySpanExporter
) -> None:
    ctl = controller(clock, tracer, FakeRuns(clock))
    ctl.handle("req-0001", "Where is ORD-10042? Email me at jane.doe@example.com")
    [span] = exporter.get_finished_spans()
    attributes = span.attributes
    assert span.name == "agent.run"
    assert attributes["openinference.span.kind"] == "AGENT"
    assert attributes["request.id"] == "req-0001"
    assert attributes["canary.group"] == "canary"
    assert attributes["agent.version"] == "v2"
    assert attributes["llm.model_name"] == "claude-sonnet-5"
    assert attributes["agent.outcome"] == "ok"
    assert attributes["agent.cost_usd"] == 0.02
    assert "jane.doe@example.com" not in attributes["input.value"]
    assert "ORD-10042" in attributes["input.value"]
    assert span.status.status_code is not StatusCode.ERROR


def test_a_looping_run_carries_the_reason_the_alert_and_the_abort(
    clock: FakeClock, tracer: Tracer, exporter: InMemorySpanExporter
) -> None:
    runs = FakeRuns(clock)
    runs.outcomes["v2"] = "loop"
    ctl = controller(clock, tracer, runs)
    serve(ctl, 1)
    [span] = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["agent.loop.reason"] == "repeat:track_shipment"
    assert [event.name for event in span.events] == ["alert.fired", "canary.aborted"]
    assert span.events[0].attributes["rule"] == "loop_detected"


def test_run_latency_is_recorded_per_version(clock: FakeClock, tracer: Tracer) -> None:
    ctl = controller(clock, tracer, FakeRuns(clock), percent=10)
    serve(ctl, 20)
    assert ctl.perf.call_count("agent.run[v1]") + ctl.perf.call_count("agent.run[v2]") == 20


# ---------------------------------------------------------------- support API outage


def _outage_controller(
    clock: FakeClock, tracer: Tracer, monkeypatch: pytest.MonkeyPatch
) -> RolloutController:
    """Real run loop and tools, a support API that answers 503, every request to the canary."""
    monkeypatch.setattr(tools, "BACKOFF_S", 0)
    tools.use_support_api(
        tools.SupportAPI(
            "https://support.example.test/v1",
            transport=httpx2.MockTransport(lambda request: httpx2.Response(503)),
        )
    )
    client = scripted_client(
        {
            model: ModelProfile(support_policy(), latency_s=1.0)
            for model in ("claude-opus-5", "claude-sonnet-5")
        },
        clock,
    )

    def run(version: AgentVersion, question: str, request_id: str, group: str) -> RunRecord:
        return run_agent(
            client,
            version,
            question,
            request_id=request_id,
            group=group,
            tracer=tracer,
            perf=PerformanceTracker(clock=clock),
            costs=CostTracker(),
            clock=clock,
        )

    registry = ModelRegistry()
    register_versions(registry, STABLE, CANARY)
    return RolloutController(
        registry=registry,
        workflow=ApprovalWorkflow(),
        stable=STABLE,
        canary=CANARY,
        run=run,
        approve=APPROVED,
        tracer=tracer,
        monitor=AlertMonitor(),
        perf=PerformanceTracker(clock=clock),
        clock=clock,
        canary_percent=100,
    )


def test_an_outage_neither_aborts_nor_promotes_the_canary_until_it_ends(
    clock: FakeClock, tracer: Tracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctl = _outage_controller(clock, tracer, monkeypatch)
    serve(ctl, READY_AFTER + 5)
    assert ctl.monitor.firing("v2") == {"service_error_rate"}
    assert (ctl.phase, stage(ctl, "v2")) == (Phase.CANARY, ModelStage.STAGING)

    tools.use_support_api(None)  # the service recovers
    serve(ctl, 20, start=READY_AFTER + 6)
    assert ctl.monitor.firing("v2") == set()
    assert (ctl.phase, stage(ctl, "v2")) == (Phase.WATCH, ModelStage.PRODUCTION)


def test_an_outage_during_the_watch_neither_rolls_back_nor_passes_it(
    clock: FakeClock, tracer: Tracer
) -> None:
    runs = FakeRuns(clock)
    ctl = controller(clock, tracer, runs)
    serve(ctl, READY_AFTER)
    assert ctl.phase is Phase.WATCH

    def outage(version: AgentVersion, question: str, request_id: str, group: str) -> RunRecord:
        record = runs(version, question, request_id, group)  # every tool call fails at the service
        return dataclasses.replace(record, service_errors=record.tool_calls)

    ctl.run = outage
    serve(ctl, WATCH_RUNS + 5, start=READY_AFTER + 1)
    assert ctl.monitor.blaming("v2") == set()
    assert (ctl.phase, stage(ctl, "v2")) == (Phase.WATCH, ModelStage.PRODUCTION)
