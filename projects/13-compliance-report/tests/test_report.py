"""Tests for the compliance findings report.

Every test runs against the shipped synthetic dataset at a **frozen anchor**
time. The dataset stores ages as relative offsets, the loader resolves them
against the anchor, and the controls evaluate against the same anchor -- so
"420 days old, 365-day window" is 55 days overdue today, tomorrow, and in
three years. Nothing here sleeps and nothing reads the wall clock.

The last class is structural rather than behavioral: it pins the property
that this project's own report objects carry no boolean verdict, mirroring
the property ``tests/unit/test_compliance.py`` pins on the framework. That
test is the one that should fail if someone later adds the convenient flag.
"""

from __future__ import annotations

import json
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

import pytest
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
    ComplianceFramework,
    ErasureBlocker,
    Severity,
    erase_subject_records,
    export_subject_records,
)

import main
from dataset import (
    DEFAULT_DATASET_PATH,
    ComplianceDataset,
    DatasetError,
    InputGroup,
    load_dataset,
    omit_inputs,
)
from report import ReportRun, run_report

# Frozen evaluation time. Every age in the dataset is relative to this, so the
# expected findings never change with the calendar.
ANCHOR = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

# The SSN placeholder the synthetic dataset uses. Kept as a module constant on
# a short line so the `# pragma: allowlist secret` marker cannot be detached
# from it by a reformat.
FAKE_SSN = "123-45-6789"  # pragma: allowlist secret

# Subject ids chosen so a substring bug ("subj-1" in "subj-10") is caught.
SUBJECT_A = "subj-1"
SUBJECT_LOOKALIKE = "subj-10"


@pytest.fixture
def dataset() -> ComplianceDataset:
    """Load the shipped dataset at the frozen anchor.

    Returns:
        The validated dataset.
    """
    return load_dataset(DEFAULT_DATASET_PATH, now=ANCHOR)


@pytest.fixture
def run(dataset: ComplianceDataset) -> ReportRun:
    """Run all three frameworks over the shipped dataset.

    Args:
        dataset: The loaded dataset.

    Returns:
        The completed run.
    """
    return run_report(dataset)


def findings_for(run: ReportRun, control_id: str) -> list[str]:
    """Collect the record ids flagged by one control.

    Args:
        run: The completed run.
        control_id: The control to filter on.

    Returns:
        The record ids of matching findings, with None entries dropped.
    """
    return [
        finding.record_id
        for finding in run.report.findings
        if finding.control_id == control_id and finding.record_id is not None
    ]


class TestRetention:
    """GDPR storage limitation: records kept past their window."""

    def test_expired_record_is_flagged(self, run: ReportRun) -> None:
        assert "rec-001" in findings_for(run, CONTROL_GDPR_RETENTION)

    def test_fresh_record_is_not_flagged(self, run: ReportRun) -> None:
        # rec-003 is 10 days old under a 365-day window.
        assert "rec-003" not in findings_for(run, CONTROL_GDPR_RETENTION)

    def test_unjustified_over_retention_outranks_justified(self, run: ReportRun) -> None:
        by_record = {
            finding.record_id: finding
            for finding in run.report.findings
            if finding.control_id == CONTROL_GDPR_RETENTION
        }
        # rec-001 has no justification; rec-002 is held under a legal hold and
        # is still reported, but as something to re-confirm rather than fix.
        assert by_record["rec-001"].severity is Severity.HIGH
        assert by_record["rec-002"].severity is Severity.LOW

    def test_category_without_a_policy_is_flagged(self, run: ReportRun) -> None:
        gaps = [
            finding.evidence["category"]
            for finding in run.report.findings
            if finding.control_id == CONTROL_GDPR_RETENTION_POLICY_GAP
        ]
        assert "support_transcript" in gaps

    def test_ages_do_not_drift_with_the_wall_clock(self) -> None:
        later = load_dataset(DEFAULT_DATASET_PATH, now=datetime(2031, 1, 1, tzinfo=UTC))
        later_run = run_report(later)
        assert findings_for(later_run, CONTROL_GDPR_RETENTION) == ["rec-001", "rec-002"]


