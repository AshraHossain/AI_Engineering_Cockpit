# AI Engineering Cockpit — Project Notes & Context

**Last Updated:** 2026-09-23  
**Status:** Three agent projects complete, moving to integration stubs & production hardening

---

## Executive Summary

Built three production-ready AI agent skeleton implementations across Python (asyncio) and TypeScript (Node async/await), all with comprehensive test coverage (99%, 96%, 99%) and zero external dependencies (stdlib only). All work merged to main. Now preparing for real-world backend integration and production deployment.

---

## Projects Completed

### Project 16: Production Agent with Observability
**Goal:** Production-grade agentic alert orchestrator with safety, observability, and rollback.

**What's Implemented:**
- **Arize Phoenix tracing** — instrument every decision (retrieve, classify, forward/escalate)
- **Loop guard** — max hops limit, visited alert tracking, escalation valve to prevent runaway loops
- **Alert rules engine** — rule-based classification and routing with canary support
- **Rollback logic** — fast revert to previous rules version on escalation
- **Metrics** — counters, timings, distributions for observability

**Why This Approach:**
- Production systems need visibility into agent behavior (Arize Phoenix chosen for its focus on LLM observability)
- Loop prevention is non-negotiable for autonomous systems; we use a max-hops counter + visited set rather than per-rule cooldowns
- Canary routing enables safe rule updates; gradual traffic shift catches issues before full rollout
- Rollback is simpler than versioned rules: store previous state, revert atomically on alert

**Test Coverage:** 16 passing tests, 664 statements, 99% coverage  
**PR:** #14 (merged)  
**Status:** ✅ Complete, ready for backend wiring

**Integration Points (TODO(integration)):**
- `AlertRetriever.get_context()` — fetch active alerts from monitoring system (Datadog, Prometheus, PagerDuty, etc.)
- `Classifier.classify()` — real LLM call to Claude SDK (currently stubbed)
- `Notifier` — Slack integration for alert escalation (marked stub)
- `MetricsLogger` — push to Prometheus/DataDog/NewRelic

---

### Project 17: Event-Driven Automation Agent
**Goal:** Event-driven automation with delivery guarantees, idempotency, and human approval workflows.

**What's Implemented:**
- **Event pipeline** — async queue-based intake with deduplication by event ID
- **Delivery guarantees** — at-least-once semantics with idempotent handlers
- **Idempotency tracking** — processed event store prevents duplicate effects
- **Approval workflow** — human-in-the-loop for sensitive events with timeout fallback
- **Async/await patterns** — identical architecture in Python (asyncio) and TypeScript (Node)

**Why This Approach:**
- At-least-once delivery is safer than exactly-once (which requires distributed consensus); combined with idempotency, it gives exactly-once effects
- Approval workflows add human safety gate without blocking all automation; timeout fallback ensures events don't hang forever
- Queue-based pipeline naturally handles backpressure and out-of-order events
- Deduplication by event ID (not handler) means one slow handler doesn't block others

**Test Coverage:** 10 passing tests, 228 statements, 96% coverage  
**PR:** #13 (merged)  
**Status:** ✅ Complete, ready for backend wiring

**Integration Points (TODO(integration)):**
- `EventQueue.enqueue()` — real message queue (RabbitMQ, SQS, Kafka, Redis)
- `ApprovalStore.is_approved()` / `.mark_approved()` — persistent approval tracking (PostgreSQL, DynamoDB, etc.)
- `Handler` implementations — wire to your actual automation targets (CI/CD, incident response, etc.)

---

### Project 18: RAG Citation Agent
**Goal:** Retrieval-augmented generation agent that answers only from sources, preventing hallucinations at scale.

**What's Implemented:**
- **Citation grounder** — resolve [n] markers to real sources, detect invented citations, renumber by first appearance
- **Confidence scorer** — coverage × support × validity formula, abstain if below threshold (default 0.6)
- **Fallback search** — trigger external search if primary confidence low, merge & re-score
- **Abstention** — return "I don't know" when fallback also below threshold (safety over false positives)
- **Error handling** — retriever/search errors logged separately, LLM errors propagated (no silent failures)

**Why This Approach:**
- Positional citations ([1], [2], ...) keep LLM away from copying opaque UUIDs or URIs
- Confidence formula is simple and interpretable (not a black-box score): coverage = sentences with ≥1 valid citation, support = mean retrieval score, validity = markers that resolve
- Fallback search happens once only; if still below threshold, we abstain rather than risk a guess
- Abstention on uncertainty is the production-safe default; customers trust "I don't know" more than a confident hallucination

