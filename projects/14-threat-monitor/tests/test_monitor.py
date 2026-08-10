"""Tests for the threat monitor: ingest, detection, ranking, and rendering.

Determinism strategy, since nothing here may sleep or reach a network:

* every timestamp is built relative to a fixed :data:`NOW`, and every
  detector call passes ``now=NOW`` explicitly. The framework makes ``now``
  and ``clock`` injectable precisely so that windowing can be tested without
  a ``sleep`` in sight;
* thresholds and windows are passed per call rather than relying on the
  shipped defaults, so a future retune of the defaults cannot silently turn
  a "fires" test into a "does not fire" test;
* the bundled ``data/events.jsonl`` is anchored to the same :data:`NOW`, so
  the end-to-end CLI assertions are exact.

Each detector gets the same four questions -- fires above threshold, stays
quiet below it, respects its window, and carries the specific triggering
events -- plus the two that matter most in aggregate: a benign-only stream
must produce **zero** findings, and findings must come back ranked.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ingest
import main
import pytest
from ingest import (
    DEMO_BENIGN_ACTORS,
    DEMO_SUSPECT_ACTORS,
    ChainIntegrityError,
    EventFormatError,
    StreamEvent,
    events_from_audit_log,
    events_from_chain_file,
    latest_timestamp,
    load_events,
    parse_stream,
)
from monitor import (
    DEFAULT_ROLE_GRANTS,
    build_detector,
    findings_at_or_above,
    level_rank,
    parse_category,
    parse_level,
    scan,
)
from report import (
    RECOMMENDED_ACTIONS,
    render_evidence,
    render_finding,
    render_report,
)

from cockpit.security.audit_logging import AuditLog
from cockpit.security.threat_detection import (
    ThreatCategory,
    ThreatFinding,
    ThreatLevel,
    detect_anomalous_rate,
    detect_brute_force,
    detect_data_exfiltration,
    detect_privilege_escalation,
)

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
FIVE_MINUTES = timedelta(minutes=5)
ONE_MINUTE = timedelta(minutes=1)


def at(seconds_before: float) -> datetime:
    """Build a timestamp a fixed distance before :data:`NOW`.

    Args:
        seconds_before: How many seconds before NOW the event occurred.

    Returns:
        The absolute UTC timestamp.
    """
    return NOW - timedelta(seconds=seconds_before)


def event(
    seconds_before: float,
    actor: str = "actor-1",
    action: str = "read",
    resource: str = "record/1",
    outcome: str = "success",
) -> StreamEvent:
    """Build one event relative to :data:`NOW`.

    Args:
        seconds_before: How many seconds before NOW the event occurred.
        actor: Actor id.
        action: Action name.
        resource: Resource id.
        outcome: Outcome name.

    Returns:
        The constructed :class:`StreamEvent`.
    """
    return StreamEvent(
        timestamp=at(seconds_before),
        actor_id=actor,
        action=action,
        resource=resource,
        outcome=outcome,
    )


def failures(count: int, *, actor: str = "actor-1", start: float, step: float) -> list[StreamEvent]:
    """Build a run of failed logins walking backwards from ``start``.

    Args:
        count: How many failures to build.
        actor: Actor id.
        start: Seconds before NOW for the first (oldest) failure.
        step: Seconds between consecutive failures.

    Returns:
        The failures, oldest first.
    """
    return [
        event(
            start - index * step,
            actor=actor,
            action="login",
            resource="auth/session",
            outcome="failure",
        )
        for index in range(count)
    ]


def reads(count: int, *, actor: str = "actor-1", start: float, step: float) -> list[StreamEvent]:
    """Build a run of successful exports walking backwards from ``start``.

    Args:
        count: How many reads to build.
        actor: Actor id.
        start: Seconds before NOW for the first (oldest) read.
        step: Seconds between consecutive reads.

    Returns:
        The reads, oldest first, each against a distinct resource.
    """
    return [
        event(start - index * step, actor=actor, action="export", resource=f"record/{index}")
        for index in range(count)
    ]


# --- brute force -----------------------------------------------------------


def test_brute_force_fires_above_threshold() -> None:
    """Five failures inside the window trip a threshold of five."""
    findings = detect_brute_force(
        failures(5, start=200, step=10), threshold=5, window=FIVE_MINUTES, now=NOW
    )

    assert len(findings) == 1
    assert findings[0].category is ThreatCategory.BRUTE_FORCE
    assert findings[0].actor_id == "actor-1"
    assert findings[0].level is ThreatLevel.HIGH


def test_brute_force_silent_below_threshold() -> None:
    """Four failures against a threshold of five produce nothing at all."""
    findings = detect_brute_force(
        failures(4, start=200, step=10), threshold=5, window=FIVE_MINUTES, now=NOW
    )

    assert findings == []


def test_brute_force_excludes_events_outside_the_window() -> None:
    """Failures older than the window do not count toward the threshold.

    Six failures exist, but three sit outside a 60s window, leaving three --
    below the threshold. The same six events fire against a 5 minute window,
    which proves the window and not the data made the difference.
    """
    events = failures(3, start=50, step=10) + failures(3, start=600, step=10)

    narrow = detect_brute_force(events, threshold=5, window=ONE_MINUTE, now=NOW)
    wide = detect_brute_force(events, threshold=5, window=FIVE_MINUTES * 3, now=NOW)

    assert narrow == []
    assert len(wide) == 1


def test_brute_force_evidence_is_the_triggering_events() -> None:
    """The finding carries exactly the in-window failures, oldest first."""
    in_window = failures(5, start=200, step=10)
    noise = [
        event(30, action="login", resource="auth/session", outcome="success"),
        event(900, action="login", resource="auth/session", outcome="failure"),
    ]

    finding = detect_brute_force(in_window + noise, threshold=5, window=FIVE_MINUTES, now=NOW)[0]

    assert len(finding.evidence) == 5
    assert [item.timestamp for item in finding.evidence] == sorted(
        item.timestamp for item in in_window
    )
    assert all(item.outcome == "failure" for item in finding.evidence)
    assert noise[0] not in finding.evidence
    assert noise[1] not in finding.evidence


def test_brute_force_counts_per_actor_not_globally() -> None:
    """Four failures each from two actors is not one eight-failure finding."""
    events = failures(4, actor="a", start=200, step=10) + failures(4, actor="b", start=200, step=10)

    assert detect_brute_force(events, threshold=5, window=FIVE_MINUTES, now=NOW) == []


# --- data exfiltration -----------------------------------------------------


def test_exfiltration_fires_above_threshold() -> None:
    """Ten exports inside the window trip a threshold of ten."""
    findings = detect_data_exfiltration(
        reads(10, start=200, step=10), threshold=10, window=FIVE_MINUTES, now=NOW
    )

    assert len(findings) == 1
    assert findings[0].category is ThreatCategory.DATA_EXFILTRATION
    assert "10 read/export operations" in findings[0].explanation


def test_exfiltration_silent_below_threshold() -> None:
    """Nine exports against a threshold of ten produce nothing."""
    findings = detect_data_exfiltration(
        reads(9, start=200, step=10), threshold=10, window=FIVE_MINUTES, now=NOW
    )

    assert findings == []


def test_exfiltration_excludes_events_outside_the_window() -> None:
    """Exports older than the window are not counted."""
    events = reads(5, start=50, step=5) + reads(5, start=600, step=5)

    narrow = detect_data_exfiltration(events, threshold=10, window=ONE_MINUTE, now=NOW)
    wide = detect_data_exfiltration(events, threshold=10, window=FIVE_MINUTES * 3, now=NOW)

    assert narrow == []
    assert len(wide) == 1


def test_exfiltration_evidence_excludes_denied_reads() -> None:
    """Denied reads are probing, not exfiltration, and stay out of evidence."""
    allowed = reads(10, start=200, step=10)
    denied = [event(100, action="export", resource="record/blocked", outcome="denied")]

    finding = detect_data_exfiltration(
        allowed + denied, threshold=10, window=FIVE_MINUTES, now=NOW
    )[0]

    assert len(finding.evidence) == 10
    assert denied[0] not in finding.evidence
    assert {item.resource for item in finding.evidence} == {item.resource for item in allowed}


# --- privilege escalation --------------------------------------------------


def test_privilege_escalation_fires_on_out_of_role_action() -> None:
    """An action the actor's role does not grant is reported."""
    events = [
        event(300, action="read"),
        event(120, action="admin_action", resource="admin/user-roles", outcome="denied"),
    ]

    findings = detect_privilege_escalation(
        events, allowed_actions={"actor-1": {"read"}}, window=ONE_MINUTE, now=NOW
    )

    assert len(findings) == 1
    assert findings[0].category is ThreatCategory.PRIVILEGE_ESCALATION
    # Denied means the control held, so it is not yet a failed-open CRITICAL.
    assert findings[0].level is ThreatLevel.HIGH


