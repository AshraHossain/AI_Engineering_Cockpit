"""Human-in-the-loop approval workflow for sensitive changes.

Three controls do the real work here, and each exists because its absence
is a known way approval processes get defeated:

* **No self-approval.** The requester cannot approve their own request. An
  approval gate a single person can satisfy alone is theatre, and this is
  the control most often missing in practice.
* **One approver, one vote.** The same principal approving twice does not
  satisfy a 2-of-M threshold; deduplication is by principal, not by call.
* **Rejection is terminal.** A rejected request cannot be walked back to
  approved by collecting more signatures afterwards.

Gated by ``feature_flags.is_enabled("governance")`` at the mutating entry
points only. Reads stay available with the flag off.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from cockpit.config.feature_flags import is_enabled
from cockpit.governance.audit_trail import (
    OUTCOME_DENIED,
    GovernanceAction,
    SubjectKind,
    record_governance_event,
)
from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)

DEFAULT_REQUIRED_APPROVALS = 1


class ApprovalStatus(StrEnum):
    """Status of an approval request.

    Attributes:
        PENDING: Awaiting a reviewer decision.
        APPROVED: Reviewer approved the request.
        REJECTED: Reviewer rejected the request.
        EXPIRED: The request passed its deadline undecided.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


TERMINAL_STATUSES: frozenset[ApprovalStatus] = frozenset(
    {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED}
)


class ApprovalError(RuntimeError):
    """Base class for approval workflow errors."""


class UnknownRequestError(ApprovalError, KeyError):
    """Raised when a referenced request id does not exist."""


class SelfApprovalError(ApprovalError):
    """Raised when a requester attempts to decide their own request.

    Attributes:
        request_id: The request that was targeted.
        principal_id: The principal that attempted the decision.
    """

    def __init__(self, request_id: str, principal_id: str) -> None:
        """Initialize the error.

        Args:
            request_id: The request that was targeted.
            principal_id: The principal that attempted the decision.
        """
        super().__init__(
            f"Principal {principal_id!r} opened request {request_id!r} and cannot decide it. "
            "Segregation of duties requires a different approver."
        )
        self.request_id = request_id
        self.principal_id = principal_id


class RequestNotPendingError(ApprovalError):
    """Raised when deciding a request that has already reached a terminal state.

    Attributes:
        request_id: The request that was targeted.
        status: The status it was already in.
    """

    def __init__(self, request_id: str, status: ApprovalStatus) -> None:
        """Initialize the error.

        Args:
            request_id: The request that was targeted.
            status: The status it was already in.
        """
        super().__init__(
            f"Request {request_id!r} is {status} and can no longer be decided. "
            "Terminal decisions are final."
        )
        self.request_id = request_id
        self.status = status


@dataclass(frozen=True)
class ApprovalDecision:
    """One reviewer's recorded decision.

    Attributes:
        principal_id: The deciding principal.
        approved: True for an approval, False for a rejection.
        decided_at: When the decision was recorded (UTC).
        comment: Optional free-text rationale.
    """

    principal_id: str
    approved: bool
    decided_at: datetime
    comment: str | None = None


@dataclass(frozen=True)
class ApprovalRequest:
    """A single request awaiting human approval.

    Attributes:
        request_id: Unique identifier for the request.
        requested_by: Identifier of the principal that opened the request.
        description: Human-readable description of the change being
            requested (e.g. "promote gemini-flash v2026-08-01 to
            production").
        status: Current status of the request.
        created_at: When the request was opened (UTC).
        decided_at: When the request reached a terminal status, if it has.
        required_approvals: How many distinct approvers are needed.
        decisions: Recorded decisions, in the order they were made.
        expires_at: Deadline after which the request can no longer be
            approved, or None for no deadline.
    """

    request_id: str
    requested_by: str
    description: str
    status: ApprovalStatus
    created_at: datetime
    decided_at: datetime | None = None
    required_approvals: int = DEFAULT_REQUIRED_APPROVALS
    decisions: tuple[ApprovalDecision, ...] = ()
    expires_at: datetime | None = None

    @property
    def approvers(self) -> frozenset[str]:
        """Distinct principals who have approved.

        Returns:
            The set of approving principal ids. Distinct, so one principal
            deciding repeatedly counts once.
        """
        return frozenset(d.principal_id for d in self.decisions if d.approved)

    @property
    def approval_count(self) -> int:
        """Number of distinct approvals recorded.

        Returns:
            The count of unique approving principals.
        """
        return len(self.approvers)

    @property
    def is_satisfied(self) -> bool:
        """Whether the approval threshold has been met.

        Returns:
            True if distinct approvals reach ``required_approvals``.
        """
        return self.approval_count >= self.required_approvals

    def is_expired(self, now: datetime | None = None) -> bool:
        """Whether the request is past its deadline.

        Args:
            now: Reference time; defaults to now (UTC). Injectable so tests
                need not manipulate the clock.

        Returns:
            True if a deadline is set and has passed.
        """
        if self.expires_at is None:
            return False
        return (now or datetime.now(UTC)) >= self.expires_at


