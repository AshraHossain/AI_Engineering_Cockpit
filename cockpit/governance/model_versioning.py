"""Model version registry and lifecycle promotion.

Answers the question an incident review always asks first: which model
version was actually serving production at the time, and who put it there.

Two rules carry the weight:

* **Promotions follow an explicit transition map.** Development straight to
  production is rejected. The legal moves are data (:data:`LEGAL_TRANSITIONS`),
  not a chain of conditionals, so the policy can be read and changed without
  re-deriving it from control flow.
* **At most one version per model is in production.** Promoting a new one
  demotes the incumbent, and both happen under a single lock so no reader
  ever observes two production versions or none.

Gated by ``feature_flags.is_enabled("governance")`` at the mutating entry
points only. Lookups stay available with the flag off, on the same reasoning
as the audit modules: the flag should not remove the tools you need while
investigating.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from cockpit.config.feature_flags import is_enabled
from cockpit.governance.audit_trail import (
    GovernanceAction,
    SubjectKind,
    record_governance_event,
)
from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)


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


# Which stage moves are permitted. Anything absent is denied, so adding a
# stage cannot accidentally open a path -- the omission fails closed.
#
# DEVELOPMENT -> PRODUCTION is deliberately missing: shipping straight from a
# developer's branch to live traffic is the transition this map exists to
# prevent. Reaching production requires passing through STAGING.
LEGAL_TRANSITIONS: dict[ModelStage, frozenset[ModelStage]] = {
    ModelStage.DEVELOPMENT: frozenset({ModelStage.STAGING, ModelStage.DEPRECATED}),
    ModelStage.STAGING: frozenset(
        {ModelStage.PRODUCTION, ModelStage.DEVELOPMENT, ModelStage.DEPRECATED}
    ),
    ModelStage.PRODUCTION: frozenset({ModelStage.STAGING, ModelStage.DEPRECATED}),
    ModelStage.DEPRECATED: frozenset({ModelStage.STAGING}),
}

INITIAL_STAGE = ModelStage.DEVELOPMENT


class ModelVersionError(RuntimeError):
    """Base class for registry errors."""


class DuplicateVersionError(ModelVersionError):
    """Raised when registering a (model, version) pair that already exists.

    Rejected rather than silently overwritten: a re-registration usually
    means two different artifacts are competing for one identifier, and
    quietly keeping the last writer makes the registry a liar about what
    shipped.
    """


class UnknownVersionError(ModelVersionError, KeyError):
    """Raised when a referenced (model, version) pair is not registered."""


class IllegalTransitionError(ModelVersionError):
    """Raised when a promotion is not permitted by :data:`LEGAL_TRANSITIONS`.

    Attributes:
        from_stage: The stage the version is currently in.
        to_stage: The stage that was requested.
    """

    def __init__(self, from_stage: ModelStage, to_stage: ModelStage) -> None:
        """Initialize the error.

        Args:
            from_stage: The stage the version is currently in.
            to_stage: The stage that was requested.
        """
        allowed = sorted(str(stage) for stage in LEGAL_TRANSITIONS.get(from_stage, frozenset()))
        super().__init__(
            f"Illegal stage transition {from_stage} -> {to_stage}. "
            f"Allowed from {from_stage}: {', '.join(allowed) or 'nothing'}."
        )
        self.from_stage = from_stage
        self.to_stage = to_stage


class NoProductionVersionError(ModelVersionError, KeyError):
    """Raised when a model has no version currently in production."""


class RollbackError(ModelVersionError):
    """Raised when there is no previous production version to roll back to."""


@dataclass(frozen=True)
class ModelVersion:
    """A single registered model version.

    Attributes:
        model_name: Logical model name (e.g. "gemini-flash").
        version: Version identifier (e.g. "2026-08-01").
        stage: Current lifecycle stage.
        registered_at: When this version was registered (UTC).
        provider_model_id: The provider's model id this version wraps, e.g.
            ``"gemini-2.5-flash"``. Kept distinct from ``version`` because a
            logical version can be re-pointed at a different provider model.
        config_fingerprint: Hash or digest of the configuration that defines
            this version, for detecting drift between registry and reality.
        metadata: Free-form structured context.
    """

    model_name: str
    version: str
    stage: ModelStage
    registered_at: datetime
    provider_model_id: str | None = None
    config_fingerprint: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        """Return the ``(model_name, version)`` identity of this version.

        Returns:
            The pair uniquely identifying this version in a registry.
        """
        return (self.model_name, self.version)


def is_legal_transition(from_stage: ModelStage, to_stage: ModelStage) -> bool:
    """Report whether a stage transition is permitted.

    Pure lookup, never flag-gated, so callers can check before attempting.

    Args:
        from_stage: Current stage.
        to_stage: Requested stage.

    Returns:
        True if the move is allowed by :data:`LEGAL_TRANSITIONS`.
    """
    return to_stage in LEGAL_TRANSITIONS.get(from_stage, frozenset())


@dataclass
class ModelRegistry:
    """A registry of model versions and their lifecycle stages.

    Thread-safe. Every mutation takes the lock for its whole duration, which
    is what makes "promote new, demote incumbent" a single atomic step
    rather than a window in which production is ambiguous.
    """

    _versions: dict[tuple[str, str], ModelVersion] = field(default_factory=dict)
    # Previous production version per model, so a rollback has somewhere to go.
    _previous_production: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def register(
        self,
        model_name: str,
        version: str,
        *,
        provider_model_id: str | None = None,
        config_fingerprint: str | None = None,
        metadata: dict[str, Any] | None = None,
        actor_id: str = "system",
        registered_at: datetime | None = None,
    ) -> ModelVersion:
        """Register a new version in :data:`INITIAL_STAGE`.

        Args:
            model_name: Logical model name.
            version: Version identifier.
            provider_model_id: Provider model id this version wraps.
            config_fingerprint: Digest of the defining configuration.
            metadata: Optional structured context.
            actor_id: Principal responsible, recorded on the governance trail.
            registered_at: Registration time; defaults to now (UTC).
                Injectable so tests need not freeze the clock.

        Returns:
            The newly registered :class:`ModelVersion`.

        Raises:
            DuplicateVersionError: If this (model, version) is already registered.
        """
        entry = ModelVersion(
            model_name=model_name,
            version=version,
            stage=INITIAL_STAGE,
            registered_at=registered_at or datetime.now(UTC),
            provider_model_id=provider_model_id,
            config_fingerprint=config_fingerprint,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            if entry.key in self._versions:
                raise DuplicateVersionError(
                    f"Version {version!r} of model {model_name!r} is already registered."
                )
            self._versions[entry.key] = entry

        record_governance_event(
            actor_id,
            GovernanceAction.VERSION_REGISTERED,
            model_name,
            subject_kind=SubjectKind.MODEL,
            after=str(INITIAL_STAGE),
            details={"version": version},
        )
        return entry

    def get(self, model_name: str, version: str) -> ModelVersion:
        """Look up a specific registered version.

        Args:
            model_name: Logical model name.
            version: Version identifier.

        Returns:
            The registered :class:`ModelVersion`.

        Raises:
            UnknownVersionError: If the pair is not registered.
        """
        with self._lock:
            try:
                return self._versions[(model_name, version)]
            except KeyError as exc:
                raise UnknownVersionError(
                    f"Version {version!r} of model {model_name!r} is not registered."
                ) from exc

    def list_versions(self, model_name: str) -> list[ModelVersion]:
        """List every registered version of a model, oldest first.

        Args:
            model_name: Logical model name.

        Returns:
            Registered versions sorted by registration time.
        """
        with self._lock:
            matches = [v for v in self._versions.values() if v.model_name == model_name]
        return sorted(matches, key=lambda v: (v.registered_at, v.version))

    def versions_at_stage(self, model_name: str, stage: ModelStage) -> list[ModelVersion]:
        """List a model's versions currently sitting at a given stage.

        Args:
            model_name: Logical model name.
            stage: Stage to filter on.

        Returns:
            Matching versions, oldest first.
        """
        return [v for v in self.list_versions(model_name) if v.stage is stage]

    def get_production(self, model_name: str) -> ModelVersion:
        """Return the version currently serving production.

        Args:
            model_name: Logical model name.

        Returns:
            The production :class:`ModelVersion`.

        Raises:
            NoProductionVersionError: If no version is in production.
        """
        current = self.versions_at_stage(model_name, ModelStage.PRODUCTION)
        if not current:
            raise NoProductionVersionError(f"Model {model_name!r} has no production version.")
        return current[0]

    def promote(
        self,
        model_name: str,
        version: str,
        target_stage: ModelStage,
        *,
        actor_id: str = "system",
    ) -> ModelVersion:
        """Move a version to a new lifecycle stage.

        Promoting into production demotes the incumbent to
        :attr:`ModelStage.STAGING` in the same locked section, so the
        one-production-version invariant never lapses even momentarily.

        Args:
            model_name: Logical model name.
            version: Version identifier to move.
            target_stage: Stage to move into.
            actor_id: Principal responsible, recorded on the governance trail.

        Returns:
            The updated :class:`ModelVersion`.

        Raises:
            UnknownVersionError: If the pair is not registered.
            IllegalTransitionError: If the move is not in :data:`LEGAL_TRANSITIONS`.
        """
        if not is_enabled("governance"):
            _logger.debug("Governance framework disabled; skipping promotion.")
            return self.get(model_name, version)

        demoted: ModelVersion | None = None
        with self._lock:
            key = (model_name, version)
            existing = self._versions.get(key)
            if existing is None:
                raise UnknownVersionError(
                    f"Version {version!r} of model {model_name!r} is not registered."
                )
            if not is_legal_transition(existing.stage, target_stage):
                raise IllegalTransitionError(existing.stage, target_stage)

            previous_stage = existing.stage

            if target_stage is ModelStage.PRODUCTION:
                for other_key, other in list(self._versions.items()):
                    if (
                        other.model_name == model_name
                        and other.stage is ModelStage.PRODUCTION
                        and other_key != key
                    ):
                        demoted = replace(other, stage=ModelStage.STAGING)
                        self._versions[other_key] = demoted
                        self._previous_production[model_name] = other.version

            updated = replace(existing, stage=target_stage)
            self._versions[key] = updated

        if demoted is not None:
            record_governance_event(
                actor_id,
                GovernanceAction.STAGE_DEMOTED,
                model_name,
                subject_kind=SubjectKind.MODEL,
                before=str(ModelStage.PRODUCTION),
                after=str(ModelStage.STAGING),
                details={"version": demoted.version, "reason": "superseded_in_production"},
            )

        action = (
            GovernanceAction.STAGE_DEMOTED
            if target_stage is ModelStage.DEPRECATED
            else GovernanceAction.STAGE_PROMOTED
        )
        record_governance_event(
            actor_id,
            action,
            model_name,
            subject_kind=SubjectKind.MODEL,
            before=str(previous_stage),
            after=str(target_stage),
            details={"version": version},
        )
        return updated

    def rollback_production(self, model_name: str, *, actor_id: str = "system") -> ModelVersion:
        """Restore production to the version it served before the current one.

        Args:
            model_name: Logical model name.
            actor_id: Principal responsible, recorded on the governance trail.

        Returns:
            The :class:`ModelVersion` restored to production.

        Raises:
            RollbackError: If there is no recorded previous production version,
                or it is no longer registered.
        """
        if not is_enabled("governance"):
            _logger.debug("Governance framework disabled; skipping rollback.")
            return self.get_production(model_name)

        with self._lock:
            previous_version = self._previous_production.get(model_name)
            if previous_version is None:
                raise RollbackError(
                    f"Model {model_name!r} has no previous production version to roll back to."
                )
            previous_key = (model_name, previous_version)
            if previous_key not in self._versions:
                raise RollbackError(
                    f"Previous production version {previous_version!r} of {model_name!r} "
                    "is no longer registered."
                )

            incumbent: ModelVersion | None = None
            for key, entry in list(self._versions.items()):
                if entry.model_name == model_name and entry.stage is ModelStage.PRODUCTION:
                    incumbent = replace(entry, stage=ModelStage.STAGING)
                    self._versions[key] = incumbent

            restored = replace(self._versions[previous_key], stage=ModelStage.PRODUCTION)
            self._versions[previous_key] = restored
            # The rolled-back version becomes the thing to roll back *to*, so a
            # second rollback returns rather than walking further into history.
            self._previous_production[model_name] = (
                incumbent.version if incumbent is not None else previous_version
            )

        record_governance_event(
            actor_id,
            GovernanceAction.PRODUCTION_ROLLED_BACK,
            model_name,
            subject_kind=SubjectKind.MODEL,
            before=incumbent.version if incumbent is not None else None,
            after=restored.version,
            details={"version": restored.version},
        )
        return restored

    def reset(self) -> None:
        """Drop all registered versions and rollback history."""
        with self._lock:
            self._versions.clear()
            self._previous_production.clear()


_default_registry = ModelRegistry()


def get_default_registry() -> ModelRegistry:
    """Return the process-wide default registry.

    Returns:
        The registry backing the module-level convenience functions.
    """
    return _default_registry


def register_model_version(
    model_name: str,
    version: str,
    *,
    provider_model_id: str | None = None,
    config_fingerprint: str | None = None,
    actor_id: str = "system",
) -> ModelVersion:
    """Register a new model version in the ``DEVELOPMENT`` stage.

    Args:
        model_name: Logical model name.
        version: Version identifier for this registration.
        provider_model_id: Provider model id this version wraps.
        config_fingerprint: Digest of the defining configuration.
        actor_id: Principal responsible, recorded on the governance trail.

    Returns:
        The newly registered :class:`ModelVersion`.

    Raises:
        DuplicateVersionError: If this (model, version) is already registered.
    """
    return _default_registry.register(
        model_name,
        version,
        provider_model_id=provider_model_id,
        config_fingerprint=config_fingerprint,
        actor_id=actor_id,
    )


def promote_model_version(
    model_name: str,
    version: str,
    target_stage: ModelStage,
    *,
    actor_id: str = "system",
) -> ModelVersion:
    """Promote a registered model version to a new lifecycle stage.

    Args:
        model_name: Logical model name.
        version: Version identifier to promote.
        target_stage: The stage to promote the version into.
        actor_id: Principal responsible, recorded on the governance trail.

    Returns:
        The updated :class:`ModelVersion`.

    Raises:
        UnknownVersionError: If the pair is not registered.
        IllegalTransitionError: If the move is not permitted.
    """
    return _default_registry.promote(model_name, version, target_stage, actor_id=actor_id)


def get_production_version(model_name: str) -> ModelVersion:
    """Look up the version currently in the ``PRODUCTION`` stage.

    Args:
        model_name: Logical model name to look up.

    Returns:
        The current production :class:`ModelVersion`.

    Raises:
        NoProductionVersionError: If no version is in production.
    """
    return _default_registry.get_production(model_name)
