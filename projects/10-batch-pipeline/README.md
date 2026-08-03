# 10 - Batch Pipeline

Bulk-process many inputs *safely*: rate limiting, retries with backoff,
partial-failure tolerance, and cost accounting. Read 18 records from a JSONL
file, classify each one, and finish the run even when some records don't.

## What makes this project different

Projects 01-05 are standalone: they never import from `cockpit/`. This one
does, and that is the entire point. It exists to make the Tier 1 utils and
Tier 2 monitoring frameworks tangible:

| Framework piece | Used for |
| --- | --- |
| `cockpit.utils.rate_limiting.RateLimiter` | token-bucket throttling of every record |
| `cockpit.utils.error_handling.retry_with_backoff` | retrying `TransientError` only |
| `cockpit.utils.error_handling.ConfigurationError` | malformed input files |
| `cockpit.monitoring.cost_tracking.CostTracker` | pricing each record's real token usage |
| `cockpit.monitoring.performance_metrics.PerformanceTracker` | timing every *attempt*, p50/p95, error rate |

The repo root is `[tool.uv] package = false` by design, so it is never
installed into this project's venv. Two lines of wiring bridge that gap:
`pythonpath = ["src", "../.."]` in `pyproject.toml` for tests, and a small
`sys.path` bootstrap at the top of `src/main.py` for the CLI.

## The four survival properties

```
for each record:
   ┌─ RateLimiter ──────────  try_acquire(), else block until capacity
   │
   ├─ retry_with_backoff ───  attempt 1 ─▶ TransientError ─▶ wait ─▶ attempt 2 ─▶ ok
   │                          (ValueError is a bug, not a blip: never retried)
   │
   ├─ PerformanceTracker ───  every attempt timed, failures booked as errors
   │
   └─ CostTracker ──────────  successful records priced from real token counts

  record raised past its retry budget?  ──▶  RecordResult(status=FAILED)
                                             the loop continues, always
```

1. **Rate limiting.** Each record acquires a token before it is processed, so
   a 5,000-record file cannot machine-gun the provider into a 429 storm. A
   record that cannot get capacity within `acquire_timeout_seconds` is failed
   rather than blocking the run forever.
2. **Retries.** Only `TransientError` is retried. A `ValueError` from bad
   input is a bug and fails immediately -- retrying it just burns three times
   the money for the same wrong answer.
3. **Partial-failure tolerance.** `run_batch` never raises on a per-record
   failure. The batch always finishes and always reports *which* records
   didn't, via `BatchResult.errors`.
4. **Cost accounting.** Successful records are priced from their real token
   counts. Failed records cost nothing in the ledger, but their attempts
   still appear in the latency and error-rate data.

The unit of work is **injected** as `process_fn: (InputRecord) -> ProcessOutput`.
Nothing imports an SDK at module scope, so the whole test suite runs with no
API key and no network.

## Design choices / tradeoffs

- **Attempts are timed individually, records are billed once.** A record that
  succeeds on its second try contributes two latency samples (one an error)
  and exactly one cost entry. Timing the whole retry loop as a single sample
  would hide the retry and blend backoff sleep into "model latency".
- **The retry policy is coarse for the live client.** `build_gemini_process_fn`
  re-raises every SDK exception as `TransientError`, so everything is
  retried. Distinguishing a retryable 429 from a permanent 400 needs
  provider-specific error inspection -- deliberately out of scope here, and
  called out in the docstring rather than hidden.
- **The rate limiter is a `Protocol`, not a hard dependency.** `PipelineConfig`
  accepts anything with `try_acquire` / `acquire`, which is what makes the
  throttling tests deterministic (see below).
- **`RecordStatus` is a `StrEnum`**, so `result.status == "succeeded"` works
  in a report or a JSON dump without an explicit `.value`.
- **The elapsed-time clock is injected** into `run_batch`, separately from the
  `PerformanceTracker`'s clock, so throughput assertions are exact.

## Project layout

```
10-batch-pipeline/
├── pyproject.toml           # own deps + pythonpath wiring to the repo root
├── .python-version          # 3.11
├── data/inputs.jsonl        # 18 short customer-review records
├── src/
│   ├── pipeline.py          # run_batch + config + result types + process_fn builders
│   └── main.py              # CLI: load, run, summarize
├── tests/test_pipeline.py   # fully offline, deterministic, ~0.2s
└── .env.example
```

## How to run

```bash
cd projects/10-batch-pipeline
uv sync

# No API key needed -- canned offline classifier:
uv run python src/main.py --dry-run

# Live, against Gemini, throttled to 2 records/second:
cp .env.example .env   # then fill in GEMINI_API_KEY
uv run python src/main.py --rate 2 --limit 10
```

| Flag | Meaning |
| --- | --- |
| `--dry-run` | canned offline classifier; no API key, no network |
| `--limit N` | process only the first N records |
| `--rate X` | sustained records per second through the rate limiter |
| `--inputs P` | path to a different JSONL file |

Exit codes: `0` every record succeeded, `1` configuration error, `3` the run
completed but at least one record failed.

The offline classifier deliberately misbehaves on three records so
`--dry-run` demonstrates the interesting paths rather than a clean sweep:
`r04` and `r13` fail once and succeed on retry; `r11` always fails.

### Expected output (`--dry-run --rate 1000`)

```
==============================================================
BATCH PIPELINE SUMMARY
==============================================================
  Records processed             18
  Succeeded                     17
  Failed                         1
  Attempts (w/retry)            20
  Throttled                      0
  Total cost             $0.000178
  Elapsed                   0.402s
  Throughput               44.82/s

  FAILED RECORDS (1)
    r11      ValueError: record r11 is malformed
==============================================================
  Latency p50/p95: 0.000s / 0.000s across 20 attempt(s), error rate 15.0%
```

## Running the tests

No API key, no network, and no real sleeping -- the suite runs in ~0.2s.

```bash
cd projects/10-batch-pipeline
uv sync
uv run pytest
```

How determinism is achieved:

- the unit of work is an injected fake `process_fn`;
- the `PerformanceTracker`'s `clock` is a fake that advances a fixed step per
  read, so every attempt has an exact latency;
- `run_batch`'s elapsed-time clock is injected separately, so throughput is
  exact (a two-reading scripted clock gives exactly 1.0s);
- retry backoff is configured to **1ms**, so exhausting a three-attempt
  budget costs microseconds;
- throttle *counts* are asserted against a `FakeLimiter` with scripted
  answers, giving exact `throttled_count` assertions with zero blocking. One
  additional test drives the **real** `RateLimiter` and asserts only a lower
  bound, because a token bucket's exact refill depends on the platform's
  monotonic-clock granularity -- an exact count there would be flaky.

Coverage includes: the happy path (cost, tokens, latency, throughput), a
record that always fails (lands in `errors`, run still completes), a
non-retryable error not being retried, a transient failure succeeding on
retry, a record exhausting its retry budget, exact throttle counting, a
rate-limit timeout failing a record without ever calling `process_fn`, the
real token bucket throttling, input-file validation, and the CLI end to end.
