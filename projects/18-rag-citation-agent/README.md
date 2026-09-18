# 18 — RAG Citation Agent

A production-ready skeleton for a retrieval-augmented agent that answers only from its sources, in both **Python** (`asyncio`) and **TypeScript** (Node `async`/`await`). Every sentence must cite a retrieved source; citations are validated against what the model was shown; low-confidence answers trigger one external search, and if still below threshold, the agent says **"I don't know."** instead of hallucinating.

**What's implemented:** grounding logic (citation validation, confidence scoring, fallback routing, abstention). **16 tests at 99% coverage** verify all rules end-to-end.

**What's stubbed:** `Retriever`, `LLM`, and `SearchFallback` are marked `TODO(integration)` — wire them to your vector DB, model provider, and search API.

**Zero dependencies:** standard library only in both languages.

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

## Running

### Tests (16 tests, both languages, all rules covered)

Python:
```bash
uv run pytest
```

TypeScript (Node 22.18+, no build needed):
```bash
node --test tests/agent.test.ts
```

Both test suites verify:
- Citation marker renumbering
- Unknown reference detection
- Confidence formula edge cases
- Fallback merge behavior
- Abstention logic
- Retriever and search error handling
- LLM error propagation

Coverage: Python 99%, TypeScript type-check strict.

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

Before production:

- [ ] **Wire the retriever.** Implement `Retriever.get_context()` to call your vector DB. Ensure scores are [0, 1].
- [ ] **Wire the LLM.** Implement `LLM.complete()` to call your model provider (Claude recommended; see comments for SDK integration). Test that the model respects the grounding prompt and produces `[n]` citations.
- [ ] **Wire search fallback.** Implement `SearchFallback.search()` to call your external search API (SearXNG, Bing, Brave, etc.).
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
