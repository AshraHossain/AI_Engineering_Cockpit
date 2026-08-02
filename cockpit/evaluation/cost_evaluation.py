"""Cost-efficiency evaluation: token usage and $/quality tradeoffs (Tier 2 — skeleton).

Disabled by default; gated by ``feature_flags.is_enabled("evaluation")``.
Every function is a typed skeleton pending Tier 2 implementation. See
``docs/ARCHITECTURE.md`` for the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostEvaluation:
    """Cost/efficiency summary for a single model call.

    Attributes:
        model: Name of the model used.
        prompt_tokens: Number of input tokens consumed.
        completion_tokens: Number of output tokens generated.
        estimated_cost_usd: Estimated cost in US dollars.
        cost_per_quality_point: Estimated cost divided by a paired quality
            score, for comparing models on efficiency.
    """

    model: str
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    cost_per_quality_point: float


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate the USD cost of a model call from token counts.

    Args:
        model: Name of the model used, used to look up per-token pricing.
        prompt_tokens: Number of input tokens consumed.
        completion_tokens: Number of output tokens generated.

    Returns:
        Estimated cost in US dollars.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")


def compare_cost_efficiency(evaluations: list[CostEvaluation]) -> CostEvaluation:
    """Identify the most cost-efficient evaluation among several candidates.

    Args:
        evaluations: Cost evaluations for competing models/configurations.

    Returns:
        The :class:`CostEvaluation` with the best (lowest)
        ``cost_per_quality_point``.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")
