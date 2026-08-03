"""The evaluation harness itself: run a Q&A pipeline and score every answer.

This is the module that makes the cockpit's Tier 2 evaluation framework
tangible. For each :class:`~dataset.EvalItem` it:

1. calls an **injected** ``generate_fn(prompt) -> str`` -- no SDK is imported
   here, so the whole harness runs offline against a fake;
2. scores the answer with
   :func:`cockpit.evaluation.quality_metrics.evaluate_quality` (token F1,
   fuzzy similarity, normalized match, required-keyword coverage) plus the
   standalone relevance and coherence metrics;
3. checks the answer with
   :func:`cockpit.evaluation.safety_evaluation.evaluate_safety`;
4. accumulates token usage into a
   :class:`cockpit.monitoring.cost_tracking.CostTracker`;
5. rolls everything into a frozen :class:`HarnessReport`, priced by
   :func:`cockpit.evaluation.cost_evaluation.evaluate_cost`.

Token accounting has two modes. If the caller injects a
:class:`UsageReporter` alongside the generator (the live Gemini adapter is
both), the harness uses the provider's real reported token counts. Otherwise
it falls back to a documented characters-per-token estimate, which keeps the
cost column meaningful in offline dry runs without pretending to be exact.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# The repo root is package=false by design, so it isn't installed into this
# project's venv -- put it on sys.path to import the cockpit frameworks.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cockpit.evaluation.cost_evaluation import (  # noqa: E402
    BudgetCheck,
    CostEvaluation,
    check_budget,
    evaluate_cost,
)
from cockpit.evaluation.quality_metrics import (  # noqa: E402
    QualityReport,
    evaluate_quality,
    score_coherence,
    score_relevance,
)
from cockpit.evaluation.safety_evaluation import (  # noqa: E402
    SafetyEvaluation,
    SafetyVerdict,
    evaluate_safety,
)
from cockpit.monitoring.cost_tracking import CostTracker  # noqa: E402
from dataset import EvalItem, EvalSet  # noqa: E402

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_PROJECT_NAME = "06-eval-harness"

#: An answer at or above this overall quality score counts as a pass.
DEFAULT_PASS_THRESHOLD = 0.35

#: Rough English characters-per-token ratio, used only when the caller does
#: not inject a :class:`UsageReporter` carrying the provider's real counts.
_CHARS_PER_TOKEN = 4

QA_PROMPT_TEMPLATE = """Answer the question below in one or two sentences. \
Be direct and factual; do not add a preamble.

Question: {question}

