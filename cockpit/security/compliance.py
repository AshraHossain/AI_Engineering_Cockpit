"""Mechanical controls that produce evidence for GDPR, HIPAA, and SOX work.

**This module cannot tell anyone they are compliant, and deliberately
offers no way to say so.** Compliance is a legal and organizational
determination made by people with accountability for it -- counsel, a
privacy officer, an auditor -- against a specific processing context,
contracts, and documented policy. Software can only evidence *specific
technical controls*, which is what is here: mechanical, checkable
questions such as "does this record still exist past its retention
window", "did the same principal both request and approve this change",
"is there PHI sitting in a field that was never designated to hold it".

Everything in this module therefore reports **findings and coverage**, and
never a verdict:

* :class:`ComplianceFinding` describes one thing that was found and why it
  matters. There is no ``passed`` field -- the skeleton this replaced had
  one, and a per-rule boolean aggregates far too easily into a green tick
  that nobody is entitled to hand an auditor.
* :class:`ComplianceReport` groups findings by framework and severity and
  states which controls were actually evaluated. "No findings" means "the
  checks that ran found nothing", which is not the same claim as "compliant"
  and is reported as such.
* A clean run still lists controls that were *skipped* for want of input,
  because an unevaluated control is the finding an auditor cares about most.

The control identifiers (``GDPR-ART17-...`` and similar) name the article
or section that motivated each check. They are internal labels for
traceability, not a certified mapping to the regulation, and a single
mechanical check never covers a whole article.

PII/PHI detection is **not** reimplemented here: it delegates to
:mod:`cockpit.security.output_security`, so there is one place where those
patterns live and one place to improve them.

Only the top-level entry points -- :func:`run_compliance_check` and
:func:`generate_compliance_report` -- are gated on
``feature_flags.is_enabled("security")``, mirroring
:func:`cockpit.security.input_security.validate_input`. The individual
checks stay callable regardless, since someone investigating an incident
should not have their tools disappear because a flag is off.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final, Protocol

from cockpit.config.feature_flags import is_enabled
from cockpit.security.audit_logging import AuditEvent
from cockpit.security.output_security import mask_pii, scan_for_pii
from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)

# Reproduced verbatim at the top of every rendered report. If a report is
# ever put in front of an auditor, this is the sentence that has to travel
# with it.
DISCLAIMER: Final[str] = (
    "This report evidences the outcome of specific automated technical checks. "
    "It is not a compliance assessment, certification, or legal opinion, and "
    "the absence of findings does not establish compliance with any framework."
)

# Control identifiers. The article/section suffix records what motivated the
# check; it is not a claim to cover that article in full.
CONTROL_GDPR_CONSENT: Final[str] = "GDPR-ART6-CONSENT"
CONTROL_GDPR_ERASURE_EXCEPTION: Final[str] = "GDPR-ART17-ERASURE-EXCEPTION"
CONTROL_GDPR_RETENTION: Final[str] = "GDPR-ART5-STORAGE-LIMITATION"
CONTROL_GDPR_RETENTION_POLICY_GAP: Final[str] = "GDPR-ART5-NO-RETENTION-POLICY"
CONTROL_HIPAA_PHI_PLACEMENT: Final[str] = "HIPAA-164.502-PHI-PLACEMENT"
CONTROL_HIPAA_MINIMUM_NECESSARY: Final[str] = "HIPAA-164.502B-MINIMUM-NECESSARY"
CONTROL_SOX_SEGREGATION_OF_DUTIES: Final[str] = "SOX-302-SEGREGATION-OF-DUTIES"
CONTROL_SOX_CHANGE_AUDIT: Final[str] = "SOX-404-CHANGE-AUDIT-COMPLETENESS"

# Placeholder written over a value that has been erased in place. Erasure
# blanks the value but keeps the key, so downstream schemas and joins do not
# break in ways that look like data loss of a different kind.
ERASURE_TOKEN: Final[str] = "[ERASED]"  # noqa: S105 -- redaction marker, not a credential

# How much of a field's content to quote in a PHI finding. Findings get read,
# copied into tickets, and mailed around; a finding that reproduces the PHI it
# is complaining about is a second breach. Excerpts are masked as well as cut.
_EVIDENCE_EXCERPT_CHARS: Final[int] = 80


class ComplianceFramework(StrEnum):
    """Regulatory frameworks these checks are organized under.

    Attributes:
        GDPR: EU General Data Protection Regulation.
        HIPAA: US Health Insurance Portability and Accountability Act.
        SOX: US Sarbanes-Oxley Act.
    """

    GDPR = "gdpr"
    HIPAA = "hipaa"
    SOX = "sox"


class Severity(StrEnum):
    """How much attention a finding warrants.

    Deliberately about the finding, not about a framework verdict: a
    CRITICAL finding is one a human should look at today, not a declaration
    that anything is non-compliant.

    Attributes:
        INFO: Recorded for completeness; no action implied.
        LOW: Worth knowing, typically already justified elsewhere.
        MEDIUM: Should be triaged; often a coverage or policy gap.
        HIGH: Likely a real control weakness.
        CRITICAL: Control appears defeated; investigate immediately.
    """

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_SEVERITY_RANK: Final[dict[Severity, int]] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


@dataclass(frozen=True)
class ComplianceFinding:
    """One thing a check found, and why it matters.

    There is intentionally no ``passed`` / ``compliant`` field. A finding
    exists because something was found; the absence of a finding is not an
    assertion in the other direction.

    Attributes:
        framework: The framework whose requirement motivated the check.
        control_id: Identifier of the specific control, e.g.
            :data:`CONTROL_GDPR_RETENTION`.
        severity: How much attention this finding warrants.
        description: Human-readable statement of what was found and why it
            matters, written to be understandable without reading the code.
        subject_id: The data subject affected, if the finding is about one.
        record_id: The record affected, if the finding is about one.
        evidence: Structured supporting detail. Any quoted content is
            already masked; never put raw PII or PHI here.
    """

    framework: ComplianceFramework
    control_id: str
    severity: Severity
    description: str
    subject_id: str | None = None
    record_id: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)


def _to_utc(moment: datetime) -> datetime:
    """Normalize a datetime to an aware UTC datetime.

    A naive datetime is assumed to be UTC. Retention arithmetic that mixes
    aware and naive values raises at the worst possible moment -- mid-check,
    on production data -- so everything is normalized on the way in.

    Args:
        moment: The datetime to normalize.

    Returns:
        The equivalent timezone-aware datetime in UTC.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _resolve_now(now: datetime | None) -> datetime:
    """Return the evaluation time, defaulting to the current UTC time.

    Args:
        now: Caller-supplied evaluation time, or None.

    Returns:
        An aware UTC datetime. Injectable so tests never sleep.
    """
    return _to_utc(now) if now is not None else datetime.now(UTC)


