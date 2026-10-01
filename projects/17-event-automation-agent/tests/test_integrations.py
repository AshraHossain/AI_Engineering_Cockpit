"""Behavioural tests for CircuitBreakerWorkflow, ValidatingEventSource, and
RateLimitedEventSource: the integration pieces with logic worth pinning (the
file-backed stores are thin I/O wrappers)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from agent import Event, EventSource, PermanentError, WorkflowContext
from integrations import CircuitBreakerWorkflow, RateLimitedEventSource, ValidatingEventSource


class FlakyWorkflow:
    """Fails every call until `succeed_from` is reached."""

    name = "flaky"

    def __init__(self, succeed_from: int | None = None) -> None:
        self.calls = 0
        self.succeed_from = succeed_from

    async def run(self, ctx: WorkflowContext) -> None:
        self.calls += 1
        if self.succeed_from is None or self.calls < self.succeed_from:
            raise RuntimeError("downstream unavailable")


class PermanentlyBadWorkflow:
    name = "bad"

    async def run(self, ctx: WorkflowContext) -> None:
        raise PermanentError("malformed payload")


def ctx() -> WorkflowContext:
    return WorkflowContext(event=Event(id="e1", source="test", type="t"), attempt=1, metrics=None)


def test_calls_pass_through_while_closed():
    wrapped = FlakyWorkflow(succeed_from=1)
    breaker = CircuitBreakerWorkflow(wrapped, min_calls=5)
    asyncio.run(breaker.run(ctx()))
    assert wrapped.calls == 1


def test_opens_after_enough_failures_and_fails_fast():
    wrapped = FlakyWorkflow()
    breaker = CircuitBreakerWorkflow(wrapped, min_calls=2, failure_threshold=0.5)

    for _ in range(2):
        try:
            asyncio.run(breaker.run(ctx()))
        except RuntimeError:
            pass

    calls_before = wrapped.calls
    try:
        asyncio.run(breaker.run(ctx()))
        raised = None
    except PermanentError as e:
        raised = e

    assert raised is not None
    assert wrapped.calls == calls_before  # circuit short-circuited; wrapped never ran


def test_permanent_error_does_not_trip_the_circuit():
    wrapped = PermanentlyBadWorkflow()
    breaker = CircuitBreakerWorkflow(wrapped, min_calls=2, failure_threshold=0.5)

    for _ in range(5):
        try:
            asyncio.run(breaker.run(ctx()))
        except PermanentError:
            pass

    # Five permanent errors on bad *events*, not an unhealthy dependency —
    # the circuit must still be closed, i.e. it keeps calling through.
    assert breaker.allow() is True


def test_name_matches_wrapped_workflow():
    breaker = CircuitBreakerWorkflow(FlakyWorkflow())
    assert breaker.name == "flaky"


def test_allow_reflects_circuit_state_for_health_checks():
    wrapped = FlakyWorkflow()
    breaker = CircuitBreakerWorkflow(wrapped, min_calls=2, failure_threshold=0.5)
    assert breaker.allow() is True
    for _ in range(2):
        try:
            asyncio.run(breaker.run(ctx()))
        except RuntimeError:
            pass
    assert breaker.allow() is False


class ListEventSource(EventSource):
    """Yields a fixed list of events; records ack/nack calls."""

    def __init__(self, events: list[Event]) -> None:
        self._events = events
        self.acked: list[str] = []
        self.nacked: list[str] = []

    async def events(self) -> AsyncIterator[Event]:
        for event in self._events:
            yield event

    async def ack(self, event: Event) -> None:
        self.acked.append(event.id)

    async def nack(self, event: Event, reason: BaseException) -> None:
        self.nacked.append(event.id)


async def _collect(source: EventSource) -> list[Event]:
    return [event async for event in source.events()]


def test_validating_source_passes_through_good_events():
    good = Event(id="e1", source="edr", type="t", payload={"k": "v"})
    wrapped = ListEventSource([good])
    source = ValidatingEventSource(wrapped)
    assert asyncio.run(_collect(source)) == [good]
    assert wrapped.acked == []


def test_validating_source_drops_and_acks_oversized_payload():
    bad = Event(id="e1", source="edr", type="t", payload={"blob": "x" * 20_000})
    wrapped = ListEventSource([bad])
    source = ValidatingEventSource(wrapped)
    assert asyncio.run(_collect(source)) == []
    assert wrapped.acked == ["e1"]  # settled, not left to redeliver forever


def test_validating_source_only_drops_the_invalid_event():
    good = Event(id="e1", source="edr", type="t", payload={})
    bad = Event(id="e2", source="edr", type="t", payload={"blob": "x" * 20_000})
    wrapped = ListEventSource([good, bad])
    source = ValidatingEventSource(wrapped)
    assert asyncio.run(_collect(source)) == [good]
    assert wrapped.acked == ["e2"]


def test_validating_source_reports_to_metrics():
    events_seen = []

    class Recorder:
        def increment(self, name: str, **tags: str) -> None:
            events_seen.append((name, tags))

    bad = Event(id="e1", source="edr", type="t", payload={"blob": "x" * 20_000})
    source = ValidatingEventSource(ListEventSource([bad]), metrics=Recorder())
    asyncio.run(_collect(source))
    assert ("event.validation_failed", {"source": "edr"}) in events_seen


def test_rate_limited_source_passes_events_within_budget():
    events = [Event(id=f"e{i}", source="edr", type="t") for i in range(3)]
    source = RateLimitedEventSource(ListEventSource(events), capacity=10, refill_per_s=0.0)
    assert asyncio.run(_collect(source)) == events


def test_rate_limited_source_defers_events_over_budget():
    events = [Event(id=f"e{i}", source="edr", type="t") for i in range(5)]
    source = RateLimitedEventSource(ListEventSource(events), capacity=2, refill_per_s=0.0)
    result = asyncio.run(_collect(source))
    assert result == events[:2]  # only the first 2 fit in the bucket


def test_rate_limited_source_tracks_sources_independently():
    events = [
        Event(id="e1", source="edr", type="t"),
        Event(id="e2", source="siem", type="t"),
    ]
    source = RateLimitedEventSource(ListEventSource(events), capacity=1, refill_per_s=0.0)
    assert asyncio.run(_collect(source)) == events  # each source has its own budget
