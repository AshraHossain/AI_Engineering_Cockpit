# 18 — RAG Citation Agent

A production-ready skeleton for a retrieval-augmented agent that answers only from its sources, in both **Python** (`asyncio`) and **TypeScript** (Node `async`/`await`). Every sentence must cite a retrieved source; citations are validated against what the model was shown; low-confidence answers trigger one external search, and if still below threshold, the agent says **"I don't know."** instead of hallucinating.

**What's implemented:** grounding logic (citation validation, confidence scoring, fallback routing, abstention), circuit breakers per dependency, structured JSON logging with correlation IDs, health checks, and a load-test harness. **52 tests, 85% overall coverage** (99% on the core grounding/scoring logic — see [Coverage Report](#coverage-report)).

**What's stubbed:** `Retriever`, `LLM`, and `SearchFallback` have realistic file-backed / mock implementations in `src/integrations.py` (a hardcoded corpus, canned or real-Claude responses, an offline-first search fallback) so the whole pipeline runs with no external services — but they're still marked `TODO(integration)` for wiring to your actual vector DB, model provider, and search API.

**Zero dependencies:** standard library only in both languages.

## What's in the box

| File | What it is |
|---|---|
| `src/agent.py` | The core: retrieval, grounding, confidence scoring, fallback, abstention. |
| `src/agent.ts` | The same core, component for component, in TypeScript. |
| `src/integrations.py` | Realistic stubs: hardcoded-corpus retriever, mock/Claude LLM, offline-first search fallback. |
| `src/circuit_breaker.py` | Per-dependency breaker; open circuit degrades to the safe default (empty context / abstain / empty results) instead of raising. |
| `src/structured_logging.py` | JSON log lines with a `correlation_id` bound per query, threaded through retrieval and search. |
| `src/health.py` | `/healthz` (liveness) and `/readyz` (readiness) over plain `http.server`; readiness reflects real circuit-breaker state. |
| `example.py` | Runnable end-to-end demo wiring all of the above together. |
| `load_test.py` | Throughput/latency baseline against the mock LLM and an offline fallback, isolated from network calls. |

`src/agent.py` and `src/agent.ts` are component-for-component identical.
Everything else in this table is **Python only** — there is no TypeScript twin
yet for the integration stubs, circuit breaker, health checks, or logging.

## Architecture

### Components

| Component | Responsibility | Implements |
|---|---|---|
| **`Retriever`** | Fetch passages from vector DB or hybrid search. Score each in [0, 1]. | Abstract interface; implement with your backend. |
| **`LLM`** | Take query + context, return answer. Prompted to cite as `[n]`. | Abstract interface; integrations with Claude SDK provided in comments. |
| **`CitationGrounder`** | Resolve `[n]` markers to real sources. Renumber by first appearance. Detect and flag invented citations. | Core logic, fully tested. |
| **`ConfidenceScorer`** | Score answer quality: coverage × support × validity. Return 0 for "I don't know." | Core logic, fully tested. |
| **`SearchFallback`** | Execute external search if primary confidence is low. Merge results into context. | Abstract interface; implement with your search provider. |
| **`MetricsLogger`** | Record counters, timings, and distributions for observability. | Protocol (interface); implement with your metrics backend. |
| **`RAGAgent`** | Orchestrate: retrieve → generate → ground → score → fallback → abstain. | Core logic, fully tested. |

`src/agent.py` and `src/agent.ts` are component-for-component identical, sharing the same design but respecting each language's idioms.

## Grounding Rules

### Citations

- **Positional, not opaque.** The prompt lists retrieved passages as `[1]`, `[2]`, ... so the model never copies UUIDs or URIs.
- **Renumbered by first appearance.** In the returned text, `[n]` refers to `citations[n-1]`, with markers renumbered in order of first use (e.g., `[3][1]` becomes `[1][2]`).
- **Invented citations are caught.** A marker with no matching source is removed from the text, lowers the confidence score, and increments `rag.hallucination_flag`.

### Confidence Scoring

```
confidence = coverage × support × validity

coverage   = sentences with ≥1 valid citation / total sentences
support    = mean retrieval score of distinct cited sources (clamped to [0, 1])
validity   = markers that resolve to a source / total markers

threshold  = 0.6 (configurable)
```

Special cases:
- **"I don't know"** (exact match, case-insensitive) always scores 0.
- **No sentences or no markers** → confidence 0.
- **Empty context** → confidence 0 (skip LLM call, go straight to fallback).

### Fallback and Abstention

1. **Primary path:** retrieve → generate → ground → score.
   - Confidence ≥ threshold → return answer with citations and `used_fallback=false`.
   
