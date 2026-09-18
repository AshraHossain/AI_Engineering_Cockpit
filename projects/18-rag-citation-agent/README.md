# 18 — RAG Citation Agent

A skeleton for a retrieval-augmented agent that answers only from its
sources, in both Python (`asyncio`) and TypeScript (Node `async`/`await`).
Every sentence must cite a retrieved source; citations are checked against
what the model was actually shown; low-confidence answers trigger one
external search, and if that is not enough either, the agent says
**"I don't know."** instead of guessing.

Nothing opens a socket: the retriever, the model call and the search API are
marked `TODO(integration)`. What *is* implemented, and tested, is the
grounding logic, which is the part that decides whether an answer can be
trusted.

Standard library only. No runtime dependencies in either language.

## Layers

| Component | Responsibility |
|---|---|
| `Retriever` | Vector DB or hybrid search. Returns passages scored in [0, 1]. |
| `LLM` | Text in, text out. `build_grounded_prompt` numbers the sources and demands `[n]` citations. |
| `CitationGrounder` | Resolves `[n]` markers to real sources, renumbers them, drops and counts invented ones. |
| `ConfidenceScorer` | `coverage x support x validity`, 0 for "I don't know". |
| `SearchFallback` | External search, used once when confidence is below the threshold. |
| `MetricsLogger` | Counters, stage timings, confidence distribution. |
| `RAGAgent` | Wires the layers together and owns the answer / fallback / abstain decision. |

`src/agent.py` and `src/agent.ts` are the same design, component for component.

## Grounding rules

- **Citations are positional.** The prompt lists sources as `[1]`, `[2]`, ...
  so the model never copies opaque IDs. In the returned text, `[n]` is
  renumbered to point at `citations[n-1]`.
- **Invented citations are caught.** A marker with no matching source is
  removed, lowers the score, and raises `rag.hallucination_flag`.
- **Confidence** = share of sentences with a valid citation × mean retrieval
  score of the cited sources × share of markers that were valid.
  Default threshold 0.6.
- **Fallback** merges search results into the original context (de-duplicated
  by `source_uri`) rather than replacing it.
- **Abstain, don't guess.** If the fallback answer is still below the
  threshold, the answer is `I don't know.` with no citations.
- **Failures:** a failing retriever means no context (so fallback); a failing
  search means abstain; a failing model call propagates to the caller.

The full rules are in
[`docs/superpowers/specs/2026-09-17-rag-citation-grounding-design.md`](../../docs/superpowers/specs/2026-09-17-rag-citation-grounding-design.md), section 3.

## Run the tests

Both suites test the same sixteen rules.

```bash
uv run pytest
```

```bash
node --test tests/agent.test.ts
```

The TypeScript uses only erasable syntax (no enums, no constructor parameter
properties), so Node 22.18+ runs it directly with no build step.

## Before production

- Calibrate the threshold on a labelled question set: plot fallback rate and
  abstain rate against answer accuracy, then pick the threshold.
- Add an entailment check behind `ConfidenceScorer`. The current score proves
  a sentence cites a real source, not that the source supports it.
- Treat retrieved snippets and search results as untrusted input. The prompt
  fences them as data, but restrict search domains too.
- Sentence splitting is punctuation-based; switch to a real segmenter if
  abbreviations and decimals make coverage noisy.
