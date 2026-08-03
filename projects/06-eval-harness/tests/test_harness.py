"""Tests for the evaluation harness: dataset loading, scoring, and the CLI.

No API key and no network access are used anywhere in this module. Every
model call goes through an injected fake ``generate_fn``, which is the whole
point of the harness taking one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cockpit.evaluation.safety_evaluation import SafetyVerdict
from cockpit.monitoring.cost_tracking import CostTracker

import main
from dataset import (
    DatasetError,
    EvalItem,
    EvalSet,
    filter_items,
    load_eval_set,
    parse_eval_set,
)
from harness import (
    DEFAULT_PASS_THRESHOLD,
    TokenUsage,
    build_prompt,
    estimate_token_usage,
    evaluate_item,
    run_harness,
)

ITEM = EvalItem(
    item_id="q-test-cosine",
    question="What does cosine similarity measure between two vectors?",
    reference_answer=(
        "Cosine similarity measures the cosine of the angle between two vectors, so it "
        "compares their direction rather than their magnitude."
    ),
    required_keywords=("angle", "direction", "magnitude"),
)

GOOD_ANSWER = (
    "It measures the cosine of the angle between two vectors, comparing their direction "
    "rather than their magnitude."
)
BAD_ANSWER = "Bananas are yellow."


class FakeGenerator:
    """A scripted ``generate_fn`` that also reports fixed token usage.

    Attributes:
        prompts: Every prompt it was called with, in order.
    """

    def __init__(self, answer: str, *, usage: TokenUsage | None = None) -> None:
        """Initialize the fake.

        Args:
            answer: The answer to return for every prompt.
            usage: Token usage to report. Defaults to a small fixed pair.
        """
        self._answer = answer
        self._usage = usage or TokenUsage(input_tokens=40, output_tokens=25)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        """Record the prompt and return the scripted answer.

        Args:
            prompt: The prompt the harness rendered.

        Returns:
            The scripted answer.
        """
        self.prompts.append(prompt)
        return self._answer

    def last_usage(self) -> TokenUsage:
        """Report the fixed token usage.

        Returns:
            The configured :class:`TokenUsage`.
        """
        return self._usage


def _eval_set(*items: EvalItem) -> EvalSet:
    """Build a small in-memory eval set.

    Args:
        *items: Items to include.

    Returns:
        The assembled :class:`EvalSet`.
    """
    return EvalSet(name="test-set", description="fixture", items=items)


# --------------------------------------------------------------------------
# dataset
# --------------------------------------------------------------------------


def test_bundled_dataset_loads_and_validates() -> None:
    """The shipped data/eval_set.json parses and has usable items."""
    eval_set = load_eval_set()
    assert len(eval_set) >= 8
    assert len({item.item_id for item in eval_set.items}) == len(eval_set)
    for item in eval_set.items:
        assert item.question.strip()
        assert item.reference_answer.strip()


def test_parse_eval_set_rejects_non_object() -> None:
    """A JSON array at the root is rejected with a clear message."""
    with pytest.raises(DatasetError, match="must be a JSON object"):
        parse_eval_set([])


def test_parse_eval_set_rejects_empty_items() -> None:
    """An empty items list is rejected rather than silently scoring nothing."""
    with pytest.raises(DatasetError, match="at least one item"):
        parse_eval_set({"name": "x", "items": []})


def test_parse_eval_set_rejects_missing_field() -> None:
    """An item missing reference_answer names the offending field."""
    payload = {"name": "x", "items": [{"item_id": "a", "question": "q?"}]}
    with pytest.raises(DatasetError, match="reference_answer"):
        parse_eval_set(payload)


def test_parse_eval_set_rejects_duplicate_ids() -> None:
    """Two items sharing an item_id is a hard error."""
    item = {"item_id": "a", "question": "q?", "reference_answer": "r"}
    with pytest.raises(DatasetError, match="duplicate item_id"):
        parse_eval_set({"name": "x", "items": [item, dict(item)]})


def test_parse_eval_set_rejects_string_keywords() -> None:
    """A bare string for required_keywords is rejected, not iterated per-char."""
    payload = {
        "name": "x",
        "items": [
            {"item_id": "a", "question": "q?", "reference_answer": "r", "required_keywords": "abc"}
        ],
    }
    with pytest.raises(DatasetError, match="list of strings"):
        parse_eval_set(payload)


def test_load_eval_set_reports_missing_file(tmp_path: Path) -> None:
    """A missing dataset file raises DatasetError, not FileNotFoundError."""
    with pytest.raises(DatasetError, match="not found"):
        load_eval_set(tmp_path / "nope.json")


def test_load_eval_set_reports_bad_json(tmp_path: Path) -> None:
    """Unparseable JSON raises DatasetError naming the file."""
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(DatasetError, match="not valid JSON"):
        load_eval_set(path)


def test_load_eval_set_round_trips_written_file(tmp_path: Path) -> None:
    """A hand-written dataset file loads back into equivalent EvalItems."""
    path = tmp_path / "set.json"
    path.write_text(
        json.dumps(
            {
                "name": "round-trip",
                "items": [
                    {
                        "item_id": "a",
                        "question": "q?",
                        "reference_answer": "r",
                        "required_keywords": ["k", "  "],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    eval_set = load_eval_set(path)
    assert eval_set.name == "round-trip"
    assert eval_set.items[0].required_keywords == ("k",)


def test_filter_items_limits_and_validates() -> None:
    """filter_items truncates, keeps order, and rejects a non-positive limit."""
    items = (ITEM, EvalItem("b", "q", "r"), EvalItem("c", "q", "r"))
    assert filter_items(items, 2) == items[:2]
    assert filter_items(items, None) == items
    with pytest.raises(ValueError, match="positive"):
        filter_items(items, 0)


# --------------------------------------------------------------------------
# per-item evaluation
# --------------------------------------------------------------------------


def test_build_prompt_contains_the_question() -> None:
    """The rendered prompt carries the question on a Question: line."""
    prompt = build_prompt(ITEM)
    assert f"Question: {ITEM.question}" in prompt
    assert main.extract_question(prompt) == ITEM.question


def test_evaluate_item_scores_a_good_answer_as_passing() -> None:
    """A close paraphrase clears the threshold and is judged safe."""
    result = evaluate_item(ITEM, FakeGenerator(GOOD_ANSWER), tracker=CostTracker())
    assert result.passed
    assert result.error is None
    assert result.quality is not None
    assert result.quality.overall_score >= DEFAULT_PASS_THRESHOLD
    assert result.quality.keyword_coverage is not None
    assert result.quality.keyword_coverage.coverage == 1.0
    assert result.safety is not None
    assert result.safety.verdict is SafetyVerdict.SAFE


def test_evaluate_item_scores_an_unrelated_answer_as_failing() -> None:
    """An off-topic answer misses every keyword and fails the threshold."""
    result = evaluate_item(ITEM, FakeGenerator(BAD_ANSWER), tracker=CostTracker())
    assert not result.passed
    assert result.quality is not None
    assert result.quality.overall_score < DEFAULT_PASS_THRESHOLD
    assert result.quality.keyword_coverage is not None
    assert result.quality.keyword_coverage.missing == ["angle", "direction", "magnitude"]


def test_evaluate_item_records_cost_on_the_tracker() -> None:
    """Token usage from the reporter is priced and recorded, not estimated."""
    tracker = CostTracker()
    generator = FakeGenerator(GOOD_ANSWER, usage=TokenUsage(1000, 500))
    result = evaluate_item(ITEM, generator, usage_reporter=generator, tracker=tracker)

    assert result.usage == TokenUsage(1000, 500)
    assert not result.usage.estimated
    assert result.cost_usd > 0
    assert tracker.total_tokens() == (1000, 500)
    assert tracker.total_cost() == pytest.approx(result.cost_usd)


def test_evaluate_item_falls_back_to_estimated_tokens() -> None:
    """With no usage reporter the harness estimates tokens and says so."""
    result = evaluate_item(ITEM, FakeGenerator(GOOD_ANSWER), tracker=CostTracker())
    assert result.usage.estimated
    assert result.usage.input_tokens > 0


def test_estimate_token_usage_scales_with_length() -> None:
    """The estimator is monotonic in text length and always flags itself."""
    short = estimate_token_usage("x" * 40, "y" * 40)
    long = estimate_token_usage("x" * 400, "y" * 400)
    assert long.input_tokens > short.input_tokens
    assert long.output_tokens > short.output_tokens
    assert short.estimated


def test_evaluate_item_captures_generator_exceptions() -> None:
    """A raising pipeline errors that one item instead of aborting the run."""

    def boom(prompt: str) -> str:
        raise RuntimeError("upstream 503")

    result = evaluate_item(ITEM, boom, tracker=CostTracker())
    assert not result.passed
    assert result.error is not None
    assert "upstream 503" in result.error
    assert result.quality is None
    assert result.cost_usd == 0.0


def test_evaluate_item_rejects_non_string_responses() -> None:
    """A pipeline returning the wrong type is an error, not a crash."""

    def wrong_type(prompt: str) -> str:
        return 42  # type: ignore[return-value]

    result = evaluate_item(ITEM, wrong_type, tracker=CostTracker())
    assert result.error is not None
    assert "expected str" in result.error


# --------------------------------------------------------------------------
# full harness
# --------------------------------------------------------------------------


def test_run_harness_all_passing() -> None:
    """A pipeline that answers every item well scores a 100% pass rate."""
    eval_set = _eval_set(ITEM, ITEM.__class__("q2", ITEM.question, ITEM.reference_answer, ()))
    generator = FakeGenerator(GOOD_ANSWER)
    report = run_harness(eval_set, generator, usage_reporter=generator)

    assert report.total == 2
    assert report.passed == 2
    assert report.failed == 0
    assert report.errored == 0
    assert report.pass_rate == 1.0
    assert report.mean_quality > DEFAULT_PASS_THRESHOLD
    assert len(generator.prompts) == 2


def test_run_harness_all_failing() -> None:
    """A pipeline that answers badly scores a 0% pass rate."""
    generator = FakeGenerator(BAD_ANSWER)
    report = run_harness(_eval_set(ITEM), generator, usage_reporter=generator)

    assert report.passed == 0
    assert report.failed == 1
    assert report.pass_rate == 0.0
    assert report.mean_quality < DEFAULT_PASS_THRESHOLD


def test_run_harness_aggregates_cost_and_tokens() -> None:
    """Usage accumulates across items and lands in the report and tracker."""
    tracker = CostTracker()
    generator = FakeGenerator(GOOD_ANSWER, usage=TokenUsage(100, 50))
    eval_set = _eval_set(ITEM, EvalItem("q2", "What is a token?", "A unit of text."))

    report = run_harness(eval_set, generator, usage_reporter=generator, tracker=tracker)

    assert report.total_input_tokens == 200
    assert report.total_output_tokens == 100
    assert report.total_cost_usd == pytest.approx(tracker.total_cost())
    assert report.cost_by_model == {"gemini-2.5-flash": pytest.approx(report.total_cost_usd)}
    assert not report.tokens_estimated
    assert report.cost_evaluation is not None
    assert report.cost_evaluation.cost_per_success_usd > 0


def test_run_harness_reports_budget_overage() -> None:
    """A cap smaller than the run's spend is reported as over budget."""
    generator = FakeGenerator(GOOD_ANSWER, usage=TokenUsage(1_000_000, 1_000_000))
    report = run_harness(_eval_set(ITEM), generator, usage_reporter=generator, budget_usd=0.000001)

    assert report.budget is not None
    assert not report.budget.within_budget
    assert report.budget.overage_usd > 0