def _normalize_principal(principal: str) -> str:
    """Fold a principal identifier for identity comparison.

    ``"Alice"``, ``"alice"``, and ``"alice "`` are the same human. Comparing
    raw strings would let a self-approval hide behind a capital letter --
    which defeats the segregation-of-duties check entirely, and does so
    silently.

    Args:
        principal: The principal identifier as supplied.

    Returns:
        The case-folded, whitespace-stripped identifier.
    """
    return principal.strip().casefold()


@dataclass(frozen=True)
class DataRecord:
    """One stored record about a data subject.

    A deliberately thin, storage-agnostic shape: these checks operate on
    whatever the caller can project their real store into, rather than
    assuming a schema.

    Attributes:
        record_id: Unique identifier for the record.
        subject_id: The data subject the record is primarily about.
        category: Data category, used to match a :class:`RetentionPolicy`.
        created_at: When the record was created; the basis for retention age.
        fields: The record's field values.
        legal_hold: True if the record is frozen for litigation or
            investigation and must not be deleted.
        retention_obligation_until: A date before which the record must be
            kept to satisfy a statutory retention duty, if any.
        related_subject_ids: Other subjects the record references, e.g. the
            second party to a transaction.
    """

    record_id: str
    subject_id: str
    category: str
    created_at: datetime
    fields: Mapping[str, Any] = field(default_factory=dict)
    legal_hold: bool = False
    retention_obligation_until: datetime | None = None
    related_subject_ids: tuple[str, ...] = ()


def references_subject(record: DataRecord, subject_id: str) -> bool:
    """Report whether a record refers to a given data subject.

    Matching is by **exact value**, never by substring. Substring matching
    is the obvious implementation and it is wrong in the dangerous
    direction: ``"subj-1"`` is a substring of ``"subj-10"``, so a right-of-
    access export would hand one subject another subject's data, and an
    erasure would delete a stranger's records.

    Args:
        record: The record to test.
        subject_id: The data subject to look for.

    Returns:
        True if the record is about the subject, lists them as a related
        subject, or carries their identifier in a field value.
    """
    if record.subject_id == subject_id:
        return True
    if subject_id in record.related_subject_ids:
        return True
    for value in record.fields.values():
        if isinstance(value, str) and value == subject_id:
            return True
        if isinstance(value, list | tuple) and subject_id in value:
            return True
    return False


def record_to_dict(record: DataRecord) -> dict[str, Any]:
    """Convert a record to a JSON-shaped dict.

    Args:
        record: The record to convert.

    Returns:
        A plain dict with datetimes rendered as ISO-8601 UTC strings.
    """
    return {
        "record_id": record.record_id,
        "subject_id": record.subject_id,
        "category": record.category,
        "created_at": _to_utc(record.created_at).isoformat(),
        "fields": dict(record.fields),
        "legal_hold": record.legal_hold,
        "retention_obligation_until": (
            _to_utc(record.retention_obligation_until).isoformat()
            if record.retention_obligation_until is not None
            else None
        ),
        "related_subject_ids": list(record.related_subject_ids),
    }


def export_subject_records(
    records: Iterable[DataRecord],
    subject_id: str,
) -> list[DataRecord]:
    """Collect every record referencing a subject (GDPR right of access).

    Args:
        records: The record set to search.
        subject_id: The data subject making the request.

    Returns:
        The matching records, in input order. Records belonging solely to
        other subjects are never included -- see :func:`references_subject`
        for why matching is exact.

    Raises:
        ValueError: If ``subject_id`` is empty. An empty id would otherwise
            match nothing and look like "this person has no data", which is
            a materially misleading answer to a legal request.
    """
    if not subject_id:
        raise ValueError("subject_id must be a non-empty string.")
    return [record for record in records if references_subject(record, subject_id)]


def export_subject_data_json(
    records: Iterable[DataRecord],
    subject_id: str,
    *,
    indent: int = 2,
) -> str:
    """Export a subject's data as structured JSON (GDPR portability).

    Portability asks for a commonly used, machine-readable format, so the
    export is JSON with stable key ordering rather than a rendered document.

    Args:
        records: The record set to search.
        subject_id: The data subject making the request.
        indent: JSON indentation for readability.

    Returns:
        A JSON document with the subject id, export timestamp, record count,
        and the full records.

    Raises:
        ValueError: If ``subject_id`` is empty.
    """
    matched = export_subject_records(records, subject_id)
    payload = {
        "subject_id": subject_id,
        "exported_at": datetime.now(UTC).isoformat(),
        "record_count": len(matched),
        "records": [record_to_dict(record) for record in matched],
    }
    return json.dumps(payload, indent=indent, sort_keys=True, ensure_ascii=True, default=str)


class ErasureBlocker(StrEnum):
    """Why a record could not be erased.

    Attributes:
        LEGAL_HOLD: The record is frozen for litigation or investigation.
        RETENTION_OBLIGATION: A statutory duty requires keeping it longer.
    """

    LEGAL_HOLD = "legal_hold"
    RETENTION_OBLIGATION = "retention_obligation"


class ErasureMode(StrEnum):
    """How erasure is carried out.

    Attributes:
        DELETE: The record is dropped from the returned set entirely.
        REDACT: The record survives with its values overwritten, for stores
            where a row cannot disappear without breaking referential
            integrity or an append-only ledger.
    """

    DELETE = "delete"
    REDACT = "redact"


@dataclass(frozen=True)
class ErasureException:
    """A record that was requested for erasure but could not be erased.

    Attributes:
        record_id: The record that survived the request.
        subject_id: The subject who requested erasure.
        blocker: Why the erasure did not happen.
        description: Human-readable explanation for the subject and for the
            audit file.
        blocked_until: When the obstacle lapses, if it is time-bounded.
    """

    record_id: str
    subject_id: str
    blocker: ErasureBlocker
    description: str
    blocked_until: datetime | None = None


@dataclass(frozen=True)
class ErasureOutcome:
    """The full result of an erasure request.

    Both halves matter. A caller that only reads ``erased_record_ids`` and
    ignores ``exceptions`` will tell a data subject their data is gone while
    some of it demonstrably is not -- so the exceptions are returned as
    first-class data and additionally raised as findings.

    Attributes:
        subject_id: The subject the request was for.
        mode: How erasure was carried out.
        erased_record_ids: Records that were deleted or redacted.
        exceptions: Records that could not be erased, with the reason.
        remaining_records: The full record set after the operation,
            including other subjects' untouched records.
        findings: One :class:`ComplianceFinding` per exception, so the
            un-erased records surface in a report and not just in a return
            value someone forgot to read.
    """

    subject_id: str
    mode: ErasureMode
    erased_record_ids: tuple[str, ...]
    exceptions: tuple[ErasureException, ...]
    remaining_records: tuple[DataRecord, ...]
    findings: tuple[ComplianceFinding, ...]


