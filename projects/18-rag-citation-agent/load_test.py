#!/usr/bin/env python3
"""
Load test for Project 18 (RAG Citation Agent).

Runs N synthetic queries through the real pipeline (retrieve, generate,
ground, score, fallback-if-needed) using MockLLM and the in-memory corpus
retriever, and reports throughput, latency percentiles, fallback rate, and
abstention rate.

MockLLM and an offline fallback, not real providers, on purpose: this
measures the agent's own grounding/scoring overhead, not network latency to
Claude, a real vector DB, or a real search API. SearchFallbackStub (the
"realistic" integration stub) attempts a live request to a public SearXNG
instance before giving up and returning mock results -- correct for a demo,
wrong for a load test, where it would make every fallback query pay a real
network round-trip (or a timeout, in a sandboxed/offline CI runner) instead
of measuring this codebase. Swap in LLMWithClaude / a real Retriever /
SearchFallbackStub to baseline the fully integrated stack instead.

Usage:
    PYTHONPATH=src python load_test.py --count 500 --concurrency 16
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
import time

from agent import ContextItem, Query, RAGAgent, SearchFallback
from integrations import FileBasedRetriever, MockLLM

# WARNING, not INFO: at load-test volumes, per-query logging would dominate
# the wall-clock time being measured and understate real throughput.
logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("load_test")

# MockLLM's canned answers are keyed on keywords in the query text; cycling
# through a few keeps retrieval and grounding exercised realistically
# instead of hammering one cached path.
_SAMPLE_QUERIES = [
    "What is Python used for?",
    "How does asyncio work?",
    "What is retrieval-augmented generation?",
    "Explain TypeScript",
    "What is the meaning of life?",  # deliberately unanswerable -> abstains
]


class NullMetrics:
    """Discards everything -- avoids file I/O skewing the throughput measurement."""

    def increment(self, name: str, **tags: str) -> None:
        pass

    def observe(self, name: str, value: float, **tags: str) -> None:
        pass

    def timing(self, name: str, value_ms: float, **tags: str) -> None:
        pass


class OfflineFallback(SearchFallback):
    """Fixed mock result, no network calls -- see module docstring for why
    this isn't SearchFallbackStub."""

    async def search(self, query: Query) -> list[ContextItem]:
        return [
            ContextItem(
                id="offline-fallback",
                source_uri="local://offline-fallback",
                title="offline fallback result",
                snippet="Load-test fallback stand-in; carries no real content.",
                score=0.5,
            )
        ]


def percentiles(values: list[float]) -> dict[str, float]:
    """p50/p95/p99 of `values`, or all-zero if too few samples to compute."""
    if len(values) < 2:
        v = values[0] if values else 0.0
        return {"p50": v, "p95": v, "p99": v}
    q = statistics.quantiles(values, n=100, method="inclusive")
    return {"p50": q[49], "p95": q[94], "p99": q[98]}


async def _time_one(agent: RAGAgent, text: str) -> tuple[float, bool, bool]:
    """Run one query; return (latency_ms, used_fallback, abstained)."""
    started = time.perf_counter()
    answer = await agent.answer(Query(text=text))
    elapsed_ms = (time.perf_counter() - started) * 1000
    return elapsed_ms, answer.used_fallback, answer.confidence == 0.0


async def run_load_test(count: int, concurrency: int) -> dict:
    """Run `count` synthetic queries through the pipeline; return raw results."""
    agent = RAGAgent(
        FileBasedRetriever(),
        MockLLM(),
        OfflineFallback(),
        metrics=NullMetrics(),
    )
    sem = asyncio.Semaphore(concurrency)

    async def bounded(text: str) -> tuple[float, bool, bool]:
        async with sem:
            return await _time_one(agent, text)

    started = time.perf_counter()
    results = await asyncio.gather(*(bounded(_SAMPLE_QUERIES[i % len(_SAMPLE_QUERIES)]) for i in range(count)))
    elapsed_s = time.perf_counter() - started

    latencies = [r[0] for r in results]
    fallback_count = sum(1 for r in results if r[1])
    abstain_count = sum(1 for r in results if r[2])

    return {
        "count": count,
        "elapsed_s": elapsed_s,
        "throughput_per_s": count / elapsed_s if elapsed_s > 0 else float("inf"),
        "latency_ms": percentiles(latencies),
        "fallback_rate": fallback_count / count,
        "abstain_rate": abstain_count / count,
    }


def _print_report(result: dict) -> None:
    pct = result["latency_ms"]
    print(f"Queries processed:  {result['count']}")
    print(f"Wall time:          {result['elapsed_s']:.3f}s")
    print(f"Throughput:         {result['throughput_per_s']:.1f} queries/sec")
    print(f"Fallback rate:      {result['fallback_rate']:.1%}")
    print(f"Abstain rate:       {result['abstain_rate']:.1%}")
    print(f"Latency p50:        {pct['p50']:.2f}ms")
    print(f"Latency p95:        {pct['p95']:.2f}ms")
    print(f"Latency p99:        {pct['p99']:.2f}ms")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load test the RAG citation pipeline")
    parser.add_argument("--count", type=int, default=500, help="number of synthetic queries")
    parser.add_argument("--concurrency", type=int, default=16, help="max concurrent in-flight queries")
    args = parser.parse_args()

    result = asyncio.run(run_load_test(args.count, args.concurrency))
    _print_report(result)


if __name__ == "__main__":
    main()
