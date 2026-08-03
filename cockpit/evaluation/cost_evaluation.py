"""Cost-effectiveness evaluation: is the quality worth the dollars?

Pricing itself lives in :mod:`cockpit.monitoring.cost_tracking` and is not
duplicated here — this module imports :func:`~cockpit.monitoring.cost_tracking.estimate_cost`
and builds the evaluation layer on top of it:

* cost per *successful* answer, once retries and failures are accounted for
* quality-per-dollar efficiency, pairing a
  :mod:`cockpit.evaluation.quality_metrics` score with a real dollar figure
* ranking several models on the (quality, cost) tradeoff to pick best value
* a budget check for "would this run blow the cap?"

Only the aggregate :func:`evaluate_cost` consults
``feature_flags.is_enabled("evaluation")``; the arithmetic helpers are pure
functions and are always available.

Division-by-zero is handled explicitly rather than by raising: a model that
answers correctly for free is infinitely efficient, and a model that never
answers correctly costs infinitely much per answer. Both are represented
with ``math.inf`` so comparisons and sorts keep working.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from cockpit.config.feature_flags import is_enabled
from cockpit.monitoring.cost_tracking import ModelPricing, UnknownModelError
from cockpit.monitoring.cost_tracking import estimate_cost as _estimate_call_cost
from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)

__all__ = [
    "BudgetCheck",
    "CostEvaluation",
    "ModelComparison",
    "ModelPricing",
    "UnknownModelError",
    "check_budget",
    "compare_cost_efficiency",
    "cost_per_quality_point",
    "cost_per_successful_answer",
    "estimate_cost",
    "evaluate_cost",
    "project_run_cost",
    "quality_per_dollar",
    "rank_by_value",
]


@dataclass(frozen=True)
class CostEvaluation:
    """Cost/efficiency summary for a single model call or batch.

    Attributes:
        model: Name of the model used.
        prompt_tokens: Number of input tokens consumed.
        completion_tokens: Number of output tokens generated.
        estimated_cost_usd: Estimated cost in US dollars.
        cost_per_quality_point: Estimated cost divided by a paired quality
            score, for comparing models on efficiency. ``math.inf`` when the
            quality score is zero.
        quality_score: The paired quality score in [0.0, 1.0], typically
            ``QualityReport.overall_score``.
        quality_per_dollar: Quality delivered per US dollar spent — the
            reciprocal view, used for ranking. ``math.inf`` when the cost is
            zero and quality is positive.
        cost_per_success_usd: Estimated cost divided by the number of
            successful answers, when attempt/pass counts were supplied.
            ``math.inf`` when nothing passed.
        attempts: Number of attempts the cost covers.
        passed: Number of attempts that produced an acceptable answer.
        enabled: False if the evaluation framework was disabled, in which
            case every numeric field holds its neutral default.
    """

    model: str
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    cost_per_quality_point: float
    quality_score: float = 0.0
    quality_per_dollar: float = 0.0
    cost_per_success_usd: float = 0.0
    attempts: int = 1
    passed: int = 1
    enabled: bool = True


@dataclass(frozen=True)
class ModelComparison:
    """Ranked (quality, cost) tradeoff across candidate models.

    Attributes:
        ranked: Candidates ordered best value first.
        best: The best-value candidate, or ``None`` if no candidates were
            supplied.
        tied_with_best: Candidates that matched ``best`` exactly on every
            tie-break criterion. Empty when the winner is unambiguous.
    """

    ranked: list[CostEvaluation] = field(default_factory=list)
    best: CostEvaluation | None = None
    tied_with_best: list[CostEvaluation] = field(default_factory=list)


@dataclass(frozen=True)
class BudgetCheck:
    """Whether a projected spend fits inside a budget cap.

    Attributes:
        within_budget: True if the projected cost does not exceed the cap.
        projected_cost_usd: The spend being checked.
        budget_usd: The cap it was checked against.
        overage_usd: Amount by which the cap is exceeded; 0.0 when within
            budget.
        utilization: Projected cost as a fraction of the budget.
            ``math.inf`` when the budget is zero and cost is positive.
    """

    within_budget: bool
    projected_cost_usd: float
    budget_usd: float
    overage_usd: float = 0.0
    utilization: float = 0.0


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int = 0,
    pricing: dict[str, ModelPricing] | None = None,
) -> float:
    """Estimate the USD cost of a model call from token counts.

    Thin delegation to :func:`cockpit.monitoring.cost_tracking.estimate_cost`
    so the price table lives in exactly one place. Kept here because the
    evaluation API surface documents it.

    Args:
        model: Name of the model used, used to look up per-token pricing.
        prompt_tokens: Number of input tokens consumed. Must be non-negative.
        completion_tokens: Number of output tokens generated. Must be
            non-negative.
        pricing: Optional price-table override, forwarded unchanged.

    Returns:
        Estimated cost in US dollars.

    Raises:
        ValueError: If either token count is negative.
        UnknownModelError: If ``model`` is absent from the price table.
    """
    return _estimate_call_cost(model, prompt_tokens, completion_tokens, pricing=pricing)


def project_run_cost(
    model: str,
    calls: int,
    prompt_tokens_per_call: int,
    completion_tokens_per_call: int = 0,
    pricing: dict[str, ModelPricing] | None = None,
) -> float:
    """Project the cost of a whole evaluation run before running it.

    Assumes uniform token counts per call, which is what you actually know
    up front for a fixed prompt template plus a capped output length.

    Args:
        model: Model the run will use.
        calls: Number of calls the run will make. Must be non-negative.
        prompt_tokens_per_call: Input tokens expected per call.
        completion_tokens_per_call: Output tokens expected per call.
        pricing: Optional price-table override.

    Returns:
        Projected total cost in US dollars.

    Raises:
        ValueError: If ``calls`` or either token count is negative.
        UnknownModelError: If ``model`` is absent from the price table.
    """
    if calls < 0:
        raise ValueError("calls must be non-negative.")
    per_call = estimate_cost(
        model, prompt_tokens_per_call, completion_tokens_per_call, pricing=pricing
    )
    return per_call * calls


def cost_per_successful_answer(total_cost_usd: float, attempts: int, passed: int) -> float:
    """Compute what each *correct* answer actually cost.

    The honest denominator for a benchmark run: paying for 100 attempts to
    get 40 correct answers means each usable answer cost 1/40th of the run,
    not 1/100th.

    Args:
        total_cost_usd: Total spend across all attempts. Must be
            non-negative.
        attempts: Number of attempts made. Must be positive.
        passed: Number of attempts judged correct. Must be in
            ``[0, attempts]``.

    Returns:
        Cost in US dollars per successful answer, or ``math.inf`` if nothing
        passed (the run bought no usable answers at any price).

    Raises:
        ValueError: If the cost is negative, ``attempts`` is not positive,
            or ``passed`` is outside ``[0, attempts]``.
    """
    if total_cost_usd < 0:
        raise ValueError("total_cost_usd must be non-negative.")
    if attempts <= 0:
        raise ValueError("attempts must be positive.")
    if not 0 <= passed <= attempts:
        raise ValueError("passed must be between 0 and attempts inclusive.")

    if passed == 0:
        _logger.debug(
            "No successful answers across %d attempt(s); cost per success is inf.", attempts
        )
        return math.inf
    return total_cost_usd / passed


def quality_per_dollar(quality_score: float, cost_usd: float) -> float:
    """Compute how much quality a dollar bought.

    Args:
        quality_score: Quality in [0.0, 1.0], e.g.
            ``QualityReport.overall_score``.
        cost_usd: Spend that produced it. Must be non-negative.

    Returns:
        Quality per US dollar. ``math.inf`` for positive quality at zero
        cost (a free correct answer is unbeatable value); 0.0 for zero
        quality at zero cost, since nothing was gained.

    Raises:
        ValueError: If ``quality_score`` is outside [0.0, 1.0] or
            ``cost_usd`` is negative.
    """
    if not 0.0 <= quality_score <= 1.0:
        raise ValueError("quality_score must be within [0.0, 1.0].")
    if cost_usd < 0:
        raise ValueError("cost_usd must be non-negative.")

    if cost_usd == 0:
        return math.inf if quality_score > 0 else 0.0
    return quality_score / cost_usd


def cost_per_quality_point(quality_score: float, cost_usd: float) -> float:
    """Compute the dollars paid per point of quality.

    Args:
        quality_score: Quality in [0.0, 1.0].
        cost_usd: Spend that produced it. Must be non-negative.

    Returns:
        Cost in US dollars per quality point. ``math.inf`` when quality is
        zero but money was spent; 0.0 when nothing was spent.

    Raises:
        ValueError: If ``quality_score`` is outside [0.0, 1.0] or
            ``cost_usd`` is negative.
    """
    if not 0.0 <= quality_score <= 1.0:
        raise ValueError("quality_score must be within [0.0, 1.0].")
    if cost_usd < 0:
        raise ValueError("cost_usd must be non-negative.")

    if cost_usd == 0:
        return 0.0
    if quality_score == 0:
        return math.inf
    return cost_usd / quality_score


def check_budget(projected_cost_usd: float, budget_usd: float) -> BudgetCheck:
    """Check whether a projected spend fits inside a budget cap.

    Args:
        projected_cost_usd: The spend to check. Must be non-negative.
        budget_usd: The cap. Must be non-negative; 0.0 means "spend
            nothing", which any positive cost breaches.

    Returns:
        A :class:`BudgetCheck` with the overage and utilization fraction.

    Raises:
        ValueError: If either argument is negative.
    """
    if projected_cost_usd < 0:
        raise ValueError("projected_cost_usd must be non-negative.")
    if budget_usd < 0:
        raise ValueError("budget_usd must be non-negative.")

    within = projected_cost_usd <= budget_usd
    overage = 0.0 if within else projected_cost_usd - budget_usd
    if budget_usd == 0:
        utilization = 0.0 if projected_cost_usd == 0 else math.inf
    else:
        utilization = projected_cost_usd / budget_usd

    if not within:
        _logger.warning(
            "Projected cost $%.4f exceeds budget $%.4f by $%.4f.",
            projected_cost_usd,
            budget_usd,
            overage,
        )
    return BudgetCheck(
        within_budget=within,
        projected_cost_usd=projected_cost_usd,
        budget_usd=budget_usd,
        overage_usd=overage,
        utilization=utilization,
    )


def _value_sort_key(evaluation: CostEvaluation) -> tuple[float, float, float, str]:
    """Build the ranking key for :func:`rank_by_value`.

    Sorted ascending, so each component is negated where "more is better".
    Ties on efficiency break toward higher quality, then lower cost, then
    model name so the ordering is stable and reproducible.

    Args:
        evaluation: The candidate to rank.

    Returns:
        A ``(-quality_per_dollar, -quality_score, cost, model)`` tuple.
    """
    return (
        -evaluation.quality_per_dollar,
        -evaluation.quality_score,
        evaluation.estimated_cost_usd,
        evaluation.model,
    )


def rank_by_value(evaluations: Sequence[CostEvaluation]) -> ModelComparison:
    """Rank candidate models on the (quality, cost) tradeoff.

    Primary criterion is quality per dollar; ties break toward the higher
    quality score, then the lower absolute cost. Candidates that are
    genuinely indistinguishable (equal efficiency, quality, and cost) are
    reported in ``tied_with_best`` instead of one being silently crowned.

    Args:
        evaluations: Cost evaluations for competing models/configurations.

    Returns:
        A :class:`ModelComparison`. Empty input yields an empty comparison
        with ``best=None`` rather than an error, so callers can rank a
        partially-failed sweep.
    """
    if not evaluations:
        _logger.debug("rank_by_value called with no candidates; returning empty comparison.")
        return ModelComparison()

    ranked = sorted(evaluations, key=_value_sort_key)
    best = ranked[0]
    tied = [
        candidate
        for candidate in ranked[1:]
        if _value_sort_key(candidate)[:3] == _value_sort_key(best)[:3]
    ]
    if tied:
        _logger.debug(
            "Best-value tie between %s and %s.",
            best.model,
            ", ".join(candidate.model for candidate in tied),
        )
    return ModelComparison(ranked=ranked, best=best, tied_with_best=tied)


def compare_cost_efficiency(evaluations: list[CostEvaluation]) -> CostEvaluation:
    """Identify the most cost-efficient evaluation among several candidates.

    Convenience wrapper over :func:`rank_by_value` for callers that only
    want the winner.

    Args:
        evaluations: Cost evaluations for competing models/configurations.

    Returns:
        The :class:`CostEvaluation` with the best (lowest)
        ``cost_per_quality_point``, ties broken as in :func:`rank_by_value`.

    Raises:
        ValueError: If ``evaluations`` is empty — there is no meaningful
            winner to return.
    """
    comparison = rank_by_value(evaluations)
    if comparison.best is None:
        raise ValueError("Cannot compare cost efficiency of an empty candidate list.")
    return comparison.best


def evaluate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int = 0,
    *,
    quality_score: float = 0.0,
    attempts: int = 1,
    passed: int = 1,
    pricing: dict[str, ModelPricing] | None = None,
) -> CostEvaluation:
    """Price a run and score its cost-effectiveness in one step.

    Token counts are treated as totals for the whole run (all ``attempts``
    combined), which is what a usage API reports back.

    Gated by ``feature_flags.is_enabled("evaluation")``: when the framework
    is disabled this returns a neutral zero-cost evaluation with
    ``enabled=False`` instead of pricing anything.

    Args:
        model: Model used, for the price-table lookup.
        prompt_tokens: Total input tokens across the run.
        completion_tokens: Total output tokens across the run.
        quality_score: Paired quality score in [0.0, 1.0], typically
            ``QualityReport.overall_score``.
        attempts: Number of attempts the token counts cover.
        passed: Number of attempts judged correct.
        pricing: Optional price-table override.

    Returns:
        A :class:`CostEvaluation` with the dollar cost plus the
        efficiency views.

    Raises:
        ValueError: If a token count is negative, ``quality_score`` is
            outside [0.0, 1.0], ``attempts`` is not positive, or ``passed``
            is outside ``[0, attempts]``.
        UnknownModelError: If ``model`` is absent from the price table.
    """
    if not is_enabled("evaluation"):
        _logger.debug("Evaluation framework disabled; skipping cost evaluation.")
        return CostEvaluation(
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            estimated_cost_usd=0.0,
            cost_per_quality_point=0.0,
            enabled=False,
        )

    cost = estimate_cost(model, prompt_tokens, completion_tokens, pricing=pricing)
    return CostEvaluation(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=cost,
        cost_per_quality_point=cost_per_quality_point(quality_score, cost),
        quality_score=quality_score,
        quality_per_dollar=quality_per_dollar(quality_score, cost),
        cost_per_success_usd=cost_per_successful_answer(cost, attempts, passed),
        attempts=attempts,
        passed=passed,
    )
