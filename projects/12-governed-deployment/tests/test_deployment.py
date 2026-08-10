"""Tests for the governed deployment pipeline.

No API key, no network, no sleeping. Every timestamp is injected, so the
approval deadlines and trail ordering are exact rather than whatever the
machine happened to produce.

Isolation matters more here than in most of these projects. The registry and
the approval workflow write their governance events to the *module-level
default trail*, so a test that asserts on trail contents has to own that
trail. Every test therefore builds a fresh ``ModelRegistry`` and
``ApprovalWorkflow`` and resets the default trail first, via the
``pipeline`` fixture.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import main
import pytest
import scenario
from deployment import (
    PRODUCTION_PERMISSION,
    DeploymentAction,
    GovernedDeployment,
    RefusalReason,
    describe_request,
)
from scenario import (
    ENGINEER,
    MODEL_NAME,
    RELEASE_MANAGER,
    REVIEWER_ONE,
    REVIEWER_TWO,
    VERSION_ONE,
    VERSION_TWO,
    ScenarioClock,
    run_scenario,
)

from cockpit.governance.approval_workflow import (
    ApprovalStatus,
    ApprovalWorkflow,
    RequestNotPendingError,
    SelfApprovalError,
)
from cockpit.governance.audit_trail import (
    OUTCOME_DENIED,
    GovernanceAction,
    get_default_trail,
)
from cockpit.governance.model_versioning import (
    IllegalTransitionError,
    ModelRegistry,
    ModelStage,
)
from cockpit.security.access_control import AuthorizationError, Principal, Role, has_permission

BASE_TIME = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
REQUEST_ID = "req-test-1"


@pytest.fixture
def clock() -> ScenarioClock:
    """Return a deterministic clock starting at :data:`BASE_TIME`.

    Returns:
        A clock advancing a fixed step per reading.
    """
    return ScenarioClock(BASE_TIME)


@pytest.fixture
def pipeline() -> Iterator[GovernedDeployment]:
    """Yield a pipeline over fresh state and a reset default trail.

    The deployment's registry and workflow are its own, and its trail is the
    default one -- reset before and after, because the framework writes
    registry and approval events there unconditionally.

    Yields:
        An isolated :class:`~deployment.GovernedDeployment`.
    """
    trail = get_default_trail()
    trail.reset()
    deployment = GovernedDeployment(
        registry=ModelRegistry(), workflow=ApprovalWorkflow(), trail=trail
    )
    yield deployment
    trail.reset()


def stage_candidate(
    pipeline: GovernedDeployment, clock: ScenarioClock, version: str = VERSION_ONE
) -> None:
    """Register a version and move it to staging.

    Args:
        pipeline: The pipeline to drive.
        clock: Timestamp source.
        version: Version to register.
    """
    assert pipeline.register(MODEL_NAME, version, actor=ENGINEER, at=clock()).allowed
    assert pipeline.promote(
        MODEL_NAME, version, ModelStage.STAGING, actor=ENGINEER, at=clock()
    ).allowed


def approved_request(
    pipeline: GovernedDeployment,
    clock: ScenarioClock,
    version: str = VERSION_ONE,
    request_id: str = REQUEST_ID,
) -> str:
    """Open a 2-of-2 request for a version and get both approvals in.

    Args:
        pipeline: The pipeline to drive.
        clock: Timestamp source.
        version: Version the request authorizes.
        request_id: Explicit request id.

    Returns:
        The approved request's id.
    """
    opened = pipeline.request_promotion(
        MODEL_NAME,
        version,
        requested_by=ENGINEER,
        required_approvals=2,
        request_id=request_id,
        created_at=clock(),
    )
    assert opened.allowed
    assert pipeline.decide(request_id, True, actor=REVIEWER_ONE, now=clock()).allowed
    assert pipeline.decide(request_id, True, actor=REVIEWER_TWO, now=clock()).allowed
    assert pipeline.workflow.get(request_id).status is ApprovalStatus.APPROVED
    return request_id


# --------------------------------------------------------------- no shortcuts


def test_development_straight_to_production_is_refused(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """The transition map has no development -> production edge."""
    pipeline.register(MODEL_NAME, VERSION_ONE, actor=ENGINEER, at=clock())

    outcome = pipeline.promote(
        MODEL_NAME, VERSION_ONE, ModelStage.PRODUCTION, actor=RELEASE_MANAGER, at=clock()
    )

    assert not outcome.allowed
    assert outcome.reason is RefusalReason.ILLEGAL_TRANSITION
    assert isinstance(outcome.error, IllegalTransitionError)
    assert outcome.error.from_stage is ModelStage.DEVELOPMENT
    assert outcome.error.to_stage is ModelStage.PRODUCTION
    assert pipeline.production_version(MODEL_NAME) is None


def test_transition_map_is_checked_before_the_approval_gate(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """An approved request cannot be spent on a structurally illegal move."""
    pipeline.register(MODEL_NAME, VERSION_ONE, actor=ENGINEER, at=clock())
    request_id = approved_request(pipeline, clock)

    outcome = pipeline.promote(
        MODEL_NAME,
        VERSION_ONE,
        ModelStage.PRODUCTION,
        actor=RELEASE_MANAGER,
        request_id=request_id,
        at=clock(),
    )

    assert outcome.reason is RefusalReason.ILLEGAL_TRANSITION
    assert pipeline.workflow.get(request_id).status is ApprovalStatus.APPROVED


def test_production_promotion_without_a_request_is_refused(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """Staging -> production is legal, but not on its own."""
    stage_candidate(pipeline, clock)

    outcome = pipeline.promote(
        MODEL_NAME, VERSION_ONE, ModelStage.PRODUCTION, actor=RELEASE_MANAGER, at=clock()
    )

    assert not outcome.allowed
    assert outcome.reason is RefusalReason.NO_APPROVAL_REQUEST
    assert pipeline.production_version(MODEL_NAME) is None


def test_production_promotion_with_a_pending_request_is_refused(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """A request short of its threshold does not open the gate."""
    stage_candidate(pipeline, clock)
    pipeline.request_promotion(
        MODEL_NAME,
        VERSION_ONE,
        requested_by=ENGINEER,
        required_approvals=2,
        request_id=REQUEST_ID,
        created_at=clock(),
    )
    pipeline.decide(REQUEST_ID, True, actor=REVIEWER_ONE, now=clock())

    outcome = pipeline.promote(
        MODEL_NAME,
        VERSION_ONE,
        ModelStage.PRODUCTION,
        actor=RELEASE_MANAGER,
        request_id=REQUEST_ID,
        at=clock(),
    )

    assert outcome.reason is RefusalReason.APPROVAL_NOT_GRANTED
    assert "1 of 2" in outcome.detail


def test_an_approval_is_bound_to_the_version_it_was_opened_for(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """An approved request is not a bearer token for any other version."""
    stage_candidate(pipeline, clock, VERSION_ONE)
    stage_candidate(pipeline, clock, VERSION_TWO)
    request_id = approved_request(pipeline, clock, VERSION_TWO)

    outcome = pipeline.promote(
        MODEL_NAME,
        VERSION_ONE,
        ModelStage.PRODUCTION,
        actor=RELEASE_MANAGER,
        request_id=request_id,
        at=clock(),
    )

    assert outcome.reason is RefusalReason.REQUEST_SUBJECT_MISMATCH


# ------------------------------------------------------ segregation of duties


def test_requester_cannot_approve_their_own_request(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """Self-approval is the control most often missing in practice."""
    stage_candidate(pipeline, clock)
    pipeline.request_promotion(
        MODEL_NAME,
        VERSION_ONE,
        requested_by=ENGINEER,
        required_approvals=2,
        request_id=REQUEST_ID,
        created_at=clock(),
    )

    outcome = pipeline.decide(REQUEST_ID, True, actor=ENGINEER, now=clock())

    assert not outcome.allowed
    assert outcome.reason is RefusalReason.SELF_APPROVAL
    assert isinstance(outcome.error, SelfApprovalError)
    assert pipeline.workflow.get(REQUEST_ID).approval_count == 0


def test_one_approver_voting_twice_does_not_satisfy_two_of_two(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """Deduplication is by principal, not by call."""
    stage_candidate(pipeline, clock)
    pipeline.request_promotion(
        MODEL_NAME,
        VERSION_ONE,
        requested_by=ENGINEER,
        required_approvals=2,
        request_id=REQUEST_ID,
        created_at=clock(),
    )

    first = pipeline.decide(REQUEST_ID, True, actor=REVIEWER_ONE, now=clock())
    second = pipeline.decide(REQUEST_ID, True, actor=REVIEWER_ONE, now=clock())

    assert first.allowed and second.allowed
    request = pipeline.workflow.get(REQUEST_ID)
    assert request.approval_count == 1
    assert request.approvers == frozenset({REVIEWER_ONE.principal_id})
    assert request.status is ApprovalStatus.PENDING
    assert not request.is_satisfied
    assert "1 of 2" in describe_request(request)


def test_the_engineer_who_requested_cannot_execute_the_promotion(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """A developer may reach staging; only config:manage reaches production."""
    stage_candidate(pipeline, clock)
    request_id = approved_request(pipeline, clock)

    outcome = pipeline.promote(
        MODEL_NAME,
        VERSION_ONE,
        ModelStage.PRODUCTION,
        actor=ENGINEER,
        request_id=request_id,
        at=clock(),
    )

    assert outcome.reason is RefusalReason.NOT_AUTHORIZED
    assert isinstance(outcome.error, AuthorizationError)
    assert not has_permission(ENGINEER, PRODUCTION_PERMISSION)
    assert has_permission(RELEASE_MANAGER, PRODUCTION_PERMISSION)


def test_a_viewer_cannot_even_register_a_candidate(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """Registration needs project:write, which a viewer does not hold."""
    watcher = Principal("obs-sam", frozenset({Role.VIEWER}))

    outcome = pipeline.register(MODEL_NAME, VERSION_ONE, actor=watcher, at=clock())

    assert outcome.reason is RefusalReason.NOT_AUTHORIZED
    assert pipeline.registry.list_versions(MODEL_NAME) == []


# ------------------------------------------------------------- the happy path


def test_two_distinct_approvals_open_the_gate(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """The full legal route: dev -> staging -> approved request -> production."""
    stage_candidate(pipeline, clock)
    request_id = approved_request(pipeline, clock)

    outcome = pipeline.promote(
        MODEL_NAME,
        VERSION_ONE,
        ModelStage.PRODUCTION,
        actor=RELEASE_MANAGER,
        request_id=request_id,
        at=clock(),
    )

    assert outcome.allowed
    assert outcome.stage is ModelStage.PRODUCTION
    current = pipeline.production_version(MODEL_NAME)
    assert current is not None
    assert current.version == VERSION_ONE
    assert pipeline.production_count(MODEL_NAME) == 1


def test_promoting_a_second_version_demotes_the_incumbent(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """Exactly one version of a model is ever in production."""
    promote_to_production(pipeline, clock, VERSION_ONE, "req-v1")
    promote_to_production(pipeline, clock, VERSION_TWO, "req-v2")

    current = pipeline.production_version(MODEL_NAME)
    assert current is not None
    assert current.version == VERSION_TWO
    assert pipeline.production_count(MODEL_NAME) == 1
    assert pipeline.registry.get(MODEL_NAME, VERSION_ONE).stage is ModelStage.STAGING


def test_rollback_restores_the_previous_production_version(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """Rollback is the supported way back, and needs no fresh approval."""
    promote_to_production(pipeline, clock, VERSION_ONE, "req-v1")
    promote_to_production(pipeline, clock, VERSION_TWO, "req-v2")

    outcome = pipeline.rollback(MODEL_NAME, actor=RELEASE_MANAGER, at=clock())

    assert outcome.allowed
    assert outcome.version == VERSION_ONE
    current = pipeline.production_version(MODEL_NAME)
    assert current is not None
    assert current.version == VERSION_ONE
    assert pipeline.production_count(MODEL_NAME) == 1
    assert pipeline.registry.get(MODEL_NAME, VERSION_TWO).stage is ModelStage.STAGING


def test_rollback_with_no_history_is_refused(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """There has to be somewhere to roll back to."""
    stage_candidate(pipeline, clock)

    outcome = pipeline.rollback(MODEL_NAME, actor=RELEASE_MANAGER, at=clock())

    assert outcome.reason is RefusalReason.NO_PREVIOUS_PRODUCTION


def promote_to_production(
    pipeline: GovernedDeployment, clock: ScenarioClock, version: str, request_id: str
) -> None:
    """Take a version the whole legal route into production.

    Args:
        pipeline: The pipeline to drive.
        clock: Timestamp source.
        version: Version to ship.
        request_id: Explicit approval request id.
    """
    stage_candidate(pipeline, clock, version)
    approved_request(pipeline, clock, version, request_id)
    assert pipeline.promote(
        MODEL_NAME,
        version,
        ModelStage.PRODUCTION,
        actor=RELEASE_MANAGER,
        request_id=request_id,
        at=clock(),
    ).allowed


# ----------------------------------------------------------- the audit trail


def test_the_trail_records_allows_and_refusals_and_verifies(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """A refused attempt has to be as visible as a successful one."""
    stage_candidate(pipeline, clock)
    refused = pipeline.promote(
        MODEL_NAME, VERSION_ONE, ModelStage.PRODUCTION, actor=RELEASE_MANAGER, at=clock()
    )
    assert not refused.allowed
    request_id = approved_request(pipeline, clock)
    assert pipeline.promote(
        MODEL_NAME,
        VERSION_ONE,
        ModelStage.PRODUCTION,
        actor=RELEASE_MANAGER,
        request_id=request_id,
        at=clock(),
    ).allowed

    verification = pipeline.verify_trail()
    assert verification.is_valid
    assert verification.records_checked == len(pipeline.trail.records)

    actions = [event.action for event in pipeline.trail.events()]
    assert GovernanceAction.VERSION_REGISTERED in actions
    assert GovernanceAction.STAGE_PROMOTED in actions
    assert GovernanceAction.APPROVAL_REQUESTED in actions
    assert GovernanceAction.APPROVAL_GRANTED in actions

    denied = [e for e in pipeline.trail.events() if e.outcome == OUTCOME_DENIED]
    assert len(denied) == 1
    assert denied[0].details["reason"] == str(RefusalReason.NO_APPROVAL_REQUEST)
    assert denied[0].actor_id == RELEASE_MANAGER.principal_id


def test_a_blocked_self_approval_is_recorded_by_the_workflow(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """The refusal lands on the trail exactly once, not twice."""
    stage_candidate(pipeline, clock)
    pipeline.request_promotion(
        MODEL_NAME,
        VERSION_ONE,
        requested_by=ENGINEER,
        required_approvals=2,
        request_id=REQUEST_ID,
        created_at=clock(),
    )

    pipeline.decide(REQUEST_ID, True, actor=ENGINEER, now=clock())

    denials = [
        event
        for event in pipeline.trail.history_for_request(REQUEST_ID)
        if event.action is GovernanceAction.APPROVAL_DENIED
    ]
    assert len(denials) == 1
    assert denials[0].outcome == OUTCOME_DENIED
    assert denials[0].details["reason"] == "self_approval"
    assert pipeline.verify_trail().is_valid


def test_a_rejected_request_stays_rejected(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """Rejection is terminal: more signatures cannot walk it back."""
    stage_candidate(pipeline, clock)
    pipeline.request_promotion(
        MODEL_NAME,
        VERSION_ONE,
        requested_by=ENGINEER,
        required_approvals=2,
        request_id=REQUEST_ID,
        created_at=clock(),
    )
    pipeline.decide(REQUEST_ID, False, actor=REVIEWER_ONE, comment="eval regression", now=clock())

    late = pipeline.decide(REQUEST_ID, True, actor=REVIEWER_TWO, now=clock())

    assert late.reason is RefusalReason.REQUEST_NOT_PENDING
    assert isinstance(late.error, RequestNotPendingError)
    assert pipeline.workflow.get(REQUEST_ID).status is ApprovalStatus.REJECTED
    assert (
        pipeline.promote(
            MODEL_NAME,
            VERSION_ONE,
            ModelStage.PRODUCTION,
            actor=RELEASE_MANAGER,
            request_id=REQUEST_ID,
            at=clock(),
        ).reason
        is RefusalReason.APPROVAL_NOT_GRANTED
    )


def test_an_expired_request_does_not_open_the_gate(
    pipeline: GovernedDeployment, clock: ScenarioClock
) -> None:
    """Deadlines are enforced against an injected clock, never a sleep."""
    stage_candidate(pipeline, clock)
    pipeline.request_promotion(
        MODEL_NAME,
        VERSION_ONE,
        requested_by=ENGINEER,
        required_approvals=2,
        request_id=REQUEST_ID,
        created_at=BASE_TIME,
        expires_in=timedelta(hours=4),
    )

    too_late = BASE_TIME + timedelta(hours=5)
    outcome = pipeline.decide(REQUEST_ID, True, actor=REVIEWER_ONE, now=too_late)

    assert outcome.reason is RefusalReason.REQUEST_NOT_PENDING
    assert pipeline.workflow.get(REQUEST_ID).status is ApprovalStatus.EXPIRED


# ---------------------------------------------------------------- the scenario


def test_scenario_is_deterministic_and_tells_the_whole_story(
    pipeline: GovernedDeployment,
) -> None:
    """The dry-run scenario ends where it should, with the trail intact."""
    result = run_scenario(pipeline)

    assert result.verification.is_valid
    assert result.production_count == 1
    assert result.production_version is not None
    assert result.production_version.version == VERSION_ONE
    assert result.allowed_count + result.refused_count == len(result.steps)

    refusals = {step.outcome.reason for step in result.steps if not step.outcome.allowed}
    assert refusals == {
        RefusalReason.ILLEGAL_TRANSITION,
        RefusalReason.NO_APPROVAL_REQUEST,
        RefusalReason.SELF_APPROVAL,
        RefusalReason.APPROVAL_NOT_GRANTED,
        RefusalReason.NOT_AUTHORIZED,
        RefusalReason.REQUEST_SUBJECT_MISMATCH,
    }
    assert {step.outcome.action for step in result.steps} == {
        DeploymentAction.REGISTER,
        DeploymentAction.PROMOTE,
        DeploymentAction.REQUEST_PROMOTION,
        DeploymentAction.DECIDE,
        DeploymentAction.ROLLBACK,
    }


def test_scenario_repeats_identically(pipeline: GovernedDeployment) -> None:
    """Same script, same clock, same transcript -- nothing reads the wall clock."""
    first = main.render_transcript(run_scenario(pipeline))

    get_default_trail().reset()
    second_pipeline = GovernedDeployment(
        registry=ModelRegistry(), workflow=ApprovalWorkflow(), trail=get_default_trail()
    )
    second = main.render_transcript(run_scenario(second_pipeline))

    assert first == second


def test_cli_dry_run_prints_a_transcript_and_exits_zero(
    pipeline: GovernedDeployment, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--dry-run`` is the story, and it has to end well."""
    exit_code = main.main(["--dry-run"])
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "GOVERNED DEPLOYMENT" in captured
    assert "REFUSED" in captured
    assert "ALLOWED" in captured
    assert "versions in production 1" in captured
    assert "VERIFIED" in captured


def test_scenario_module_uses_no_wall_clock_defaults() -> None:
    """The fixed base time is what makes the dry run reproducible."""
    assert scenario.FIXED_BASE_TIME.tzinfo is UTC
    clock = ScenarioClock(scenario.FIXED_BASE_TIME)
    assert clock() == scenario.FIXED_BASE_TIME
    assert clock() == scenario.FIXED_BASE_TIME + scenario.CLOCK_STEP
