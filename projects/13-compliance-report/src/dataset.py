"""Load and validate the synthetic dataset the compliance controls run over.

The controls in :mod:`cockpit.security.compliance` are storage-agnostic on
purpose: they take plain :class:`~cockpit.security.compliance.DataRecord`
shapes rather than assuming a schema. This module is the adapter for one
concrete store -- a JSON file -- and it exists to show what that adapter has
to do:

* **Validate loudly.** A malformed dataset must fail with a message naming
  the offending element, not produce a short record list that reads as "this
  subject has less data than you thought". Silent truncation is the failure
  mode that turns a loader bug into a wrong answer to a legal request.
* **Resolve time relative to an injected anchor.** Ages in the file are
  offsets in days, not timestamps. A retention window compared against a
  frozen file of absolute dates slowly changes its answer as the file ages,
  which makes tests flaky and demonstrations wrong.
* **Distinguish "empty" from "absent".** A consent ledger with no decisions
  is not the same input as no ledger at all: the first makes every access
  look unconsented, the second makes the control report itself as skipped.
  Only the second is honest when the data was never supplied.

Nothing here decides anything about compliance. It only builds inputs.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from cockpit.security.compliance import (
    ComplianceFramework,
    ConsentLedger,
    DataRecord,
    FieldAccess,
    RetentionPolicy,
)

DEFAULT_DATASET_PATH: Final[Path] = Path(__file__).resolve().parent.parent / "data" / "records.json"


class DatasetError(ValueError):
    """Raised when the dataset file is missing, malformed, or inconsistent.

    A distinct type so the CLI can turn it into a configuration exit code
    rather than a traceback, and so a caller can tell a bad input file apart
    from a bug in the checks.
    """


@dataclass(frozen=True)
class ChangeApproval:
    """A change request and its approval.

    Structurally satisfies
    :class:`~cockpit.security.compliance.ApprovalRecordLike`, which is a
    :class:`~typing.Protocol` rather than a base class -- so this project
    declares the three attributes the SOX checks read and never imports an
    approval-workflow class it does not need.

    Attributes:
        change_id: Identifier of the change.
        requested_by: Principal that requested the change.
        approved_by: Principal that approved it, or None if still pending.
    """

    change_id: str
    requested_by: str
    approved_by: str | None = None


class InputGroup(StrEnum):
    """A group of inputs that can be dropped to demonstrate a skipped control.

    Attributes:
        RECORDS: The data records themselves.
        RETENTION_POLICIES: Retention windows by category.
        CONSENT: The consent ledger.
        FIELD_ACCESSES: Recorded accesses to fields.
        APPROVALS: Change approvals and their audit coverage.
        ERASURE_REQUESTS: Pending right-to-erasure requests.
    """

    RECORDS = "records"
    RETENTION_POLICIES = "policies"
    CONSENT = "consent"
    FIELD_ACCESSES = "accesses"
    APPROVALS = "approvals"
    ERASURE_REQUESTS = "erasure"


@dataclass(frozen=True)
class ComplianceDataset:
    """Every input one compliance run needs, already validated.

    Attributes:
        records: The data records under review.
        retention_policies: Retention windows by data category.
        consent: The consent ledger, or None if none was supplied.
        designated_phi_fields: Field names the schema permits to hold
            identifiers.
        purpose_field_map: Declared field sets keyed by processing purpose.
        field_accesses: Recorded accesses to evaluate.
        approvals: Change approvals for the SOX controls.
        audited_change_ids: Change ids that have an audit record.
        pending_erasure_requests: Subjects with an outstanding erasure request.
        anchor: The evaluation time every relative offset was resolved
            against. Carried on the dataset so a report, a test, and a
            re-run all share one clock.
        source: Where the dataset was loaded from, for the report header.
    """

    records: tuple[DataRecord, ...]
    retention_policies: tuple[RetentionPolicy, ...]
    consent: ConsentLedger | None
    designated_phi_fields: tuple[str, ...]
    purpose_field_map: Mapping[str, tuple[str, ...]]
    field_accesses: tuple[FieldAccess, ...]
    approvals: tuple[ChangeApproval, ...]
    audited_change_ids: frozenset[str]
    pending_erasure_requests: tuple[str, ...]
    anchor: datetime
    source: str = str(DEFAULT_DATASET_PATH)


_OMIT_FIELDS: Final[dict[InputGroup, dict[str, Any]]] = {
    InputGroup.RECORDS: {"records": ()},
    InputGroup.RETENTION_POLICIES: {"retention_policies": ()},
    InputGroup.CONSENT: {"consent": None},
    InputGroup.FIELD_ACCESSES: {"field_accesses": ()},
    InputGroup.APPROVALS: {"approvals": (), "audited_change_ids": frozenset()},
    InputGroup.ERASURE_REQUESTS: {"pending_erasure_requests": ()},
}


def omit_inputs(dataset: ComplianceDataset, groups: Iterable[InputGroup]) -> ComplianceDataset:
    """Return a copy of the dataset with whole input groups removed.

    Exists so the skipped-control path can be demonstrated from the CLI
    rather than only asserted in a test. Dropping an input does not make the
    report cleaner -- it moves a control from "evaluated" to "not evaluated",
    which is the point being illustrated.

    Args:
        dataset: The dataset to reduce.
        groups: Input groups to drop.

    Returns:
        A new dataset with those groups emptied.
    """
    result = dataset
    for group in groups:
        result = replace(result, **_OMIT_FIELDS[group])
    return result


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    """Assert that a value is a JSON object.

    Args:
        value: The decoded JSON value.
        where: Human-readable location, used in the error message.

    Returns:
        The value, narrowed to a mapping.

    Raises:
        DatasetError: If the value is not a JSON object.
    """
    if not isinstance(value, Mapping):
        raise DatasetError(f"{where}: expected a JSON object, got {type(value).__name__}.")
    return value


def _require_list(value: Any, where: str) -> list[Any]:
    """Assert that a value is a JSON array.

    Args:
        value: The decoded JSON value.
        where: Human-readable location, used in the error message.

    Returns:
        The value, narrowed to a list.

    Raises:
        DatasetError: If the value is not a JSON array.
    """
    if not isinstance(value, list):
        raise DatasetError(f"{where}: expected a JSON array, got {type(value).__name__}.")
    return value


def _require_str(payload: Mapping[str, Any], key: str, where: str) -> str:
    """Read a required non-empty string field.

    Args:
        payload: The object to read from.
        key: The field name.
        where: Human-readable location, used in the error message.

    Returns:
        The field value.

    Raises:
        DatasetError: If the field is missing, not a string, or blank.
    """
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DatasetError(f"{where}: '{key}' is required and must be a non-empty string.")
    return value


def _require_int(payload: Mapping[str, Any], key: str, where: str) -> int:
    """Read a required integer field.

    Args:
        payload: The object to read from.
        key: The field name.
        where: Human-readable location, used in the error message.

    Returns:
        The field value.

    Raises:
        DatasetError: If the field is missing or not an integer. Booleans are
            rejected: ``True`` is an ``int`` in Python and would silently
            become a one-day age.
    """
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DatasetError(f"{where}: '{key}' is required and must be an integer.")
    return value


def _optional_int(payload: Mapping[str, Any], key: str, where: str) -> int | None:
    """Read an optional integer field that may be JSON null.

    Args:
        payload: The object to read from.
        key: The field name.
        where: Human-readable location, used in the error message.

    Returns:
        The field value, or None if absent or null.

    Raises:
        DatasetError: If the field is present, non-null, and not an integer.
    """
    if payload.get(key) is None:
        return None
    return _require_int(payload, key, where)


def _require_bool(payload: Mapping[str, Any], key: str, where: str) -> bool:
    """Read an optional boolean field, defaulting to False.

    Args:
        payload: The object to read from.
        key: The field name.
        where: Human-readable location, used in the error message.

    Returns:
        The field value, or False if absent.

    Raises:
        DatasetError: If the field is present and not a boolean.
    """
    value = payload.get(key, False)
    if not isinstance(value, bool):
        raise DatasetError(f"{where}: '{key}' must be true or false.")
    return value


def _require_str_list(value: Any, where: str) -> tuple[str, ...]:
    """Read an array of non-empty strings.

    Args:
        value: The decoded JSON value.
        where: Human-readable location, used in the error message.

    Returns:
        The values as a tuple.

    Raises:
        DatasetError: If the value is not an array of non-empty strings.
    """
    items = _require_list(value, where)
    for index, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            raise DatasetError(f"{where}[{index}]: expected a non-empty string.")
    return tuple(str(item) for item in items)


def _shift(anchor: datetime, days: int) -> datetime:
    """Move the anchor backwards by a number of days.

    Args:
        anchor: The evaluation time.
        days: How many days back, negative for the future.

    Returns:
        The resolved timestamp.
    """
    return anchor - timedelta(days=days)


def _build_record(payload: Mapping[str, Any], anchor: datetime, where: str) -> DataRecord:
    """Build one :class:`DataRecord` from its JSON object.

    Args:
        payload: The record object.
        anchor: The evaluation time relative offsets resolve against.
        where: Human-readable location, used in error messages.

    Returns:
        The parsed record.

    Raises:
        DatasetError: If any field is missing or of the wrong type.
    """
    fields_value = payload.get("fields", {})
    fields = _require_mapping(fields_value, f"{where}.fields")
    obligation_days = _optional_int(payload, "retention_obligation_in_days", where)
    return DataRecord(
        record_id=_require_str(payload, "record_id", where),
        subject_id=_require_str(payload, "subject_id", where),
        category=_require_str(payload, "category", where),
        created_at=_shift(anchor, _require_int(payload, "created_days_ago", where)),
        fields=dict(fields),
        legal_hold=_require_bool(payload, "legal_hold", where),
        retention_obligation_until=(
            _shift(anchor, -obligation_days) if obligation_days is not None else None
        ),
        related_subject_ids=_require_str_list(
            payload.get("related_subject_ids", []), f"{where}.related_subject_ids"
        ),
    )


def _build_policy(payload: Mapping[str, Any], where: str) -> RetentionPolicy:
    """Build one :class:`RetentionPolicy` from its JSON object.

    Args:
        payload: The policy object.
        where: Human-readable location, used in error messages.

    Returns:
        The parsed policy.

    Raises:
        DatasetError: If a field is missing, mistyped, names an unknown
            framework, or specifies a non-positive window.
    """
    raw_framework = payload.get("framework", "gdpr")
    if not isinstance(raw_framework, str):
        raise DatasetError(f"{where}: 'framework' must be a string.")
    try:
        framework = ComplianceFramework(raw_framework)
    except ValueError as exc:
        known = ", ".join(str(item) for item in ComplianceFramework)
        raise DatasetError(
            f"{where}: unknown framework '{raw_framework}'. Known: {known}."
        ) from exc

    basis = payload.get("basis", "")
    if not isinstance(basis, str):
        raise DatasetError(f"{where}: 'basis' must be a string.")

    try:
        return RetentionPolicy(
            category=_require_str(payload, "category", where),
            max_age_days=_require_int(payload, "max_age_days", where),
            basis=basis,
            framework=framework,
        )
    except ValueError as exc:
        raise DatasetError(f"{where}: {exc}") from exc


def _build_access(payload: Mapping[str, Any], anchor: datetime, where: str) -> FieldAccess:
    """Build one :class:`FieldAccess` from its JSON object.

    Args:
        payload: The access object.
        anchor: The evaluation time relative offsets resolve against.
        where: Human-readable location, used in error messages.

    Returns:
        The parsed access.

    Raises:
        DatasetError: If any field is missing or of the wrong type.
    """
    subject_id = payload.get("subject_id")
    if subject_id is not None and (not isinstance(subject_id, str) or not subject_id.strip()):
        raise DatasetError(f"{where}: 'subject_id' must be a non-empty string or null.")
    return FieldAccess(
        purpose=_require_str(payload, "purpose", where),
        fields=_require_str_list(payload.get("fields", []), f"{where}.fields"),
        actor_id=_require_str(payload, "actor_id", where),
        subject_id=subject_id,
        accessed_at=_shift(anchor, _require_int(payload, "accessed_days_ago", where)),
    )


def _build_approval(payload: Mapping[str, Any], where: str) -> ChangeApproval:
    """Build one :class:`ChangeApproval` from its JSON object.

    Args:
        payload: The approval object.
        where: Human-readable location, used in error messages.

    Returns:
        The parsed approval.

    Raises:
        DatasetError: If any field is missing or of the wrong type.
    """
    approved_by = payload.get("approved_by")
    if approved_by is not None and not isinstance(approved_by, str):
        raise DatasetError(f"{where}: 'approved_by' must be a string or null.")
    return ChangeApproval(
        change_id=_require_str(payload, "change_id", where),
        requested_by=_require_str(payload, "requested_by", where),
        approved_by=approved_by,
    )


def _build_consent(entries: list[Any], anchor: datetime) -> ConsentLedger | None:
    """Replay consent decisions into a ledger, oldest first.

    Args:
        entries: The decoded ``consent_decisions`` array.
        anchor: The evaluation time relative offsets resolve against.

    Returns:
        A populated ledger, or None if no decisions were supplied -- an
        empty ledger and an absent ledger are different inputs, and only the
        second should make the consent control report itself as skipped.

    Raises:
        DatasetError: If any decision is missing a field or mistyped.
    """
    if not entries:
        return None

    parsed: list[tuple[datetime, str, str, bool, str]] = []
    for index, raw in enumerate(entries):
        where = f"consent_decisions[{index}]"
        payload = _require_mapping(raw, where)
        granted = payload.get("granted")
        if not isinstance(granted, bool):
            raise DatasetError(f"{where}: 'granted' is required and must be true or false.")
        note = payload.get("note", "")
        if not isinstance(note, str):
            raise DatasetError(f"{where}: 'note' must be a string.")
        parsed.append(
            (
                _shift(anchor, _require_int(payload, "decided_days_ago", where)),
                _require_str(payload, "subject_id", where),
                _require_str(payload, "purpose", where),
                granted,
                note,
            )
        )

    # Appended oldest first so the ledger's insertion order matches the order
    # the decisions were actually made in. The ledger breaks same-timestamp
    # ties by insertion order, so file order must not decide who wins.
    ledger = ConsentLedger()
    for at, subject_id, purpose, granted, note in sorted(parsed, key=lambda item: item[0]):
        if granted:
            ledger.grant(subject_id, purpose, at=at, note=note)
        else:
            ledger.withdraw(subject_id, purpose, at=at, note=note)
    return ledger


def _build_purpose_field_map(value: Any) -> dict[str, tuple[str, ...]]:
    """Parse the declared field set for each processing purpose.

    Args:
        value: The decoded ``purpose_field_map`` object.

    Returns:
        Field names by purpose.

    Raises:
        DatasetError: If the value is not an object of string arrays.
    """
    mapping = _require_mapping(value, "purpose_field_map")
    return {
        str(purpose): _require_str_list(fields, f"purpose_field_map['{purpose}']")
        for purpose, fields in mapping.items()
    }


def load_dataset(
    path: Path = DEFAULT_DATASET_PATH,
    *,
    now: datetime | None = None,
) -> ComplianceDataset:
    """Load, validate, and time-resolve the dataset file.

    Args:
        path: Path to the JSON dataset.
        now: Evaluation time every relative offset is resolved against.
            Defaults to the current UTC time. Injected so a test, a report,
            and a re-run all agree on what "420 days old" means.

    Returns:
        The validated :class:`ComplianceDataset`.

    Raises:
        DatasetError: If the file is missing, is not valid JSON, or any
            element is missing a required field or has the wrong type. The
            message names the element so a bad file can be fixed without
            reading this code.
    """
    anchor = now.astimezone(UTC) if now is not None else datetime.now(UTC)

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DatasetError(f"Could not read dataset file '{path}': {exc}") from exc

    try:
        decoded = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"'{path.name}' is not valid JSON: {exc}") from exc

    root = _require_mapping(decoded, f"'{path.name}'")

    records = tuple(
        _build_record(_require_mapping(raw, f"records[{index}]"), anchor, f"records[{index}]")
        for index, raw in enumerate(_require_list(root.get("records", []), "records"))
    )
    seen: set[str] = set()
    for record in records:
        if record.record_id in seen:
            raise DatasetError(
                f"records: duplicate record_id '{record.record_id}'. Record ids identify "
                f"findings, so a duplicate makes a finding ambiguous."
            )
        seen.add(record.record_id)

    policies = tuple(
        _build_policy(
            _require_mapping(raw, f"retention_policies[{index}]"), f"retention_policies[{index}]"
        )
        for index, raw in enumerate(
            _require_list(root.get("retention_policies", []), "retention_policies")
        )
    )
    accesses = tuple(
        _build_access(
            _require_mapping(raw, f"field_accesses[{index}]"), anchor, f"field_accesses[{index}]"
        )
        for index, raw in enumerate(_require_list(root.get("field_accesses", []), "field_accesses"))
    )
    approvals = tuple(
        _build_approval(_require_mapping(raw, f"approvals[{index}]"), f"approvals[{index}]")
        for index, raw in enumerate(_require_list(root.get("approvals", []), "approvals"))
    )

    return ComplianceDataset(
        records=records,
        retention_policies=policies,
        consent=_build_consent(
            _require_list(root.get("consent_decisions", []), "consent_decisions"), anchor
        ),
        designated_phi_fields=_require_str_list(
            root.get("designated_phi_fields", []), "designated_phi_fields"
        ),
        purpose_field_map=_build_purpose_field_map(root.get("purpose_field_map", {})),
        field_accesses=accesses,
        approvals=approvals,
        audited_change_ids=frozenset(
            _require_str_list(root.get("audited_change_ids", []), "audited_change_ids")
        ),
        pending_erasure_requests=_require_str_list(
            root.get("pending_erasure_requests", []), "pending_erasure_requests"
        ),
        anchor=anchor,
        source=str(path),
    )


__all__ = [
    "DEFAULT_DATASET_PATH",
    "ChangeApproval",
    "ComplianceDataset",
    "DatasetError",
    "InputGroup",
    "load_dataset",
    "omit_inputs",
]
