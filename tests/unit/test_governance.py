"""Unit tests for the cockpit governance framework.

The load-bearing controls are the ones that stop a single person pushing an
unreviewed change to production: the stage-transition map, the
one-production-version invariant, and the no-self-approval rule. Those get
tested from both directions -- allowed paths work, forbidden paths raise.

Timestamps are always injected; nothing here sleeps.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from cockpit.config import feature_flags
from cockpit.governance.approval_workflow import (
    ApprovalStatus,
    ApprovalWorkflow,
    RequestNotPendingError,
    SelfApprovalError,
    UnknownRequestError,
)
from cockpit.governance.audit_trail import (
    OUTCOME_DENIED,
    GovernanceAction,
    GovernanceTrail,
)
from cockpit.governance.model_versioning import (
    LEGAL_TRANSITIONS,
    DuplicateVersionError,
    IllegalTransitionError,
    ModelRegistry,
    ModelStage,
    NoProductionVersionError,
    RollbackError,
    UnknownVersionError,
    is_legal_transition,
)

FIXED_TIME = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def governance_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force the ``governance`` feature flag off for one test.

    Yields:
        None.
    """
    monkeypatch.setitem(feature_flags.FRAMEWORKS_ENABLED, "governance", False)
    yield


@pytest.fixture
def registry() -> ModelRegistry:
    """Return an isolated registry.

    Returns:
        A fresh :class:`ModelRegistry`.
    """
    return ModelRegistry()


@pytest.fixture
def workflow() -> ApprovalWorkflow:
    """Return an isolated approval workflow.

    Returns:
        A fresh :class:`ApprovalWorkflow`.
    """
    return ApprovalWorkflow()


def staged(registry: ModelRegistry, model: str, version: str) -> None:
    """Register a version and move it to STAGING.

    Args:
        registry: Registry to act on.
        model: Logical model name.
        version: Version identifier.
    """
    registry.register(model, version, registered_at=FIXED_TIME)
    registry.promote(model, version, ModelStage.STAGING)


class TestTransitionMap:
    """The promotion policy itself, before any registry is involved."""

    def test_development_cannot_reach_production_directly(self) -> None:
        """The whole point of the map: no shipping straight from dev."""
        assert is_legal_transition(ModelStage.DEVELOPMENT, ModelStage.PRODUCTION) is False

    def test_staging_can_reach_production(self) -> None:
        assert is_legal_transition(ModelStage.STAGING, ModelStage.PRODUCTION) is True

    def test_every_stage_has_an_entry(self) -> None:
        """A missing entry denies everything, so absence must be deliberate."""
        assert set(LEGAL_TRANSITIONS) == set(ModelStage)

    def test_no_stage_transitions_to_itself(self) -> None:
        for stage, targets in LEGAL_TRANSITIONS.items():
            assert stage not in targets


class TestRegistry:
    """Registration and lookup."""

    def test_register_starts_in_development(self, registry: ModelRegistry) -> None:
        version = registry.register("gemini-flash", "v1", registered_at=FIXED_TIME)
        assert version.stage is ModelStage.DEVELOPMENT

    def test_duplicate_registration_is_rejected(self, registry: ModelRegistry) -> None:
        registry.register("gemini-flash", "v1", registered_at=FIXED_TIME)
        with pytest.raises(DuplicateVersionError):
            registry.register("gemini-flash", "v1", registered_at=FIXED_TIME)

    def test_same_version_string_under_a_different_model_is_fine(
        self, registry: ModelRegistry
    ) -> None:
        registry.register("model-a", "v1", registered_at=FIXED_TIME)
        registry.register("model-b", "v1", registered_at=FIXED_TIME)
        assert registry.get("model-b", "v1").model_name == "model-b"

    def test_get_unknown_version_raises(self, registry: ModelRegistry) -> None:
        with pytest.raises(UnknownVersionError):
            registry.get("gemini-flash", "nope")

    def test_list_versions_is_scoped_to_the_model(self, registry: ModelRegistry) -> None:
        registry.register("model-a", "v1", registered_at=FIXED_TIME)
        registry.register("model-a", "v2", registered_at=FIXED_TIME + timedelta(days=1))
        registry.register("model-b", "v1", registered_at=FIXED_TIME)

        assert [v.version for v in registry.list_versions("model-a")] == ["v1", "v2"]

    def test_versions_are_immutable(self, registry: ModelRegistry) -> None:
        version = registry.register("gemini-flash", "v1", registered_at=FIXED_TIME)
        with pytest.raises(AttributeError):
            version.stage = ModelStage.PRODUCTION  # type: ignore[misc]


