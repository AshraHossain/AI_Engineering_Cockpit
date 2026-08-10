"""A scripted release, including every way it is allowed to fail.

The point of a governance demo is not the happy path -- it is watching the
controls refuse. This scenario walks one model through two production
releases and a rollback, and along the way deliberately attempts six things
that must not work:

1. shipping straight from development to production,
2. promoting to production with no approval request at all,
3. approving your own request,
4. promoting while the request is still short of its 2-of-2 threshold
   (after the same approver votes twice),
5. the requesting engineer executing the production promotion,
6. spending an approved request on a version it was not opened for.

Everything is deterministic: timestamps come from an injected clock, request
ids are fixed in dry-run mode, and nothing sleeps. Run the same scenario
twice and the transcript is identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from cockpit.governance.approval_workflow import ApprovalWorkflow
from cockpit.governance.audit_trail import GovernanceTrail, get_default_trail
from cockpit.governance.model_versioning import ModelRegistry, ModelStage, ModelVersion
from cockpit.security.access_control import Principal, Role
from cockpit.security.audit_logging import ChainVerification
from deployment import DeploymentOutcome, GovernedDeployment

MODEL_NAME = "gemini-flash"
VERSION_ONE = "2026-08-01"
VERSION_TWO = "2026-09-01"

PROVIDER_MODEL_ID = "gemini-2.5-flash"

# Fixed so a dry run is byte-for-byte reproducible.
FIXED_BASE_TIME = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
CLOCK_STEP = timedelta(minutes=7)

FIXED_REQUEST_ONE = "req-promote-v1"
FIXED_REQUEST_TWO = "req-promote-v2"

# The cast. Roles are what make segregation of duties real: ENGINEER holds
# `project:write` and can move a candidate to staging, but only the RELEASE
# MANAGER holds `config:manage` and can put anything in front of traffic.
ENGINEER = Principal("dev-priya", frozenset({Role.DEVELOPER}))
REVIEWER_ONE = Principal("sre-marcus", frozenset({Role.DEVELOPER}))
REVIEWER_TWO = Principal("qa-lena", frozenset({Role.DEVELOPER}))
RELEASE_MANAGER = Principal("rel-omar", frozenset({Role.ADMIN}))


@dataclass
class ScenarioClock:
    """A deterministic clock that advances a fixed step per reading.

    Injected everywhere a timestamp is needed, so the scenario never calls
    ``datetime.now`` and never sleeps.

    Attributes:
        base: The first timestamp returned.
        step: How far the clock advances between readings.
    """

    base: datetime
    step: timedelta = CLOCK_STEP
    _ticks: int = field(default=0, init=False)

    def __call__(self) -> datetime:
        """Return the next timestamp and advance the clock.

        Returns:
            ``base + step * n`` for the nth call, starting at n = 0.
        """
        moment = self.base + self.step * self._ticks
        self._ticks += 1
        return moment


@dataclass(frozen=True)
class ScenarioStep:
    """One narrated step of the scripted release.

    Attributes:
        index: One-based position in the script.
        narrative: Why this step is in the story -- the thing a reader
            should take away, not a restatement of the call.
        outcome: What the pipeline actually did.
    """

    index: int
    narrative: str
    outcome: DeploymentOutcome


@dataclass(frozen=True)
class ScenarioResult:
    """Everything a transcript needs to report.

    Attributes:
        model_name: The model the scenario released.
        steps: The narrated steps, in order.
        production_version: The version in production when the script ends.
        production_count: How many versions ended up at ``PRODUCTION``.
        trail_record_count: Number of records on the governance trail.
        verification: Result of verifying that trail's hash chain.
    """

    model_name: str
    steps: tuple[ScenarioStep, ...]
    production_version: ModelVersion | None
    production_count: int
    trail_record_count: int
    verification: ChainVerification

    @property
    def allowed_count(self) -> int:
        """How many steps were allowed.

        Returns:
            The number of steps whose outcome took effect.
        """
        return sum(1 for step in self.steps if step.outcome.allowed)

    @property
    def refused_count(self) -> int:
        """How many steps were refused by a control.

        Returns:
            The number of steps a control blocked.
        """
        return sum(1 for step in self.steps if not step.outcome.allowed)


@dataclass
class _Script:
    """Accumulator that numbers steps as they are appended.

    Attributes:
        steps: The steps recorded so far.
    """

    steps: list[ScenarioStep] = field(default_factory=list)

    def add(self, narrative: str, outcome: DeploymentOutcome) -> DeploymentOutcome:
        """Record one step and return its outcome for chaining.

        Args:
            narrative: Why this step is in the story.
            outcome: What the pipeline did.

        Returns:
            The same ``outcome``, so callers can read ``request_id`` off it.
        """
        self.steps.append(ScenarioStep(len(self.steps) + 1, narrative, outcome))
        return outcome


def build_deployment(
    *,
    registry: ModelRegistry | None = None,
    workflow: ApprovalWorkflow | None = None,
    trail: GovernanceTrail | None = None,
) -> GovernedDeployment:
    """Assemble a pipeline, defaulting the trail to the shared default one.

    The registry and workflow write their own events to the module-level
    default trail, so pointing the orchestrator at anything else would split
    one release's history across two chains.

    Args:
        registry: Registry to use; a fresh one when omitted.
        workflow: Workflow to use; a fresh one when omitted.
        trail: Trail to record refusals on; the process default when omitted.

    Returns:
        A wired :class:`~deployment.GovernedDeployment`.
    """
    return GovernedDeployment(
        registry=registry or ModelRegistry(),
        workflow=workflow or ApprovalWorkflow(),
        trail=trail or get_default_trail(),
    )


def run_scenario(
    deployment: GovernedDeployment | None = None,
    *,
    base_time: datetime | None = None,
    deterministic_ids: bool = True,
) -> ScenarioResult:
    """Run the scripted release and return every step, allowed or refused.

    Args:
        deployment: Pipeline to drive; a freshly built one when omitted.
        base_time: First timestamp of the injected clock. Defaults to
            :data:`FIXED_BASE_TIME` so a dry run is reproducible; pass
            ``datetime.now(UTC)`` for a wall-clock run.
        deterministic_ids: Use fixed approval request ids. Set False to let
            the workflow generate UUID-based ids.

    Returns:
        A :class:`ScenarioResult` describing the whole run.
    """
    pipeline = deployment or build_deployment()
    clock = ScenarioClock(base_time or FIXED_BASE_TIME)
    script = _Script()

    request_one_id = FIXED_REQUEST_ONE if deterministic_ids else None
    request_two_id = FIXED_REQUEST_TWO if deterministic_ids else None

    # --- Act one: the first release ------------------------------------
    script.add(
        "a new candidate enters the registry, always in 'development'",
        pipeline.register(
            MODEL_NAME,
            VERSION_ONE,
            actor=ENGINEER,
            provider_model_id=PROVIDER_MODEL_ID,
            config_fingerprint="sha256:8f1c2a",
            at=clock(),
        ),
    )
    script.add(
        "straight to prod: the transition map has no development -> production edge",
        pipeline.promote(
            MODEL_NAME,
            VERSION_ONE,
            ModelStage.PRODUCTION,
            actor=RELEASE_MANAGER,
            at=clock(),
        ),
    )
    script.add(
        "the only legal way forward is through staging",
        pipeline.promote(MODEL_NAME, VERSION_ONE, ModelStage.STAGING, actor=ENGINEER, at=clock()),
    )
    script.add(
        "staging -> production is legal, but not on its own: no request, no deploy",
        pipeline.promote(
            MODEL_NAME,
            VERSION_ONE,
            ModelStage.PRODUCTION,
            actor=RELEASE_MANAGER,
            at=clock(),
        ),
    )
    first_request = script.add(
        "so the engineer asks, and two distinct sign-offs are required",
        pipeline.request_promotion(
            MODEL_NAME,
            VERSION_ONE,
            requested_by=ENGINEER,
            required_approvals=2,
            request_id=request_one_id,
            created_at=clock(),
        ),
    )
    request_one = first_request.request_id or ""

    script.add(
        "segregation of duties: the requester cannot sign their own request",
        pipeline.decide(request_one, True, actor=ENGINEER, now=clock()),
    )
    script.add(
        "one genuine approval lands: 1 of 2",
        pipeline.decide(
            request_one, True, actor=REVIEWER_ONE, comment="eval suite green", now=clock()
        ),
    )
    script.add(
        "the same approver votes again -- recorded, but one principal counts once",
        pipeline.decide(request_one, True, actor=REVIEWER_ONE, comment="still happy", now=clock()),
    )
    script.add(
        "so the gate is still shut at 1 of 2",
        pipeline.promote(
            MODEL_NAME,
            VERSION_ONE,
            ModelStage.PRODUCTION,
            actor=RELEASE_MANAGER,
            request_id=request_one,
            at=clock(),
        ),
    )
    script.add(
        "a second, distinct approver satisfies the threshold",
        pipeline.decide(
            request_one, True, actor=REVIEWER_TWO, comment="rollback plan reviewed", now=clock()
        ),
    )
    script.add(
        "approved is not the same as authorized: the engineer still cannot ship it",
        pipeline.promote(
            MODEL_NAME,
            VERSION_ONE,
            ModelStage.PRODUCTION,
            actor=ENGINEER,
            request_id=request_one,
            at=clock(),
        ),
    )
    script.add(
        "the release manager holds config:manage, and now the gate opens",
        pipeline.promote(
            MODEL_NAME,
            VERSION_ONE,
            ModelStage.PRODUCTION,
            actor=RELEASE_MANAGER,
            request_id=request_one,
            at=clock(),
        ),
    )

    # --- Act two: the second release supersedes the first ---------------
    script.add(
        "a successor candidate is registered",
        pipeline.register(
            MODEL_NAME,
            VERSION_TWO,
            actor=ENGINEER,
            provider_model_id=PROVIDER_MODEL_ID,
            config_fingerprint="sha256:31d90b",
            at=clock(),
        ),
    )
    script.add(
        "same road: development -> staging first",
        pipeline.promote(MODEL_NAME, VERSION_TWO, ModelStage.STAGING, actor=ENGINEER, at=clock()),
    )
    second_request = script.add(
        "a second request, bound to v2026-09-01 and nothing else",
        pipeline.request_promotion(
            MODEL_NAME,
            VERSION_TWO,
            requested_by=ENGINEER,
            required_approvals=2,
            request_id=request_two_id,
            created_at=clock(),
        ),
    )
    request_two = second_request.request_id or ""

    script.add(
        "first sign-off on the successor",
        pipeline.decide(
            request_two, True, actor=REVIEWER_ONE, comment="regression suite green", now=clock()
        ),
    )
    script.add(
        "second sign-off: 2 of 2",
        pipeline.decide(
            request_two, True, actor=REVIEWER_TWO, comment="canary metrics fine", now=clock()
        ),
    )
    script.add(
        "promoting the successor demotes the incumbent in the same locked step",
        pipeline.promote(
            MODEL_NAME,
            VERSION_TWO,
            ModelStage.PRODUCTION,
            actor=RELEASE_MANAGER,
            request_id=request_two,
            at=clock(),
        ),
    )

    # --- Act three: an approval is not a bearer token -------------------
    script.add(
        "reusing the successor's approval to reinstate the old version",
        pipeline.promote(
            MODEL_NAME,
            VERSION_ONE,
            ModelStage.PRODUCTION,
            actor=RELEASE_MANAGER,
            request_id=request_two,
            at=clock(),
        ),
    )
    script.add(
        "the supported way back is a rollback, which needs no fresh approval",
        pipeline.rollback(MODEL_NAME, actor=RELEASE_MANAGER, at=clock()),
    )

    return ScenarioResult(
        model_name=MODEL_NAME,
        steps=tuple(script.steps),
        production_version=pipeline.production_version(MODEL_NAME),
        production_count=pipeline.production_count(MODEL_NAME),
        trail_record_count=len(pipeline.trail.records),
        verification=pipeline.verify_trail(),
    )


__all__ = [
    "CLOCK_STEP",
    "ENGINEER",
    "FIXED_BASE_TIME",
    "FIXED_REQUEST_ONE",
    "FIXED_REQUEST_TWO",
    "MODEL_NAME",
    "PROVIDER_MODEL_ID",
    "RELEASE_MANAGER",
    "REVIEWER_ONE",
    "REVIEWER_TWO",
    "VERSION_ONE",
    "VERSION_TWO",
    "ScenarioClock",
    "ScenarioResult",
    "ScenarioStep",
    "build_deployment",
    "run_scenario",
]
