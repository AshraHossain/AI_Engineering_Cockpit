# 17 — Event Automation Agent

A skeleton for event-triggered SOC automation, in both Python (`asyncio`) and
TypeScript (Node `async`/`await`). Alerts come in from a webhook or a queue,
trigger rules pick the workflows that should run (enrich, contain, open a
case), and the agent runs them **once per detection**, retrying transient
failures and dead-lettering permanent ones.

It is a skeleton on purpose. Nothing opens a socket: every place where a real
transport, store, or SOAR action plugs in is marked `TODO(integration)`. What
*is* implemented, and tested, is the delivery logic, which is the part that is
easy to get subtly wrong.

Standard library only. No runtime dependencies in either language.

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

`src/agent.py` and `src/agent.ts` are the same design, component for component.

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

## Run the tests

Both suites test the same ten guarantees.

```bash
uv run pytest
```

```bash
node --test tests/agent.test.ts
```

The TypeScript uses only erasable syntax (no enums, no constructor parameter
properties), so Node 22.18+ runs it directly with no build step.

## Before production

- Replace `InMemoryIdempotencyStore` with Redis (`SET NX PX`) or a UNIQUE-keyed
  table. The in-memory store gives no dedup across replicas.
- Return a lease token from `claim` and fence `complete` / `fail` / `release` on
  it, so a worker whose lease expired cannot settle a claim someone else took over.
- Give settled ledger entries a retention TTL.