def test_run_harness_within_budget() -> None:
    """A generous cap is reported as within budget."""
    generator = FakeGenerator(GOOD_ANSWER, usage=TokenUsage(10, 10))
    report = run_harness(_eval_set(ITEM), generator, usage_reporter=generator, budget_usd=1.0)

    assert report.budget is not None
    assert report.budget.within_budget
    assert report.budget.overage_usd == 0.0


def test_run_harness_counts_errored_items_separately() -> None:
    """An item whose call raises is counted as errored, not merely failed."""

    def flaky(prompt: str) -> str:
        if "cosine" in prompt:
            raise RuntimeError("boom")
        return GOOD_ANSWER

    eval_set = _eval_set(ITEM, EvalItem("q2", "What is a token?", "A unit of text a model reads."))
    report = run_harness(eval_set, flaky)

    assert report.errored == 1
    assert report.total == 2
    assert report.passed + report.failed + report.errored == report.total


def test_run_harness_rejects_bad_threshold() -> None:
    """A threshold outside the unit interval is rejected up front."""
    with pytest.raises(ValueError, match="threshold"):
        run_harness(_eval_set(ITEM), FakeGenerator(GOOD_ANSWER), threshold=1.5)


def test_run_harness_rejects_negative_budget() -> None:
    """A negative budget cap is rejected up front."""
    with pytest.raises(ValueError, match="budget_usd"):
        run_harness(_eval_set(ITEM), FakeGenerator(GOOD_ANSWER), budget_usd=-1.0)


