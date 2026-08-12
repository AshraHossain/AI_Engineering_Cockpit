#!/usr/bin/env python3
"""Scan project trees for retired model IDs and end-of-life provider SDKs.

Why this exists: a mocked test suite cannot detect that the model you call
was retired last quarter. The tests stub the SDK boundary, so they keep
passing while the application is dead. Every example project in this repo
was broken this way -- 167 green tests, five projects that could not make a
single successful API call.

This is a text scan, so it is fast, needs no credentials, and finds the rot
before someone runs the code. Point it at a directory of projects and run
it on a schedule; provider deprecations are continuous, so a one-time fix
decays but a recurring check does not.

Usage:
    python scripts/check_model_rot.py D:/AI_Engineering_Cockpit/projects
    python scripts/check_model_rot.py . --quiet
    python scripts/check_model_rot.py ~/code --json
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# The rot list. Dated on purpose: this file is a snapshot of what was true on
# LIST_VERIFIED_DATE, not a permanent truth. Re-check it against the
# providers' own deprecation pages before trusting an all-clear.
#
# Sources:
#   https://ai.google.dev/gemini-api/docs/changelog
#   https://platform.openai.com/docs/deprecations
#   https://docs.anthropic.com/en/docs/about-claude/model-deprecations
# ---------------------------------------------------------------------------
LIST_VERIFIED_DATE = "2026-08-12"

SEVERITY_BROKEN = "BROKEN"
SEVERITY_EOL = "EOL"
SEVERITY_STALE = "STALE"


@dataclass(frozen=True)
class RotPattern:
    """One thing worth flagging in source.

    Attributes:
        pattern: Compiled regex matched against each line.
        label: Short name for the problem.
        severity: BROKEN (fails today), EOL (works, unsupported), or STALE
            (still resolves, but worth revisiting).
        fix: What to do about it.
    """

    pattern: re.Pattern[str]
    label: str
    severity: str
    fix: str


ROT_PATTERNS: tuple[RotPattern, ...] = (
    RotPattern(
        re.compile(r"\bgemini-1\.5-(?:flash|pro)\b"),
        "gemini-1.5-* retired",
        SEVERITY_BROKEN,
        "Use gemini-2.5-flash (or gemini-flash-latest). Verified 404 against the live API.",
    ),
    RotPattern(
        re.compile(r"\btext-embedding-004\b"),
        "text-embedding-004 retired",
        SEVERITY_BROKEN,
        "Use gemini-embedding-001 (3072 dims). Verified 404 against the live API.",
    ),
    RotPattern(
        re.compile(
            r"\bimport\s+google\.generativeai\b|\bfrom\s+google\.generativeai\b"
            r"|google-generativeai"
        ),
        "google-generativeai is end-of-life",
        SEVERITY_EOL,
        "Migrate to google-genai: `from google import genai; genai.Client(api_key=...)`, "
        "then client.models.generate_content(model=..., contents=...).",
    ),
    RotPattern(
        re.compile(r"\bgenai\.configure\s*\(|\bgenai\.GenerativeModel\s*\("),
        "old google-generativeai call shape",
        SEVERITY_EOL,
        "google-genai has no configure()/GenerativeModel(); build a Client and pass "
        "model= on each call.",
    ),
    RotPattern(
        re.compile(
            r"\bclaude-3-(?:opus|sonnet|haiku)-2024\d{4}\b"
            r"|anthropic\.claude-3-(?:opus|sonnet|haiku)-2024\d{4}"
        ),
        "Claude 3 dated snapshot",
        SEVERITY_STALE,
        "Claude 3 snapshots are deprecated on several platforms. Check the provider's "
        "deprecation page and move to a current model.",
    ),
    RotPattern(
        re.compile(r"\btext-(?:davinci|curie|babbage|ada)-\d{3}\b"),
        "OpenAI completions-era model",
        SEVERITY_BROKEN,
        "These were shut down. Use a current chat model.",
    ),
    RotPattern(
        re.compile(r"\bgpt-4-32k\b|\bgpt-4-vision-preview\b|\bgpt-4-1106-preview\b"),
        "retired GPT-4 preview/32k",
        SEVERITY_BROKEN,
        "Use a current GPT-4o/GPT-5 family model.",
    ),
    RotPattern(
        re.compile(r"\bgpt-3\.5-turbo\b"),
        "gpt-3.5-turbo",
        SEVERITY_STALE,
        "Still served, but superseded and poor value. Newer small models cost less "
        "and perform better.",
    ),
    RotPattern(
        re.compile(r"\bgemini-2\.0-flash\b"),
        "gemini-2.0-flash deprecated",
        SEVERITY_STALE,
        "Google's pricing page lists a 2026-06-01 shutdown. Move to gemini-2.5-flash.",
    ),
    RotPattern(
        re.compile(r"\bopenai\.ChatCompletion\b|\bopenai\.Completion\b"),
        "openai<1.0 call shape",
        SEVERITY_EOL,
        "Pre-1.0 openai API. Use `from openai import OpenAI; client.chat.completions.create(...)`.",
    ),
)

# Directories that are never the user's own code.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        ".env",
        "site-packages",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "htmlcov",
        "dist",
        "build",
        ".tox",
        ".eggs",
        "vendor",
        ".next",
        "target",
    }
)

SCAN_SUFFIXES = frozenset(
    {".py", ".toml", ".txt", ".cfg", ".ini", ".yaml", ".yml", ".json", ".md", ".env.example"}
)

MAX_FILE_BYTES = 2_000_000


@dataclass(frozen=True)
class Finding:
    """One flagged line.

    Attributes:
        project: Top-level project the file belongs to.
        path: File path, relative to the scan root.
        line_number: 1-indexed line.
        label: Which pattern matched.
        severity: BROKEN / EOL / STALE.
        excerpt: The matching line, trimmed.
        fix: Remediation guidance.
    """

    project: str
    path: str
    line_number: int
    label: str
    severity: str
    excerpt: str
    fix: str


@dataclass
class ScanStats:
    """Counters describing what the scan looked at.

    Attributes:
        files_scanned: Files actually read.
        files_skipped: Files skipped for size or encoding.
        projects_seen: Distinct top-level project directories.
    """

    files_scanned: int = 0
    files_skipped: int = 0
    projects_seen: set[str] = field(default_factory=set)


def should_skip(path: Path, root: Path) -> bool:
    """Report whether a path lies inside a directory worth ignoring.

    Args:
        path: Candidate file path.
        root: Scan root, used to keep the check relative.

    Returns:
        True if any path component is in :data:`SKIP_DIRS`.
    """
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(part in SKIP_DIRS for part in parts)


def project_of(path: Path, root: Path) -> str:
    """Name the top-level project a file belongs to.

    Args:
        path: File path.
        root: Scan root.

    Returns:
        The first path component below the root, or "." for a file sitting
        directly in it.
    """
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return str(path.parent)
    return parts[0] if len(parts) > 1 else "."


def scan_file(path: Path, root: Path) -> list[Finding]:
    """Scan one file for every rot pattern.

    Args:
        path: File to scan.
        root: Scan root, for relative reporting.

    Returns:
        Findings, possibly empty.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    findings: list[Finding] = []
    relative = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    project = project_of(path, root)

    for number, line in enumerate(text.splitlines(), start=1):
        findings.extend(
            Finding(
                project=project,
                path=relative,
                line_number=number,
                label=rot.label,
                severity=rot.severity,
                excerpt=line.strip()[:120],
                fix=rot.fix,
            )
            for rot in ROT_PATTERNS
            if rot.pattern.search(line)
        )
    return findings


