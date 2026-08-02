"""Tamper-evident audit logging (Tier 3 — not yet implemented).

Planned scope: structured, append-only audit event records covering
authentication, authorization decisions, and data access, suitable for
compliance review. Gated by ``feature_flags.is_enabled("security")`` at the
framework level, but every function here is a typed skeleton pending Tier 3
implementation. See ``docs/ARCHITECTURE.md`` for the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class AuditEvent:
    """A single immutable audit log entry.

    Attributes:
        event_id: Unique identifier for this event.
        timestamp: When the event occurred (UTC).
        actor_id: Identifier of the principal that triggered the event.
        action: The action performed (e.g. "read", "delete", "login").
        resource: Identifier of the affected resource.
        outcome: Result of the action (e.g. "success", "denied", "error").
        metadata: Additional structured context about the event.
    """

    event_id: str
    timestamp: datetime
    actor_id: str
    action: str
    resource: str
    outcome: str
    metadata: dict[str, Any]


def record_event(
    actor_id: str,
    action: str,
    resource: str,
    outcome: str,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append a new audit event to the tamper-evident log.

    Args:
        actor_id: Identifier of the principal performing the action.
        action: The action performed.
        resource: Identifier of the affected resource.
        outcome: Result of the action.
        metadata: Optional additional structured context.

    Returns:
        The recorded :class:`AuditEvent`, including its assigned
        ``event_id`` and ``timestamp``.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def query_events(
    actor_id: str | None = None,
    resource: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> list[AuditEvent]:
    """Query recorded audit events with optional filters.

    Args:
        actor_id: Restrict results to this actor, if provided.
        resource: Restrict results to this resource, if provided.
        start_time: Only include events at or after this time, if provided.
        end_time: Only include events at or before this time, if provided.

    Returns:
        Matching :class:`AuditEvent` records, ordered by timestamp.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def verify_log_integrity() -> bool:
    """Verify that the audit log has not been tampered with.

    Returns:
        True if the log's integrity check (e.g. hash chain) passes.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")