2. **Low confidence:** trigger one fallback search.
   - Merge search results into original context (de-duplicated by `source_uri`).
   - Generate → ground → score again.
   - Confidence ≥ threshold → return answer with citations and `used_fallback=true`.
   
3. **Still below threshold:** abstain.
   - Text: `"I don't know."`, no citations, confidence = actual score, `used_fallback=true`.

### Error Handling

- **Retriever error** → logged as `rag.retrieve.error`, treated as empty context → fallback.
- **Search error** → logged as `rag.fallback.error` → abstain immediately.
- **LLM error** → propagated to caller (silent "I don't know" would hide outages).

## Data Models

### Query
```python
class Query:
    id: str                   # Correlates logs across stages
    text: str                 # User question
    timestamp: datetime       # When asked
    metadata: dict            # Optional tags for routing
```

### ContextItem
```python
class ContextItem:
    id: str                   # Internal ref for renumbering
    source_uri: str           # For deduplication and citation
    title: str                # Display name
    snippet: str              # Passage text
    score: float              # Retrieval score, [0, 1]
```

### Citation
```python
class Citation:
    context_id: str           # Which source this cites
    source_uri: str           # Echoed from ContextItem
    title: str                # Echoed from ContextItem
    snippet: str              # Echoed from ContextItem
```

### Answer
```python
class Answer:
    text: str                 # Generated response (with citations)
    citations: [Citation]     # Resolved citations, in order
    confidence: float         # Coverage × support × validity
    used_fallback: bool       # Whether fallback search was triggered
    raw_context_ids: [str]    # IDs of sources used (for audit)
```

## Resilience, Logging, and Health Checks

Wrap any `Retriever`, `LLM`, or `SearchFallback` in the matching
`CircuitBreaker*` class (`src/circuit_breaker.py`, `src/integrations.py`) to
stop calling a dependency that's failing repeatedly. Each degrades to the
answer `RAGAgent` would already produce for that failure mode, rather than
raising:

| Wrapped | Circuit open → |
|---|---|
| `CircuitBreakerRetriever` | empty context (routes to fallback search, same as a real empty retrieval) |
| `CircuitBreakerLLM` | abstain immediately (`ABSTAIN_TEXT`) — never guess when the model is known-unhealthy |
| `CircuitBreakerSearchFallback` | empty results (the primary answer, if any, still returns) |

`example.py` wraps all three and registers a `HealthChecker` against them:
`GET /healthz` is 200 whenever the process is alive; `GET /readyz` is 200 only
if the LLM's circuit is closed **and** at least one of retriever/search-fallback
is closed (`require_any` — matching `RAGAgent`'s own tolerance for one of those
two being down). The demo prints both URLs on startup.

Every log line is JSON (`configure_json_logging()`), with `correlation_id` set
to the query's own ID via `bind(logger, correlation_id=query.id)` — grep one ID
to follow a single query across retrieval, fallback, and the circuit-breaker
wrappers. `LLM.complete()` takes a bare prompt string, not a `Query`, so
`LLMWithClaude` has no correlation ID to attach without widening that core
interface; that's a documented gap, not an oversight. Set
`EXAMPLE_LOG_FORMAT=text` for plain text instead while developing.

```bash
PYTHONPATH=src python load_test.py --count 500 --concurrency 16
```

measures the pipeline's own retrieve → generate → ground → score overhead
against `MockLLM` and an offline fallback stand-in — deliberately not
`SearchFallbackStub`, whose real network attempt to a public SearXNG instance
would make the benchmark measure that service's latency instead of this
codebase's.

## Getting Started

### Prerequisites

- **Python 3.11+**
- **Node 22.18+** (for TypeScript)
- **VS Code** (recommended) or any text editor

### First Time Setup

Open VS Code, navigate to this directory, and:

```bash
# Activate the virtual environment
source .venv/bin/activate                    # macOS/Linux
# or
.venv\Scripts\activate                       # Windows

# Install the project (creates venv if needed, installs all dependencies)
uv sync --all-groups

# Verify Python version
python --version
```

You should see Python 3.11 or 3.12 installed.

### Running the Tests

After setup, run tests to verify everything works:

#### Python (52 tests, ~2.5 seconds)

```bash
uv run pytest -v
```

**Output you'll see:**
```
tests/test_agent.py::test_a_fingerprint_is_stable_and_covers_the_model PASSED
tests/test_agent.py::test_outcome_for_each_final_stop_reason[end_turn-ok] PASSED
...
52 passed in 2.58s
```

**What this means:** all grounding rules are working correctly, plus the operational
pieces added on top of them. Test files, by what they cover:
- `test_agent.py` (16) — citation renumbering, unknown-ref detection, confidence
  scoring, fallback merging, abstention, error propagation
