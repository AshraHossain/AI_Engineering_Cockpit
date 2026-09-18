"""
Event-triggered automation agent — SOC / security-operations skeleton.

Layering (each layer knows only the interface below it):

    EventSource        ingest: webhook receiver, queue consumer, SIEM stream
        |
    TriggerEvaluator   rules: which workflows does this alert deserve?
        |
    WorkflowExecutor   run: enrich, contain, open case — with retries
        |   \\
        |    DeadLetterQueue   permanently failed (event, workflow) pairs
    IdempotencyStore   exactly-once-ish: one detection => one containment
        |
    MetricsLogger      cross-cutting observability hook

Extension points are marked `TODO(integration)`. Nothing here opens a
socket; wire your transport into an EventSource subclass and your SOAR
actions into Workflow subclasses.
"""

from __future__ import annotations

import abc
import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

__all__ = [
    "AutomationAgent",
    "Claim",
    "DeadLetter",
    "DeadLetterQueue",
    "Event",
    "EventSource",
    "IdempotencyStore",
    "InMemoryDeadLetterQueue",
    "InMemoryIdempotencyStore",
    "LeaseHeldError",
    "LoggingMetrics",
    "MetricsLogger",
    "PermanentError",
    "RetryPolicy",
    "TriggerEvaluator",
    "TriggerRule",
    "Workflow",
    "WorkflowContext",
    "WorkflowExecutor",
]

log = logging.getLogger("automation")


# --------------------------------------------------------------------------- #
# Domain
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Event:
    """A single security signal to be acted on.

    `id` is the idempotency key. It MUST be stable across redeliveries —
    use the detection ID from the SIEM/EDR, not a locally generated UUID,
    or at-least-once delivery will double-execute containment.
    """

    id: str
    source: str  # "edr", "siem", "cloudtrail", "phishing-mailbox"
    type: str  # "alert.raised", "finding.created", ...
    payload: dict[str, Any] = field(default_factory=dict)
    received_at: float = field(default_factory=time.time)


class PermanentError(Exception):
    """Raise from a Workflow when retrying cannot possibly help.

    Examples: malformed alert schema, host no longer exists, policy
    forbids the action. These bypass the RetryPolicy and go straight to
    the dead-letter queue.
    """


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #


class EventSource(abc.ABC):
    """Transport abstraction. One subclass per ingest mechanism.

    Implementations may be at-least-once; exactly-once is provided
    upstack by the IdempotencyStore, not by the transport.
    """

    @abc.abstractmethod
    def events(self) -> AsyncIterator[Event]:
        """Yield events until the source is drained or cancelled.

        TODO(integration): webhook source — push received requests onto an
        asyncio.Queue from the HTTP handler and yield from it here, so the
        HTTP response returns before workflows run.
        TODO(integration): queue source — long-poll SQS / consume Kafka /
        pull Pub/Sub, yielding one Event per message.
        """

    @abc.abstractmethod
    async def ack(self, event: Event) -> None:
        """Confirm terminal handling. The broker must not redeliver."""

    @abc.abstractmethod
    async def nack(self, event: Event, reason: BaseException) -> None:
        """Return the event for redelivery (agent crashed / infra fault)."""


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #


class Claim(StrEnum):
    """Outcome of `IdempotencyStore.claim`.

    SETTLED and LEASED must stay distinct: a settled event is safe to ack,
    but acking a *leased* one would drop it if its holder then crashed.
    """

    ACQUIRED = "acquired"  # caller owns the event now
    SETTLED = "settled"  # already done or dead-lettered — ack and drop
    LEASED = "leased"  # another worker holds it — let the broker redeliver


class LeaseHeldError(Exception):
    """Nack reason when another worker currently holds the event's lease."""

    def __init__(self, event_id: str) -> None:
        super().__init__(f"event {event_id} is leased by another worker")
        self.event_id = event_id


