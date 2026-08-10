"""Render findings as a text report an analyst can actually act on.

Two rules shape everything here.

**Every finding shows its evidence.** A line that says "anomalous activity
for dana@corp" cannot be investigated, so it gets muted, and a muted
detector is worse than no detector because it looks like cover. The
framework makes this possible by putting the triggering events on
:class:`~cockpit.security.threat_detection.ThreatFinding`; this module's job
is to not throw them away. When there are too many to print, the head *and*
the tail are shown -- the first event says when it started, the last says
whether it is still going -- and the elision says exactly how many were cut
and how to see them all.

**Nothing here prints.** Every function returns a string. That keeps the
renderer testable by assertion rather than by capturing stdout, and leaves
the decision of where output goes to the CLI.
"""

from __future__ import annotations

import textwrap
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from cockpit.security.threat_detection import (
    SecurityEvent,
    ThreatCategory,
    ThreatFinding,
    ThreatLevel,
)
from monitor import MonitorReport

DEFAULT_WIDTH = 78
DEFAULT_MAX_EVIDENCE = 5

# What to do about each category, in one line. Written as an instruction
# rather than a description: a finding that does not say what to do next
# leaves the reader to invent a response under time pressure.
RECOMMENDED_ACTIONS: Mapping[ThreatCategory, str] = {
    ThreatCategory.BRUTE_FORCE: (
        "Lock the account and force a credential reset. Check whether any "
        "attempt in the window succeeded before the failures stopped."
    ),
    ThreatCategory.DATA_EXFILTRATION: (
        "Suspend the session and hold the exported resources for review. "
        "Confirm with the actor's team whether a bulk pull was expected."
    ),
    ThreatCategory.PRIVILEGE_ESCALATION: (
        "Revoke the granted role and audit who approved it. A successful "
        "out-of-role action means the access control failed open, not that "
        "it blocked an attempt."
    ),
    ThreatCategory.ANOMALOUS_RATE: (
        "Rate-limit the actor rather than blocking it, then compare against "
        "deploy and batch schedules before escalating."
    ),
}

_FALLBACK_ACTION = "Review the evidence below and confirm the activity was expected."


def _iso(moment: datetime) -> str:
    """Format an instant for the report.

    Args:
        moment: The instant to format.

    Returns:
        An ISO-8601 string.
    """
    return moment.isoformat()


def _wrap(text: str, *, width: int, indent: str) -> list[str]:
    """Wrap prose to the report width at a fixed indent.

    Args:
        text: The prose to wrap.
        width: Total line width including the indent.
        indent: Leading whitespace for every produced line.

    Returns:
        The wrapped lines.
    """
    return textwrap.wrap(
        text,
        width=max(width, len(indent) + 20),
        initial_indent=indent,
        subsequent_indent=indent,
    ) or [indent.rstrip()]


def _metadata_suffix(event: SecurityEvent) -> str:
    """Render an event's metadata, if it carries any.

    The detectors ignore metadata; an analyst reading evidence usually wants
    the source address, so it is printed when present.

    Args:
        event: The event to inspect.

    Returns:
        A " key=value key=value" suffix, or an empty string.
    """
    metadata: Any = getattr(event, "metadata", None)
    if not isinstance(metadata, Mapping) or not metadata:
        return ""
    pairs = " ".join(f"{key}={value}" for key, value in sorted(metadata.items()))
    return f"  [{pairs}]"


def render_evidence_line(event: SecurityEvent) -> str:
    """Render one evidence event as a single fixed-column line.

    Args:
        event: The event to render.

    Returns:
        A line holding when, what, on what, and with what result.
    """
    return (
        f"{_iso(event.timestamp):<26} {event.action:<13} "
        f"{event.resource:<24} {event.outcome}{_metadata_suffix(event)}"
    )


def render_evidence(
    evidence: Sequence[SecurityEvent],
    *,
    max_events: int = DEFAULT_MAX_EVIDENCE,
    indent: str = "      ",
) -> list[str]:
    """Render an evidence block, eliding the middle when it is long.

    Args:
        evidence: The triggering events, oldest first.
        max_events: How many to print. ``0`` prints all of them. When
            truncating, the newest event is always kept: the first event
            says when the behavior started, the last says whether it has
            stopped.
        indent: Leading whitespace for every produced line.

    Returns:
        The rendered lines, including the elision marker when one applies.
    """
    if not evidence:
        # The framework never builds an evidence-free finding, so reaching
        # this means something upstream constructed one by hand.
        return [f"{indent}(no evidence recorded -- this finding is not investigable)"]

    if max_events <= 0 or len(evidence) <= max_events:
        return [f"{indent}{render_evidence_line(event)}" for event in evidence]

    head_count = max(max_events - 1, 1)
    head = list(evidence[:head_count])
    tail = list(evidence[-1:]) if max_events > 1 else []
    hidden = len(evidence) - len(head) - len(tail)

    lines = [f"{indent}{render_evidence_line(event)}" for event in head]
    if hidden > 0:
        lines.append(f"{indent}... {hidden} more event(s) hidden; re-run with --evidence 0")
    lines.extend(f"{indent}{render_evidence_line(event)}" for event in tail)
    return lines