Answer:"""


@dataclass(frozen=True)
class TokenUsage:
    """Input/output token counts for a single model call.

    Attributes:
        input_tokens: Prompt tokens billed.
        output_tokens: Completion tokens billed.
        estimated: True if the counts were derived from text length rather
            than reported by the provider.
    """

    input_tokens: int
    output_tokens: int
    estimated: bool = False


class GenerateFn(Protocol):
    """A Q&A pipeline under evaluation: prompt in, answer text out.

    Deliberately the narrowest possible interface, so the harness can be
    pointed at a raw model client, a whole RAG pipeline, or an in-memory
    fake with no code changes.
    """

    def __call__(self, prompt: str) -> str: ...


class UsageReporter(Protocol):
    """Optional companion to a :class:`GenerateFn` that reports real usage.

    Implementations record the provider's token counts for the most recent
    call. Injecting one is what upgrades the cost column from an estimate to
    the provider's own accounting.
    """

    def last_usage(self) -> TokenUsage: ...


@dataclass(frozen=True)
class ItemResult:
    """Scored outcome for one evaluation item.

    Attributes:
        item_id: Identifier of the item that was run.
        question: The question that was asked.
        response: The generated answer, or empty when the call raised.
        quality: Aggregate quality report for the answer.
        safety: Safety evaluation for the answer.
        relevance: Standalone relevance score in [0.0, 1.0].
        coherence: Standalone coherence score in [0.0, 1.0].
        usage: Token usage attributed to this item.
        cost_usd: Dollar cost of this item's call.
        passed: True if the item met the pass threshold and was judged safe.
        error: Repr of the exception raised by the generator, if any.
    """

    item_id: str
    question: str
    response: str
    quality: QualityReport | None
    safety: SafetyEvaluation | None
    relevance: float
    coherence: float
    usage: TokenUsage
    cost_usd: float
    passed: bool
    error: str | None = None

    @property
    def overall_score(self) -> float:
        """Overall quality score for this item.

        Returns:
            The aggregate quality score, or 0.0 if the item errored.
        """
        return 0.0 if self.quality is None else self.quality.overall_score


@dataclass(frozen=True)
class HarnessReport:
    """Aggregate scorecard for a full harness run.

    Attributes:
        dataset_name: Name of the evaluation set that was run.
        model: Model the run was priced against.
        threshold: Quality score an item had to reach to pass.
        total: Number of items run.
        passed: Items that met the threshold and were judged safe.
        failed: Items that ran but did not pass.
        errored: Items whose generator call raised.
        pass_rate: ``passed / total``, in [0.0, 1.0]. 1.0 for an empty run.
        mean_quality: Mean overall quality score across non-errored items.
        mean_relevance: Mean relevance score across non-errored items.
        mean_coherence: Mean coherence score across non-errored items.
        mean_token_f1: Mean token-level F1 against the reference answers.
        mean_keyword_coverage: Mean required-keyword coverage.
        unsafe_items: Identifiers of items whose safety verdict was unsafe.
        review_items: Identifiers of items flagged for human review.
        total_input_tokens: Prompt tokens across the whole run.
        total_output_tokens: Completion tokens across the whole run.
        total_cost_usd: Total spend across the whole run.
        cost_by_model: Spend broken down per model, from the cost tracker.
        cost_evaluation: The run priced and scored for value.
        budget: Budget check, or ``None`` if no budget was supplied.
        tokens_estimated: True if any item's tokens were estimated rather
            than reported by the provider.
        results: Per-item results, in execution order.
    """

    dataset_name: str
    model: str
    threshold: float
    total: int
    passed: int
    failed: int
    errored: int
    pass_rate: float
    mean_quality: float
    mean_relevance: float
    mean_coherence: float
    mean_token_f1: float
    mean_keyword_coverage: float
    unsafe_items: tuple[str, ...] = ()
    review_items: tuple[str, ...] = ()
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    cost_by_model: dict[str, float] = field(default_factory=dict)
    cost_evaluation: CostEvaluation | None = None
    budget: BudgetCheck | None = None
    tokens_estimated: bool = False
    results: tuple[ItemResult, ...] = ()


def build_prompt(item: EvalItem) -> str:
    """Render the Q&A prompt for one evaluation item.

    Args:
        item: The item to build a prompt for.

    Returns:
        The formatted prompt string.
    """
    return QA_PROMPT_TEMPLATE.format(question=item.question)


def estimate_token_usage(prompt: str, response: str) -> TokenUsage:
    """Estimate token counts from text length.

    Used only when no :class:`UsageReporter` is injected. English text runs
    at roughly four characters per token, so this is accurate enough to keep
    the dry-run cost column honest while being clearly labelled an estimate.

    Args:
        prompt: The prompt that was sent.
        response: The answer that came back.

    Returns:
        A :class:`TokenUsage` with ``estimated=True``.
    """
    return TokenUsage(
        input_tokens=max(1, len(prompt) // _CHARS_PER_TOKEN),
        output_tokens=len(response) // _CHARS_PER_TOKEN,
        estimated=True,
    )


def _mean(values: Sequence[float]) -> float:
    """Compute an arithmetic mean that tolerates an empty sequence.

    Args:
        values: The values to average.

    Returns:
        The mean, or 0.0 if ``values`` is empty.
    """
    return sum(values) / len(values) if values else 0.0


def evaluate_item(
    item: EvalItem,
    generate_fn: GenerateFn,
    *,
    usage_reporter: UsageReporter | None = None,
    tracker: CostTracker | None = None,
    model: str = DEFAULT_MODEL,
    threshold: float = DEFAULT_PASS_THRESHOLD,
    project: str = DEFAULT_PROJECT_NAME,
) -> ItemResult:
    """Run and score a single evaluation item.

    A generator that raises produces an errored :class:`ItemResult` rather
    than aborting the run: one flaky call should not destroy a scorecard.

    Args:
        item: The evaluation item to run.
        generate_fn: Injected pipeline under evaluation.
        usage_reporter: Optional source of the provider's real token counts.
        tracker: Cost tracker to accumulate usage into. ``None`` skips
            accounting for this item.
        model: Model name used for pricing.
        threshold: Overall quality score the answer must reach to pass.
        project: Project label recorded on the cost tracker.

    Returns:
        The scored :class:`ItemResult`.
    """
    prompt = build_prompt(item)
    zero_usage = TokenUsage(0, 0)

    try:
        response = generate_fn(prompt)
    # The pipeline is arbitrary caller code, so every exception type is in play.
    except Exception as exc:
        return ItemResult(
            item_id=item.item_id,
            question=item.question,
            response="",
            quality=None,
            safety=None,
            relevance=0.0,
            coherence=0.0,
            usage=zero_usage,
            cost_usd=0.0,
            passed=False,
            error=repr(exc),
        )

    if not isinstance(response, str):
        return ItemResult(
            item_id=item.item_id,
            question=item.question,
            response="",
            quality=None,
            safety=None,
            relevance=0.0,
            coherence=0.0,
            usage=zero_usage,
            cost_usd=0.0,
            passed=False,
            error=f"generate_fn returned {type(response).__name__}, expected str",
        )

    quality = evaluate_quality(
        response,
        item.reference_answer,
        prompt=item.question,
        required_phrases=item.required_keywords or None,
    )
    safety = evaluate_safety(response, prompt=item.question)
    relevance = score_relevance(item.question, response).score
    coherence = score_coherence(response).score

    usage = (
        usage_reporter.last_usage()
        if usage_reporter is not None
        else estimate_token_usage(prompt, response)
    )
    cost_usd = 0.0
    if tracker is not None:
        entry = tracker.record_usage(project, model, usage.input_tokens, usage.output_tokens)
        cost_usd = entry.cost_usd

    passed = quality.overall_score >= threshold and safety.verdict is not SafetyVerdict.UNSAFE
    return ItemResult(
        item_id=item.item_id,
        question=item.question,
        response=response,
        quality=quality,
        safety=safety,
        relevance=relevance,
        coherence=coherence,
        usage=usage,
        cost_usd=cost_usd,
        passed=passed,
    )


def run_harness(
    eval_set: EvalSet,
    generate_fn: GenerateFn,
    *,
    usage_reporter: UsageReporter | None = None,
    tracker: CostTracker | None = None,
    model: str = DEFAULT_MODEL,
    threshold: float = DEFAULT_PASS_THRESHOLD,
    budget_usd: float | None = None,
    project: str = DEFAULT_PROJECT_NAME,
) -> HarnessReport:
    """Run every item in an evaluation set and produce an aggregate scorecard.

    Args:
        eval_set: The dataset to run.
        generate_fn: Injected pipeline under evaluation.
        usage_reporter: Optional source of the provider's real token counts.
            When omitted, token counts are estimated from text length.
        tracker: Cost tracker to accumulate into. A fresh one is created if
            omitted, so the run is always priced.
        model: Model name used for pricing. Must exist in the cockpit price
            table.
        threshold: Overall quality score an answer must reach to pass. Must
            be within [0.0, 1.0].
        budget_usd: Optional spend cap; when given, the report carries a
            :class:`~cockpit.evaluation.cost_evaluation.BudgetCheck`.
        project: Project label recorded on the cost tracker.

    Returns:
        The aggregate :class:`HarnessReport`.

    Raises:
        ValueError: If ``threshold`` is outside [0.0, 1.0] or ``budget_usd``
            is negative.
        UnknownModelError: If ``model`` is absent from the cockpit price
            table.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be within [0.0, 1.0].")
    if budget_usd is not None and budget_usd < 0:
        raise ValueError("budget_usd must be non-negative.")

    cost_tracker = tracker if tracker is not None else CostTracker()
    results = tuple(
        evaluate_item(
            item,
            generate_fn,
            usage_reporter=usage_reporter,
            tracker=cost_tracker,
            model=model,
            threshold=threshold,
            project=project,
        )
        for item in eval_set.items
    )
    return summarize(
        results,
        eval_set=eval_set,
        tracker=cost_tracker,
        model=model,
        threshold=threshold,
        budget_usd=budget_usd,
        project=project,
    )


