"""Response quality metrics: relevance, coherence, factuality (Tier 2 — skeleton).

Disabled by default; gated by ``feature_flags.is_enabled("evaluation")``.
Every function is a typed skeleton pending Tier 2 implementation. See
``docs/ARCHITECTURE.md`` for the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QualityScore:
    """A single quality metric result.

    Attributes:
        metric_name: Name of the metric (e.g. "relevance", "coherence").
        score: Normalized score in [0.0, 1.0].
        explanation: Human-readable rationale for the score.
    """

    metric_name: str
    score: float
    explanation: str


def score_relevance(prompt: str, response: str) -> QualityScore:
    """Score how relevant a response is to its prompt.

    Args:
        prompt: The input prompt that elicited the response.
        response: The model-generated response to evaluate.

    Returns:
        A :class:`QualityScore` for the "relevance" metric.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")


def score_coherence(response: str) -> QualityScore:
    """Score the internal logical/linguistic coherence of a response.

    Args:
        response: The model-generated response to evaluate.

    Returns:
        A :class:`QualityScore` for the "coherence" metric.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")


def score_factuality(response: str, reference: str) -> QualityScore:
    """Score how factually consistent a response is with a reference source.

    Args:
        response: The model-generated response to evaluate.
        reference: Ground-truth or retrieved source text to check against.

    Returns:
        A :class:`QualityScore` for the "factuality" metric.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")