def test_privilege_escalation_silent_when_everything_is_in_role() -> None:
    """An actor acting entirely inside their grants produces nothing."""
    events = [event(300, action="read"), event(120, action="query")]

    assert (
        detect_privilege_escalation(
            events, allowed_actions={"actor-1": {"read", "query"}}, window=ONE_MINUTE, now=NOW
        )
        == []
    )


def test_privilege_escalation_respects_the_chain_window() -> None:
    """Sensitive access long after a role change is not treated as a chain.

    The window bounds the role-change-to-access gap, which is the only half
    of this detector it applies to. With no role grants supplied there is no
    out-of-role check, so the chain is the sole reason a finding could fire.
    """
    events = [
        event(300, action="role_change", resource="iam/role/actor-1"),
        event(210, action="export", resource="dataset/all"),
    ]

    outside = detect_privilege_escalation(events, window=timedelta(seconds=60), now=NOW)
    inside = detect_privilege_escalation(events, window=timedelta(seconds=120), now=NOW)

    assert outside == []
    assert len(inside) == 1
    assert inside[0].level is ThreatLevel.CRITICAL


def test_privilege_escalation_evidence_holds_the_chain() -> None:
    """Evidence names the role change and the access that followed it."""
    change = event(300, action="role_change", resource="iam/role/actor-1")
    access = event(280, action="read_secret", resource="vault/entry-17")
    unrelated = event(290, action="read", resource="record/9")

    finding = detect_privilege_escalation([change, unrelated, access], window=ONE_MINUTE, now=NOW)[
        0
    ]

    assert change in finding.evidence
    assert access in finding.evidence
    assert unrelated not in finding.evidence
    assert list(finding.evidence) == sorted(finding.evidence, key=lambda item: item.timestamp)