class TestPromotion:
    """Stage movement and the production invariant."""

    def test_legal_promotion_succeeds(self, registry: ModelRegistry) -> None:
        registry.register("gemini-flash", "v1", registered_at=FIXED_TIME)
        promoted = registry.promote("gemini-flash", "v1", ModelStage.STAGING)
        assert promoted.stage is ModelStage.STAGING

    def test_illegal_promotion_raises_and_names_the_transition(
        self, registry: ModelRegistry
    ) -> None:
        registry.register("gemini-flash", "v1", registered_at=FIXED_TIME)
        with pytest.raises(IllegalTransitionError) as excinfo:
            registry.promote("gemini-flash", "v1", ModelStage.PRODUCTION)

        assert excinfo.value.from_stage is ModelStage.DEVELOPMENT
        assert excinfo.value.to_stage is ModelStage.PRODUCTION

    def test_illegal_promotion_leaves_the_stage_untouched(self, registry: ModelRegistry) -> None:
        registry.register("gemini-flash", "v1", registered_at=FIXED_TIME)
        with pytest.raises(IllegalTransitionError):
            registry.promote("gemini-flash", "v1", ModelStage.PRODUCTION)

        assert registry.get("gemini-flash", "v1").stage is ModelStage.DEVELOPMENT

    def test_promoting_unknown_version_raises(self, registry: ModelRegistry) -> None:
        with pytest.raises(UnknownVersionError):
            registry.promote("gemini-flash", "ghost", ModelStage.STAGING)

    def test_promoting_to_production_demotes_the_incumbent(self, registry: ModelRegistry) -> None:
        staged(registry, "gemini-flash", "v1")
        registry.promote("gemini-flash", "v1", ModelStage.PRODUCTION)
        staged(registry, "gemini-flash", "v2")
        registry.promote("gemini-flash", "v2", ModelStage.PRODUCTION)

        assert registry.get("gemini-flash", "v1").stage is ModelStage.STAGING
        assert registry.get("gemini-flash", "v2").stage is ModelStage.PRODUCTION

    def test_only_one_version_is_ever_in_production(self, registry: ModelRegistry) -> None:
        for version in ("v1", "v2", "v3"):
            staged(registry, "gemini-flash", version)
            registry.promote("gemini-flash", version, ModelStage.PRODUCTION)

        in_production = registry.versions_at_stage("gemini-flash", ModelStage.PRODUCTION)
        assert len(in_production) == 1
        assert in_production[0].version == "v3"

    def test_demoting_another_model_is_not_affected(self, registry: ModelRegistry) -> None:
        """Production is per-model; promoting one must not disturb another."""
        staged(registry, "model-a", "v1")
        registry.promote("model-a", "v1", ModelStage.PRODUCTION)
        staged(registry, "model-b", "v1")
        registry.promote("model-b", "v1", ModelStage.PRODUCTION)

        assert registry.get("model-a", "v1").stage is ModelStage.PRODUCTION
        assert registry.get("model-b", "v1").stage is ModelStage.PRODUCTION

    def test_get_production_raises_when_none_serving(self, registry: ModelRegistry) -> None:
        registry.register("gemini-flash", "v1", registered_at=FIXED_TIME)
        with pytest.raises(NoProductionVersionError):
            registry.get_production("gemini-flash")

    def test_promotion_is_a_no_op_when_disabled(
        self, registry: ModelRegistry, governance_off: None
    ) -> None:
        registry.register("gemini-flash", "v1", registered_at=FIXED_TIME)
        result = registry.promote("gemini-flash", "v1", ModelStage.STAGING)
        assert result.stage is ModelStage.DEVELOPMENT


