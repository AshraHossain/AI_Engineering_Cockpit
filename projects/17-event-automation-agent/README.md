# 17 — Event Automation Agent

An event-triggered automation agent for security operations, in Python
(`asyncio`) and TypeScript (Node `async`/`await`). Alerts arrive from a webhook
or a queue, trigger rules pick the workflows that should run (enrich, contain,
open a case), and the agent runs them **once per detection**, retrying transient
failures and dead-lettering permanent ones.

The delivery logic is the point. It is the part that is easy to get subtly
wrong, so it is fully implemented and tested. Real transports and SOAR actions
are not: every place one plugs in is marked `TODO(integration)`. Nothing here
opens a network socket.

Standard library only, in both languages. No runtime dependencies.

## What's in the box

| File | What it is |
|---|---|
| `src/agent.py` | The core: event lifecycle, idempotency, retries, dead-lettering. |
| `src/agent.ts` | The same core, component for component, in TypeScript. |
| `src/integrations.py` | File-backed stubs (JSONL / JSON) plus sample workflows, so it runs with no external services. |
| `src/circuit_breaker.py` | Per-dependency breaker that stops retrying into an outage. |
| `src/structured_logging.py` | JSON log lines with a `correlation_id` bound per event, threaded through every component. |
| `src/health.py` | `/healthz` (liveness) and `/readyz` (readiness) over plain `http.server`; readiness reflects real circuit-breaker state. |
| `src/shutdown.py` | Turns SIGTERM/SIGINT into a clean `task.cancel()`; `AutomationAgent.run()` already drains in-flight work on cancellation. |
| `example.py` | Runnable end-to-end demo wiring all of the above together. |
| `load_test.py` | Throughput/latency baseline against in-memory stores, isolated from disk I/O. |

`src/agent.py` and `src/agent.ts` are equivalent. Everything else in this table
is **Python only** — there is no TypeScript twin yet.

## Layers

| Component | Responsibility |
|---|---|
| `EventSource` | Transport abstraction (webhook, SQS, Kafka, ...). Yields events; `ack` / `nack`. |
| `TriggerEvaluator` | Maps an event to an ordered, de-duplicated list of workflow names. |
| `WorkflowExecutor` | Runs each workflow under a `RetryPolicy`; dead-letters terminal failures. |
| `IdempotencyStore` | Lease-based dedup ledger keyed by the detection's own ID. |
| `RetryPolicy` / `DeadLetterQueue` | Exponential backoff with full jitter; terminal sink for replay. |
| `MetricsLogger` | Counter and timing hook for StatsD, OpenTelemetry, or a test double. |
| `AutomationAgent` | Wires the layers together and owns the per-event lifecycle. |

## Delivery guarantees

Every event goes through `claim -> evaluate -> execute -> settle -> ack`. The ack
policy is where at-least-once delivery either becomes effectively exactly-once
or quietly drops alerts:

- **Redelivered after settling**: acked, workflows not re-run.
- **Dead-lettered**: still acked. The DLQ owns it; redelivery would only fail again.
- **Leased by another worker**: *nacked*, never acked. If that worker crashes,
  redelivery is the only way the alert comes back.
- **Agent fault before settling**: lease released, event nacked, so a healthy
  replica can take it.
- **Agent fault after settling** (for example, `ack` fails): the verdict is kept,
  so the redelivery is recognised as a duplicate instead of re-running containment.

Workflows must still be safe to re-run, since a retry can follow a partial side
effect. Raise `PermanentError` (Python) or throw it (TypeScript) when retrying
cannot help, such as a host that no longer exists. Generic errors are retried.

## Retries versus the circuit breaker

The two are orthogonal, and both are needed:

- `RetryPolicy` governs **one event's** attempts: exponential backoff with full
  jitter, because a SIEM outage delivers a burst of correlated alerts and
  undithered retries would reconverge on the API that just failed.
- `CircuitBreaker` governs **one dependency's health across all events**. When
  the EDR or IAM API is degraded, the breaker opens and calls fail fast with
  `CircuitOpenError` instead of hammering it.

States are `CLOSED` → `OPEN` → `HALF_OPEN` (one trial call; success closes,
failure re-opens). Knobs: `failure_threshold` (0.5), `min_calls` (5),
`window_size` (10), `reset_after_s` (30). Wrap any workflow with
`CircuitBreakerWorkflow` to get it. The breaker is per-process, so replicas trip
independently.