# --- anomalous rate --------------------------------------------------------


def spiking_actor(spike: int, *, baseline: int = 20) -> list[StreamEvent]:
    """Build an actor with a slow baseline and a burst inside the window.

    Args:
        spike: Events inside the trailing 60s window.
        baseline: Events spread over the preceding hour.

    Returns:
        The events, oldest first.
    """
    history = [event(3600 - index * 150, action="query") for index in range(baseline)]
    burst = [event(55 - index * 2, action="query") for index in range(spike)]
    return sorted(history + burst, key=lambda item: item.timestamp)


def test_anomalous_rate_fires_above_multiplier() -> None:
    """A burst far above the actor's own baseline is reported."""
    findings = detect_anomalous_rate(
        spiking_actor(20), multiplier=5.0, window=ONE_MINUTE, min_events=10, now=NOW
    )

    assert len(findings) == 1
    assert findings[0].category is ThreatCategory.ANOMALOUS_RATE
    assert "baseline" in findings[0].explanation


def test_anomalous_rate_silent_below_min_events() -> None:
    """Nine in-window events never reach a ten-event floor, whatever the ratio.

    The floor exists so a tiny absolute count cannot produce a huge ratio
    against a near-zero baseline. This is the anti-false-positive guard that
    matters most for low-traffic actors.
    """
    findings = detect_anomalous_rate(
        spiking_actor(9), multiplier=5.0, window=ONE_MINUTE, min_events=10, now=NOW
    )

    assert findings == []


