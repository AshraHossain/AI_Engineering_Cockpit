"""CLI: run a prompt workload, then render the cockpit monitoring dashboard.

Unlike projects 01-05, this project is *not* standalone -- importing the
Tier 2 monitoring framework is the whole point. It runs a fixed batch of
prompts through :class:`~instrumented_client.InstrumentedClient`, which
records spend into a ``CostTracker`` and latency into a
``PerformanceTracker``, then hands both to
:func:`~cockpit.monitoring.dashboard.build_dashboard_snapshot` and prints the
rendered report.

Run it with:
    uv run python src/main.py --dry-run
    uv run python src/main.py --models gemini-2.5-flash --budget 0.01
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

from cockpit.evaluation.cost_evaluation import BudgetCheck, check_budget  # noqa: E402
from cockpit.monitoring.cost_tracking import PRICING_VERIFIED_DATE, CostTracker  # noqa: E402
from cockpit.monitoring.dashboard import (  # noqa: E402
    build_dashboard_snapshot,
    render_dashboard_text,
)
from cockpit.monitoring.performance_metrics import PerformanceTracker  # noqa: E402
from instrumented_client import InstrumentedClient, build_gemini_generate_fn  # noqa: E402
from workload import (  # noqa: E402
    DEFAULT_MODELS,
    DEFAULT_WORKLOAD,
    make_offline_clock,
    make_offline_generate_fn,
    run_workload,
)

logger = logging.getLogger(__name__)


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
        prog="08-cost-dashboard",
        description="Run a prompt workload and render a cost + latency dashboard.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use a canned offline model and a fake clock. No API key needed.",
    )
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="Comma-separated model ids to run the workload against.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Run only the first N prompts of the workload.",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=None,
        help="Fail with exit code 2 if total spend exceeds this many US dollars.",
    )
    return parser


def parse_models(raw: str) -> list[str]:
    """Split a comma-separated ``--models`` value into model ids.

    Args:
        raw: The raw flag value.

    Returns:
        The non-empty, whitespace-stripped model ids.

    Raises:
        ValueError: If no model id survives parsing.
    """
    models = [part.strip() for part in raw.split(",") if part.strip()]
    if not models:
        raise ValueError("--models must name at least one model.")
    return models


def format_budget_line(check: BudgetCheck) -> str:
    """Format a one-line verdict for a budget check.

    Args:
        check: The result of :func:`check_budget`.

    Returns:
        A human-readable verdict line.
    """
    verdict = "WITHIN BUDGET" if check.within_budget else "OVER BUDGET"
    return (
        f"BUDGET: {verdict} -- spent ${check.projected_cost_usd:.6f} of "
        f"${check.budget_usd:.6f} ({check.utilization * 100:.1f}% used, "
        f"${check.overage_usd:.6f} over)"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: run the workload and print the dashboard.

    Args:
        argv: Argument vector to parse. Defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 on success, 1 on a configuration error, 2 if a
        ``--budget`` cap was exceeded.
    """
    configure_logging()
    args = build_parser().parse_args(argv)

    try:
        models = parse_models(args.models)
    except ValueError as exc:
        logger.error("Argument error: %s", exc)
        return 1

    cost_tracker = CostTracker()

    if args.dry_run:
        generate_fn = make_offline_generate_fn()
        performance_tracker = PerformanceTracker(clock=make_offline_clock())
    else:
        load_dotenv()
        try:
            generate_fn = build_gemini_generate_fn(get_api_key())
        except MissingAPIKeyError as exc:
            logger.error("Configuration error: %s", exc)
            return 1
        performance_tracker = PerformanceTracker()

    items = DEFAULT_WORKLOAD[: args.limit] if args.limit else DEFAULT_WORKLOAD
    client = InstrumentedClient(
        generate_fn=generate_fn,
        cost_tracker=cost_tracker,
        performance_tracker=performance_tracker,
    )

    logger.info("Running %d prompt(s) against %d model(s).", len(items), len(models))
    run_workload(client, items=items, models=models)

    snapshot = build_dashboard_snapshot(cost_tracker, performance_tracker)
    print(render_dashboard_text(snapshot))
    print(f"\nPricing table verified: {PRICING_VERIFIED_DATE} (override it before trusting it).")

    if args.budget is not None:
        check = check_budget(snapshot.total_cost_usd, args.budget)
        print(format_budget_line(check))
        if not check.within_budget:
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
