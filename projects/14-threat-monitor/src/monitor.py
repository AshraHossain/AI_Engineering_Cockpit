"""Run the Tier 3 detectors over a stream and rank what comes back.

This is the thin layer between "a pile of events" and "a list an analyst can
work". It does four things and deliberately no more:

* builds a :class:`~cockpit.security.threat_detection.ThreatDetector` with
  this deployment's thresholds and role grants;
* runs it at an explicit evaluation time;
* narrows the *findings* to one actor or one category on request;
* wraps the result with the counts a report needs.

The narrowing happens after detection, never before. Dropping other actors'
events first would break
:func:`~cockpit.security.threat_detection.detect_anomalous_rate`, which
compares an actor against their own history, and would quietly change the
answer for the actor you asked about. Scoping is a view over findings; it is
not a different analysis.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from cockpit.security.threat_detection import (
    SecurityEvent,
    ThreatCategory,
    ThreatDetector,
    ThreatFinding,
    ThreatLevel,
    score_threat_level,
)

# Severity, lowest first. `threat_detection` keeps an equivalent table
# private, and re-deriving it here beats importing an underscore name --
# StrEnum members compare as strings, and "critical" < "high" < "low"
# alphabetically is exactly backwards.
LEVEL_ORDER: tuple[ThreatLevel, ...] = (
    ThreatLevel.LOW,
    ThreatLevel.MEDIUM,
    ThreatLevel.HIGH,
    ThreatLevel.CRITICAL,
)

# Which actions each principal's role grants, for the out-of-role half of
# the privilege-escalation detector. An actor absent from this mapping is
# not evaluated for out-of-role activity at all: "no entry" means the role
# is unknown, and inventing an empty role would flag every action they take.
#
# In a real deployment this comes from the IAM system. It is a constant here
# because the bundled stream is a fixture, and a fixture that needs a
# directory service to explain itself is not much of a fixture.
DEFAULT_ROLE_GRANTS: Mapping[str, frozenset[str]] = {
    "alice@corp": frozenset({"login", "read", "query", "export"}),
    "bob@corp": frozenset({"login", "read", "query"}),
    "carol@corp": frozenset({"login", "read", "query", "write"}),
    "dana@corp": frozenset({"login", "read", "export"}),
    "mallory@corp": frozenset({"login"}),
    "svc-batch": frozenset({"process"}),
    "svc-scraper": frozenset({"fetch"}),
}

# Severity at which the CLI stops exiting 0. HIGH by default so a MEDIUM
# rate deviation lands in the report for a human to read without failing a
# pipeline -- the same reasoning as `should_block`'s HIGH default.
DEFAULT_FAIL_AT = ThreatLevel.HIGH


@dataclass(frozen=True)
class MonitorReport:
    """The outcome of one monitor run.

    Attributes:
        findings: Findings in scope, most severe first.
        evaluated_at: The instant every detector was run against (UTC).
        events_scanned: How many events the detectors saw, before scoping.
        actors_seen: Distinct actor ids in the scanned stream, sorted.
        highest_level: Severity of the most severe in-scope finding, or
            :attr:`ThreatLevel.LOW` when nothing fired.
        actor_scope: The ``--actor`` filter applied, or None.
        category_scope: The ``--category`` filter applied, or None.
        suppressed_count: Findings dropped by the scope filters. Reported
            so a scoped run cannot read as "the system is quiet" when it is
            only quiet for the slice you asked about.
    """

    findings: tuple[ThreatFinding, ...]
    evaluated_at: datetime
    events_scanned: int
    actors_seen: tuple[str, ...]
    highest_level: ThreatLevel
    actor_scope: str | None = None
    category_scope: ThreatCategory | None = None
    suppressed_count: int = 0


def level_rank(level: ThreatLevel) -> int:
    """Return a sortable rank for a severity level.

    Args:
        level: The level to rank.

    Returns:
        Zero for LOW through three for CRITICAL.
    """
    return LEVEL_ORDER.index(level)


def parse_level(raw: str) -> ThreatLevel:
    """Parse a severity name, case-insensitively.

    Args:
        raw: A level name such as "high".

    Returns:
        The matching :class:`ThreatLevel`.

    Raises:
        ValueError: If ``raw`` names no known level.
    """
    try:
        return ThreatLevel(raw.strip().lower())
    except ValueError as exc:
        options = ", ".join(level.value for level in LEVEL_ORDER)
        raise ValueError(f"Unknown severity {raw!r}. Choose one of: {options}.") from exc


def parse_category(raw: str) -> ThreatCategory:
    """Parse a threat category name, case-insensitively.

    Args:
        raw: A category name such as "brute_force".

    Returns:
        The matching :class:`ThreatCategory`.

    Raises:
        ValueError: If ``raw`` names no known category.
    """
    try:
        return ThreatCategory(raw.strip().lower())
    except ValueError as exc:
        options = ", ".join(category.value for category in ThreatCategory)
        raise ValueError(f"Unknown category {raw!r}. Choose one of: {options}.") from exc


def build_detector(
    *,
    brute_force_threshold: int | None = None,
    brute_force_window: timedelta | None = None,
    exfiltration_threshold: int | None = None,
    exfiltration_window: timedelta | None = None,
    escalation_window: timedelta | None = None,
    rate_multiplier: float | None = None,
    rate_window: timedelta | None = None,
    role_grants: Mapping[str, Collection[str]] | None = DEFAULT_ROLE_GRANTS,
) -> ThreatDetector:
    """Build a detector, overriding only the thresholds you pass.

    Every threshold the framework ships is a starting point, not a tuned
    value. Keeping them as constructor arguments -- and this function as the
    single place they are set -- means retuning a deployment is one edit
    rather than a hunt through call sites.

    Args:
        brute_force_threshold: Failed auth attempts that trigger a finding.
        brute_force_window: Window for the brute-force detector.
        exfiltration_threshold: Reads/exports that trigger a finding.
        exfiltration_window: Window for the exfiltration detector.
        escalation_window: Role-change-to-sensitive-access gap.
        rate_multiplier: Multiple of an actor's own baseline that counts as
            anomalous.
        rate_window: Window for the rate detector.
        role_grants: Actor id to granted actions. None disables out-of-role
            checking entirely, leaving only the role-change chain.

    Returns:
        A configured :class:`ThreatDetector`.
    """
    defaults = ThreatDetector()
    return ThreatDetector(
        brute_force_threshold=brute_force_threshold or defaults.brute_force_threshold,
        brute_force_window=brute_force_window or defaults.brute_force_window,
        exfiltration_threshold=exfiltration_threshold or defaults.exfiltration_threshold,
        exfiltration_window=exfiltration_window or defaults.exfiltration_window,
        escalation_window=escalation_window or defaults.escalation_window,
        rate_multiplier=rate_multiplier or defaults.rate_multiplier,
        rate_window=rate_window or defaults.rate_window,
        allowed_actions=role_grants,
    )


def scan(
    events: Sequence[SecurityEvent],
    *,
    now: datetime,
    detector: ThreatDetector | None = None,
    actor: str | None = None,
    category: ThreatCategory | None = None,
) -> MonitorReport:
    """Run every detector over a stream and rank the findings.

    Args:
        events: The stream to inspect. Passed to the detectors whole, even
            when ``actor`` is set, so per-actor baselines stay intact.
        now: Evaluation time. Required rather than defaulted to the wall
            clock -- a replay evaluated against "right now" finds nothing,
            and silently finding nothing is the worst failure mode a
            detector has.
        detector: Detector supplying thresholds. Defaults to
            :func:`build_detector` with this project's role grants.
        actor: Keep only findings about this actor id.
        category: Keep only findings in this category.

    Returns:
        A :class:`MonitorReport` whose findings are ordered most severe
        first, then by category and actor for a stable ordering.
    """
    active = detector or build_detector()
    all_findings = active.detect(events, now=now)

    kept = [
        finding
        for finding in all_findings
        if (actor is None or finding.actor_id == actor)
        and (category is None or finding.category == category)
    ]

    return MonitorReport(
        findings=tuple(kept),
        evaluated_at=now,
        events_scanned=len(events),
        actors_seen=tuple(sorted({event.actor_id for event in events})),
        highest_level=score_threat_level(kept),
        actor_scope=actor,
        category_scope=category,
        suppressed_count=len(all_findings) - len(kept),
    )


def findings_at_or_above(
    report: MonitorReport, level: ThreatLevel = DEFAULT_FAIL_AT
) -> tuple[ThreatFinding, ...]:
    """Select the in-scope findings at or above a severity.

    Args:
        report: The report to filter.
        level: Minimum severity to include.

    Returns:
        The matching findings, in report order.
    """
    floor = level_rank(level)
    return tuple(finding for finding in report.findings if level_rank(finding.level) >= floor)


def actors_with_findings(report: MonitorReport) -> tuple[str, ...]:
    """List the actors a report has findings for, sorted.

    Args:
        report: The report to inspect.

    Returns:
        The distinct actor ids named by in-scope findings.
    """
    return tuple(sorted({finding.actor_id for finding in report.findings}))


__all__ = [
    "DEFAULT_FAIL_AT",
    "DEFAULT_ROLE_GRANTS",
    "LEVEL_ORDER",
    "MonitorReport",
    "actors_with_findings",
    "build_detector",
    "findings_at_or_above",
    "level_rank",
    "parse_category",
    "parse_level",
    "scan",
]
