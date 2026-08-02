"""Aggregated monitoring dashboard rendering (Tier 2 — skeleton).

Combines ``cost_tracking`` and ``performance_metrics`` into a single view.
Disabled by default; gated by ``feature_flags.is_enabled("monitoring")``.
Every function is a typed skeleton pending Tier 2 implementation. See
``docs/ARCHITECTURE.md`` for the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass

from cockpit.monitoring.cost_tracking import CostEntry
from cockpit.monitoring.performance_metrics import PerformanceSummary


@dataclass(frozen=True)
class DashboardSnapshot:
    """A point-in-time snapshot of platform-wide monitoring data.

    Attributes:
        total_cost_usd: Total spend across all tracked projects.
        recent_costs: Most recent cost entries.
        performance_by_operation: Latency summaries keyed by operation name.
    """

    total_cost_usd: float
    recent_costs: list[CostEntry]
    performance_by_operation: dict[str, PerformanceSummary]


def build_dashboard_snapshot() -> DashboardSnapshot:
    """Assemble a current snapshot of cost and performance data.

    Returns:
        A :class:`DashboardSnapshot` reflecting current monitoring state.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")


def render_dashboard_text(snapshot: DashboardSnapshot) -> str:
    """Render a dashboard snapshot as a human-readable text report.

    Args:
        snapshot: The snapshot to render.

    Returns:
        A formatted text summary suitable for terminal or log output.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")
