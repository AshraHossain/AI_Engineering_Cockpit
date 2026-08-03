"""CLI for the red-team runner: attack a target and report whether it held.

This is the only module in the project that touches an SDK. The runner and
the report renderer take injected targets, which is why
``tests/test_runner.py`` exercises full campaigns with no key and no network.

Two modes:

* ``--dry-run`` attacks the offline stubs from :mod:`target`. Pick which one
  with ``--fake refusing`` (the hardened control, should hold) or
  ``--fake naive`` (the undefended control, should be shredded).
* the default live mode attacks :class:`~target.GeminiTarget`, with a canary
  token planted in its system prompt so system-prompt-leak payloads are
  objectively adjudicable.

Run it with:
    uv run python src/main.py --dry-run --fake naive
    uv run python src/main.py --skip-edge-cases
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# The repo root is package=false by design, so it isn't installed into this
# project's venv -- put it on sys.path to import the cockpit frameworks.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

from cockpit.red_teaming.adversarial_tests import Target  # noqa: E402
from report import render_report  # noqa: E402
from runner import build_canary_target_prompt, run_red_team  # noqa: E402
from target import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    BrittleTarget,
    GeminiTarget,
    NaiveTarget,
    RefusingTarget,
)

FAKE_TARGETS = ("refusing", "naive", "brittle")


class MissingAPIKeyError(RuntimeError):
    """Raised when GEMINI_API_KEY is missing or empty in the environment."""


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


def build_fake_target(kind: str, system_prompt: str) -> tuple[Target, str]:
    """Construct one of the offline stub targets.

    Args:
        kind: One of ``"refusing"``, ``"naive"``, or ``"brittle"``.
        system_prompt: System prompt handed to the stub, canary included.

    Returns:
        A ``(target, label)`` pair.

    Raises:
        ValueError: If ``kind`` is not a known stub name.
    """
    if kind == "refusing":
        return RefusingTarget(), "RefusingTarget (offline, hardened control)"
    if kind == "naive":
        return NaiveTarget(system_prompt), "NaiveTarget (offline, undefended control)"
    if kind == "brittle":
        return BrittleTarget(), "BrittleTarget (offline, crashes on boundary inputs)"
    raise ValueError(f"Unknown fake target {kind!r}. Choose from: {', '.join(FAKE_TARGETS)}.")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser.

    Returns:
        The configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="red-team-runner",
        description=(
            "Run the cockpit injection corpus and edge-case suite against a target, "
            "and report whether its defenses held."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Attack an offline stub target; needs no API key and no network.",
    )
    parser.add_argument(
        "--fake",
        choices=FAKE_TARGETS,
        default="refusing",
        help="Which offline stub to attack in --dry-run mode (default: refusing).",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Model to attack (default: {DEFAULT_MODEL})."
    )
    parser.add_argument(
        "--system-prompt",
        default=DEFAULT_SYSTEM_PROMPT,
        help="System prompt for the target under attack. A canary is planted in it.",
    )
    parser.add_argument(
        "--skip-edge-cases",
        action="store_true",
        help="Skip the edge-case suite. Worth doing live: it sends 100k-character inputs.",
    )
    parser.add_argument(
        "--skip-injection", action="store_true", help="Skip the injection campaign."
    )
    parser.add_argument(
        "--gap-only",
        action="store_true",
        help="Only report the defense-coverage gap. Contacts no target at all.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Per-case wall-clock budget for edge cases, in seconds (default: 30).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: run the red-team probes and print the report.

    Args:
        argv: Command-line arguments, excluding the program name. Defaults
            to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 if every probe that ran came back clean, 1 on a
        configuration error, 2 if an attack landed or an edge case failed.
    """
    args = build_parser().parse_args(argv)
    load_dotenv()

    canary, instrumented_prompt = build_canary_target_prompt(args.system_prompt)

    if args.gap_only:
        report = run_red_team(
            lambda prompt: "",
            target_name="(no target -- defense scan only)",
            include_injection=False,
            include_edge_cases=False,
        )
        print(render_report(report))
        return 0

    target: Target
    if args.dry_run:
        try:
            target, label = build_fake_target(args.fake, instrumented_prompt)
        except ValueError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 1
    else:
        try:
            target = GeminiTarget(get_api_key(), instrumented_prompt, model=args.model)
        except MissingAPIKeyError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 1
        label = f"GeminiTarget ({args.model})"

    try:
        report = run_red_team(
            target,
            target_name=label,
            canary=canary,
            include_injection=not args.skip_injection,
            include_edge_cases=not args.skip_edge_cases,
            timeout_seconds=args.timeout,
        )
    except (TypeError, ValueError) as exc:
        print(f"Runner error: {exc}", file=sys.stderr)
        return 1

    print(render_report(report))
    return 0 if report.clean else 2


if __name__ == "__main__":
    sys.exit(main())
