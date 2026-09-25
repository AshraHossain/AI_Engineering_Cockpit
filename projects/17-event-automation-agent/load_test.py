#!/usr/bin/env python3
"""
Load test for Project 17 (Event Automation Agent).

Feeds N synthetic events through the real pipeline (trigger evaluation,
idempotency claim/settle, workflow execution with retries) using in-memory
stores, and reports throughput plus workflow-latency percentiles.

In-memory, not the file-backed integration stubs, on purpose: this
measures the pipeline's own overhead. Point a variant of this script at
FileEventSource / FileIdempotencyStore if you want to baseline the
stub-backed stack (disk I/O) specifically -- that number will be smaller
and dominated by file writes, not agent logic.

Usage:
    PYTHONPATH=src python load_test.py --count 1000 --concurrency 16
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
import time
from collections.abc import AsyncIterator

from agent import (
    AutomationAgent,
    Event,
    EventSource,
    InMemoryDeadLetterQueue,
    InMemoryIdempotencyStore,
    RetryPolicy,
    TriggerEvaluator,
    TriggerRule,
    Workflow,
    WorkflowContext,
    WorkflowExecutor,
)

# WARNING, not INFO: at load-test volumes, per-event logging would dominate
# the wall-clock time being measured and understate real throughput.
logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("load_test")


class SyntheticEventSource(EventSource):
    """Yields `count` synthetic events immediately, no backing store."""

    def __init__(self, count: int) -> None:
        self._count = count

    async def events(self) -> AsyncIterator[Event]:
        for i in range(self._count):
            yield Event(id=f"load-{i}", source="loadtest", type="synthetic")

    async def ack(self, event: Event) -> None:
        pass

    async def nack(self, event: Event, reason: BaseException) -> None:
        pass


class NoopWorkflow(Workflow):
    """Does nothing -- isolates pipeline overhead from workflow work."""

    name = "noop"

    async def run(self, ctx: WorkflowContext) -> None:
        pass


class RecordingMetrics:
    """Captures counters and timings in memory for percentile analysis."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.timings: dict[str, list[float]] = {}

    def increment(self, name: str, **tags: str) -> None:
        self.counters[name] = self.counters.get(name, 0) + 1

    def timing(self, name: str, value_ms: float, **tags: str) -> None:
        self.timings.setdefault(name, []).append(value_ms)


def percentiles(values: list[float]) -> dict[str, float]:
    """p50/p95/p99 of `values`, or all-zero if too few samples to compute."""
    if len(values) < 2:
        v = values[0] if values else 0.0
        return {"p50": v, "p95": v, "p99": v}
    q = statistics.quantiles(values, n=100, method="inclusive")
    return {"p50": q[49], "p95": q[94], "p99": q[98]}


async def run_load_test(count: int, concurrency: int) -> dict:
    """Run the pipeline over `count` synthetic events; return raw results."""
    source = SyntheticEventSource(count)
    store = InMemoryIdempotencyStore()
    dlq = InMemoryDeadLetterQueue()
    metrics = RecordingMetrics()
    triggers = TriggerEvaluator([TriggerRule(name="all", matches=lambda e: True, workflows=("noop",))])
    executor = WorkflowExecutor(
        workflows=[NoopWorkflow()],
        retry_policy=RetryPolicy(max_attempts=1),
        dlq=dlq,
        metrics=metrics,
    )
    agent = AutomationAgent(source, triggers, executor, store, metrics, max_concurrency=concurrency)

    started = time.perf_counter()
    await agent.run()
    elapsed_s = time.perf_counter() - started

    return {
        "count": count,
        "elapsed_s": elapsed_s,
        "throughput_per_s": count / elapsed_s if elapsed_s > 0 else float("inf"),
        "dead_lettered": len(dlq.letters),
        "workflow_latency_ms": percentiles(metrics.timings.get("workflow.duration_ms", [])),
        "counters": metrics.counters,
    }


def _print_report(result: dict) -> None:
    pct = result["workflow_latency_ms"]
    count = result["count"]
    print(f"Events processed:     {count}")
    print(f"Wall time:            {result['elapsed_s']:.3f}s")
    print(f"Throughput:           {result['throughput_per_s']:.1f} events/sec")
    print(f"Dead-lettered:        {result['dead_lettered']} ({result['dead_lettered'] / count:.1%})")
    print(f"Workflow latency p50: {pct['p50']:.3f}ms")
    print(f"Workflow latency p95: {pct['p95']:.3f}ms")
    print(f"Workflow latency p99: {pct['p99']:.3f}ms")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load test the event automation pipeline")
    parser.add_argument("--count", type=int, default=1000, help="number of synthetic events")
    parser.add_argument("--concurrency", type=int, default=16, help="max concurrent in-flight events")
    args = parser.parse_args()

    result = asyncio.run(run_load_test(args.count, args.concurrency))
    _print_report(result)


if __name__ == "__main__":
    main()