@dataclass
class ApprovalWorkflow:
    """An in-memory store of approval requests.

    Thread-safe: decisions arriving concurrently must not race the
    threshold check, or two simultaneous approvals could both observe
    "not yet satisfied" and neither flip the status.
    """

    _requests: dict[str, ApprovalRequest] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def submit(
        self,
        requested_by: str,
        description: str,
        *,
        required_approvals: int = DEFAULT_REQUIRED_APPROVALS,
        request_id: str | None = None,
        created_at: datetime | None = None,
        expires_in: timedelta | None = None,
    ) -> ApprovalRequest:
        """Open a new request in the ``PENDING`` state.

        Args:
            requested_by: Principal opening the request.
            description: Human-readable description of the change.
            required_approvals: Distinct approvals needed. Must be >= 1.
            request_id: Explicit id; generated when omitted.
            created_at: Creation time; defaults to now (UTC).
            expires_in: Optional lifetime after which the request expires.

        Returns:
            The newly created :class:`ApprovalRequest`.

        Raises:
            ValueError: If ``required_approvals`` is less than 1.
        """
        if required_approvals < 1:
            raise ValueError("required_approvals must be at least 1.")

        created = created_at or datetime.now(UTC)
        request = ApprovalRequest(
            request_id=request_id or f"req-{uuid.uuid4().hex[:12]}",
            requested_by=requested_by,
            description=description,
            status=ApprovalStatus.PENDING,
            created_at=created,
            required_approvals=required_approvals,
            expires_at=created + expires_in if expires_in is not None else None,
        )
        with self._lock:
            self._requests[request.request_id] = request

        record_governance_event(
            requested_by,
            GovernanceAction.APPROVAL_REQUESTED,
            request.request_id,
            subject_kind=SubjectKind.APPROVAL_REQUEST,
            after=str(ApprovalStatus.PENDING),
            details={"description": description, "required_approvals": required_approvals},
        )
        return request

    def get(self, request_id: str) -> ApprovalRequest:
        """Look up a request by id.

        Args:
            request_id: Identifier of the request.

        Returns:
            The :class:`ApprovalRequest`.

        Raises:
            UnknownRequestError: If no such request exists.
        """
        with self._lock:
            try:
                return self._requests[request_id]
            except KeyError as exc:
                raise UnknownRequestError(f"No approval request with id {request_id!r}.") from exc

    def decide(
        self,
        request_id: str,
        approved: bool,
        decided_by: str,
        *,
        comment: str | None = None,
        now: datetime | None = None,
    ) -> ApprovalRequest:
        """Record a reviewer's decision.

        Args:
            request_id: Identifier of the request being decided.
            approved: True to approve, False to reject.
            decided_by: The reviewing principal.
            comment: Optional rationale.
            now: Decision time; defaults to now (UTC).

        Returns:
            The updated :class:`ApprovalRequest`.

        Raises:
            UnknownRequestError: If no such request exists.
            SelfApprovalError: If the requester tries to decide their own request.
            RequestNotPendingError: If the request is already terminal or expired.
        """
        if not is_enabled("governance"):
            _logger.debug("Governance framework disabled; skipping approval decision.")
            return self.get(request_id)

        moment = now or datetime.now(UTC)

        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                raise UnknownRequestError(f"No approval request with id {request_id!r}.")

            # Self-approval is checked before anything else so the denial is
            # recorded even for a request that is also expired or decided --
            # an attempt to self-approve is worth seeing either way.
            if decided_by == request.requested_by:
                self._record_denial(request, decided_by, "self_approval")
                raise SelfApprovalError(request_id, decided_by)

            if request.status in TERMINAL_STATUSES:
                self._record_denial(request, decided_by, f"already_{request.status}")
                raise RequestNotPendingError(request_id, request.status)

            if request.is_expired(moment):
                expired = replace(
                    request, status=ApprovalStatus.EXPIRED, decided_at=request.expires_at
                )
                self._requests[request_id] = expired
                self._record_denial(expired, decided_by, "expired")
                raise RequestNotPendingError(request_id, ApprovalStatus.EXPIRED)

            decision = ApprovalDecision(
                principal_id=decided_by,
                approved=approved,
                decided_at=moment,
                comment=comment,
            )
            updated = replace(request, decisions=(*request.decisions, decision))

            if not approved:
                # One rejection is decisive. Requiring N rejections to match N
                # approvals would let a veto be outvoted, which is not what a
                # rejection means.
                updated = replace(updated, status=ApprovalStatus.REJECTED, decided_at=moment)
            elif updated.is_satisfied:
                updated = replace(updated, status=ApprovalStatus.APPROVED, decided_at=moment)

            self._requests[request_id] = updated

        action = (
            GovernanceAction.APPROVAL_GRANTED if approved else GovernanceAction.APPROVAL_REJECTED
        )
        record_governance_event(
            decided_by,
            action,
            request_id,
            subject_kind=SubjectKind.APPROVAL_REQUEST,
            before=str(request.status),
            after=str(updated.status),
            details={
                "approvals": updated.approval_count,
                "required": updated.required_approvals,
            },
        )
        return updated

    def _record_denial(self, request: ApprovalRequest, principal_id: str, reason: str) -> None:
        """Record a refused decision attempt on the governance trail.

        Called while holding the lock; emits outside-visible history so a
        blocked self-approval is not invisible.

        Args:
            request: The targeted request.
            principal_id: The principal whose attempt was refused.
            reason: Short machine-readable reason code.
        """
        record_governance_event(
            principal_id,
            GovernanceAction.APPROVAL_DENIED,
            request.request_id,
            subject_kind=SubjectKind.APPROVAL_REQUEST,
            before=str(request.status),
            after=str(request.status),
            outcome=OUTCOME_DENIED,
            details={"reason": reason},
        )

    def expire_overdue(self, now: datetime | None = None) -> list[ApprovalRequest]:
        """Move every past-deadline pending request to ``EXPIRED``.

        Args:
            now: Reference time; defaults to now (UTC).

        Returns:
            The requests that were expired by this call.
        """
        moment = now or datetime.now(UTC)
        expired: list[ApprovalRequest] = []
        with self._lock:
            for request_id, request in list(self._requests.items()):
                if request.status is ApprovalStatus.PENDING and request.is_expired(moment):
                    updated = replace(
                        request, status=ApprovalStatus.EXPIRED, decided_at=request.expires_at
                    )
                    self._requests[request_id] = updated
                    expired.append(updated)

        for request in expired:
            record_governance_event(
                "system",
                GovernanceAction.APPROVAL_EXPIRED,
                request.request_id,
                subject_kind=SubjectKind.APPROVAL_REQUEST,
                before=str(ApprovalStatus.PENDING),
                after=str(ApprovalStatus.EXPIRED),
            )
        return expired

    def list_pending(self, now: datetime | None = None) -> list[ApprovalRequest]:
        """List requests still awaiting a decision, oldest first.

        Args:
            now: Reference time used to exclude past-deadline requests;
                defaults to now (UTC).

        Returns:
            Pending :class:`ApprovalRequest` instances.
        """
        moment = now or datetime.now(UTC)
        with self._lock:
            pending = [
                r
                for r in self._requests.values()
                if r.status is ApprovalStatus.PENDING and not r.is_expired(moment)
            ]
        return sorted(pending, key=lambda r: (r.created_at, r.request_id))

    def reset(self) -> None:
        """Drop all stored requests."""
        with self._lock:
            self._requests.clear()