class TestErasure:
    """GDPR erasure: what could not be erased, and why."""

    def test_legal_hold_record_is_reported_not_silently_skipped(
        self, dataset: ComplianceDataset
    ) -> None:
        outcome = erase_subject_records(dataset.records, SUBJECT_A, now=ANCHOR)
        blocked = {exc.record_id: exc for exc in outcome.exceptions}
        assert "rec-002" in blocked
        assert blocked["rec-002"].blocker is ErasureBlocker.LEGAL_HOLD
        assert "rec-002" not in outcome.erased_record_ids

    def test_legal_hold_record_remains_in_the_store(self, dataset: ComplianceDataset) -> None:
        outcome = erase_subject_records(dataset.records, SUBJECT_A, now=ANCHOR)
        remaining = {record.record_id for record in outcome.remaining_records}
        assert "rec-002" in remaining
        # ...and the records that could be erased are gone.
        assert "rec-001" not in remaining
        assert "rec-003" not in remaining

    def test_retention_obligation_reports_when_it_lapses(self, dataset: ComplianceDataset) -> None:
        outcome = erase_subject_records(dataset.records, SUBJECT_A, now=ANCHOR)
        blocked = {exc.record_id: exc for exc in outcome.exceptions}
        assert blocked["rec-007"].blocker is ErasureBlocker.RETENTION_OBLIGATION
        assert blocked["rec-007"].blocked_until is not None
        assert blocked["rec-007"].blocked_until > ANCHOR

    def test_blocked_records_reach_the_report(self, run: ReportRun) -> None:
        flagged = findings_for(run, CONTROL_GDPR_ERASURE_EXCEPTION)
        assert set(flagged) == {"rec-002", "rec-007"}

    def test_another_subjects_records_are_untouched(self, dataset: ComplianceDataset) -> None:
        outcome = erase_subject_records(dataset.records, SUBJECT_A, now=ANCHOR)
        remaining = {record.record_id for record in outcome.remaining_records}
        assert {"rec-006", "rec-009"} <= remaining


class TestSubjectExport:
    """GDPR right of access: exactly one subject's records, never a neighbour's."""

    def test_export_excludes_the_lookalike_subject(self, dataset: ComplianceDataset) -> None:
        exported = {
            record.record_id for record in export_subject_records(dataset.records, SUBJECT_A)
        }
        assert exported == {"rec-001", "rec-002", "rec-003", "rec-007"}
        # rec-006 and rec-009 belong to subj-10. A substring match would leak them.
        assert "rec-006" not in exported
        assert "rec-009" not in exported

    def test_lookalike_subject_gets_its_own_records(self, dataset: ComplianceDataset) -> None:
        exported = {
            record.record_id
            for record in export_subject_records(dataset.records, SUBJECT_LOOKALIKE)
        }
        # rec-007 is subj-1's record but names subj-10 as a related subject.
        assert exported == {"rec-006", "rec-007", "rec-009"}

    def test_empty_subject_id_is_refused(self, dataset: ComplianceDataset) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            export_subject_records(dataset.records, "")


class TestPhiPlacement:
    """HIPAA: identifiers sitting in fields nobody designated for them."""

    def test_undesignated_field_is_flagged(self, run: ReportRun) -> None:
        flagged = {
            (finding.record_id, finding.evidence["field"])
            for finding in run.report.findings
            if finding.control_id == CONTROL_HIPAA_PHI_PLACEMENT
        }
        assert ("rec-004", "internal_note") in flagged

    def test_designated_field_holding_the_same_value_is_not_flagged(
        self, dataset: ComplianceDataset, run: ReportRun
    ) -> None:
        record = next(item for item in dataset.records if item.record_id == "rec-004")
        assert record.fields["patient_ssn"] == FAKE_SSN
        flagged_fields = {
            finding.evidence["field"]
            for finding in run.report.findings
            if finding.control_id == CONTROL_HIPAA_PHI_PLACEMENT
        }
        assert "patient_ssn" not in flagged_fields

    def test_evidence_does_not_reproduce_the_identifier(self, run: ReportRun) -> None:
        for finding in run.report.findings:
            if finding.control_id != CONTROL_HIPAA_PHI_PLACEMENT:
                continue
            assert FAKE_SSN not in finding.evidence["masked_excerpt"]
            assert "[REDACTED:" in finding.evidence["masked_excerpt"]


