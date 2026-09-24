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

## Milestone 2: Structured Logging & Correlation IDs (High Priority)

**Why:** Production debugging requires tracing a request through all services. Correlation IDs link logs across components.

### Implementation
- [ ] Update logging format to structured JSON
  - Add correlation_id, component, level, timestamp, message, context
  - Example: `{"correlation_id":"evt-123","component":"p17.workflow","level":"error","message":"circuit open","workflow":"contain"}`

- [ ] Thread correlation_id through all calls
  - P17: `Event.id` is the correlation_id (already present)
  - P18: `Query.id` is the correlation_id (already present)
  
- [ ] Update all log calls to include relevant context
  - Replace: `log.info("workflow.log event=%s", event.id)`
  - With: structured logger that captures event.id + attempt + attempt_duration
  
- [ ] Make integrations correlation-aware
  - FileEventSource logs ack/nack with event.id
  - FileIdempotencyStore logs claim/complete with event.id
  - FileBasedRetriever logs retrieve with query.id
  - LLMWithClaude logs complete with query.id

---

## Milestone 3: Health Checks & Deployment Readiness (Medium Priority)

**Why:** Kubernetes and orchestration platforms need health checks. Graceful shutdown and zero-downtime deployment require careful sequencing.

### Implementation
- [ ] Add health check endpoints to both agents
  - `/healthz` (liveness) — agent process is running
  - `/readyz` (readiness) — agent is ready to process requests
  - Return JSON: `{"status":"healthy","components":{"retriever":"up","llm":"up","fallback":"up"}}`

- [ ] Graceful shutdown handlers
  - On SIGTERM: stop accepting new events, finish in-flight work, drain queue
  - Timeout: force shutdown after 30s
  - Log shutdown sequence

- [ ] Readiness depends on downstream availability
  - P17: at least one workflow must be available (circuit CLOSED or HALF_OPEN)
  - P18: retriever OR fallback must be available (not both down)

---

## Milestone 4: Performance Baselines & Load Testing (Medium Priority)

**Why:** You can't optimize what you don't measure. Baselines catch regressions.

### Implementation
- [ ] Create load test script for each project
  - P17: generate N events/second, measure latency, error rate, dead-letter rate
  - P18: generate N queries/second, measure end-to-end latency, fallback rate, abstention rate

- [ ] Record baseline metrics
  - P17: 1000 events/sec, p99 latency < 500ms, < 1% dead-letter rate
  - P18: 100 queries/sec, p99 latency < 5s, < 10% fallback rate, 0% abstention

- [ ] Automated performance regression detection
  - CI runs load test on every PR
  - Fail if latency p99 > baseline * 1.2 or error rate > baseline * 2

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