class TestRollback:
    """Returning production to the previously serving version."""

    def test_rollback_restores_the_previous_version(self, registry: ModelRegistry) -> None:
        staged(registry, "gemini-flash", "v1")
        registry.promote("gemini-flash", "v1", ModelStage.PRODUCTION)
        staged(registry, "gemini-flash", "v2")
        registry.promote("gemini-flash", "v2", ModelStage.PRODUCTION)

        restored = registry.rollback_production("gemini-flash")

        assert restored.version == "v1"
        assert registry.get_production("gemini-flash").version == "v1"
        assert registry.get("gemini-flash", "v2").stage is ModelStage.STAGING

    def test_rollback_without_history_raises(self, registry: ModelRegistry) -> None:
        staged(registry, "gemini-flash", "v1")
        registry.promote("gemini-flash", "v1", ModelStage.PRODUCTION)

        with pytest.raises(RollbackError):
            registry.rollback_production("gemini-flash")

    def test_rollback_twice_returns_to_the_later_version(self, registry: ModelRegistry) -> None:
        """A second rollback undoes the first rather than walking further back."""
        staged(registry, "gemini-flash", "v1")
        registry.promote("gemini-flash", "v1", ModelStage.PRODUCTION)
        staged(registry, "gemini-flash", "v2")
        registry.promote("gemini-flash", "v2", ModelStage.PRODUCTION)

        registry.rollback_production("gemini-flash")
        registry.rollback_production("gemini-flash")

        assert registry.get_production("gemini-flash").version == "v2"


class TestSelfApproval:
    """The control most often missing, tested hardest."""

    def test_requester_cannot_approve_their_own_request(self, workflow: ApprovalWorkflow) -> None:
        request = workflow.submit("alice", "promote v2", created_at=FIXED_TIME)
        with pytest.raises(SelfApprovalError):
            workflow.decide(request.request_id, True, "alice", now=FIXED_TIME)

    def test_requester_cannot_reject_their_own_request_either(
        self, workflow: ApprovalWorkflow
    ) -> None:
        """The rule is about who decides, not which way they decide."""
        request = workflow.submit("alice", "promote v2", created_at=FIXED_TIME)
        with pytest.raises(SelfApprovalError):
            workflow.decide(request.request_id, False, "alice", now=FIXED_TIME)

    def test_blocked_self_approval_leaves_the_request_pending(
        self, workflow: ApprovalWorkflow
    ) -> None:
        request = workflow.submit("alice", "promote v2", created_at=FIXED_TIME)
        with pytest.raises(SelfApprovalError):
            workflow.decide(request.request_id, True, "alice", now=FIXED_TIME)

        assert workflow.get(request.request_id).status is ApprovalStatus.PENDING
        assert workflow.get(request.request_id).approval_count == 0

    def test_another_principal_can_approve(self, workflow: ApprovalWorkflow) -> None:
        request = workflow.submit("alice", "promote v2", created_at=FIXED_TIME)
        decided = workflow.decide(request.request_id, True, "bob", now=FIXED_TIME)
        assert decided.status is ApprovalStatus.APPROVED


class TestApprovalThreshold:
    """N-of-M counting, including the duplicate-vote case."""

    def test_stays_pending_below_the_threshold(self, workflow: ApprovalWorkflow) -> None:
        request = workflow.submit(
            "alice", "promote v2", required_approvals=2, created_at=FIXED_TIME
        )
        after_one = workflow.decide(request.request_id, True, "bob", now=FIXED_TIME)

        assert after_one.status is ApprovalStatus.PENDING
        assert after_one.approval_count == 1

    def test_approves_on_reaching_the_threshold(self, workflow: ApprovalWorkflow) -> None:
        request = workflow.submit(
            "alice", "promote v2", required_approvals=2, created_at=FIXED_TIME
        )
        workflow.decide(request.request_id, True, "bob", now=FIXED_TIME)
        after_two = workflow.decide(request.request_id, True, "carol", now=FIXED_TIME)

        assert after_two.status is ApprovalStatus.APPROVED

    def test_same_approver_twice_does_not_satisfy_two_of_m(
        self, workflow: ApprovalWorkflow
    ) -> None:
        """One principal, one vote -- otherwise 2-of-M is really 1-of-M."""
        request = workflow.submit(
            "alice", "promote v2", required_approvals=2, created_at=FIXED_TIME
        )
        workflow.decide(request.request_id, True, "bob", now=FIXED_TIME)
        again = workflow.decide(request.request_id, True, "bob", now=FIXED_TIME)

        assert again.approval_count == 1
        assert again.status is ApprovalStatus.PENDING

    def test_required_approvals_must_be_positive(self, workflow: ApprovalWorkflow) -> None:
        with pytest.raises(ValueError):
            workflow.submit("alice", "promote v2", required_approvals=0)


