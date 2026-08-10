"""A policy-enforcing gateway that sits between a caller and a model.

Every request takes the same five steps, and the point of this module is
that four of them can *refuse*:

1. **Authorize** -- the calling :class:`~cockpit.security.access_control.Principal`
   must hold ``Permission.MODEL_INVOKE``. An ``external`` principal is refused
   here, before the prompt is looked at.
2. **Validate input** -- :func:`~cockpit.security.input_security.validate_input`
   runs length, control-character, invisible-character, and prompt-injection
   checks. A hit refuses by default, or is recorded and waved through when
   ``GatewayConfig.refuse_on_injection`` is False.
3. **Invoke** -- the model call, injected as ``generate_fn``. It is reached
   only by a request that survived steps 1 and 2, which is the property the
   test suite pins: on a refusal the injected callable is never called at all.
4. **Sanitize output** -- :func:`~cockpit.security.output_security.mask_pii`
   runs before the text is returned *or* audited. A model that echoes a
   customer's email address must not turn the audit log into a second copy
   of that leak.
5. **Audit** -- every request lands in a tamper-evident
   :class:`~cockpit.security.audit_logging.AuditLog`, allowed or refused.
   Refusals especially: the denied attempts are the ones worth reviewing,
   and a log that only records successes cannot answer "who kept trying?".

The model call is **injected**, so nothing here imports an SDK at module
scope and the whole test suite runs with no API key and no network.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from cockpit.security.access_control import (
    Permission,
    Principal,
    check_permission,
    requires_permission,
)
from cockpit.security.audit_logging import AuditLog, AuditRecord
from cockpit.security.input_security import validate_input
from cockpit.security.output_security import mask_pii, scan_for_pii

logger = logging.getLogger(__name__)

# The model call, narrowed to what the gateway actually needs: text in, text
# out. Deliberately not the SDK's response object -- a fake has to satisfy
# this, and the smaller the contract the less a fake can drift from reality.
GenerateFn = Callable[[str], str]

DEFAULT_MODEL = "gemini-2.5-flash"

# One action name for every request, so an audit query on it returns the
# complete population of model calls rather than only the interesting ones.
AUDIT_ACTION = "model.invoke"


class Stage(StrEnum):
    """A step in the gateway pipeline, used to say where a request stopped.

    Attributes:
        AUTHORIZE: The RBAC check against ``Permission.MODEL_INVOKE``.
        VALIDATE: Input validation and prompt-injection scanning.
        INVOKE: The injected model call.
        SANITIZE: PII masking of the model's response.
    """

    AUTHORIZE = "authorize"
    VALIDATE = "validate"
    INVOKE = "invoke"
    SANITIZE = "sanitize"


class Outcome(StrEnum):
    """How a request ended. Recorded verbatim as the audit ``outcome``.

    Attributes:
        ALLOWED: The request reached the model and a response was returned.
        DENIED: Policy refused the request. No model call was made.
        ERROR: The request was permitted but the model call itself failed.
    """

    ALLOWED = "allowed"
    DENIED = "denied"
    ERROR = "error"


@dataclass(frozen=True)
class GatewayConfig:
    """Policy knobs for a :class:`SecureGateway`.

    Attributes:
        model: Model identifier, used to name the audited resource.
        required_permission: Permission the caller must hold to reach the
            model at all.
        refuse_on_injection: True to refuse a request whose prompt trips
            input validation; False to record the findings and continue.
            Configurable because the right answer differs by deployment --
            an internal tool may prefer a flagged-but-served request, a
            public endpoint almost never does.
        max_prompt_chars: Length ceiling handed to
            :func:`~cockpit.security.input_security.validate_input`.
    """

    model: str = DEFAULT_MODEL
    required_permission: Permission = Permission.MODEL_INVOKE
    refuse_on_injection: bool = True
    max_prompt_chars: int = 8_000

    @property
    def resource(self) -> str:
        """Name the audited resource for this configuration.

        Returns:
            The resource string recorded on every audit entry, e.g.
            ``"model:gemini-2.5-flash"``.
        """
        return f"model:{self.model}"


@dataclass(frozen=True)
class GatewayResult:
    """The outcome of one request through the gateway.

    Attributes:
        outcome: Allowed, denied, or errored.
        refused_at: The :class:`Stage` that refused, or ``None`` when the
            request completed. This is the field to look at first: "denied"
            alone does not tell an operator whether the caller lacked a
            permission or sent a hostile prompt.
        reason: Human-readable explanation of the outcome.
        principal_id: Identifier of the calling principal.
        response: The sanitized model response, or ``None`` if the request
            never reached the model.
        findings: Input-validation problems, if any. Non-empty on an allowed
            request means the prompt was flagged and served anyway.
        pii_categories: PII categories masked out of the response.
        audit_sequence: Position of this request's record in the audit chain.
    """

    outcome: Outcome
    refused_at: Stage | None
    reason: str
    principal_id: str
    response: str | None = None
    findings: tuple[str, ...] = ()
    pii_categories: tuple[str, ...] = ()
    audit_sequence: int = -1

    @property
    def allowed(self) -> bool:
        """Report whether the request reached the model and returned.

        Returns:
            True only for :attr:`Outcome.ALLOWED`.
        """
        return self.outcome is Outcome.ALLOWED


@dataclass
class SecureGateway:
    """Run every model request through authorize/validate/invoke/sanitize/audit.

    Attributes:
        generate_fn: The injected ``prompt -> text`` model call. Reached only
            by a request that passed authorization and validation.
        audit_log: The tamper-evident log every request is appended to.
        config: Policy configuration.
    """

    generate_fn: GenerateFn
    audit_log: AuditLog
    config: GatewayConfig = field(default_factory=GatewayConfig)

    def handle(self, principal: Principal, prompt: str) -> GatewayResult:
        """Process one request end to end.

        Args:
            principal: The authenticated caller.
            prompt: The caller's untrusted prompt text.

        Returns:
            A :class:`GatewayResult` describing the outcome, the reason, and
            the stage that refused if one did. Every return path from this
            method has already written an audit record.

        Raises:
            TypeError: If ``prompt`` is not a string (propagated from the
                input-security scanners, which refuse to guess).
        """
        decision = check_permission(principal, self.config.required_permission)
        if not decision.allowed:
            return self._refuse(
                principal,
                Stage.AUTHORIZE,
                decision.reason,
                metadata={"required_permission": str(self.config.required_permission)},
            )

        findings = tuple(validate_input(prompt, max_length=self.config.max_prompt_chars))
        if findings and self.config.refuse_on_injection:
            return self._refuse(
                principal,
                Stage.VALIDATE,
                "; ".join(findings),
                metadata={"findings": list(findings), "prompt_chars": len(prompt)},
                findings=findings,
            )
        if findings:
            logger.warning(
                "Serving a flagged prompt for principal %r: %s",
                principal.principal_id,
                "; ".join(findings),
            )

        try:
            raw_response = self.generate_fn(prompt)
        except Exception as exc:  # SDKs raise assorted provider-specific errors
            # An error is not a refusal: the request was permitted, the
            # provider failed. It is audited as its own outcome so a provider
            # outage never reads as a wave of policy denials.
            return self._refuse(
                principal,
                Stage.INVOKE,
                f"Model call failed: {exc}",
                metadata={"prompt_chars": len(prompt), "findings": list(findings)},
                findings=findings,
                outcome=Outcome.ERROR,
            )

        safe_response, categories = sanitize_response(raw_response)
        record = self._record(
            principal,
            Outcome.ALLOWED,
            metadata={
                "stage": str(Stage.SANITIZE),
                "prompt_chars": len(prompt),
                "response_chars": len(safe_response),
                "pii_masked": list(categories),
                "findings": list(findings),
            },
        )
        return GatewayResult(
            outcome=Outcome.ALLOWED,
            refused_at=None,
            reason="Request authorized, validated, and served.",
            principal_id=principal.principal_id,
            response=safe_response,
            findings=findings,
            pii_categories=categories,
            audit_sequence=record.sequence,
        )

    def _refuse(
        self,
        principal: Principal,
        stage: Stage,
        reason: str,
        metadata: dict[str, Any],
        *,
        findings: tuple[str, ...] = (),
        outcome: Outcome = Outcome.DENIED,
    ) -> GatewayResult:
        """Audit a request that stopped early and build its result.

        Args:
            principal: The calling principal.
            stage: The stage that refused.
            reason: Why it refused.
            metadata: Structured context for the audit record.
            findings: Input-validation problems, if the refusal came from
                validation.
            outcome: The audited outcome, ``DENIED`` unless the model call
                itself failed.

        Returns:
            The refusal :class:`GatewayResult`, already audited.
        """
        record = self._record(
            principal,
            outcome,
            metadata={"stage": str(stage), "reason": reason, **metadata},
        )
        logger.warning("Refused at %s for principal %r: %s", stage, principal.principal_id, reason)
        return GatewayResult(
            outcome=outcome,
            refused_at=stage,
            reason=reason,
            principal_id=principal.principal_id,
            response=None,
            findings=findings,
            audit_sequence=record.sequence,
        )

    def _record(
        self, principal: Principal, outcome: Outcome, metadata: dict[str, Any]
    ) -> AuditRecord:
        """Append one entry to the tamper-evident chain.

        Args:
            principal: The calling principal, recorded as the actor.
            outcome: The outcome to record.
            metadata: Structured context. The audit framework masks PII in it
                before hashing, so nothing here can turn the log into a leak.

        Returns:
            The appended :class:`~cockpit.security.audit_logging.AuditRecord`.
        """
        return self.audit_log.record(
            actor_id=principal.principal_id,
            action=AUDIT_ACTION,
            resource=self.config.resource,
            outcome=str(outcome),
            metadata=metadata,
        )


def sanitize_response(text: str) -> tuple[str, tuple[str, ...]]:
    """Mask PII in a model response and report what was masked.

    Args:
        text: The raw model output.

    Returns:
        A ``(masked_text, categories)`` pair. ``categories`` is sorted and
        empty when the response was already clean, in which case the text is
        returned unchanged.

    Raises:
        TypeError: If ``text`` is not a string.
    """
    scan = scan_for_pii(text)
    if not scan.has_pii:
        return text, ()
    categories = tuple(sorted({finding.category for finding in scan.findings}))
    return mask_pii(text), categories


@requires_permission(Permission.AUDIT_LOG_READ)
def read_audit_records(principal: Principal, audit_log: AuditLog) -> list[AuditRecord]:
    """Return the audit chain, for a caller allowed to read it.

    Reading the trail is itself a privileged action -- the denied attempts it
    contains say who probed the system and with what. The decorator denies a
    call with no principal at all, so this cannot be reached by forgetting an
    argument.

    Args:
        principal: The caller, which must hold ``Permission.AUDIT_LOG_READ``.
        audit_log: The log to read.

    Returns:
        A copy of the records, oldest first.

    Raises:
        AuthorizationError: If the caller does not hold the permission.
    """
    return list(audit_log.records)


def build_gemini_generate_fn(api_key: str, model: str = DEFAULT_MODEL) -> GenerateFn:
    """Build a live ``generate_fn`` backed by the google-genai SDK.

    The SDK import is deliberately lazy (inside the function) so importing
    this module never requires ``google-genai`` to be installed, and the test
    suite never touches the network.

    Args:
        api_key: Gemini API key to authenticate with.
        model: Model identifier to send requests to.

    Returns:
        A ``prompt -> text`` callable suitable for :class:`SecureGateway`.
    """
    from google import genai

    client = genai.Client(api_key=api_key)

    def generate(prompt: str) -> str:
        response = client.models.generate_content(model=model, contents=prompt)
        return response.text or ""

    return generate
