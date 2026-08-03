"""Cockpit instrumentation for the tool-use agent.

This is the module that makes this project different from ``projects/01``
through ``projects/05``: it imports the cockpit frameworks and uses them to
put numbers on an agent run.

* :class:`~cockpit.monitoring.performance_metrics.PerformanceTracker` times
  every model call and every tool execution, under separate operation names
  (``model.generate`` versus ``tool.calculate``) so a slow tool is
  distinguishable from a slow model.
* :class:`~cockpit.monitoring.cost_tracking.CostTracker` prices each model
  call from the token counts the SDK reports back.
* :func:`~cockpit.evaluation.safety_evaluation.detect_pii_leakage` screens
  each tool's output *before* it is fed back into the conversation. A tool
  result is untrusted data heading straight into the model's context, and
  the round trip means anything a tool emits can end up echoed to the user.

The trackers are plain injectable objects -- nothing here touches the
process-wide default trackers, so a test can construct its own instance with
a deterministic ``clock`` and assert on exact durations without sleeping.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final

from cockpit.evaluation.safety_evaluation import detect_pii_leakage
from cockpit.monitoring.cost_tracking import CostTracker, UnknownModelError
from cockpit.monitoring.performance_metrics import (
    Measurement,
    PerformanceTracker,
)

DEFAULT_PROJECT: Final[str] = "09-agent-tool-use"

MODEL_OPERATION: Final[str] = "model.generate"
TOOL_OPERATION_PREFIX: Final[str] = "tool."

_REDACTION_NOTICE: Final[str] = (
    "[tool output withheld: it contained what looks like personal data "
    "({categories}), so it was not passed back to the model]"
)


def tool_operation(tool_name: str) -> str:
    """Build the performance-tracker operation name for a tool.

    Args:
        tool_name: Name of the tool being executed.

    Returns:
        The namespaced operation name, e.g. ``"tool.calculate"``.
    """
    return f"{TOOL_OPERATION_PREFIX}{tool_name}"


@dataclass(frozen=True)
class OutputScreening:
    """Result of screening a tool's output before returning it to the model.

    Attributes:
        safe: True when nothing suspicious was found.
        categories: PII categories detected, sorted. Empty when ``safe``.
        text: The text to actually hand back to the model -- the original
            output when safe, a redaction notice otherwise.
    """

    safe: bool
    categories: tuple[str, ...]
    text: str


def screen_tool_output(output: str) -> OutputScreening:
    """Check a tool result for leaked personal data before the model sees it.

    Args:
        output: The raw string a tool returned.

    Returns:
        An :class:`OutputScreening` whose ``text`` is safe to append to the
        conversation.
    """
    check = detect_pii_leakage(output)
    if not check.has_pii:
        return OutputScreening(safe=True, categories=(), text=output)

    categories = tuple(check.categories)
    return OutputScreening(
        safe=False,
        categories=categories,
        text=_REDACTION_NOTICE.format(categories=", ".join(categories) or "unknown"),
    )


@dataclass(frozen=True)
class OperationStat:
    """Latency rollup for one instrumented operation.

    Attributes:
        operation: Operation name, e.g. ``"model.generate"``.
        calls: Number of recorded calls.
        total_seconds: Summed duration across those calls.
        mean_seconds: Mean duration.
        p95_seconds: 95th-percentile duration.
        error_count: Calls that raised or were flagged as failures.
    """

    operation: str
    calls: int
    total_seconds: float
    mean_seconds: float
    p95_seconds: float
    error_count: int


@dataclass(frozen=True)
class InstrumentationReport:
    """Everything measured during one agent run.

    Attributes:
        total_cost_usd: Summed cost of every model call in the run.
        cost_by_model: Cost broken down per model id.
        total_input_tokens: Prompt tokens billed across the run.
        total_output_tokens: Completion tokens billed across the run.
        operations: Per-operation latency rollups, sorted by name.
    """

    total_cost_usd: float
    cost_by_model: dict[str, float]
    total_input_tokens: int
    total_output_tokens: int
    operations: tuple[OperationStat, ...]

    def render(self) -> str:
        """Format the report as plain text for a CLI to print.

        Returns:
            A multi-line, human-readable summary. Returns a placeholder line
            when nothing was recorded.
        """
        if not self.operations:
            return "(nothing instrumented)"

        lines = [
            f"{stat.operation:<24} calls={stat.calls:<3} "
            f"total={stat.total_seconds:.3f}s mean={stat.mean_seconds:.3f}s "
            f"p95={stat.p95_seconds:.3f}s errors={stat.error_count}"
            for stat in self.operations
        ]
        lines.append(
            f"tokens: in={self.total_input_tokens} out={self.total_output_tokens} | "
            f"cost: ${self.total_cost_usd:.6f}"
        )
        return "\n".join(lines)


@dataclass
class AgentInstrumentation:
    """Bundles the cockpit trackers used by a single agent.

    Attributes:
        project: Project name recorded on every cost entry, so a shared
            :class:`CostTracker` can be broken down per project.
        performance: Latency tracker. Inject one with a custom ``clock`` to
            make timings deterministic in tests.
        cost: Spend tracker.
    """

    project: str = DEFAULT_PROJECT
    performance: PerformanceTracker = field(default_factory=PerformanceTracker)
    cost: CostTracker = field(default_factory=CostTracker)

    @contextmanager
    def measure_model_call(self) -> Iterator[Measurement]:
        """Time one model round trip.

        Yields:
            The mutable :class:`Measurement` to write token counts into once
            the response has come back.
        """
        with self.performance.measure(MODEL_OPERATION) as call:
            yield call

    @contextmanager
    def measure_tool_call(self, tool_name: str) -> Iterator[Measurement]:
        """Time one tool execution.

        Args:
            tool_name: Name of the tool being executed.

        Yields:
            The mutable :class:`Measurement` for this tool call. A tool that
            raises is still recorded, flagged as an error.
        """
        with self.performance.measure(tool_operation(tool_name)) as call:
            yield call

    def record_model_usage(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Price a model call from its token counts and record the spend.

        An unpriced model is not fatal: the call is simply not costed, since
        losing the whole run over a missing price-table row would be worse
        than an under-reported total.

        Args:
            model: Model id the call was made against.
            input_tokens: Prompt tokens the SDK reported.
            output_tokens: Completion tokens the SDK reported.

        Returns:
            The cost of this call in US dollars, or 0.0 if the model has no
            entry in the price table.
        """
        try:
            entry = self.cost.record_usage(self.project, model, input_tokens, output_tokens)
        except UnknownModelError:
            return 0.0
        return entry.cost_usd

    def report(self) -> InstrumentationReport:
        """Roll every recorded measurement up into one report.

        Returns:
            The :class:`InstrumentationReport` for this run.
        """
        input_tokens, output_tokens = self.cost.total_tokens()
        operations = tuple(
            OperationStat(
                operation=name,
                calls=summary.sample_count,
                total_seconds=summary.total_seconds,
                mean_seconds=summary.mean_seconds,
                p95_seconds=summary.p95_seconds,
                error_count=summary.error_count,
            )
            for name, summary in self.performance.summaries().items()
        )
        return InstrumentationReport(
            total_cost_usd=self.cost.total_cost(self.project),
            cost_by_model=self.cost.cost_by_model(),
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
            operations=operations,
        )