class TestSegregationOfDuties:
    """SOX: one principal on both sides of a change."""

    def test_self_approved_change_is_flagged(self, run: ReportRun) -> None:
        assert "chg-102" in findings_for(run, CONTROL_SOX_SEGREGATION_OF_DUTIES)

    def test_self_approval_is_caught_through_case_and_whitespace(self, run: ReportRun) -> None:
        finding = next(
            item
            for item in run.report.findings
            if item.control_id == CONTROL_SOX_SEGREGATION_OF_DUTIES
        )
        # The dataset spells the approver "Morgan.Lee " against a requester of
        # "morgan.lee": a raw string comparison would miss it entirely.
        assert finding.evidence["requested_by"] != finding.evidence["approved_by"]
        assert finding.severity is Severity.CRITICAL

    def test_independently_approved_change_is_not_flagged(self, run: ReportRun) -> None:
        assert "chg-101" not in findings_for(run, CONTROL_SOX_SEGREGATION_OF_DUTIES)

    def test_unapproved_change_is_not_a_segregation_finding(self, run: ReportRun) -> None:
        # chg-104 has no approver yet. That is a workflow state, not a failure.
        assert "chg-104" not in findings_for(run, CONTROL_SOX_SEGREGATION_OF_DUTIES)

    def test_change_without_an_audit_record_is_flagged(self, run: ReportRun) -> None:
        assert findings_for(run, CONTROL_SOX_CHANGE_AUDIT) == ["chg-103"]


class TestConsentAndMinimumNecessary:
    """GDPR consent evaluated at access time, and HIPAA minimum necessary."""

    def test_access_after_withdrawal_is_flagged(self, run: ReportRun) -> None:
        subjects = {
            finding.subject_id
            for finding in run.report.findings
            if finding.control_id == CONTROL_GDPR_CONSENT
        }
        assert "subj-3" in subjects

    def test_access_under_a_live_grant_is_not_flagged(self, run: ReportRun) -> None:
        subjects = {
            finding.subject_id
            for finding in run.report.findings
            if finding.control_id == CONTROL_GDPR_CONSENT
        }
        assert SUBJECT_A not in subjects

    def test_field_beyond_the_declared_purpose_is_flagged(self, run: ReportRun) -> None:
        excess = [
            finding.evidence.get("excess_fields")
            for finding in run.report.findings
            if finding.control_id == CONTROL_HIPAA_MINIMUM_NECESSARY
        ]
        assert ["diagnosis_code"] in excess

    def test_undeclared_purpose_is_itself_a_finding(self, run: ReportRun) -> None:
        purposes = {
            finding.evidence["purpose"]
            for finding in run.report.findings
            if finding.control_id == CONTROL_HIPAA_MINIMUM_NECESSARY
        }
        assert "incident_triage" in purposes


