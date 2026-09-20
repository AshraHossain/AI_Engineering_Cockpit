"""One agent run: the SDK Tool Runner loop, instrumented.

Everything a run produces is measured once, here: an ``llm.call`` span, a
latency sample and a cost entry for every model turn, and a ``tool.<name>``
span and latency sample for every tool call. The loop guard sees each turn's
tool calls *before* the runner executes them; breaking out of the loop at that
point means those tools never run.

The root ``agent.run`` span belongs to the caller (the rollout controller),
which also decides what the run means for alerts and the canary. This module
only reports what happened, as a :class:`RunRecord`.
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

import anthropic
from anthropic import beta_tool
from anthropic.lib.tools import BetaFunctionTool
from anthropic.types.beta import BetaMessage
from cockpit.monitoring.cost_tracking import CostTracker
from cockpit.monitoring.performance_metrics import CallOutcome, PerformanceTracker
from cockpit.security.output_security import mask_pii
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry.trace import Status, StatusCode, Tracer

from alerts import LoopGuard
from tools import TOOL_FUNCTIONS, ServiceError
from tracing import to_ns

PROJECT: Final = "16-production-agent"
MAX_ITERATIONS: Final = 8

SYSTEM_PROMPT: Final = (
    "You are the order-support assistant for an outdoor gear shop. Answer questions "
    "about orders, shipments and returns using the tools, and keep answers short.\n\n"
    "Order IDs have the form ORD-12345. If a customer gives a bare order number such "
    "as 10042, call lookup_order with ORD-10042. If a tool returns an error, tell the "
    "customer plainly what went wrong instead of guessing."
)

KIND: Final = SpanAttributes.OPENINFERENCE_SPAN_KIND


@dataclass(frozen=True)
class AgentVersion:
    """Everything that defines one deployable version of the agent.

    Attributes:
        version: Registry version identifier, e.g. ``"v1"``.
        model: Claude model ID.
        system_prompt: System prompt sent on every request.
        effort: ``output_config.effort`` level.
        max_tokens: Per-response output cap.
    """

    version: str
    model: str
    system_prompt: str = SYSTEM_PROMPT
    effort: str = "medium"
    max_tokens: int = 16000

    def fingerprint(self) -> str:
        """Digest of every setting that shapes this version's requests.

        Returns:
            Hex SHA-256 over model, prompt, thinking mode, effort, output cap
            and tool schemas.
        """
        payload = {
            "model": self.model,
            "system": self.system_prompt,
            "thinking": "adaptive",
            "effort": self.effort,
            "max_tokens": self.max_tokens,
            "tools": [beta_tool(fn).to_dict() for fn in TOOL_FUNCTIONS],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


STABLE: Final = AgentVersion("v1", "claude-opus-5")
CANARY: Final = AgentVersion("v2", "claude-sonnet-5")


@dataclass(frozen=True)
class RunRecord:
    """What one run did, as alerts and the canary judge see it.

    Attributes:
        request_id: Request identifier.
        version: Version that served it.
        group: ``"stable"`` or ``"canary"``.
        outcome: ``ok``, ``loop``, ``max_iterations``, ``refusal``,
            ``incomplete`` or ``api_error``.
        duration_s: Wall time of the whole run, in seconds.
        cost_usd: Cost of every model turn in the run.
        tool_calls: Tool calls executed.
        tool_errors: Tool calls that failed on their input (a bad or unknown
            ID): the version's own mistakes.
        input_tokens: Prompt tokens across all turns.
        output_tokens: Output tokens across all turns.
        answer: Text of the final assistant turn.
        loop_reason: Loop guard verdict when the outcome is ``loop``.
        service_errors: Tool calls that failed because the support service
            did (``tools.ServiceError``). Counted in ``tool_calls``, never in
            ``tool_errors``.
    """

    request_id: str
    version: str
    group: str
    outcome: str
    duration_s: float
    cost_usd: float
    tool_calls: int
    tool_errors: int
    input_tokens: int
    output_tokens: int
    answer: str = ""
    loop_reason: str | None = None
    service_errors: int = 0


def outcome_for(stop_reason: str | None) -> str:
    """Map the final turn's stop reason to a run outcome.

    Args:
        stop_reason: ``stop_reason`` of the last message the runner yielded.

    Returns:
        ``ok`` for ``end_turn``; ``max_iterations`` for ``tool_use`` (the
        runner stopped with tool calls still pending); ``refusal``; otherwise
        ``incomplete``.
    """
    if stop_reason == "end_turn":
        return "ok"
    if stop_reason == "tool_use":
        return "max_iterations"
    if stop_reason == "refusal":
        return "refusal"
    return "incomplete"


@dataclass
class _Run:
    """Per-run state shared between the loop and the instrumented tools."""

    tracer: Tracer
    clock: Callable[[], float]
    perf: PerformanceTracker
    mark: float
    tool_calls: int = 0
    tool_errors: int = 0
    service_errors: int = 0

    def call_tool(self, fn: Callable[..., str], kwargs: dict[str, Any]) -> str:
        """Run one tool inside a span, counting calls, errors and latency.

        Exceptions are re-raised: the Tool Runner turns them into
        ``is_error`` tool results.
        """
        name = fn.__name__
        start = self.clock()
        span = self.tracer.start_span(
            f"tool.{name}",
            start_time=to_ns(start),
            attributes={
                KIND: OpenInferenceSpanKindValues.TOOL.value,
                SpanAttributes.TOOL_NAME: name,
                SpanAttributes.INPUT_VALUE: mask_pii(json.dumps(kwargs)),
            },
        )
        self.tool_calls += 1
        outcome = CallOutcome.SUCCESS
        try:
            result = fn(**kwargs)
        except Exception as exc:
            outcome = CallOutcome.ERROR
            if isinstance(exc, ServiceError):
                self.service_errors += 1
                span.set_attribute("tool.error.source", "service")
            else:
                self.tool_errors += 1
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            span.set_attribute(SpanAttributes.OUTPUT_VALUE, mask_pii(result))
            return result
        finally:
            end = self.clock()
            self.perf.record(f"tool.{name}", end - start, outcome=outcome)
            span.end(end_time=to_ns(end))
            self.mark = end


def _instrument(fn: Callable[..., str], run: _Run) -> BetaFunctionTool[Any]:
    """Wrap a tool so each call is traced; ``wraps`` keeps its schema and validation."""

    @functools.wraps(fn)
    def wrapper(**kwargs: Any) -> str:
        return run.call_tool(fn, kwargs)

    return beta_tool(wrapper)


def _llm_span(
    tracer: Tracer, model: str, message: BetaMessage, *, start: float, end: float
) -> None:
    tracer.start_span(
        "llm.call",
        start_time=to_ns(start),
        attributes={
            KIND: OpenInferenceSpanKindValues.LLM.value,
            SpanAttributes.LLM_MODEL_NAME: model,
            SpanAttributes.LLM_TOKEN_COUNT_PROMPT: message.usage.input_tokens,
            SpanAttributes.LLM_TOKEN_COUNT_COMPLETION: message.usage.output_tokens,
            "llm.stop_reason": message.stop_reason or "",
        },
    ).end(end_time=to_ns(end))


def run_agent(
    client: anthropic.Anthropic,
    version: AgentVersion,
    question: str,
    *,
    request_id: str,
    group: str,
    tracer: Tracer,
    perf: PerformanceTracker,
    costs: CostTracker,
    clock: Callable[[], float],
    max_iterations: int = MAX_ITERATIONS,
) -> RunRecord:
    """Answer one question with the Tool Runner and report what happened.

    Spans are created under whatever span is current, so the caller should
    make its ``agent.run`` span current first.

    Args:
        client: Anthropic client (real, or the dry-run fake).
        version: Version serving this request.
        question: The customer's question.
        request_id: Request identifier, copied into the record.
        group: Canary group, copied into the record.
        tracer: Tracer for ``llm.call`` and ``tool.*`` spans.
        perf: Receives ``model.generate[<version>]`` and ``tool.<name>`` latencies.
        costs: Receives one cost entry per model turn.
        clock: Epoch-seconds clock used for every timestamp and duration.
        max_iterations: Hard cap on model turns.

    Returns:
        The run's record.

    Raises:
        anthropic.AuthenticationError: The API key was rejected. Every
            other API failure becomes outcome ``api_error``.
    """
    start = clock()
    run = _Run(tracer=tracer, clock=clock, perf=perf, mark=start)
    guard = LoopGuard()
    last: BetaMessage | None = None
    outcome: str | None = None
    loop_reason: str | None = None
    cost = 0.0
    input_tokens = output_tokens = 0

    runner = client.beta.messages.tool_runner(
        model=version.model,
        max_tokens=version.max_tokens,
        system=version.system_prompt,
        thinking={"type": "adaptive"},
        output_config={"effort": version.effort},
        tools=[_instrument(fn, run) for fn in TOOL_FUNCTIONS],
        messages=[{"role": "user", "content": question}],
        max_iterations=max_iterations,
    )
    try:
        for message in runner:
            end = clock()
            usage = message.usage
            _llm_span(tracer, version.model, message, start=run.mark, end=end)
            perf.record(
                f"model.generate[{version.version}]",
                end - run.mark,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            )
            cost += costs.record_usage(
                PROJECT, version.model, usage.input_tokens, usage.output_tokens
            ).cost_usd
            input_tokens += usage.input_tokens
            output_tokens += usage.output_tokens
            last = message
            run.mark = end
            loop_reason = guard.check(
                (b.name, b.input) for b in message.content if b.type == "tool_use"
            )
            if loop_reason:
                outcome = "loop"
                break
    except anthropic.AuthenticationError:
        raise
    except (anthropic.APIStatusError, anthropic.APIConnectionError):
        outcome = "api_error"

    if outcome is None:
        outcome = outcome_for(last.stop_reason if last else None)
    answer = "".join(b.text for b in last.content if b.type == "text") if last else ""
    return RunRecord(
        request_id=request_id,
        version=version.version,
        group=group,
        outcome=outcome,
        duration_s=clock() - start,
        cost_usd=cost,
        tool_calls=run.tool_calls,
        tool_errors=run.tool_errors,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        answer=answer,
        loop_reason=loop_reason,
        service_errors=run.service_errors,
    )
