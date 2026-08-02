"""Runtime threat detection and anomaly scoring (Tier 3 — not yet implemented).

Planned scope: behavioral anomaly detection (unusual request volume,
patterns of repeated injection attempts, credential-stuffing-like access
patterns) layered on top of the Tier 1 ``input_security`` /
``output_security`` heuristics. Gated by
``feature_flags.is_enabled("security")`` at the framework level, but every
function here is a typed skeleton pending Tier 3 implementation. See
``docs/ARCHITECTURE.md`` for the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ThreatLevel(StrEnum):
    """Severity levels for a detected threat.

    Attributes:
        LOW: Informational; no action required.
        MEDIUM: Worth reviewing; consider rate limiting.
        HIGH: Likely malicious; consider blocking.
        CRITICAL: Active attack; block and alert immediately.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ThreatSignal:
    """A single detected threat indicator.

    Attributes:
        signal_id: Unique identifier for this signal.
        detected_at: When the signal was detected (UTC).
        source_id: Identifier of the actor/session that triggered it.
        level: Assessed severity.
        description: Human-readable explanation of what was detected.
    """

    signal_id: str
    detected_at: datetime
    source_id: str
    level: ThreatLevel
    description: str


def analyze_request_pattern(source_id: str, window_seconds: int = 60) -> list[ThreatSignal]:
    """Analyze recent request history for a source for anomalous patterns.

    Args:
        source_id: Identifier of the actor/session to analyze.
        window_seconds: Size of the trailing time window to examine.

    Returns:
        Threat signals detected within the window, if any.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def score_threat_level(signals: list[ThreatSignal]) -> ThreatLevel:
    """Aggregate multiple threat signals into a single overall severity.

    Args:
        signals: Threat signals to aggregate.

    Returns:
        The overall :class:`ThreatLevel` for the combined signals.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def should_block(source_id: str) -> bool:
    """Decide whether traffic from a source should currently be blocked.

    Args:
        source_id: Identifier of the actor/session to evaluate.

    Returns:
        True if the source's current threat level warrants blocking.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")
