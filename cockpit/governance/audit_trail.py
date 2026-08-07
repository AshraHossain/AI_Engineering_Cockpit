"""Governance audit trail for model-lifecycle and approval decisions.

Distinct from :mod:`cockpit.security.audit_logging`, which records
security-relevant access events ("who touched what"). This trail records
*governance* events: a version was registered, a stage was promoted or
demoted, production was rolled back, an approval was requested, granted,
denied, or rejected. The two answer different questions and are queried by
different people, so they are separate append-only streams.

Reuse decision -- the hash chain is imported, not reimplemented
-----------------------------------------------------------------
:mod:`cockpit.security.audit_logging` already owns a reviewed, tested
SHA-256 chain: :func:`~cockpit.security.audit_logging.canonical_payload`
pins the serialization, :func:`~cockpit.security.audit_logging.compute_entry_hash`
digests it, and :func:`~cockpit.security.audit_logging.verify_chain` locates
the first broken link. Reimplementing that here would give two
canonicalizations to keep in step and two places for a subtle
determinism bug to hide, for no benefit -- integrity semantics are not
domain-specific.

So this module keeps its own *record shape* (typed actor / action /
subject / before / after, which the security event shape does not carry
as first-class fields) and projects each record onto an
:class:`~cockpit.security.audit_logging.AuditEvent` purely for hashing.
The projection is deterministic and total, so verification rebuilds it
rather than storing it twice.

Deliberately *not* reused: :class:`~cockpit.security.audit_logging.AuditLog`
itself. It masks PII in metadata before hashing, which is right for a
security log fed raw request bodies and wrong here -- silently redacting a
before/after state would make the governance record lie about what changed.

Only the top-level mutating entry point, :func:`record_governance_event`,
is gated on ``feature_flags.is_enabled("governance")``. Hashing,
verification, and every query stay available regardless, since a disabled
framework is exactly when someone is likely to be reading the history.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from cockpit.config.feature_flags import is_enabled
from cockpit.security.audit_logging import (
    GENESIS_HASH,
    AuditEvent,
    AuditRecord,
    ChainVerification,
    compute_entry_hash,
    verify_chain,
)
from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)

# Default outcome for an event that simply happened. Kept as a constant so
# the "something was refused" case reads as a deliberate difference.
OUTCOME_RECORDED = "recorded"
OUTCOME_DENIED = "denied"


class GovernanceAction(StrEnum):
    """The governance actions this trail knows how to record.

    A closed set rather than a free-form string: the trail is queried and
    reported on, and a typo in an action name silently drops an event out
    of whatever report filters on it.

    Attributes:
        VERSION_REGISTERED: A new model version entered the registry.
        STAGE_PROMOTED: A version moved forward through the lifecycle.
        STAGE_DEMOTED: A version moved back, including the automatic
            demotion of a production incumbent.
        PRODUCTION_ROLLED_BACK: Production was restored to the previously
            serving version.
        APPROVAL_REQUESTED: An approval request was opened.
        APPROVAL_GRANTED: An approver signed off on a request.
        APPROVAL_REJECTED: An approver vetoed a request.
        APPROVAL_DENIED: A decision attempt was refused by a control, e.g.
            a requester trying to approve their own request.
        APPROVAL_EXPIRED: A request passed its deadline undecided.
    """

    VERSION_REGISTERED = "version_registered"
    STAGE_PROMOTED = "stage_promoted"
    STAGE_DEMOTED = "stage_demoted"
    PRODUCTION_ROLLED_BACK = "production_rolled_back"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_DENIED = "approval_denied"
    APPROVAL_EXPIRED = "approval_expired"


class SubjectKind(StrEnum):
    """What kind of thing an event's ``subject`` identifies.

    Attributes:
        MODEL: ``subject`` is a logical model name.
        APPROVAL_REQUEST: ``subject`` is an approval request id.
    """

    MODEL = "model"
    APPROVAL_REQUEST = "approval_request"


@dataclass(frozen=True)
class GovernanceEvent:
    """A single recorded governance decision or lifecycle change.

    Attributes:
        event_id: Unique identifier for this event.
        timestamp: When the event occurred (UTC).
        actor_id: Identifier of the principal responsible for the event.
        action: What happened.
        subject: The model name or approval request id the event is about.
        subject_kind: How to interpret ``subject``.
        before: State before the change, or None when the event does not
            replace a prior state (a registration, for instance).
        after: State after the change, or None where not applicable.
        outcome: :data:`OUTCOME_RECORDED` for an event that took effect,
            :data:`OUTCOME_DENIED` for a refused attempt.
        details: Additional structured context, e.g. the version string
            behind a model-level event. Stored verbatim -- unlike the
            security log, nothing here is masked, because a redacted
            before/after would misreport what changed.
    """

    event_id: str
    timestamp: datetime
    actor_id: str
    action: GovernanceAction
    subject: str
    subject_kind: SubjectKind
    before: str | None = None
    after: str | None = None
    outcome: str = OUTCOME_RECORDED
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GovernanceRecord:
    """One link in the trail's hash chain: an event plus its integrity fields.

    Attributes:
        sequence: Zero-based position of this record in its trail.
        event: The governance event this record commits to.
        previous_hash: ``entry_hash`` of the preceding record, or
            :data:`~cockpit.security.audit_logging.GENESIS_HASH` for the
            first record.
        entry_hash: SHA-256 over the canonical serialization of the
            record, computed by :func:`compute_governance_hash`.
    """

    sequence: int
    event: GovernanceEvent
    previous_hash: str
    entry_hash: str


def _to_audit_event(event: GovernanceEvent) -> AuditEvent:
    """Project a governance event onto the shape the hash chain digests.

    Deterministic and total, so verification can rebuild the projection
    instead of the trail having to store both representations.

    Args:
        event: The governance event to project.

    Returns:
        An equivalent :class:`~cockpit.security.audit_logging.AuditEvent`.
    """
    return AuditEvent(
        event_id=event.event_id,
        timestamp=event.timestamp,
        actor_id=event.actor_id,
        action=str(event.action),
        # Qualify the subject with its kind so a model named "req-1" can
        # never collide with an approval request of the same id.
        resource=f"{event.subject_kind}:{event.subject}",
        outcome=event.outcome,
        metadata={"before": event.before, "after": event.after, "details": event.details},
    )


def compute_governance_hash(event: GovernanceEvent, sequence: int, previous_hash: str) -> str:
    """Compute the SHA-256 entry hash for a governance record.

    Args:
        event: The event to hash.
        sequence: The record's position in the trail.
        previous_hash: The preceding record's ``entry_hash``.

    Returns:
        Lowercase hex SHA-256 digest of the canonical payload.
    """
    return compute_entry_hash(_to_audit_event(event), sequence, previous_hash)


def verify_trail(records: Sequence[GovernanceRecord]) -> ChainVerification:
    """Verify a governance chain and locate the first broken link.

    Args:
        records: The trail to verify, in order. An empty trail is valid.

    Returns:
        A :class:`~cockpit.security.audit_logging.ChainVerification`
        describing the result and, on failure, where and why it broke.
    """
    projected = [
        AuditRecord(
            sequence=record.sequence,
            event=_to_audit_event(record.event),
            previous_hash=record.previous_hash,
            entry_hash=record.entry_hash,
        )
        for record in records
    ]
    return verify_chain(projected)


@dataclass
class GovernanceTrail:
    """An in-memory, thread-safe, tamper-evident governance history.

    Thread-safe for the same reason the security chain is: a hash chain is
    order-dependent, so two unsynchronized appends would both read the same
    head hash and produce a trail that fails its own verification.

    Lock ordering note: :class:`~cockpit.governance.model_versioning.ModelRegistry`
    and :class:`~cockpit.governance.approval_workflow.ApprovalWorkflow` hold
    their own lock while calling :meth:`record`, so the trail lock is always
    acquired last. Nothing here calls back into them, so the ordering cannot
    invert and there is no deadlock.

    Attributes:
        records: The trail, oldest first.
    """

    records: list[GovernanceRecord] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @property
    def head_hash(self) -> str:
        """Hash of the most recent record, or the genesis hash if empty.

        Returns:
            The current head hash.
        """
        with self._lock:
            return self.records[-1].entry_hash if self.records else GENESIS_HASH

    def record(
        self,
        actor_id: str,
        action: GovernanceAction,
        subject: str,
        *,
        subject_kind: SubjectKind = SubjectKind.MODEL,
        before: str | None = None,
        after: str | None = None,
        outcome: str = OUTCOME_RECORDED,
        details: Mapping[str, Any] | None = None,
        timestamp: datetime | None = None,
        event_id: str | None = None,
    ) -> GovernanceRecord:
        """Append one governance event to the trail.

        Args:
            actor_id: Identifier of the principal responsible.
            action: What happened.
            subject: Model name or approval request id.
            subject_kind: How to interpret ``subject``.
            before: State before the change, if any.
            after: State after the change, if any.
            outcome: :data:`OUTCOME_RECORDED` or :data:`OUTCOME_DENIED`.
            details: Optional structured context. Copied, so a later
                mutation of the caller's mapping cannot invalidate the hash
                and raise a false tamper alarm.
            timestamp: Event time; defaults to now (UTC). Injectable so
                tests do not have to freeze the clock.
            event_id: Explicit event id; defaults to a fresh UUID4 hex.

        Returns:
            The appended :class:`GovernanceRecord`.
        """
        event = GovernanceEvent(
            event_id=event_id or uuid.uuid4().hex,
            timestamp=timestamp or datetime.now(UTC),
            actor_id=actor_id,
            action=action,
            subject=subject,
            subject_kind=subject_kind,
            before=before,
            after=after,
            outcome=outcome,
            details=dict(details or {}),
        )
        # Read the head, build the link, and append under one lock: any gap
        # between reading the head hash and appending lets a second thread
        # fork the chain.
        with self._lock:
            sequence = len(self.records)
            previous_hash = self.records[-1].entry_hash if self.records else GENESIS_HASH
            new_record = GovernanceRecord(
                sequence=sequence,
                event=event,
                previous_hash=previous_hash,
                entry_hash=compute_governance_hash(event, sequence, previous_hash),
            )
            self.records.append(new_record)
        return new_record

    def events(self) -> list[GovernanceEvent]:
        """Return every recorded event, oldest first.

        Returns:
            The events in trail order.
        """
        with self._lock:
            return [record.event for record in self.records]

    def history_for_model(self, model_name: str) -> list[GovernanceEvent]:
        """Return the governance history of one model.

        Args:
            model_name: Logical model name (exact match).

        Returns:
            Matching events in trail order, which is timestamp order.
        """
        return [
            event
            for event in self.events()
            if event.subject_kind is SubjectKind.MODEL and event.subject == model_name
        ]

    def history_for_request(self, request_id: str) -> list[GovernanceEvent]:
        """Return the governance history of one approval request.

        Args:
            request_id: Approval request id (exact match).

        Returns:
            Matching events in trail order.
        """
        return [
            event
            for event in self.events()
            if event.subject_kind is SubjectKind.APPROVAL_REQUEST and event.subject == request_id
        ]

    def history_for_actor(self, actor_id: str) -> list[GovernanceEvent]:
        """Return every event attributed to one actor.

        Args:
            actor_id: The actor to filter on (exact match).

        Returns:
            Matching events in trail order.
        """
        return [event for event in self.events() if event.actor_id == actor_id]

    def history_for_action(self, action: GovernanceAction) -> list[GovernanceEvent]:
        """Return every event of one action type.

        Args:
            action: The action to filter on.

        Returns:
            Matching events in trail order.
        """
        return [event for event in self.events() if event.action is action]

    def verify(self) -> ChainVerification:
        """Verify this trail's hash chain.

        Returns:
            A :class:`~cockpit.security.audit_logging.ChainVerification`
            for the current trail.
        """
        with self._lock:
            snapshot = list(self.records)
        return verify_trail(snapshot)

    def reset(self) -> None:
        """Drop every record, restarting the trail from genesis.

        Intended for tests and for a caller that has already archived the
        history elsewhere. It is not a governance operation: discarding
        history is what the chain exists to make visible.
        """
        with self._lock:
            self.records.clear()


# Shared default trail backing the module-level convenience functions, the
# same way `logging` exposes a root logger. The default registry and default
# workflow are both wired to this instance, so a single query answers "what
# happened to this model" across versioning and approvals.
_default_trail = GovernanceTrail()


def get_default_trail() -> GovernanceTrail:
    """Return the process-wide default :class:`GovernanceTrail`.

    Returns:
        The trail backing the module-level convenience functions. Tests
        should prefer constructing their own :class:`GovernanceTrail` over
        mutating this one.
    """
    return _default_trail


def record_governance_event(
    actor_id: str,
    action: GovernanceAction,
    subject: str,
    *,
    subject_kind: SubjectKind = SubjectKind.MODEL,
    before: str | None = None,
    after: str | None = None,
    outcome: str = OUTCOME_RECORDED,
    details: Mapping[str, Any] | None = None,
) -> GovernanceEvent | None:
    """Append a governance event to the default trail.

    The single flag-gated entry point in this module, mirroring
    :func:`cockpit.security.input_security.validate_input`.

    Args:
        actor_id: Identifier of the principal responsible.
        action: What happened.
        subject: Model name or approval request id.
        subject_kind: How to interpret ``subject``.
        before: State before the change, if any.
        after: State after the change, if any.
        outcome: :data:`OUTCOME_RECORDED` or :data:`OUTCOME_DENIED`.
        details: Optional structured context.

    Returns:
        The recorded :class:`GovernanceEvent`, or None if the governance
        framework is disabled (in which case nothing is recorded).
    """
    if not is_enabled("governance"):
        _logger.debug("Governance framework disabled; skipping governance event.")
        return None
    return _default_trail.record(
        actor_id,
        action,
        subject,
        subject_kind=subject_kind,
        before=before,
        after=after,
        outcome=outcome,
        details=details,
    ).event


def get_governance_history(model_name: str) -> list[GovernanceEvent]:
    """Retrieve the governance history for a specific model.

    Args:
        model_name: Logical model name to retrieve history for.

    Returns:
        :class:`GovernanceEvent` records related to the model, ordered by
        timestamp.
    """
    return _default_trail.history_for_model(model_name)


def get_request_history(request_id: str) -> list[GovernanceEvent]:
    """Retrieve the governance history for a specific approval request.

    Args:
        request_id: Approval request id to retrieve history for.

    Returns:
        :class:`GovernanceEvent` records related to the request, ordered by
        timestamp.
    """
    return _default_trail.history_for_request(request_id)


def get_actor_history(actor_id: str) -> list[GovernanceEvent]:
    """Retrieve every governance event attributed to one actor.

    Args:
        actor_id: The actor to retrieve history for.

    Returns:
        :class:`GovernanceEvent` records for the actor, ordered by
        timestamp.
    """
    return _default_trail.history_for_actor(actor_id)


def verify_trail_integrity() -> bool:
    """Verify that the default governance trail has not been tampered with.

    Returns:
        True if the hash chain is intact. Use :meth:`GovernanceTrail.verify`
        (or :func:`verify_trail`) when you need to know *where* it broke --
        a bare False is not actionable.
    """
    result = _default_trail.verify()
    if not result.is_valid:
        _logger.error("Governance trail integrity check failed: %s", result.reason)
    return result.is_valid


__all__ = [
    "OUTCOME_DENIED",
    "OUTCOME_RECORDED",
    "GovernanceAction",
    "GovernanceEvent",
    "GovernanceRecord",
    "GovernanceTrail",
    "SubjectKind",
    "compute_governance_hash",
    "get_actor_history",
    "get_default_trail",
    "get_governance_history",
    "get_request_history",
    "record_governance_event",
    "verify_trail",
    "verify_trail_integrity",
]