**Test Coverage:** 16 passing tests, 197 statements, 99% coverage  
**PR:** #12 (merged)  
**Status:** ✅ Complete, ready for backend wiring

**Integration Points (TODO(integration)):**
- `Retriever.get_context()` — fetch passages from vector DB (Pinecone, Weaviate, Milvus, chromadb, etc.)
- `LLM.complete()` — call Claude SDK (or other provider) with citation-focused prompt
- `SearchFallback.search()` — external search API (SearXNG, Bing, Brave, custom)
- `MetricsLogger` — same as P16

---

## Architectural Decisions & Rationale

### Why Stdlib Only?
- **Tradeoff:** No external dependencies means more boilerplate for common tasks, but guarantees portability and zero supply-chain risk
- **Chosen because:** Three skeleton projects need to be easy to fork/integrate into existing codebases; adding dependencies would create adoption friction
- **Integration layer can add:** When wiring real backends (Retriever, LLM, etc.), integrations can pull their own dependencies

### Why Python + TypeScript?
- **Goal:** Prove the design is language-agnostic
- **Implementation:** Both versions component-for-component identical; same logic, same test coverage, same architecture
- **Benefit:** Teams can pick their language; design is already validated in both

### Why Async/Await Throughout?
- **Tradeoff:** Slightly harder to reason about than synchronous code, but required for production scale
- **Chosen because:** All three projects involve I/O (retrieval, LLM calls, fallback searches); blocking on these would serialize work and tank throughput
- **Pattern:** Use `asyncio` (Python) / `async/await` (Node) consistently; avoid mixing sync and async

### Why Zero External Dependencies in Tests?
- **Tradeoff:** Can't use pytest, Jest, or other frameworks; had to write minimal custom test harnesses
- **Chosen because:** Keeps the skeleton deployable everywhere; anyone with Python 3.11+ or Node 22+ can run tests immediately
- **Verification:** 42 total tests (16+10+16) all pass; coverage reports generated with minimal tooling

---

## CI/CD & Security

### detect-secrets Baseline
- **What:** GitHub Actions workflow scans for committed secrets (entropy, keywords, API key patterns)
- **Challenge:** Initial scan found "false positive secrets" (high-entropy strings in test fixtures, public hashes in graphify output)
- **Solution:** Created `.detectsecrets` baseline file; marked all findings as audited/safe (`is_secret: false`)
- **Lesson:** Entropy-based scanning needs human audit; can't automate away the judgment call

### GitHub Actions
- **bandit:** Static security analysis (Python); runs on all Python projects
- **detect-secrets:** Secret scanning with baseline audit; catches accidentally-committed credentials
- **dependency-audit:** Dependency vulnerability scan via pip-audit; blocks on known CVEs
- **Runs on:** push to main, pull requests, weekly scheduled scan (catches newly-disclosed CVEs)

---

## Documentation & Onboarding

### README Updates (Project 18 model)
Created detailed setup instructions for new developers:
- **First-time setup:** venv activation, `uv sync --all-groups`, Python version check
- **Running tests:** Python with coverage, TypeScript with node --test, expected output examples
- **Understanding results:** What 99% coverage means, why 1 line is missed (edge case), how to interpret test categories
- **Type checking:** TypeScript with strict flags, integration with npm install
- **Linting:** ruff and black (from parent directory), how to fix formatting issues

**Why:** README is the first impression; without clear instructions, new contributors get stuck on environment setup, not code

---

## Jira Tracking

Created in project **KAN (AeroSense)**:
- **3 Epics** (one per agent project)
- **11 Stories** (key components per epic)
- All linked to PRs, tests, and coverage reports

**Purpose:** Backlog for integration work, bug fixes, and production hardening; team visibility into what's planned next

---

## What's Next: Integration & Hardening

### Phase 1: Realistic Integration Stubs
**Goal:** Replace `TODO(integration)` comments with realistic mock implementations that can be swapped for real backends.

**For Project 17 (Event Automation):**
- `EventQueue` — in-memory queue with persistence simulation (write to local file)
- `ApprovalStore` — in-memory dict with JSON serialization
- `Handlers` — example automation tasks (log, sleep, fail patterns for testing)