def test_anomalous_rate_silent_for_a_steady_high_volume_actor() -> None:
    """A busy but constant actor is not anomalous relative to itself."""
    steady = [event(3600 - index * 6, action="process") for index in range(600)]

    findings = detect_anomalous_rate(
        steady, multiplier=5.0, window=ONE_MINUTE, min_events=10, now=NOW
    )

    assert findings == []


def test_anomalous_rate_excludes_events_outside_the_window() -> None:
    """A burst that has already aged out of the window counts as history."""
    history = [event(3600 - index * 150, action="query") for index in range(20)]
    aged_burst = [event(400 - index * 2, action="query") for index in range(20)]

    findings = detect_anomalous_rate(
        sorted(history + aged_burst, key=lambda item: item.timestamp),
        multiplier=5.0,
        window=ONE_MINUTE,
        min_events=10,
        now=NOW,
    )

    assert findings == []


def test_anomalous_rate_evidence_is_only_the_in_window_events() -> None:
    """Evidence holds the burst, not the baseline it was measured against."""
    events = spiking_actor(20)
    finding = detect_anomalous_rate(
        events, multiplier=5.0, window=ONE_MINUTE, min_events=10, now=NOW
    )[0]

    assert len(finding.evidence) == 20
    assert all(item.timestamp >= NOW - ONE_MINUTE for item in finding.evidence)


def test_anomalous_rate_needs_an_established_baseline() -> None:
    """A brand new actor has no normal to deviate from, so nothing fires."""
    burst = [event(55 - index * 2, action="query") for index in range(20)]

    findings = detect_anomalous_rate(
        burst, multiplier=5.0, window=ONE_MINUTE, min_events=10, now=NOW
    )

    assert findings == []


# --- aggregation, ranking, and scoping -------------------------------------


def bundled_events() -> list[StreamEvent]:
    """Load the bundled demo stream.

    Returns:
        Every event in ``data/events.jsonl``, oldest first.
    """
    return load_events()


def test_bundled_stream_is_anchored_to_now() -> None:
    """The fixture ends exactly at NOW, which is what makes replay exact."""
    events = bundled_events()

    assert latest_timestamp(events) == NOW
    assert len(events) == 235


def test_benign_only_stream_produces_zero_findings() -> None:
    """The anti-false-positive test: benign traffic must be silent.

    This is the direction that decides whether anyone keeps the detector
    switched on. The benign slice includes a high-volume service account, an
    analyst who mistyped a password, and a support agent with three failed
    logins -- all of which look like the real thing from a distance.
    """
    benign = [item for item in bundled_events() if item.actor_id in DEMO_BENIGN_ACTORS]
    report = scan(benign, now=NOW, detector=build_detector())

    assert len(benign) > 90, "benign slice should be substantial, not a token sample"
    assert report.findings == ()
    assert report.highest_level is ThreatLevel.LOW


def test_full_stream_flags_exactly_the_suspect_actors() -> None:
    """Every planted actor fires, and no benign one does."""
    report = scan(bundled_events(), now=NOW, detector=build_detector())
    flagged = {finding.actor_id for finding in report.findings}

    assert flagged == set(DEMO_SUSPECT_ACTORS)
    assert flagged.isdisjoint(DEMO_BENIGN_ACTORS)


