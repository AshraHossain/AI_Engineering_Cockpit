# Production Hardening — Phase 2 Implementation Plan

**Goal:** Make projects 16, 17, 18 production-ready through error resilience, observability, and deployment safety.

**Timeline:** 3 focused milestones (not all at once)

---

## Milestone 1: Circuit Breakers & Graceful Degradation (High Priority) ✅ DONE

**Why:** Production systems fail. Circuit breakers prevent cascading failures; graceful degradation keeps the system partially functional.

**Implemented:** `src/circuit_breaker.py` in both projects — a stdlib-only CLOSED/OPEN/HALF_OPEN state
machine (rolling window of last N outcomes, configurable failure threshold, timed reset to HALF_OPEN,
one trial call before fully closing). 9 tests each (`tests/test_circuit_breaker.py`), same file duplicated
by design — these projects are deliberately dependency-free and don't share a package.

### P17 (Event Automation) ✅
- [x] `CircuitBreakerWorkflow` wraps any `Workflow` (see `src/integrations.py`)
  - Opens when failure rate ≥ threshold (default 50%) over a rolling window (default 10 calls, min 5 to trip)
  - `PermanentError` (bad event, not bad dependency) never counts against the circuit
  - Half-open after `reset_after_s` (default 30s); one trial call decides CLOSED vs. back to OPEN
- [x] Graceful degradation: an open circuit raises `PermanentError`, which `WorkflowExecutor`
  routes straight to the dead-letter queue — no wasted retries against a known-dead dependency
- [x] Metrics hook: `metrics.increment("circuit.open"/"circuit.half_open"/"circuit.closed", circuit=name)`
- [ ] Health check endpoint (deferred to Milestone 3 — needs an HTTP surface, not just the breaker)

### P18 (RAG Citation) ✅
- [x] `CircuitBreakerRetriever` — open circuit returns `[]` (empty context), which `RAGAgent`
  already treats as low-confidence and routes to fallback search (no new code path needed)
- [x] `CircuitBreakerLLM` — open circuit returns `ABSTAIN_TEXT` directly (safe default; never
  guess when the model backing the agent is known-unhealthy)
- [x] `CircuitBreakerSearchFallback` — open circuit returns `[]` (empty results); primary answer,
  if any, still returns rather than cascading the failure
- [ ] Health check endpoint (deferred to Milestone 3)
  - Test retriever, LLM, fallback availability
  - Return component-level status (UP, DEGRADED, DOWN)
  - Return 503 if retriever + fallback both unavailable

### Implementation Details
- [ ] Create `src/circuit_breaker.py` (reusable across projects)
  - `CircuitBreaker` class with CLOSED/OPEN/HALF_OPEN states
  - Configurable failure threshold, timeout, and reset period
  - Emit metrics on state transitions
  
- [ ] Update integrations to use circuit breakers
  - Wrap `FileEventSource.ack()` calls
  - Wrap `LLMWithClaude.complete()` calls
  - Wrap `SearchFallbackStub.search()` calls

---

## Milestone 2: Structured Logging & Correlation IDs (High Priority) ✅ DONE

**Why:** Production debugging requires tracing a request through all services. Correlation IDs link logs across components.

**Implemented:** `src/structured_logging.py` in both projects (stdlib-only, duplicated by design, same as
`circuit_breaker.py`) — a `JsonFormatter` that renders every `LogRecord` as one JSON line (timestamp,
level, component, message, plus any `extra={...}` fields verbatim), and `bind(logger, **context)`, which
returns a `LoggerAdapter` that attaches context (typically `correlation_id=event.id` / `query.id`) to
every call made through it, merging with whatever `extra` the call site adds rather than clobbering it
(the stdlib `LoggerAdapter` default *replaces* `extra`, which would have silently dropped per-call fields).
6 tests each (`tests/test_structured_logging.py`).

- [x] JSON logging format: `{"timestamp":"...","level":"ERROR","component":"integrations","message":"event dead-lettered","correlation_id":"evt-1","workflow":"contain","attempts":3,"error":"..."}`
- [x] Correlation ID threaded through every log call that has one available:
  - P17: `FileEventSource` (ack/nack), `FileIdempotencyStore` (claim/complete/fail/release),
    `FileDeadLetterQueue` (send), every example `Workflow` — all keyed on `Event.id`
  - P18: `FileBasedRetriever` (retrieve), `SearchFallbackStub` (search), the retriever/search
    circuit-breaker wrappers — all keyed on `Query.id`
  - **Known ceiling:** `LLM.complete(prompt: str)` takes a bare string, not a `Query`, so
    `LLMWithClaude` / `CircuitBreakerLLM` have no correlation_id to attach without widening that
    core interface — left as-is (see `ponytail:` comment in `CircuitBreakerLLM.complete`)
