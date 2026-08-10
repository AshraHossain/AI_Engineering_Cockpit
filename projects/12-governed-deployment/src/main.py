"""CLI: run the governed deployment scenario and print the transcript.

Like projects 08 and 10, this one is *not* standalone -- importing the Tier 3
governance framework is the whole point. It drives
:class:`~deployment.GovernedDeployment` through
:func:`~scenario.run_scenario` and prints what each control allowed, what it
refused, and why.

There are no model calls anywhere in this project, so ``--dry-run`` does not
mean "skip the network". It means "use the injected fixed clock and fixed
approval request ids", which makes the transcript byte-for-byte
reproducible. Without it the same script runs against the wall clock and
UUID request ids.

Run it with:
    uv run python src/main.py --dry-run
    uv run python src/main.py --model gemini-flash
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
import textwrap  # noqa: E402
from collections.abc import Sequence  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

from dotenv import load_dotenv  # noqa: E402

from scenario import ScenarioResult, ScenarioStep, run_scenario  # noqa: E402

logger = logging.getLogger(__name__)

WIDTH = 78
RULE = "=" * WIDTH
THIN_RULE = "-" * WIDTH
BODY_INDENT = " " * 15

CONTROL_SUMMARY: tuple[tuple[str, str], ...] = (
    ("transition map", "development -> production is not a legal move"),
    ("approval gate", "production needs an APPROVED request bound to that version"),
    ("segregation", "the requester cannot approve their own request"),
    ("distinct votes", "one principal counts once toward an N-of-M threshold"),
    ("RBAC", "only a principal holding 'config:manage' may promote to production"),
    ("audit trail", "every allow and every refusal is hash-chained"),
)


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


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="12-governed-deployment",
        description=(
            "Walk a model version through a deployment pipeline that cannot be "
            "shortcut, printing what each governance control allowed and refused."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use the fixed clock and fixed request ids, for a reproducible transcript.",
    )
    return parser


def wrap_body(text: str, prefix: str = "") -> list[str]:
    """Wrap one detail line to the transcript body column.

    Args:
        text: The text to wrap.
        prefix: Marker placed before the first wrapped line.

    Returns:
        The wrapped lines, already indented.
    """
    return textwrap.wrap(
        f"{prefix}{text}",
        width=WIDTH,
        initial_indent=BODY_INDENT,
        subsequent_indent=BODY_INDENT + " " * len(prefix),
    ) or [BODY_INDENT + prefix]


def render_step(step: ScenarioStep) -> list[str]:
    """Render one scenario step as transcript lines.

    Args:
        step: The step to render.

    Returns:
        The lines describing the attempt and its verdict.
    """
    outcome = step.outcome
    verdict = "ALLOWED" if outcome.allowed else "REFUSED"
    lines = [f"  {step.index:>2}. {verdict}  {outcome.summary} ({outcome.actor_id})"]
    lines.extend(wrap_body(step.narrative, prefix="# "))
    if not outcome.allowed:
        lines.extend(wrap_body(f"{outcome.control} [{outcome.reason}]", prefix="blocked by "))
    lines.extend(wrap_body(outcome.detail, prefix="-> "))
    return lines


def render_transcript(result: ScenarioResult) -> str:
    """Render the whole scenario as a readable transcript.

    Args:
        result: The scenario to render.

    Returns:
        The transcript, ready to print.
    """
    lines = [
        RULE,
        f" GOVERNED DEPLOYMENT -- {result.model_name}",
        RULE,
        " Controls in force:",
    ]
    lines.extend(f"   {name:<16}{note}" for name, note in CONTROL_SUMMARY)
    lines.append(THIN_RULE)

    for step in result.steps:
        lines.extend(render_step(step))

    if result.production_version is None:
        production = "none"
    else:
        current = result.production_version
        production = f"v{current.version} (stage={current.stage})"

    verification = result.verification
    if verification.is_valid:
        integrity = f"VERIFIED -- chain intact across {verification.records_checked} record(s)"
    else:
        integrity = f"BROKEN at record {verification.broken_sequence}: {verification.reason}"

    lines.extend(
        [
            THIN_RULE,
            " SUMMARY",
            f"   steps                  {len(result.steps)} "
            f"(allowed {result.allowed_count}, refused {result.refused_count})",
            f"   production version     {production}",
            f"   versions in production {result.production_count}",
            f"   governance trail       {result.trail_record_count} record(s)",
            f"   trail verification     {integrity}",
            RULE,
        ]
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: run the scenario and print its transcript.

    Args:
        argv: Argument vector to parse. Defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 when every control behaved and the trail
        verifies, 2 if the governance trail failed its integrity check.
    """
    configure_logging()
    args = build_parser().parse_args(argv)

    if not args.dry_run:
        # Nothing here reads a key -- load_dotenv only picks up LOG_LEVEL --
        # but keeping the call means the project behaves like its siblings.
        load_dotenv()

    base_time = None if args.dry_run else datetime.now(UTC)
    result = run_scenario(base_time=base_time, deterministic_ids=args.dry_run)

    print(render_transcript(result))

    if not result.verification.is_valid:
        logger.error("Governance trail integrity check failed: %s", result.verification.reason)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
