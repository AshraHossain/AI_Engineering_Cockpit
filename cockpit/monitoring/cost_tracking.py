"""Running cost tracking across model calls and projects (Tier 2 — skeleton).

Disabled by default; gated by ``feature_flags.is_enabled("monitoring")``.
Every function is a typed skeleton pending Tier 2 implementation. See
``docs/ARCHITECTURE.md`` for the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CostEntry:
    """A single recorded cost event.

    Attributes:
        timestamp: When the cost was incurred (UTC).
        project: Name of the project/use case that incurred the cost.
        model: Name of the model used.
        cost_usd: Cost of this single call in US dollars.
    """

    timestamp: datetime
    project: str
    model: str
    cost_usd: float


def record_cost(project: str, model: str, cost_usd: float) -> CostEntry:
    """Record a single cost event.

    Args:
        project: Name of the project/use case that incurred the cost.
        model: Name of the model used.
        cost_usd: Cost of this call in US dollars.

    Returns:
        The recorded :class:`CostEntry`, including its timestamp.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")


def get_total_cost(project: str | None = None) -> float:
    """Sum recorded costs, optionally scoped to a single project.

    Args:
        project: If provided, only sum costs for this project. Otherwise
            sum across all projects.

    Returns:
        Total cost in US dollars.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")


def get_cost_by_model() -> dict[str, float]:
    """Break down total recorded cost per model.

    Returns:
        A mapping of model name to total cost in US dollars.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")