def _erasure_blocker(
    record: DataRecord,
    now: datetime,
) -> tuple[ErasureBlocker, str, datetime | None] | None:
    """Determine whether anything prevents erasing a record.

    Args:
        record: The record under consideration.
        now: Evaluation time.

    Returns:
        A ``(blocker, description, blocked_until)`` triple, or None if the
        record can be erased.
    """
    if record.legal_hold:
        return (
            ErasureBlocker.LEGAL_HOLD,
            (
                f"Record {record.record_id} is under legal hold and was retained despite an "
                f"erasure request. The subject must be told the data still exists and why; "
                f"the hold owner should confirm the hold is still warranted."
            ),
            None,
        )
    obligation = record.retention_obligation_until
    if obligation is not None and _to_utc(obligation) > now:
        until = _to_utc(obligation)
        return (
            ErasureBlocker.RETENTION_OBLIGATION,
            (
                f"Record {record.record_id} is subject to a retention obligation until "
                f"{until.isoformat()} and was retained despite an erasure request. It must "
                f"be erased once that date passes; nothing here schedules that."
            ),
            until,
        )
    return None


def _redact_record(record: DataRecord) -> DataRecord:
    """Overwrite a record's identifying content in place.

    Keys are kept and values replaced, so a consumer of the store sees a
    blanked row rather than a schema that changed shape underneath it.

    Args:
        record: The record to redact.

    Returns:
        A new record with the subject id and every field value replaced by
        :data:`ERASURE_TOKEN`.
    """
    return replace(
        record,
        subject_id=ERASURE_TOKEN,
        fields=dict.fromkeys(record.fields, ERASURE_TOKEN),
        related_subject_ids=(),
    )


def erase_subject_records(
    records: Iterable[DataRecord],
    subject_id: str,
    *,
    mode: ErasureMode = ErasureMode.DELETE,
    now: datetime | None = None,
) -> ErasureOutcome:
    """Erase a subject's records, reporting everything that could not be.

    Right to erasure is not absolute: legal holds and statutory retention
    duties override it. The interesting output of this function is
    therefore the *exceptions*, not the deletions. Nothing is ever skipped
    quietly -- every record that stays is named, explained, and turned into
    a finding.

    This function is pure: the input iterable is not mutated, and the caller
    applies ``remaining_records`` to their store themselves.

    Args:
        records: The record set to operate on.
        subject_id: The data subject requesting erasure.
        mode: Whether to delete records outright or redact them in place.
        now: Evaluation time; defaults to now (UTC).

    Returns:
        An :class:`ErasureOutcome` describing what was erased, what was not,
        why, and the resulting record set.

    Raises:
        ValueError: If ``subject_id`` is empty.
    """
    if not subject_id:
        raise ValueError("subject_id must be a non-empty string.")

    moment = _resolve_now(now)
    erased: list[str] = []
    exceptions: list[ErasureException] = []
    findings: list[ComplianceFinding] = []
    remaining: list[DataRecord] = []

    for record in records:
        if not references_subject(record, subject_id):
            remaining.append(record)
            continue

        blocked = _erasure_blocker(record, moment)
        if blocked is not None:
            blocker, description, blocked_until = blocked
            exceptions.append(
                ErasureException(
                    record_id=record.record_id,
                    subject_id=subject_id,
                    blocker=blocker,
                    description=description,
                    blocked_until=blocked_until,
                )
            )
            findings.append(
                ComplianceFinding(
                    framework=ComplianceFramework.GDPR,
                    control_id=CONTROL_GDPR_ERASURE_EXCEPTION,
                    severity=Severity.HIGH,
                    description=description,
                    subject_id=subject_id,
                    record_id=record.record_id,
                    evidence={
                        "blocker": str(blocker),
                        "blocked_until": (
                            blocked_until.isoformat() if blocked_until is not None else None
                        ),
                        "category": record.category,
                    },
                )
            )
            remaining.append(record)
            continue

        erased.append(record.record_id)
        if mode is ErasureMode.REDACT:
            remaining.append(_redact_record(record))

    if exceptions:
        _logger.warning(
            "Erasure for subject %s left %d record(s) in place: %s",
            subject_id,
            len(exceptions),
            ", ".join(f"{e.record_id} ({e.blocker})" for e in exceptions),
        )

    return ErasureOutcome(
        subject_id=subject_id,
        mode=mode,
        erased_record_ids=tuple(erased),
        exceptions=tuple(exceptions),
        remaining_records=tuple(remaining),
        findings=tuple(findings),
    )


@dataclass(frozen=True)
class ConsentDecision:
    """One grant or withdrawal of consent, at a point in time.

    Attributes:
        subject_id: The data subject.
        purpose: The processing purpose consent was decided for.
        granted: True for a grant, False for a withdrawal.
        decided_at: When the decision was made (UTC).
        note: Optional free-text context, e.g. the capture mechanism.
    """

    subject_id: str
    purpose: str
    granted: bool
    decided_at: datetime
    note: str = ""


@dataclass(frozen=True)
class ConsentStatus:
    """The consent position for one subject and purpose at a point in time.

    ``is_valid`` is a statement about the ledger -- "the latest decision on
    file is a grant" -- not a judgement that the processing is lawful.
    Consent is only one of several lawful bases, and a recorded grant can
    still be invalid for reasons no ledger can see (it was bundled, it was
    not freely given, the purpose drifted).

    Attributes:
        subject_id: The data subject.
        purpose: The processing purpose.
        is_valid: True if the most recent decision at or before the
            evaluation time was a grant.
        decided_at: When that decision was made, or None if there is none.
        reason: Human-readable explanation of the position.
    """

    subject_id: str
    purpose: str
    is_valid: bool
    decided_at: datetime | None
    reason: str