def render_finding(
    finding: ThreatFinding,
    *,
    index: int | None = None,
    max_events: int = DEFAULT_MAX_EVIDENCE,
    width: int = DEFAULT_WIDTH,
) -> str:
    """Render one finding: what fired, for whom, why, and what to do.

    Args:
        finding: The finding to render.
        index: 1-based position in the report, printed as ``[n]`` when set.
        max_events: How many evidence events to print; ``0`` prints all.
        width: Line width for wrapped prose.

    Returns:
        A multi-line block without a trailing newline.
    """
    label = f"[{index}] " if index is not None else ""
    header = (
        f"{label}{finding.level.value.upper():<8}  {finding.category.value:<22}"
        f"actor={finding.actor_id}"
    )
    action = RECOMMENDED_ACTIONS.get(finding.category, _FALLBACK_ACTION)

    lines = [header, ""]
    lines.extend(_wrap(finding.explanation, width=width, indent="    "))
    lines.append("")
    lines.extend(_wrap(f"Recommended action: {action}", width=width, indent="    "))
    lines.append("")

    shown = len(finding.evidence) if max_events <= 0 else min(max_events, len(finding.evidence))
    lines.append(f"    Evidence -- {len(finding.evidence)} event(s), showing {shown}:")
    lines.extend(render_evidence(finding.evidence, max_events=max_events))
    return "\n".join(lines)


def _scope_line(report: MonitorReport) -> str:
    """Describe the actor/category scope a report was rendered under.

    Args:
        report: The report to describe.

    Returns:
        A one-line scope description.
    """
    actor = report.actor_scope or "all actors"
    category = report.category_scope.value if report.category_scope else "all categories"
    scope = f"{actor} / {category}"
    if report.suppressed_count:
        scope += f"  ({report.suppressed_count} finding(s) hidden by this scope)"
    return scope


def render_summary(report: MonitorReport, *, width: int = DEFAULT_WIDTH) -> str:
    """Render the report header block.

    Args:
        report: The report to summarize.
        width: Width of the rule lines.

    Returns:
        A multi-line header without a trailing newline.
    """
    rule = "=" * width
    severity = report.highest_level.value.upper() if report.findings else "none"
    return "\n".join(
        [
            rule,
            "THREAT MONITOR",
            rule,
            f"  Evaluated at    {_iso(report.evaluated_at)}",
            f"  Events scanned  {report.events_scanned:,} across "
            f"{len(report.actors_seen)} actor(s)",
            f"  Scope           {_scope_line(report)}",
            f"  Findings        {len(report.findings)} (highest severity: {severity})",
            rule,
        ]
    )


def render_report(
    report: MonitorReport,
    *,
    max_events: int = DEFAULT_MAX_EVIDENCE,
    width: int = DEFAULT_WIDTH,
) -> str:
    """Render a full monitor report.

    Args:
        report: The report to render.
        max_events: How many evidence events to print per finding; ``0``
            prints all of them.
        width: Line width for rules and wrapped prose.

    Returns:
        The report text, without a trailing newline.
    """
    blocks = [render_summary(report, width=width)]

    if not report.findings:
        blocks.append("")
        blocks.append(
            "No findings. Every detector ran and none fired. Note that a clean "
            "report means the configured thresholds were not crossed, not that "
            "nothing happened -- see README.md on tuning."
        )
        blocks.append("=" * width)
        return "\n".join(blocks)

    for index, finding in enumerate(report.findings, start=1):
        blocks.append("")
        blocks.append(render_finding(finding, index=index, max_events=max_events, width=width))
        blocks.append("")
        blocks.append("-" * width)

    # When the run is scoped to one actor, the roster is restricted to that
    # actor. Naming everyone else contradicts the Scope line printed above,
    # and a report claiming to be about one principal should not enumerate
    # who else was in the stream.
    seen = report.actors_seen
    if report.actor_scope:
        seen = tuple(actor for actor in seen if actor == report.actor_scope)

    clean = tuple(actor for actor in seen if actor not in _flagged(report))
    blocks.append("")
    blocks.append(f"  Flagged: {', '.join(_flagged(report)) or 'none'}")
    blocks.append(f"  Clean:   {', '.join(clean) or 'none'}")
    blocks.append("=" * width)
    return "\n".join(blocks)


def _flagged(report: MonitorReport) -> tuple[str, ...]:
    """List the actors named by a report's in-scope findings.

    Args:
        report: The report to inspect.

    Returns:
        The distinct actor ids, sorted.
    """
    return tuple(sorted({finding.actor_id for finding in report.findings}))


def render_exit_note(threshold: ThreatLevel, breaching: int) -> str:
    """Render the one-line verdict explaining the process exit code.

    Args:
        threshold: The severity the run was configured to fail at.
        breaching: How many in-scope findings reached it.

    Returns:
        A single line stating the verdict.
    """
    if breaching:
        return (
            f"VERDICT: FAIL -- {breaching} finding(s) at or above "
            f"{threshold.value.upper()}; exiting 2."
        )
    return f"VERDICT: PASS -- nothing at or above {threshold.value.upper()}; exiting 0."


__all__ = [
    "DEFAULT_MAX_EVIDENCE",
    "DEFAULT_WIDTH",
    "RECOMMENDED_ACTIONS",
    "render_evidence",
    "render_evidence_line",
    "render_exit_note",
    "render_finding",
    "render_report",
    "render_summary",
]