def test_findings_are_ordered_by_descending_severity() -> None:
    """Findings come back most severe first, so triage reads top-down."""
    report = scan(bundled_events(), now=NOW, detector=build_detector())
    ranks = [level_rank(finding.level) for finding in report.findings]

    assert len(ranks) == 5
    assert ranks == sorted(ranks, reverse=True)
    assert report.findings[0].level is ThreatLevel.CRITICAL
    assert report.highest_level is ThreatLevel.CRITICAL


def test_every_finding_carries_evidence() -> None:
    """No finding in the demo report is un-investigable."""
    report = scan(bundled_events(), now=NOW, detector=build_detector())

    assert report.findings
    for finding in report.findings:
        assert finding.evidence, f"{finding.category} for {finding.actor_id} has no evidence"
        assert all(item.actor_id == finding.actor_id for item in finding.evidence)


def test_actor_scope_filters_findings_without_changing_them() -> None:
    """Scoping is a view: the same detection runs, fewer findings are shown."""
    events = bundled_events()
    everything = scan(events, now=NOW, detector=build_detector())
    scoped = scan(events, now=NOW, detector=build_detector(), actor="dana@corp")

    expected = [f for f in everything.findings if f.actor_id == "dana@corp"]
    assert list(scoped.findings) == expected
    assert scoped.suppressed_count == len(everything.findings) - len(expected)
    assert scoped.events_scanned == everything.events_scanned


def test_category_scope_selects_one_detector_family() -> None:
    """``--category`` narrows to one kind of behavior."""
    report = scan(
        bundled_events(),
        now=NOW,
        detector=build_detector(),
        category=ThreatCategory.BRUTE_FORCE,
    )

    assert [finding.actor_id for finding in report.findings] == ["mallory@corp"]
    assert report.category_scope is ThreatCategory.BRUTE_FORCE


def test_findings_at_or_above_respects_the_threshold() -> None:
    """The exit-code gate counts only findings at or above the given level."""
    report = scan(bundled_events(), now=NOW, detector=build_detector())

    assert len(findings_at_or_above(report, ThreatLevel.LOW)) == 5
    assert len(findings_at_or_above(report, ThreatLevel.HIGH)) == 5
    assert len(findings_at_or_above(report, ThreatLevel.CRITICAL)) == 2


def test_role_grants_cover_every_actor_in_the_stream() -> None:
    """An actor missing from the grants map is silently unchecked, so guard it."""
    actors = {item.actor_id for item in bundled_events()}

    assert actors <= set(DEFAULT_ROLE_GRANTS)


def test_parse_level_and_category_reject_nonsense() -> None:
    """Bad flag values name the valid options instead of raising KeyError."""
    assert parse_level("HIGH") is ThreatLevel.HIGH
    assert parse_category("Brute_Force") is ThreatCategory.BRUTE_FORCE

    with pytest.raises(ValueError, match="critical"):
        parse_level("severe")
    with pytest.raises(ValueError, match="brute_force"):
        parse_category("phishing")


# --- ingest ----------------------------------------------------------------


def test_parse_stream_skips_blank_lines_and_sorts() -> None:
    """Blank lines are ignored and out-of-order input comes back sorted."""
    text = "\n".join(
        [
            json.dumps(
                {
                    "timestamp": "2026-08-08T12:00:00+00:00",
                    "actor_id": "a",
                    "action": "read",
                    "resource": "r",
                    "outcome": "success",
                }
            ),
            "",
            json.dumps(
                {
                    "timestamp": "2026-08-08T11:00:00Z",
                    "actor_id": "a",
                    "action": "login",
                    "resource": "auth/session",
                    "outcome": "success",
                }
            ),
        ]
    )

    events = parse_stream(text.splitlines(), source="demo")

    assert [item.action for item in events] == ["login", "read"]
    assert events[0].timestamp.tzinfo is not None


def test_parse_stream_reports_the_offending_line_number() -> None:
    """A malformed line is named by file and line, not by stack trace."""
    good = json.dumps(
        {
            "timestamp": "2026-08-08T12:00:00Z",
            "actor_id": "a",
            "action": "read",
            "resource": "r",
            "outcome": "success",
        }
    )

    with pytest.raises(EventFormatError, match="line 2"):
        parse_stream([good, "{not json"], source="demo.jsonl")