@dataclass
class ConsentLedger:
    """An append-only record of consent grants and withdrawals.

    Append-only on purpose: consent state is a history, not a flag. "Was
    this processing consented to *at the time it happened*" is the question
    that gets asked after the fact, and overwriting a boolean destroys the
    only evidence that could answer it.

    Thread-safe, because grants and withdrawals arrive on whatever thread
    served the request and a decision must never be lost to an interleaved
    append.

    Attributes:
        decisions: Every decision recorded, in insertion order.
    """

    decisions: list[ConsentDecision] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def _record(
        self,
        subject_id: str,
        purpose: str,
        granted: bool,
        at: datetime | None,
        note: str,
    ) -> ConsentDecision:
        """Append one decision.

        Args:
            subject_id: The data subject.
            purpose: The processing purpose.
            granted: True for a grant, False for a withdrawal.
            at: When the decision was made; defaults to now (UTC).
            note: Optional free-text context.

        Returns:
            The appended :class:`ConsentDecision`.

        Raises:
            ValueError: If ``subject_id`` or ``purpose`` is empty.
        """
        if not subject_id:
            raise ValueError("subject_id must be a non-empty string.")
        if not purpose:
            raise ValueError("purpose must be a non-empty string.")
        decision = ConsentDecision(
            subject_id=subject_id,
            purpose=purpose,
            granted=granted,
            decided_at=_resolve_now(at),
            note=note,
        )
        with self._lock:
            self.decisions.append(decision)
        return decision

    def grant(
        self,
        subject_id: str,
        purpose: str,
        *,
        at: datetime | None = None,
        note: str = "",
    ) -> ConsentDecision:
        """Record that a subject granted consent for a purpose.

        Args:
            subject_id: The data subject.
            purpose: The processing purpose consented to.
            at: When consent was granted; defaults to now (UTC).
            note: Optional free-text context.

        Returns:
            The appended :class:`ConsentDecision`.

        Raises:
            ValueError: If ``subject_id`` or ``purpose`` is empty.
        """
        return self._record(subject_id, purpose, True, at, note)

    def withdraw(
        self,
        subject_id: str,
        purpose: str,
        *,
        at: datetime | None = None,
        note: str = "",
    ) -> ConsentDecision:
        """Record that a subject withdrew consent for a purpose.

        Args:
            subject_id: The data subject.
            purpose: The processing purpose consent is withdrawn from.
            at: When consent was withdrawn; defaults to now (UTC).
            note: Optional free-text context.

        Returns:
            The appended :class:`ConsentDecision`.

        Raises:
            ValueError: If ``subject_id`` or ``purpose`` is empty.
        """
        return self._record(subject_id, purpose, False, at, note)

    def history(
        self,
        subject_id: str | None = None,
        purpose: str | None = None,
    ) -> list[ConsentDecision]:
        """Return recorded decisions, oldest first.

        Args:
            subject_id: Restrict to this subject, if provided.
            purpose: Restrict to this purpose, if provided.

        Returns:
            Matching decisions in insertion order.
        """
        with self._lock:
            snapshot = list(self.decisions)
        return [
            decision
            for decision in snapshot
            if (subject_id is None or decision.subject_id == subject_id)
            and (purpose is None or decision.purpose == purpose)
        ]

    def status(
        self,
        subject_id: str,
        purpose: str,
        *,
        as_of: datetime | None = None,
    ) -> ConsentStatus:
        """Report the consent position as it stood at a point in time.

        Decisions recorded after ``as_of`` are ignored, so a past processing
        event can be evaluated against the consent that existed then rather
        than against today's ledger.

        Args:
            subject_id: The data subject.
            purpose: The processing purpose.
            as_of: Evaluation time; defaults to now (UTC).

        Returns:
            A :class:`ConsentStatus` for the subject and purpose.
        """
        moment = _resolve_now(as_of)
        relevant = [
            decision
            for decision in self.history(subject_id, purpose)
            if _to_utc(decision.decided_at) <= moment
        ]
        if not relevant:
            return ConsentStatus(
                subject_id=subject_id,
                purpose=purpose,
                is_valid=False,
                decided_at=None,
                reason=(
                    f"No consent decision is on file for subject {subject_id} and purpose "
                    f"'{purpose}' as of {moment.isoformat()}. Absent consent is not implied "
                    f"consent."
                ),
            )
        # Insertion order breaks ties, so two decisions bearing the same
        # timestamp resolve to the one that was recorded second.
        latest = max(enumerate(relevant), key=lambda pair: (_to_utc(pair[1].decided_at), pair[0]))[
            1
        ]
        if latest.granted:
            reason = (
                f"Consent for purpose '{purpose}' was granted at "
                f"{_to_utc(latest.decided_at).isoformat()} and has not been withdrawn."
            )
        else:
            reason = (
                f"Consent for purpose '{purpose}' was withdrawn at "
                f"{_to_utc(latest.decided_at).isoformat()}; processing on this basis must stop."
            )
        return ConsentStatus(
            subject_id=subject_id,
            purpose=purpose,
            is_valid=latest.granted,
            decided_at=_to_utc(latest.decided_at),
            reason=reason,
        )

    def has_valid_consent(
        self,
        subject_id: str,
        purpose: str,
        *,
        as_of: datetime | None = None,
    ) -> bool:
        """Convenience wrapper returning only the validity of the position.

        Prefer :meth:`status` when the answer will be shown to a person --
        a bare False does not say whether consent was withdrawn or never
        given, and those call for different responses.

        Args:
            subject_id: The data subject.
            purpose: The processing purpose.
            as_of: Evaluation time; defaults to now (UTC).

        Returns:
            True if the latest decision at or before ``as_of`` was a grant.
        """
        return self.status(subject_id, purpose, as_of=as_of).is_valid


@dataclass(frozen=True)
class RetentionPolicy:
    """The maximum time one category of data may be kept.

    Attributes:
        category: The data category this policy governs, matched against
            :attr:`DataRecord.category`.
        max_age_days: Maximum age in days, measured from
            :attr:`DataRecord.created_at`.
        basis: Why this window exists (contractual, statutory, business
            need). Recorded because "we have always kept it" is the answer
            that turns into a finding.
        framework: Which framework the window is attributed to in findings.

    Raises:
        ValueError: If ``max_age_days`` is not positive.
    """

    category: str
    max_age_days: int
    basis: str = ""
    framework: ComplianceFramework = ComplianceFramework.GDPR

    def __post_init__(self) -> None:
        """Validate the retention window.

        Raises:
            ValueError: If ``max_age_days`` is not positive. A zero or
                negative window would flag every record ever written, which
                buries the real findings rather than surfacing them.
        """
        if self.max_age_days <= 0:
            raise ValueError("max_age_days must be positive.")


def check_retention(
    records: Iterable[DataRecord],
    policies: Iterable[RetentionPolicy],
    *,
    now: datetime | None = None,
    flag_uncovered_categories: bool = True,
) -> list[ComplianceFinding]:
    """Find records kept past their retention window.

    Over-retention is among the most common findings in real audits, for a
    mundane reason: deleting data requires someone to build the deletion
    path, and nothing breaks when they do not.

    Records held past their window under a legal hold or a statutory
    retention duty are still reported, at :attr:`Severity.LOW`, because the
    justification needs to be visible and revisited -- an expired hold that
    nobody lifted looks exactly like a lawful one until someone checks.

    Args:
        records: The record set to check.
        policies: Retention policies, one per data category. A later policy
            for the same category replaces an earlier one.
        now: Evaluation time; defaults to now (UTC).
        flag_uncovered_categories: Whether to raise a finding for categories
            with no policy at all.

    Returns:
        Findings for over-retained records and, optionally, for categories
        that have no retention policy defined.
    """
    moment = _resolve_now(now)
    by_category = {policy.category: policy for policy in policies}
    findings: list[ComplianceFinding] = []
    uncovered: set[str] = set()

    for record in records:
        policy = by_category.get(record.category)
        if policy is None:
            if flag_uncovered_categories and record.category not in uncovered:
                uncovered.add(record.category)
                findings.append(
                    ComplianceFinding(
                        framework=ComplianceFramework.GDPR,
                        control_id=CONTROL_GDPR_RETENTION_POLICY_GAP,
                        severity=Severity.MEDIUM,
                        description=(
                            f"No retention policy is defined for data category "
                            f"'{record.category}', so nothing here can say when those records "
                            f"should be deleted. Data with no defined lifetime is kept forever "
                            f"by default."
                        ),
                        evidence={"category": record.category},
                    )
                )
            continue

        age = moment - _to_utc(record.created_at)
        limit = timedelta(days=policy.max_age_days)
        if age <= limit:
            continue

        overdue_days = (age - limit).days
        justification = _retention_justification(record, moment)
        severity = Severity.LOW if justification else Severity.HIGH
        detail = (
            f" It is retained under {justification}, which should be re-confirmed."
            if justification
            else " There is no recorded justification for keeping it."
        )
        findings.append(
            ComplianceFinding(
                framework=policy.framework,
                control_id=CONTROL_GDPR_RETENTION,
                severity=severity,
                description=(
                    f"Record {record.record_id} in category '{record.category}' is "
                    f"{age.days} days old, {overdue_days} day(s) past its "
                    f"{policy.max_age_days}-day retention window.{detail}"
                ),
                subject_id=record.subject_id,
                record_id=record.record_id,
                evidence={
                    "category": record.category,
                    "age_days": age.days,
                    "max_age_days": policy.max_age_days,
                    "overdue_days": overdue_days,
                    "policy_basis": policy.basis,
                    "justification": justification,
                },
            )
        )

    return findings


