# API Comparison: Gemini vs OpenAI vs Claude

This repo is built to be provider-agnostic — `projects/03-multi-model-orchestrator`
exists specifically to compare providers side by side. This doc gives you the
qualitative shape of each API so you can pick sensibly. Exact prices and
context-window sizes change frequently; check each provider's pricing page
before making cost-sensitive decisions rather than trusting numbers pasted
into a doc.

## Pricing model shape

All three providers price primarily per-token, split into input and output
rates, with output tokens typically costing more than input tokens:

| Provider | Pricing shape | Notes |
|---|---|---|
| **Google Gemini** | Per-token, tiered by model size (Flash/Pro-class tiers) | Often has a free tier suitable for prototyping; check current quotas |
| **OpenAI** | Per-token, tiered by model (mini/standard/reasoning-tier models) | Prompt caching can reduce cost on repeated context |
| **Anthropic Claude** | Per-token, tiered by model (Haiku/Sonnet/Opus-class) | Prompt caching and batch APIs available for cost reduction |

All three also charge separately (and differently) for embeddings, fine-tuning,
and batch/async request modes where available. Treat any single "$ per 1M
tokens" number as a snapshot, not a constant — verify against the provider's
live pricing page before budgeting.

## Context windows

All three providers offer models spanning from moderate (tens of thousands of
tokens) up to very large (six-figure token counts) context windows, with the
larger windows generally reserved for their flagship/latest model tier. If
your use case needs a specific minimum context size, check the current specs
for the exact model you intend to call — this changes across model
generations faster than this doc can track.

## Strengths (qualitative)

- **Gemini**: strong at multimodal input (image/video/audio alongside text),
  competitive pricing at the low end, tight integration with Google Cloud
  tooling. Good default for cost-sensitive or multimodal prototypes.
- **OpenAI**: broad ecosystem (tools, function calling maturity, wide library
  support), strong general-purpose reasoning, large developer community means
  more existing examples and third-party integrations to draw from.
- **Claude (Anthropic)**: strong at long-context reasoning, careful
  instruction-following, and structured/agentic tool use; frequently favored
  for tasks that need reliable adherence to complex system prompts or
  extended multi-step reasoning.

These are general tendencies, not benchmarks — always evaluate against your
own task (see `cockpit/evaluation/` once enabled) rather than picking a
provider on reputation alone.

## When to pick which

- **Prototyping fast on a budget** → start with Gemini's free/low tier.
- **Need the widest tooling/ecosystem** → OpenAI.
- **Long documents, complex multi-step agents, or strict instruction-following**
  → Claude.
- **Uncertain / want to compare empirically** → run the same prompt through
  all three via `projects/03-multi-model-orchestrator` and compare outputs,
  latency, and cost directly for your actual workload.

## SDK packages

| Provider | Package | Entry point |
|---|---|---|
| **Google Gemini** | `google-genai` | `from google import genai` → `genai.Client(api_key=...)` → `client.models.generate_content(model=..., contents=...)` |
| **OpenAI** | `openai` | `OpenAI(api_key=...)` → `client.chat.completions.create(model=..., messages=[...])` |
| **Anthropic Claude** | `anthropic` | `Anthropic(api_key=...)` → `client.messages.create(model=..., messages=[...])` |

Note for Gemini: the older `google-generativeai` package (`import
google.generativeai as genai`, `genai.GenerativeModel(...)`) has reached end of
life and is **not** what this repo targets — use `google-genai` and its
client-based API. The Gemini model ids that SDK generation defaulted to are
gone too; see `models/registry.json` for the ids that are currently served.

## In this repo

- `cockpit/config/settings.py` reads `GEMINI_API_KEY`, `OPENAI_API_KEY`, and
  `ANTHROPIC_API_KEY` from the environment — set only the ones you need.
- `projects/03-multi-model-orchestrator` shows how to call Gemini and OpenAI
  behind a common interface; the same pattern extends to Claude.
- Cost tracking (once `monitoring` is enabled via feature flags) is the right
  place to record actual observed per-call cost rather than relying on
  published rate cards.