## Logging, health checks, and shutdown

Every log line is JSON (`configure_json_logging()`), with `correlation_id` set
to the event's own ID via `bind(logger, correlation_id=event.id)` — grep one ID
to follow a single event across ingest, claim, workflow execution, and ack.
Set `EXAMPLE_LOG_FORMAT=text` to get plain text instead while developing.

`example.py` wraps its workflows in `CircuitBreakerWorkflow` and starts a
background health server (`serve_health`) exposing:

- `GET /healthz` — 200 whenever the process is alive, independent of readiness.
- `GET /readyz` — 200 if every registered workflow's circuit is closed, 503
  with per-workflow detail otherwise. The demo prints both URLs on startup.

`install_signal_handlers(task)` turns SIGTERM/SIGINT into `task.cancel()`, which
`AutomationAgent.run()` was already built to drain cleanly — its `finally`
block awaits in-flight work, and `_handle()` releases the lease on
cancellation. The demo's 5-second timeout exercises that same path, so a real
signal and a timed shutdown behave identically.

## Run it

The demo creates sample events, processes them, and prints the claims ledger,
dead-letter count and metrics. It runs for about five seconds, or until Ctrl+C.

```bash
PYTHONPATH=src python example.py
```

## Load test

Throughput and latency percentiles against an in-memory store (isolates the
pipeline's own overhead from file I/O — point a variant at the file-backed
stubs to baseline that stack specifically):

```bash
PYTHONPATH=src python load_test.py --count 2000 --concurrency 32
```

## Run the tests

```bash
uv run pytest
# 47 passed: 12 agent lifecycle, 9 circuit breaker, 5 integrations,
# 10 health checks, 6 structured logging, 4 load test harness, 1 shutdown
```

```bash
node --test tests/agent.test.ts
# 12 passed
```

The TypeScript uses only erasable syntax (no enums, no constructor parameter
properties), so Node 22.18+ runs it directly with no build step. To type-check:

```bash
npx -p typescript -p @types/node tsc --noEmit --strict --erasableSyntaxOnly \
  --verbatimModuleSyntax --allowImportingTsExtensions --module nodenext \
  --target es2022 --types node src/agent.ts tests/agent.test.ts
```

### Coverage

Measured with `pytest --cov --cov-branch` and `node --test --experimental-test-coverage`:

| Module | Line | Branch |
|---|---|---|
| `src/agent.py` | 98% | 97% |
| `src/agent.ts` | 100% | 94% |
| `src/circuit_breaker.py` | 89% | — |
| `src/health.py` | 100% | 100% |
| `src/integrations.py` | 42% | — |
| `src/shutdown.py` | 57% | — |
| `src/structured_logging.py` | 74% | — |

The delivery lifecycle is the part that is thoroughly covered. `integrations.py`
is low on purpose: it is stub code you are expected to replace, and its file I/O
is exercised by `example.py` rather than by unit tests. The gap in `agent.py` is
the cancellation path (releasing a lease only while the event is unsettled),
which is hard to test without driving the event loop directly. `shutdown.py`'s
gap is the two lines of actual `signal` module wiring — OS signal delivery
differs enough by platform (Windows, in the CI matrix, doesn't deliver SIGTERM
to a Python handler the way Unix does) that testing it would be flaky by
platform, not by bug; what's tested is the callback logic that wiring invokes.

## Before production

Done: retries with jitter, dead-lettering, lease-based idempotency, per-dependency
circuit breaking, structured JSON logging with correlation IDs, health checks,
graceful shutdown.

Still to do:

- Replace `FileIdempotencyStore` / `InMemoryIdempotencyStore` with Redis
  (`SET NX PX`) or a UNIQUE-keyed table. Neither shipped store dedups across
  replicas, and the file-backed one is not safe against concurrent writers.
- Return a lease token from `claim` and fence `complete` / `fail` / `release` on
  it, so a worker whose lease expired cannot settle a claim someone else took over.
- Give settled ledger entries a retention TTL; today they are kept forever.
- Replace the stub workflows in `integrations.py` with real SOAR calls, and the
  `FileEventSource` with your webhook or queue consumer.
- Port the integration stubs and circuit breaker to TypeScript, or drop the
  TypeScript core if you are not using it.