def _retention_justification(record: DataRecord, now: datetime) -> str | None:
    """Describe why a record may lawfully outlive its retention window.

    Args:
        record: The over-retained record.
        now: Evaluation time.

    Returns:
        A short description of the justification, or None if there is none.
    """
    if record.legal_hold:
        return "a legal hold"
    obligation = record.retention_obligation_until
    if obligation is not None and _to_utc(obligation) > now:
        return f"a retention obligation until {_to_utc(obligation).isoformat()}"
    return None


def detect_undesignated_phi(
    records: Iterable[DataRecord],
    designated_fields: Iterable[str],
) -> list[ComplianceFinding]:
    """Flag records carrying PHI-shaped content in undesignated fields.

    Detection delegates to
    :func:`cockpit.security.output_security.scan_for_pii` -- the patterns
    live in one place and improve in one place.

    What this actually catches is *misplacement*: identifiers landing in a
    free-text note, a description, or a debug field that no one designated
    to hold them and that therefore inherits none of the access controls,
    encryption, or retention rules the designated fields have. It is a
    heuristic on format, not a determination that any value is PHI.

    Quoted evidence is masked and truncated, so a finding never reproduces
    the content it is complaining about.

    Args:
        records: The record set to scan.
        designated_fields: Field names that are permitted to carry
            identifiers. Matching is exact and case-sensitive.

    Returns:
        One finding per undesignated field found to carry PHI-shaped content.
    """
    designated = set(designated_fields)
    findings: list[ComplianceFinding] = []

    for record in records:
        for name, value in record.fields.items():
            if name in designated or not isinstance(value, str):
                continue
            scan = scan_for_pii(value)
            if not scan.has_pii:
                continue
            categories = sorted({finding.category for finding in scan.findings})
            findings.append(
                ComplianceFinding(
                    framework=ComplianceFramework.HIPAA,
                    control_id=CONTROL_HIPAA_PHI_PLACEMENT,
                    severity=Severity.HIGH,
                    description=(
                        f"Field '{name}' on record {record.record_id} carries identifier-shaped "
                        f"content ({', '.join(categories)}) but is not a designated PHI field. "
                        f"Undesignated fields do not inherit the access controls, encryption, or "
                        f"retention rules applied to designated ones."
                    ),
                    subject_id=record.subject_id,
                    record_id=record.record_id,
                    evidence={
                        "field": name,
                        "pii_categories": categories,
                        "masked_excerpt": mask_pii(value)[:_EVIDENCE_EXCERPT_CHARS],
                    },
                )
            )

    return findings


@dataclass(frozen=True)
class FieldAccess:
    """One access to a set of fields, made for a declared purpose.

    Attributes:
        purpose: The declared purpose of the access, e.g. "billing".
        fields: The field names actually read.
        actor_id: The principal that performed the access.
        subject_id: The data subject whose fields were read, if known.
        accessed_at: When the access happened.
    """

    purpose: str
    fields: tuple[str, ...]
    actor_id: str
    subject_id: str | None = None
    accessed_at: datetime | None = None


def check_minimum_necessary(
    purpose: str,
    accessed_fields: Iterable[str],
    purpose_field_map: Mapping[str, Iterable[str]],
    *,
    actor_id: str | None = None,
    subject_id: str | None = None,
) -> list[ComplianceFinding]:
    """Flag fields read beyond what a declared purpose needs.

    The "minimum necessary" standard is about what an access *needed*, not
    about whether the accessor was authorized. A billing clerk with entirely
    legitimate access to a record still should not be reading the diagnosis
    field to send an invoice, and that over-reach is invisible to any
    role-based check.

    An undeclared purpose is itself a finding: with no declared field set,
    no amount of access can be justified as minimum necessary.

    Args:
        purpose: The declared purpose of the access.
        accessed_fields: The field names actually read.
        purpose_field_map: Declared field sets, keyed by purpose.
        actor_id: The principal that performed the access, for the finding.
        subject_id: The affected data subject, for the finding.

    Returns:
        Findings for an undeclared purpose or for fields beyond the declared
        set. Empty when the access stayed within its purpose.
    """
    accessed = set(accessed_fields)
    if purpose not in purpose_field_map:
        return [
            ComplianceFinding(
                framework=ComplianceFramework.HIPAA,
                control_id=CONTROL_HIPAA_MINIMUM_NECESSARY,
                severity=Severity.HIGH,
                description=(
                    f"Purpose '{purpose}' has no declared field set, so the {len(accessed)} "
                    f"field(s) read for it cannot be justified as the minimum necessary. "
                    f"Declare the fields the purpose requires, or stop using the purpose."
                ),
                subject_id=subject_id,
                evidence={
                    "purpose": purpose,
                    "actor_id": actor_id,
                    "accessed_fields": sorted(accessed),
                },
            )
        ]

    allowed = set(purpose_field_map[purpose])
    excess = sorted(accessed - allowed)
    if not excess:
        return []

    return [
        ComplianceFinding(
            framework=ComplianceFramework.HIPAA,
            control_id=CONTROL_HIPAA_MINIMUM_NECESSARY,
            severity=Severity.MEDIUM,
            description=(
                f"Access for purpose '{purpose}' read {len(excess)} field(s) beyond the "
                f"declared set for that purpose: {', '.join(excess)}. Either the purpose's "
                f"declared fields are out of date or the access exceeded what it needed."
            ),
            subject_id=subject_id,
            evidence={
                "purpose": purpose,
                "actor_id": actor_id,
                "excess_fields": excess,
                "declared_fields": sorted(allowed),
            },
        )
    ]


