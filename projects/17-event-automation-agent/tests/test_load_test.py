"""Smoke test for load_test.py: not a performance assertion (CI hardware
varies too much for a fixed threshold to be meaningful), just proof the
harness itself produces sane, non-degenerate output."""

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


def test_run_load_test_processes_every_event_with_no_dead_letters():
    result = asyncio.run(run_load_test(count=50, concurrency=8))
    assert result["count"] == 50
    assert result["dead_lettered"] == 0
    assert result["counters"]["event.received"] == 50
    assert result["counters"]["workflow.succeeded"] == 50
    assert result["throughput_per_s"] > 0