def test_parse_stream_names_the_missing_fields() -> None:
    """A line short of a required field says which field."""
    payload = json.dumps({"timestamp": "2026-08-08T12:00:00Z", "actor_id": "a"})

    with pytest.raises(EventFormatError, match="action, resource, outcome"):
        parse_stream([payload], source="demo.jsonl")


def test_parse_stream_rejects_an_unparseable_timestamp() -> None:
    """A bad timestamp is a format error, not a silent drop."""
    payload = json.dumps(
        {
            "timestamp": "last tuesday",
            "actor_id": "a",
            "action": "read",
            "resource": "r",
            "outcome": "success",
        }
    )

    with pytest.raises(EventFormatError, match="ISO-8601"):
        parse_stream([payload], source="demo.jsonl")


def test_parse_stream_rejects_a_non_object_line() -> None:
    """A JSON array where an object belongs is caught with its line number."""
    with pytest.raises(EventFormatError, match="expected a JSON object"):
        parse_stream(["[1, 2, 3]"], source="demo.jsonl")


def test_load_events_missing_file_is_a_clear_error() -> None:
    """A missing stream raises FileNotFoundError naming the path."""
    with pytest.raises(FileNotFoundError, match="nope.jsonl"):
        load_events(Path("nope.jsonl"))


def test_load_events_since_trims_the_stream() -> None:
    """``since`` is applied at load time, before any detector sees the data."""
    cutoff = NOW - timedelta(minutes=5)
    trimmed = load_events(since=cutoff)

    assert trimmed
    assert all(item.timestamp >= cutoff for item in trimmed)
    assert len(trimmed) < len(bundled_events())


def build_audit_log() -> AuditLog:
    """Record a small brute-force burst into a live audit log.

    Returns:
        An :class:`AuditLog` holding six failed logins for one actor.
    """
    log = AuditLog()
    for index in range(6):
        log.record(
            "mallory@corp",
            "login",
            "auth/session",
            "failure",
            {"attempt": index},
            timestamp=at(200 - index * 10),
        )
    return log


def test_events_from_a_live_audit_log_feed_the_detectors() -> None:
    """The live path: an AuditLog chain converts straight into events."""
    events = events_from_audit_log(build_audit_log())

    assert len(events) == 6
    findings = detect_brute_force(events, threshold=5, window=FIVE_MINUTES, now=NOW)
    assert len(findings) == 1
    assert findings[0].actor_id == "mallory@corp"


def test_a_tampered_audit_chain_is_refused() -> None:
    """Findings from an editable log say whatever the editor wanted."""
    log = build_audit_log()
    original = log.records[2]
    log.records[2] = replace(original, event=replace(original.event, outcome="success"))

    with pytest.raises(ChainIntegrityError, match="tampered"):
        events_from_audit_log(log)

    # The escape hatch exists, and it is explicit at the call site.
    assert len(events_from_audit_log(log, require_intact_chain=False)) == 6


def test_events_round_trip_through_an_audit_chain_file(tmp_path: Path) -> None:
    """The replay path over a durable AuditLog sink verifies before parsing."""
    sink = tmp_path / "chain.jsonl"
    log = AuditLog(sink_path=sink)
    for index in range(3):
        log.record(
            "alice@corp", "read", f"record/{index}", "success", timestamp=at(300 - index * 10)
        )

    events = events_from_chain_file(sink)

    assert [item.resource for item in events] == ["record/0", "record/1", "record/2"]
    assert events_from_chain_file(tmp_path / "absent.jsonl") == []