class ApprovalRecordLike(Protocol):
    """The minimum shape a change approval must have for the SOX checks.

    A structural :class:`~typing.Protocol` rather than an import from
    ``cockpit.governance.approval_workflow``, for two reasons. The immediate
    one is that the approval workflow module is being authored concurrently,
    and importing a module whose surface is still moving would couple these
    checks to a shape that does not exist yet. The lasting one is that this
    is the better dependency direction anyway: a compliance check should
    depend on the few attributes it actually reads, so any approval-shaped
    object -- a workflow record, a row from a change management system, a
    test fixture -- can be checked without adapting it first.

    Attributes:
        change_id: Identifier of the change being approved.
        requested_by: Principal that created or requested the change.
        approved_by: Principal that approved it, or None if not yet approved.
    """

    change_id: str
    requested_by: str
    approved_by: str | None


def check_segregation_of_duties(
    approvals: Iterable[ApprovalRecordLike],
) -> list[ComplianceFinding]:
    """Flag changes where the same principal both requested and approved.

    Self-approval is the failure mode segregation of duties exists to
    prevent: the approval step is supposed to put a second pair of eyes
    between a change and production, and one person doing both turns it into
    a formality that still produces a green audit trail.

    Unapproved changes are not flagged here -- an absent approval is a
    workflow state, not a segregation failure.

    Args:
        approvals: Approval records to check.

    Returns:
        One finding per change approved by its own requester.
    """
    findings: list[ComplianceFinding] = []
    for approval in approvals:
        approver = approval.approved_by
        if approver is None:
            continue
        if _normalize_principal(approver) != _normalize_principal(approval.requested_by):
            continue
        findings.append(
            ComplianceFinding(
                framework=ComplianceFramework.SOX,
                control_id=CONTROL_SOX_SEGREGATION_OF_DUTIES,
                severity=Severity.CRITICAL,
                description=(
                    f"Change {approval.change_id} was both requested and approved by "
                    f"'{approval.requested_by}'. The approval provided no independent review, "
                    f"so the change reached production with a single pair of eyes on it while "
                    f"leaving an audit trail that looks reviewed."
                ),
                record_id=approval.change_id,
                evidence={
                    "change_id": approval.change_id,
                    "requested_by": approval.requested_by,
                    "approved_by": approver,
                },
            )
        )
    if findings:
        _logger.warning(
            "Segregation-of-duties violations detected on %d change(s).",
            len(findings),
        )
    return findings


def audited_change_ids(events: Iterable[AuditEvent]) -> set[str]:
    """Extract the set of change ids that have audit coverage.

    Args:
        events: Audit events, typically from
            :class:`cockpit.security.audit_logging.AuditLog`.

    Returns:
        The ``resource`` value of every event, which is where a change id
        lands when the change is recorded through the audit log.
    """
    return {event.resource for event in events}


def check_change_audit_completeness(
    approvals: Iterable[ApprovalRecordLike],
    audited_ids: Iterable[str],
) -> list[ComplianceFinding]:
    """Flag changes that have no corresponding audit record.

    A change with no audit record is unreconstructable after the fact: there
    is no evidence of when it happened, who ran it, or what it did. That gap
    is worse than a bad change, because a bad change with a record can at
    least be traced and reversed.

    Args:
        approvals: The changes that are known to have occurred.
        audited_ids: Change ids that do have an audit record -- see
            :func:`audited_change_ids` to derive these from an audit log.

    Returns:
        One finding per change missing an audit record.
    """
    covered = set(audited_ids)
    return [
        ComplianceFinding(
            framework=ComplianceFramework.SOX,
            control_id=CONTROL_SOX_CHANGE_AUDIT,
            severity=Severity.HIGH,
            description=(
                f"Change {approval.change_id} has no audit record. There is no evidence of "
                f"when it was applied or by whom, so it cannot be reconstructed or reviewed "
                f"after the fact."
            ),
            record_id=approval.change_id,
            evidence={
                "change_id": approval.change_id,
                "requested_by": approval.requested_by,
                "approved_by": approval.approved_by,
            },
        )
        for approval in approvals
        if approval.change_id not in covered
    ]


@dataclass(frozen=True)
class ComplianceContext:
    """The inputs a compliance run evaluates.

    Every field is optional. Whatever is missing simply means the controls
    depending on it were not evaluated, and that is reported explicitly in
    :attr:`ComplianceReport.controls_skipped` rather than passing silently.

    Attributes:
        records: The data records under review.
        retention_policies: Retention windows by data category.
        consent: Consent ledger to evaluate processing purposes against.
        pending_erasure_requests: Subject ids with an outstanding erasure
            request, evaluated without mutating anything so blocked records
            surface in the report.
        designated_phi_fields: Field names permitted to hold identifiers.
        purpose_field_map: Declared field sets for each processing purpose.
        field_accesses: Accesses to evaluate for minimum necessary and, when
            a consent ledger is supplied, for a valid consent basis.
        approvals: Change approval records for the SOX checks.
        audited_change_ids: Change ids that have an audit record.
        now: Evaluation time; defaults to now (UTC).
    """

    records: tuple[DataRecord, ...] = ()
    retention_policies: tuple[RetentionPolicy, ...] = ()
    consent: ConsentLedger | None = None
    pending_erasure_requests: tuple[str, ...] = ()
    designated_phi_fields: tuple[str, ...] = ()
    purpose_field_map: Mapping[str, Sequence[str]] = field(default_factory=dict)
    field_accesses: tuple[FieldAccess, ...] = ()
    approvals: tuple[ApprovalRecordLike, ...] = ()
    audited_change_ids: frozenset[str] = frozenset()
    now: datetime | None = None


def _check_gdpr(context: ComplianceContext) -> tuple[list[ComplianceFinding], list[str], list[str]]:
    """Run the GDPR controls that the context supplies inputs for.

    Args:
        context: The inputs to evaluate.

    Returns:
        A ``(findings, controls_evaluated, controls_skipped)`` triple.
    """
    findings: list[ComplianceFinding] = []
    evaluated: list[str] = []
    skipped: list[str] = []

    if context.records and context.retention_policies:
        evaluated.append(CONTROL_GDPR_RETENTION)
        findings.extend(
            check_retention(context.records, context.retention_policies, now=context.now)
        )
    else:
        skipped.append(
            f"{CONTROL_GDPR_RETENTION}: no records and/or no retention policies were supplied."
        )

    if context.pending_erasure_requests and context.records:
        evaluated.append(CONTROL_GDPR_ERASURE_EXCEPTION)
        for subject_id in context.pending_erasure_requests:
            outcome = erase_subject_records(context.records, subject_id, now=context.now)
            findings.extend(outcome.findings)
    else:
        skipped.append(
            f"{CONTROL_GDPR_ERASURE_EXCEPTION}: no pending erasure requests were supplied."
        )

    if context.consent is not None and context.field_accesses:
        evaluated.append(CONTROL_GDPR_CONSENT)
        findings.extend(_check_consent_for_accesses(context))
    else:
        skipped.append(
            f"{CONTROL_GDPR_CONSENT}: no consent ledger and/or no field accesses were supplied."
        )

    return findings, evaluated, skipped


