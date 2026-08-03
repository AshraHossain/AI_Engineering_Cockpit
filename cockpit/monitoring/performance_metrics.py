"""Latency and throughput accounting for model calls.

Two layers, mirroring :mod:`cockpit.monitoring.cost_tracking`:

* :func:`percentile` and :func:`summarize_samples` are pure functions — given
  numbers they return numbers. No state, no I/O, no feature flag, trivially
  testable.
* :class:`PerformanceTracker` accumulates :class:`LatencySample` records and
  rolls them up. Module-level :func:`record_latency` /
  :func:`summarize_performance` delegate to a shared default tracker for
  convenience, the same way :mod:`logging` exposes a root logger.

Durations come from :func:`time.perf_counter`, which is monotonic: a
wall-clock adjustment (NTP step, DST change) part-way through a call cannot
produce a negative or wildly inflated latency.

Gated by ``feature_flags.is_enabled("monitoring")`` at the module-level
recording entry point only. The percentile math, summary math, and the
tracker's own methods are always available so a caller holding its own
tracker can measure without turning the framework on globally.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from functools import wraps
from typing import ParamSpec, TypeVar

from cockpit.config.feature_flags import is_enabled
from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)

_P = ParamSpec("_P")
_R = TypeVar("_R")


class CallOutcome(StrEnum):
    """Whether the measured call finished cleanly.

    Attributes:
        SUCCESS: The operation returned normally.
        ERROR: The operation raised, timed out, or was explicitly marked
            failed by the caller.
    """

    SUCCESS = "success"
    ERROR = "error"


@dataclass(frozen=True)
class LatencySample:
    """A single recorded latency measurement.

    Attributes:
        operation: Name of the operation measured (e.g. "gemini.generate").
        duration_seconds: Elapsed duration of the operation, in seconds, as
            measured on a monotonic clock.
        outcome: Whether the call succeeded or failed.
        input_tokens: Prompt tokens processed by the call, when known.
        output_tokens: Completion tokens produced by the call, when known.
    """

    operation: str
    duration_seconds: float
    outcome: CallOutcome = CallOutcome.SUCCESS
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class PerformanceSummary:
    """Aggregated latency statistics for an operation.

    Attributes:
        operation: Name of the operation summarized.
        sample_count: Number of samples aggregated.
        total_seconds: Sum of all sample durations.
        mean_seconds: Arithmetic mean duration.
        min_seconds: Fastest observed duration.
        max_seconds: Slowest observed duration.
        p50_seconds: Median latency.
        p95_seconds: 95th-percentile latency.
        p99_seconds: 99th-percentile latency.
        success_count: Samples whose outcome was :attr:`CallOutcome.SUCCESS`.
        error_count: Samples whose outcome was :attr:`CallOutcome.ERROR`.
        total_input_tokens: Prompt tokens across all samples.
        total_output_tokens: Completion tokens across all samples.
    """

    operation: str
    sample_count: int
    total_seconds: float
    mean_seconds: float
    min_seconds: float
    max_seconds: float
    p50_seconds: float
    p95_seconds: float
    p99_seconds: float
    success_count: int = 0
    error_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    @classmethod
    def empty(cls, operation: str) -> PerformanceSummary:
        """Build the zero-valued summary for an operation with no samples.

        Args:
            operation: Name of the operation summarized.

        Returns:
            A summary whose every numeric field is zero. Callers can render
            it without special-casing the "nothing recorded yet" state.
        """
        return cls(
            operation=operation,
            sample_count=0,
            total_seconds=0.0,
            mean_seconds=0.0,
            min_seconds=0.0,
            max_seconds=0.0,
            p50_seconds=0.0,
            p95_seconds=0.0,
            p99_seconds=0.0,
        )

    @property
    def error_rate(self) -> float:
        """Fraction of samples that failed.

        Returns:
            ``error_count / sample_count`` in the range 0.0-1.0, or 0.0 when
            no samples were recorded.
        """
        if self.sample_count <= 0:
            return 0.0
        return self.error_count / self.sample_count

    @property
    def tokens_per_second(self) -> float:
        """Combined token throughput across all samples.

        Returns:
            ``(input + output) / total_seconds``, or 0.0 when no time or no
            tokens were recorded.
        """
        total_tokens = self.total_input_tokens + self.total_output_tokens
        if self.total_seconds <= 0.0 or total_tokens <= 0:
            return 0.0
        return total_tokens / self.total_seconds


def _percentile_of_sorted(ordered: Sequence[float], percentile_rank: float) -> float:
    """Nearest-rank percentile of an already-sorted sequence.

    Uses the inclusive nearest-rank definition: the value at 1-based index
    ``ceil(rank% * n)``, clamped into ``[1, n]``. The result is therefore
    always a value that was actually observed — no interpolation invents a
    latency nobody measured. Clamping is what makes the small-sample cases
    behave: with ``n == 1`` every rank resolves to index 0, so a single
    sample is its own p50, p95 and p99.

    Args:
        ordered: Sample values sorted ascending. Not re-sorted here.
        percentile_rank: Rank in the range 0-100.

    Returns:
        The selected value, or 0.0 when ``ordered`` is empty.
    """
    count = len(ordered)
    if count == 0:
        return 0.0
    # `percentile_rank * count` is exact for integer ranks, so dividing by
    # 100 lands exactly on the integer boundary cases (e.g. p50 of n=10 is
    # exactly 5.0, not 5.000000000000001, which would round up to rank 6).
    rank = math.ceil(percentile_rank * count / 100.0)
    index = min(max(rank, 1), count) - 1
    return float(ordered[index])


def percentile(values: Sequence[float], percentile_rank: float) -> float:
    """Compute the nearest-rank percentile of a sequence of numbers.

    Pure function — safe to call regardless of feature flags.

    Args:
        values: Sample values in any order. May be empty.
        percentile_rank: Rank to compute, from 0 to 100 inclusive.

    Returns:
        The percentile value. Returns 0.0 for an empty sequence, and the sole
        value for a one-element sequence at every rank.

    Raises:
        ValueError: If ``percentile_rank`` is outside 0-100.
    """
    if not 0.0 <= percentile_rank <= 100.0:
        raise ValueError("percentile_rank must be between 0 and 100 inclusive.")
    return _percentile_of_sorted(sorted(values), percentile_rank)


def summarize_samples(operation: str, samples: Sequence[LatencySample]) -> PerformanceSummary:
    """Roll a batch of samples up into a :class:`PerformanceSummary`.

    Pure function — it reads the samples handed to it and touches no shared
    state, so it is not feature-flag gated.

    Args:
        operation: Name to label the resulting summary with.
        samples: Samples to aggregate. May be empty.

    Returns:
        The aggregated summary, or :meth:`PerformanceSummary.empty` when
        ``samples`` is empty.
    """
    if not samples:
        return PerformanceSummary.empty(operation)

    durations = sorted(sample.duration_seconds for sample in samples)
    count = len(durations)
    total = math.fsum(durations)
    errors = sum(1 for sample in samples if sample.outcome is CallOutcome.ERROR)

    return PerformanceSummary(
        operation=operation,
        sample_count=count,
        total_seconds=total,
        mean_seconds=total / count,
        min_seconds=durations[0],
        max_seconds=durations[-1],
        p50_seconds=_percentile_of_sorted(durations, 50.0),
        p95_seconds=_percentile_of_sorted(durations, 95.0),
        p99_seconds=_percentile_of_sorted(durations, 99.0),
        success_count=count - errors,
        error_count=errors,
        total_input_tokens=sum(sample.input_tokens for sample in samples),
        total_output_tokens=sum(sample.output_tokens for sample in samples),
    )


@dataclass
class Measurement:
    """Mutable handle yielded by :meth:`PerformanceTracker.measure`.

    Unlike the frozen result objects in this package this one is deliberately
    mutable: it is a live scratchpad the caller writes token counts (and, if
    needed, a failure outcome) into *while* the timed block is still running.
    It is converted into an immutable :class:`LatencySample` on exit.

    Attributes:
        operation: Name of the operation being measured.
        input_tokens: Prompt tokens; set inside the block once known.
        output_tokens: Completion tokens; set inside the block once known.
        outcome: Defaults to success, flipped to error automatically if the
            block raises. Assign it directly to flag a soft failure that did
            not raise (e.g. a refusal or an empty response).
        duration_seconds: Populated when the block exits; 0.0 before that.
    """

    operation: str
    input_tokens: int = 0
    output_tokens: int = 0
    outcome: CallOutcome = CallOutcome.SUCCESS
    duration_seconds: float = 0.0


@dataclass
class PerformanceTracker:
    """Accumulates latency samples and rolls them up.

    Thread-safe: model calls are commonly fanned out across a thread pool, so
    appends and aggregations are guarded by a lock.

    Attributes:
        samples: All recorded samples, oldest first.
        clock: Monotonic clock used for timing. Injectable so tests can feed
            deterministic durations instead of sleeping.
    """

    samples: list[LatencySample] = field(default_factory=list)
    clock: Callable[[], float] = field(default=time.perf_counter, repr=False, compare=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def record(
        self,
        operation: str,
        duration_seconds: float,
        *,
        outcome: CallOutcome = CallOutcome.SUCCESS,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> LatencySample:
        """Record one latency measurement.

        Args:
            operation: Name of the operation measured.
            duration_seconds: Elapsed duration in seconds. Must be
                non-negative.
            outcome: Whether the call succeeded or failed.
            input_tokens: Prompt tokens processed, when known.
            output_tokens: Completion tokens produced, when known.

        Returns:
            The recorded :class:`LatencySample`.

        Raises:
            ValueError: If ``duration_seconds`` or either token count is
                negative.
        """
        if duration_seconds < 0:
            raise ValueError("duration_seconds must be non-negative.")
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token counts must be non-negative.")

        sample = LatencySample(
            operation=operation,
            duration_seconds=duration_seconds,
            outcome=outcome,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        with self._lock:
            self.samples.append(sample)
        return sample

    @contextmanager
    def measure(
        self,
        operation: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> Iterator[Measurement]:
        """Time a block of code and record the result.

        The sample is recorded even when the block raises — the exception
        propagates unchanged, but the call is booked as
        :attr:`CallOutcome.ERROR` so failures show up in the error rate
        instead of silently vanishing from the latency distribution.

        Example:
            >>> tracker = PerformanceTracker()
            >>> with tracker.measure("gemini.generate") as call:
            ...     call.output_tokens = 128
            >>> tracker.call_count("gemini.generate")
            1

        Args:
            operation: Name of the operation being measured.
            input_tokens: Prompt tokens, if already known up front.
            output_tokens: Completion tokens, if already known up front.

        Yields:
            A mutable :class:`Measurement` handle to attach token counts (or
            a failure outcome) to before the block exits.
        """
        handle = Measurement(
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        start = self.clock()
        try:
            yield handle
        except BaseException:
            handle.outcome = CallOutcome.ERROR
            raise
        finally:
            handle.duration_seconds = max(self.clock() - start, 0.0)
            self.record(
                handle.operation,
                handle.duration_seconds,
                outcome=handle.outcome,
                input_tokens=handle.input_tokens,
                output_tokens=handle.output_tokens,
            )

    def timed(self, operation: str | None = None) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
        """Build a decorator that times every call to a function.

        Example:
            >>> tracker = PerformanceTracker()
            >>> @tracker.timed("embed")
            ... def embed(text: str) -> int:
            ...     return len(text)
            >>> embed("abc")
            3

        Args:
            operation: Name to record under. Defaults to the decorated
                function's qualified name.

        Returns:
            A decorator that preserves the wrapped function's signature and
            records one sample per call.
        """

        def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
            name = operation or func.__qualname__

            @wraps(func)
            def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
                with self.measure(name):
                    return func(*args, **kwargs)

            return wrapper

        return decorator

    def samples_for(self, operation: str) -> list[LatencySample]:
        """Return every sample recorded for one operation.

        Args:
            operation: Name of the operation.

        Returns:
            A copy of the matching samples, oldest first.
        """
        with self._lock:
            return [sample for sample in self.samples if sample.operation == operation]

    def operations(self) -> list[str]:
        """List the operations that have at least one sample.

        Returns:
            Operation names sorted alphabetically.
        """
        with self._lock:
            return sorted({sample.operation for sample in self.samples})

    def summary(self, operation: str) -> PerformanceSummary:
        """Summarize the latency distribution of one operation.

        Args:
            operation: Name of the operation to summarize.

        Returns:
            A :class:`PerformanceSummary`; the zero-valued summary if the
            operation has no samples (unknown operations do not raise).
        """
        return summarize_samples(operation, self.samples_for(operation))

    def summaries(self) -> dict[str, PerformanceSummary]:
        """Summarize every operation seen so far.

        Returns:
            Mapping of operation name to its :class:`PerformanceSummary`,
            ordered alphabetically by operation.
        """
        with self._lock:
            grouped: dict[str, list[LatencySample]] = {}
            for sample in self.samples:
                grouped.setdefault(sample.operation, []).append(sample)
        return {name: summarize_samples(name, grouped[name]) for name in sorted(grouped)}

    def call_count(self, operation: str | None = None) -> int:
        """Count recorded calls, optionally scoped to one operation.

        Args:
            operation: If given, only count samples for this operation.

        Returns:
            Number of matching samples.
        """
        with self._lock:
            return sum(1 for s in self.samples if operation is None or s.operation == operation)

    def error_rate(self, operation: str | None = None) -> float:
        """Fraction of recorded calls that failed.

        Args:
            operation: If given, only consider samples for this operation.

        Returns:
            A value in 0.0-1.0, or 0.0 when nothing has been recorded.
        """
        with self._lock:
            matching = [s for s in self.samples if operation is None or s.operation == operation]
        if not matching:
            return 0.0
        errors = sum(1 for s in matching if s.outcome is CallOutcome.ERROR)
        return errors / len(matching)

    def total_tokens(self) -> tuple[int, int]:
        """Sum tokens across all recorded samples.

        Returns:
            An ``(input_tokens, output_tokens)`` pair.
        """
        with self._lock:
            return (
                sum(s.input_tokens for s in self.samples),
                sum(s.output_tokens for s in self.samples),
            )

    def reset(self) -> None:
        """Drop all recorded samples."""
        with self._lock:
            self.samples.clear()


# Shared default tracker backing the module-level convenience functions.
_default_tracker = PerformanceTracker()


def get_default_tracker() -> PerformanceTracker:
    """Return the process-wide default :class:`PerformanceTracker`.

    Returns:
        The tracker backing the module-level convenience functions. Tests
        should prefer constructing their own :class:`PerformanceTracker` over
        mutating this one.
    """
    return _default_tracker


def record_latency(
    operation: str,
    duration_seconds: float,
    *,
    outcome: CallOutcome = CallOutcome.SUCCESS,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> LatencySample | None:
    """Record a single latency measurement on the default tracker.

    Args:
        operation: Name of the operation measured.
        duration_seconds: Elapsed duration in seconds.
        outcome: Whether the call succeeded or failed.
        input_tokens: Prompt tokens processed, when known.
        output_tokens: Completion tokens produced, when known.

    Returns:
        The recorded :class:`LatencySample`, or ``None`` if the monitoring
        framework is disabled (in which case nothing is recorded).

    Raises:
        ValueError: If ``duration_seconds`` or either token count is
            negative.
    """
    if not is_enabled("monitoring"):
        _logger.debug("Monitoring framework disabled; skipping latency record.")
        return None
    return _default_tracker.record(
        operation,
        duration_seconds,
        outcome=outcome,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def summarize_performance(operation: str) -> PerformanceSummary:
    """Compute latency percentile statistics for an operation.

    Args:
        operation: Name of the operation to summarize.

    Returns:
        A :class:`PerformanceSummary` with p50/p95/p99 latencies, drawn from
        the default tracker. Unknown operations yield the zero-valued
        summary rather than raising.
    """
    return _default_tracker.summary(operation)