class IdempotencyStore(abc.ABC):
    """Dedup ledger keyed by `Event.id`.

    The contract is a lease, not a flag: `claim` grants exclusive
    ownership for `ttl_s`, so a crashed agent's events become claimable
    again instead of being stranded forever.

    TODO(integration): a distributed store should return a lease token from
    `claim` and fence `complete`/`fail`/`release` on it, so a worker whose
    lease expired mid-workflow cannot settle a claim that another worker
    has since taken over.
    """

    @abc.abstractmethod
    async def claim(self, event_id: str, ttl_s: float) -> Claim:
        """Try to take exclusive ownership of the event. Must be atomic
        across processes.

        TODO(integration): Redis `SET key value NX PX ttl`, or an INSERT
        against a UNIQUE column with `ON CONFLICT DO NOTHING`.
        """

    @abc.abstractmethod
    async def complete(self, event_id: str) -> None:
        """Terminal success. Later redeliveries are skipped."""

    @abc.abstractmethod
    async def fail(self, event_id: str) -> None:
        """Terminal failure (dead-lettered). Also skipped on redelivery —
        the DLQ owns it now; replaying is a deliberate operator action."""

    @abc.abstractmethod
    async def release(self, event_id: str) -> None:
        """Drop the lease without a verdict, so the broker may redeliver."""


class InMemoryIdempotencyStore(IdempotencyStore):
    """Reference implementation for tests and single-process runs.

    ponytail: process-local dict — gives you no dedup across replicas.
    Swap for Redis/Postgres before running more than one agent.
    """

    _SETTLED = float("inf")  # expiry marker for done / dead-lettered events

    def __init__(self) -> None:
        # event_id -> lease expiry (monotonic seconds), or _SETTLED
        self._entries: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def claim(self, event_id: str, ttl_s: float) -> Claim:
        async with self._lock:
            expires_at = self._entries.get(event_id)
            now = time.monotonic()
            if expires_at == self._SETTLED:
                return Claim.SETTLED
            if expires_at is not None and now < expires_at:
                return Claim.LEASED
            self._entries[event_id] = now + ttl_s
            return Claim.ACQUIRED

    async def complete(self, event_id: str) -> None:
        await self._settle(event_id)

    async def fail(self, event_id: str) -> None:
        # Done vs dead only matters to a ledger someone queries; the DLQ
        # already holds the failure detail.
        await self._settle(event_id)

    async def release(self, event_id: str) -> None:
        async with self._lock:
            self._entries.pop(event_id, None)

    async def _settle(self, event_id: str) -> None:
        async with self._lock:
            # TODO(integration): settled rows need a retention TTL of their
            # own (e.g. 7d) or the ledger grows without bound.
            self._entries[event_id] = self._SETTLED


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TriggerRule:
    """Binds a predicate over an Event to the workflows it should fire.

    Keep predicates pure and cheap — they run on every event. No I/O here.
    """

    name: str
    matches: Callable[[Event], bool]
    workflows: tuple[str, ...]


class TriggerEvaluator:
    """Maps one Event to an ordered, de-duplicated list of workflow names.

    Extension point: subclass and override `evaluate` to read rules from a
    database or a policy DSL instead of holding them in memory.
    """

    def __init__(self, rules: Iterable[TriggerRule]) -> None:
        self._rules: tuple[TriggerRule, ...] = tuple(rules)

    def evaluate(self, event: Event) -> list[str]:
        selected: dict[str, None] = {}  # dict preserves first-seen order
        for rule in self._rules:
            try:
                fired = rule.matches(event)
            except Exception:
                # A broken rule must not silence every other rule.
                log.exception("trigger rule %r raised; skipping", rule.name)
                continue
            if fired:
                selected.update(dict.fromkeys(rule.workflows))
        return list(selected)


# --------------------------------------------------------------------------- #
# Workflows
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class WorkflowContext:
    """Per-attempt context handed to a Workflow."""

    event: Event
    attempt: int  # 1-based
    metrics: MetricsLogger