class TestRendering:
    """What a reader of the rendered report actually sees."""

    def test_disclaimer_is_present(self, run: ReportRun) -> None:
        assert DISCLAIMER in run.rendered_text

    def test_full_dataset_evaluates_every_control(self, run: ReportRun) -> None:
        assert run.report.controls_skipped == ()
        assert "Controls NOT evaluated (0)" in run.rendered_text

    def test_skipped_controls_are_listed_with_reasons(self, dataset: ComplianceDataset) -> None:
        reduced = omit_inputs(dataset, [InputGroup.APPROVALS, InputGroup.CONSENT])
        reduced_run = run_report(reduced)
        skipped = " ".join(reduced_run.report.controls_skipped)
        assert CONTROL_SOX_SEGREGATION_OF_DUTIES in skipped
        assert CONTROL_SOX_CHANGE_AUDIT in skipped
        assert CONTROL_GDPR_CONSENT in skipped
        assert "no approval records were supplied" in skipped
        for control_id in (CONTROL_SOX_SEGREGATION_OF_DUTIES, CONTROL_GDPR_CONSENT):
            assert control_id in reduced_run.rendered_text

    def test_a_report_with_gaps_does_not_read_as_clean(self, dataset: ComplianceDataset) -> None:
        reduced = omit_inputs(reduced_all(dataset), [])
        reduced_run = run_report(reduced)
        assert reduced_run.report.findings == ()
        # No findings, but the reader is told why -- loudly, above the findings.
        assert "Controls NOT evaluated (7)" in reduced_run.rendered_text
        assert "found nothing because it never ran" in reduced_run.rendered_text
        assert reduced_run.rendered_text.index("COVERAGE") < reduced_run.rendered_text.index(
            "FINDINGS BY SEVERITY"
        )

    def test_findings_are_grouped_by_framework(self, run: ReportRun) -> None:
        for framework in ComplianceFramework:
            assert f"{str(framework).upper()} FINDINGS" in run.rendered_text

    def test_severity_headings_appear_within_a_framework(self, run: ReportRun) -> None:
        assert "CRITICAL (1)" in run.rendered_text

    def test_rendering_is_pure_ascii(self, run: ReportRun) -> None:
        run.rendered_text.encode("ascii")

    def test_report_states_it_is_not_a_certification(self, run: ReportRun) -> None:
        assert "no pass/fail result, no score" in run.rendered_text


def reduced_all(dataset: ComplianceDataset) -> ComplianceDataset:
    """Strip every input group from a dataset.

    Args:
        dataset: The dataset to strip.

    Returns:
        A dataset with no inputs at all, so every control reports as skipped.
    """
    return omit_inputs(dataset, list(InputGroup))


class TestDatasetValidation:
    """Malformed input fails loudly, naming the offending element."""

    def test_missing_file_is_a_dataset_error(self, tmp_path: Path) -> None:
        with pytest.raises(DatasetError, match="Could not read dataset file"):
            load_dataset(tmp_path / "nope.json", now=ANCHOR)

    def test_invalid_json_is_a_dataset_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(DatasetError, match="is not valid JSON"):
            load_dataset(path, now=ANCHOR)

    def test_record_missing_a_field_names_the_record(self, tmp_path: Path) -> None:
        path = tmp_path / "partial.json"
        path.write_text(
            json.dumps({"records": [{"record_id": "r1", "category": "c", "created_days_ago": 1}]}),
            encoding="utf-8",
        )
        with pytest.raises(DatasetError, match=r"records\[0\]: 'subject_id' is required"):
            load_dataset(path, now=ANCHOR)

    def test_non_integer_age_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "age.json"
        path.write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "record_id": "r1",
                            "subject_id": "s1",
                            "category": "c",
                            "created_days_ago": "yesterday",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(DatasetError, match="'created_days_ago' is required"):
            load_dataset(path, now=ANCHOR)

    def test_duplicate_record_id_is_rejected(self, tmp_path: Path) -> None:
        record = {"record_id": "r1", "subject_id": "s1", "category": "c", "created_days_ago": 1}
        path = tmp_path / "dupe.json"
        path.write_text(json.dumps({"records": [record, dict(record)]}), encoding="utf-8")
        with pytest.raises(DatasetError, match="duplicate record_id"):
            load_dataset(path, now=ANCHOR)

    def test_unknown_framework_lists_the_known_ones(self, tmp_path: Path) -> None:
        path = tmp_path / "framework.json"
        path.write_text(
            json.dumps(
                {"retention_policies": [{"category": "c", "max_age_days": 1, "framework": "ccpa"}]}
            ),
            encoding="utf-8",
        )
        with pytest.raises(DatasetError, match="unknown framework 'ccpa'"):
            load_dataset(path, now=ANCHOR)

    def test_non_positive_retention_window_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "window.json"
        path.write_text(
            json.dumps({"retention_policies": [{"category": "c", "max_age_days": 0}]}),
            encoding="utf-8",
        )
        with pytest.raises(DatasetError, match="max_age_days must be positive"):
            load_dataset(path, now=ANCHOR)

    def test_absent_consent_is_not_an_empty_ledger(self, tmp_path: Path) -> None:
        path = tmp_path / "noconsent.json"
        path.write_text(json.dumps({"records": []}), encoding="utf-8")
        assert load_dataset(path, now=ANCHOR).consent is None