def summarize(
    results: Sequence[ItemResult],
    *,
    eval_set: EvalSet,
    tracker: CostTracker,
    model: str = DEFAULT_MODEL,
    threshold: float = DEFAULT_PASS_THRESHOLD,
    budget_usd: float | None = None,
    project: str = DEFAULT_PROJECT_NAME,
) -> HarnessReport:
    """Aggregate per-item results into a :class:`HarnessReport`.

    Args:
        results: Per-item results, in execution order.
        eval_set: The dataset the results came from, for its name.
        tracker: The tracker the run's usage was accumulated into.
        model: Model name the run was priced against.
        threshold: Threshold the items were judged against.
        budget_usd: Optional spend cap to check the total against.
        project: Project label used when scoping the tracker total.

    Returns:
        The aggregate :class:`HarnessReport`.

    Raises:
        UnknownModelError: If ``model`` is absent from the cockpit price
            table.
    """
    total = len(results)
    scored = [r for r in results if r.quality is not None]
    passed = sum(1 for r in results if r.passed)
    errored = sum(1 for r in results if r.error is not None)

    mean_quality = _mean([r.overall_score for r in scored])
    total_input = sum(r.usage.input_tokens for r in results)
    total_output = sum(r.usage.output_tokens for r in results)
    total_cost = tracker.total_cost(project)

    cost_evaluation = evaluate_cost(
        model,
        total_input,
        total_output,
        quality_score=mean_quality,
        attempts=max(1, total),
        passed=min(passed, max(1, total)),
    )

    unsafe = tuple(
        r.item_id
        for r in results
        if r.safety is not None and r.safety.verdict is SafetyVerdict.UNSAFE
    )
    review = tuple(
        r.item_id
        for r in results
        if r.safety is not None and r.safety.verdict is SafetyVerdict.NEEDS_REVIEW
    )

    return HarnessReport(
        dataset_name=eval_set.name,
        model=model,
        threshold=threshold,
        total=total,
        passed=passed,
        failed=total - passed - errored,
        errored=errored,
        pass_rate=1.0 if total == 0 else passed / total,
        mean_quality=mean_quality,
        mean_relevance=_mean([r.relevance for r in scored]),
        mean_coherence=_mean([r.coherence for r in scored]),
        mean_token_f1=_mean([r.quality.token_overlap.f1 for r in scored if r.quality is not None]),
        mean_keyword_coverage=_mean(
            [
                r.quality.keyword_coverage.coverage
                for r in scored
                if r.quality is not None and r.quality.keyword_coverage is not None
            ]
        ),
        unsafe_items=unsafe,
        review_items=review,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_cost_usd=total_cost,
        cost_by_model=tracker.cost_by_model(),
        cost_evaluation=cost_evaluation,
        budget=None if budget_usd is None else check_budget(total_cost, budget_usd),
        tokens_estimated=any(r.usage.estimated for r in results),
        results=tuple(results),
    )
