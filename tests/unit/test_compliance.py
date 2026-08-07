"""Unit tests for cockpit/security/compliance.py.

Behavioral tests for the mechanical controls: an access export never leaks
another subject's records, erasure reports what it could not erase instead
of skipping it, consent is evaluated at a point in time, over-retention is
found, misplaced PHI is found, and a self-approved change is caught. One
structural test pins the honesty property -- there is no boolean verdict
anywhere on the report or the finding, and there never should be.

Every time is injected; nothing here sleeps.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta

import pytest

from cockpit.config import feature_flags
from cockpit.security import compliance
from cockpit.security.audit_logging import AuditLog
from cockpit.security.compliance import (
    CONTROL_GDPR_CONSENT,
    CONTROL_GDPR_ERASURE_EXCEPTION,
    CONTROL_GDPR_RETENTION,
    CONTROL_GDPR_RETENTION_POLICY_GAP,
    CONTROL_HIPAA_MINIMUM_NECESSARY,
    CONTROL_HIPAA_PHI_PLACEMENT,
    CONTROL_SOX_CHANGE_AUDIT,
    CONTROL_SOX_SEGREGATION_OF_DUTIES,
    DISCLAIMER,
    ERASURE_TOKEN,
    ComplianceContext,
    ComplianceFinding,
    ComplianceFramework,
    ComplianceReport,
    ConsentLedger,
    DataRecord,
    ErasureBlocker,
    ErasureMode,
    FieldAccess,
    RetentionPolicy,
    Severity,
    audited_change_ids,
    check_change_audit_completeness,
    check_minimum_necessary,
    check_retention,
    check_segregation_of_duties,
    detect_undesignated_phi,
    erase_subject_records,
    export_subject_data_json,
    export_subject_records,
    generate_compliance_report,
    references_subject,
    render_report_markdown,
    run_compliance_check,
)

NOW = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)
SSN = "123-45-6789"


@pytest.fixture
def security_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force the ``security`` feature flag on for one test.

    Yields:
        None.
    """
    monkeypatch.setitem(feature_flags.FRAMEWORKS_ENABLED, "security", True)
    yield


