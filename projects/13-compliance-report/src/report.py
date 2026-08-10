"""Run the compliance controls over a dataset and render the findings.

What this module renders is a **findings report**. It has no verdict, and it
is not one line of code away from having one:

* There is no pass/fail, no percentage, no score, and no banner. The report
  says what was found and what was looked at, and stops there.
* :data:`~cockpit.security.compliance.DISCLAIMER` is reproduced verbatim at
  the top of every rendered report, because a rendered report is the artifact
  most likely to be forwarded on its own, away from anyone who knows what it
  does and does not mean.
* Controls that did **not** run are printed before the findings, not after.
  A report with three findings and four unevaluated controls is a worse
  result than a report with three findings and full coverage, and it must not
  read as the better one.

:class:`ReportRun` does carry ``findings_at_or_above_threshold``. That is an
operator's triage policy -- "page me at HIGH" -- expressed as a filter over
findings. It is deliberately not stored as a boolean and deliberately not
named after a verdict, because "did anything cross my threshold" and "is this
organization compliant" are not the same question and must not share a field.

Nothing here prints. The CLI owns stdout.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from cockpit.security.compliance import (
    DISCLAIMER,
    ComplianceContext,
    ComplianceFinding,
    ComplianceFramework,
    ComplianceReport,
    Severity,
    generate_compliance_report,
)
from dataset import ComplianceDataset

REPORT_TITLE: Final[str] = "TECHNICAL CONTROL FINDINGS -- 13-compliance-report"
LINE_WIDTH: Final[int] = 78
_RULE: Final[str] = "=" * LINE_WIDTH
_THIN_RULE: Final[str] = "-" * LINE_WIDTH

# Severity is declared INFO -> CRITICAL, so definition order is rank order.
SEVERITY_ORDER: Final[tuple[Severity, ...]] = tuple(Severity)

# Authored here and pre-broken into lines rather than wrapped at render
# time. Wrapping would let a reflow silently split a sentence that other
# code and other people search for verbatim.
_NO_VERDICT_NOTE: Final[tuple[str, ...]] = (
    "  This report states two things and no others: what the checks that ran",
    "  found, and which checks ran. It contains no pass/fail result, no score,",
    "  and no statement that any framework's requirements are or are not met.",
    "  Those determinations belong to counsel, a privacy officer, or an auditor,",
    "  working from context this program cannot see.",
)

_SKIPPED_NOTE: Final[tuple[str, ...]] = (
    "  An unevaluated control found nothing because it never ran. Read the list",
    "  above before reading the findings below: missing coverage is the gap most",
    "  likely to matter, and it is invisible in a finding count.",
)

_DRY_RUN_NOTE: Final[tuple[str, ...]] = (
    "  Coverage only. Findings were withheld, so nothing here says anything about",
    "  what the controls did or did not find. Re-run without --dry-run to see",
    "  them.",
)

_EMPTY_FRAMEWORK_NOTE: Final[tuple[str, ...]] = (
    "  No findings from the controls that ran. That is a statement about these",
    "  checks, not about this framework's requirements.",
)


@dataclass(frozen=True)
class ReportRun:
    """Everything one run of the controls produced.

    Note the absence of any summary field. There is no ``ok``, no
    ``clean``, no ``status``: a caller that wants to act on this run has to
    look at the findings and the coverage, which is the only honest basis for
    acting on it.

    Attributes:
        report: The :class:`~cockpit.security.compliance.ComplianceReport`
            the framework produced.
        rendered_text: The full rendered report, disclaimer first.
        threshold: The operator's triage threshold, or None if no threshold
            was set.
        findings_at_or_above_threshold: Findings at or above ``threshold``.
            Empty when no threshold was set. This drives the CLI's exit code
            and means exactly "these crossed the line the operator drew".
    """

    report: ComplianceReport
    rendered_text: str
    threshold: Severity | None
    findings_at_or_above_threshold: tuple[ComplianceFinding, ...]


def severity_rank(severity: Severity) -> int:
    """Return the ordinal rank of a severity, INFO lowest.

    Args:
        severity: The severity to rank.

    Returns:
        Its index in :data:`SEVERITY_ORDER`.
    """
    return SEVERITY_ORDER.index(severity)


def build_context(dataset: ComplianceDataset, *, now: datetime | None = None) -> ComplianceContext:
    """Project a dataset into the context the controls evaluate.

    Args:
        dataset: The loaded dataset.
        now: Evaluation time. Defaults to the dataset's own anchor, so the
            record ages the loader resolved and the retention arithmetic the
            controls perform are measured from the same instant.

    Returns:
        The assembled :class:`~cockpit.security.compliance.ComplianceContext`.
    """
    return ComplianceContext(
        records=dataset.records,
        retention_policies=dataset.retention_policies,
        consent=dataset.consent,
        pending_erasure_requests=dataset.pending_erasure_requests,
        designated_phi_fields=dataset.designated_phi_fields,
        purpose_field_map=dict(dataset.purpose_field_map),
        field_accesses=dataset.field_accesses,
        approvals=dataset.approvals,
        audited_change_ids=dataset.audited_change_ids,
        now=now if now is not None else dataset.anchor,
    )


def run_report(
    dataset: ComplianceDataset,
    *,
    frameworks: tuple[ComplianceFramework, ...] | None = None,
    threshold: Severity | None = Severity.HIGH,
    now: datetime | None = None,
) -> ReportRun:
    """Run the requested frameworks' controls and render the result.

    Args:
        dataset: The loaded dataset.
        frameworks: Frameworks to cover; None covers all three.
        threshold: Operator triage threshold. Findings at or above it are
            collected into
            :attr:`ReportRun.findings_at_or_above_threshold`. None collects
            nothing.
        now: Evaluation time; defaults to the dataset's anchor.

    Returns:
        A :class:`ReportRun` holding the report, its rendering, and the
        threshold hits.
    """
    report = generate_compliance_report(build_context(dataset, now=now), frameworks)
    flagged = (
        ()
        if threshold is None
        else tuple(
            finding
            for finding in report.findings
            if severity_rank(finding.severity) >= severity_rank(threshold)
        )
    )
    return ReportRun(
        report=report,
        rendered_text=render_report(report, source=dataset.source),
        threshold=threshold,
        findings_at_or_above_threshold=flagged,
    )


def _wrap(text: str, indent: str = "  ") -> list[str]:
    """Wrap text to the report width at a fixed indent.

    Args:
        text: The text to wrap.
        indent: Leading whitespace applied to every line.

    Returns:
        The wrapped lines.
    """
    return textwrap.wrap(
        text,
        width=LINE_WIDTH,
        initial_indent=indent,
        subsequent_indent=indent,
    ) or [indent.rstrip()]


def _header_lines(report: ComplianceReport, source: str | None) -> list[str]:
    """Render the title block and the disclaimer.

    Args:
        report: The report being rendered.
        source: Where the dataset came from, if known.

    Returns:
        The header lines.
    """
    frameworks = ", ".join(str(framework) for framework in report.frameworks) or "none"
    # The disclaimer is emitted verbatim on one line, deliberately not
    # re-wrapped to the report width. It is the sentence that has to travel
    # intact when this report is forwarded, pasted, or grepped for, and a
    # line break inserted into it defeats every one of those.
    lines = [_RULE, REPORT_TITLE, _RULE, "NOTICE:", f"  {DISCLAIMER}"]
    lines.extend(
        ["", f"Generated:  {report.generated_at.isoformat()}", f"Frameworks: {frameworks}"]
    )
    if source is not None:
        lines.append(f"Dataset:    {source} (synthetic)")
    return lines


def _coverage_lines(report: ComplianceReport) -> list[str]:
    """Render which controls ran and which did not, with reasons.

    Args:
        report: The report being rendered.

    Returns:
        The coverage section lines.
    """
    lines = ["", _THIN_RULE, "COVERAGE", _THIN_RULE, ""]
    lines.append(f"Controls evaluated ({len(report.controls_evaluated)}):")
    if report.controls_evaluated:
        lines.extend(f"  - {control}" for control in report.controls_evaluated)
    else:
        lines.append("  (none)")

    lines.extend(["", f"Controls NOT evaluated ({len(report.controls_skipped)}):"])
    if report.controls_skipped:
        for reason in report.controls_skipped:
            lines.extend(_wrap(f"- {reason}", indent="  "))
        lines.append("")
        lines.extend(_SKIPPED_NOTE)
    else:
        lines.append("  (none -- every control for the selected frameworks had its inputs)")
    return lines


def _severity_lines(report: ComplianceReport) -> list[str]:
    """Render the finding counts per severity, most severe first.

    Args:
        report: The report being rendered.

    Returns:
        The severity summary lines.
    """
    counts = report.severity_counts()
    lines = ["", _THIN_RULE, f"FINDINGS BY SEVERITY ({len(report.findings)} total)", _THIN_RULE, ""]
    lines.extend(
        f"  {str(severity).upper():<9} {counts[severity]}" for severity in reversed(SEVERITY_ORDER)
    )
    return lines


def _finding_lines(finding: ComplianceFinding) -> list[str]:
    """Render one finding as a labelled block.

    Args:
        finding: The finding to render.

    Returns:
        The lines for this finding.
    """
    target_parts = []
    if finding.record_id is not None:
        target_parts.append(f"record={finding.record_id}")
    if finding.subject_id is not None:
        target_parts.append(f"subject={finding.subject_id}")
    target = "  ".join(target_parts) or "no specific record"

    lines = [f"  [{str(finding.severity).upper()}] {finding.control_id}", f"    {target}"]
    lines.extend(_wrap(finding.description, indent="    "))
    if finding.evidence:
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(finding.evidence.items()))
        lines.extend(_wrap(f"evidence: {rendered}", indent="    "))
    return lines


def _findings_lines(report: ComplianceReport) -> list[str]:
    """Render findings grouped by framework, then by severity within it.

    Args:
        report: The report being rendered.

    Returns:
        The findings section lines.
    """
    lines: list[str] = []
    for framework, items in report.findings_by_framework().items():
        lines.extend(
            ["", _THIN_RULE, f"{str(framework).upper()} FINDINGS ({len(items)})", _THIN_RULE]
        )
        if not items:
            lines.append("")
            lines.extend(
                _wrap(
                    "No findings from the controls that ran. That is a statement about "
                    "these checks, not about this framework's requirements."
                )
            )
            continue
        for severity in reversed(SEVERITY_ORDER):
            at_severity = [item for item in items if item.severity is severity]
            if not at_severity:
                continue
            lines.extend(["", f"  {str(severity).upper()} ({len(at_severity)})", ""])
            for finding in at_severity:
                lines.extend(_finding_lines(finding))
                lines.append("")
    return lines


def _footer_lines(report: ComplianceReport) -> list[str]:
    """Render the closing notes and the no-verdict statement.

    Args:
        report: The report being rendered.

    Returns:
        The footer lines.
    """
    lines = ["", _THIN_RULE, "WHAT THIS REPORT IS NOT", _THIN_RULE, ""]
    lines.extend(_NO_VERDICT_NOTE)
    for note in report.notes:
        lines.append("")
        lines.extend(_wrap(note))
    lines.extend(["", _RULE])
    return lines


def render_report(report: ComplianceReport, *, source: str | None = None) -> str:
    """Render a report as plain text, disclaimer first and coverage second.

    Args:
        report: The report to render.
        source: Where the dataset came from, shown in the header.

    Returns:
        The rendered report. Pure ASCII, so it survives a Windows console
        and a plain-text email without re-encoding.
    """
    lines = _header_lines(report, source)
    lines.extend(_coverage_lines(report))
    lines.extend(_severity_lines(report))
    lines.extend(_findings_lines(report))
    lines.extend(_footer_lines(report))
    return "\n".join(lines)


def render_coverage_plan(report: ComplianceReport, *, source: str | None = None) -> str:
    """Render only the header and coverage sections, with no findings.

    This is what ``--dry-run`` prints. The question it answers is "what will
    this run be able to check", which is worth asking before a report is
    generated and circulated -- and answering it does not require exposing
    the findings themselves.

    Args:
        report: The report to render.
        source: Where the dataset came from, shown in the header.

    Returns:
        The rendered coverage plan.
    """
    lines = _header_lines(report, source)
    lines.extend(_coverage_lines(report))
    lines.extend(
        [
            "",
            _THIN_RULE,
            "DRY RUN",
            _THIN_RULE,
            "",
        ]
    )
    lines.extend(
        _wrap(
            "Coverage only. Findings were withheld, so nothing here says anything "
            "about what the controls did or did not find. Re-run without --dry-run "
            "for the findings."
        )
    )
    lines.extend(["", _RULE])
    return "\n".join(lines)


__all__ = [
    "LINE_WIDTH",
    "REPORT_TITLE",
    "SEVERITY_ORDER",
    "ReportRun",
    "build_context",
    "render_coverage_plan",
    "render_report",
    "run_report",
    "severity_rank",
]