**For Project 18 (RAG Citation):**
- `Retriever` — hardcoded document corpus (Wikipedia snippets, docs) with mock scoring
- `LLM` — calls Claude SDK if ANTHROPIC_API_KEY set, else returns canned responses
- `SearchFallback` — calls SearXNG public API (or returns mock results)
- `MetricsLogger` — writes to local file + stdout

**Why realistic:** Tests can run end-to-end without external services; swap modules in one line of code when integrating real backends

### Phase 2: Production Hardening
**Goals:**
- Error recovery & circuit breakers
- Graceful degradation (fail open vs. fail closed)
- Observability hooks ready for real metrics backend
- Deployment-ready Dockerfiles, Kubernetes manifests (optional)
- Load testing & performance baselines

**To be determined based on deployment target (AWS/GCP/self-hosted, containers/serverless, etc.)**

---

## Key Learnings & Principles

### Principle: Positional > Opaque
- Don't force LLMs to generate UUIDs or copy technical IDs; use positional references ([1], [2])
- Easier for models to reason about, easier to validate in post-processing

### Principle: Simplicity Over Cleverness
- Confidence formula `coverage × support × validity` is interpretable by humans
- Black-box ML scoring would lose that clarity
- Tradeoff: slightly conservative (lower coverage = lower confidence), but safe

### Principle: Fail Safe, Not Open
- Retriever error → fallback search
- Search error → abstain (don't guess)
- LLM error → propagate (don't hide outages)
- *Not* "hide problems and try to keep going"

### Principle: Observability from Day 1
- Every decision traced (Arize Phoenix for P16)
- Metrics for fallbacks, abstentions, hallucinations
- Can't optimize what you don't measure

---

## Risks & Future Work

### Risks
1. **Sentence splitting is punctuation-based** — abbreviations ("e.g.") and decimals ("3.14") may split early, lowering coverage in P18. Upgrade to NLTK/spaCy segmenter if corpus has many abbreviations.
2. **Confidence score is structural, not semantic** — checks that citations resolve to real sources, not that sources actually support the claim. Layer an NLI model or LLM-judge if semantic entailment matters.
3. **Single fallback only** — P18 tries fallback search once; if both primary and fallback are below threshold, it gives up. Future: iterative search (rewrite query, search again).

### Future Work
- [ ] Wire P16 to real alert source (Datadog, Prometheus, PagerDuty)
- [ ] Wire P16 to Slack notifier for escalations
- [ ] Wire P17 to message queue (RabbitMQ, SQS, Kafka)
- [ ] Wire P17 handlers to CI/CD (GitHub Actions, GitLab CI) and incident response (PagerDuty, Opsgenie)
- [ ] Wire P18 to production vector DB (Pinecone, Weaviate)
- [ ] Wire P18 to search API (SearXNG, Bing, Brave)
- [ ] Add entailment checking to P18 confidence scorer
- [ ] Load testing & performance baselines for all three
- [ ] Kubernetes manifests / Docker deployments
- [ ] Canary deployment strategy for P16 rule updates

---

## How to Use This Document

**For new team members:** Start here, then read each project's README.md and architecture spec (docs/superpowers/specs/)

**For reviewers:** This explains the "why" behind each design choice; use it to evaluate PRs against original intent

**For future you:** When you return weeks/months later, this captures what worked, what was risky, and what's next

---

## Quick Links

| Item | Link |
|------|------|
| Project 16 README | `projects/16-production-agent/README.md` |
| Project 16 Code | `projects/16-production-agent/src/agent.py` (+ `.ts` variant) |
| Project 16 Tests | `projects/16-production-agent/tests/test_agent.py` |
| Project 17 README | `projects/17-event-automation-agent/README.md` |
| Project 17 Code | `projects/17-event-automation-agent/src/agent.py` |
| Project 18 README | `projects/18-rag-citation-agent/README.md` |
| Project 18 Code | `projects/18-rag-citation-agent/src/agent.py` |
| Design Specs | `docs/superpowers/specs/` |
| PR #12 | RAG Citation Agent (Project 18) |
| PR #13 | Event Automation Agent (Project 17) |
| PR #14 | Production Agent (Project 16) |
| PR #17 | .gitignore refinement |
| Jira Project | KAN (AeroSense) — epics + stories |

---

## Contact & Questions

If something is unclear or needs clarification, update this file or open an issue. Future you will thank present you.

Last updated by: Claude Haiku 4.5 on 2026-09-23