def test_a_tampered_chain_file_is_refused(tmp_path: Path) -> None:
    """Editing a stored record is detected before any detector runs."""
    sink = tmp_path / "chain.jsonl"
    log = AuditLog(sink_path=sink)
    for index in range(3):
        log.record("alice@corp", "read", f"record/{index}", "success", timestamp=at(300))

    lines = sink.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[1])
    payload["event"]["outcome"] = "denied"
    lines[1] = json.dumps(payload)
    sink.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ChainIntegrityError):
        events_from_chain_file(sink)


# --- rendering -------------------------------------------------------------


def sample_finding(evidence_count: int = 3) -> ThreatFinding:
    """Build a finding with a known evidence run.

    Args:
        evidence_count: How many evidence events to attach.

    Returns:
        A :class:`ThreatFinding` in the brute-force category.
    """
    return ThreatFinding(
        category=ThreatCategory.BRUTE_FORCE,
        level=ThreatLevel.HIGH,
        actor_id="actor-1",
        evidence=tuple(failures(evidence_count, start=200, step=10)),
        explanation="Actor 'actor-1' recorded failures.",
        detected_at=NOW,
    )


def test_render_finding_shows_evidence_and_an_action() -> None:
    """A rendered finding is investigable: who, why, evidence, next step."""
    text = render_finding(sample_finding(), index=1)

    assert "actor=actor-1" in text
    assert "brute_force" in text
    assert RECOMMENDED_ACTIONS[ThreatCategory.BRUTE_FORCE].split(".")[0] in text
    assert text.count("auth/session") == 3


def test_render_evidence_keeps_the_first_and_last_events() -> None:
    """Truncation keeps the head and the tail, and says how many it cut."""
    evidence = tuple(failures(10, start=200, step=10))

    lines = render_evidence(evidence, max_events=4)
    body = "\n".join(lines)

    assert evidence[0].timestamp.isoformat() in body
    assert evidence[-1].timestamp.isoformat() in body
    # max_events=4 shows 3 head + 1 tail, so 6 of the 10 are elided.
    assert "6 more event(s) hidden" in body


def test_render_evidence_zero_means_everything() -> None:
    """``--evidence 0`` prints every event with no elision marker."""
    lines = render_evidence(tuple(failures(10, start=200, step=10)), max_events=0)

    assert len(lines) == 10
    assert not any("hidden" in line for line in lines)


def test_render_report_lists_flagged_and_clean_actors() -> None:
    """The report names who was cleared, not only who was flagged."""
    report = scan(bundled_events(), now=NOW, detector=build_detector())
    text = render_report(report)

    assert "Flagged: carol@corp, dana@corp, mallory@corp, svc-scraper" in text
    assert "Clean:   alice@corp, bob@corp, svc-batch" in text
    for finding in report.findings:
        assert finding.evidence[0].resource in text


def test_render_report_on_a_clean_stream_says_so_honestly() -> None:
    """A clean report says thresholds held, not that nothing happened."""
    benign = [item for item in bundled_events() if item.actor_id in DEMO_BENIGN_ACTORS]
    text = render_report(scan(benign, now=NOW, detector=build_detector()))

    assert "No findings" in text
    assert "thresholds were not crossed" in text


# --- CLI -------------------------------------------------------------------


def test_cli_dry_run_exits_two_and_prints_every_finding(capsys) -> None:
    """The bundled replay fires, so the process exits 2 by default."""
    code = main.main(["--dry-run"])
    out = capsys.readouterr().out

    assert code == 2
    assert "Findings        5 (highest severity: CRITICAL)" in out
    assert "Evaluated at    2026-08-08T12:00:00+00:00" in out
    for actor in DEMO_SUSPECT_ACTORS:
        assert actor in out
    assert "VERDICT: FAIL" in out


def test_cli_actor_scope_narrows_the_report(capsys) -> None:
    """``--actor`` shows one principal and admits what it hid."""
    code = main.main(["--dry-run", "--actor", "mallory@corp"])
    out = capsys.readouterr().out

    assert code == 2
    assert "Scope           mallory@corp / all categories" in out
    assert "4 finding(s) hidden by this scope" in out
    assert "carol@corp" not in out