_default_workflow = ApprovalWorkflow()


def get_default_workflow() -> ApprovalWorkflow:
    """Return the process-wide default workflow.

    Returns:
        The workflow backing the module-level convenience functions.
    """
    return _default_workflow


def submit_approval_request(
    requested_by: str,
    description: str,
    *,
    required_approvals: int = DEFAULT_REQUIRED_APPROVALS,
) -> ApprovalRequest:
    """Open a new approval request in the ``PENDING`` state.

    Args:
        requested_by: Identifier of the principal opening the request.
        description: Human-readable description of the requested change.
        required_approvals: Distinct approvals needed.

    Returns:
        The newly created :class:`ApprovalRequest`.

    Raises:
        ValueError: If ``required_approvals`` is less than 1.
    """
    return _default_workflow.submit(
        requested_by, description, required_approvals=required_approvals
    )


def decide_approval_request(request_id: str, approved: bool, decided_by: str) -> ApprovalRequest:
    """Record a reviewer's decision on a pending approval request.

    Args:
        request_id: Identifier of the request being decided.
        approved: True to approve, False to reject.
        decided_by: Identifier of the reviewing principal.

    Returns:
        The updated :class:`ApprovalRequest`.

    Raises:
        UnknownRequestError: If no such request exists.
        SelfApprovalError: If the requester tries to decide their own request.
        RequestNotPendingError: If the request is already terminal.
    """
    return _default_workflow.decide(request_id, approved, decided_by)


def list_pending_requests() -> list[ApprovalRequest]:
    """List all approval requests currently awaiting a decision.

    Returns:
        Pending :class:`ApprovalRequest` instances, oldest first.
    """
    return _default_workflow.list_pending()