def _check_consent_for_accesses(context: ComplianceContext) -> list[ComplianceFinding]:
    """Check each recorded access against the consent on file at that time.

    Args:
        context: The inputs to evaluate; must carry a consent ledger.

    Returns:
        Findings for accesses with no valid consent basis at access time.
    """
    ledger = context.consent
    if ledger is None:
        return []

    findings: list[ComplianceFinding] = []
    for access in context.field_accesses:
        if access.subject_id is None:
            continue
        # Evaluated at the time of the access, not now: consent granted
        # afterwards does not retroactively legitimize past processing, and
        # consent withdrawn afterwards does not retroactively taint it.
        as_of = access.accessed_at or context.now
        status = ledger.status(access.subject_id, access.purpose, as_of=as_of)
        if status.is_valid:
            continue
        findings.append(
            ComplianceFinding(
                framework=ComplianceFramework.GDPR,
                control_id=CONTROL_GDPR_CONSENT,
                severity=Severity.HIGH,
                description=(
                    f"Principal '{access.actor_id}' processed data for subject "
                    f"{access.subject_id} under purpose '{access.purpose}' with no valid "
                    f"consent on file at the time. {status.reason} If the processing relies on "
                    f"a lawful basis other than consent, record that basis instead."
                ),
                subject_id=access.subject_id,
                evidence={
                    "purpose": access.purpose,
                    "actor_id": access.actor_id,
                    "consent_decided_at": (
                        status.decided_at.isoformat() if status.decided_at is not None else None
                    ),
                },
            )
        )
    return findings


def _check_hipaa(
    context: ComplianceContext,
) -> tuple[list[ComplianceFinding], list[str], list[str]]:
    """Run the HIPAA controls that the context supplies inputs for.

    Args:
        context: The inputs to evaluate.

    Returns:
        A ``(findings, controls_evaluated, controls_skipped)`` triple.
    """
    findings: list[ComplianceFinding] = []
    evaluated: list[str] = []
    skipped: list[str] = []

    if context.records:
        evaluated.append(CONTROL_HIPAA_PHI_PLACEMENT)
        findings.extend(detect_undesignated_phi(context.records, context.designated_phi_fields))
    else:
        skipped.append(f"{CONTROL_HIPAA_PHI_PLACEMENT}: no records were supplied.")

    if context.field_accesses:
        evaluated.append(CONTROL_HIPAA_MINIMUM_NECESSARY)
        for access in context.field_accesses:
            findings.extend(
                check_minimum_necessary(
                    access.purpose,
                    access.fields,
                    context.purpose_field_map,
                    actor_id=access.actor_id,
                    subject_id=access.subject_id,
                )
            )
    else:
        skipped.append(f"{CONTROL_HIPAA_MINIMUM_NECESSARY}: no field accesses were supplied.")

    return findings, evaluated, skipped


def _check_sox(context: ComplianceContext) -> tuple[list[ComplianceFinding], list[str], list[str]]:
    """Run the SOX controls that the context supplies inputs for.

    Args:
        context: The inputs to evaluate.

    Returns:
        A ``(findings, controls_evaluated, controls_skipped)`` triple.
    """
    findings: list[ComplianceFinding] = []
    evaluated: list[str] = []
    skipped: list[str] = []

    if context.approvals:
        evaluated.append(CONTROL_SOX_SEGREGATION_OF_DUTIES)
        findings.extend(check_segregation_of_duties(context.approvals))
        evaluated.append(CONTROL_SOX_CHANGE_AUDIT)
        findings.extend(
            check_change_audit_completeness(context.approvals, context.audited_change_ids)
        )
    else:
        skipped.append(f"{CONTROL_SOX_SEGREGATION_OF_DUTIES}: no approval records were supplied.")
        skipped.append(f"{CONTROL_SOX_CHANGE_AUDIT}: no approval records were supplied.")

    return findings, evaluated, skipped


# Every framework checker has the same shape: take a context, return
# (findings, controls evaluated, controls skipped). Typed precisely rather
# than as Any so a checker that drifts from the contract fails type checking
# instead of silently returning something run_compliance_check mishandles.
FrameworkChecker = Callable[
    ["ComplianceContext"], tuple[list[ComplianceFinding], list[str], list[str]]
]

_FRAMEWORK_CHECKS: Final[dict[ComplianceFramework, FrameworkChecker]] = {
    ComplianceFramework.GDPR: _check_gdpr,
    ComplianceFramework.HIPAA: _check_hipaa,
    ComplianceFramework.SOX: _check_sox,
}


def run_compliance_check(
    framework: ComplianceFramework,
    context: ComplianceContext,
) -> list[ComplianceFinding]:
    """Run every control for one framework that the context supports.

    Gated on ``feature_flags.is_enabled("security")``, mirroring
    :func:`cockpit.security.input_security.validate_input`. When the flag is
    off this returns an empty list -- which means "nothing was checked", not
    "nothing was wrong". Use :func:`generate_compliance_report` when that
    distinction needs to survive into what a reader sees.

    Args:
        framework: The framework whose controls to run.
        context: The inputs to evaluate.

    Returns:
        Findings from the controls that had the inputs they needed. Empty if
        the security framework is disabled.

    Raises:
        KeyError: If ``framework`` is not a supported framework.
    """
    if not is_enabled("security"):
        _logger.debug("Security framework disabled; skipping compliance checks.")
        return []

    findings, evaluated, skipped = _FRAMEWORK_CHECKS[framework](context)
    _logger.info(
        "Compliance check for %s: %d finding(s), %d control(s) evaluated, %d skipped.",
        framework,
        len(findings),
        len(evaluated),
        len(skipped),
    )
    return findings


