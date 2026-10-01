"""Smoke test for load_test.py: not a performance assertion (CI hardware
varies too much for a fixed threshold to be meaningful), just proof the
harness itself produces sane, non-degenerate output -- and that it never
makes a real network call (OfflineFallback, not SearchFallbackStub)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # load_test.py lives at the project root, not src/

from load_test import percentiles, run_load_test


def test_percentiles_of_empty_list_is_all_zero():
    assert percentiles([]) == {"p50": 0.0, "p95": 0.0, "p99": 0.0}


def test_percentiles_of_single_value_repeats_it():
    assert percentiles([5.0]) == {"p50": 5.0, "p95": 5.0, "p99": 5.0}


def test_percentiles_are_nondecreasing():
    result = percentiles([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    assert result["p50"] <= result["p95"] <= result["p99"]


def test_run_load_test_processes_every_query():
    result = asyncio.run(run_load_test(count=20, concurrency=4))
    assert result["count"] == 20
    assert result["throughput_per_s"] > 0
    assert 0.0 <= result["fallback_rate"] <= 1.0
    assert 0.0 <= result["abstain_rate"] <= 1.0


def test_run_load_test_is_fast_with_no_network_calls():
    # OfflineFallback keeps this offline and sub-second even with retries;
    # a real SearchFallbackStub run could take multiple seconds per query
    # on network timeout. This is the check that would fail if someone
    # swapped OfflineFallback back for SearchFallbackStub in load_test.py.
    import time

    started = time.perf_counter()
    asyncio.run(run_load_test(count=20, concurrency=4))
    assert time.perf_counter() - started < 2.0