- `test_circuit_breaker.py` (9) — CLOSED/OPEN/HALF_OPEN state transitions
- `test_integrations.py` (6) — the circuit-breaker wrappers around
  Retriever/LLM/SearchFallback degrade to their safe defaults when open
- `test_health.py` (10) — readiness aggregation logic, plus the real HTTP
  server on an ephemeral port
- `test_structured_logging.py` (6) — JSON formatting and correlation-ID binding
- `test_load_test.py` (5) — the load-test harness itself produces sane output

#### TypeScript (16 tests, Node 22.18+ required)

```bash
node --test tests/agent.test.ts
```

**Output:**
```
✔ test/tests for citation grounder
✔ test/tests for confidence scorer
✔ test/agent orchestrator
✔ ...
16 tests passing
```

#### Coverage Report

```bash
uv run pytest --cov=src --cov-branch --cov-report=term
```

**Output shows:**
```
Name                        Stmts   Miss Branch BrPart  Cover
---------------------------------------------------------------
src/agent.py                  197      1     28      2    99%
src/circuit_breaker.py         72      6     20      4    89%
src/health.py                  57      0      8      0   100%
src/integrations.py           162     61     22      2    62%
src/structured_logging.py      29      6     10      0    74%
---------------------------------------------------------------
TOTAL                          517     74     88      8    85%
```

`agent.py`'s core grounding/scoring logic is the part that matters most and is
covered most thoroughly (99%). `integrations.py` is lower on purpose: it's
stub code you're expected to replace, and a lot of it is file I/O and a real
network call attempt (`SearchFallbackStub`) that's exercised by `example.py`
and `load_test.py` rather than by unit tests.

### Understanding the Test Results

Each test file (`tests/test_agent.py` or `tests/agent.test.ts`) contains multiple test cases. Here's what each category tests:

#### Grounding Tests
- ✅ `test_marker_renumbering` — checks that citations are renumbered by first appearance
- ✅ `test_unknown_ref_detection` — verifies invented citations are caught
- ✅ `test_confidence_calculation` — ensures coverage × support × validity works

#### Fallback Tests
- ✅ `test_low_confidence_triggers_fallback` — confirms fallback search is called when needed
- ✅ `test_fallback_merges_context` — verifies results are deduplicated

#### Abstention Tests
- ✅ `test_abstain_when_below_threshold` — checks "I don't know" is returned when appropriate
- ✅ `test_abstention_has_no_citations` — confirms abstained answers don't cite sources

#### Error Handling Tests
- ✅ `test_retriever_error_triggers_fallback` — retriever failure → search
- ✅ `test_search_error_triggers_abstention` — search failure → "I don't know"
- ✅ `test_llm_error_propagates` — model errors bubble up to caller

### Type Checking (TypeScript only)

```bash
npm install --prefix .                      # Install @types/node
npx tsc --strict --noImplicitOverride --noUncheckedIndexedAccess --erasableSyntaxOnly --verbatimModuleSyntax
```

**Output:** no errors if type-check passes, or error lines with line numbers if there are type issues.

### Linting and Formatting

From the parent directory:

```bash
cd ..
uv run ruff check 18-rag-citation-agent/          # Check for style issues
uv run black --check 18-rag-citation-agent/       # Check formatting
uv run black 18-rag-citation-agent/               # Auto-fix formatting
```

**Output:**
```
All checks passed!                           # Good ✅
error: Found 3 formatting issues             # Need to run black
```

### Running the Test Suites

