"""Behavioural tests for the circuit breaker's state machine."""

from __future__ import annotations

import time

from circuit_breaker import CircuitBreaker, CircuitState


def make_breaker(**overrides) -> CircuitBreaker:
    defaults = {"name": "test", "failure_threshold": 0.5, "min_calls": 4, "window_size": 4, "reset_after_s": 0.05}
    defaults.update(overrides)
    return CircuitBreaker(**defaults)


def test_starts_closed_and_allows_calls():
    b = make_breaker()
    assert b.state == CircuitState.CLOSED
    assert b.allow() is True


def test_stays_closed_below_min_calls_even_if_all_fail():
    b = make_breaker(min_calls=4)
    b.record_failure()
    b.record_failure()
    assert b.state == CircuitState.CLOSED


def test_opens_when_failure_rate_exceeds_threshold_after_min_calls():
    b = make_breaker(min_calls=4, failure_threshold=0.5)
    b.record_success()
    b.record_failure()
    b.record_failure()
    b.record_failure()
    assert b.state == CircuitState.OPEN
    assert b.allow() is False


def test_stays_closed_when_failure_rate_is_below_threshold():
    b = make_breaker(min_calls=4, failure_threshold=0.5)
    b.record_success()
    b.record_success()
    b.record_success()
    b.record_failure()
    assert b.state == CircuitState.CLOSED


def test_transitions_to_half_open_after_reset_window():
    b = make_breaker(min_calls=2, failure_threshold=0.5, reset_after_s=0.02)
    b.record_failure()
    b.record_failure()
    assert b.state == CircuitState.OPEN
    time.sleep(0.03)
    assert b.state == CircuitState.HALF_OPEN
    assert b.allow() is True


def test_half_open_success_closes_circuit_and_clears_history():
    b = make_breaker(min_calls=2, failure_threshold=0.5, reset_after_s=0.01)
    b.record_failure()
    b.record_failure()
    time.sleep(0.02)
    assert b.state == CircuitState.HALF_OPEN
    b.record_success()
    assert b.state == CircuitState.CLOSED
    # History cleared: a single failure right after shouldn't reopen it.
    b.record_failure()
    assert b.state == CircuitState.CLOSED


def test_half_open_failure_reopens_circuit_for_a_full_window():
    b = make_breaker(min_calls=2, failure_threshold=0.5, reset_after_s=0.01)
    b.record_failure()
    b.record_failure()
    time.sleep(0.02)
    assert b.state == CircuitState.HALF_OPEN
    b.record_failure()
    assert b.state == CircuitState.OPEN
    assert b.time_until_retry() > 0


def test_time_until_retry_counts_down_to_zero():
    b = make_breaker(min_calls=2, failure_threshold=0.5, reset_after_s=0.02)
    b.record_failure()
    b.record_failure()
    assert b.time_until_retry() > 0
    time.sleep(0.03)
    assert b.time_until_retry() == 0.0


def test_metrics_hook_receives_state_transitions():
    events: list[tuple[str, dict[str, str]]] = []

    class Recorder:
        def increment(self, name: str, **tags: str) -> None:
            events.append((name, tags))

    b = make_breaker(min_calls=2, failure_threshold=0.5, metrics=Recorder())
    b.record_failure()
    b.record_failure()
    assert ("circuit.open", {"circuit": "test"}) in events
