"""Behavioural tests for CircuitBreakerWorkflow: the one integration piece
with logic worth pinning (the file-backed stores are thin I/O wrappers)."""

from __future__ import annotations

import asyncio

from agent import Event, PermanentError, WorkflowContext
from integrations import CircuitBreakerWorkflow


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
    assert breaker._breaker.allow() is True


def test_name_matches_wrapped_workflow():
    breaker = CircuitBreakerWorkflow(FlakyWorkflow())
    assert breaker.name == "flaky"
