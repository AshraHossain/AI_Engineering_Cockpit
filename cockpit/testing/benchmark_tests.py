"""Example timing/benchmark helper for cockpit code.

Uses ``time.perf_counter`` for high-resolution timing with no new
dependencies (no ``pytest-benchmark`` required). Intended as a lightweight
pattern project authors can copy for their own perf-sensitive code paths.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)

_T = TypeVar("_T")


@dataclass(frozen=True)
class BenchmarkResult:
    """Summary statistics for a repeated timing run.

    Attributes:
        name: Label for the benchmarked operation.
        iterations: Number of times the function was invoked.
        total_seconds: Sum of all iteration durations.
        min_seconds: Fastest single iteration.
        max_seconds: Slowest single iteration.
        mean_seconds: Average iteration duration.
    """

    name: str
    iterations: int
    total_seconds: float
    min_seconds: float
    max_seconds: float
    mean_seconds: float


def benchmark(
    func: Callable[[], _T],
    *,
    iterations: int = 10,
    name: str | None = None,
) -> BenchmarkResult:
    """Time repeated calls to a zero-argument callable.

    Args:
        func: A no-argument callable to invoke and time. Use
            ``functools.partial`` or a lambda to bind arguments.
        iterations: Number of times to call ``func``. Must be at least 1.
        name: Human-readable label for logging/reporting. Defaults to
            ``func.__name__``.

    Returns:
        A :class:`BenchmarkResult` summarizing the timing run.

    Raises:
        ValueError: If ``iterations`` is less than 1.
    """
    if iterations < 1:
        raise ValueError("iterations must be at least 1.")

    label: str = name if name is not None else str(getattr(func, "__name__", "benchmarked_call"))
    durations: list[float] = []

    for _ in range(iterations):
        start = time.perf_counter()
        func()
        durations.append(time.perf_counter() - start)

    result = BenchmarkResult(
        name=label,
        iterations=iterations,
        total_seconds=sum(durations),
        min_seconds=min(durations),
        max_seconds=max(durations),
        mean_seconds=sum(durations) / iterations,
    )
    _logger.info(
        "Benchmark '%s': %d iters, mean=%.6fs, min=%.6fs, max=%.6fs",
        result.name,
        result.iterations,
        result.mean_seconds,
        result.min_seconds,
        result.max_seconds,
    )
    return result


def test_benchmark_reports_positive_durations() -> None:
    """Sanity-check example: benchmarking a trivial function yields timings >= 0."""
    result = benchmark(lambda: sum(range(1000)), iterations=5, name="sum_range_1000")

    assert result.iterations == 5
    assert result.total_seconds >= 0.0
    assert result.min_seconds <= result.mean_seconds <= result.max_seconds
