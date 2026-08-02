"""Human-in-the-loop approval workflow for sensitive changes (Tier 3 — skeleton).

Disabled by default; gated by ``feature_flags.is_enabled("governance")``.
Every function is a typed skeleton pending Tier 3 implementation. See
``docs/ARCHITECTURE.md`` for the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ApprovalStatus(StrEnum):
    """Status of an approval request.

    Attributes:
        PENDING: Awaiting a reviewer decision.
        APPROVED: Reviewer approved the request.
        REJECTED: Reviewer rejected the request.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


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
        decided_at: When the request was approved/rejected, if applicable.
    """

    request_id: str
    requested_by: str
    description: str
    status: ApprovalStatus
    created_at: datetime
    decided_at: datetime | None = None


def submit_approval_request(requested_by: str, description: str) -> ApprovalRequest:
    """Open a new approval request in the ``PENDING`` state.

    Args:
        requested_by: Identifier of the principal opening the request.
        description: Human-readable description of the requested change.

    Returns:
        The newly created :class:`ApprovalRequest`.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def decide_approval_request(request_id: str, approved: bool, decided_by: str) -> ApprovalRequest:
    """Record a reviewer's decision on a pending approval request.

    Args:
        request_id: Identifier of the request being decided.
        approved: True to approve, False to reject.
        decided_by: Identifier of the reviewing principal.

    Returns:
        The updated :class:`ApprovalRequest`.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def list_pending_requests() -> list[ApprovalRequest]:
    """List all approval requests currently awaiting a decision.

    Returns:
        Pending :class:`ApprovalRequest` instances, oldest first.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")
