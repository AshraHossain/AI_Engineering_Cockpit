"""CLI: turn an audit event stream into a ranked, evidence-carrying report.

Like projects 08 and 10 and unlike 01-05, this project is not standalone --
importing the Tier 3 security framework is the whole point. It reads events,
hands them to :mod:`cockpit.security.threat_detection`, and prints what came
back with the evidence attached.

Exit codes:
    0   no in-scope finding reached ``--fail-at``.
    1   a configuration error: bad flag value, missing or malformed stream,
        or an audit chain that failed verification.
    2   at least one in-scope finding reached ``--fail-at`` (HIGH by
        default). This is the "something fired" signal for a scheduled run;
        keep it distinct from 1 so a broken input file can never be mistaken
        for a clean scan.

Run it with:
    uv run python src/main.py --dry-run
    uv run python src/main.py --dry-run --actor dana@corp --evidence 0
    uv run python src/main.py --dry-run --category brute_force
    uv run python src/main.py --dry-run --since 2026-08-08T11:55:00Z
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
from datetime import UTC, datetime  # noqa: E402

from dotenv import load_dotenv  # noqa: E402
from ingest import (  # noqa: E402
    DEFAULT_EVENTS_PATH,
    ChainIntegrityError,
    EventFormatError,
    StreamEvent,
    events_from_chain_file,
    latest_timestamp,
    load_events,
    parse_timestamp,
)
from monitor import (  # noqa: E402
    DEFAULT_FAIL_AT,
    MonitorReport,
    build_detector,
    findings_at_or_above,
    parse_category,
    parse_level,
    scan,
)
from report import DEFAULT_MAX_EVIDENCE, render_exit_note, render_report  # noqa: E402

from cockpit.security.threat_detection import ThreatCategory, ThreatLevel  # noqa: E402

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_THREAT_FOUND = 2


def configure_logging(level: str | None = None) -> None:
    """Configure a minimal stderr logging setup for this script.

    Args:
        level: Logging level name. Defaults to the LOG_LEVEL environment
            variable, or "WARNING" if unset. Quieter than the other
            projects on purpose: the report is the output, and the
            framework logs a warning of its own for every scan that fires.
    """
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=level or os.environ.get("LOG_LEVEL", "WARNING"),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        stream=sys.stderr,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="14-threat-monitor",
        description="Run behavioral threat detectors over an audit event stream.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Replay a saved stream, anchoring the evaluation time to that "
            "stream's newest event instead of the wall clock. Deterministic; "
            "no API key, no network."
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--events",
        type=Path,
        default=DEFAULT_EVENTS_PATH,
        help="Path to a JSON Lines event stream (default: the bundled demo stream).",
    )
    source.add_argument(
        "--chain",
        type=Path,
        default=None,
        help=(
            "Path to an AuditLog hash-chain sink. The chain is verified "
            "before analysis and a broken one is refused."
        ),
    )
    parser.add_argument(
        "--actor",
        default=None,
        help="Show only findings about this actor id. Detection still sees every actor.",
    )
    parser.add_argument(
        "--category",
        default=None,
        help=(
            "Show only findings in this category: "
            + ", ".join(category.value for category in ThreatCategory)
            + "."
        ),
    )
    parser.add_argument(
        "--since",
        default=None,
        help=(
            "Drop events before this ISO-8601 instant. This trims the "
            "baseline history too, so a narrow value can quiet the rate detector."
        ),
    )
    parser.add_argument(
        "--fail-at",
        default=DEFAULT_FAIL_AT.value,
        help="Exit 2 if any in-scope finding reaches this severity (default: high).",
    )
    parser.add_argument(
        "--evidence",
        type=int,
        default=DEFAULT_MAX_EVIDENCE,
        help="Evidence events to print per finding; 0 prints all of them.",
    )
    return parser


class CliError(RuntimeError):
    """Raised for a user-correctable problem with the invocation."""


def resolve_arguments(
    args: argparse.Namespace,
) -> tuple[ThreatLevel, ThreatCategory | None, datetime | None]:
    """Validate and convert the string-valued flags.

    Args:
        args: The parsed namespace.

    Returns:
        The parsed ``--fail-at`` level, ``--category``, and ``--since``.

    Raises:
        CliError: If any of the three values cannot be parsed.
    """
    try:
        fail_at = parse_level(args.fail_at)
        category = parse_category(args.category) if args.category else None
    except ValueError as exc:
        raise CliError(str(exc)) from exc

    since: datetime | None = None
    if args.since:
        try:
            since = parse_timestamp(args.since)
        except ValueError as exc:
            raise CliError(f"--since {args.since!r} is not a valid ISO-8601 datetime.") from exc

    if args.evidence < 0:
        raise CliError("--evidence must be zero (all) or a positive count.")
    return fail_at, category, since


def collect_events(args: argparse.Namespace, since: datetime | None) -> list[StreamEvent]:
    """Load the event stream named by the flags.

    Args:
        args: The parsed namespace.
        since: Lower time bound, or None.

    Returns:
        The events, oldest first.

    Raises:
        CliError: If the stream is missing, malformed, or fails chain
            verification.
    """
    try:
        if args.chain is not None:
            return events_from_chain_file(args.chain, since=since)
        return load_events(args.events, since=since)
    except (EventFormatError, ChainIntegrityError, FileNotFoundError) as exc:
        raise CliError(str(exc)) from exc


def resolve_now(events: Sequence[StreamEvent], *, dry_run: bool) -> datetime:
    """Choose the instant every detector is evaluated against.

    Args:
        events: The loaded stream.
        dry_run: Whether this is a replay.

    Returns:
        The stream's newest timestamp under ``--dry-run``, otherwise the
        current UTC time.

    Raises:
        CliError: If a replay is requested over an empty stream, which has
            no timestamp to anchor to.
    """
    if not dry_run:
        return datetime.now(UTC)
    newest = latest_timestamp(events)
    if newest is None:
        raise CliError("--dry-run needs a non-empty stream to anchor the evaluation time to.")
    return newest


def run(args: argparse.Namespace) -> tuple[MonitorReport, ThreatLevel, str]:
    """Load, scan, and render, without touching stdout or the exit code.

    Args:
        args: The parsed namespace.

    Returns:
        The report, the configured fail-at level, and the rendered text.

    Raises:
        CliError: If any flag or input is unusable.
    """
    fail_at, category, since = resolve_arguments(args)
    events = collect_events(args, since)
    now = resolve_now(events, dry_run=args.dry_run)

    report = scan(
        events,
        now=now,
        detector=build_detector(),
        actor=args.actor,
        category=category,
    )
    return report, fail_at, render_report(report, max_events=args.evidence)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: scan a stream and print the report.

    Args:
        argv: Argument vector to parse. Defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 clean, 1 configuration error, 2 at least one
        in-scope finding reached ``--fail-at``.
    """
    configure_logging()
    args = build_parser().parse_args(argv)
    if not args.dry_run:
        # Nothing here needs an API key; loading .env keeps LOG_LEVEL and any
        # future deployment settings in the same place as the other projects.
        load_dotenv()

    try:
        report, fail_at, text = run(args)
    except CliError as exc:
        logger.error("Configuration error: %s", exc)
        return EXIT_CONFIG_ERROR

    print(text)
    breaching = findings_at_or_above(report, fail_at)
    print(render_exit_note(fail_at, len(breaching)))
    return EXIT_THREAT_FOUND if breaching else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
