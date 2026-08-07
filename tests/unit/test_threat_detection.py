"""Unit tests for cockpit/security/threat_detection.py.

Every detector is exercised in three directions: it fires above its
threshold, it stays quiet below it (the false-positive direction is the one
that decides whether anyone still trusts the alerts), and it respects its
time window. All timing is injected -- nothing here sleeps.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest

from cockpit.security.audit_logging import AuditLog
from cockpit.security.threat_detection import (
    ThreatCategory,
    ThreatDetector,
    ThreatFinding,
    ThreatLevel,
    ThreatSignal,
    analyze_request_pattern,
    detect_anomalous_rate,
    detect_brute_force,
    detect_data_exfiltration,
    detect_privilege_escalation,
    score_threat_level,
    should_block,
)

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)

# Local copy of the severity ordering, so a bug in the module's own ordering
# table cannot make an ordering assertion pass by agreeing with itself.
_ORDER = {
    ThreatLevel.LOW: 0,
    ThreatLevel.MEDIUM: 1,
    ThreatLevel.HIGH: 2,
    ThreatLevel.CRITICAL: 3,
}


@dataclass(frozen=True)
class FakeEvent:
    """A minimal stand-in satisfying the SecurityEvent protocol structurally."""

    timestamp: datetime
    actor_id: str
    action: str
    resource: str
    outcome: str


def event(
    seconds_ago: float,
    actor_id: str = "alice",
    action: str = "login",
    outcome: str = "failure",
    resource: str = "session",
) -> FakeEvent:
    """Build an event positioned relative to :data:`NOW`.

    Args:
        seconds_ago: How far before ``NOW`` the event occurred.
        actor_id: Acting principal.
        action: Action performed.
        outcome: Result of the action.
        resource: Affected resource.

    Returns:
        A :class:`FakeEvent`.
    """
    return FakeEvent(
        timestamp=NOW - timedelta(seconds=seconds_ago),
        actor_id=actor_id,
        action=action,
        resource=resource,
        outcome=outcome,
    )


def failures(count: int, actor_id: str = "alice", spacing: float = 5.0) -> list[FakeEvent]:
    """Build ``count`` failed logins ending just before :data:`NOW`.

    Args:
        count: How many failures to build.
        actor_id: Acting principal.
        spacing: Seconds between attempts.

    Returns:
        The events, oldest first.
    """
    return [event((count - index) * spacing, actor_id=actor_id) for index in range(count)]


def reads(
    count: int, actor_id: str = "alice", spacing: float = 1.0, distinct: bool = True
) -> list[FakeEvent]:
    """Build ``count`` successful reads ending just before :data:`NOW`.

    Args:
        count: How many reads to build.
        actor_id: Acting principal.
        spacing: Seconds between reads.
        distinct: Whether each read targets a different resource.

    Returns:
        The events, oldest first.
    """
    return [
        event(
            (count - index) * spacing,
            actor_id=actor_id,
            action="read",
            outcome="success",
            resource=f"record-{index}" if distinct else "record-0",
        )
        for index in range(count)
    ]


class TestBruteForceDetector:
    """Tests for detect_brute_force."""

    def test_fires_at_the_threshold(self) -> None:
        findings = detect_brute_force(failures(5), threshold=5, now=NOW)
        assert len(findings) == 1
        assert findings[0].category is ThreatCategory.BRUTE_FORCE
        assert findings[0].actor_id == "alice"

    def test_does_not_fire_below_the_threshold(self) -> None:
        assert detect_brute_force(failures(4), threshold=5, now=NOW) == []

    def test_respects_the_time_window(self) -> None:
        stale = [event(3600 + index, actor_id="alice") for index in range(10)]
        recent = failures(2)
        assert detect_brute_force(stale + recent, threshold=5, now=NOW) == []

    def test_evidence_identifies_the_triggering_events(self) -> None:
        attempts = failures(6)
        findings = detect_brute_force(attempts, threshold=5, now=NOW)
        assert len(findings[0].evidence) == 6
        assert set(findings[0].evidence) == set(attempts)

    def test_successful_logins_do_not_count(self) -> None:
        successes = [event(index * 5, action="login", outcome="success") for index in range(1, 11)]
        assert detect_brute_force(successes, threshold=5, now=NOW) == []

    def test_counts_per_actor_not_globally(self) -> None:
        """Spraying across accounts is a different attack and must not merge."""
        spread = [event(index, actor_id=f"user-{index}") for index in range(10)]
        assert detect_brute_force(spread, threshold=5, now=NOW) == []

    def test_separate_actors_produce_separate_findings(self) -> None:
        findings = detect_brute_force(
            failures(5, actor_id="alice") + failures(5, actor_id="bob"), threshold=5, now=NOW
        )
        assert {finding.actor_id for finding in findings} == {"alice", "bob"}

    def test_sustained_volume_escalates_to_critical(self) -> None:
        findings = detect_brute_force(failures(15, spacing=2.0), threshold=5, now=NOW)
        assert findings[0].level is ThreatLevel.CRITICAL

    def test_moderate_volume_stays_high(self) -> None:
        findings = detect_brute_force(failures(6), threshold=5, now=NOW)
        assert findings[0].level is ThreatLevel.HIGH

    def test_rejects_a_non_positive_threshold(self) -> None:
        with pytest.raises(ValueError, match="threshold must be positive"):
            detect_brute_force(failures(5), threshold=0, now=NOW)

    def test_rejects_a_non_positive_window(self) -> None:
        with pytest.raises(ValueError, match="window must be a positive timedelta"):
            detect_brute_force(failures(5), window=timedelta(0), now=NOW)


class TestDataExfiltrationDetector:
    """Tests for detect_data_exfiltration."""

    def test_fires_at_the_threshold(self) -> None:
        findings = detect_data_exfiltration(reads(20), threshold=20, now=NOW)
        assert len(findings) == 1
        assert findings[0].category is ThreatCategory.DATA_EXFILTRATION

    def test_does_not_fire_below_the_threshold(self) -> None:
        assert detect_data_exfiltration(reads(19), threshold=20, now=NOW) == []

    def test_respects_the_time_window(self) -> None:
        stale = [
            event(3600 + index, action="read", outcome="success", resource=f"old-{index}")
            for index in range(50)
        ]
        assert detect_data_exfiltration(stale + reads(5), threshold=20, now=NOW) == []

    def test_evidence_identifies_the_triggering_events(self) -> None:
        pulled = reads(25)
        findings = detect_data_exfiltration(pulled, threshold=20, now=NOW)
        assert len(findings[0].evidence) == 25
        assert set(findings[0].evidence) == set(pulled)

    def test_denied_reads_do_not_count_as_exfiltration(self) -> None:
        """Nothing left the system, so this belongs to a different detector."""
        denied = [
            event(index, action="read", outcome="denied", resource=f"record-{index}")
            for index in range(1, 41)
        ]
        assert detect_data_exfiltration(denied, threshold=20, now=NOW) == []

    def test_broad_sweep_escalates_to_critical(self) -> None:
        findings = detect_data_exfiltration(reads(45, spacing=0.5), threshold=20, now=NOW)
        assert findings[0].level is ThreatLevel.CRITICAL

    def test_repeated_reads_of_one_resource_stay_high(self) -> None:
        findings = detect_data_exfiltration(
            reads(45, spacing=0.5, distinct=False), threshold=20, now=NOW
        )
        assert findings[0].level is ThreatLevel.HIGH

    def test_explanation_reports_the_observed_value(self) -> None:
        findings = detect_data_exfiltration(reads(30), threshold=20, now=NOW)
        assert "30 read/export operations" in findings[0].explanation
        assert "threshold 20" in findings[0].explanation


class TestPrivilegeEscalationDetector:
    """Tests for detect_privilege_escalation."""

    def test_out_of_role_action_fires(self) -> None:
        events = [event(10, action="delete", outcome="success", resource="prod-db")]
        findings = detect_privilege_escalation(events, allowed_actions={"alice": {"read"}}, now=NOW)
        assert len(findings) == 1
        assert findings[0].category is ThreatCategory.PRIVILEGE_ESCALATION
        assert findings[0].evidence == tuple(events)

    def test_in_role_actions_do_not_fire(self) -> None:
        events = [event(index, action="read", outcome="success") for index in range(1, 20)]
        assert (
            detect_privilege_escalation(
                events, allowed_actions={"alice": {"read", "login"}}, now=NOW
            )
            == []
        )

    def test_actor_with_no_granted_role_entry_is_not_evaluated(self) -> None:
        """No entry means unknown role, not empty role -- guessing here is noise."""
        events = [event(10, action="delete", outcome="success")]
        assert detect_privilege_escalation(events, allowed_actions={"bob": {"read"}}, now=NOW) == []

    def test_successful_out_of_role_action_is_critical(self) -> None:
        events = [event(10, action="delete", outcome="success")]
        findings = detect_privilege_escalation(events, allowed_actions={"alice": {"read"}}, now=NOW)
        assert findings[0].level is ThreatLevel.CRITICAL

    def test_denied_out_of_role_action_is_high_not_critical(self) -> None:
        events = [event(10, action="delete", outcome="denied")]
        findings = detect_privilege_escalation(events, allowed_actions={"alice": {"read"}}, now=NOW)
        assert findings[0].level is ThreatLevel.HIGH

    def test_role_change_then_immediate_sensitive_access_fires(self) -> None:
        change = event(30, action="role_change", outcome="success", resource="iam")
        access = event(25, action="export", outcome="success", resource="customers")
        findings = detect_privilege_escalation(
            [change, access], window=timedelta(minutes=1), now=NOW
        )
        assert len(findings) == 1
        assert findings[0].evidence == (change, access)
        assert findings[0].level is ThreatLevel.CRITICAL

    def test_sensitive_access_long_after_a_role_change_does_not_fire(self) -> None:
        change = event(7200, action="role_change", outcome="success", resource="iam")
        access = event(10, action="export", outcome="success", resource="customers")
        assert (
            detect_privilege_escalation([change, access], window=timedelta(minutes=1), now=NOW)
            == []
        )

    def test_role_change_without_sensitive_access_does_not_fire(self) -> None:
        change = event(30, action="role_change", outcome="success", resource="iam")
        benign = event(25, action="read", outcome="success", resource="doc-1")
        assert (
            detect_privilege_escalation([change, benign], window=timedelta(minutes=1), now=NOW)
            == []
        )

    def test_sensitive_access_before_the_role_change_does_not_fire(self) -> None:
        """Order matters: access then grant is not an escalation chain."""
        access = event(60, action="export", outcome="success", resource="customers")
        change = event(30, action="role_change", outcome="success", resource="iam")
        assert (
            detect_privilege_escalation([access, change], window=timedelta(minutes=1), now=NOW)
            == []
        )

    def test_evidence_is_deduplicated_and_ordered(self) -> None:
        change = event(30, action="role_change", outcome="success", resource="iam")
        access = event(25, action="export", outcome="success", resource="customers")
        findings = detect_privilege_escalation(
            [access, change],
            allowed_actions={"alice": {"read"}},
            window=timedelta(minutes=1),
            now=NOW,
        )
        evidence = findings[0].evidence
        assert len(evidence) == len(set(evidence))
        assert list(evidence) == sorted(evidence, key=lambda item: item.timestamp)

    def test_rejects_a_non_positive_window(self) -> None:
        with pytest.raises(ValueError, match="window must be a positive timedelta"):
            detect_privilege_escalation([event(1)], window=timedelta(0), now=NOW)


class TestAnomalousRateDetector:
    """Tests for detect_anomalous_rate."""

    @staticmethod
    def baseline(count: int = 20, actor_id: str = "alice") -> list[FakeEvent]:
        """Build a slow historical baseline well outside the live window.

        Args:
            count: How many historical events to build.
            actor_id: Acting principal.

        Returns:
            Historical events, roughly one every 100 seconds.
        """
        return [
            event(
                120 + index * 100,
                actor_id=actor_id,
                action="read",
                outcome="success",
                resource=f"old-{index}",
            )
            for index in range(count)
        ]

    def test_fires_on_a_burst_far_above_baseline(self) -> None:
        events = self.baseline() + reads(30, spacing=1.5)
        findings = detect_anomalous_rate(
            events, multiplier=5.0, window=timedelta(minutes=1), now=NOW
        )
        assert len(findings) == 1
        assert findings[0].category is ThreatCategory.ANOMALOUS_RATE

    def test_does_not_fire_when_the_rate_matches_baseline(self) -> None:
        steady = [
            event(
                index * 5,
                action="read",
                outcome="success",
                resource=f"doc-{index}",
            )
            for index in range(1, 200)
        ]
        assert (
            detect_anomalous_rate(steady, multiplier=5.0, window=timedelta(minutes=1), now=NOW)
            == []
        )

    def test_does_not_fire_without_enough_in_window_events(self) -> None:
        """A 2-event burst against a tiny baseline is arithmetic, not a signal."""
        events = self.baseline() + reads(3, spacing=1.0)
        assert (
            detect_anomalous_rate(events, multiplier=5.0, window=timedelta(minutes=1), now=NOW)
            == []
        )

    def test_does_not_fire_without_an_established_baseline(self) -> None:
        """A brand new actor has no normal to deviate from."""
        events = self.baseline(count=2) + reads(30, spacing=1.5)
        assert (
            detect_anomalous_rate(events, multiplier=5.0, window=timedelta(minutes=1), now=NOW)
            == []
        )

    def test_evidence_contains_only_in_window_events(self) -> None:
        burst = reads(30, spacing=1.5)
        events = self.baseline() + burst
        findings = detect_anomalous_rate(
            events, multiplier=5.0, window=timedelta(minutes=1), now=NOW
        )
        assert set(findings[0].evidence) == set(burst)

    def test_extreme_deviation_escalates_to_high(self) -> None:
        events = self.baseline() + reads(50, spacing=1.0)
        findings = detect_anomalous_rate(
            events, multiplier=5.0, window=timedelta(minutes=1), now=NOW
        )
        assert findings[0].level is ThreatLevel.HIGH

    def test_uses_each_actor_s_own_baseline(self) -> None:
        """A busy service and a quiet user must not share one global ceiling."""
        busy = [
            event(
                index * 0.5,
                actor_id="batch-service",
                action="read",
                outcome="success",
                resource=f"row-{index}",
            )
            for index in range(1, 1200)
        ]
        findings = detect_anomalous_rate(busy, multiplier=5.0, window=timedelta(minutes=1), now=NOW)
        assert [finding.actor_id for finding in findings] == []

    def test_rejects_a_non_positive_multiplier(self) -> None:
        with pytest.raises(ValueError, match="multiplier must be positive"):
            detect_anomalous_rate([event(1)], multiplier=0.0, now=NOW)


class TestThreatDetector:
    """Tests for the detector that runs everything."""

    def test_returns_findings_sorted_by_descending_severity(self) -> None:
        detector = ThreatDetector(
            brute_force_threshold=5,
            brute_force_window=timedelta(minutes=5),
            exfiltration_threshold=20,
            exfiltration_window=timedelta(minutes=5),
            allowed_actions={"mallory": {"read"}},
        )
        events = [
            *failures(6, actor_id="alice"),
            *reads(25, actor_id="bob"),
            event(10, actor_id="mallory", action="delete", outcome="success"),
        ]
        findings = detector.detect(events, now=NOW)
        levels = [finding.level for finding in findings]
        assert levels == sorted(levels, key=lambda level: -_ORDER[level])
        assert findings[0].level is ThreatLevel.CRITICAL

    def test_finds_nothing_in_a_quiet_stream(self) -> None:
        detector = ThreatDetector(allowed_actions={"alice": {"read", "login"}})
        quiet = [
            event(index * 30, action="read", outcome="success", resource=f"doc-{index}")
            for index in range(1, 5)
        ]
        assert detector.detect(quiet, now=NOW) == []

    def test_injected_clock_is_used_when_now_is_omitted(self) -> None:
        """Deterministic without sleeping: the clock is the only time source."""
        detector = ThreatDetector(brute_force_threshold=5, clock=lambda: NOW)
        assert len(detector.detect(failures(5))) == 1

        stale_clock = ThreatDetector(
            brute_force_threshold=5, clock=lambda: NOW + timedelta(hours=2)
        )
        assert stale_clock.detect(failures(5)) == []

    def test_explicit_now_overrides_the_clock(self) -> None:
        detector = ThreatDetector(brute_force_threshold=5, clock=lambda: NOW + timedelta(hours=2))
        assert len(detector.detect(failures(5), now=NOW)) == 1

    def test_thresholds_are_injectable(self) -> None:
        events = failures(3)
        assert ThreatDetector(brute_force_threshold=5).detect(events, now=NOW) == []
        assert len(ThreatDetector(brute_force_threshold=3).detect(events, now=NOW)) == 1


class TestScoringAndBlocking:
    """Tests for score_threat_level, should_block, and the signal adapter."""

    @staticmethod
    def finding(level: ThreatLevel) -> ThreatFinding:
        """Build a finding at a given level.

        Args:
            level: Severity to assign.

        Returns:
            A :class:`ThreatFinding`.
        """
        return ThreatFinding(
            category=ThreatCategory.BRUTE_FORCE,
            level=level,
            actor_id="alice",
            evidence=(event(1),),
            explanation="test",
            detected_at=NOW,
        )

    def test_empty_findings_score_low(self) -> None:
        assert score_threat_level([]) is ThreatLevel.LOW

    def test_scoring_takes_the_maximum_not_the_average(self) -> None:
        findings = [self.finding(ThreatLevel.LOW)] * 20 + [self.finding(ThreatLevel.CRITICAL)]
        assert score_threat_level(findings) is ThreatLevel.CRITICAL

    def test_scoring_is_not_alphabetical(self) -> None:
        """'critical' < 'high' as strings; severity must not come from that."""
        pair = [self.finding(ThreatLevel.CRITICAL), self.finding(ThreatLevel.HIGH)]
        assert score_threat_level(pair) is ThreatLevel.CRITICAL

    def test_should_block_on_a_high_severity_actor(self) -> None:
        detector = ThreatDetector(brute_force_threshold=5)
        assert should_block("alice", failures(6), now=NOW, detector=detector) is True

    def test_should_not_block_a_quiet_actor(self) -> None:
        detector = ThreatDetector(brute_force_threshold=5)
        assert should_block("alice", failures(2), now=NOW, detector=detector) is False

    def test_should_not_block_a_different_actor(self) -> None:
        detector = ThreatDetector(brute_force_threshold=5)
        assert (
            should_block("bob", failures(6, actor_id="alice"), now=NOW, detector=detector) is False
        )

    def test_block_threshold_is_configurable(self) -> None:
        detector = ThreatDetector(brute_force_threshold=5)
        assert (
            should_block(
                "alice",
                failures(6),
                now=NOW,
                detector=detector,
                block_at=ThreatLevel.CRITICAL,
            )
            is False
        )

    def test_analyze_request_pattern_scopes_to_one_source(self) -> None:
        events = failures(6, actor_id="alice") + failures(6, actor_id="bob")
        signals = analyze_request_pattern("alice", events, window_seconds=300, now=NOW)
        assert len(signals) == 1
        assert isinstance(signals[0], ThreatSignal)
        assert signals[0].source_id == "alice"
        assert signals[0].level is ThreatLevel.HIGH

    def test_analyze_request_pattern_window_excludes_old_events(self) -> None:
        assert analyze_request_pattern("alice", failures(6), window_seconds=10, now=NOW) == []

    def test_analyze_request_pattern_rejects_a_non_positive_window(self) -> None:
        with pytest.raises(ValueError, match="window_seconds must be positive"):
            analyze_request_pattern("alice", failures(6), window_seconds=0, now=NOW)


class TestAuditEventInterop:
    """The protocol is structural, so real AuditEvents must work unchanged."""

    def test_audit_events_satisfy_the_protocol(self) -> None:
        log = AuditLog()
        for index in range(6):
            log.record(
                actor_id="alice",
                action="login",
                resource="session",
                outcome="failure",
                timestamp=NOW - timedelta(seconds=(6 - index) * 5),
            )
        findings = detect_brute_force(log.events(), threshold=5, now=NOW)
        assert len(findings) == 1
        assert findings[0].actor_id == "alice"
        assert len(findings[0].evidence) == 6

    def test_naive_timestamps_are_handled_without_raising(self) -> None:
        naive = [
            replace(item, timestamp=item.timestamp.replace(tzinfo=None)) for item in failures(6)
        ]
        assert len(detect_brute_force(naive, threshold=5, now=NOW)) == 1
