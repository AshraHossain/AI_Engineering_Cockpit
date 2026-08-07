"""Behavioral threat detection over a stream of security events.

Where :mod:`cockpit.security.input_security` inspects a single message,
this module looks at *sequences*: five failed logins in a minute, a
thousand record reads from an account that normally reads ten, an action
nobody with that role should be able to attempt. None of those are
suspicious one at a time, which is exactly why per-request filters miss
them.

Design constraints that shaped the API:

* **Detectors are pure functions.** Each takes a sequence of events plus
  its threshold and window and returns findings. No global state, no
  hidden history buffer -- the caller owns retention, and a detector can be
  re-run over the same window with a different threshold to tune it.
* **Time is injectable.** Every detector accepts ``now`` and
  :class:`ThreatDetector` accepts a ``clock``. Windowing logic that reads
  the wall clock can only be tested by sleeping, and tests that sleep get
  deleted the first time they flake.
* **Evidence is mandatory.** A :class:`ThreatFinding` carries the specific
  events that triggered it. An alert that says "anomalous activity for
  user 42" and nothing else cannot be investigated, so it gets muted, and
  a muted detector is worse than no detector because it looks like cover.

Thresholds here are *starting points* chosen to be defensible, not tuned
against this platform's traffic; see each constant for its rationale. They
are constructor arguments precisely because they should be re-tuned
against real baselines before anyone pages on them.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)


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


class ThreatCategory(StrEnum):
    """The kind of behavior a finding describes.

    Attributes:
        BRUTE_FORCE: Repeated failed authentication for one actor.
        DATA_EXFILTRATION: Unusual volume of reads/exports by one actor.
        PRIVILEGE_ESCALATION: Actions outside an actor's granted role, or
            sensitive access immediately following a role change.
        ANOMALOUS_RATE: Request rate far above the actor's own baseline.
    """

    BRUTE_FORCE = "brute_force"
    DATA_EXFILTRATION = "data_exfiltration"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    ANOMALOUS_RATE = "anomalous_rate"


# Ordering for aggregation and sorting. StrEnum compares as a string, and
# "critical" < "high" < "low" alphabetically -- exactly backwards -- so
# severity must never be derived from the member value itself.
_LEVEL_ORDER: dict[ThreatLevel, int] = {
    ThreatLevel.LOW: 0,
    ThreatLevel.MEDIUM: 1,
    ThreatLevel.HIGH: 2,
    ThreatLevel.CRITICAL: 3,
}


class SecurityEvent(Protocol):
    """The minimum an event must expose for the detectors to work.

    Deliberately a structural :class:`~typing.Protocol` rather than an
    import of ``cockpit.security.audit_logging.AuditEvent``. That module is
    being written in parallel with this one, and a hard import would couple
    this module's correctness to the exact field set that lands there. A
    protocol says what is actually needed -- when, who, what, on what, with
    what result -- and ``AuditEvent`` satisfies it structurally today
    without either module depending on the other. It also lets a caller
    feed in gateway access logs or an ORM row without adapting them first.

    Attributes:
        timestamp: When the event occurred. Naive values are read as UTC.
        actor_id: Identifier of the principal responsible.
        action: The action attempted, e.g. "login" or "export".
        resource: Identifier of the affected resource.
        outcome: Result of the action, e.g. "success" or "denied".
    """

    @property
    def timestamp(self) -> datetime:
        """When the event occurred."""
        ...

    @property
    def actor_id(self) -> str:
        """Identifier of the principal responsible."""
        ...

    @property
    def action(self) -> str:
        """The action attempted."""
        ...

    @property
    def resource(self) -> str:
        """Identifier of the affected resource."""
        ...

    @property
    def outcome(self) -> str:
        """Result of the action."""
        ...


# --- Vocabulary ------------------------------------------------------------
# Deployments name their actions differently, so every set below is an
# argument with a default rather than a hardcoded constant.

AUTH_ACTIONS: frozenset[str] = frozenset(
    {"login", "signin", "sign_in", "authenticate", "auth", "token_refresh", "mfa_verify"}
)
FAILURE_OUTCOMES: frozenset[str] = frozenset(
    {"failure", "failed", "denied", "error", "invalid_credentials", "rejected"}
)
READ_ACTIONS: frozenset[str] = frozenset(
    {"read", "export", "download", "query", "fetch", "list", "search"}
)
SENSITIVE_ACTIONS: frozenset[str] = frozenset(
    {"export", "download", "delete", "read_secret", "key_access", "admin_action", "impersonate"}
)
ROLE_CHANGE_ACTIONS: frozenset[str] = frozenset(
    {"role_change", "grant_role", "assign_role", "privilege_grant", "permission_change"}
)

# --- Default thresholds ----------------------------------------------------

# Five failures in five minutes. Five is the lockout threshold most account
# policies already use, so it is a number security teams recognise; the
# five-minute window is what separates a script from a human who mistyped a
# password twice this morning and once after lunch.
DEFAULT_BRUTE_FORCE_THRESHOLD = 5
DEFAULT_BRUTE_FORCE_WINDOW = timedelta(minutes=5)

# Fifty successful reads/exports in five minutes. A person working through
# records rarely sustains ten a minute; tooling doing a bulk pull clears
# this in seconds. Set well above human peak so the finding means "machine",
# not "busy analyst".
DEFAULT_EXFILTRATION_THRESHOLD = 50
DEFAULT_EXFILTRATION_WINDOW = timedelta(minutes=5)

# Sensitive access within a minute of a role change. Legitimate role changes
# are followed by a human logging in, not by an immediate privileged read.
DEFAULT_ESCALATION_WINDOW = timedelta(minutes=1)

# Five times the actor's own historical rate, over a one-minute window.
# Compared against each actor's own baseline rather than a global ceiling,
# because a batch service and an interactive user have legitimately
# different normal rates. The floors below stop the ratio from firing on
# noise: ten events minimum in the window (2 requests against a baseline of
# 0.2 is a 10x ratio and means nothing), and five baseline events minimum
# before any baseline is trusted at all -- a brand new actor has no normal.
DEFAULT_RATE_MULTIPLIER = 5.0
DEFAULT_RATE_WINDOW = timedelta(minutes=1)
DEFAULT_RATE_MIN_EVENTS = 10
DEFAULT_RATE_MIN_BASELINE_EVENTS = 5


@dataclass(frozen=True)
class ThreatFinding:
    """A detected behavioral threat, with the evidence that produced it.

    Attributes:
        category: What kind of behavior fired.
        level: Assessed severity.
        actor_id: The principal the finding is about.
        evidence: The specific events that triggered the finding, oldest
            first. Required, never empty: this is what an analyst opens
            first, and a finding that cannot be traced back to concrete
            events is noise dressed as signal.
        explanation: Human-readable description including the observed
            value and the threshold it crossed, so the reader can judge how
            close to the line it was.
        detected_at: The evaluation time the detector ran against (UTC).
    """

    category: ThreatCategory
    level: ThreatLevel
    actor_id: str
    evidence: tuple[SecurityEvent, ...]
    explanation: str
    detected_at: datetime


@dataclass(frozen=True)
class ThreatSignal:
    """A single detected threat indicator.

    Retained for the pre-Tier-3 API surface. :class:`ThreatFinding` is the
    richer type detectors return; this is a flattened view for callers that
    only want a severity and a sentence. Prefer ``ThreatFinding`` -- this
    type drops the evidence.

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

    @classmethod
    def from_finding(cls, finding: ThreatFinding) -> ThreatSignal:
        """Flatten a :class:`ThreatFinding` into a signal.

        Args:
            finding: The finding to convert.

        Returns:
            An equivalent :class:`ThreatSignal`, without the evidence.
        """
        return cls(
            signal_id=uuid.uuid4().hex,
            detected_at=finding.detected_at,
            source_id=finding.actor_id,
            level=finding.level,
            description=finding.explanation,
        )


