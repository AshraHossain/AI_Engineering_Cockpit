# AI Engineering Cockpit

A production-grade AIDevOps platform: a modular framework for testing,
evaluating, red-teaming, securing, monitoring, and governing AI systems —
plus a set of runnable example projects that show how the pieces fit
together in practice.

This is **Tier 1 (MVP)**. Testing and security ship with real, working
logic; evaluation, red-teaming, monitoring, and governance ship as typed
skeletons behind feature flags, ready to be filled in as later tiers land.

## Quick start (5 minutes)

```bash
git clone <this-repo-url> AI_Engineering_Cockpit
cd AI_Engineering_Cockpit

# Windows
./scripts/setup-windows.ps1
# Mac
./scripts/setup-mac.sh

cp .env.example .env   # then add your GEMINI_API_KEY

cd projects/01-hello-world
cp .env.example .env   # add GEMINI_API_KEY here too
uv sync
uv run python src/main.py
```

The setup scripts install [`uv`](https://docs.astral.sh/uv/) if it's
missing, sync dependencies, and run the root test suite (which needs no API
keys — it's all mocked or config-only). See [SETUP.md](SETUP.md) for the
full walkthrough and troubleshooting.

## What makes this different

- **Security is first-class, not bolted on.** `cockpit/security/` ships
  real regex-based prompt-injection detection and PII masking (with Luhn
  validation to cut credit-card false positives) — not a TODO.
- **Every framework is feature-flagged.** `cockpit/config/feature_flags.py`
  gates testing/evaluation/red-teaming/security/monitoring/governance
  independently, with pre-built profiles for startup, enterprise, research,
  and safety-focused use cases.
- **Examples are real, isolated, and tested.** Each `projects/0X-*/` is its
  own `uv`-managed project with mocked tests — no live API key needed to
  run `pytest`, but the code itself talks to real provider SDKs.
- **Cross-platform by design.** Windows runs cloud-only; Mac can hybridize
  with local Ollama models. See
  [docs/WINDOWS_VS_MAC.md](docs/WINDOWS_VS_MAC.md).

## Repository layout

```
cockpit/      Shared framework: testing, evaluation, red_teaming, security,
              monitoring, governance, config, utils. See docs/ARCHITECTURE.md.
projects/     5 standalone example projects, 01-hello-world through
              05-hybrid-orchestrator. Each has its own pyproject.toml.
models/       Model registry + Ollama/Hugging Face reference material.
docs/         Architecture, getting started, deployment, troubleshooting,
              API comparison, and recipe guides.
scripts/      Setup, test, format, and new-project scaffolding scripts.
config/       pytest, ruff, mypy, pre-commit, and coverage configuration.
tests/        Root-level tests covering cockpit/config/* and repo structure.
.github/      CI workflows (test, security, docs) and issue/PR templates.
```

## Examples

| Project | Demonstrates |
|---|---|
| [01-hello-world](projects/01-hello-world) | Minimal Gemini API call |
| [02-rag-chatbot](projects/02-rag-chatbot) | Retrieval-augmented generation |
| [03-multi-model-orchestrator](projects/03-multi-model-orchestrator) | Comparing Gemini vs OpenAI |
| [04-streaming-responses](projects/04-streaming-responses) | Streaming model output |
| [05-hybrid-orchestrator](projects/05-hybrid-orchestrator) | Local Ollama + cloud Gemini routing (Mac hybrid mode) |

## Installation

See [SETUP.md](SETUP.md) for detailed Windows (PowerShell) and Mac (Bash)
instructions, including troubleshooting.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Security issues: see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
