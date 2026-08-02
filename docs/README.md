# Documentation Index

Welcome to the AI Engineering Cockpit docs. This folder covers everything you
need to understand, run, and extend the cockpit.

## Start here

| Doc | What it covers |
|---|---|
| [GETTING_STARTED.md](GETTING_STARTED.md) | Clone the repo, run setup, execute your first example project |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How `cockpit/` and `projects/` fit together, feature flags, cross-platform split |
| [WINDOWS_VS_MAC.md](WINDOWS_VS_MAC.md) | Why Windows is cloud-only and Mac is hybrid (Ollama + cloud) |

## Reference

| Doc | What it covers |
|---|---|
| [API_COMPARISON.md](API_COMPARISON.md) | Gemini vs OpenAI vs Claude — pricing shape, context windows, when to pick which |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Fixes for common setup issues (missing API keys, `uv` not found, venv problems) |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Taking an example project from `projects/` to production |

## Recipes

Short, copy-pasteable patterns for common tasks:

- [recipes/basic-rag.md](recipes/basic-rag.md) — minimal retrieval-augmented generation loop
- [recipes/multi-turn-chat.md](recipes/multi-turn-chat.md) — maintaining conversation history across turns
- [recipes/streaming-response.md](recipes/streaming-response.md) — streaming tokens back to a caller

## Where things live

- `cockpit/` — the shared framework (testing, evaluation, red-teaming, security,
  monitoring, governance, config, utils). See [ARCHITECTURE.md](ARCHITECTURE.md).
- `projects/` — small, self-contained example applications (`01-hello-world` through
  `05-hybrid-orchestrator`), each with its own `pyproject.toml` and isolated venv.
- `models/` — model registry and local-model assets (Ollama Modelfiles, Hugging Face
  model list).
- `tests/` — root-level tests that exercise `cockpit/config/*` and repo structure.
  Each project also has its own tests under `projects/0*-*/tests/`.

If a doc here goes stale, prefer fixing it over deleting it — this index is the
map newcomers use to find their way around the repo.
