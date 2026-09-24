"""
Circuit breaker for protecting workflow execution from cascading failures.

States:
    CLOSED     -> normal operation, calls pass through
    OPEN       -> calls fail fast without attempting the underlying call
    HALF_OPEN  -> one trial call allowed; success closes, failure re-opens

A workflow whose downstream (EDR API, IAM, SOAR) is degraded should stop
hammering it immediately, not retry into an outage. This is orthogonal to
RetryPolicy in agent.py: RetryPolicy governs one event's attempts, the
circuit breaker governs the health of the *workflow* across all events.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

log = logging.getLogger("circuit_breaker")


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised instead of attempting a call while the circuit is OPEN."""

    def __init__(self, name: str, opened_for_s: float) -> None:
        super().__init__(f"circuit {name!r} is open ({opened_for_s:.1f}s remaining)")
        self.name = name


class CircuitMetrics(Protocol):
    def increment(self, name: str, **tags: str) -> None: ...


@dataclass
class CircuitBreaker:
    """Per-dependency failure tracker.

    ponytail: single-process, in-memory counters — fine for one agent
    replica. If you run several replicas behind a queue, each gets its own
    circuit; that's usually correct (a replica-local network issue shouldn't
    open the circuit cluster-wide) but coordinate thresholds if it matters.

    Usage:
        breaker = CircuitBreaker(name="edr_api", failure_threshold=0.5, reset_after_s=30)
        if not breaker.allow():
            raise CircuitOpenError(breaker.name, breaker.time_until_retry())
        try:
            result = await call_downstream()
        except Exception:
            breaker.record_failure()
            raise
        else:
            breaker.record_success()
    """

    name: str
    failure_threshold: float = 0.5  # open when failure rate exceeds this
    min_calls: int = 5  # don't trip on the first couple of failures
    window_size: int = 10  # rolling window of recent outcomes
    reset_after_s: float = 30.0  # how long OPEN lasts before trying HALF_OPEN
    metrics: CircuitMetrics | None = None

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _outcomes: list[bool] = field(default_factory=list, init=False)  # True = success
    _opened_at: float | None = field(default=None, init=False)

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN and self._should_try_half_open():
            self._transition(CircuitState.HALF_OPEN)
        return self._state

    def allow(self) -> bool:
        """Call before attempting the protected operation."""
        return self.state != CircuitState.OPEN

    def time_until_retry(self) -> float:
        if self._opened_at is None:
            return 0.0
        return max(0.0, self.reset_after_s - (time.monotonic() - self._opened_at))

    def record_success(self) -> None:
        self._outcomes.append(True)
        self._trim_window()
        if self._state == CircuitState.HALF_OPEN:
            self._transition(CircuitState.CLOSED)
            self._outcomes.clear()

    def record_failure(self) -> None:
        self._outcomes.append(False)
        self._trim_window()
        if self._state == CircuitState.HALF_OPEN:
            # The trial call failed; back to OPEN for a full reset window.
            self._transition(CircuitState.OPEN)
            return
        if len(self._outcomes) >= self.min_calls and self._failure_rate() >= self.failure_threshold:
            self._transition(CircuitState.OPEN)

    def _failure_rate(self) -> float:
        if not self._outcomes:
            return 0.0
        failures = sum(1 for ok in self._outcomes if not ok)
        return failures / len(self._outcomes)

    def _trim_window(self) -> None:
        if len(self._outcomes) > self.window_size:
            self._outcomes = self._outcomes[-self.window_size :]

    def _should_try_half_open(self) -> bool:
        return self._opened_at is not None and (time.monotonic() - self._opened_at) >= self.reset_after_s

    def _transition(self, new_state: CircuitState) -> None:
        if new_state == self._state:
            return
        old_state = self._state
        self._state = new_state
        if new_state == CircuitState.OPEN:
            self._opened_at = time.monotonic()
        log.warning("circuit %r: %s -> %s", self.name, old_state, new_state)
        if self.metrics is not None:
            self.metrics.increment(f"circuit.{new_state.value}", circuit=self.name)