def test_run_harness_flags_unsafe_answers() -> None:
    """A response leaking PII is flagged unsafe and cannot pass."""
    leaky = FakeGenerator("Contact the admin at admin@example.com for the answer.")
    report = run_harness(_eval_set(ITEM), leaky, usage_reporter=leaky)

    assert report.unsafe_items == ("q-test-cosine",)
    assert report.passed == 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_offline_generator_answers_from_the_bank() -> None:
    """The dry-run generator resolves canned answers via the prompt's question."""
    generator = main.OfflineGenerator()
    answer = generator(build_prompt(ITEM))

    assert "cosine" in answer.lower()
    assert generator.last_usage().estimated


def test_offline_generator_falls_back_for_unknown_questions() -> None:
    """An unknown question gets the generic non-answer, not a KeyError."""
    generator = main.OfflineGenerator()
    assert generator(build_prompt(EvalItem("x", "Who won in 1911?", "r"))) == main.FALLBACK_ANSWER


def test_get_api_key_rejects_blank() -> None:
    """A blank key is treated as missing and points the user at --dry-run."""
    with pytest.raises(main.MissingAPIKeyError, match="dry-run"):
        main.get_api_key({"GEMINI_API_KEY": "   "})


def test_get_api_key_returns_value() -> None:
    """A present key is returned stripped."""
    assert main.get_api_key({"GEMINI_API_KEY": " abc "}) == "abc"


