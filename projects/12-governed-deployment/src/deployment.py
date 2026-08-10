"""A deployment pipeline you cannot shortcut.

This module is the orchestrator that ties three Tier 3 governance pieces --
the model registry, the approval workflow, and the governance trail -- into
one release path, and adds Tier 1 RBAC on top of them.

The interesting behaviour is what it *refuses*. Each control below exists
because its absence is a known way a release process gets defeated:

* **Authorization.** Promoting into production needs
  :attr:`~cockpit.security.access_control.Permission.CONFIG_MANAGE`. The
  engineer who built the candidate holds ``project:write`` and can move it
  as far as staging, and no further.
* **The transition map.** ``development -> production`` is not an edge in
  :data:`~cockpit.governance.model_versioning.LEGAL_TRANSITIONS`, so a
  candidate reaches production only by passing through staging. Checked
  before the approval gate, because a structurally impossible move should
  not be able to consume an approval.
* **The approval gate.** A production promotion needs an approval request
  that is bound to *this* model and version and has reached
  :attr:`~cockpit.governance.approval_workflow.ApprovalStatus.APPROVED`.
  A pending request at 1-of-2, or an approved request for a different
  version, does not open the gate.
* **Segregation of duties.** The workflow refuses self-approval, and one
  principal counts once toward an N-of-M threshold.
* **The trail.** Every allow and every refusal lands on a hash-chained
  governance trail, so "we blocked that" is evidence rather than a claim.

Refusals are returned as a :class:`DeploymentOutcome`, not raised. A caller
driving a pipeline wants to report *which control fired and why* and keep
going; the originating exception is still attached as
:attr:`DeploymentOutcome.error` for anyone who wants to re-raise it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from cockpit.governance.approval_workflow import (
    ApprovalRequest,
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
    SubjectKind,
    get_default_trail,
)
from cockpit.governance.model_versioning import (
    DuplicateVersionError,
    IllegalTransitionError,
    ModelRegistry,
    ModelStage,
    ModelVersion,
    RollbackError,
    UnknownVersionError,
    is_legal_transition,
)
from cockpit.security.access_control import (
    AuthorizationError,
    Permission,
    Principal,
    require_permission,
)
from cockpit.security.audit_logging import ChainVerification

# Moving a candidate around the pre-production stages, and asking for a
# production slot, is ordinary engineering work.
STAGE_PERMISSION = Permission.PROJECT_WRITE

# Putting something in front of live traffic, or rolling it back, is not.
# ``config:manage`` is admin-only, which is what makes "the engineer who
# requested the promotion cannot also execute it" true by construction.
PRODUCTION_PERMISSION = Permission.CONFIG_MANAGE

# Approving needs only read access, deliberately: if approval required the
# production permission, the segregation-of-duties refusal would never be
# reached -- the requester would be turned away as unauthorized first, and
# the more informative control would never get to speak.
APPROVAL_PERMISSION = Permission.PROJECT_READ

# Named controls, so a refusal says which mechanism fired rather than only
# what the message happened to be.
CONTROL_RBAC = "security.access_control"
CONTROL_TRANSITION_MAP = "governance.model_versioning.LEGAL_TRANSITIONS"
CONTROL_APPROVAL_GATE = "governance.approval_workflow"
CONTROL_REGISTRY = "governance.model_versioning.ModelRegistry"


class DeploymentAction(StrEnum):
    """What a caller asked the pipeline to do.

    Attributes:
        REGISTER: Add a new candidate version to the registry.
        PROMOTE: Move a registered version to another lifecycle stage.
        REQUEST_PROMOTION: Open an approval request for a production move.
        DECIDE: Record an approval or rejection on a request.
        ROLLBACK: Restore production to the previously serving version.
    """

    REGISTER = "register"
    PROMOTE = "promote"
    REQUEST_PROMOTION = "request_promotion"
    DECIDE = "decide"
    ROLLBACK = "rollback"


class RefusalReason(StrEnum):
    """Why the pipeline refused an action.

    Attributes:
        NOT_AUTHORIZED: The actor lacks the permission the action requires.
        UNKNOWN_VERSION: The (model, version) pair is not registered.
        DUPLICATE_VERSION: The (model, version) pair is already registered.
        ILLEGAL_TRANSITION: The stage move is not in the transition map.
        NO_APPROVAL_REQUEST: A production move was attempted with no
            approval request attached, or with an id that does not exist.
        REQUEST_SUBJECT_MISMATCH: The request was opened for a different
            model or version than the one being promoted.
        APPROVAL_NOT_GRANTED: The request exists but has not reached
            ``APPROVED``.
        SELF_APPROVAL: The requester tried to decide their own request.
        REQUEST_NOT_PENDING: The request already reached a terminal state.
        NO_PREVIOUS_PRODUCTION: There is nothing to roll back to.
    """

    NOT_AUTHORIZED = "not_authorized"
    UNKNOWN_VERSION = "unknown_version"
    DUPLICATE_VERSION = "duplicate_version"
    ILLEGAL_TRANSITION = "illegal_transition"
    NO_APPROVAL_REQUEST = "no_approval_request"
    REQUEST_SUBJECT_MISMATCH = "request_subject_mismatch"
    APPROVAL_NOT_GRANTED = "approval_not_granted"
    SELF_APPROVAL = "self_approval"
    REQUEST_NOT_PENDING = "request_not_pending"
    NO_PREVIOUS_PRODUCTION = "no_previous_production"


@dataclass(frozen=True)
class DeploymentOutcome:
    """The result of one pipeline action, allowed or refused.

    Attributes:
        allowed: True if the action took effect.
        action: What was attempted.
        actor_id: Principal that attempted it.
        summary: One-line description of the attempt, e.g.
            ``"promote gemini-flash v2026-08-01 to production"``.
        detail: What happened, or why it was refused.
        control: Name of the control that refused, or None when allowed.
        reason: Machine-readable refusal reason, or None when allowed.
        error: The originating exception, when a framework raised one, so a
            caller that would rather re-raise than branch on ``reason``
            still can.
        model_name: Model the action concerned, if any.
        version: Version the action concerned, if any.
        stage: Stage the version ended up in, when the action moved it.
        request_id: Approval request involved, if any.
    """

    allowed: bool
    action: DeploymentAction
    actor_id: str
    summary: str
    detail: str
    control: str | None = None
    reason: RefusalReason | None = None
    error: Exception | None = None
    model_name: str | None = None
    version: str | None = None
    stage: ModelStage | None = None
    request_id: str | None = None


@dataclass
class GovernedDeployment:
    """Registry, approvals, RBAC, and audit trail wired into one release path.

    The registry and the workflow both write to the module-level *default*
    governance trail. This orchestrator therefore defaults ``trail`` to that
    same instance, so one trail answers "what happened to this model" across
    versioning, approvals, and the refusals recorded here. Pass explicit
    instances to isolate a test.

    Attributes:
        registry: Model version registry backing the pipeline.
        workflow: Approval workflow backing the production gate.
        trail: Governance trail refusals are recorded on.
    """

    registry: ModelRegistry = field(default_factory=ModelRegistry)
    workflow: ApprovalWorkflow = field(default_factory=ApprovalWorkflow)
    trail: GovernanceTrail = field(default_factory=get_default_trail)
    # Which (model, version) each request was opened for. Binding the request
    # to its subject is what stops an approved request for one version being
    # spent on another.
    _request_subjects: dict[str, tuple[str, str]] = field(default_factory=dict, init=False)

    # ---------------------------------------------------------------- writes

    def register(
        self,
        model_name: str,
        version: str,
        *,
        actor: Principal,
        provider_model_id: str | None = None,
        config_fingerprint: str | None = None,
        at: datetime | None = None,
    ) -> DeploymentOutcome:
        """Add a new candidate version to the registry.

        Args:
            model_name: Logical model name.
            version: Version identifier for the candidate.
            actor: Principal performing the registration.
            provider_model_id: Provider model id this version wraps.
            config_fingerprint: Digest of the configuration that defines it.
            at: Registration time; also stamped on any refusal record.

        Returns:
            A :class:`DeploymentOutcome`. Refused if the actor lacks
            ``project:write`` or the version is already registered.
        """
        summary = f"register {model_name} v{version}"
        denied = self._authorize(
            actor,
            STAGE_PERMISSION,
            action=DeploymentAction.REGISTER,
            summary=summary,
            governance_action=GovernanceAction.VERSION_REGISTERED,
            subject=model_name,
            subject_kind=SubjectKind.MODEL,
            model_name=model_name,
            version=version,
            at=at,
        )
        if denied is not None:
            return denied

        try:
            entry = self.registry.register(
                model_name,
                version,
                provider_model_id=provider_model_id,
                config_fingerprint=config_fingerprint,
                actor_id=actor.principal_id,
                registered_at=at,
            )
        except DuplicateVersionError as exc:
            return self._refuse(
                action=DeploymentAction.REGISTER,
                actor_id=actor.principal_id,
                summary=summary,
                detail=str(exc),
                control=CONTROL_REGISTRY,
                reason=RefusalReason.DUPLICATE_VERSION,
                error=exc,
                governance_action=GovernanceAction.VERSION_REGISTERED,
                subject=model_name,
                subject_kind=SubjectKind.MODEL,
                model_name=model_name,
                version=version,
                at=at,
            )

        return DeploymentOutcome(
            allowed=True,
            action=DeploymentAction.REGISTER,
            actor_id=actor.principal_id,
            summary=summary,
            detail=f"registered at stage '{entry.stage}'",
            model_name=model_name,
            version=version,
            stage=entry.stage,
        )

    def promote(
        self,
        model_name: str,
        version: str,
        target_stage: ModelStage,
        *,
        actor: Principal,
        request_id: str | None = None,
        at: datetime | None = None,
    ) -> DeploymentOutcome:
        """Move a registered version to another lifecycle stage.

        Controls fire in this order, cheapest and most structural first:
        authorization, then registration, then the transition map, then --
        only for a production target -- the approval gate.

        Args:
            model_name: Logical model name.
            version: Version identifier to move.
            target_stage: Stage to move into.
            actor: Principal performing the promotion.
            request_id: Approved approval request authorizing a production
                move. Ignored for non-production targets.
            at: Timestamp stamped on any refusal record.

        Returns:
            A :class:`DeploymentOutcome` describing the move or naming the
            control that refused it.
        """
        summary = f"promote {model_name} v{version} to {target_stage}"
        to_production = target_stage is ModelStage.PRODUCTION
        required = PRODUCTION_PERMISSION if to_production else STAGE_PERMISSION

        denied = self._authorize(
            actor,
            required,
            action=DeploymentAction.PROMOTE,
            summary=summary,
            governance_action=GovernanceAction.STAGE_PROMOTED,
            subject=model_name,
            subject_kind=SubjectKind.MODEL,
            model_name=model_name,
            version=version,
            at=at,
            request_id=request_id,
        )
        if denied is not None:
            return denied

        try:
            current = self.registry.get(model_name, version)
        except UnknownVersionError as exc:
            return self._refuse(
                action=DeploymentAction.PROMOTE,
                actor_id=actor.principal_id,
                summary=summary,
                detail=str(exc),
                control=CONTROL_REGISTRY,
                reason=RefusalReason.UNKNOWN_VERSION,
                error=exc,
                governance_action=GovernanceAction.STAGE_PROMOTED,
                subject=model_name,
                subject_kind=SubjectKind.MODEL,
                model_name=model_name,
                version=version,
                at=at,
            )

        if not is_legal_transition(current.stage, target_stage):
            # Checked ahead of the approval gate on purpose: a move the map
            # forbids must not be able to consume an approval, and the
            # refusal a reader most needs to see is the structural one.
            illegal = IllegalTransitionError(current.stage, target_stage)
            return self._refuse(
                action=DeploymentAction.PROMOTE,
                actor_id=actor.principal_id,
                summary=summary,
                detail=str(illegal),
                control=CONTROL_TRANSITION_MAP,
                reason=RefusalReason.ILLEGAL_TRANSITION,
                error=illegal,
                governance_action=GovernanceAction.STAGE_PROMOTED,
                subject=model_name,
                subject_kind=SubjectKind.MODEL,
                model_name=model_name,
                version=version,
                stage=current.stage,
                at=at,
            )

        if to_production:
            gate = self._check_production_gate(
                model_name, version, request_id, actor=actor, summary=summary, at=at
            )
            if gate is not None:
                return gate

        updated = self.registry.promote(
            model_name, version, target_stage, actor_id=actor.principal_id
        )
        detail = f"moved {current.stage} -> {updated.stage}"
        if to_production:
            detail += f" against approved request {request_id}"
        return DeploymentOutcome(
            allowed=True,
            action=DeploymentAction.PROMOTE,
            actor_id=actor.principal_id,
            summary=summary,
            detail=detail,
            model_name=model_name,
            version=version,
            stage=updated.stage,
            request_id=request_id if to_production else None,
        )

    def request_promotion(
        self,
        model_name: str,
        version: str,
        *,
        requested_by: Principal,
        required_approvals: int = 2,
        request_id: str | None = None,
        created_at: datetime | None = None,
        expires_in: timedelta | None = None,
    ) -> DeploymentOutcome:
        """Open an approval request for a production promotion.

        The request is bound to ``(model_name, version)``, so approving it
        does not authorize promoting anything else.

        Args:
            model_name: Logical model name.
            version: Version the promotion would move.
            requested_by: Principal opening the request.
            required_approvals: Distinct approvals needed. Two by default.
            request_id: Explicit request id; generated when omitted.
            created_at: Creation time; defaults to now (UTC).
            expires_in: Optional lifetime after which the request expires.

        Returns:
            A :class:`DeploymentOutcome` carrying the new ``request_id``,
            or a refusal if the actor lacks ``project:write``.
        """
        summary = f"request approval to promote {model_name} v{version} to production"
        denied = self._authorize(
            requested_by,
            STAGE_PERMISSION,
            action=DeploymentAction.REQUEST_PROMOTION,
            summary=summary,
            governance_action=GovernanceAction.APPROVAL_REQUESTED,
            subject=request_id or f"{model_name}:{version}",
            subject_kind=SubjectKind.APPROVAL_REQUEST,
            model_name=model_name,
            version=version,
            at=created_at,
        )
        if denied is not None:
            return denied

        request = self.workflow.submit(
            requested_by.principal_id,
            summary,
            required_approvals=required_approvals,
            request_id=request_id,
            created_at=created_at,
            expires_in=expires_in,
        )
        self._request_subjects[request.request_id] = (model_name, version)
        return DeploymentOutcome(
            allowed=True,
            action=DeploymentAction.REQUEST_PROMOTION,
            actor_id=requested_by.principal_id,
            summary=summary,
            detail=(
                f"request {request.request_id} opened, "
                f"needs {required_approvals} distinct approval(s)"
            ),
            model_name=model_name,
            version=version,
            request_id=request.request_id,
        )

    def decide(
        self,
        request_id: str,
        approved: bool,
        *,
        actor: Principal,
        comment: str | None = None,
        now: datetime | None = None,
    ) -> DeploymentOutcome:
        """Record an approval or rejection on a pending request.

        Args:
            request_id: Request being decided.
            approved: True to approve, False to reject.
            actor: Reviewing principal.
            comment: Optional rationale.
            now: Decision time; defaults to now (UTC).

        Returns:
            A :class:`DeploymentOutcome`. Refused when the actor lacks
            ``project:read``, when the request does not exist, when the
            requester tries to decide their own request, or when the
            request is already terminal.
        """
        verb = "approve" if approved else "reject"
        summary = f"{verb} request {request_id}"
        denied = self._authorize(
            actor,
            APPROVAL_PERMISSION,
            action=DeploymentAction.DECIDE,
            summary=summary,
            governance_action=GovernanceAction.APPROVAL_DENIED,
            subject=request_id,
            subject_kind=SubjectKind.APPROVAL_REQUEST,
            at=now,
            request_id=request_id,
        )
        if denied is not None:
            return denied

        subject = self._request_subjects.get(request_id, (None, None))
        try:
            request = self.workflow.decide(
                request_id, approved, actor.principal_id, comment=comment, now=now
            )
        except SelfApprovalError as exc:
            # The workflow has already written its own APPROVAL_DENIED record
            # for this attempt, so nothing is added here -- a second record
            # would double-count the same refusal in the history.
            return DeploymentOutcome(
                allowed=False,
                action=DeploymentAction.DECIDE,
                actor_id=actor.principal_id,
                summary=summary,
                detail=str(exc),
                control=CONTROL_APPROVAL_GATE,
                reason=RefusalReason.SELF_APPROVAL,
                error=exc,
                model_name=subject[0],
                version=subject[1],
                request_id=request_id,
            )
        except RequestNotPendingError as exc:
            return DeploymentOutcome(
                allowed=False,
                action=DeploymentAction.DECIDE,
                actor_id=actor.principal_id,
                summary=summary,
                detail=str(exc),
                control=CONTROL_APPROVAL_GATE,
                reason=RefusalReason.REQUEST_NOT_PENDING,
                error=exc,
                model_name=subject[0],
                version=subject[1],
                request_id=request_id,
            )
        except UnknownRequestError as exc:
            return self._refuse(
                action=DeploymentAction.DECIDE,
                actor_id=actor.principal_id,
                summary=summary,
                detail=str(exc),
                control=CONTROL_APPROVAL_GATE,
                reason=RefusalReason.NO_APPROVAL_REQUEST,
                error=exc,
                governance_action=GovernanceAction.APPROVAL_DENIED,
                subject=request_id,
                subject_kind=SubjectKind.APPROVAL_REQUEST,
                at=now,
                request_id=request_id,
            )

        return DeploymentOutcome(
            allowed=True,
            action=DeploymentAction.DECIDE,
            actor_id=actor.principal_id,
            summary=summary,
            detail=describe_request(request),
            model_name=subject[0],
            version=subject[1],
            request_id=request_id,
        )

    def rollback(
        self,
        model_name: str,
        *,
        actor: Principal,
        at: datetime | None = None,
    ) -> DeploymentOutcome:
        """Restore production to the version it served before the current one.

        Deliberately not gated on an approval request: a rollback is the
        control you need to be able to reach at 3am, and the version it
        restores was itself approved on the way in.

        Args:
            model_name: Logical model name.
            actor: Principal performing the rollback.
            at: Timestamp stamped on any refusal record.

        Returns:
            A :class:`DeploymentOutcome` naming the restored version, or a
            refusal if the actor lacks ``config:manage`` or there is no
            previous production version.
        """
        summary = f"roll back production of {model_name}"
        denied = self._authorize(
            actor,
            PRODUCTION_PERMISSION,
            action=DeploymentAction.ROLLBACK,
            summary=summary,
            governance_action=GovernanceAction.PRODUCTION_ROLLED_BACK,
            subject=model_name,
            subject_kind=SubjectKind.MODEL,
            model_name=model_name,
            at=at,
        )
        if denied is not None:
            return denied

        try:
            restored = self.registry.rollback_production(model_name, actor_id=actor.principal_id)
        except RollbackError as exc:
            return self._refuse(
                action=DeploymentAction.ROLLBACK,
                actor_id=actor.principal_id,
                summary=summary,
                detail=str(exc),
                control=CONTROL_REGISTRY,
                reason=RefusalReason.NO_PREVIOUS_PRODUCTION,
                error=exc,
                governance_action=GovernanceAction.PRODUCTION_ROLLED_BACK,
                subject=model_name,
                subject_kind=SubjectKind.MODEL,
                model_name=model_name,
                at=at,
            )

        return DeploymentOutcome(
            allowed=True,
            action=DeploymentAction.ROLLBACK,
            actor_id=actor.principal_id,
            summary=summary,
            detail=f"production restored to v{restored.version}",
            model_name=model_name,
            version=restored.version,
            stage=restored.stage,
        )

    # ----------------------------------------------------------------- reads

    def production_version(self, model_name: str) -> ModelVersion | None:
        """Return the version currently in production, if there is one.

        Args:
            model_name: Logical model name.

        Returns:
            The production :class:`~cockpit.governance.model_versioning.ModelVersion`,
            or None when the model has never reached production.
        """
        found = self.registry.versions_at_stage(model_name, ModelStage.PRODUCTION)
        return found[0] if found else None

    def production_count(self, model_name: str) -> int:
        """Count how many versions of a model sit in production.

        The invariant this exists to assert is that the answer is never
        greater than one.

        Args:
            model_name: Logical model name.

        Returns:
            The number of versions currently at ``PRODUCTION``.
        """
        return len(self.registry.versions_at_stage(model_name, ModelStage.PRODUCTION))

    def verify_trail(self) -> ChainVerification:
        """Verify the governance trail's hash chain.

        Returns:
            A :class:`~cockpit.security.audit_logging.ChainVerification`
            describing the result and, on failure, where it broke.
        """
        return self.trail.verify()

    # -------------------------------------------------------------- internal

    def _check_production_gate(
        self,
        model_name: str,
        version: str,
        request_id: str | None,
        *,
        actor: Principal,
        summary: str,
        at: datetime | None,
    ) -> DeploymentOutcome | None:
        """Verify an approved, correctly bound request backs a production move.

        Args:
            model_name: Model being promoted.
            version: Version being promoted.
            request_id: Request offered as authorization, if any.
            actor: Principal attempting the promotion.
            summary: One-line description of the attempt.
            at: Timestamp stamped on any refusal record.

        Returns:
            None if the gate opens, otherwise the refusing
            :class:`DeploymentOutcome`.
        """
        common = {
            "action": DeploymentAction.PROMOTE,
            "actor_id": actor.principal_id,
            "summary": summary,
            "control": CONTROL_APPROVAL_GATE,
            "governance_action": GovernanceAction.STAGE_PROMOTED,
            "subject": model_name,
            "subject_kind": SubjectKind.MODEL,
            "model_name": model_name,
            "version": version,
            "at": at,
            "request_id": request_id,
        }

        if request_id is None:
            return self._refuse(
                detail=(
                    "A production promotion requires an approved approval request; "
                    "none was supplied."
                ),
                reason=RefusalReason.NO_APPROVAL_REQUEST,
                **common,
            )

        try:
            request = self.workflow.get(request_id)
        except UnknownRequestError as exc:
            return self._refuse(
                detail=str(exc), reason=RefusalReason.NO_APPROVAL_REQUEST, error=exc, **common
            )

        bound = self._request_subjects.get(request_id)
        if bound != (model_name, version):
            described = f"{bound[0]} v{bound[1]}" if bound else "an unrecorded subject"
            return self._refuse(
                detail=(
                    f"Request {request_id} was opened for {described}, "
                    f"not {model_name} v{version}."
                ),
                reason=RefusalReason.REQUEST_SUBJECT_MISMATCH,
                **common,
            )

        if request.status is not ApprovalStatus.APPROVED:
            return self._refuse(
                detail=(
                    f"Request {request_id} is {request.status} "
                    f"({request.approval_count} of {request.required_approvals} "
                    "distinct approvals); production stays closed."
                ),
                reason=RefusalReason.APPROVAL_NOT_GRANTED,
                **common,
            )

        return None

    def _authorize(
        self,
        actor: Principal,
        permission: Permission,
        *,
        action: DeploymentAction,
        summary: str,
        governance_action: GovernanceAction,
        subject: str,
        subject_kind: SubjectKind,
        at: datetime | None,
        model_name: str | None = None,
        version: str | None = None,
        request_id: str | None = None,
    ) -> DeploymentOutcome | None:
        """Check a permission, producing a recorded refusal when it is absent.

        Args:
            actor: Principal attempting the action.
            permission: Permission the action requires.
            action: What was attempted.
            summary: One-line description of the attempt.
            governance_action: Action to record the denial under.
            subject: Trail subject for the denial record.
            subject_kind: How to interpret ``subject``.
            at: Timestamp for the denial record.
            model_name: Model concerned, if any.
            version: Version concerned, if any.
            request_id: Request concerned, if any.

        Returns:
            None if authorized, otherwise the refusing
            :class:`DeploymentOutcome`.
        """
        try:
            require_permission(actor, permission)
        except AuthorizationError as exc:
            return self._refuse(
                action=action,
                actor_id=actor.principal_id,
                summary=summary,
                detail=f"{exc} Required: '{permission}'.",
                control=CONTROL_RBAC,
                reason=RefusalReason.NOT_AUTHORIZED,
                error=exc,
                governance_action=governance_action,
                subject=subject,
                subject_kind=subject_kind,
                model_name=model_name,
                version=version,
                request_id=request_id,
                at=at,
            )
        return None

    def _refuse(
        self,
        *,
        action: DeploymentAction,
        actor_id: str,
        summary: str,
        detail: str,
        control: str,
        reason: RefusalReason,
        governance_action: GovernanceAction,
        subject: str,
        subject_kind: SubjectKind,
        at: datetime | None,
        error: Exception | None = None,
        model_name: str | None = None,
        version: str | None = None,
        stage: ModelStage | None = None,
        request_id: str | None = None,
    ) -> DeploymentOutcome:
        """Record a refusal on the trail and build the outcome describing it.

        Args:
            action: What was attempted.
            actor_id: Principal that attempted it.
            summary: One-line description of the attempt.
            detail: Why it was refused.
            control: Name of the control that refused.
            reason: Machine-readable refusal reason.
            governance_action: Action to record the denial under.
            subject: Trail subject for the denial record.
            subject_kind: How to interpret ``subject``.
            at: Timestamp for the denial record.
            error: Originating exception, if a framework raised one.
            model_name: Model concerned, if any.
            version: Version concerned, if any.
            stage: Stage the version is in, if known.
            request_id: Request concerned, if any.

        Returns:
            The refusing :class:`DeploymentOutcome`.
        """
        details: dict[str, str] = {"reason": str(reason), "control": control}
        if version is not None:
            details["version"] = version
        if request_id is not None:
            details["request_id"] = request_id

        self.trail.record(
            actor_id,
            governance_action,
            subject,
            subject_kind=subject_kind,
            before=str(stage) if stage is not None else None,
            outcome=OUTCOME_DENIED,
            details=details,
            timestamp=at,
        )
        return DeploymentOutcome(
            allowed=False,
            action=action,
            actor_id=actor_id,
            summary=summary,
            detail=detail,
            control=control,
            reason=reason,
            error=error,
            model_name=model_name,
            version=version,
            stage=stage,
            request_id=request_id,
        )


def describe_request(request: ApprovalRequest) -> str:
    """Summarize an approval request's progress in one line.

    Args:
        request: The request to describe.

    Returns:
        A line naming the status, the distinct approvals recorded, and the
        threshold, e.g. ``"approved: 2 of 2 distinct approvals (qa-lena,
        sre-marcus)"``.
    """
    who = ", ".join(sorted(request.approvers)) or "nobody"
    return (
        f"{request.status}: {request.approval_count} of {request.required_approvals} "
        f"distinct approvals ({who})"
    )


__all__ = [
    "APPROVAL_PERMISSION",
    "CONTROL_APPROVAL_GATE",
    "CONTROL_RBAC",
    "CONTROL_REGISTRY",
    "CONTROL_TRANSITION_MAP",
    "PRODUCTION_PERMISSION",
    "STAGE_PERMISSION",
    "DeploymentAction",
    "DeploymentOutcome",
    "GovernedDeployment",
    "RefusalReason",
    "describe_request",
]
