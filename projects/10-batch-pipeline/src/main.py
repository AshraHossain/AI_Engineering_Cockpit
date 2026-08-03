"""CLI: run the batch pipeline over a JSONL file and print a run summary.

Like project 08 and unlike projects 01-05, this project deliberately imports
the cockpit frameworks -- the rate limiter and retry decorator from Tier 1
utils, and the cost/latency trackers from Tier 2 monitoring.

Run it with:
    uv run python src/main.py --dry-run
    uv run python src/main.py --dry-run --limit 5 --rate 4
    uv run python src/main.py --rate 2 --limit 10
"""

from __future__ import annotations

import sys
from pathlib import Path

# The repo root is package=false by design, so it isn't installed into this
# project's venv -- put it on sys.path to import the cockpit frameworks.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import argparse  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
from collections.abc import Sequence  # noqa: E402

from dotenv import load_dotenv  # noqa: E402
from pipeline import (  # noqa: E402
    DEFAULT_INPUT_PATH,
    BatchResult,
    PipelineConfig,
    ProcessFn,
    build_gemini_process_fn,
    load_records,
    make_offline_process_fn,
    run_batch,
)

from cockpit.monitoring.cost_tracking import CostTracker  # noqa: E402
from cockpit.monitoring.performance_metrics import PerformanceTracker  # noqa: E402
from cockpit.utils.error_handling import ConfigurationError  # noqa: E402
from cockpit.utils.rate_limiting import RateLimiter  # noqa: E402

logger = logging.getLogger(__name__)

# Ids the offline fake misbehaves on, so `--dry-run` actually demonstrates
# the retry path and the partial-failure path instead of a clean sweep.
DEMO_FLAKY_IDS = ("r04", "r13")
DEMO_FAILING_IDS = ("r11",)


class MissingAPIKeyError(RuntimeError):
    """Raised when GEMINI_API_KEY is missing or empty in the environment."""


def configure_logging(level: str | None = None) -> None:
    """Configure a minimal stdout logging setup for this script.

    Args:
        level: Logging level name. Defaults to the LOG_LEVEL environment
            variable, or "INFO" if unset.
    """
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=level or os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def get_api_key(env: dict[str, str] | None = None) -> str:
    """Read the Gemini API key from the environment.

    Args:
        env: Optional mapping to read from instead of ``os.environ``
            (mainly for testing).

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


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="10-batch-pipeline",
        description="Bulk-process records with rate limiting, retries, and cost accounting.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use a canned offline classifier. No API key needed.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N records of the input file.",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=5.0,
        help="Sustained records per second allowed through the rate limiter.",
    )
    parser.add_argument(
        "--inputs",
        type=Path,
        default=DEFAULT_INPUT_PATH,
        help="Path to the JSON Lines input file.",
    )
    return parser


def format_summary(result: BatchResult) -> str:
    """Format a human-readable summary of a completed batch run.

    Args:
        result: The batch outcome to summarize.

    Returns:
        A multi-line summary string, without a trailing newline.
    """
    lines = [
        "=" * 62,
        "BATCH PIPELINE SUMMARY",
        "=" * 62,
        f"  Records processed   {len(result.results):>12,}",
        f"  Succeeded           {len(result.succeeded):>12,}",
        f"  Failed              {len(result.failed):>12,}",
        f"  Attempts (w/retry)  {result.total_attempts:>12,}",
        f"  Throttled           {result.throttled_count:>12,}",
        f"  Total cost          {'$' + format(result.total_cost_usd, '.6f'):>12}",
        f"  Elapsed             {format(result.elapsed_seconds, '.3f') + 's':>12}",
        f"  Throughput          {format(result.records_per_second, '.2f') + '/s':>12}",
    ]
    if result.errors:
        lines.append("")
        lines.append(f"  FAILED RECORDS ({len(result.errors)})")
        for record_id, message in result.errors:
            lines.append(f"    {record_id:<8} {message}")
    lines.append("=" * 62)
    return "\n".join(lines)


def build_process_fn(dry_run: bool) -> ProcessFn:
    """Choose the unit of work for this run.

    Args:
        dry_run: Whether to use the canned offline classifier.

    Returns:
        The ``process_fn`` to inject into the pipeline.

    Raises:
        MissingAPIKeyError: If a live run is requested without an API key.
    """
    if dry_run:
        return make_offline_process_fn(flaky_ids=DEMO_FLAKY_IDS, failing_ids=DEMO_FAILING_IDS)
    load_dotenv()
    return build_gemini_process_fn(get_api_key())


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: run the batch and print its summary.

    Args:
        argv: Argument vector to parse. Defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 if every record succeeded, 1 on a configuration
        error, 3 if the run completed with at least one failed record.
    """
    configure_logging()
    args = build_parser().parse_args(argv)

    try:
        records = load_records(args.inputs, limit=args.limit)
        process_fn = build_process_fn(args.dry_run)
    except (MissingAPIKeyError, ConfigurationError, FileNotFoundError) as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    config = PipelineConfig(
        rate_limiter=RateLimiter(capacity=max(1, int(args.rate)), refill_rate_per_second=args.rate),
        # A short backoff keeps the demo snappy; production would start higher.
        initial_delay_seconds=0.2,
    )
    cost_tracker = CostTracker()
    performance_tracker = PerformanceTracker()

    logger.info("Processing %d record(s) at %.1f/s.", len(records), args.rate)
    result = run_batch(
        records,
        process_fn,
        cost_tracker=cost_tracker,
        performance_tracker=performance_tracker,
        config=config,
    )

    print(format_summary(result))
    summary = performance_tracker.summary(config.operation)
    print(
        f"  Latency p50/p95: {summary.p50_seconds:.3f}s / {summary.p95_seconds:.3f}s "
        f"across {summary.sample_count} attempt(s), "
        f"error rate {summary.error_rate * 100:.1f}%"
    )
    return 3 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
