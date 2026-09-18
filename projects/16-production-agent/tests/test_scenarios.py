"""The scripted support policy and both dry-run scenarios, end to end."""

from __future__ import annotations

from typing import Any

from cockpit.governance.approval_workflow import ApprovalWorkflow
from cockpit.governance.audit_trail import verify_trail_integrity
from cockpit.governance.model_versioning import ModelRegistry, ModelStage
from cockpit.monitoring.cost_tracking import CostTracker
from cockpit.monitoring.performance_metrics import PerformanceTracker
from opentelemetry.trace import Tracer

from agent import CANARY, STABLE, AgentVersion, RunRecord, run_agent
from alerts import AlertMonitor
from canary import (
    DEFAULT_CANARY_PERCENT,
    MODEL_NAME,
    READY_AFTER,
    Phase,
    RolloutController,
    register_versions,
    route,
)
from fake_model import FakeClock, scripted_client, text, tool_call
from scenarios import SCENARIOS, Scenario, support_policy
from tools import track_shipment


def conversation(
    question: str, *steps: tuple[str, dict[str, Any], str, bool]
) -> list[dict[str, Any]]:
    """Build a request's messages from completed tool calls."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    for n, (name, tool_input, result, is_error) in enumerate(steps):
        messages.append(
            {"role": "assistant", "content": [{**tool_call(name, **tool_input), "id": f"t{n}"}]}
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"t{n}",
                        "content": result,
                        "is_error": is_error,
                    }
                ],
            }
        )
    return messages


# ------------------------------------------------------------------- policy


def test_the_first_turn_normalizes_a_bare_order_number() -> None:
    turn = support_policy()(conversation("Where is my order 10042?"))
    assert turn.content[-1] == tool_call("lookup_order", order_id="ORD-10042")


def test_without_normalizing_the_bare_number_goes_straight_through() -> None:
    turn = support_policy(normalize_ids=False)(conversation("Where is my order 10042?"))
    assert turn.content[-1] == tool_call("lookup_order", order_id="10042")


def test_a_stuck_shipment_is_reported_or_rechecked() -> None:
    messages = conversation(
        "Where is my order ORD-10044?",
        ("track_shipment", {"tracking_id": "TRK-5503"}, track_shipment("TRK-5503"), False),
    )
    assert support_policy()(messages).stop_reason == "end_turn"
    assert support_policy(recheck_stuck=True)(messages).content[-1] == tool_call(
        "track_shipment", tracking_id="TRK-5503"
    )


def test_a_tool_error_ends_with_an_apology() -> None:
    messages = conversation(
        "Where is 10042?", ("lookup_order", {"order_id": "10042"}, "Invalid order ID", True)
    )
    assert support_policy()(messages).content == [
        text("Sorry, I couldn't look that up: Invalid order ID")
    ]


# ---------------------------------------------------------------- scenarios


def play(scenario: Scenario, clock: FakeClock, tracer: Tracer) -> RolloutController:
    client = scripted_client(scenario.profiles, clock)
    perf, costs = PerformanceTracker(clock=clock), CostTracker()
    registry = ModelRegistry()
    register_versions(registry, STABLE, CANARY)

    def run(version: AgentVersion, question: str, request_id: str, group: str) -> RunRecord:
        return run_agent(
            client,
            version,
            question,
            request_id=request_id,
            group=group,
            tracer=tracer,
            perf=perf,
            costs=costs,
            clock=clock,
        )

    ctl = RolloutController(
        registry=registry,
        workflow=ApprovalWorkflow(),
        stable=STABLE,
        canary=CANARY,
        run=run,
        approve=scenario.approve,
        tracer=tracer,
        monitor=AlertMonitor(),
        perf=perf,
        clock=clock,
    )
    for request_id, question in scenario.requests:
        ctl.handle(request_id, question)
    return ctl


def test_bad_canary_is_aborted_by_the_loop_alert(clock: FakeClock, tracer: Tracer) -> None:
    ctl = play(SCENARIOS["bad-canary"], clock, tracer)
    assert ctl.timeline == [
        ("req-0001", "canary started: v2 on 10% of traffic"),
        ("req-0062", "alert fired: loop_detected for v2 (1)"),
        ("req-0062", "canary aborted (alert firing: loop_detected): v2 back to development"),
    ]
    assert ctl.phase is Phase.DONE
    assert ctl.registry.get_production(MODEL_NAME).version == "v1"
    assert ctl.registry.get(MODEL_NAME, "v2").stage is ModelStage.DEVELOPMENT
    assert verify_trail_integrity()


def test_late_regression_is_promoted_then_rolled_back(clock: FakeClock, tracer: Tracer) -> None:
    ctl = play(SCENARIOS["late-regression"], clock, tracer)
    assert ctl.timeline == [
        ("req-0001", "canary started: v2 on 10% of traffic"),
        ("req-0323", "canary ready (30 canary runs, within margins); approval requested"),
        ("req-0323", "alice approved"),
        ("req-0323", "bob approved"),
        ("req-0323", "promoted: v2 to production; watching 30 runs"),
        ("req-0338", "alert fired: tool_error_rate for v2 (0.333)"),
        ("req-0338", "rolled back (tool_error_rate): v1 back in production"),
    ]
    assert ctl.registry.get_production(MODEL_NAME).version == "v1"
    assert ctl.registry.get(MODEL_NAME, "v2").stage is ModelStage.STAGING
    assert verify_trail_integrity()


def test_legacy_order_numbers_appear_only_after_the_canary_is_ready() -> None:
    requests = SCENARIOS["late-regression"].requests
    canary_runs = 0
    for request_id, question in requests:
        assert "ORD-" in question, f"{request_id} carries a legacy number before promotion"
        canary_runs += route(request_id, DEFAULT_CANARY_PERCENT)
        if canary_runs == READY_AFTER:
            break
    assert canary_runs == READY_AFTER