class TestTerminalStates:
    """Once decided, a request stays decided."""

    def test_rejection_is_immediate_regardless_of_threshold(
        self, workflow: ApprovalWorkflow
    ) -> None:
        """A veto that can be outvoted is not a veto."""
        request = workflow.submit(
            "alice", "promote v2", required_approvals=3, created_at=FIXED_TIME
        )
        rejected = workflow.decide(request.request_id, False, "bob", now=FIXED_TIME)
        assert rejected.status is ApprovalStatus.REJECTED

    def test_rejected_request_cannot_later_be_approved(self, workflow: ApprovalWorkflow) -> None:
        request = workflow.submit("alice", "promote v2", created_at=FIXED_TIME)
        workflow.decide(request.request_id, False, "bob", now=FIXED_TIME)

        with pytest.raises(RequestNotPendingError):
            workflow.decide(request.request_id, True, "carol", now=FIXED_TIME)

        assert workflow.get(request.request_id).status is ApprovalStatus.REJECTED

    def test_approved_request_cannot_be_decided_again(self, workflow: ApprovalWorkflow) -> None:
        request = workflow.submit("alice", "promote v2", created_at=FIXED_TIME)
        workflow.decide(request.request_id, True, "bob", now=FIXED_TIME)

        with pytest.raises(RequestNotPendingError):
            workflow.decide(request.request_id, False, "carol", now=FIXED_TIME)

    def test_unknown_request_raises(self, workflow: ApprovalWorkflow) -> None:
        with pytest.raises(UnknownRequestError):
            workflow.decide("req-missing", True, "bob", now=FIXED_TIME)


class TestExpiry:
    """Deadlines, evaluated against an injected clock."""

    def test_request_past_its_deadline_cannot_be_approved(self, workflow: ApprovalWorkflow) -> None:
        request = workflow.submit(
            "alice",
            "promote v2",
            created_at=FIXED_TIME,
            expires_in=timedelta(hours=1),
        )
        later = FIXED_TIME + timedelta(hours=2)

        with pytest.raises(RequestNotPendingError):
            workflow.decide(request.request_id, True, "bob", now=later)

        assert workflow.get(request.request_id).status is ApprovalStatus.EXPIRED

    def test_request_inside_its_window_is_approvable(self, workflow: ApprovalWorkflow) -> None:
        request = workflow.submit(
            "alice",
            "promote v2",
            created_at=FIXED_TIME,
            expires_in=timedelta(hours=1),
        )
        decided = workflow.decide(
            request.request_id, True, "bob", now=FIXED_TIME + timedelta(minutes=30)
        )
        assert decided.status is ApprovalStatus.APPROVED

    def test_expire_overdue_sweeps_pending_requests(self, workflow: ApprovalWorkflow) -> None:
        workflow.submit("alice", "a", created_at=FIXED_TIME, expires_in=timedelta(hours=1))
        workflow.submit("alice", "b", created_at=FIXED_TIME)

        expired = workflow.expire_overdue(now=FIXED_TIME + timedelta(hours=2))

        assert [r.description for r in expired] == ["a"]

    def test_list_pending_excludes_expired(self, workflow: ApprovalWorkflow) -> None:
        workflow.submit("alice", "a", created_at=FIXED_TIME, expires_in=timedelta(hours=1))
        workflow.submit("alice", "b", created_at=FIXED_TIME)

        pending = workflow.list_pending(now=FIXED_TIME + timedelta(hours=2))
        assert [r.description for r in pending] == ["b"]

    def test_decision_is_a_no_op_when_disabled(
        self, workflow: ApprovalWorkflow, governance_off: None
    ) -> None:
        request = workflow.submit("alice", "promote v2", created_at=FIXED_TIME)
        result = workflow.decide(request.request_id, True, "bob", now=FIXED_TIME)
        assert result.status is ApprovalStatus.PENDING


class TestGovernanceTrail:
    """The trail records decisions and survives verification."""

    def test_trail_records_and_verifies(self) -> None:
        trail = GovernanceTrail()
        trail.record(
            "alice",
            GovernanceAction.VERSION_REGISTERED,
            "gemini-flash",
            after=str(ModelStage.DEVELOPMENT),
        )
        assert trail.verify().is_valid is True

    def test_denied_attempts_are_recorded_with_a_denied_outcome(self) -> None:
        """A blocked self-approval must not be invisible."""
        trail = GovernanceTrail()
        trail.record(
            "alice",
            GovernanceAction.APPROVAL_DENIED,
            "req-1",
            outcome=OUTCOME_DENIED,
            details={"reason": "self_approval"},
        )
        recorded = trail.records[0].event
        assert recorded.outcome == OUTCOME_DENIED
        assert recorded.action is GovernanceAction.APPROVAL_DENIED
