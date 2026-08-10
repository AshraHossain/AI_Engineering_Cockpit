"""CLI: run the cockpit compliance controls over a synthetic dataset.

Like projects 08 and 10, this project is *not* standalone -- importing the
Tier 3 :mod:`cockpit.security.compliance` framework is the whole point. It
loads ``data/records.json``, projects it into a ``ComplianceContext``, runs
the GDPR, HIPAA, and SOX controls, and prints a findings report.

Exit codes are a **triage policy chosen by the operator**, not a compliance
verdict. ``--fail-on high`` means "this run found something I said I wanted
to be told about today"; it does not mean "non-compliant", and exit 0 does
not mean "compliant". See the README.

    0  the run completed and nothing reached --fail-on
    1  configuration error (bad dataset, unreadable file, bad arguments)
    2  the run completed and at least one finding reached --fail-on

Run it with:
    uv run python src/main.py
    uv run python src/main.py --framework sox --fail-on critical
    uv run python src/main.py --dry-run --omit approvals
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

from cockpit.security.compliance import (  # noqa: E402
    ComplianceFramework,
    Severity,
    export_subject_data_json,
    generate_compliance_report,
)
from dataset import (  # noqa: E402
    DEFAULT_DATASET_PATH,
    ComplianceDataset,
    DatasetError,
    InputGroup,
    load_dataset,
    omit_inputs,
)
from report import build_context, render_coverage_plan, run_report  # noqa: E402

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_THRESHOLD_REACHED = 2

NEVER = "never"


def configure_logging(level: str | None = None) -> None:
    """Configure a minimal stderr logging setup for this script.

    Args:
        level: Logging level name. Defaults to the LOG_LEVEL environment
            variable, or "WARNING" if unset -- the report is the output, and
            INFO chatter interleaved with it makes the report harder to read
            and harder to redirect to a file cleanly.
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
        prog="13-compliance-report",
        description=(
            "Run the cockpit's GDPR/HIPAA/SOX technical controls over a synthetic "
            "dataset and print the findings. This reports findings; it does not "
            "certify compliance."
        ),
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help="Path to the JSON dataset. Defaults to data/records.json.",
    )
    parser.add_argument(
        "--framework",
        choices=[str(framework) for framework in ComplianceFramework],
        default=None,
        help="Scope the run to one framework. Defaults to all three.",
    )
    parser.add_argument(
        "--fail-on",
        choices=[*(str(severity) for severity in Severity), NEVER],
        default=str(Severity.HIGH),
        help=(
            "Exit 2 if any finding reaches this severity. This is an operator "
            "triage policy, not a compliance verdict. 'never' always exits 0."
        ),
    )
    parser.add_argument(
        "--omit",
        action="append",
        choices=[str(group) for group in InputGroup],
        default=None,
        help=(
            "Drop an input group before running, to see what a report looks like "
            "when a control has nothing to evaluate. Repeatable."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate the dataset and print the coverage plan only -- which controls "
            "can run and which cannot, with no findings. Always exits 0."
        ),
    )
    parser.add_argument(
        "--subject-export",
        metavar="SUBJECT_ID",
        default=None,
        help=(
            "Instead of a report, print that subject's records as portability JSON "
            "(GDPR right of access). Prints personal data by design; the dataset is "
            "synthetic."
        ),
    )
    return parser


def resolve_frameworks(name: str | None) -> tuple[ComplianceFramework, ...] | None:
    """Turn the ``--framework`` value into the framework tuple to run.

    Args:
        name: The flag value, or None for all frameworks.

    Returns:
        A one-element tuple, or None meaning "all of them".
    """
    return None if name is None else (ComplianceFramework(name),)


def resolve_threshold(name: str) -> Severity | None:
    """Turn the ``--fail-on`` value into a triage threshold.

    Args:
        name: The flag value, possibly ``"never"``.

    Returns:
        The threshold severity, or None if no finding should ever set the
        exit code.
    """
    return None if name == NEVER else Severity(name)


def load_inputs(path: Path, omit: Sequence[str] | None) -> ComplianceDataset:
    """Load the dataset and apply any ``--omit`` reductions.

    Args:
        path: Path to the JSON dataset.
        omit: Input group names to drop.

    Returns:
        The loaded, possibly reduced dataset.

    Raises:
        DatasetError: If the dataset is missing or malformed.
    """
    dataset = load_dataset(path)
    if not omit:
        return dataset
    return omit_inputs(dataset, [InputGroup(name) for name in omit])


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: load the dataset, run the controls, print the report.

    Args:
        argv: Argument vector to parse. Defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 if the run completed with nothing at or above
        ``--fail-on``, 1 on a configuration error, 2 if the run completed and
        at least one finding reached ``--fail-on``. Exit 2 records that an
        operator's triage threshold was crossed; it is not a determination
        that anything is non-compliant.
    """
    configure_logging()
    # Loaded for LOG_LEVEL only. There is deliberately no API key here: every
    # control in this project runs locally against a local file.
    load_dotenv()
    args = build_parser().parse_args(argv)

    try:
        dataset = load_inputs(args.data, args.omit)
    except DatasetError as exc:
        logger.error("Dataset error: %s", exc)
        return EXIT_CONFIG_ERROR

    if args.subject_export is not None:
        try:
            print(export_subject_data_json(dataset.records, args.subject_export))
        except ValueError as exc:
            logger.error("Export error: %s", exc)
            return EXIT_CONFIG_ERROR
        return EXIT_OK

    frameworks = resolve_frameworks(args.framework)

    if args.dry_run:
        report = generate_compliance_report(build_context(dataset), frameworks)
        print(render_coverage_plan(report, source=dataset.source))
        return EXIT_OK

    run = run_report(dataset, frameworks=frameworks, threshold=resolve_threshold(args.fail_on))
    print(run.rendered_text)

    if not run.findings_at_or_above_threshold:
        return EXIT_OK

    print(
        f"\nExit 2: {len(run.findings_at_or_above_threshold)} finding(s) reached the "
        f"--fail-on {args.fail_on} threshold this operator set. That is a triage "
        f"policy, not a compliance verdict."
    )
    return EXIT_THRESHOLD_REACHED


if __name__ == "__main__":
    sys.exit(main())
