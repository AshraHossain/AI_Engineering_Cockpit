"""Latency and throughput metrics for model calls (Tier 2 — skeleton).

Disabled by default; gated by ``feature_flags.is_enabled("monitoring")``.
Every function is a typed skeleton pending Tier 2 implementation. See
``docs/ARCHITECTURE.md`` for the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LatencySample:
    """A single recorded latency measurement.

    Attributes:
        operation: Name of the operation measured (e.g. "gemini.generate").
        duration_seconds: Wall-clock duration of the operation.
    """

    operation: str
    duration_seconds: float


@dataclass(frozen=True)
class PerformanceSummary:
    """Aggregated latency statistics for an operation.

    Attributes:
        operation: Name of the operation summarized.
        sample_count: Number of samples aggregated.
        p50_seconds: Median latency.
        p95_seconds: 95th-percentile latency.
        p99_seconds: 99th-percentile latency.
    """

    operation: str
    sample_count: int
    p50_seconds: float
    p95_seconds: float
    p99_seconds: float


def record_latency(operation: str, duration_seconds: float) -> LatencySample:
    """Record a single latency measurement for an operation.

    Args:
        operation: Name of the operation measured.
        duration_seconds: Wall-clock duration of the operation.

    Returns:
        The recorded :class:`LatencySample`.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")


def summarize_performance(operation: str) -> PerformanceSummary:
    """Compute latency percentile statistics for an operation.

    Args:
        operation: Name of the operation to summarize.

    Returns:
        A :class:`PerformanceSummary` with p50/p95/p99 latencies.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")