class TestCli:
    """The CLI's exit-code convention, which is a policy and not a verdict."""

    def test_default_run_exits_2_because_high_findings_exist(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main.main([]) == main.EXIT_THRESHOLD_REACHED
        out = capsys.readouterr().out
        assert DISCLAIMER in out
        assert "triage policy, not a compliance verdict" in out

    def test_fail_on_never_exits_zero_with_the_same_findings(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main.main(["--fail-on", "never"]) == main.EXIT_OK
        assert "CRITICAL" in capsys.readouterr().out

    def test_framework_scope_limits_the_report(self, capsys: pytest.CaptureFixture[str]) -> None:
        main.main(["--framework", "sox", "--fail-on", "never"])
        out = capsys.readouterr().out
        assert "Frameworks: sox" in out

    def test_dry_run_prints_coverage_without_findings(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main.main(["--dry-run"]) == main.EXIT_OK
        out = capsys.readouterr().out
        assert "COVERAGE" in out
        assert "FINDINGS BY SEVERITY" not in out

    def test_omit_moves_controls_into_the_skipped_list(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main.main(["--dry-run", "--omit", "approvals"]) == main.EXIT_OK
        out = capsys.readouterr().out
        assert "Controls NOT evaluated (2)" in out
        assert CONTROL_SOX_CHANGE_AUDIT in out

    def test_missing_dataset_is_a_configuration_error(self, tmp_path: Path) -> None:
        assert main.main(["--data", str(tmp_path / "nope.json")]) == main.EXIT_CONFIG_ERROR

    def test_subject_export_emits_only_that_subject(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main.main(["--subject-export", SUBJECT_A]) == main.EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["subject_id"] == SUBJECT_A
        assert {record["record_id"] for record in payload["records"]} == {
            "rec-001",
            "rec-002",
            "rec-003",
            "rec-007",
        }

    def test_empty_subject_export_is_a_configuration_error(self) -> None:
        assert main.main(["--subject-export", ""]) == main.EXIT_CONFIG_ERROR


class TestHonestyProperties:
    """Structural tests pinning what this project refuses to claim.

    ``tests/unit/test_compliance.py`` pins this property on the framework's
    own types. It has to be pinned here too: a project that consumes an
    honest API can still be the place where somebody bolts a green tick onto
    it, and this is the report that would carry the tick to a reader.
    """

    _VERDICT_WORDS = ("compliant", "compliance_status", "passed", "verdict", "certified", "score")

    @pytest.mark.parametrize("cls", [ReportRun, ComplianceDataset])
    def test_project_types_expose_no_boolean_verdict_field(self, cls: type) -> None:
        for spec in fields(cls):
            assert "bool" not in str(spec.type), (
                f"{cls.__name__}.{spec.name} is a boolean; this project's report objects "
                f"must not carry a verdict."
            )
            lowered = spec.name.lower()
            assert not any(
                word in lowered for word in self._VERDICT_WORDS
            ), f"{cls.__name__}.{spec.name} names a verdict."

    def test_project_modules_export_no_verdict_function(self) -> None:
        import dataset as dataset_module
        import report as report_module

        for module in (dataset_module, report_module, main):
            for name in dir(module):
                if name.startswith("_"):
                    continue
                lowered = name.lower()
                assert "is_compliant" not in lowered
                assert not lowered.startswith(("is_gdpr", "is_hipaa", "is_sox"))

    def test_rendered_report_contains_no_verdict_language(self, run: ReportRun) -> None:
        lowered = run.rendered_text.lower()
        for phrase in ("compliant:", "is compliant", "pass/fail:", "overall score", "certified"):
            assert phrase not in lowered

    def test_threshold_hits_are_findings_not_a_flag(self, run: ReportRun) -> None:
        # The exit code is derived from findings each time it is needed, so
        # there is no stored boolean anyone could read as a verdict.
        assert all(
            finding.severity in (Severity.HIGH, Severity.CRITICAL)
            for finding in run.findings_at_or_above_threshold
        )
        assert run.threshold is Severity.HIGH