Python (52 tests: grounding, scoring, circuit breakers, health checks, logging,
load-test harness) and TypeScript (16 tests: the core grounding rules only —
the operational additions are Python-only, see [What's in the box](#whats-in-the-box)).
Run one or both:

**Python tests with coverage:**
```bash
uv run pytest -v --cov=src --cov-report=term
```

**TypeScript tests:**
```bash
node --test tests/agent.test.ts
```

**All checks (format, lint, type, test):**
```bash
# From parent directory
uv run black --check 18-rag-citation-agent/       # Formatting
uv run ruff check 18-rag-citation-agent/          # Linting
cd 18-rag-citation-agent
uv run pytest -v                                   # Tests
node --test tests/agent.test.ts                   # TypeScript tests
```

### Type Checking (TypeScript only)

```bash
npm install --prefix .  # Install @types/node
npx tsc --strict --noImplicitOverride --noUncheckedIndexedAccess --erasableSyntaxOnly --verbatimModuleSyntax
```

### Linting

```bash
cd ..
uv run ruff check 18-rag-citation-agent/
uv run black --check 18-rag-citation-agent/
```

## Integration Checklist

`src/integrations.py` already has working (if simplistic) implementations of
all three — `FileBasedRetriever` (hardcoded corpus, word-overlap ranking),
`MockLLM` / `LLMWithClaude` (canned responses, or real Claude if
`ANTHROPIC_API_KEY` is set), `SearchFallbackStub` (tries a public SearXNG
instance, falls back to a fixed mock result). Before production:

- [ ] **Replace the retriever's corpus.** `FileBasedRetriever` returns results
  from five hardcoded Wikipedia snippets. Implement `Retriever.retrieve()`
  against your vector DB or hybrid search instead. Ensure scores are [0, 1].
- [ ] **Confirm the LLM in production.** `LLMWithClaude` already calls Claude
  when `ANTHROPIC_API_KEY` is set — verify the model respects the grounding
  prompt and produces `[n]` citations at your expected volume and latency.
- [ ] **Replace the search fallback's target.** `SearchFallbackStub` calls a
  *public* SearXNG instance — fine for a demo, not for anything handling real
  traffic or real user queries. Point `SearchFallback.search()` at your own
  search infrastructure (private SearXNG, Bing, Brave, etc.) and restrict
  domains, since results feed the prompt as untrusted data.
- [ ] **Tune the confidence threshold.** Collect labelled questions with known good answers. Plot fallback rate, abstain rate, and accuracy against threshold values (0.4–0.8). Pick the threshold that balances false negatives (missing good answers) vs false positives (returning bad answers).
- [ ] **Add entailment checking.** The current `ConfidenceScorer` proves a sentence cites a real source, not that the source actually supports it. Layer an NLI model or LLM-judge (`confidence_score` has a TODO) to check entailment.
- [ ] **Secure your context.** The prompt fences retrieved and search snippets as data, but enforce these extra safeguards:
  - Restrict search domains (no arbitrary URLs).
  - Validate retrieved sources (e.g., verify they're from your own DB, not user-injected).
  - Log all sources sent to the model for audit.
- [ ] **Handle sentence boundaries.** Sentence splitting is punctuation-based (`.`, `!`, `?`). If your corpus has many abbreviations ("e.g.", "vs.") or decimals ("3.14"), switch to a real segmenter (e.g., NLTK, spaCy).
- [ ] **Set up metrics.** Implement the `MetricsLogger` interface to record:
  - `rag.query` (counter, per request)
  - `rag.fallback` (counter, triggered when needed)
  - `rag.abstain` (counter, final answer is "I don't know")
  - `rag.hallucination_flag` (counter, unknown citations detected)
  - `rag.retrieve.error`, `rag.fallback.error` (counters)
  - `rag.confidence` (histogram, tagged `stage=primary|fallback`)
  - `rag.stage` (timing, tagged by stage: retrieve, generate, fallback_search)

## Known Limitations

- **Sentence splitting:** Punctuation-based. Abbreviations and decimals may cause early splits, lowering coverage. Upgrade to NLTK or spaCy segmenter if needed.
- **Bracket ambiguity:** A bare bracketed number in prose (e.g., "published in [2023]") is parsed as a citation marker and counts as an unknown reference. Quote it or escape it if the source doesn't explain it.
- **Structural vs. semantic:** The confidence score checks that citations are **structurally valid** (source exists, marker resolves). It does NOT check **entailment** (whether the source actually supports the claim). Add an NLI layer or LLM-judge to catch semantic hallucinations.
- **Single fallback:** Only one external search is attempted. If both primary and fallback answers are below threshold, the agent gives up. Future versions could iterate: search → rewrite query → search again.

## Metrics and Observability

The agent emits structured events during each run:

| Metric | Type | When | Tags |
|--------|------|------|------|
| `rag.query` | counter | Each question | — |
| `rag.fallback` | counter | Search triggered | — |
| `rag.abstain` | counter | Final answer is "I don't know" | — |
| `rag.hallucination_flag` | counter | Unknown citation detected | — |
| `rag.retrieve.error` | counter | Retriever raised | — |
| `rag.fallback.error` | counter | Search raised | — |
| `rag.confidence` | histogram | After grounding | `stage=primary\|fallback` |
| `rag.stage` | timing | Per stage | `stage=retrieve\|generate\|fallback_search` |

Implement `MetricsLogger` with your observability backend (Prometheus, DataDog, etc.) to track these.

## Design Rationale

See [`docs/superpowers/specs/2026-09-17-rag-citation-grounding-design.md`](../../docs/superpowers/specs/2026-09-17-rag-citation-grounding-design.md) for the full design spec, including:
- Why positional citations (no opaque IDs)
- Confidence formula derivation
- Error handling philosophy (fail fast vs. fallback)
- Comparison to alternative approaches