def scan_tree(root: Path) -> tuple[list[Finding], ScanStats]:
    """Walk a directory tree and collect findings.

    Args:
        root: Directory to scan.

    Returns:
        A ``(findings, stats)`` pair.

    Raises:
        NotADirectoryError: If ``root`` is not a directory.
    """
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a directory")

    findings: list[Finding] = []
    stats = ScanStats()

    # os.walk with in-place pruning of dirnames, not rglob. rglob descends
    # into every .venv and node_modules before the skip check can reject the
    # files inside, which on a tree of real projects means walking hundreds of
    # thousands of irrelevant paths. Pruning turns minutes into seconds, and a
    # scanner nobody waits for is a scanner nobody runs.
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        directory = Path(current)

        for filename in filenames:
            path = directory / filename
            if path.suffix.lower() not in SCAN_SUFFIXES and filename != ".env.example":
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    stats.files_skipped += 1
                    continue
            except OSError:
                stats.files_skipped += 1
                continue

            stats.files_scanned += 1
            stats.projects_seen.add(project_of(path, root))
            findings.extend(scan_file(path, root))

    return findings, stats


def render_report(findings: list[Finding], stats: ScanStats, root: Path) -> str:
    """Render findings as a readable report grouped by project.

    Args:
        findings: Findings to report.
        stats: Scan counters.
        root: The scanned root.

    Returns:
        The report text.
    """
    width = 78
    order = {SEVERITY_BROKEN: 0, SEVERITY_EOL: 1, SEVERITY_STALE: 2}
    lines = [
        "=" * width,
        "MODEL / SDK ROT SCAN",
        "=" * width,
        f"  Root         {root}",
        f"  Scanned      {stats.files_scanned} files across {len(stats.projects_seen)} project(s)",
        f"  Rot list     verified {LIST_VERIFIED_DATE} -- re-check against provider docs",
        "",
    ]

    if not findings:
        lines += [
            "  No retired models or end-of-life SDKs found.",
            "",
            "  This is a text scan against a dated list. It cannot see a model that",
            "  was retired after that date, and it cannot confirm your credentials",
            "  work. Running the code is still the only real proof.",
            "=" * width,
        ]
        return "\n".join(lines)

    by_project: dict[str, list[Finding]] = {}
    for finding in findings:
        by_project.setdefault(finding.project, []).append(finding)

    counts = {severity: sum(1 for f in findings if f.severity == severity) for severity in order}
    lines.append(
        f"  {counts[SEVERITY_BROKEN]} BROKEN, {counts[SEVERITY_EOL]} EOL, "
        f"{counts[SEVERITY_STALE]} STALE across {len(by_project)} project(s)"
    )
    lines.append("")

    def project_rank(item: tuple[str, list[Finding]]) -> tuple[int, str]:
        worst = min(order[f.severity] for f in item[1])
        return (worst, item[0])

    for project, items in sorted(by_project.items(), key=project_rank):
        lines.append("-" * width)
        lines.append(f"  {project}")
        lines.append("-" * width)
        for finding in sorted(items, key=lambda f: (order[f.severity], f.path, f.line_number)):
            lines.append(f"    [{finding.severity:<6}] {finding.label}")
            lines.append(f"      {finding.path}:{finding.line_number}")
            lines.append(f"      | {finding.excerpt}")
            lines.append(f"      -> {finding.fix}")
            lines.append("")

    lines += [
        "=" * width,
        "  BROKEN  fails against the live API today",
        "  EOL     still runs, but the SDK is unsupported and will stop",
        "  STALE   resolves for now; revisit before it becomes BROKEN",
        "=" * width,
    ]
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default=".", help="Directory to scan")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a report")
    parser.add_argument("--quiet", action="store_true", help="Only print if something was found")
    parser.add_argument(
        "--fail-at",
        choices=[SEVERITY_BROKEN, SEVERITY_EOL, SEVERITY_STALE],
        default=SEVERITY_EOL,
        help="Exit non-zero at or above this severity (default: EOL)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        0 if nothing at or above --fail-at was found, 1 if something was,
        2 on a usage error.
    """
    # Excerpts are copied verbatim out of scanned files, so the report can
    # contain any character those files do. On a cp1252 Windows console that
    # crashes the print outright -- a scanner must not die on the code it is
    # inspecting. Degrade unencodable characters instead of failing.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    args = parse_args(argv)
    root = Path(args.root).expanduser().resolve()

    try:
        findings, stats = scan_tree(root)
    except NotADirectoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([f.__dict__ for f in findings], indent=2))
    elif findings or not args.quiet:
        print(render_report(findings, stats, root))

    order = {SEVERITY_BROKEN: 0, SEVERITY_EOL: 1, SEVERITY_STALE: 2}
    threshold = order[args.fail_at]
    return 1 if any(order[f.severity] <= threshold for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
