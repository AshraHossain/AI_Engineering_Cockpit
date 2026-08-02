"""Model version registry and promotion tracking (Tier 3 — skeleton).

Disabled by default; gated by ``feature_flags.is_enabled("governance")``.
Every function is a typed skeleton pending Tier 3 implementation. See
``docs/ARCHITECTURE.md`` for the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ModelStage(StrEnum):
    """Lifecycle stage of a registered model version.

    Attributes:
        DEVELOPMENT: Under active development, not for production traffic.
        STAGING: Candidate for promotion, under evaluation.
        PRODUCTION: Actively serving production traffic.
        DEPRECATED: No longer recommended for new use.
    """

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    DEPRECATED = "deprecated"


@dataclass(frozen=True)
class ModelVersion:
    """A single registered model version.

    Attributes:
        model_name: Logical model name (e.g. "gemini-flash").
        version: Version identifier (e.g. "2026-08-01").
        stage: Current lifecycle stage.
        registered_at: When this version was registered (UTC).
    """

    model_name: str
    version: str
    stage: ModelStage
    registered_at: datetime


def register_model_version(model_name: str, version: str) -> ModelVersion:
    """Register a new model version in the ``DEVELOPMENT`` stage.

    Args:
        model_name: Logical model name.
        version: Version identifier for this registration.

    Returns:
        The newly registered :class:`ModelVersion`.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def promote_model_version(model_name: str, version: str, target_stage: ModelStage) -> ModelVersion:
    """Promote a registered model version to a new lifecycle stage.

    Args:
        model_name: Logical model name.
        version: Version identifier to promote.
        target_stage: The stage to promote the version into.

    Returns:
        The updated :class:`ModelVersion`.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def get_production_version(model_name: str) -> ModelVersion:
    """Look up the version currently in the ``PRODUCTION`` stage.

    Args:
        model_name: Logical model name to look up.

    Returns:
        The current production :class:`ModelVersion`.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")