def test_cli_benign_actor_scope_exits_zero(capsys) -> None:
    """A scope with no findings is a clean run, not a silent one."""
    code = main.main(["--dry-run", "--actor", "svc-batch"])
    out = capsys.readouterr().out

    assert code == 0
    assert "No findings" in out
    assert "VERDICT: PASS" in out


def test_cli_fail_at_is_configurable(capsys) -> None:
    """Raising the bar changes the exit code, not the report contents."""
    assert main.main(["--dry-run", "--fail-at", "critical"]) == 2
    capsys.readouterr()

    code = main.main(["--dry-run", "--category", "anomalous_rate", "--fail-at", "critical"])
    out = capsys.readouterr().out

    assert code == 0
    assert "VERDICT: PASS -- nothing at or above CRITICAL" in out
    assert "anomalous_rate" in out


def test_cli_evidence_zero_prints_the_full_run(capsys) -> None:
    """``--evidence 0`` is the escape hatch the elision marker advertises."""
    code = main.main(["--dry-run", "--actor", "dana@corp", "--evidence", "0"])
    out = capsys.readouterr().out

    assert code == 2
    # Narrow to the evidence elision marker: the scope line legitimately
    # says "N finding(s) hidden by this scope", which is unrelated here.
    assert "more event(s) hidden" not in out
    assert out.count("record/5059") >= 2


def test_cli_since_trims_the_stream(capsys) -> None:
    """``--since`` reduces the events scanned, and the report says so."""
    code = main.main(["--dry-run", "--since", "2026-08-08T11:58:00Z"])
    out = capsys.readouterr().out

    assert code in (0, 2)
    assert "Events scanned  235" not in out


def test_cli_rejects_bad_flag_values(capsys) -> None:
    """Unusable flags exit 1 -- never 0, which would read as a clean scan."""
    assert main.main(["--dry-run", "--category", "phishing"]) == 1
    assert main.main(["--dry-run", "--fail-at", "severe"]) == 1
    assert main.main(["--dry-run", "--evidence", "-1"]) == 1
    assert capsys.readouterr().out == ""


def test_cli_missing_stream_exits_one(capsys) -> None:
    """A missing input file is a configuration error, not a clean report."""
    assert main.main(["--dry-run", "--events", "does-not-exist.jsonl"]) == 1
    assert capsys.readouterr().out == ""


def test_cli_reads_an_audit_chain_file(tmp_path: Path, capsys) -> None:
    """``--chain`` runs the detectors over a verified AuditLog sink."""
    sink = tmp_path / "chain.jsonl"
    log = AuditLog(sink_path=sink)
    for index in range(8):
        log.record(
            "mallory@corp",
            "login",
            "auth/session",
            "failure",
            timestamp=at(200 - index * 10),
        )

    code = main.main(["--dry-run", "--chain", str(sink)])
    out = capsys.readouterr().out

    assert code == 2
    assert "brute_force" in out
    assert "mallory@corp" in out


def test_cli_refuses_a_tampered_chain(tmp_path: Path, capsys) -> None:
    """A broken chain exits 1 rather than reporting attacker-chosen findings."""
    sink = tmp_path / "chain.jsonl"
    log = AuditLog(sink_path=sink)
    for index in range(3):
        log.record("alice@corp", "read", f"record/{index}", "success", timestamp=at(300))

    lines = sink.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    payload["event"]["actor_id"] = "someone-else"
    lines[0] = json.dumps(payload)
    sink.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert main.main(["--dry-run", "--chain", str(sink)]) == 1
    assert capsys.readouterr().out == ""


def test_default_events_path_points_at_the_bundled_stream() -> None:
    """The bundled stream resolves relative to the package, not the CWD."""
    assert ingest.DEFAULT_EVENTS_PATH.is_file()
    assert ingest.DEFAULT_EVENTS_PATH.name == "events.jsonl"
