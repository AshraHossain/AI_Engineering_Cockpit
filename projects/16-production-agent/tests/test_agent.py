"""One run through the SDK's real Tool Runner, against scripted HTTP responses."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

import anthropic
import httpx2
import pytest
from cockpit.monitoring.cost_tracking import CostTracker
from cockpit.monitoring.performance_metrics import PerformanceTracker
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode, Tracer

from agent import CANARY, STABLE, AgentVersion, RunRecord, outcome_for, run_agent
from fake_model import (
    DRY_RUN_EPOCH,
    FakeClock,
    ModelProfile,
    Policy,
    Turn,
    scripted_client,
    text,
    tool_call,
    tool_history,
)
from tracing import build_tracer_provider


def where_is_order(messages: list[dict[str, Any]]) -> Turn:
    """lookup_order, then track_shipment, then answer."""
    history = tool_history(messages)
    if not history:
        return Turn(
            [text("Let me check."), tool_call("lookup_order", order_id="ORD-10042")], "tool_use"
        )
    if history[-1][0] == "lookup_order":
        return Turn([tool_call("track_shipment", tracking_id="TRK-5501")], "tool_use")
    return Turn([text("It was delivered on 2026-09-14.")])


def run(
    policy: Policy,
    *,
    clock: FakeClock,
    tracer: Tracer,
    version: AgentVersion = STABLE,
    **kwargs: Any,
) -> tuple[RunRecord, PerformanceTracker, CostTracker]:
    perf, costs = PerformanceTracker(clock=clock), CostTracker()
    client = scripted_client({version.model: ModelProfile(policy, latency_s=2.0)}, clock)
    record = run_agent(
        client,
        version,
        "Where is my order ORD-10042?",
        request_id="req-0001",
        group="stable",
        tracer=tracer,
        perf=perf,
        costs=costs,
        clock=clock,
        **kwargs,
    )
    return record, perf, costs


def by_name(spans: Sequence[ReadableSpan], name: str) -> list[ReadableSpan]:
    return [s for s in spans if s.name == name]


# ------------------------------------------------------------ version identity


def test_a_fingerprint_is_stable_and_covers_the_model() -> None:
    assert STABLE.fingerprint() == AgentVersion("v1", "claude-opus-5").fingerprint()
    assert STABLE.fingerprint() != CANARY.fingerprint()
    assert STABLE.fingerprint() != AgentVersion("v1", "claude-opus-5", effort="high").fingerprint()


@pytest.mark.parametrize(
    ("stop_reason", "outcome"),
    [
        ("end_turn", "ok"),
        ("tool_use", "max_iterations"),
        ("refusal", "refusal"),
        ("max_tokens", "incomplete"),
        ("model_context_window_exceeded", "incomplete"),
        (None, "incomplete"),
    ],
)
def test_outcome_for_each_final_stop_reason(stop_reason: str | None, outcome: str) -> None:
    assert outcome_for(stop_reason) == outcome


# ------------------------------------------------------------------- happy path


def test_a_clean_run_is_recorded_in_full(clock: FakeClock, tracer: Tracer) -> None:
    record, _, costs = run(where_is_order, clock=clock, tracer=tracer)
    # Three turns at 2 s each. Prompt tokens grow 400 per earlier turn:
    # 1200 + 1600 + 2000 in, 3 x 300 out, at $5 / $25 per million.
    assert record == RunRecord(
        request_id="req-0001",
        version="v1",
        group="stable",
        outcome="ok",
        duration_s=6.0,
        cost_usd=pytest.approx(0.0465),
        tool_calls=2,
        tool_errors=0,
        input_tokens=4800,
        output_tokens=900,
        answer="It was delivered on 2026-09-14.",
    )
    assert costs.total_cost() == pytest.approx(0.0465)


def test_every_turn_and_tool_call_gets_a_span(
    clock: FakeClock, tracer: Tracer, exporter: InMemorySpanExporter
) -> None:
    run(where_is_order, clock=clock, tracer=tracer)
    spans = exporter.get_finished_spans()
    assert [s.name for s in spans] == [
        "llm.call",
        "tool.lookup_order",
        "llm.call",
        "tool.track_shipment",
        "llm.call",
    ]

    first_llm = spans[0]
    assert first_llm.attributes["openinference.span.kind"] == "LLM"
    assert first_llm.attributes["llm.model_name"] == "claude-opus-5"
    assert first_llm.attributes["llm.token_count.prompt"] == 1200
    assert first_llm.attributes["llm.token_count.completion"] == 300
    assert first_llm.attributes["llm.stop_reason"] == "tool_use"
    assert (first_llm.start_time, first_llm.end_time) == (
        int(DRY_RUN_EPOCH * 1e9),
        int((DRY_RUN_EPOCH + 2) * 1e9),
    )

    lookup = spans[1]
    assert lookup.attributes["openinference.span.kind"] == "TOOL"
    assert lookup.attributes["tool.name"] == "lookup_order"
    assert json.loads(lookup.attributes["input.value"]) == {"order_id": "ORD-10042"}
    assert json.loads(lookup.attributes["output.value"])["tracking_id"] == "TRK-5501"


def test_spans_nest_under_the_callers_current_span(
    clock: FakeClock, tracer: Tracer, exporter: InMemorySpanExporter
) -> None:
    with tracer.start_as_current_span("agent.run") as root:
        run(where_is_order, clock=clock, tracer=tracer)
    children = [s for s in exporter.get_finished_spans() if s.name != "agent.run"]
    assert {s.parent.span_id for s in children} == {root.get_span_context().span_id}


def test_latency_is_recorded_per_model_version_and_per_tool(
    clock: FakeClock, tracer: Tracer
) -> None:
    _, perf, _ = run(where_is_order, clock=clock, tracer=tracer)
    assert perf.call_count("model.generate[v1]") == 3
    assert perf.summary("model.generate[v1]").p50_seconds == 2.0
    assert perf.call_count("tool.lookup_order") == 1
    assert perf.call_count("tool.track_shipment") == 1


# ---------------------------------------------------------------- failure modes


def test_the_loop_guard_stops_the_run_before_the_third_identical_call(
    clock: FakeClock, tracer: Tracer
) -> None:
    def stuck(messages: list[dict[str, Any]]) -> Turn:
        return Turn([tool_call("track_shipment", tracking_id="TRK-5503")], "tool_use")

    record, _, _ = run(stuck, clock=clock, tracer=tracer)
    assert record.outcome == "loop"
    assert record.loop_reason == "repeat:track_shipment"
    assert record.tool_calls == 2


def test_a_failing_tool_is_counted_and_the_model_sees_the_error(
    clock: FakeClock, tracer: Tracer, exporter: InMemorySpanExporter
) -> None:
    def bare_id(messages: list[dict[str, Any]]) -> Turn:
        history = tool_history(messages)
        if not history:
            return Turn([tool_call("lookup_order", order_id="10042")], "tool_use")
        _, _, result, is_error = history[-1]
        return Turn([text(f"is_error={is_error}: {result}")])

    record, perf, _ = run(bare_id, clock=clock, tracer=tracer)
    assert (record.outcome, record.tool_calls, record.tool_errors) == ("ok", 1, 1)
    assert record.answer.startswith("is_error=True: Invalid order ID '10042'")
    [span] = by_name(exporter.get_finished_spans(), "tool.lookup_order")
    assert span.status.status_code is StatusCode.ERROR
    assert perf.summary("tool.lookup_order").error_count == 1


def test_the_iteration_cap_ends_a_run_with_calls_pending(clock: FakeClock, tracer: Tracer) -> None:
    orders = iter(["ORD-10042", "ORD-10043", "ORD-10044"])

    def keeps_going(messages: list[dict[str, Any]]) -> Turn:
        return Turn([tool_call("lookup_order", order_id=next(orders))], "tool_use")

    record, _, _ = run(keeps_going, clock=clock, tracer=tracer, max_iterations=3)
    assert record.outcome == "max_iterations"


@pytest.mark.parametrize(
    ("stop_reason", "outcome"), [("refusal", "refusal"), ("max_tokens", "incomplete")]
)
def test_other_final_stop_reasons(
    clock: FakeClock, tracer: Tracer, stop_reason: str, outcome: str
) -> None:
    record, _, _ = run(lambda messages: Turn([], stop_reason), clock=clock, tracer=tracer)
    assert record.outcome == outcome


def _error(status: int, kind: str) -> Callable[[list[dict[str, Any]]], httpx2.Response]:
    return lambda messages: httpx2.Response(
        status, json={"type": "error", "error": {"type": kind, "message": kind}}
    )


@pytest.mark.parametrize(
    "policy",
    [_error(429, "rate_limit_error"), _error(500, "api_error"), _error(529, "overloaded_error")],
    ids=["429", "500", "529"],
)
def test_api_failures_become_an_api_error_outcome(
    clock: FakeClock, tracer: Tracer, policy: Policy
) -> None:
    record, _, _ = run(policy, clock=clock, tracer=tracer)
    assert record.outcome == "api_error"


def test_a_connection_failure_is_an_api_error(clock: FakeClock, tracer: Tracer) -> None:
    def unreachable(messages: list[dict[str, Any]]) -> Turn:
        raise httpx2.ConnectError("connection refused")

    record, _, _ = run(unreachable, clock=clock, tracer=tracer)
    assert record.outcome == "api_error"


def test_a_rejected_api_key_propagates(clock: FakeClock, tracer: Tracer) -> None:
    with pytest.raises(anthropic.AuthenticationError):
        run(_error(401, "authentication_error"), clock=clock, tracer=tracer)


class _BrokenExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise RuntimeError("collector unreachable")


def test_a_broken_exporter_never_fails_a_run(clock: FakeClock) -> None:
    tracer = build_tracer_provider(_BrokenExporter(), batch=False).get_tracer("tests")
    record, _, _ = run(where_is_order, clock=clock, tracer=tracer)
    assert record.outcome == "ok"


# ------------------------------------------------------------ request settings


def _capture_request(version: AgentVersion, clock: FakeClock, tracer: Tracer) -> dict[str, Any]:
    bodies: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": version.model,
                "content": [{"type": "text", "text": "Hi."}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    client = anthropic.Anthropic(
        api_key="test",  # pragma: allowlist secret
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handle)),
    )
    run_agent(
        client,
        version,
        "Hello",
        request_id="r",
        group="stable",
        tracer=tracer,
        perf=PerformanceTracker(clock=clock),
        costs=CostTracker(),
        clock=clock,
    )
    [body] = bodies
    return body


def test_both_versions_send_the_same_request_apart_from_the_model(
    clock: FakeClock, tracer: Tracer
) -> None:
    stable = _capture_request(STABLE, clock, tracer)
    canary = _capture_request(CANARY, clock, tracer)
    assert stable.pop("model") == "claude-opus-5"
    assert canary.pop("model") == "claude-sonnet-5"
    assert stable == canary
    assert stable["thinking"] == {"type": "adaptive"}
    assert stable["output_config"] == {"effort": "medium"}
    assert stable["max_tokens"] == 16000
    assert [tool["name"] for tool in stable["tools"]] == [
        "lookup_order",
        "track_shipment",
        "refund_policy",
    ]
