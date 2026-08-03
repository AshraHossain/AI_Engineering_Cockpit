# 08 - Cost Dashboard

Run a batch of prompts across one or more models, record what each call
**cost** and how long it **took**, and render the cockpit's monitoring
dashboard from the result.

## What makes this project different

Projects 01-05 are standalone: they never import from `cockpit/`. This one
does, and that is the entire point. It exists to make the Tier 2 monitoring
framework tangible:

| Framework piece | Used for |
| --- | --- |
| `cockpit.monitoring.cost_tracking.CostTracker` | pricing real token counts into dollars |
| `cockpit.monitoring.performance_metrics.PerformanceTracker` | timing every call, p50/p95/p99, error rate |
| `cockpit.monitoring.dashboard` | composing both trackers into one rendered report |
| `cockpit.evaluation.cost_evaluation.check_budget` | the optional `--budget` cap |

The repo root is `[tool.uv] package = false` by design, so it is never
installed into this project's venv. Two lines of wiring bridge that gap:
`pythonpath = ["src", "../.."]` in `pyproject.toml` for tests, and a small
`sys.path` bootstrap at the top of `src/main.py` for the CLI.

## The interesting piece: instrument the call once

The usual failure mode is to bolt cost and latency on in two unrelated
places -- a timer here, a hand-rolled token-price multiplication there -- and
then find the two disagree about how many calls actually happened.

`src/instrumented_client.py` wraps the model call exactly once:

```
                    ┌─ PerformanceTracker.measure(...) ──────────────┐
prompt ──▶ generate_fn(model, prompt) ──▶ response                   │
                    │                       │                        │
                    │                       ├─ .text                 │
                    │                       └─ .usage_metadata ──────┤
                    └────────────────────────────────────────────────┘
                                            │
                        CostTracker.record_usage(project, model, in, out)
                                            │
                                            ▼
                              build_dashboard_snapshot(...)
                              render_dashboard_text(...)
```

The model call is **injected** as `generate_fn: (model, prompt) -> response`.
Nothing imports an SDK at module scope, so the whole test suite runs with no
API key and no network. A failing call is still booked as an error latency
sample -- failures show up in the dashboard's error rate instead of silently
vanishing from the distribution.

## Design choices / tradeoffs

- **Real token counts, not estimates.** Cost comes from
  `response.usage_metadata.prompt_token_count` / `candidates_token_count`,
  not from a character-count guess. `extract_usage` is deliberately
  defensive: a response with no usage metadata yields zeros rather than
  raising, so an unusual response shape degrades the dashboard instead of
  killing the run.
- **Trackers are constructed here, not pulled from module globals.** The
  monitoring framework exposes process-wide default trackers; this project
  ignores them and owns its own. Globals would make the tests order-dependent
  and would prevent showing two runs side by side.
- **Latency is read off the measurement handle**, not by re-reading the
  tracker's sample list, so nothing races if calls are ever fanned out.
- **The price table is a dated default.** `PRICING_VERIFIED_DATE` is printed
  after every run as a reminder. `InstrumentedClient` takes a `pricing=`
  override for negotiated or newer rates.
- **`--dry-run` uses a fake clock as well as a fake model.** The
  `PerformanceTracker`'s `clock` field is injectable, so the offline demo
  records an exact 0.250s per call and produces reproducible output rather
  than reflecting how fast the machine happened to be.

## Project layout

```
08-cost-dashboard/
├── pyproject.toml              # own deps + pythonpath wiring to the repo root
├── .python-version             # 3.11
├── src/
│   ├── instrumented_client.py  # the interesting piece: one call -> cost + latency
│   ├── workload.py             # the prompt batch, the offline fake, the fake clock
│   └── main.py                 # CLI: run workload, render dashboard, check budget
├── tests/test_dashboard.py     # fully offline; fake model + fake clock
└── .env.example
```

## How to run

```bash
cd projects/08-cost-dashboard
uv sync

# No API key needed -- canned offline model, deterministic output:
uv run python src/main.py --dry-run

# Live, against Gemini:
cp .env.example .env   # then fill in GEMINI_API_KEY
uv run python src/main.py --models gemini-2.5-flash --budget 0.01
```

| Flag | Meaning |
| --- | --- |
| `--dry-run` | canned offline model + fake clock; no API key, no network |
| `--models` | comma-separated model ids (default: flash and flash-lite) |
| `--limit N` | run only the first N prompts of the workload |
| `--budget X` | exit with code 2 if total spend exceeds X US dollars |

Exit codes: `0` success, `1` configuration error, `2` over budget.

### Expected output (`--dry-run --limit 2 --budget 0.01`)

```
======================================================================
AI Engineering Cockpit - Monitoring Dashboard
Generated: 2026-08-03T20:49:09+00:00
======================================================================

COST
  Total spend                  $0.000145
  Input tokens                       114
  Output tokens                       84
  Total tokens                       198

  By model
    gemini-2.5-flash           $0.000122
    gemini-2.5-flash-lite      $0.000023

  By project
    08-cost-dashboard          $0.000145

PERFORMANCE
  Total calls                          4
  Error rate                       0.00%

  Operation                   Calls      p50      p95      p99  Errors
  --------------------------------------------------------------------
  gemini-2.5-flash-lite....       2   0.250s   0.250s   0.250s       0
  gemini-2.5-flash.generate       2   0.250s   0.250s   0.250s       0

RECENT COSTS (last 4)
  ...
======================================================================

Pricing table verified: 2026-08-03 (override it before trusting it).
BUDGET: WITHIN BUDGET -- spent $0.000145 of $0.010000 (1.4% used, $0.000000 over)
```

## Running the tests

No API key and no network access is required, and nothing sleeps.

```bash
cd projects/08-cost-dashboard
uv sync
uv run pytest
```

`tests/test_dashboard.py` covers:

- token extraction, including missing / partial `usage_metadata`
- cost math cross-checked against `estimate_cost`, plus a `pricing=` override
- **exact latency assertions**, via a fake clock that advances a fixed step
  per read (`PerformanceTracker.measure` reads it exactly twice per call, so
  a 0.25s step means every call is recorded as exactly 0.25s)
- a scripted clock pinning a whole p50/min/max distribution
- a failing call: error latency sample recorded, no cost entry recorded
- workload totals across two models, and tolerance of one failing prompt
- the rendered report containing every section, the 100% error-rate case,
  and the empty "nothing recorded yet" state
- the `--dry-run` CLI end to end, including both budget verdicts
