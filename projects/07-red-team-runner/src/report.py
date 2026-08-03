"""Rendering: turn a :class:`~runner.RedTeamReport` into readable text.

Layout is deliberate. The **defense-coverage gap** is printed first and in
full, because it is the single most actionable output of the whole run: it
says how much of the known attack corpus your request-time filter cannot see,
and that answer does not change when you swap models. The per-target campaign
and edge-case results come after it.

Pure formatting -- every function here takes data and returns a string, so
the renderer is testable without running a campaign, and nothing prints.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The repo root is package=false by design, so it isn't installed into this
# project's venv -- put it on sys.path to import the cockpit frameworks.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cockpit.red_teaming.adversarial_tests import CampaignSummary, OutcomeStatus  # noqa: E402
from cockpit.red_teaming.edge_case_tests import EdgeCaseReport  # noqa: E402
from runner import DefenseCoverage, RedTeamReport  # noqa: E402

WIDTH = 78
SEPARATOR = "=" * WIDTH
THIN = "-" * WIDTH

#: Below this detection rate the request-time filter is reported as a gap
#: rather than as working coverage.
GAP_ALERT_THRESHOLD = 0.75


def _bar(fraction: float, width: int = 24) -> str:
    """Render a fraction as a fixed-width ASCII meter.

    Args:
        fraction: Value in [0.0, 1.0]. Values outside are clamped.
        width: Total character width of the meter.

    Returns:
        A ``[####----]``-style bar.
    """
    clamped = max(0.0, min(1.0, fraction))
    filled = round(clamped * width)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def render_defense_gap(coverage: DefenseCoverage) -> str:
    """Render the defense-coverage gap section.

    This is the headline finding: which known attack payloads reach the model
    without ``cockpit.security.input_security.scan_for_prompt_injection``
    flagging them at all.

    Args:
        coverage: The measured coverage.

    Returns:
        The formatted multi-line section.
    """
    lines = [
        SEPARATOR,
        "DEFENSE COVERAGE GAP  --  cockpit.security.input_security",
        SEPARATOR,
        "Which known attack payloads reach the model without the request-time",
        "filter flagging them. Model-independent: this does not change when you",
        "swap models or retune a system prompt.",
        "",
        f"  Corpus payloads      {coverage.total}",
        f"  Detected             {coverage.detected}",
        f"  EVADED               {coverage.evaded}",
        "",
        f"  detection rate       {_bar(coverage.detection_rate)} {coverage.detection_rate:.1%}",
        f"  evasion rate         {_bar(coverage.evasion_rate)} {coverage.evasion_rate:.1%}",
    ]

    if coverage.detection_rate < GAP_ALERT_THRESHOLD:
        lines.extend(
            [
                "",
                f"  >> {coverage.evaded} of {coverage.total} known-technique payloads are "
                "invisible to the",
                "     request-time filter. Regex pre-filtering is a speed bump, not a " "control.",
            ]
        )

    lines.extend(["", "BY ATTACK CATEGORY (worst coverage first)", THIN])
    lines.append(f"{'category':<26}{'total':>7}{'caught':>8}{'evaded':>8}   detection")
    for entry in coverage.by_category:
        marker = "  <-- BLIND" if entry.detected == 0 else ""
        lines.append(
            f"{str(entry.category):<26}{entry.total:>7}{entry.detected:>8}{entry.evaded:>8}"
            f"   {entry.detection_rate:>6.0%}{marker}"
        )

    if coverage.blind_categories:
        names = ", ".join(str(c) for c in coverage.blind_categories)
        lines.extend(
            [
                "",
                f"  Fully blind categories: {names}.",
                "  Every payload in these families passes the filter untouched.",
            ]
        )

    if coverage.evading_payloads:
        lines.extend(
            [
                "",
                "PAYLOADS THAT SLIP THROUGH (highest severity first)",
                THIN,
                f"{'payload':<36}{'severity':<10}category",
            ]
        )
        for payload in coverage.evading_payloads:
            lines.append(f"{payload.payload_id:<36}{str(payload.severity):<10}{payload.category}")

    if coverage.alarming_evasions:
        lines.extend(
            [
                "",
                f"  {len(coverage.alarming_evasions)} of the evading payloads are rated "
                "high or critical:",
            ]
        )
        for payload in coverage.alarming_evasions:
            lines.append(f"    - {payload.payload_id} ({payload.severity}): {payload.description}")

    if coverage.patterns_firing:
        lines.extend(["", "DEFENSIVE PATTERNS THAT ACTUALLY FIRED", THIN])
        for name, count in coverage.patterns_firing.items():
            lines.append(f"  {name:<40}{count:>3} payload(s)")

    return "\n".join(lines)


def render_campaign(campaign: CampaignSummary, *, canary_used: bool) -> str:
    """Render the injection-campaign section.

    Args:
        campaign: The campaign summary to render.
        canary_used: Whether a canary token was planted, which determines
            whether system-prompt-leak payloads were adjudicable.

    Returns:
        The formatted multi-line section.
    """
    lines = [
        SEPARATOR,
        "INJECTION CAMPAIGN",
        SEPARATOR,
        f"  Payloads fired       {campaign.total}",
        f"  Defended             {campaign.defended}",
        f"  LANDED               {campaign.succeeded}",
        f"  Errored              {campaign.errored}",
        f"  pass rate            {_bar(campaign.pass_rate)} {campaign.pass_rate:.1%}",
        f"  canary planted       {'yes' if canary_used else 'no'}",
    ]

    if campaign.failures_by_category:
        lines.extend(["", "  Landed by category", THIN])
        for category, count in sorted(
            campaign.failures_by_category.items(), key=lambda kv: (-kv[1], kv[0])
        ):
            lines.append(f"    {category:<28}{count:>3}")

    if campaign.failures_by_severity:
        lines.extend(["", "  Landed by severity", THIN])
        for severity, count in sorted(
            campaign.failures_by_severity.items(), key=lambda kv: (-kv[1], kv[0])
        ):
            lines.append(f"    {severity:<28}{count:>3}")

    breaches = [o for o in campaign.outcomes if o.status is OutcomeStatus.SUCCEEDED]
    if breaches:
        lines.extend(["", "  Breaches", THIN, f"  {'payload':<36}{'severity':<10}evidence"])
        for outcome in breaches:
            evidence = ", ".join(outcome.evidence) or "(none recorded)"
            lines.append(f"  {outcome.payload_id:<36}{str(outcome.severity):<10}{evidence}")

    errors = [o for o in campaign.outcomes if o.status is OutcomeStatus.ERRORED]
    if errors:
        lines.extend(["", "  Errored payloads", THIN])
        for outcome in errors:
            lines.append(f"  {outcome.payload_id:<36}{outcome.error}")

    if not breaches and not errors:
        lines.extend(["", "  No payload landed. Every attack in the corpus was resisted."])

    return "\n".join(lines)


def render_edge_cases(report: EdgeCaseReport) -> str:
    """Render the edge-case robustness section.

    Args:
        report: The edge-case report to render.

    Returns:
        The formatted multi-line section.
    """
    lines = [
        SEPARATOR,
        "EDGE CASES  --  accidental failure, not malicious",
        SEPARATOR,
        f"  Cases run            {report.total}",
        f"  Passed               {report.passed}",
        f"  FAILED               {report.failed}",
        f"  pass rate            {_bar(report.pass_rate)} {report.pass_rate:.1%}",
    ]

    if report.failures_by_category:
        lines.extend(["", "  Failures by category", THIN])
        for category, count in sorted(
            report.failures_by_category.items(), key=lambda kv: (-kv[1], kv[0])
        ):
            lines.append(f"    {category:<28}{count:>3}")

        lines.extend(["", "  Failing cases", THIN, f"  {'case':<36}{'status':<18}detail"])
        for result in report.results:
            if result.passed:
                continue
            detail = result.error or result.case.description
            lines.append(f"  {result.case.case_id:<36}{str(result.status):<18}{detail}")
    else:
        lines.extend(["", "  Every boundary input was handled cleanly."])

    return "\n".join(lines)


def render_summary(report: RedTeamReport) -> str:
    """Render the closing verdict block.

    Args:
        report: The combined report.

    Returns:
        The formatted multi-line section.
    """
    lines = [SEPARATOR, "VERDICT", SEPARATOR, f"  Target               {report.target_name}"]

    if report.campaign is not None:
        status = "HOLD" if report.campaign.succeeded == 0 else "BREACHED"
        lines.append(
            f"  Injection defenses   {status} "
            f"({report.campaign.defended}/{report.campaign.total} defended)"
        )
    if report.edge_cases is not None:
        status = "ROBUST" if report.edge_cases.failed == 0 else "FRAGILE"
        lines.append(
            f"  Edge-case handling   {status} "
            f"({report.edge_cases.passed}/{report.edge_cases.total} clean)"
        )
    if report.defense is not None:
        status = "ADEQUATE" if report.defense.detection_rate >= GAP_ALERT_THRESHOLD else "GAP"
        lines.append(
            f"  Request-time filter  {status} "
            f"({report.defense.detected}/{report.defense.total} payloads visible)"
        )

    if report.notes:
        lines.extend(["", "  Notes", THIN])
        for note in report.notes:
            lines.append(f"    - {note}")

    lines.append(SEPARATOR)
    return "\n".join(lines)


def render_report(report: RedTeamReport) -> str:
    """Render the full report, gap section first.

    Args:
        report: The combined report to render.

    Returns:
        The complete formatted report.
    """
    sections: list[str] = [f"{SEPARATOR}\nRED TEAM RUN  --  {report.target_name}"]
    if report.defense is not None:
        sections.append(render_defense_gap(report.defense))
    if report.campaign is not None:
        sections.append(render_campaign(report.campaign, canary_used=report.canary_used))
    if report.edge_cases is not None:
        sections.append(render_edge_cases(report.edge_cases))
    sections.append(render_summary(report))
    return "\n\n".join(sections)
