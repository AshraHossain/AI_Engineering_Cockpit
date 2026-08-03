# AI Engineering Cockpit

A production-grade AIDevOps platform: a modular framework for testing,
evaluating, red-teaming, securing, monitoring, and governing AI systems —
plus a set of runnable example projects that show how the pieces fit
together in practice.

**Tier 2 is complete.** Testing, security, evaluation, red-teaming, and
monitoring all ship with real, working logic and are enabled by default.
Governance remains a typed skeleton behind a feature flag, pending Tier 3.

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
- **The red-team harness grades your own defenses.** It runs its 32-payload
  corpus through the injection detector and reports which attacks slip past,
  so the security module's blind spots are measured rather than assumed.
  (Today: 12/32 caught — see [docs/SECURITY_COVERAGE.md](docs/SECURITY_COVERAGE.md).)
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

Projects 01-05 are standalone. Projects 06-10 additionally import the
`cockpit/` frameworks — they exist to show the platform in use. Every
project runs its tests offline with no API key, and 06-10 each support a
`--dry-run` flag so you can see them work before adding credentials.

| Project | Demonstrates |
|---|---|
| [01-hello-world](projects/01-hello-world) | Minimal Gemini API call |
| [02-rag-chatbot](projects/02-rag-chatbot) | Retrieval-augmented generation, with task-asymmetric embeddings |
| [03-multi-model-orchestrator](projects/03-multi-model-orchestrator) | Comparing Gemini vs OpenAI |
| [04-streaming-responses](projects/04-streaming-responses) | Streaming model output |
| [05-hybrid-orchestrator](projects/05-hybrid-orchestrator) | Local Ollama + cloud Gemini routing (Mac hybrid mode) |
| [06-eval-harness](projects/06-eval-harness) | Scoring a pipeline against a reference set — quality, safety, and cost |
| [07-red-team-runner](projects/07-red-team-runner) | Attacking a target with the injection corpus, and reporting where defenses leak |
| [08-cost-dashboard](projects/08-cost-dashboard) | Instrumenting model calls once to get both spend and latency |
| [09-agent-tool-use](projects/09-agent-tool-use) | Function calling, automatic and manually-authorized, with guard rails |
| [10-batch-pipeline](projects/10-batch-pipeline) | Bulk processing with rate limiting, retries, and partial-failure tolerance |

## Installation

See [SETUP.md](SETUP.md) for detailed Windows (PowerShell) and Mac (Bash)
instructions, including troubleshooting.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Security issues: see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
