"""Tests for the batch pipeline: retries, partial failure, and throttling.

Determinism strategy, since none of this may sleep for real or touch a
network:

* the unit of work is an injected fake ``process_fn``;
* the ``PerformanceTracker``'s clock is a fake that advances a fixed step per
  read, so every measured attempt has an exact latency;
* ``run_batch``'s elapsed-time clock is injected separately, so throughput is
  exact;
* retry backoff is configured to 1ms, so exhausting a retry budget costs
  microseconds rather than seconds;
* rate-limit *counts* are asserted against a fake limiter with scripted
  answers. One test additionally exercises the real
  ``RateLimiter`` and asserts only a lower bound, because a token bucket's
  exact refill depends on the platform's monotonic-clock granularity.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import main
import pipeline
import pytest
from pipeline import (
    BatchResult,
    InputRecord,
    PipelineConfig,
    ProcessOutput,
    RecordStatus,
    load_records,
    make_offline_process_fn,
    run_batch,
)

from cockpit.monitoring.cost_tracking import CostTracker, estimate_cost
from cockpit.monitoring.performance_metrics import CallOutcome, PerformanceTracker
from cockpit.utils.error_handling import ConfigurationError, TransientError
from cockpit.utils.rate_limiting import RateLimiter

MODEL = "gemini-2.5-flash"
STEP = 0.02
FAST_RETRY = {"initial_delay_seconds": 0.001, "backoff_multiplier": 1.0}


def fixed_step_clock(step: float = STEP) -> Callable[[], float]:
    """Build a fake monotonic clock that advances a fixed step per read.

    ``PerformanceTracker.measure`` reads its clock exactly twice per attempt,
    so every attempt is recorded with a latency of exactly ``step``.

    Args:
        step: Seconds to advance on each read.

    Returns:
        A zero-argument callable returning a monotonically increasing float.
    """
    state = {"now": 0.0}

    def clock() -> float:
        current = state["now"]
        state["now"] = current + step
        return current

    return clock


def scripted_clock(readings: Sequence[float]) -> Callable[[], float]:
    """Build a clock that returns a pre-scripted sequence of readings.

    Args:
        readings: Values to return, in order. The last value repeats if the
            script runs out.

    Returns:
        A zero-argument callable returning the next scripted reading.
    """
    state = {"index": 0}
    values = list(readings)

    def clock() -> float:
        value = values[min(state["index"], len(values) - 1)]
        state["index"] += 1
        return value

    return clock


@dataclass
class FakeLimiter:
    """A rate limiter with scripted answers, for exact throttle assertions.

    Attributes:
        immediate: How many ``try_acquire`` calls succeed without waiting.
            Every call after that reports the bucket empty.
        acquire_succeeds: What the blocking ``acquire`` returns.
        try_calls: Number of ``try_acquire`` calls seen.
        acquire_calls: Number of blocking ``acquire`` calls seen.
    """

    immediate: int = 0
    acquire_succeeds: bool = True
    try_calls: int = field(default=0, init=False)
    acquire_calls: int = field(default=0, init=False)

    def try_acquire(self, tokens: int = 1) -> bool:
        """Consume a token if the script still allows an immediate one.

        Args:
            tokens: Ignored; present to satisfy the protocol.

        Returns:
            True for the first :attr:`immediate` calls, then False.
        """
        self.try_calls += 1
        return self.try_calls <= self.immediate

    def acquire(self, tokens: int = 1, timeout_seconds: float | None = None) -> bool:
        """Record a blocking acquisition and return the scripted answer.

        Args:
            tokens: Ignored; present to satisfy the protocol.
            timeout_seconds: Ignored; present to satisfy the protocol.

        Returns:
            :attr:`acquire_succeeds`. Never actually blocks.
        """
        self.acquire_calls += 1
        return self.acquire_succeeds


def records(count: int) -> list[InputRecord]:
    """Build a list of simple numbered input records.

    Args:
        count: How many records to build.

    Returns:
        Records with ids ``r1``..``rN``.
    """
    return [InputRecord(record_id=f"r{i}", text=f"text {i}") for i in range(1, count + 1)]


def constant_process_fn(input_tokens: int = 100, output_tokens: int = 20) -> pipeline.ProcessFn:
    """Build a fake ``process_fn`` that always succeeds with fixed usage.

    Args:
        input_tokens: Prompt tokens every record reports.
        output_tokens: Completion tokens every record reports.

    Returns:
        An ``InputRecord -> ProcessOutput`` callable.
    """

    def process(record: InputRecord) -> ProcessOutput:
        return ProcessOutput(
            text=f"ok:{record.record_id}",
            model=MODEL,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    return process


def run(
    input_records: Sequence[InputRecord],
    process_fn: pipeline.ProcessFn,
    config: PipelineConfig | None = None,
    clock: Callable[[], float] | None = None,
) -> tuple[BatchResult, CostTracker, PerformanceTracker]:
    """Run a batch over fresh trackers with fully injected clocks.

    Args:
        input_records: Records to process.
        process_fn: The fake unit of work.
        config: Pipeline configuration. Defaults to a 1ms-backoff config.
        clock: Elapsed-time clock. Defaults to a two-reading scripted clock
            giving exactly 1.0 second of elapsed time.

    Returns:
        A ``(result, cost_tracker, performance_tracker)`` triple.
    """
    costs = CostTracker()
    perf = PerformanceTracker(clock=fixed_step_clock())
    result = run_batch(
        input_records,
        process_fn,
        cost_tracker=costs,
        performance_tracker=perf,
        config=config or PipelineConfig(**FAST_RETRY),
        clock=clock or scripted_clock([0.0, 1.0]),
    )
    return result, costs, perf


# --------------------------------------------------------------------------
# Input loading
# --------------------------------------------------------------------------


def test_load_records_reads_the_bundled_inputs() -> None:
    loaded = load_records()
    assert len(loaded) == 18
    assert loaded[0].record_id == "r01"
    assert all(record.text for record in loaded)


def test_load_records_honours_the_limit() -> None:
    assert [r.record_id for r in load_records(limit=3)] == ["r01", "r02", "r03"]


def test_load_records_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "in.jsonl"
    path.write_text('{"id": "a", "text": "x"}\n\n{"id": "b", "text": "y"}\n', encoding="utf-8")
    assert len(load_records(path)) == 2


def test_load_records_rejects_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "in.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_records(path)


def test_load_records_rejects_missing_fields(tmp_path: Path) -> None:
    path = tmp_path / "in.jsonl"
    path.write_text('{"id": "a"}\n', encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_records(path)


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


def test_happy_path_records_cost_latency_and_throughput() -> None:
    result, costs, perf = run(records(3), constant_process_fn())

    assert len(result.succeeded) == 3
    assert result.failed == ()
    assert result.errors == ()
    assert result.total_attempts == 3
    assert result.throttled_count == 0

    expected_cost = 3 * estimate_cost(MODEL, 100, 20)
    assert result.total_cost_usd == pytest.approx(expected_cost)
    assert costs.total_cost() == pytest.approx(expected_cost)
    assert costs.total_tokens() == (300, 60)
    assert costs.cost_by_project() == {"10-batch-pipeline": pytest.approx(expected_cost)}

    assert perf.call_count() == 3
    assert perf.error_rate() == 0.0
    assert perf.summary("batch.process").p50_seconds == pytest.approx(STEP)
    assert all(r.latency_seconds == pytest.approx(STEP) for r in result.results)

    # Elapsed comes from the injected clock: readings 0.0 then 1.0.
    assert result.elapsed_seconds == pytest.approx(1.0)
    assert result.records_per_second == pytest.approx(3.0)


def test_every_result_carries_its_output_and_status() -> None:
    result, _, _ = run(records(2), constant_process_fn())
    first = result.results[0]
    assert first.status is RecordStatus.SUCCEEDED
    assert first.status == "succeeded"
    assert first.output is not None
    assert first.output.text == "ok:r1"
    assert first.attempts == 1


def test_empty_batch_is_a_valid_no_op() -> None:
    result, costs, perf = run([], constant_process_fn())
    assert result.results == ()
    assert result.total_cost_usd == 0.0
    assert result.records_per_second == 0.0
    assert costs.entries == []
    assert perf.call_count() == 0


# --------------------------------------------------------------------------
# Partial failure
# --------------------------------------------------------------------------


def test_a_permanently_failing_record_does_not_kill_the_run() -> None:
    def process(record: InputRecord) -> ProcessOutput:
        if record.record_id == "r2":
            raise ValueError("record r2 is malformed")
        return ProcessOutput(text="ok", model=MODEL, input_tokens=10, output_tokens=2)

    result, costs, perf = run(records(4), process)

    assert len(result.results) == 4
    assert [r.record_id for r in result.succeeded] == ["r1", "r3", "r4"]
    assert [r.record_id for r in result.failed] == ["r2"]
    assert result.errors == (("r2", "ValueError: record r2 is malformed"),)

    failure = result.failed[0]
    assert failure.status is RecordStatus.FAILED
    assert failure.output is None
    assert failure.cost_usd == 0.0
    # The failed attempt was still timed, so it shows up in the error rate.
    assert failure.latency_seconds == pytest.approx(STEP)

    assert len(costs.entries) == 3
    assert perf.call_count() == 4
    assert perf.error_rate() == pytest.approx(0.25)
    assert perf.samples[1].outcome is CallOutcome.ERROR


def test_a_non_transient_error_is_not_retried() -> None:
    calls = {"n": 0}

    def process(record: InputRecord) -> ProcessOutput:
        calls["n"] += 1
        raise ValueError("permanent")

    result, _, _ = run(records(1), process, config=PipelineConfig(max_attempts=5, **FAST_RETRY))

    assert calls["n"] == 1
    assert result.results[0].attempts == 1
    assert result.failed[0].error is not None
    assert result.failed[0].error.startswith("ValueError")


# --------------------------------------------------------------------------
# Retries
# --------------------------------------------------------------------------


def test_a_transient_failure_succeeds_on_retry() -> None:
    seen: dict[str, int] = {}

    def process(record: InputRecord) -> ProcessOutput:
        seen[record.record_id] = seen.get(record.record_id, 0) + 1
        if record.record_id == "r2" and seen[record.record_id] == 1:
            raise TransientError("simulated 429")
        return ProcessOutput(text="ok", model=MODEL, input_tokens=10, output_tokens=2)

    result, costs, perf = run(records(3), process)

    assert result.failed == ()
    assert len(result.succeeded) == 3
    retried = next(r for r in result.results if r.record_id == "r2")
    assert retried.attempts == 2
    assert retried.status is RecordStatus.SUCCEEDED

    # Four attempts, one of which was an error: the retry is visible in the
    # latency data, but the record is still billed exactly once.
    assert result.total_attempts == 4
    assert perf.call_count() == 4
    assert perf.error_rate() == pytest.approx(0.25)
    assert len(costs.entries) == 3


def test_a_record_that_never_recovers_exhausts_its_retry_budget() -> None:
    def process(record: InputRecord) -> ProcessOutput:
        raise TransientError("always down")

    config = PipelineConfig(max_attempts=3, **FAST_RETRY)
    result, costs, perf = run(records(1), process, config=config)

    assert result.results[0].attempts == 3
    assert result.results[0].status is RecordStatus.FAILED
    assert result.errors[0][1].startswith("TransientError")
    assert costs.entries == []
    assert perf.call_count() == 3
    assert perf.error_rate() == 1.0


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


def test_throttling_is_counted_exactly_with_a_scripted_limiter() -> None:
    limiter = FakeLimiter(immediate=2)
    config = PipelineConfig(rate_limiter=limiter, **FAST_RETRY)

    result, _, _ = run(records(5), constant_process_fn(), config=config)

    # Two records passed straight through; the other three had to wait.
    assert limiter.try_calls == 5
    assert limiter.acquire_calls == 3
    assert result.throttled_count == 3
    assert len(result.succeeded) == 5


def test_no_limiter_means_no_throttling() -> None:
    result, _, _ = run(records(5), constant_process_fn())
    assert result.throttled_count == 0


def test_a_rate_limit_timeout_fails_the_record_without_calling_process_fn() -> None:
    calls = {"n": 0}

    def process(record: InputRecord) -> ProcessOutput:
        calls["n"] += 1
        return ProcessOutput(text="ok", model=MODEL)

    limiter = FakeLimiter(immediate=0, acquire_succeeds=False)
    config = PipelineConfig(rate_limiter=limiter, acquire_timeout_seconds=0.0, **FAST_RETRY)

    result, _, perf = run(records(2), process, config=config)

    assert calls["n"] == 0
    assert perf.call_count() == 0
    assert len(result.failed) == 2
    assert result.errors[0][1].startswith("RateLimitTimeoutError")
    assert result.results[0].attempts == 0


def test_the_real_token_bucket_actually_throttles() -> None:
    """The real RateLimiter, not a fake -- but with a fast refill.

    Only a lower bound is asserted: how many records get through before the
    bucket empties depends on the platform's monotonic-clock granularity, so
    an exact count would be flaky. At 1000 tokens/s the blocking waits are
    ~10ms each, keeping the test well under a tenth of a second.
    """
    limiter = RateLimiter(capacity=2, refill_rate_per_second=1000.0)
    config = PipelineConfig(rate_limiter=limiter, acquire_timeout_seconds=5.0, **FAST_RETRY)

    result, _, _ = run(records(4), constant_process_fn(), config=config)

    assert result.throttled_count >= 1
    assert len(result.succeeded) == 4


# --------------------------------------------------------------------------
# Offline stand-in
# --------------------------------------------------------------------------


def test_offline_process_fn_is_deterministic_and_labels_records() -> None:
    process = make_offline_process_fn()
    record = InputRecord("r01", "The battery died after two hours.")
    assert process(record) == process(record)
    assert process(record).text == "negative"


def test_offline_process_fn_simulates_flaky_and_failing_records() -> None:
    process = make_offline_process_fn(flaky_ids=("r04",), failing_ids=("r11",))
    flaky = InputRecord("r04", "third replacement")

    with pytest.raises(TransientError):
        process(flaky)
    assert process(flaky).text  # second attempt succeeds

    with pytest.raises(ValueError):
        process(InputRecord("r11", "colour is different"))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _write_inputs(tmp_path: Path, rows: Sequence[tuple[str, str]]) -> Path:
    """Write a small JSONL input file for CLI tests.

    Args:
        tmp_path: Pytest-provided temporary directory.
        rows: ``(id, text)`` pairs to write.

    Returns:
        Path to the written file.
    """
    path = tmp_path / "cli_inputs.jsonl"
    path.write_text(
        "".join(f'{{"id": "{rid}", "text": "{text}"}}\n' for rid, text in rows),
        encoding="utf-8",
    )
    return path


def test_cli_dry_run_succeeds_on_clean_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _write_inputs(tmp_path, [("r01", "arrived early"), ("r02", "works fine")])

    exit_code = main.main(["--dry-run", "--rate", "1000", "--inputs", str(inputs)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "BATCH PIPELINE SUMMARY" in out
    assert "Succeeded" in out
    assert "Latency p50/p95" in out


def test_cli_dry_run_exits_3_and_lists_the_failed_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # "r11" is in main.DEMO_FAILING_IDS, so the offline fake always fails it.
    inputs = _write_inputs(tmp_path, [("r02", "arrived early"), ("r11", "colour differs")])

    exit_code = main.main(["--dry-run", "--rate", "1000", "--inputs", str(inputs)])

    out = capsys.readouterr().out
    assert exit_code == 3
    assert "FAILED RECORDS (1)" in out
    assert "r11" in out


def test_cli_reports_a_missing_input_file(tmp_path: Path) -> None:
    assert main.main(["--dry-run", "--inputs", str(tmp_path / "nope.jsonl")]) == 1


def test_cli_reports_a_missing_api_key_without_running() -> None:
    with pytest.raises(main.MissingAPIKeyError):
        main.get_api_key({"GEMINI_API_KEY": "  "})
