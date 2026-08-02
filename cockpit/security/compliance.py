"""Regulatory compliance checks: GDPR, HIPAA, SOX (Tier 3 — not yet implemented).

Planned scope: automated checks that cockpit configuration and data
handling meet the requirements of common regulatory frameworks, plus
report generation for audits. Gated by
``feature_flags.is_enabled("security")`` at the framework level, but every
function here is a typed skeleton pending Tier 3 implementation. See
``docs/ARCHITECTURE.md`` for the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ComplianceFramework(StrEnum):
    """Regulatory frameworks the compliance module can check against.

    Attributes:
        GDPR: EU General Data Protection Regulation.
        HIPAA: US Health Insurance Portability and Accountability Act.
        SOX: US Sarbanes-Oxley Act.
    """

    GDPR = "gdpr"
    HIPAA = "hipaa"
    SOX = "sox"


@dataclass(frozen=True)
class ComplianceFinding:
    """A single compliance check result.

    Attributes:
        rule_id: Identifier of the specific rule checked.
        framework: The regulatory framework the rule belongs to.
        passed: Whether the current configuration satisfies the rule.
        description: Human-readable explanation of the rule and result.
    """

    rule_id: str
    framework: ComplianceFramework
    passed: bool
    description: str


def run_compliance_check(framework: ComplianceFramework) -> list[ComplianceFinding]:
    """Run all checks for a given regulatory framework.

    Args:
        framework: The regulatory framework to check compliance against.

    Returns:
        A list of :class:`ComplianceFinding` results, one per rule.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def generate_compliance_report(findings: list[ComplianceFinding]) -> str:
    """Render a human-readable compliance report from findings.

    Args:
        findings: Results previously produced by
            :func:`run_compliance_check`.

    Returns:
        A formatted report (e.g. Markdown) summarizing pass/fail status.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def is_compliant(framework: ComplianceFramework) -> bool:
    """Check whether the platform currently passes all rules for a framework.

    Args:
        framework: The regulatory framework to check.

    Returns:
        True if every rule for the framework currently passes.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")
