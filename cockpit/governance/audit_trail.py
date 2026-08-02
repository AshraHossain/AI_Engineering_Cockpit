"""Governance-level audit trail for versioning and approval decisions (Tier 3 — skeleton).

Distinct from ``cockpit.security.audit_logging`` (which covers
security-relevant access events): this module tracks the governance
history of model promotions and approval decisions specifically. Disabled
by default; gated by ``feature_flags.is_enabled("governance")``. Every
function is a typed skeleton pending Tier 3 implementation. See
``docs/ARCHITECTURE.md`` for the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class GovernanceEvent:
    """A single recorded governance decision.

    Attributes:
        event_id: Unique identifier for this event.
        timestamp: When the event occurred (UTC).
        actor_id: Identifier of the principal responsible for the event.
        event_type: Kind of event (e.g. "model_promotion",
            "approval_decision").
        details: Human-readable description of what changed.
    """

    event_id: str
    timestamp: datetime
    actor_id: str
    event_type: str
    details: str


def record_governance_event(actor_id: str, event_type: str, details: str) -> GovernanceEvent:
    """Append a new governance event to the audit trail.

    Args:
        actor_id: Identifier of the principal responsible for the event.
        event_type: Kind of event being recorded.
        details: Human-readable description of what changed.

    Returns:
        The recorded :class:`GovernanceEvent`.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def get_governance_history(model_name: str) -> list[GovernanceEvent]:
    """Retrieve the governance history for a specific model.

    Args:
        model_name: Logical model name to retrieve history for.

    Returns:
        :class:`GovernanceEvent` records related to the model, ordered by
        timestamp.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")