- [x] `configure_json_logging()` wired into both `example.py` scripts (set `EXAMPLE_LOG_FORMAT=text`
  for the old human-readable format while developing)

**Bugs found and fixed while actually running the examples end-to-end** (not just unit tests):
  - `AutomationAgent(source, store, triggers, executor)` in P17's example used the wrong argument
    order and omitted the required `metrics` argument entirely — `AutomationAgent.__init__` is
    `(source, evaluator, executor, store, metrics)`
  - P17's example read `dlq.letters`, an attribute that only exists on `InMemoryDeadLetterQueue`;
    `FileDeadLetterQueue` (the stub actually used) has no such attribute
  - P18's example called `ConfidenceScorer(threshold=0.6)` — the threshold lives on `RAGAgent`,
    `ConfidenceScorer` takes no constructor arguments
  - `FileBasedRetriever.retrieve()` tested whether the *entire query sentence* was a substring of a
    title/snippet ("what is python used for?" in "python (programming language)") — this is
    essentially never true for natural-language questions, so retrieval silently returned zero
    results for every query. Replaced with word-overlap scoring, which actually retrieves.
  - `LLMWithClaude.complete()` returned `MockLLM().complete(prompt)` (a coroutine) instead of
    `await MockLLM().complete(prompt)` in both its fallback paths — same "coroutine created but
    never awaited, silently returns nothing" shape as the `SearchFallbackStub` bug fixed in
    Milestone 1

---

## Milestone 3: Health Checks & Deployment Readiness (Medium Priority) ✅ DONE

**Why:** Kubernetes and orchestration platforms need health checks. Graceful shutdown and zero-downtime deployment require careful sequencing.

**Implemented:** `src/health.py` in both projects (stdlib `http.server`, no dependency) — `HealthChecker`
aggregates named component checks into a readiness verdict (`require_any(...)` for groups where one
healthy member is enough), and `serve_health(checker, port)` exposes it over real HTTP on a background
daemon thread. 10 tests each (`tests/test_health.py`), including tests that start the actual server on
an OS-assigned port and hit it with `urllib.request` — not just the aggregation logic in isolation.

- [x] `/healthz` (liveness) — 200 `{"status": "alive"}` whenever the process is up, independent of readiness
- [x] `/readyz` (readiness) — 200 `{"status": "ready", "components": {...}}` or 503 `{"status": "not_ready", ...}`
- [x] Readiness reflects real circuit-breaker state (`CircuitBreakerWorkflow.allow()` / `CircuitBreakerRetriever.allow()`
  / `CircuitBreakerLLM.allow()` / `CircuitBreakerSearchFallback.allow()` — each wrapper now exposes this
  publicly for exactly this purpose; both example scripts wire it up and print the live URLs on startup)
  - P17: each workflow individually required (no `require_any` — a dead-lettering workflow still means degraded capability)
  - P18: `require_any("retriever", "search_fallback")` — matches `RAGAgent`'s own tolerance for either one being down; `llm` is individually required, since without it there's no answer to ground
- [x] Graceful shutdown (P17 only — P18's `RAGAgent.answer()` is request/response with no persistent loop to
  drain): `src/shutdown.py` turns SIGTERM/SIGINT into `task.cancel()`. `AutomationAgent.run()` already
  drained correctly on cancellation before this milestone (its `finally` awaits in-flight work, `_handle()`
  releases the lease on `CancelledError`) — the gap was only that a real signal never reached that path. 1
  test (`tests/test_shutdown.py`) pins the callback logic; OS-level signal delivery isn't exercised in
  tests since Windows (in the CI matrix) doesn't deliver SIGTERM to a Python handler the same way Unix
  does, and a platform-fragile test would be testing the OS, not this code.