def test_cli_dry_run_prints_a_scorecard(capsys: pytest.CaptureFixture[str]) -> None:
    """--dry-run runs end-to-end with no key and prints the full scorecard."""
    exit_code = main.main(["--dry-run"])
    out = capsys.readouterr().out

    assert exit_code in (0, 2)
    assert "EVAL SCORECARD" in out
    assert "QUALITY (mean across scored items)" in out
    assert "SAFETY" in out
    assert "COST" in out
    assert "PER-ITEM" in out


def test_cli_dry_run_respects_limit(capsys: pytest.CaptureFixture[str]) -> None:
    """--limit truncates the run to the first N items."""
    main.main(["--dry-run", "--limit", "3"])
    out = capsys.readouterr().out

    assert "Items                 3" in out


def test_cli_reports_a_missing_dataset(capsys: pytest.CaptureFixture[str]) -> None:
    """A bad --dataset path exits 1 with a message on stderr."""
    exit_code = main.main(["--dry-run", "--dataset", "does-not-exist.json"])

    assert exit_code == 1
    assert "Dataset error" in capsys.readouterr().err


def test_cli_requires_a_key_when_not_dry_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Live mode with no key exits 1 rather than attempting a network call."""
    monkeypatch.setattr(main, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("GEMINI_API_KEY", "")

    exit_code = main.main([])

    assert exit_code == 1
    assert "Configuration error" in capsys.readouterr().err


def test_format_scorecard_marks_estimated_tokens() -> None:
    """The cost block says so when token counts were estimated."""
    report = run_harness(_eval_set(ITEM), FakeGenerator(GOOD_ANSWER))
    assert "estimated from text length" in main.format_scorecard(report)
