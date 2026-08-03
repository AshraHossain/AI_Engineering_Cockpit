"""Orchestration: run the injection campaign, edge cases, and the defense scan.

Three probes are combined into one :class:`RedTeamReport`:

1. **Injection campaign** --
   :func:`cockpit.red_teaming.adversarial_tests.run_campaign` fires the
   32-payload corpus at the target and adjudicates each response. A canary
   token planted in the target's system prompt makes system-prompt-leak
   payloads objectively judgeable.
2. **Edge cases** --
   :func:`cockpit.red_teaming.edge_case_tests.run_edge_case_campaign` probes
   for *accidental* failure: empty inputs, 100k-character walls, zero-width
   joiners, control bytes, malformed JSON.
3. **Defense coverage** --
   :func:`cockpit.red_teaming.prompt_injection.scan_corpus_against_defense`
   runs every attack payload past
   :func:`cockpit.security.input_security.scan_for_prompt_injection`, the
   request-time filter, and reports which ones slip through.

Probe 3 is the one worth staring at. Probes 1 and 2 tell you how *this*
target behaved. Probe 3 tells you how much of the known attack surface your
request-time filter cannot see at all, independent of any model -- so it is
the finding that stays true after you swap models.

No SDK is imported here. The target is always injected.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

# The repo root is package=false by design, so it isn't installed into this
# project's venv -- put it on sys.path to import the cockpit frameworks.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cockpit.red_teaming.adversarial_tests import (  # noqa: E402
    CampaignSummary,
    Target,
    run_campaign,
)
from cockpit.red_teaming.edge_case_tests import (  # noqa: E402
    EdgeCase,
    EdgeCaseReport,
    generate_edge_cases,
    run_edge_case_campaign,
)
from cockpit.red_teaming.prompt_injection import (  # noqa: E402
    INJECTION_CORPUS,
    AttackCategory,
    InjectionPayload,
    Severity,
    build_canary_probe,
    payloads_evading_defense,
    scan_corpus_against_defense,
)

#: Severities that make an undetected payload genuinely alarming rather than
#: merely interesting.
ALARMING_SEVERITIES: frozenset[Severity] = frozenset({Severity.HIGH, Severity.CRITICAL})


@dataclass(frozen=True)
class EvadingPayload:
    """One attack payload the request-time defensive scanner does not flag.

    Attributes:
        payload_id: Identifier of the payload.
        category: Technique family it belongs to.
        severity: Impact if it lands.
        description: What the technique does.
    """

    payload_id: str
    category: AttackCategory
    severity: Severity
    description: str


@dataclass(frozen=True)
class CategoryCoverage:
    """Defensive-scanner coverage for one attack category.

    Attributes:
        category: The technique family.
        total: Payloads in the corpus for this category.
        detected: Payloads the scanner flags as suspicious.
        evaded: Payloads the scanner considers benign.
        detection_rate: ``detected / total``, in [0.0, 1.0].
    """

    category: AttackCategory
    total: int
    detected: int
    evaded: int
    detection_rate: float


@dataclass(frozen=True)
class DefenseCoverage:
    """How much of the known attack corpus the request-time filter can see.

    This is model-independent: it measures
    :func:`cockpit.security.input_security.scan_for_prompt_injection`
    against the shipped payload corpus, so the answer does not change when
    you swap models or tune a system prompt.

    Attributes:
        total: Payloads scanned.
        detected: Payloads the scanner flagged as suspicious.
        evaded: Payloads the scanner considered benign.
        detection_rate: ``detected / total``, in [0.0, 1.0].
        evasion_rate: ``evaded / total``, in [0.0, 1.0].
        by_category: Per-category coverage, worst detection rate first.
        evading_payloads: The payloads that slipped through, ordered by
            descending severity then by identifier.
        blind_categories: Categories where the scanner detects nothing at
            all.
        alarming_evasions: Evading payloads rated high or critical.
        patterns_firing: How many payloads each defensive pattern caught,
            i.e. which regexes are actually carrying the filter.
    """

    total: int
    detected: int
    evaded: int
    detection_rate: float
    evasion_rate: float
    by_category: tuple[CategoryCoverage, ...] = ()
    evading_payloads: tuple[EvadingPayload, ...] = ()
    blind_categories: tuple[AttackCategory, ...] = ()
    alarming_evasions: tuple[EvadingPayload, ...] = ()
    patterns_firing: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RedTeamReport:
    """Combined result of every probe run against one target.

    Attributes:
        target_name: Human-readable label for the system under test.
        campaign: Injection campaign summary, or ``None`` if skipped.
        edge_cases: Edge-case robustness report, or ``None`` if skipped.
        defense: Defensive-scanner coverage over the attack corpus.
        canary_used: True if a canary token was planted, making
            system-prompt-leak payloads adjudicable.
        notes: Caveats worth printing alongside the numbers.
    """

    target_name: str
    campaign: CampaignSummary | None = None
    edge_cases: EdgeCaseReport | None = None
    defense: DefenseCoverage | None = None
    canary_used: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def clean(self) -> bool:
        """Whether every probe that ran came back clean.

        Note this deliberately ignores :attr:`defense` coverage: a gap in the
        request-time filter is a real finding, but it is not this *target*
        failing, so it must not flip the run's exit status on its own.

        Returns:
            True if no attack landed and no edge case failed.
        """
        campaign_clean = self.campaign is None or (
            self.campaign.succeeded == 0 and self.campaign.errored == 0
        )
        edge_clean = self.edge_cases is None or self.edge_cases.failed == 0
        return campaign_clean and edge_clean


def _severity_rank(severity: Severity) -> int:
    """Rank a severity so higher impact sorts first.

    Args:
        severity: The severity to rank.

    Returns:
        A descending sort key: 0 for critical, 3 for low.
    """
    order = list(Severity)
    return len(order) - 1 - order.index(severity)


def analyze_defense_coverage(
    payloads: tuple[InjectionPayload, ...] | None = None,
) -> DefenseCoverage:
    """Measure the request-time defensive scanner against the attack corpus.

    Calls :func:`~cockpit.red_teaming.prompt_injection.scan_corpus_against_defense`
    and :func:`~cockpit.red_teaming.prompt_injection.payloads_evading_defense`
    and turns their raw output into a ranked, per-category picture. Detection
    logic is not reimplemented here -- this only reports.

    Args:
        payloads: Payloads to scan. Defaults to the full shipped corpus.

    Returns:
        The :class:`DefenseCoverage` for those payloads.
    """
    selected = INJECTION_CORPUS if payloads is None else payloads
    scan = scan_corpus_against_defense(selected)
    evading_ids = set(payloads_evading_defense(selected))

    firing: dict[str, int] = {}
    for result in scan.values():
        for pattern_name in result.matched_patterns:
            firing[pattern_name] = firing.get(pattern_name, 0) + 1

    evading = tuple(
        sorted(
            (
                EvadingPayload(
                    payload_id=payload.payload_id,
                    category=payload.category,
                    severity=payload.severity,
                    description=payload.description,
                )
                for payload in selected
                if payload.payload_id in evading_ids
            ),
            key=lambda p: (_severity_rank(p.severity), p.payload_id),
        )
    )

    totals: dict[AttackCategory, list[int]] = {}
    for payload in selected:
        bucket = totals.setdefault(payload.category, [0, 0])
        bucket[0] += 1
        if payload.payload_id in evading_ids:
            bucket[1] += 1

    by_category = tuple(
        sorted(
            (
                CategoryCoverage(
                    category=category,
                    total=total,
                    detected=total - evaded,
                    evaded=evaded,
                    detection_rate=0.0 if total == 0 else (total - evaded) / total,
                )
                for category, (total, evaded) in totals.items()
            ),
            key=lambda c: (c.detection_rate, str(c.category)),
        )
    )

    total = len(selected)
    evaded_count = len(evading)
    detected_count = total - evaded_count

    return DefenseCoverage(
        total=total,
        detected=detected_count,
        evaded=evaded_count,
        detection_rate=0.0 if total == 0 else detected_count / total,
        evasion_rate=0.0 if total == 0 else evaded_count / total,
        by_category=by_category,
        evading_payloads=evading,
        blind_categories=tuple(c.category for c in by_category if c.detected == 0),
        alarming_evasions=tuple(p for p in evading if p.severity in ALARMING_SEVERITIES),
        patterns_firing=dict(sorted(firing.items(), key=lambda kv: (-kv[1], kv[0]))),
    )


def run_red_team(
    target: Target,
    *,
    target_name: str = "target",
    canary: str | None = None,
    payloads: tuple[InjectionPayload, ...] | None = None,
    edge_cases: list[EdgeCase] | None = None,
    include_injection: bool = True,
    include_edge_cases: bool = True,
    include_defense_scan: bool = True,
    timeout_seconds: float = 30.0,
) -> RedTeamReport:
    """Run every enabled probe against a target and combine the results.

    Args:
        target: The system under test.
        target_name: Human-readable label for the report header.
        canary: Canary token planted in the target's system prompt. Without
            it, system-prompt-leak payloads can only ever score as defended,
            so the report records a note saying so.
        payloads: Injection payloads to fire. Defaults to the full corpus.
        edge_cases: Edge cases to run. Defaults to
            :func:`~cockpit.red_teaming.edge_case_tests.generate_edge_cases`.
        include_injection: Whether to run the injection campaign.
        include_edge_cases: Whether to run the edge-case campaign.
        include_defense_scan: Whether to measure defensive-scanner coverage.
            This probe never contacts the target, so it is cheap and safe to
            leave on.
        timeout_seconds: Per-case wall-clock budget for edge cases.

    Returns:
        The combined :class:`RedTeamReport`.

    Raises:
        TypeError: If ``target`` is not callable.
        ValueError: If ``timeout_seconds`` is not positive.
    """
    if not callable(target):
        raise TypeError(f"target must be callable, got {type(target).__name__}.")

    notes: list[str] = []
    campaign: CampaignSummary | None = None
    if include_injection:
        campaign = run_campaign(target, payloads=payloads, canary=canary)
        if canary is None:
            notes.append(
                "No canary planted: system-prompt-leak payloads could only be scored as "
                "defended, so the injection pass rate is optimistic."
            )
        if campaign.errored:
            notes.append(
                f"{campaign.errored} payload(s) errored; an unanswered attack is not a "
                "demonstrated defense and counts against the pass rate."
            )

    report_edge: EdgeCaseReport | None = None
    if include_edge_cases:
        report_edge = run_edge_case_campaign(
            target,
            cases=edge_cases if edge_cases is not None else generate_edge_cases(),
            timeout_seconds=timeout_seconds,
        )

    defense = analyze_defense_coverage(payloads) if include_defense_scan else None
    if defense is not None and defense.evaded:
        notes.append(
            f"{defense.evaded}/{defense.total} corpus payloads reach the model without "
            "the request-time filter flagging them."
        )

    return RedTeamReport(
        target_name=target_name,
        campaign=campaign,
        edge_cases=report_edge,
        defense=defense,
        canary_used=canary is not None,
        notes=tuple(notes),
    )


def build_canary_target_prompt(system_prompt: str) -> tuple[str, str]:
    """Plant a fresh canary token in a system prompt.

    Thin wrapper over
    :func:`~cockpit.red_teaming.prompt_injection.build_canary_probe` so
    callers get the pair back in the order they need it.

    Args:
        system_prompt: The application's real system prompt.

    Returns:
        A ``(token, instrumented_prompt)`` pair.
    """
    probe = build_canary_probe(system_prompt)
    return probe.token, probe.system_prompt
