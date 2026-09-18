"""Behavioural tests for the automation agent's delivery guarantees.

Each test pins one guarantee the module docstrings promise: dedup, retry,
dead-lettering, the ack/nack policy, and lease handling.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from agent import (
    AutomationAgent,
    Claim,
    DeadLetter,
    DeadLetterQueue,
    Event,
    EventSource,
    InMemoryDeadLetterQueue,
    InMemoryIdempotencyStore,
    LeaseHeldError,
    LoggingMetrics,
    PermanentError,
    RetryPolicy,
    TriggerEvaluator,
    TriggerRule,
    Workflow,
    WorkflowContext,
    WorkflowExecutor,
)

FAST_RETRY = RetryPolicy(max_attempts=3, base_delay_s=0.001, max_delay_s=0.01)


class ListSource(EventSource):
    def __init__(self, events: list[Event], *, fail_first_ack: bool = False) -> None:
        self._events = events
        self._fail_next_ack = fail_first_ack
        self.acked: list[str] = []
        self.nacked: list[tuple[str, BaseException]] = []

    async def events(self) -> AsyncIterator[Event]:
        for event in self._events:
            yield event

    async def ack(self, event: Event) -> None:
        if self._fail_next_ack:
            self._fail_next_ack = False
            raise ConnectionError("broker unreachable")
        self.acked.append(event.id)

    async def nack(self, event: Event, reason: BaseException) -> None:
        self.nacked.append((event.id, reason))


class Enrich(Workflow):
    name = "enrich"

    def __init__(self) -> None:
        self.runs = 0

    async def run(self, ctx: WorkflowContext) -> None:
        self.runs += 1


class Flaky(Workflow):
    """Times out once, then succeeds."""

    name = "flaky"

    def __init__(self) -> None:
        self.attempts = 0

    async def run(self, ctx: WorkflowContext) -> None:
        self.attempts += 1
        if self.attempts < 2:
            raise TimeoutError("intel API timed out")


class Quarantine(Workflow):
    name = "quarantine"

    def __init__(self) -> None:
        self.attempts = 0

    async def run(self, ctx: WorkflowContext) -> None:
        self.attempts += 1
        raise PermanentError("host decommissioned")


class BrokenDLQ(DeadLetterQueue):
    async def send(self, letter: DeadLetter) -> None:
        raise ConnectionError("dlq unreachable")


RULES = [
    TriggerRule("high-severity", lambda e: e.payload.get("severity") == "high", ("enrich",)),
    TriggerRule("siem-alerts", lambda e: e.source == "siem", ("flaky",)),
    TriggerRule("critical-edr", lambda e: e.payload.get("severity") == "critical", ("quarantine",)),
]


def alert(event_id: str, source: str = "edr", severity: str = "high") -> Event:
    return Event(event_id, source, "alert.raised", {"severity": severity})


def run_agent(
    events: list[Event],
    *,
    store: InMemoryIdempotencyStore | None = None,
    dlq: DeadLetterQueue | None = None,
    source: ListSource | None = None,
) -> tuple[ListSource, LoggingMetrics, DeadLetterQueue, dict[str, Workflow]]:
    source = source or ListSource(events)
    metrics = LoggingMetrics()
    dlq = dlq or InMemoryDeadLetterQueue()
    workflows: dict[str, Workflow] = {w.name: w for w in (Enrich(), Flaky(), Quarantine())}
    executor = WorkflowExecutor(workflows.values(), FAST_RETRY, dlq, metrics)
    # Serial, so a redelivery is handled after the original has settled.
    agent = AutomationAgent(
        source,
        TriggerEvaluator(RULES),
        executor,
        store or InMemoryIdempotencyStore(),
        metrics,
        max_concurrency=1,
    )
    asyncio.run(agent.run())
    return source, metrics, dlq, workflows


def test_redelivered_event_runs_its_workflows_once() -> None:
    source, metrics, _, workflows = run_agent([alert("A-1"), alert("A-1")])

    assert isinstance(workflows["enrich"], Enrich)
    assert workflows["enrich"].runs == 1
    assert metrics.counters["event.duplicate"] == 1
    assert source.acked == ["A-1", "A-1"]  # the duplicate is acked, not redelivered forever


def test_transient_failure_is_retried_until_it_succeeds() -> None:
    source, metrics, dlq, workflows = run_agent([alert("B-1", source="siem", severity="low")])

    assert isinstance(workflows["flaky"], Flaky)
    assert workflows["flaky"].attempts == 2
    assert metrics.counters["workflow.retry"] == 1
    assert isinstance(dlq, InMemoryDeadLetterQueue)
    assert dlq.letters == []
    assert source.acked == ["B-1"]


def test_permanent_failure_is_dead_lettered_without_retry() -> None:
    source, _, dlq, workflows = run_agent([alert("C-1", severity="critical")])

    assert isinstance(workflows["quarantine"], Quarantine)
    assert workflows["quarantine"].attempts == 1
    assert isinstance(dlq, InMemoryDeadLetterQueue)
    assert [(d.event.id, d.workflow, d.attempts) for d in dlq.letters] == [("C-1", "quarantine", 1)]
    # Acked: the DLQ owns it now, and redelivery would only fail again.
    assert source.acked == ["C-1"]
    assert source.nacked == []


def test_dead_lettered_event_is_not_rerun_on_redelivery() -> None:
    _, metrics, dlq, workflows = run_agent(
        [alert("C-1", severity="critical"), alert("C-1", severity="critical")]
    )

    assert isinstance(workflows["quarantine"], Quarantine)
    assert workflows["quarantine"].attempts == 1
    assert isinstance(dlq, InMemoryDeadLetterQueue)
    assert len(dlq.letters) == 1
    assert metrics.counters["event.duplicate"] == 1


def test_event_matching_no_rule_is_settled_and_acked() -> None:
    source, metrics, _, workflows = run_agent([alert("D-1", source="cloudtrail", severity="info")])

    assert metrics.counters["event.no_match"] == 1
    assert all(getattr(w, "runs", 0) == 0 for w in workflows.values())
    assert source.acked == ["D-1"]


def test_event_leased_by_another_worker_is_nacked_not_acked() -> None:
    store = InMemoryIdempotencyStore()
    assert asyncio.run(store.claim("A-1", ttl_s=60)) is Claim.ACQUIRED  # another worker

    source, _, _, workflows = run_agent([alert("A-1")], store=store)

    assert isinstance(workflows["enrich"], Enrich)
    assert workflows["enrich"].runs == 0
    assert source.acked == []
    assert [(eid, type(reason)) for eid, reason in source.nacked] == [("A-1", LeaseHeldError)]


def test_agent_fault_before_settling_releases_lease_and_nacks() -> None:
    store = InMemoryIdempotencyStore()

    source, metrics, _, _ = run_agent(
        [alert("C-1", severity="critical")], store=store, dlq=BrokenDLQ()
    )

    assert metrics.counters["event.agent_error"] == 1
    assert [eid for eid, _ in source.nacked] == ["C-1"]
    # Lease was given back, so a healthy replica can take the redelivery.
    assert asyncio.run(store.claim("C-1", ttl_s=60)) is Claim.ACQUIRED


def test_ack_failure_after_settling_keeps_the_verdict() -> None:
    store = InMemoryIdempotencyStore()
    source = ListSource([alert("A-1"), alert("A-1")], fail_first_ack=True)

    _, metrics, _, workflows = run_agent([], store=store, source=source)

    # First ack failed -> nacked, but the verdict must survive: the
    # redelivery is recognised as a duplicate, not re-executed.
    assert isinstance(workflows["enrich"], Enrich)
    assert workflows["enrich"].runs == 1
    assert [eid for eid, _ in source.nacked] == ["A-1"]
    assert metrics.counters["event.duplicate"] == 1
    assert source.acked == ["A-1"]


def test_expired_lease_can_be_reclaimed() -> None:
    async def scenario() -> tuple[Claim, Claim]:
        store = InMemoryIdempotencyStore()
        await store.claim("A-1", ttl_s=0)  # holder crashed; lease lapses at once
        return await store.claim("A-1", ttl_s=60), await store.claim("A-1", ttl_s=60)

    assert asyncio.run(scenario()) == (Claim.ACQUIRED, Claim.LEASED)


def test_retry_delay_is_jittered_within_the_capped_ceiling() -> None:
    policy = RetryPolicy(base_delay_s=1.0, max_delay_s=5.0)

    for attempt in range(1, 10):
        ceiling = min(5.0, 2 ** (attempt - 1))
        assert all(0.0 <= policy.delay_for(attempt) <= ceiling for _ in range(50))
