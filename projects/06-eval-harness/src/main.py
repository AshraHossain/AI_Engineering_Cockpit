"""CLI for the evaluation harness: run a Q&A pipeline and print a scorecard.

This is the only module in the project that touches an SDK. Everything the
harness needs is injected from here, which is why ``tests/test_harness.py``
can exercise the whole pipeline with no key and no network.

Two modes:

* ``--dry-run`` wires in :class:`OfflineGenerator`, a canned answer bank, so
  the project is demonstrable with no API key at all.
* the default live mode wires in :class:`GeminiGenerator`, which calls
  ``gemini-2.5-flash`` and reports the provider's real token counts back to
  the harness so the cost column is exact rather than estimated.

Run it with:
    uv run python src/main.py --dry-run
    uv run python src/main.py --limit 5 --budget 0.05
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

# The repo root is package=false by design, so it isn't installed into this
# project's venv -- put it on sys.path to import the cockpit frameworks.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

from cockpit.monitoring.cost_tracking import CostTracker  # noqa: E402
from dataset import (  # noqa: E402
    DEFAULT_DATASET_PATH,
    DatasetError,
    EvalSet,
    filter_items,
    load_eval_set,
)
from harness import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_PASS_THRESHOLD,
    HarnessReport,
    TokenUsage,
    run_harness,
)

SEPARATOR = "=" * 78
THIN = "-" * 78

#: Canned answers for ``--dry-run``, keyed by the item's question. Written to
#: be realistic rather than perfect: several are good paraphrases, one is a
#: vague non-answer, and one is confidently wrong -- so the dry-run scorecard
#: shows a real spread instead of a meaningless 100%.
CANNED_ANSWERS: dict[str, str] = {
    "In HTTP, what does the status code 404 mean?": (
        "HTTP 404 means Not Found. The server was reached but has no resource at that URL."
    ),
    "What does cosine similarity measure between two vectors?": (
        "It measures the cosine of the angle between two vectors, comparing their "
        "direction rather than their magnitude, on a scale from -1 to 1."
    ),
    "What makes an operation idempotent?": (
        "An operation is idempotent when applying it more than once has the same effect "
        "as applying it once, so repeating the request leaves the state unchanged."
    ),
    "How does git rebase differ from git merge?": (
        "Merge joins branches with a merge commit and keeps the original history, while "
        "rebase replays commits onto another branch for a linear history."
    ),
    "What is Retrieval-Augmented Generation?": (
        "It retrieves relevant passages from an external corpus and puts them in the "
        "prompt as context, so the model can answer from documents it never trained on."
    ),
    "What is a prompt injection attack?": (
        "An attacker hides instructions inside untrusted input so the model follows them "
        "instead of the application's system prompt, which can leak the system prompt."
    ),
    "What is a text embedding?": (
        "A fixed-length vector of numbers representing text, arranged so texts with "
        "similar meaning sit close together in the vector space."
    ),
    "What is the difference between a unit test and an integration test?": (
        "A unit test exercises one component in isolation with fake dependencies; an "
        "integration test runs several real components together."
    ),
    "What does the temperature parameter control in language model sampling?": (
        "It is a setting you can tune. Higher values change the output somewhat."
    ),
    "What is a token in the context of a language model?": (
        "A token is the unit of text a model processes, usually a word fragment. "
        "Context window limits and billing are both counted in tokens."
    ),
    "What does ACID stand for in database transactions?": (
        "ACID stands for Availability, Caching, Indexing, and Distribution."
    ),
    "What is a hallucination in a language model's output?": (
        "Output that is fluent and confident but factually wrong or unsupported by any "
        "source, because the model generates plausible text rather than looking facts up."
    ),
}

FALLBACK_ANSWER = "I don't have enough information to answer that."


class MissingAPIKeyError(RuntimeError):
    """Raised when GEMINI_API_KEY is missing or empty in the environment."""


class OfflineGenerator:
    """A canned, deterministic stand-in for a real Q&A pipeline.

    Satisfies both the ``GenerateFn`` and ``UsageReporter`` protocols, so
    ``--dry-run`` exercises exactly the same harness code path as live mode.
    Token counts are reported as estimates derived from text length, since
    no provider is involved.

    Attributes:
        answers: Question-to-answer bank consulted for each prompt.
    """

    def __init__(self, answers: dict[str, str] | None = None) -> None:
        """Initialize the generator.

        Args:
            answers: Question-to-answer bank. Defaults to
                :data:`CANNED_ANSWERS`.
        """
        self.answers = CANNED_ANSWERS if answers is None else answers
        self._last_usage = TokenUsage(0, 0, estimated=True)

    def __call__(self, prompt: str) -> str:
        """Look up a canned answer for the question inside a prompt.

        Args:
            prompt: The rendered harness prompt.

        Returns:
            The canned answer, or a generic non-answer for unknown questions.
        """
        question = extract_question(prompt)
        answer = self.answers.get(question, FALLBACK_ANSWER)
        self._last_usage = TokenUsage(
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(answer) // 4),
            estimated=True,
        )
        return answer

    def last_usage(self) -> TokenUsage:
        """Report the token usage of the most recent call.

        Returns:
            The estimated :class:`~harness.TokenUsage` for the last call.
        """
        return self._last_usage


class GeminiGenerator:
    """A live Gemini-backed Q&A pipeline with real usage reporting.

    Satisfies both the ``GenerateFn`` and ``UsageReporter`` protocols. The
    ``google.genai`` import is deferred to construction time so importing
    this module never requires the SDK.

    Attributes:
        model: Name of the Gemini model to generate with.
    """

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        """Build an authenticated client.

        Args:
            api_key: The Gemini API key to authenticate with.
            model: Name of the Gemini model to generate with.
        """
        from google import genai

        self._client: Any = genai.Client(api_key=api_key)
        self.model = model
        self._last_usage = TokenUsage(0, 0)

    def __call__(self, prompt: str) -> str:
        """Send a prompt to Gemini and return the answer text.

        Args:
            prompt: The rendered harness prompt.

        Returns:
            The model's answer text, or an empty string if it returned none.
        """
        response = self._client.models.generate_content(model=self.model, contents=prompt)
        usage = getattr(response, "usage_metadata", None)
        self._last_usage = TokenUsage(
            input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
        )
        return getattr(response, "text", "") or ""

    def last_usage(self) -> TokenUsage:
        """Report the provider's token counts for the most recent call.

        Returns:
            The reported :class:`~harness.TokenUsage` for the last call.
        """
        return self._last_usage


def extract_question(prompt: str) -> str:
    """Recover the question text from a rendered harness prompt.

    Args:
        prompt: The rendered prompt, containing a ``Question:`` line.

    Returns:
        The question text, or an empty string if the prompt has no
        ``Question:`` line.
    """
    for line in prompt.splitlines():
        if line.startswith("Question:"):
            return line[len("Question:") :].strip()
    return ""


def get_api_key(env: dict[str, str] | None = None) -> str:
    """Read the Gemini API key from the environment.

    Args:
        env: Optional mapping to read from instead of ``os.environ``.

    Returns:
        The value of the GEMINI_API_KEY environment variable.

    Raises:
        MissingAPIKeyError: If GEMINI_API_KEY is unset or blank.
    """
    source = env if env is not None else os.environ
    api_key = source.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise MissingAPIKeyError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key, "
            "or run with --dry-run."
        )
    return api_key


def format_scorecard(report: HarnessReport) -> str:
    """Render a harness report as a human-readable scorecard.

    Args:
        report: The report to render.

    Returns:
        The formatted multi-line scorecard.
    """
    lines: list[str] = [
        SEPARATOR,
        f"EVAL SCORECARD  --  {report.dataset_name}",
        SEPARATOR,
        f"Model                 {report.model}",
        f"Pass threshold        {report.threshold:.2f} overall quality",
        "",
        f"Items                 {report.total}",
        f"Passed                {report.passed}",
        f"Failed                {report.failed}",
        f"Errored               {report.errored}",
        f"Pass rate             {report.pass_rate:.1%}",
        "",
        "QUALITY (mean across scored items)",
        f"  overall             {report.mean_quality:.3f}",
        f"  token F1            {report.mean_token_f1:.3f}",
        f"  keyword coverage    {report.mean_keyword_coverage:.3f}",
        f"  relevance           {report.mean_relevance:.3f}",
        f"  coherence           {report.mean_coherence:.3f}",
        "",
        "SAFETY",
        f"  unsafe              {len(report.unsafe_items)}"
        + (f"  {', '.join(report.unsafe_items)}" if report.unsafe_items else ""),
        f"  needs review        {len(report.review_items)}"
        + (f"  {', '.join(report.review_items)}" if report.review_items else ""),
        "",
        "COST" + ("  (token counts estimated from text length)" if report.tokens_estimated else ""),
        f"  input tokens        {report.total_input_tokens:,}",
        f"  output tokens       {report.total_output_tokens:,}",
        f"  total              ${report.total_cost_usd:.6f}",
    ]

    if report.cost_evaluation is not None:
        lines.append(f"  per passed answer  ${report.cost_evaluation.cost_per_success_usd:.6f}")
        lines.append(f"  quality per dollar  {report.cost_evaluation.quality_per_dollar:,.1f}")
    if report.budget is not None:
        verdict = "within budget" if report.budget.within_budget else "OVER BUDGET"
        lines.append(
            f"  budget             ${report.budget.budget_usd:.6f} "
            f"({report.budget.utilization:.1%} used, {verdict})"
        )

    lines.extend(["", "PER-ITEM", THIN, f"{'item':<34}{'score':>7}{'kw':>7}{'safety':>14}  result"])
    for result in report.results:
        coverage = (
            result.quality.keyword_coverage.coverage
            if result.quality is not None and result.quality.keyword_coverage is not None
            else 0.0
        )
        verdict = "errored" if result.safety is None else str(result.safety.verdict)
        status = "PASS" if result.passed else ("ERROR" if result.error else "FAIL")
        lines.append(
            f"{result.item_id:<34}{result.overall_score:>7.3f}{coverage:>7.2f}"
            f"{verdict:>14}  {status}"
        )
        if result.error:
            lines.append(f"{'':<34}  {result.error}")

    lines.append(SEPARATOR)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser.

    Returns:
        The configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="eval-harness",
        description=(
            "Score a Q&A pipeline against a reference dataset using the cockpit "
            "evaluation framework, and print a scorecard."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use the offline canned generator; needs no API key and no network.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help="Path to the JSON evaluation set (default: data/eval_set.json).",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Only run the first N items of the dataset."
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Model to run and price (default: {DEFAULT_MODEL})."
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_PASS_THRESHOLD,
        help=f"Overall quality score required to pass (default: {DEFAULT_PASS_THRESHOLD}).",
    )
    parser.add_argument(
        "--budget", type=float, default=None, help="Optional spend cap in USD to check against."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: run the harness and print the scorecard.

    Args:
        argv: Command-line arguments, excluding the program name. Defaults
            to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 if every item passed, 1 on a configuration or
        dataset error, 2 if the run completed but some items failed.
    """
    args = build_parser().parse_args(argv)
    load_dotenv()

    try:
        full_set = load_eval_set(args.dataset)
    except DatasetError as exc:
        print(f"Dataset error: {exc}", file=sys.stderr)
        return 1

    eval_set = EvalSet(
        name=full_set.name,
        description=full_set.description,
        items=filter_items(full_set.items, args.limit),
    )

    generator: OfflineGenerator | GeminiGenerator
    if args.dry_run:
        generator = OfflineGenerator()
    else:
        try:
            generator = GeminiGenerator(get_api_key(), model=args.model)
        except MissingAPIKeyError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 1

    try:
        report = run_harness(
            eval_set,
            generator,
            usage_reporter=generator,
            tracker=CostTracker(),
            model=args.model,
            threshold=args.threshold,
            budget_usd=args.budget,
        )
    except (ValueError, KeyError) as exc:
        print(f"Harness error: {exc}", file=sys.stderr)
        return 1

    print(format_scorecard(report))
    return 0 if report.passed == report.total else 2


if __name__ == "__main__":
    sys.exit(main())
