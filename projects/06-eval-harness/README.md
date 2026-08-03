# 06 - Eval Harness

A runnable evaluation harness: score a Q&A pipeline against a reference
dataset and print a quality / safety / cost scorecard.

This is the first of two projects that make the cockpit's **Tier 2
frameworks** tangible. Projects 01-05 are standalone demos that never touch
`cockpit/`. This one is the opposite -- importing the frameworks *is* the
point.

## What it uses from `cockpit/`

| Framework module | What this project gets from it |
| --- | --- |
| `cockpit.evaluation.quality_metrics` | `evaluate_quality` (token F1, fuzzy similarity, normalized match, required-keyword coverage), plus `score_relevance` and `score_coherence` |
| `cockpit.evaluation.safety_evaluation` | `evaluate_safety` -- refusal detection, PII leakage, harm categories, and a `SafetyVerdict` per answer |
| `cockpit.evaluation.cost_evaluation` | `evaluate_cost` (cost per *passed* answer, quality per dollar) and `check_budget` |
| `cockpit.monitoring.cost_tracking` | `CostTracker` accumulating real token usage into a per-model spend breakdown |

## The one interesting design decision: everything is injected

The harness never imports an LLM SDK. `run_harness` takes an injected
`generate_fn(prompt) -> str`, which is why the entire test suite runs with
**no API key and no network**, and why you can point the harness at a raw
model client, a whole RAG pipeline, or an agent loop without changing a line
of harness code.

Token accounting has two modes:

- Inject a **`UsageReporter`** alongside the generator and the harness uses
  the provider's real `usage_metadata` counts. `GeminiGenerator` in
  `src/main.py` is both, so live runs are priced exactly.
- Omit it and the harness falls back to a documented ~4-characters-per-token
  estimate. The scorecard then prints `(token counts estimated from text
  length)` rather than quietly pretending the number is exact.

## Project layout

```
06-eval-harness/
├── pyproject.toml          # own deps; pythonpath = ["src", "../.."]
├── .python-version           # 3.11
├── data/eval_set.json        # 12-item reference set: question, answer, keywords
├── src/
│   ├── dataset.py             # load + validate the eval set (loud on malformed input)
│   ├── harness.py             # the harness: score, price, aggregate -- no SDK import
│   └── main.py                # CLI: wires in Gemini (live) or a canned stub (--dry-run)
└── tests/test_harness.py     # full harness against a fake generate_fn, no network
```

The repo root is `package = false` by design, so it is not installed into
this project's venv. Both `pyproject.toml` (`pythonpath = ["src", "../.."]`)
and a small `sys.path` insert at the top of `src/harness.py` and
`src/main.py` put it on the import path instead.

## How to run

```bash
cd projects/06-eval-harness
uv sync

# No API key needed -- uses the offline canned answer bank.
uv run python src/main.py --dry-run

# Live, against gemini-2.5-flash.
cp .env.example .env   # then fill in GEMINI_API_KEY
uv run python src/main.py --budget 0.05
```

Flags: `--dry-run`, `--dataset PATH`, `--limit N`, `--model NAME`,
`--threshold FLOAT`, `--budget USD`.

Exit codes: `0` every item passed, `2` the run completed but some items
failed, `1` a configuration or dataset error.

### Expected `--dry-run` output

```
==============================================================================
EVAL SCORECARD  --  cockpit-qa-reference-set
==============================================================================
Model                 gemini-2.5-flash
Pass threshold        0.35 overall quality

Items                 12
Passed                10
Failed                2
Errored               0
Pass rate             83.3%

QUALITY (mean across scored items)
  overall             0.595
  token F1            0.702
  keyword coverage    0.833
  relevance           0.419
  coherence           1.000

SAFETY
  unsafe              0
  needs review        0

COST  (token counts estimated from text length)
  input tokens        489
  output tokens       373
  total              $0.001079
  per passed answer  $0.000108
  quality per dollar  551.3

PER-ITEM
------------------------------------------------------------------------------
item                                score     kw        safety  result
q-001-http-404                      0.690   1.00          safe  PASS
...
q-009-temperature                   0.109   0.00          safe  FAIL
...
q-011-acid                          0.181   0.00          safe  FAIL
q-012-hallucination                 0.781   1.00          safe  PASS
==============================================================================
```

The two failures are deliberate. The canned answer bank is written to be
*realistic*, not perfect: `q-009` gets a vague non-answer ("It is a setting
you can tune") and `q-011` gets a confidently wrong expansion of ACID. A
harness that always reports 100% is a harness that is not measuring
anything, so the demo data proves the scoring actually discriminates.

Note `relevance` sits far below `overall` (0.419 vs 0.595). That is the
metric working as designed: `score_relevance` is content-word recall against
the *question*, and short direct answers legitimately do not echo every word
of the question back.

## The eval set

`data/eval_set.json` holds 12 items, each with an `item_id`, a `question`, a
`reference_answer`, and `required_keywords`. Questions cover general software
and AI engineering concepts (HTTP status codes, idempotency, ACID, rebase vs
merge, RAG, prompt injection, embeddings, tokens, temperature,
hallucination). Every reference answer is short, factual, and checkable
without external context, so the token-overlap and keyword metrics mean
something.

`src/dataset.py` validates the file structurally -- missing fields, duplicate
`item_id`s, a bare string where a keyword list belongs, and unparseable JSON
all raise `DatasetError` with the offending location named, rather than
producing a silently wrong scorecard three layers up.

## Scoring, and what "passed" means

An item passes when its **overall quality score** clears `--threshold`
(default `0.35`) **and** its safety verdict is not `unsafe`. The overall
score is `evaluate_quality`'s weighted blend of the components that apply:
normalized match (0.15), token F1 (0.30), fuzzy similarity (0.15), and
required-keyword coverage (0.20), renormalized over whatever participates.

The default threshold is deliberately not 0.8. These metrics are lexical, so
a *correct* answer phrased differently from the reference lands around
0.55-0.75, not 0.95. Setting the bar where correct paraphrases actually land
is the difference between a useful gate and one everybody disables.

Safety is checked on every answer even though the dataset is benign -- that
is what catches a pipeline that starts echoing PII from its retrieval
corpus. `test_run_harness_flags_unsafe_answers` pins that behaviour.

## Running the tests

No API key or network access is required. `tests/test_harness.py` covers:

- dataset validation: every malformed-input path, plus a round-trip
- per-item scoring: a good paraphrase passes, an off-topic answer fails
- cost: real reported usage vs the estimated fallback, tracker accumulation
- failure handling: a generator that raises, and one that returns a non-`str`
- aggregation: pass rates, budget overage, unsafe-answer flagging
- the CLI itself, `--dry-run` end-to-end via `capsys`

```bash
cd projects/06-eval-harness
uv run pytest -q
```

## Getting a Gemini API key

Create a free key at [Google AI Studio](https://aistudio.google.com/app/apikey).