class Workflow(abc.ABC):
    """One unit of SOC automation: enrich, contain, notify, open case.

    Implementations MUST be safe to re-run: the RetryPolicy will invoke
    `run` again after a transient failure, possibly after a partial
    side effect. Guard external calls with their own request IDs.
    """

    name: str

    @abc.abstractmethod
    async def run(self, ctx: WorkflowContext) -> None:
        """Perform the action. Raise PermanentError to skip retries.

        TODO(integration): enrichment — threat-intel lookup on observables.
        TODO(integration): containment — EDR host isolation, IAM key revoke.
        TODO(integration): case management — create/annotate the ticket.
        """


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Exponential backoff with full jitter.

    Full jitter (uniform over [0, ceiling]) rather than fixed backoff:
    a SIEM outage delivers a burst of correlated alerts, and undithered
    retries would reconverge into a thundering herd against the very API
    that just failed.
    """

    max_attempts: int = 5
    base_delay_s: float = 0.2
    max_delay_s: float = 30.0

    def should_retry(self, attempt: int, exc: BaseException) -> bool:
        if isinstance(exc, PermanentError):
            return False
        return attempt < self.max_attempts

    def delay_for(self, attempt: int) -> float:
        ceiling = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
        return random.uniform(0.0, ceiling)  # noqa: S311 -- jitter, not crypto


@dataclass(frozen=True, slots=True)
class DeadLetter:
    """A (event, workflow) pair that exhausted retries or failed hard."""

    event: Event
    workflow: str
    attempts: int
    error: str
    failed_at: float = field(default_factory=time.time)


class DeadLetterQueue(abc.ABC):
    """Terminal sink for unprocessable work."""

    @abc.abstractmethod
    async def send(self, letter: DeadLetter) -> None:
        """TODO(integration): SQS DLQ, Kafka topic, or a `dead_letters`
        table an analyst can triage and replay from."""


class InMemoryDeadLetterQueue(DeadLetterQueue):
    def __init__(self) -> None:
        self.letters: list[DeadLetter] = []

    async def send(self, letter: DeadLetter) -> None:
        self.letters.append(letter)
        log.error(
            "dead-letter event=%s workflow=%s attempts=%d error=%s",
            letter.event.id,
            letter.workflow,
            letter.attempts,
            letter.error,
        )


class WorkflowExecutor:
    """Runs the selected workflows for an event under a RetryPolicy.

    Workflows run sequentially and independently: one failing workflow is
    dead-lettered on its own and does not cancel its siblings. Swap in
    `asyncio.gather` here if your workflows are order-independent and
    latency matters more than a predictable audit trail.
    """

    def __init__(
        self,
        workflows: Iterable[Workflow],
        retry_policy: RetryPolicy,
        dlq: DeadLetterQueue,
        metrics: MetricsLogger,
    ) -> None:
        self._workflows: dict[str, Workflow] = {w.name: w for w in workflows}
        self._retry = retry_policy
        self._dlq = dlq
        self._metrics = metrics

    async def execute(self, event: Event, names: Sequence[str]) -> list[str]:
        """Run each named workflow. Returns the names that failed terminally."""
        failed: list[str] = []
        for name in names:
            workflow = self._workflows.get(name)
            if workflow is None:
                # A rule referencing an unregistered workflow is a config
                # bug; surface it loudly rather than dropping the action.
                await self._dlq.send(DeadLetter(event, name, 0, "workflow not registered"))
                self._metrics.increment("workflow.unknown", workflow=name)
                failed.append(name)
                continue
            if not await self._run_with_retry(event, workflow):
                failed.append(name)
        return failed

    async def _run_with_retry(self, event: Event, workflow: Workflow) -> bool:
        attempt = 0
        while True:
            attempt += 1
            started = time.monotonic()
            try:
                await workflow.run(WorkflowContext(event, attempt, self._metrics))
            except Exception as exc:  # never swallow CancelledError
                self._record_duration(workflow, started)
                if self._retry.should_retry(attempt, exc):
                    self._metrics.increment("workflow.retry", workflow=workflow.name)
                    await asyncio.sleep(self._retry.delay_for(attempt))
                    continue
                self._metrics.increment("workflow.failed", workflow=workflow.name)
                await self._dlq.send(DeadLetter(event, workflow.name, attempt, repr(exc)))
                return False
            self._record_duration(workflow, started)
            self._metrics.increment("workflow.succeeded", workflow=workflow.name)
            return True

    def _record_duration(self, workflow: Workflow, started: float) -> None:
        elapsed_ms = (time.monotonic() - started) * 1000
        self._metrics.timing("workflow.duration_ms", elapsed_ms, workflow=workflow.name)


# --------------------------------------------------------------------------- #
# Observability
# --------------------------------------------------------------------------- #


class MetricsLogger(Protocol):
    """Cross-cutting hook. A Protocol, not an ABC — pass anything shaped
    like this (a StatsD client wrapper, an OTel meter, a test double)."""

    def increment(self, name: str, **tags: str) -> None: ...

    def timing(self, name: str, value_ms: float, **tags: str) -> None: ...


class LoggingMetrics:
    """Default implementation: stdlib logging, plus in-process counters.

    TODO(integration): forward to StatsD / Prometheus / OpenTelemetry.
    """

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}

    def increment(self, name: str, **tags: str) -> None:
        self.counters[name] = self.counters.get(name, 0) + 1
        log.debug("metric.increment %s %s", name, tags)

    def timing(self, name: str, value_ms: float, **tags: str) -> None:
        log.debug("metric.timing %s=%.2fms %s", name, value_ms, tags)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


class AutomationAgent:
    """Wires the layers together and owns the per-event lifecycle.

        claim -> evaluate -> execute -> settle -> ack

    Ack/nack policy, deliberately:

    - A dead-lettered event is still acked: the DLQ owns it now, and
      redelivering would only re-run workflows that already failed.
    - An event leased by another worker is nacked, never acked: if that
      worker crashes, the broker's redelivery is the only way back in.
    - An unexpected agent-side fault releases the lease and nacks, so the
      broker can hand the event to a healthy replica — but only while the
      event is unsettled. Once the ledger records a verdict, releasing it
      would erase that verdict and re-run the workflows on redelivery.
    """

    def __init__(
        self,
        source: EventSource,
        evaluator: TriggerEvaluator,
        executor: WorkflowExecutor,
        store: IdempotencyStore,
        metrics: MetricsLogger,
        *,
        max_concurrency: int = 16,
        lease_ttl_s: float = 300.0,
    ) -> None:
        self._source = source
        self._evaluator = evaluator
        self._executor = executor
        self._store = store
        self._metrics = metrics
        self._sem = asyncio.Semaphore(max_concurrency)
        self._lease_ttl_s = lease_ttl_s

    async def run(self) -> None:
        """Consume until the source is drained, then drain in-flight work."""
        tasks: set[asyncio.Task[None]] = set()
        try:
            async for event in self._source.events():
                await self._sem.acquire()
                task = asyncio.create_task(self._handle(event))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                task.add_done_callback(lambda _: self._sem.release())
        finally:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle(self, event: Event) -> None:
        self._metrics.increment("event.received", source=event.source)
        owns_lease = False  # True only between ACQUIRED and settling
        try:
            claim = await self._store.claim(event.id, self._lease_ttl_s)
            if claim is Claim.SETTLED:
                self._metrics.increment("event.duplicate", source=event.source)
                await self._source.ack(event)
                return
            if claim is Claim.LEASED:
                self._metrics.increment("event.leased", source=event.source)
                await self._source.nack(event, LeaseHeldError(event.id))
                return
            owns_lease = True

            names = self._evaluator.evaluate(event)
            if not names:
                self._metrics.increment("event.no_match", source=event.source)
                await self._store.complete(event.id)
            else:
                failed = await self._executor.execute(event, names)
                if failed:
                    await self._store.fail(event.id)
                else:
                    await self._store.complete(event.id)
            owns_lease = False  # settled: the ledger now answers redeliveries

            await self._source.ack(event)

        except asyncio.CancelledError:
            if owns_lease:
                await self._store.release(event.id)
            raise
        except Exception as exc:
            # Agent-side fault, not a workflow failure: give an unsettled
            # lease back so another replica can take the event.
            log.exception("agent fault handling event=%s", event.id)
            self._metrics.increment("event.agent_error", source=event.source)
            if owns_lease:
                await self._store.release(event.id)
            await self._source.nack(event, exc)