def _to_utc(moment: datetime) -> datetime:
    """Normalize a datetime to an aware UTC datetime.

    Naive values are read as UTC rather than local time; comparing a naive
    event timestamp against an aware window bound raises, and silently
    shifting by the host's offset would quietly move events in and out of
    windows depending on which machine ran the detector.

    Args:
        moment: The datetime to normalize.

    Returns:
        The equivalent timezone-aware UTC datetime.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _utc_now() -> datetime:
    """Return the current time in UTC.

    Returns:
        An aware UTC datetime.
    """
    return datetime.now(UTC)


def _resolve_now(now: datetime | None) -> datetime:
    """Resolve an optional evaluation time to an aware UTC datetime.

    Args:
        now: Caller-supplied evaluation time, or None for the wall clock.

    Returns:
        The evaluation time in UTC.
    """
    return _to_utc(now) if now is not None else _utc_now()


def _in_window(
    events: Iterable[SecurityEvent], now: datetime, window: timedelta
) -> list[SecurityEvent]:
    """Select events falling inside a trailing window ending at ``now``.

    The window is ``(now - window, now]`` on the closing edge and inclusive
    at the start bound, so an event exactly ``window`` old still counts.

    Args:
        events: Events to filter.
        now: End of the window (already UTC).
        window: Window length. Must be positive.

    Returns:
        Matching events sorted oldest first.

    Raises:
        ValueError: If ``window`` is not positive.
    """
    if window <= timedelta(0):
        raise ValueError("window must be a positive timedelta.")
    start = now - window
    selected = [event for event in events if start <= _to_utc(event.timestamp) <= now]
    selected.sort(key=lambda event: _to_utc(event.timestamp))
    return selected


def _group_by_actor(events: Iterable[SecurityEvent]) -> dict[str, list[SecurityEvent]]:
    """Bucket events by actor id, preserving order within each bucket.

    Args:
        events: Events to group.

    Returns:
        Mapping of actor id to that actor's events.
    """
    grouped: dict[str, list[SecurityEvent]] = {}
    for event in events:
        grouped.setdefault(event.actor_id, []).append(event)
    return grouped


def detect_brute_force(
    events: Sequence[SecurityEvent],
    *,
    threshold: int = DEFAULT_BRUTE_FORCE_THRESHOLD,
    window: timedelta = DEFAULT_BRUTE_FORCE_WINDOW,
    now: datetime | None = None,
    auth_actions: Collection[str] = AUTH_ACTIONS,
    failure_outcomes: Collection[str] = FAILURE_OUTCOMES,
) -> list[ThreatFinding]:
    """Detect repeated failed authentication by a single actor.

    Counts per actor rather than globally: a hundred failures spread over a
    hundred accounts is a different attack (password spraying) with a
    different response, and folding them together would hide both.

    Args:
        events: The event stream to inspect.
        threshold: Failures within the window that trigger a finding. The
            finding fires at ``>= threshold``.
        window: Trailing window ending at ``now``.
        now: Evaluation time; defaults to the current UTC time.
        auth_actions: Action names counted as authentication attempts.
        failure_outcomes: Outcome names counted as failures.

    Returns:
        One finding per offending actor, or an empty list.

    Raises:
        ValueError: If ``threshold`` is not positive or ``window`` is not a
            positive timedelta.
    """
    if threshold <= 0:
        raise ValueError("threshold must be positive.")

    evaluated_at = _resolve_now(now)
    candidates = [
        event
        for event in _in_window(events, evaluated_at, window)
        if event.action in auth_actions and event.outcome in failure_outcomes
    ]

    findings: list[ThreatFinding] = []
    for actor_id, actor_events in _group_by_actor(candidates).items():
        if len(actor_events) < threshold:
            continue
        # Triple the threshold reads as sustained automation rather than a
        # user who kept trying, so it escalates past "likely" to "active".
        level = ThreatLevel.CRITICAL if len(actor_events) >= threshold * 3 else ThreatLevel.HIGH
        findings.append(
            ThreatFinding(
                category=ThreatCategory.BRUTE_FORCE,
                level=level,
                actor_id=actor_id,
                evidence=tuple(actor_events),
                explanation=(
                    f"Actor {actor_id!r} recorded {len(actor_events)} failed authentication "
                    f"attempts in the last {int(window.total_seconds())}s "
                    f"(threshold {threshold})."
                ),
                detected_at=evaluated_at,
            )
        )
    return findings


def detect_data_exfiltration(
    events: Sequence[SecurityEvent],
    *,
    threshold: int = DEFAULT_EXFILTRATION_THRESHOLD,
    window: timedelta = DEFAULT_EXFILTRATION_WINDOW,
    now: datetime | None = None,
    read_actions: Collection[str] = READ_ACTIONS,
    failure_outcomes: Collection[str] = FAILURE_OUTCOMES,
) -> list[ThreatFinding]:
    """Detect an unusual volume of successful reads or exports by one actor.

    Only non-failed reads count. A burst of *denied* reads is a probing or
    brute-force pattern, not exfiltration -- nothing left the system -- and
    counting it here would attach the wrong response to it.

    Args:
        events: The event stream to inspect.
        threshold: Reads within the window that trigger a finding. Fires at
            ``>= threshold``.
        window: Trailing window ending at ``now``.
        now: Evaluation time; defaults to the current UTC time.
        read_actions: Action names counted as data access.
        failure_outcomes: Outcome names treated as "nothing was returned".

    Returns:
        One finding per offending actor, or an empty list.

    Raises:
        ValueError: If ``threshold`` is not positive or ``window`` is not a
            positive timedelta.
    """
    if threshold <= 0:
        raise ValueError("threshold must be positive.")

    evaluated_at = _resolve_now(now)
    candidates = [
        event
        for event in _in_window(events, evaluated_at, window)
        if event.action in read_actions and event.outcome not in failure_outcomes
    ]

    findings: list[ThreatFinding] = []
    for actor_id, actor_events in _group_by_actor(candidates).items():
        if len(actor_events) < threshold:
            continue
        distinct_resources = len({event.resource for event in actor_events})
        # Breadth is the tell. Reading one record repeatedly is a retry
        # loop; sweeping many distinct resources is a harvest.
        level = (
            ThreatLevel.CRITICAL
            if len(actor_events) >= threshold * 2 and distinct_resources >= threshold
            else ThreatLevel.HIGH
        )
        findings.append(
            ThreatFinding(
                category=ThreatCategory.DATA_EXFILTRATION,
                level=level,
                actor_id=actor_id,
                evidence=tuple(actor_events),
                explanation=(
                    f"Actor {actor_id!r} performed {len(actor_events)} read/export operations "
                    f"across {distinct_resources} distinct resources in the last "
                    f"{int(window.total_seconds())}s (threshold {threshold})."
                ),
                detected_at=evaluated_at,
            )
        )
    return findings


def detect_privilege_escalation(
    events: Sequence[SecurityEvent],
    *,
    allowed_actions: Mapping[str, Collection[str]] | None = None,
    sensitive_actions: Collection[str] = SENSITIVE_ACTIONS,
    role_change_actions: Collection[str] = ROLE_CHANGE_ACTIONS,
    failure_outcomes: Collection[str] = FAILURE_OUTCOMES,
    window: timedelta = DEFAULT_ESCALATION_WINDOW,
    now: datetime | None = None,
) -> list[ThreatFinding]:
    """Detect actions outside an actor's role, or role change then access.

    Two distinct shapes, reported under one category because the response
    is the same:

    1. **Out-of-role attempt** -- the actor performed an action their
       granted role does not list. Reported whether or not it succeeded: a
       *denied* attempt says access control worked, and a *successful* one
       says it did not, which is the more urgent of the two.
    2. **Role change followed by immediate sensitive access** -- the
       classic escalation chain. A legitimate grant is followed by a human
       coming back later; a stolen one is used at once.

    Note the window applies to shape 2 only. An out-of-role attempt is
    interesting whenever it happened, so filtering it by recency would just
    lose evidence.

    Args:
        events: The event stream to inspect, any order.
        allowed_actions: Mapping of actor id to the actions their role
            grants. Actors absent from the mapping are not evaluated for
            shape 1, since "no entry" means unknown role, not empty role.
        sensitive_actions: Actions treated as privileged access.
        role_change_actions: Actions that alter a principal's role.
        failure_outcomes: Outcome names counted as blocked attempts.
        window: How soon after a role change sensitive access is suspicious.
        now: Evaluation time; defaults to the current UTC time. Recorded on
            the finding.

    Returns:
        One finding per offending actor, or an empty list.

    Raises:
        ValueError: If ``window`` is not a positive timedelta.
    """
    if window <= timedelta(0):
        raise ValueError("window must be a positive timedelta.")

    evaluated_at = _resolve_now(now)
    ordered = sorted(events, key=lambda event: _to_utc(event.timestamp))
    grants = allowed_actions or {}

    findings: list[ThreatFinding] = []
    for actor_id, actor_events in _group_by_actor(ordered).items():
        evidence: list[SecurityEvent] = []
        reasons: list[str] = []
        succeeded_out_of_role = False

        permitted = grants.get(actor_id)
        if permitted is not None:
            out_of_role = [event for event in actor_events if event.action not in permitted]
            if out_of_role:
                evidence.extend(out_of_role)
                succeeded_out_of_role = any(
                    event.outcome not in failure_outcomes for event in out_of_role
                )
                attempted = sorted({event.action for event in out_of_role})
                reasons.append(
                    f"attempted {len(out_of_role)} action(s) outside their granted role "
                    f"({', '.join(attempted)})"
                )

        chains = _role_change_chains(actor_events, sensitive_actions, role_change_actions, window)
        if chains:
            for change_event, access_event in chains:
                evidence.extend((change_event, access_event))
            reasons.append(
                f"performed sensitive access within {int(window.total_seconds())}s of a role "
                f"change ({len(chains)} occurrence(s))"
            )

        if not reasons:
            continue

        # A successful out-of-role action means the control failed open;
        # that is an active compromise, not a blocked attempt.
        level = ThreatLevel.CRITICAL if succeeded_out_of_role or chains else ThreatLevel.HIGH
        deduped = _dedupe_events(evidence)
        findings.append(
            ThreatFinding(
                category=ThreatCategory.PRIVILEGE_ESCALATION,
                level=level,
                actor_id=actor_id,
                evidence=deduped,
                explanation=f"Actor {actor_id!r} " + "; ".join(reasons) + ".",
                detected_at=evaluated_at,
            )
        )
    return findings


def _role_change_chains(
    actor_events: Sequence[SecurityEvent],
    sensitive_actions: Collection[str],
    role_change_actions: Collection[str],
    window: timedelta,
) -> list[tuple[SecurityEvent, SecurityEvent]]:
    """Pair each role change with sensitive access that closely follows it.

    Args:
        actor_events: One actor's events, oldest first.
        sensitive_actions: Actions treated as privileged access.
        role_change_actions: Actions that alter a principal's role.
        window: Maximum gap between the change and the access.

    Returns:
        ``(role_change_event, sensitive_access_event)`` pairs.
    """
    chains: list[tuple[SecurityEvent, SecurityEvent]] = []
    for index, event in enumerate(actor_events):
        if event.action not in role_change_actions:
            continue
        change_time = _to_utc(event.timestamp)
        for follower in actor_events[index + 1 :]:
            gap = _to_utc(follower.timestamp) - change_time
            if gap > window:
                break
            if follower.action in sensitive_actions:
                chains.append((event, follower))
    return chains


def _dedupe_events(events: Sequence[SecurityEvent]) -> tuple[SecurityEvent, ...]:
    """Remove duplicate event objects while preserving chronological order.

    Identity, not equality: events are arbitrary caller objects and may not
    be hashable or comparable.

    Args:
        events: Events to deduplicate.

    Returns:
        The unique events, oldest first.
    """
    seen: set[int] = set()
    unique: list[SecurityEvent] = []
    for event in events:
        if id(event) not in seen:
            seen.add(id(event))
            unique.append(event)
    unique.sort(key=lambda event: _to_utc(event.timestamp))
    return tuple(unique)


def detect_anomalous_rate(
    events: Sequence[SecurityEvent],
    *,
    multiplier: float = DEFAULT_RATE_MULTIPLIER,
    window: timedelta = DEFAULT_RATE_WINDOW,
    min_events: int = DEFAULT_RATE_MIN_EVENTS,
    min_baseline_events: int = DEFAULT_RATE_MIN_BASELINE_EVENTS,
    now: datetime | None = None,
) -> list[ThreatFinding]:
    """Detect a request rate far above the actor's own historical baseline.

    The baseline is built from that actor's events *before* the window, so
    the comparison is against their own normal rather than a global
    ceiling. A nightly batch job and an interactive analyst have very
    different legitimate rates, and any single global threshold is
    simultaneously too loud for one and too quiet for the other.

    An actor with fewer than ``min_baseline_events`` of history produces no
    finding at all. A new account has no normal to deviate from, and
    inventing one is how a detector spends its credibility on first-day
    users.

    Args:
        events: The event stream to inspect.
        multiplier: How many times the baseline rate counts as anomalous.
        window: Trailing window ending at ``now``.
        min_events: Minimum in-window events before the ratio is trusted.
        min_baseline_events: Minimum historical events before a baseline is
            considered established.
        now: Evaluation time; defaults to the current UTC time.

    Returns:
        One finding per offending actor, or an empty list.

    Raises:
        ValueError: If ``multiplier`` is not positive or ``window`` is not
            a positive timedelta.
    """
    if multiplier <= 0:
        raise ValueError("multiplier must be positive.")
    if window <= timedelta(0):
        raise ValueError("window must be a positive timedelta.")

    evaluated_at = _resolve_now(now)
    window_start = evaluated_at - window
    window_seconds = window.total_seconds()

    findings: list[ThreatFinding] = []
    for actor_id, actor_events in _group_by_actor(events).items():
        recent = [
            event
            for event in actor_events
            if window_start <= _to_utc(event.timestamp) <= evaluated_at
        ]
        history = [event for event in actor_events if _to_utc(event.timestamp) < window_start]
        if len(recent) < min_events or len(history) < min_baseline_events:
            continue

        earliest = min(_to_utc(event.timestamp) for event in history)
        baseline_span = (window_start - earliest).total_seconds()
        if baseline_span <= 0:
            continue

        baseline_rate = len(history) / baseline_span
        current_rate = len(recent) / window_seconds
        if baseline_rate <= 0 or current_rate < baseline_rate * multiplier:
            continue

        ratio = current_rate / baseline_rate
        level = ThreatLevel.HIGH if ratio >= multiplier * 2 else ThreatLevel.MEDIUM
        recent.sort(key=lambda event: _to_utc(event.timestamp))
        findings.append(
            ThreatFinding(
                category=ThreatCategory.ANOMALOUS_RATE,
                level=level,
                actor_id=actor_id,
                evidence=tuple(recent),
                explanation=(
                    f"Actor {actor_id!r} is running at {current_rate:.3f} events/s over the last "
                    f"{int(window_seconds)}s, {ratio:.1f}x their baseline of "
                    f"{baseline_rate:.3f} events/s (alert at {multiplier:.1f}x)."
                ),
                detected_at=evaluated_at,
            )
        )
    return findings


@dataclass
class ThreatDetector:
    """Runs every detector over an event stream with shared configuration.

    Attributes:
        brute_force_threshold: Failed auth attempts that trigger a finding.
        brute_force_window: Window for the brute-force detector.
        exfiltration_threshold: Reads/exports that trigger a finding.
        exfiltration_window: Window for the exfiltration detector.
        escalation_window: Role-change-to-sensitive-access gap.
        rate_multiplier: Multiple of baseline that counts as anomalous.
        rate_window: Window for the rate detector.
        rate_min_events: Minimum in-window events before the ratio counts.
        rate_min_baseline_events: Minimum history before a baseline counts.
        allowed_actions: Actor id to granted actions, for the privilege
            detector. None disables out-of-role checking.
        sensitive_actions: Actions treated as privileged access.
        role_change_actions: Actions that alter a principal's role.
        auth_actions: Action names counted as authentication attempts.
        read_actions: Action names counted as data access.
        failure_outcomes: Outcome names counted as failures.
        clock: Returns the current time. Injectable so tests are
            deterministic without sleeping.
    """

    brute_force_threshold: int = DEFAULT_BRUTE_FORCE_THRESHOLD
    brute_force_window: timedelta = DEFAULT_BRUTE_FORCE_WINDOW
    exfiltration_threshold: int = DEFAULT_EXFILTRATION_THRESHOLD
    exfiltration_window: timedelta = DEFAULT_EXFILTRATION_WINDOW
    escalation_window: timedelta = DEFAULT_ESCALATION_WINDOW
    rate_multiplier: float = DEFAULT_RATE_MULTIPLIER
    rate_window: timedelta = DEFAULT_RATE_WINDOW
    rate_min_events: int = DEFAULT_RATE_MIN_EVENTS
    rate_min_baseline_events: int = DEFAULT_RATE_MIN_BASELINE_EVENTS
    allowed_actions: Mapping[str, Collection[str]] | None = None
    sensitive_actions: Collection[str] = SENSITIVE_ACTIONS
    role_change_actions: Collection[str] = ROLE_CHANGE_ACTIONS
    auth_actions: Collection[str] = AUTH_ACTIONS
    read_actions: Collection[str] = READ_ACTIONS
    failure_outcomes: Collection[str] = FAILURE_OUTCOMES
    clock: Callable[[], datetime] = field(default=_utc_now)

    def detect(
        self,
        events: Sequence[SecurityEvent],
        *,
        now: datetime | None = None,
    ) -> list[ThreatFinding]:
        """Run every detector and return findings, most severe first.

        Args:
            events: The event stream to inspect.
            now: Evaluation time. Defaults to ``self.clock()``, which
                defaults to the wall clock.

        Returns:
            All findings, sorted by descending severity then by category
            and actor so the ordering is stable for snapshot comparisons.
        """
        evaluated_at = _to_utc(now) if now is not None else _to_utc(self.clock())

        findings: list[ThreatFinding] = []
        findings.extend(
            detect_brute_force(
                events,
                threshold=self.brute_force_threshold,
                window=self.brute_force_window,
                now=evaluated_at,
                auth_actions=self.auth_actions,
                failure_outcomes=self.failure_outcomes,
            )
        )
        findings.extend(
            detect_data_exfiltration(
                events,
                threshold=self.exfiltration_threshold,
                window=self.exfiltration_window,
                now=evaluated_at,
                read_actions=self.read_actions,
                failure_outcomes=self.failure_outcomes,
            )
        )
        findings.extend(
            detect_privilege_escalation(
                events,
                allowed_actions=self.allowed_actions,
                sensitive_actions=self.sensitive_actions,
                role_change_actions=self.role_change_actions,
                failure_outcomes=self.failure_outcomes,
                window=self.escalation_window,
                now=evaluated_at,
            )
        )
        findings.extend(
            detect_anomalous_rate(
                events,
                multiplier=self.rate_multiplier,
                window=self.rate_window,
                min_events=self.rate_min_events,
                min_baseline_events=self.rate_min_baseline_events,
                now=evaluated_at,
            )
        )

        findings.sort(key=lambda f: (-_LEVEL_ORDER[f.level], str(f.category), f.actor_id))
        if findings:
            _logger.warning(
                "Threat detection produced %d finding(s); highest severity=%s",
                len(findings),
                score_threat_level(findings),
            )
        return findings


def score_threat_level(findings: Sequence[ThreatFinding | ThreatSignal]) -> ThreatLevel:
    """Aggregate findings or signals into a single overall severity.

    Takes the maximum rather than an average or a sum: one CRITICAL finding
    buried among twenty LOW ones is still a critical situation, and
    averaging is how it gets missed.

    Args:
        findings: Findings or signals to aggregate.

    Returns:
        The highest severity present, or :attr:`ThreatLevel.LOW` when there
        is nothing to report.
    """
    if not findings:
        return ThreatLevel.LOW
    return max((f.level for f in findings), key=lambda level: _LEVEL_ORDER[level])


def analyze_request_pattern(
    source_id: str,
    events: Sequence[SecurityEvent],
    window_seconds: int = 60,
    *,
    now: datetime | None = None,
    detector: ThreatDetector | None = None,
) -> list[ThreatSignal]:
    """Analyze one source's recent history for anomalous patterns.

    Convenience wrapper over :meth:`ThreatDetector.detect` scoped to a
    single actor. ``window_seconds`` bounds every detector's window, so
    this answers "what does this source look like right now" rather than
    running each detector on its own natural timescale.

    Args:
        source_id: Identifier of the actor/session to analyze.
        events: The event stream to inspect; other actors' events are used
            only as context and never reported.
        window_seconds: Size of the trailing window to examine. Must be
            positive.
        now: Evaluation time; defaults to the detector's clock.
        detector: Detector supplying thresholds. Defaults to a new one with
            default thresholds.

    Returns:
        Threat signals for ``source_id`` within the window, most severe
        first.

    Raises:
        ValueError: If ``window_seconds`` is not positive.
    """
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive.")

    window = timedelta(seconds=window_seconds)
    base = detector or ThreatDetector()
    scoped = ThreatDetector(
        brute_force_threshold=base.brute_force_threshold,
        brute_force_window=window,
        exfiltration_threshold=base.exfiltration_threshold,
        exfiltration_window=window,
        escalation_window=base.escalation_window,
        rate_multiplier=base.rate_multiplier,
        rate_window=window,
        rate_min_events=base.rate_min_events,
        rate_min_baseline_events=base.rate_min_baseline_events,
        allowed_actions=base.allowed_actions,
        sensitive_actions=base.sensitive_actions,
        role_change_actions=base.role_change_actions,
        auth_actions=base.auth_actions,
        read_actions=base.read_actions,
        failure_outcomes=base.failure_outcomes,
        clock=base.clock,
    )
    findings = scoped.detect(events, now=now)
    return [
        ThreatSignal.from_finding(finding) for finding in findings if finding.actor_id == source_id
    ]


def should_block(
    source_id: str,
    events: Sequence[SecurityEvent],
    *,
    now: datetime | None = None,
    detector: ThreatDetector | None = None,
    block_at: ThreatLevel = ThreatLevel.HIGH,
) -> bool:
    """Decide whether traffic from a source should currently be blocked.

    Args:
        source_id: Identifier of the actor/session to evaluate.
        events: The event stream to inspect.
        now: Evaluation time; defaults to the detector's clock.
        detector: Detector supplying thresholds. Defaults to a new one with
            default thresholds.
        block_at: Minimum severity that warrants blocking. HIGH by default,
            so MEDIUM rate anomalies are reviewed rather than enforced --
            auto-blocking on a rate deviation is how a detector takes the
            service down instead of the attacker.

    Returns:
        True if the source's current threat level reaches ``block_at``.
    """
    active = detector or ThreatDetector()
    findings = [f for f in active.detect(events, now=now) if f.actor_id == source_id]
    return _LEVEL_ORDER[score_threat_level(findings)] >= _LEVEL_ORDER[block_at] and bool(findings)


__all__ = [
    "AUTH_ACTIONS",
    "DEFAULT_BRUTE_FORCE_THRESHOLD",
    "DEFAULT_BRUTE_FORCE_WINDOW",
    "DEFAULT_ESCALATION_WINDOW",
    "DEFAULT_EXFILTRATION_THRESHOLD",
    "DEFAULT_EXFILTRATION_WINDOW",
    "DEFAULT_RATE_MIN_BASELINE_EVENTS",
    "DEFAULT_RATE_MIN_EVENTS",
    "DEFAULT_RATE_MULTIPLIER",
    "DEFAULT_RATE_WINDOW",
    "FAILURE_OUTCOMES",
    "READ_ACTIONS",
    "ROLE_CHANGE_ACTIONS",
    "SENSITIVE_ACTIONS",
    "SecurityEvent",
    "ThreatCategory",
    "ThreatDetector",
    "ThreatFinding",
    "ThreatLevel",
    "ThreatSignal",
    "analyze_request_pattern",
    "detect_anomalous_rate",
    "detect_brute_force",
    "detect_data_exfiltration",
    "detect_privilege_escalation",
    "score_threat_level",
    "should_block",
]
