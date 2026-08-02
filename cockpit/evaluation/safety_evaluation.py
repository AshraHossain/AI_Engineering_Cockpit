"""Safety evaluation: harmful content and policy-violation scoring (Tier 2 — skeleton).

Disabled by default; gated by ``feature_flags.is_enabled("evaluation")``.
Every function is a typed skeleton pending Tier 2 implementation. See
``docs/ARCHITECTURE.md`` for the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SafetyCategory(StrEnum):
    """Categories of potential safety violations.

    Attributes:
        HARASSMENT: Harassing or abusive content.
        SELF_HARM: Content related to self-harm.
        VIOLENCE: Violent or graphic content.
        HATE_SPEECH: Discriminatory or hateful content.
        DANGEROUS_INSTRUCTIONS: Instructions enabling real-world harm.
    """

    HARASSMENT = "harassment"
    SELF_HARM = "self_harm"
    VIOLENCE = "violence"
    HATE_SPEECH = "hate_speech"
    DANGEROUS_INSTRUCTIONS = "dangerous_instructions"


@dataclass(frozen=True)
class SafetyEvaluation:
    """Result of a safety evaluation over a single response.

    Attributes:
        is_safe: True if no category exceeded its violation threshold.
        category_scores: Per-category violation likelihood in [0.0, 1.0].
        flagged_categories: Categories that exceeded their threshold.
    """

    is_safe: bool
    category_scores: dict[SafetyCategory, float]
    flagged_categories: list[SafetyCategory]


def evaluate_safety(response: str) -> SafetyEvaluation:
    """Evaluate a response for safety-policy violations.

    Args:
        response: The model-generated response to evaluate.

    Returns:
        A :class:`SafetyEvaluation` summarizing per-category risk.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")


def get_violation_threshold(category: SafetyCategory) -> float:
    """Get the configured violation threshold for a safety category.

    Args:
        category: The safety category to look up.

    Returns:
        The threshold score above which a response is flagged, in
        [0.0, 1.0].

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")
