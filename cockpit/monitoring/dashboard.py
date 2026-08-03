"""Aggregated monitoring dashboard: one point-in-time view of cost + latency.

This module is read-only. It never records anything itself — it composes a
:class:`~cockpit.monitoring.cost_tracking.CostTracker` and a
:class:`~cockpit.monitoring.performance_metrics.PerformanceTracker` that the
caller already owns into an immutable :class:`DashboardSnapshot`, then
formats it.

Both trackers are **injected as parameters** rather than pulled from
module-level defaults: a dashboard that reaches for globals cannot be tested
without mutating process-wide state, and cannot show two tenants side by
side. Callers that do want the process-wide view pass the shared trackers in
explicitly (``get_default_tracker()`` from each module).

No feature-flag gate lives here. Snapshotting and formatting are pure
computation over data the trackers already hold; the ``monitoring`` flag is
enforced upstream, at the recording entry points.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from cockpit.monitoring.cost_tracking import CostEntry, CostTracker
from cockpit.monitoring.performance_metrics import PerformanceSummary, PerformanceTracker
from cockpit.utils.helpers import truncate

_DEFAULT_RECENT_LIMIT = 5

# Text-report column widths, shared by the header and the rows so they line up.
_LABEL_WIDTH = 24
_VALUE_WIDTH = 14
_NAME_WIDTH = 26
_COUNT_WIDTH = 7
_LATENCY_WIDTH = 9
_ERRORS_WIDTH = 8
_TABLE_WIDTH = _NAME_WIDTH + _COUNT_WIDTH + 3 * _LATENCY_WIDTH + _ERRORS_WIDTH
_RULE_WIDTH = 70


@dataclass(frozen=True)
class DashboardSnapshot:
    """A point-in-time snapshot of platform-wide monitoring data.

    Attributes:
        generated_at: When the snapshot was taken (UTC).
        total_cost_usd: Total spend across all tracked projects.
        cost_by_model: Spend per model, in US dollars.
        cost_by_project: Spend per project, in US dollars.
        total_input_tokens: Prompt tokens billed across all cost entries.
        total_output_tokens: Completion tokens billed across all cost
            entries.
        recent_costs: Most recent cost entries, newest last.
        performance_by_operation: Latency summaries keyed by operation name.
        total_calls: Number of timed calls recorded.
        error_rate: Fraction of timed calls that failed, 0.0-1.0.
    """

    generated_at: datetime
    total_cost_usd: float
    cost_by_model: dict[str, float]
    cost_by_project: dict[str, float]
    total_input_tokens: int
    total_output_tokens: int
    recent_costs: list[CostEntry]
    performance_by_operation: dict[str, PerformanceSummary]
    total_calls: int
    error_rate: float

    @property
    def total_tokens(self) -> int:
        """Total billed tokens, input plus output.

        Returns:
            The combined token count.
        """
        return self.total_input_tokens + self.total_output_tokens

    @property
    def is_empty(self) -> bool:
        """Whether the snapshot contains no data at all.

        Returns:
            True when neither tracker had recorded anything.
        """
        return not self.recent_costs and not self.performance_by_operation


def build_dashboard_snapshot(
    cost_tracker: CostTracker,
    performance_tracker: PerformanceTracker,
    *,
    recent_limit: int = _DEFAULT_RECENT_LIMIT,
    generated_at: datetime | None = None,
) -> DashboardSnapshot:
    """Assemble a snapshot of cost and performance data from two trackers.

    Args:
        cost_tracker: Tracker holding the spend data to roll up.
        performance_tracker: Tracker holding the latency data to roll up.
        recent_limit: How many of the newest cost entries to carry in
            :attr:`DashboardSnapshot.recent_costs`. Must be non-negative.
        generated_at: Snapshot time; defaults to now (UTC). Injectable so
            tests do not have to freeze the clock.

    Returns:
        An immutable :class:`DashboardSnapshot`. Reading two live trackers is
        not one atomic operation, so the snapshot is "recent", not a
        transactionally consistent instant across both.

    Raises:
        ValueError: If ``recent_limit`` is negative.
    """
    if recent_limit < 0:
        raise ValueError("recent_limit must be non-negative.")

    input_tokens, output_tokens = cost_tracker.total_tokens()
    entries = list(cost_tracker.entries)
    recent = entries[-recent_limit:] if recent_limit else []

    return DashboardSnapshot(
        generated_at=generated_at or datetime.now(UTC),
        total_cost_usd=cost_tracker.total_cost(),
        cost_by_model=cost_tracker.cost_by_model(),
        cost_by_project=cost_tracker.cost_by_project(),
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        recent_costs=recent,
        performance_by_operation=performance_tracker.summaries(),
        total_calls=performance_tracker.call_count(),
        error_rate=performance_tracker.error_rate(),
    )


def _money(amount: float) -> str:
    """Format a dollar amount for the text report.

    Args:
        amount: Amount in US dollars.

    Returns:
        A fixed-precision string such as ``"$0.001234"``.
    """
    return f"${amount:,.6f}"


def _stat_line(label: str, value: str) -> str:
    """Format one indented ``label ....... value`` line.

    Args:
        label: Left-hand label.
        value: Right-aligned value.

    Returns:
        The formatted line, without a trailing newline.
    """
    return f"  {label:<{_LABEL_WIDTH}}{value:>{_VALUE_WIDTH}}"


def _breakdown_lines(title: str, totals: dict[str, float]) -> list[str]:
    """Format a sorted ``name -> dollars`` breakdown block.

    Args:
        title: Heading for the block, e.g. ``"By model"``.
        totals: Mapping of name to dollar amount.

    Returns:
        The block's lines, including its heading. Renders an explicit
        placeholder rather than an empty block when ``totals`` is empty.
    """
    lines = [f"  {title}"]
    if not totals:
        lines.append("    (none)")
        return lines
    for name, amount in sorted(totals.items(), key=lambda item: (-item[1], item[0])):
        label = truncate(name, _LABEL_WIDTH - 2)
        lines.append(f"    {label:<{_LABEL_WIDTH - 2}}{_money(amount):>{_VALUE_WIDTH}}")
    return lines


def _performance_table(summaries: dict[str, PerformanceSummary]) -> list[str]:
    """Format the per-operation latency table.

    Args:
        summaries: Latency summaries keyed by operation name.

    Returns:
        The table's lines: header, rule, and one row per operation.
    """
    header = (
        f"  {'Operation':<{_NAME_WIDTH}}"
        f"{'Calls':>{_COUNT_WIDTH}}"
        f"{'p50':>{_LATENCY_WIDTH}}"
        f"{'p95':>{_LATENCY_WIDTH}}"
        f"{'p99':>{_LATENCY_WIDTH}}"
        f"{'Errors':>{_ERRORS_WIDTH}}"
    )
    lines = [header, "  " + "-" * _TABLE_WIDTH]
    for name in sorted(summaries):
        summary = summaries[name]
        lines.append(
            f"  {truncate(name, _NAME_WIDTH - 1):<{_NAME_WIDTH}}"
            f"{summary.sample_count:>{_COUNT_WIDTH}}"
            f"{f'{summary.p50_seconds:.3f}s':>{_LATENCY_WIDTH}}"
            f"{f'{summary.p95_seconds:.3f}s':>{_LATENCY_WIDTH}}"
            f"{f'{summary.p99_seconds:.3f}s':>{_LATENCY_WIDTH}}"
            f"{summary.error_count:>{_ERRORS_WIDTH}}"
        )
    return lines


def _recent_cost_lines(entries: list[CostEntry]) -> list[str]:
    """Format the recent-cost-entry rows.

    Args:
        entries: Cost entries to render, oldest first.

    Returns:
        One line per entry.
    """
    lines: list[str] = []
    for entry in entries:
        stamp = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        project = truncate(entry.project, 18)
        model = truncate(entry.model, 22)
        cost = _money(entry.cost_usd)
        lines.append(f"  {stamp}  {project:<18}  {model:<22}{cost:>{_VALUE_WIDTH}}")
    return lines


def render_dashboard_text(snapshot: DashboardSnapshot) -> str:
    """Render a dashboard snapshot as a human-readable text report.

    Pure formatting: the string is returned, never printed, so callers decide
    whether it goes to a terminal, a log record, or a file. The empty state
    (nothing recorded yet) renders a valid report with explicit placeholders
    and no division by zero.

    Args:
        snapshot: The snapshot to render.

    Returns:
        A formatted, newline-separated text summary suitable for terminal or
        log output. No trailing newline.
    """
    rule = "=" * _RULE_WIDTH
    lines: list[str] = [
        rule,
        "AI Engineering Cockpit - Monitoring Dashboard",
        f"Generated: {snapshot.generated_at.isoformat(timespec='seconds')}",
        rule,
    ]

    if snapshot.is_empty:
        lines.append("")
        lines.append("  No monitoring data recorded yet.")
        lines.append(rule)
        return "\n".join(lines)

    lines.append("")
    lines.append("COST")
    lines.append(_stat_line("Total spend", _money(snapshot.total_cost_usd)))
    lines.append(_stat_line("Input tokens", f"{snapshot.total_input_tokens:,}"))
    lines.append(_stat_line("Output tokens", f"{snapshot.total_output_tokens:,}"))
    lines.append(_stat_line("Total tokens", f"{snapshot.total_tokens:,}"))
    lines.append("")
    lines.extend(_breakdown_lines("By model", snapshot.cost_by_model))
    lines.append("")
    lines.extend(_breakdown_lines("By project", snapshot.cost_by_project))

    lines.append("")
    lines.append("PERFORMANCE")
    lines.append(_stat_line("Total calls", f"{snapshot.total_calls:,}"))
    lines.append(_stat_line("Error rate", f"{snapshot.error_rate * 100:.2f}%"))
    lines.append("")
    if snapshot.performance_by_operation:
        lines.extend(_performance_table(snapshot.performance_by_operation))
    else:
        lines.append("  (no timed calls recorded)")

    lines.append("")
    lines.append(f"RECENT COSTS (last {len(snapshot.recent_costs)})")
    if snapshot.recent_costs:
        lines.extend(_recent_cost_lines(snapshot.recent_costs))
    else:
        lines.append("  (none)")

    lines.append(rule)
    return "\n".join(lines)