**Bug found while wiring this in:** neither example script had ever actually used the circuit-breaker
wrappers built in Milestone 1 — `example.py` constructed the raw `Retriever`/`LLM`/`SearchFallback`/`Workflow`
instances directly. Fixed by wrapping them, which is also what makes the health checks meaningful (a
breaker with nothing routed through it can't reflect real dependency health).

---

## Milestone 4: Performance Baselines & Load Testing (Medium Priority) ✅ DONE

**Why:** You can't optimize what you don't measure. Baselines catch regressions.

**Implemented:** `load_test.py` at the root of both projects (stdlib `statistics.quantiles` for
percentiles, no dependency). Each measures the pipeline's *own* overhead against in-memory / offline
stand-ins, deliberately not the network-touching integration stubs — a load test that spends its time
waiting on a real API measures that API, not this codebase. 4-5 smoke tests each (`tests/test_load_test.py`)
assert the harness produces sane, non-degenerate output; these are not performance assertions (CI hardware
varies too much for a fixed threshold to mean anything).

- [x] P17: `SyntheticEventSource` + `NoopWorkflow` + in-memory stores (`InMemoryIdempotencyStore`,
  `InMemoryDeadLetterQueue`) isolate trigger-evaluation + claim/settle + retry-policy overhead from
  disk I/O. Reports throughput, dead-letter rate, and `workflow.duration_ms` percentiles (already-emitted
  metrics, not new instrumentation).
- [x] P18: `MockLLM` (deterministic, no API calls) + `FileBasedRetriever` (in-memory corpus) +
  `OfflineFallback` (fixed result, no network) isolate retrieve → generate → ground → score overhead.
  Reports throughput, fallback rate, abstain rate, and end-to-end latency percentiles.
- [ ] CI regression gate — not implemented. A fixed pass/fail threshold needs a stable reference
  machine to mean anything; this repo's CI matrix runs on three OSes with variable-performance shared
  runners, where a hard latency threshold would be flaky by infrastructure, not by regression. Worth
  revisiting if/when this gets a dedicated benchmark runner.

**Measured baselines (this machine, informational only — no assertion in CI):**

| | Throughput | p50 | p95 | p99 |
|---|---|---|---|---|
| P17 (2000 synthetic events, concurrency 32) | ~34k events/sec | <0.01ms | <0.01ms | <0.01ms |
| P18 (300 synthetic queries, concurrency 16) | ~4.5k queries/sec | 0.19ms | 0.26ms | 0.37ms |

**Bug found while building this:** the first version of P18's load test used `SearchFallbackStub`
(the real integration stub), which attempts a live HTTP request to a public SearXNG instance before
falling back to a mock result. With 40% of the sample queries hitting fallback, that turned a
sub-millisecond pipeline benchmark into one dominated by real network round-trips (p95/p99 jumped to
580-620ms) — the opposite of what a load test for *this codebase* should measure, and a real flakiness
risk if a smoke test ever exercised it in a network-restricted CI runner. Replaced with `OfflineFallback`,
a fixed-result stand-in local to `load_test.py`; `test_run_load_test_is_fast_with_no_network_calls` pins
this so a future edit can't silently reintroduce it.

---

## Milestone 5: Security Hardening (Low Priority, but Important)

**Why:** Production systems are targets. Validate inputs, rate-limit, rotate secrets.

### Implementation
- [ ] Input validation
  - P17: validate Event.payload schema (no unbounded strings, arrays)
  - P18: validate Query.text length (max 1000 chars) and format
  
- [ ] Rate limiting
  - Per-source (EDR, SIEM, CloudTrail) rate limits on P17
  - Per-user/API-key rate limits on P18
  
- [ ] Secret rotation
  - Store API keys in environment, not config
  - Rotate on CI (regenerate test keys weekly)
  - Log key rotation events, not the keys themselves

---

## Quick Reference: What Changes Where

| Component | Circuit Breaker | Logging | Health Check | Graceful Shutdown |
|-----------|-----------------|---------|--------------|-------------------|
| P17: Workflow | ✅ | ✅ | — | ✅ |
| P17: EventSource | — | ✅ | ✅ | ✅ |
| P18: Retriever | ✅ | ✅ | ✅ | — |
| P18: LLM | ✅ | ✅ | ✅ | — |
| P18: SearchFallback | ✅ | ✅ | ✅ | — |

---

## Success Criteria

**Phase 2 is done when:**
1. ✅ All circuit breakers are in place and emit metrics
2. ✅ All logs are structured JSON with correlation_id
3. ✅ Health check endpoints work and match Kubernetes expectations
4. ✅ Load test scripts run and establish baselines
5. ✅ Graceful shutdown is tested (can deploy without dropping requests)

**Testing:**
- Integration tests for circuit breaker state transitions
- Health check tests for each component combination (up/down)
- Load test with simulated failures (LLM timeout, retriever error, etc.)
- Chaos test: kill each dependency one at a time, verify graceful degradation

---

## Implementation Order (Recommended)

1. **First:** Circuit breaker implementation (reusable)
2. **Second:** Structured logging with correlation IDs
3. **Third:** Health check endpoints and graceful shutdown
4. **Fourth:** Load test baselines
5. **Fifth:** Security hardening (only if time permits)

Estimated effort: 3-5 hours for milestones 1-3; 2-3 hours for 4-5.