@pytest.fixture
def security_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force the ``security`` feature flag off for one test.

    Yields:
        None.
    """
    monkeypatch.setitem(feature_flags.FRAMEWORKS_ENABLED, "security", False)
    yield


@dataclass(frozen=True)
class FakeApproval:
    """Minimal stand-in satisfying the ``ApprovalRecordLike`` protocol.

    Structural typing is the point: the compliance checks never import the
    real approval workflow, so a three-field fixture is a complete substitute.

    Attributes:
        change_id: Identifier of the change.
        requested_by: Principal that requested it.
        approved_by: Principal that approved it, if any.
    """

    change_id: str
    requested_by: str
    approved_by: str | None


def record(
    record_id: str,
    subject_id: str,
    *,
    category: str = "profile",
    age_days: int = 1,
    legal_hold: bool = False,
    retention_obligation_until: datetime | None = None,
    fields_: dict[str, object] | None = None,
    related: tuple[str, ...] = (),
) -> DataRecord:
    """Build a :class:`DataRecord` with sane defaults.

    Args:
        record_id: Record identifier.
        subject_id: Primary data subject.
        category: Data category.
        age_days: How many days before :data:`NOW` the record was created.
        legal_hold: Whether the record is frozen for litigation.
        retention_obligation_until: Statutory retention deadline, if any.
        fields_: Field values for the record.
        related: Additional subjects the record references.

    Returns:
        The constructed record.
    """
    return DataRecord(
        record_id=record_id,
        subject_id=subject_id,
        category=category,
        created_at=NOW - timedelta(days=age_days),
        fields=fields_ or {"name": "Test Person"},
        legal_hold=legal_hold,
        retention_obligation_until=retention_obligation_until,
        related_subject_ids=related,
    )


class TestRightOfAccess:
    """Tests for the GDPR right-of-access export."""

    def test_export_returns_only_the_requesting_subjects_records(self) -> None:
        records = [
            record("r1", "subj-1"),
            record("r2", "subj-2"),
            record("r3", "subj-1"),
            record("r4", "subj-3"),
        ]
        exported = export_subject_records(records, "subj-1")
        assert [r.record_id for r in exported] == ["r1", "r3"]
        assert all(r.subject_id == "subj-1" for r in exported)

    def test_export_does_not_leak_records_with_a_prefix_matching_id(self) -> None:
        # The failure that matters: substring matching would hand subj-1 the
        # records of subj-10 and subj-11.
        records = [record("r1", "subj-1"), record("r2", "subj-10"), record("r3", "subj-11")]
        exported = export_subject_records(records, "subj-1")
        assert [r.record_id for r in exported] == ["r1"]

    def test_export_includes_records_that_only_reference_the_subject(self) -> None:
        records = [
            record("r1", "subj-2", related=("subj-1",)),
            record("r2", "subj-2", fields_={"counterparty": "subj-1"}),
            record("r3", "subj-2", fields_={"participants": ["subj-9", "subj-1"]}),
            record("r4", "subj-2"),
        ]
        exported = export_subject_records(records, "subj-1")
        assert [r.record_id for r in exported] == ["r1", "r2", "r3"]

    def test_export_on_unknown_subject_is_empty(self) -> None:
        assert export_subject_records([record("r1", "subj-1")], "nobody") == []

    def test_empty_subject_id_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            export_subject_records([record("r1", "subj-1")], "")

    def test_references_subject_matches_exactly(self) -> None:
        target = record("r1", "subj-10")
        assert references_subject(target, "subj-10")
        assert not references_subject(target, "subj-1")


class TestPortability:
    """Tests for the GDPR portability export."""

    def test_export_is_valid_json_with_only_the_subjects_records(self) -> None:
        records = [record("r1", "subj-1"), record("r2", "subj-2")]
        payload = json.loads(export_subject_data_json(records, "subj-1"))
        assert payload["subject_id"] == "subj-1"
        assert payload["record_count"] == 1
        assert [r["record_id"] for r in payload["records"]] == ["r1"]
        assert "subj-2" not in json.dumps(payload)

    def test_export_renders_datetimes_as_iso_strings(self) -> None:
        payload = json.loads(export_subject_data_json([record("r1", "subj-1")], "subj-1"))
        created = payload["records"][0]["created_at"]
        assert datetime.fromisoformat(created).tzinfo is not None


class TestErasure:
    """Tests for the GDPR right to erasure and its exceptions."""

    def test_erasure_removes_only_the_requesting_subjects_records(self) -> None:
        records = [record("r1", "subj-1"), record("r2", "subj-2"), record("r3", "subj-1")]
        outcome = erase_subject_records(records, "subj-1", now=NOW)
        assert set(outcome.erased_record_ids) == {"r1", "r3"}
        assert [r.record_id for r in outcome.remaining_records] == ["r2"]
        assert outcome.exceptions == ()

    def test_legal_hold_is_reported_not_silently_skipped(self) -> None:
        records = [record("r1", "subj-1"), record("r2", "subj-1", legal_hold=True)]
        outcome = erase_subject_records(records, "subj-1", now=NOW)

        assert outcome.erased_record_ids == ("r1",)
        assert len(outcome.exceptions) == 1
        exception = outcome.exceptions[0]
        assert exception.record_id == "r2"
        assert exception.blocker is ErasureBlocker.LEGAL_HOLD
        assert "legal hold" in exception.description

        # The held record survives, and the fact that it survived is a finding.
        assert [r.record_id for r in outcome.remaining_records] == ["r2"]
        assert len(outcome.findings) == 1
        assert outcome.findings[0].control_id == CONTROL_GDPR_ERASURE_EXCEPTION
        assert outcome.findings[0].record_id == "r2"
        assert outcome.findings[0].severity is Severity.HIGH

    def test_retention_obligation_blocks_erasure_and_reports_the_deadline(self) -> None:
        deadline = NOW + timedelta(days=365)
        records = [record("r1", "subj-1", retention_obligation_until=deadline)]
        outcome = erase_subject_records(records, "subj-1", now=NOW)

        assert outcome.erased_record_ids == ()
        assert outcome.exceptions[0].blocker is ErasureBlocker.RETENTION_OBLIGATION
        assert outcome.exceptions[0].blocked_until == deadline
        assert deadline.isoformat() in outcome.exceptions[0].description

    def test_lapsed_retention_obligation_no_longer_blocks_erasure(self) -> None:
        records = [record("r1", "subj-1", retention_obligation_until=NOW - timedelta(days=1))]
        outcome = erase_subject_records(records, "subj-1", now=NOW)
        assert outcome.erased_record_ids == ("r1",)
        assert outcome.exceptions == ()

    def test_redact_mode_keeps_the_row_and_blanks_the_values(self) -> None:
        records = [record("r1", "subj-1", fields_={"name": "Test Person", "email": "a@b.com"})]
        outcome = erase_subject_records(records, "subj-1", mode=ErasureMode.REDACT, now=NOW)

        assert outcome.erased_record_ids == ("r1",)
        remaining = outcome.remaining_records[0]
        assert remaining.record_id == "r1"
        assert remaining.subject_id == ERASURE_TOKEN
        assert set(remaining.fields) == {"name", "email"}
        assert set(remaining.fields.values()) == {ERASURE_TOKEN}

    def test_erasure_does_not_mutate_the_input_records(self) -> None:
        records = [record("r1", "subj-1")]
        erase_subject_records(records, "subj-1", now=NOW)
        assert [r.record_id for r in records] == ["r1"]

    def test_empty_subject_id_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            erase_subject_records([record("r1", "subj-1")], "", now=NOW)


class TestConsent:
    """Tests for the consent ledger."""

    def test_never_granted_purpose_is_invalid(self) -> None:
        ledger = ConsentLedger()
        status = ledger.status("subj-1", "marketing", as_of=NOW)
        assert status.is_valid is False
        assert status.decided_at is None
        assert "No consent decision" in status.reason

    def test_granted_purpose_is_valid(self) -> None:
        ledger = ConsentLedger()
        ledger.grant("subj-1", "marketing", at=NOW - timedelta(days=1))
        assert ledger.has_valid_consent("subj-1", "marketing", as_of=NOW) is True

    def test_withdrawal_invalidates_a_previously_granted_purpose(self) -> None:
        ledger = ConsentLedger()
        ledger.grant("subj-1", "marketing", at=NOW - timedelta(days=2))
        ledger.withdraw("subj-1", "marketing", at=NOW - timedelta(days=1))
        status = ledger.status("subj-1", "marketing", as_of=NOW)
        assert status.is_valid is False
        assert "withdrawn" in status.reason

    def test_consent_is_evaluated_at_the_supplied_time(self) -> None:
        granted_at = NOW - timedelta(days=5)
        withdrawn_at = NOW - timedelta(days=2)
        ledger = ConsentLedger()
        ledger.grant("subj-1", "marketing", at=granted_at)
        ledger.withdraw("subj-1", "marketing", at=withdrawn_at)

        # Before the grant: nothing on file yet.
        assert (
            ledger.has_valid_consent("subj-1", "marketing", as_of=granted_at - timedelta(seconds=1))
            is False
        )
        # Between grant and withdrawal: valid.
        assert (
            ledger.has_valid_consent(
                "subj-1", "marketing", as_of=withdrawn_at - timedelta(seconds=1)
            )
            is True
        )
        # After the withdrawal: invalid.
        assert ledger.has_valid_consent("subj-1", "marketing", as_of=NOW) is False

    def test_regranting_after_withdrawal_restores_validity(self) -> None:
        ledger = ConsentLedger()
        ledger.grant("subj-1", "marketing", at=NOW - timedelta(days=3))
        ledger.withdraw("subj-1", "marketing", at=NOW - timedelta(days=2))
        ledger.grant("subj-1", "marketing", at=NOW - timedelta(days=1))
        assert ledger.has_valid_consent("subj-1", "marketing", as_of=NOW) is True

    def test_consent_is_scoped_per_purpose_and_subject(self) -> None:
        ledger = ConsentLedger()
        ledger.grant("subj-1", "marketing", at=NOW - timedelta(days=1))
        assert ledger.has_valid_consent("subj-1", "analytics", as_of=NOW) is False
        assert ledger.has_valid_consent("subj-2", "marketing", as_of=NOW) is False

    def test_history_is_append_only_and_keeps_both_decisions(self) -> None:
        ledger = ConsentLedger()
        ledger.grant("subj-1", "marketing", at=NOW - timedelta(days=2))
        ledger.withdraw("subj-1", "marketing", at=NOW - timedelta(days=1))
        history = ledger.history("subj-1", "marketing")
        assert [decision.granted for decision in history] == [True, False]

    def test_naive_timestamps_are_treated_as_utc(self) -> None:
        ledger = ConsentLedger()
        ledger.grant("subj-1", "marketing", at=datetime(2026, 8, 1, 12, 0, 0))
        assert ledger.history("subj-1")[0].decided_at.tzinfo is not None

    def test_empty_purpose_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ConsentLedger().grant("subj-1", "")


class TestRetention:
    """Tests for retention-window checking."""

    def test_record_past_its_window_is_flagged(self) -> None:
        policies = [RetentionPolicy(category="profile", max_age_days=30)]
        findings = check_retention([record("r1", "subj-1", age_days=40)], policies, now=NOW)
        assert len(findings) == 1
        assert findings[0].control_id == CONTROL_GDPR_RETENTION
        assert findings[0].record_id == "r1"
        assert findings[0].severity is Severity.HIGH
        assert findings[0].evidence["overdue_days"] == 10

    def test_record_inside_its_window_is_not_flagged(self) -> None:
        policies = [RetentionPolicy(category="profile", max_age_days=30)]
        findings = check_retention([record("r1", "subj-1", age_days=10)], policies, now=NOW)
        assert findings == []

    def test_record_exactly_at_the_window_boundary_is_not_flagged(self) -> None:
        policies = [RetentionPolicy(category="profile", max_age_days=30)]
        findings = check_retention([record("r1", "subj-1", age_days=30)], policies, now=NOW)
        assert findings == []

    def test_over_retention_under_legal_hold_is_downgraded_not_hidden(self) -> None:
        policies = [RetentionPolicy(category="profile", max_age_days=30)]
        findings = check_retention(
            [record("r1", "subj-1", age_days=90, legal_hold=True)], policies, now=NOW
        )
        assert len(findings) == 1
        assert findings[0].severity is Severity.LOW
        assert findings[0].evidence["justification"] == "a legal hold"

    def test_category_without_a_policy_is_reported_as_a_gap(self) -> None:
        findings = check_retention([record("r1", "subj-1", category="telemetry")], [], now=NOW)
        assert len(findings) == 1
        assert findings[0].control_id == CONTROL_GDPR_RETENTION_POLICY_GAP
        assert findings[0].evidence["category"] == "telemetry"

    def test_uncovered_category_is_reported_once_not_per_record(self) -> None:
        records = [record(f"r{i}", "subj-1", category="telemetry") for i in range(5)]
        findings = check_retention(records, [], now=NOW)
        assert len(findings) == 1

    def test_non_positive_retention_window_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            RetentionPolicy(category="profile", max_age_days=0)


class TestPhiPlacement:
    """Tests for PHI found in fields not designated to hold it."""

    def test_phi_in_an_undesignated_field_is_flagged(self) -> None:
        target = record("r1", "subj-1", fields_={"notes": f"patient ssn {SSN}"})
        findings = detect_undesignated_phi([target], designated_fields=("ssn",))
        assert len(findings) == 1
        assert findings[0].framework is ComplianceFramework.HIPAA
        assert findings[0].control_id == CONTROL_HIPAA_PHI_PLACEMENT
        assert findings[0].evidence["field"] == "notes"
        assert "ssn" in findings[0].evidence["pii_categories"]

    def test_phi_in_a_designated_field_is_not_flagged(self) -> None:
        target = record("r1", "subj-1", fields_={"ssn": SSN})
        assert detect_undesignated_phi([target], designated_fields=("ssn",)) == []

    def test_field_without_phi_is_not_flagged(self) -> None:
        target = record("r1", "subj-1", fields_={"notes": "follow up next week"})
        assert detect_undesignated_phi([target], designated_fields=("ssn",)) == []

    def test_finding_never_reproduces_the_phi_it_reports(self) -> None:
        target = record("r1", "subj-1", fields_={"notes": f"patient ssn {SSN}"})
        finding = detect_undesignated_phi([target], designated_fields=("ssn",))[0]
        serialized = finding.description + json.dumps(dict(finding.evidence))
        assert SSN not in serialized
        assert "[REDACTED:ssn]" in finding.evidence["masked_excerpt"]

    def test_non_string_field_values_are_ignored(self) -> None:
        target = record("r1", "subj-1", fields_={"visit_count": 4, "active": True})
        assert detect_undesignated_phi([target], designated_fields=()) == []


class TestMinimumNecessary:
    """Tests for the HIPAA minimum-necessary check."""

    def test_fields_beyond_the_purpose_are_flagged(self) -> None:
        findings = check_minimum_necessary(
            "billing",
            ["account_id", "amount", "diagnosis"],
            {"billing": ["account_id", "amount"]},
            actor_id="clerk-1",
        )
        assert len(findings) == 1
        assert findings[0].control_id == CONTROL_HIPAA_MINIMUM_NECESSARY
        assert findings[0].evidence["excess_fields"] == ["diagnosis"]

    def test_access_within_the_purpose_is_not_flagged(self) -> None:
        findings = check_minimum_necessary(
            "billing", ["account_id"], {"billing": ["account_id", "amount"]}
        )
        assert findings == []

    def test_undeclared_purpose_is_itself_a_finding(self) -> None:
        findings = check_minimum_necessary("research", ["diagnosis"], {"billing": ["amount"]})
        assert len(findings) == 1
        assert findings[0].severity is Severity.HIGH
        assert "no declared field set" in findings[0].description


class TestSegregationOfDuties:
    """Tests for the SOX segregation-of-duties check."""

    def test_same_principal_creating_and_approving_is_flagged(self) -> None:
        findings = check_segregation_of_duties([FakeApproval("chg-1", "alice", "alice")])
        assert len(findings) == 1
        assert findings[0].framework is ComplianceFramework.SOX
        assert findings[0].control_id == CONTROL_SOX_SEGREGATION_OF_DUTIES
        assert findings[0].severity is Severity.CRITICAL
        assert findings[0].record_id == "chg-1"

    def test_different_principals_are_not_flagged(self) -> None:
        assert check_segregation_of_duties([FakeApproval("chg-1", "alice", "bob")]) == []

    def test_self_approval_cannot_hide_behind_case_or_whitespace(self) -> None:
        findings = check_segregation_of_duties([FakeApproval("chg-1", "Alice", "  alice ")])
        assert len(findings) == 1

    def test_unapproved_change_is_not_a_segregation_finding(self) -> None:
        assert check_segregation_of_duties([FakeApproval("chg-1", "alice", None)]) == []

    def test_only_the_offending_change_is_flagged(self) -> None:
        approvals = [
            FakeApproval("chg-1", "alice", "bob"),
            FakeApproval("chg-2", "carol", "carol"),
            FakeApproval("chg-3", "dave", "erin"),
        ]
        findings = check_segregation_of_duties(approvals)
        assert [f.record_id for f in findings] == ["chg-2"]


class TestChangeAuditCompleteness:
    """Tests for changes lacking an audit record."""

    def test_change_without_an_audit_record_is_flagged(self) -> None:
        approvals = [FakeApproval("chg-1", "alice", "bob"), FakeApproval("chg-2", "alice", "bob")]
        findings = check_change_audit_completeness(approvals, {"chg-1"})
        assert [f.record_id for f in findings] == ["chg-2"]
        assert findings[0].control_id == CONTROL_SOX_CHANGE_AUDIT

    def test_fully_audited_changes_produce_no_findings(self) -> None:
        approvals = [FakeApproval("chg-1", "alice", "bob")]
        assert check_change_audit_completeness(approvals, {"chg-1", "chg-9"}) == []

    def test_audited_change_ids_are_read_from_audit_events(self) -> None:
        log = AuditLog()
        log.record("alice", "apply", "chg-1", "success", timestamp=NOW)
        log.record("alice", "apply", "chg-2", "success", timestamp=NOW)
        assert audited_change_ids(log.events()) == {"chg-1", "chg-2"}


class TestFeatureFlagGating:
    """Tests that only the top-level entry points are flag-gated."""

    def test_check_returns_no_findings_when_security_is_disabled(self, security_off: None) -> None:
        context = ComplianceContext(approvals=(FakeApproval("chg-1", "alice", "alice"),))
        assert run_compliance_check(ComplianceFramework.SOX, context) == []

    def test_check_runs_when_security_is_enabled(self, security_on: None) -> None:
        context = ComplianceContext(
            approvals=(FakeApproval("chg-1", "alice", "alice"),),
            audited_change_ids=frozenset({"chg-1"}),
        )
        findings = run_compliance_check(ComplianceFramework.SOX, context)
        assert [f.control_id for f in findings] == [CONTROL_SOX_SEGREGATION_OF_DUTIES]

    def test_individual_checks_are_not_gated(self, security_off: None) -> None:
        # Turning the framework off must not take the investigator's tools away.
        findings = check_segregation_of_duties([FakeApproval("chg-1", "alice", "alice")])
        assert len(findings) == 1

    def test_disabled_report_says_nothing_was_checked(self, security_off: None) -> None:
        report = generate_compliance_report(ComplianceContext(now=NOW))
        assert report.findings == ()
        assert report.controls_evaluated == ()
        assert report.controls_skipped
        assert any("disabled" in note for note in report.notes)


class TestReport:
    """Tests for report aggregation and rendering."""

    def build_context(self) -> ComplianceContext:
        """Build a context exercising all three frameworks.

        Returns:
            A populated :class:`ComplianceContext`.
        """
        ledger = ConsentLedger()
        ledger.withdraw("subj-1", "marketing", at=NOW - timedelta(days=1))
        return ComplianceContext(
            records=(
                record("r1", "subj-1", age_days=90),
                record("r2", "subj-1", legal_hold=True),
                record("r3", "subj-2", fields_={"notes": f"ssn {SSN}"}),
            ),
            retention_policies=(RetentionPolicy(category="profile", max_age_days=30),),
            consent=ledger,
            pending_erasure_requests=("subj-1",),
            designated_phi_fields=("ssn",),
            purpose_field_map={"marketing": ["email"]},
            field_accesses=(
                FieldAccess(
                    purpose="marketing",
                    fields=("email", "diagnosis"),
                    actor_id="agent-1",
                    subject_id="subj-1",
                    accessed_at=NOW,
                ),
            ),
            approvals=(FakeApproval("chg-1", "alice", "alice"),),
            audited_change_ids=frozenset(),
            now=NOW,
        )

    def test_report_aggregates_findings_across_frameworks(self, security_on: None) -> None:
        report = generate_compliance_report(self.build_context())
        by_framework = report.findings_by_framework()
        assert set(by_framework) == set(ComplianceFramework)
        assert by_framework[ComplianceFramework.GDPR]
        assert by_framework[ComplianceFramework.HIPAA]
        assert by_framework[ComplianceFramework.SOX]

    def test_report_surfaces_each_control(self, security_on: None) -> None:
        report = generate_compliance_report(self.build_context())
        control_ids = {finding.control_id for finding in report.findings}
        assert CONTROL_GDPR_RETENTION in control_ids
        assert CONTROL_GDPR_ERASURE_EXCEPTION in control_ids
        assert CONTROL_GDPR_CONSENT in control_ids
        assert CONTROL_HIPAA_PHI_PLACEMENT in control_ids
        assert CONTROL_HIPAA_MINIMUM_NECESSARY in control_ids
        assert CONTROL_SOX_SEGREGATION_OF_DUTIES in control_ids
        assert CONTROL_SOX_CHANGE_AUDIT in control_ids

    def test_findings_are_ordered_most_severe_first(self, security_on: None) -> None:
        report = generate_compliance_report(self.build_context())
        ranks = [compliance._SEVERITY_RANK[f.severity] for f in report.findings]
        assert ranks == sorted(ranks, reverse=True)

    def test_severity_counts_include_empty_levels(self, security_on: None) -> None:
        report = generate_compliance_report(self.build_context())
        counts = report.severity_counts()
        assert set(counts) == set(Severity)
        assert counts[Severity.CRITICAL] >= 1

    def test_missing_inputs_are_reported_as_skipped_controls(self, security_on: None) -> None:
        report = generate_compliance_report(ComplianceContext(now=NOW))
        assert report.findings == ()
        assert report.controls_evaluated == ()
        # An empty report must never read as a clean one.
        assert len(report.controls_skipped) >= 7

    def test_framework_selection_limits_coverage(self, security_on: None) -> None:
        report = generate_compliance_report(
            self.build_context(), frameworks=[ComplianceFramework.SOX]
        )
        assert report.frameworks == (ComplianceFramework.SOX,)
        assert {f.framework for f in report.findings} == {ComplianceFramework.SOX}

    def test_markdown_leads_with_the_disclaimer(self, security_on: None) -> None:
        rendered = render_report_markdown(generate_compliance_report(self.build_context()))
        assert DISCLAIMER in rendered
        assert rendered.index(DISCLAIMER) < rendered.index("## Coverage")

    def test_markdown_states_that_no_findings_is_not_compliance(self, security_on: None) -> None:
        report = generate_compliance_report(
            ComplianceContext(now=NOW), frameworks=[ComplianceFramework.SOX]
        )
        rendered = render_report_markdown(report)
        assert "not a statement that the framework's requirements are met" in rendered


class TestHonestyProperties:
    """Structural tests pinning what this module refuses to claim.

    These are the tests that fail if someone later adds the convenient
    boolean. That is the point: a per-framework verdict is a thing an
    auditor would reasonably read as an assurance, and no automated check
    is in a position to give one.
    """

    _VERDICT_WORDS = ("compliant", "compliance_status", "passed", "verdict", "certified", "score")

    def test_report_exposes_no_boolean_verdict_field(self) -> None:
        for report_field in fields(ComplianceReport):
            assert "bool" not in str(report_field.type), (
                f"ComplianceReport.{report_field.name} is a boolean; the report must not "
                f"carry a verdict."
            )
            lowered = report_field.name.lower()
            assert not any(
                word in lowered for word in self._VERDICT_WORDS
            ), f"ComplianceReport.{report_field.name} names a verdict."

    def test_finding_exposes_no_pass_fail_field(self) -> None:
        for finding_field in fields(ComplianceFinding):
            assert "bool" not in str(finding_field.type)
            lowered = finding_field.name.lower()
            assert not any(word in lowered for word in self._VERDICT_WORDS)

    def test_module_exports_no_compliance_verdict_function(self) -> None:
        for name in dir(compliance):
            if name.startswith("_"):
                continue
            lowered = name.lower()
            assert "is_compliant" not in lowered
            assert not lowered.startswith("is_gdpr")
            assert not lowered.startswith("is_hipaa")
            assert not lowered.startswith("is_sox")

    def test_report_states_it_is_not_a_certification(self) -> None:
        assert "not a compliance assessment" in DISCLAIMER
        assert "does not establish compliance" in DISCLAIMER

    def test_module_source_is_pure_ascii(self) -> None:
        # Literal non-ASCII in source has crashed bandit's text report on a
        # cp1252 console in this project before.
        source = compliance.__file__
        assert source is not None
        with open(source, "rb") as handle:
            handle.read().decode("ascii")