@dataclass(frozen=True)
class ComplianceReport:
    """Findings and coverage from a compliance run.

    **This class has no verdict field and will not be given one.** There is
    no ``compliant``, no ``passed``, no overall score. Those fields get
    screenshotted, pasted into slide decks, and eventually shown to someone
    who takes them as an assurance that this software is in no position to
    give. What it does report is exactly two things: what was found, and
    what was looked at.

    :attr:`controls_skipped` is as important as :attr:`findings`. A report
    with no findings because nothing was checked reads identically to a
    clean one unless the skips are stated, so they are.

    Attributes:
        generated_at: When the report was produced (UTC).
        frameworks: Frameworks the run covered.
        findings: Every finding produced, most severe first.
        controls_evaluated: Control ids that actually ran.
        controls_skipped: Control ids that did not run, each with the reason.
        notes: Additional context for the reader.
    """

    generated_at: datetime
    frameworks: tuple[ComplianceFramework, ...]
    findings: tuple[ComplianceFinding, ...]
    controls_evaluated: tuple[str, ...]
    controls_skipped: tuple[str, ...]
    notes: tuple[str, ...] = ()

    def findings_by_framework(self) -> dict[ComplianceFramework, list[ComplianceFinding]]:
        """Group findings by framework.

        Returns:
            A dict keyed by every framework the run covered, including those
            with an empty list -- "covered and found nothing" and "not
            covered" must not look the same.
        """
        grouped: dict[ComplianceFramework, list[ComplianceFinding]] = {
            framework: [] for framework in self.frameworks
        }
        for finding in self.findings:
            grouped.setdefault(finding.framework, []).append(finding)
        return grouped

    def findings_by_severity(self) -> dict[Severity, list[ComplianceFinding]]:
        """Group findings by severity.

        Returns:
            A dict keyed by every severity level, most of which are usually
            empty. Fixed keys keep a caller from mistaking a missing key for
            a zero count.
        """
        grouped: dict[Severity, list[ComplianceFinding]] = {severity: [] for severity in Severity}
        for finding in self.findings:
            grouped[finding.severity].append(finding)
        return grouped

    def severity_counts(self) -> dict[Severity, int]:
        """Count findings at each severity level.

        Returns:
            A dict from severity to count, with zero entries included.
        """
        return {severity: len(items) for severity, items in self.findings_by_severity().items()}


def _sort_key(finding: ComplianceFinding) -> tuple[int, str, str]:
    """Order findings most severe first, then by framework and control.

    Args:
        finding: The finding to key.

    Returns:
        A sort key placing higher severities first.
    """
    return (-_SEVERITY_RANK[finding.severity], str(finding.framework), finding.control_id)


def generate_compliance_report(
    context: ComplianceContext,
    frameworks: Sequence[ComplianceFramework] | None = None,
) -> ComplianceReport:
    """Run the requested frameworks' controls and assemble a report.

    Gated on ``feature_flags.is_enabled("security")``. When the flag is off
    the report is still returned, with no findings and every control listed
    as skipped, so a reader can never mistake a disabled framework for a
    clean result.

    Args:
        context: The inputs to evaluate.
        frameworks: Frameworks to cover; defaults to all of them.

    Returns:
        A :class:`ComplianceReport` of findings and coverage. It carries no
        pass/fail verdict, by design -- see the class docstring.
    """
    selected = tuple(frameworks) if frameworks is not None else tuple(ComplianceFramework)
    generated_at = _resolve_now(context.now)

    if not is_enabled("security"):
        _logger.debug("Security framework disabled; producing an empty compliance report.")
        return ComplianceReport(
            generated_at=generated_at,
            frameworks=selected,
            findings=(),
            controls_evaluated=(),
            controls_skipped=tuple(
                f"All {framework} controls: the security framework is disabled."
                for framework in selected
            ),
            notes=(
                DISCLAIMER,
                "No controls were evaluated because the security framework is disabled. "
                "The absence of findings here carries no information at all.",
            ),
        )

    findings: list[ComplianceFinding] = []
    evaluated: list[str] = []
    skipped: list[str] = []
    for framework in selected:
        run_findings, run_evaluated, run_skipped = _FRAMEWORK_CHECKS[framework](context)
        findings.extend(run_findings)
        evaluated.extend(run_evaluated)
        skipped.extend(run_skipped)

    findings.sort(key=_sort_key)
    _logger.info(
        "Compliance report generated: %d finding(s) across %d framework(s); "
        "%d control(s) evaluated, %d skipped.",
        len(findings),
        len(selected),
        len(evaluated),
        len(skipped),
    )
    return ComplianceReport(
        generated_at=generated_at,
        frameworks=selected,
        findings=tuple(findings),
        controls_evaluated=tuple(evaluated),
        controls_skipped=tuple(skipped),
        notes=(DISCLAIMER,),
    )


def render_report_markdown(report: ComplianceReport) -> str:
    """Render a report as Markdown, disclaimer first.

    The disclaimer leads the document because a rendered report is the
    artifact most likely to be forwarded on its own, detached from the code
    and from whoever knows what it does and does not mean.

    Args:
        report: The report to render.

    Returns:
        A Markdown document listing coverage and findings.
    """
    lines: list[str] = [
        "# Technical Control Findings",
        "",
        f"> {DISCLAIMER}",
        "",
        f"Generated: {report.generated_at.isoformat()}",
        f"Frameworks covered: {', '.join(str(f) for f in report.frameworks) or 'none'}",
        "",
        "## Coverage",
        "",
        f"Controls evaluated: {len(report.controls_evaluated)}",
    ]
    lines.extend(f"- {control}" for control in report.controls_evaluated)
    lines.extend(["", f"Controls not evaluated: {len(report.controls_skipped)}"])
    lines.extend(f"- {reason}" for reason in report.controls_skipped)

    counts = report.severity_counts()
    lines.extend(["", "## Findings by severity", ""])
    lines.extend(f"- {severity}: {counts[severity]}" for severity in reversed(list(Severity)))

    for framework, items in report.findings_by_framework().items():
        lines.extend(["", f"## {str(framework).upper()} findings ({len(items)})", ""])
        if not items:
            lines.append(
                "No findings from the controls that ran. This is not a statement that the "
                "framework's requirements are met."
            )
            continue
        for finding in items:
            target = finding.record_id or finding.subject_id or "-"
            lines.append(
                f"- **[{str(finding.severity).upper()}] {finding.control_id}** "
                f"({target}): {finding.description}"
            )

    if report.notes:
        lines.extend(["", "## Notes", ""])
        lines.extend(f"- {note}" for note in report.notes)

    return "\n".join(lines)


__all__ = [
    "CONTROL_GDPR_CONSENT",
    "CONTROL_GDPR_ERASURE_EXCEPTION",
    "CONTROL_GDPR_RETENTION",
    "CONTROL_GDPR_RETENTION_POLICY_GAP",
    "CONTROL_HIPAA_MINIMUM_NECESSARY",
    "CONTROL_HIPAA_PHI_PLACEMENT",
    "CONTROL_SOX_CHANGE_AUDIT",
    "CONTROL_SOX_SEGREGATION_OF_DUTIES",
    "DISCLAIMER",
    "ERASURE_TOKEN",
    "ApprovalRecordLike",
    "ComplianceContext",
    "ComplianceFinding",
    "ComplianceFramework",
    "ComplianceReport",
    "ConsentDecision",
    "ConsentLedger",
    "ConsentStatus",
    "DataRecord",
    "ErasureBlocker",
    "ErasureException",
    "ErasureMode",
    "ErasureOutcome",
    "FieldAccess",
    "RetentionPolicy",
    "Severity",
    "audited_change_ids",
    "check_change_audit_completeness",
    "check_minimum_necessary",
    "check_retention",
    "check_segregation_of_duties",
    "detect_undesignated_phi",
    "erase_subject_records",
    "export_subject_data_json",
    "export_subject_records",
    "generate_compliance_report",
    "record_to_dict",
    "references_subject",
    "render_report_markdown",
    "run_compliance_check",
]
