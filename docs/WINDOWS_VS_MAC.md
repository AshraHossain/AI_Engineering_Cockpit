# Windows vs Mac: Why the Platform Split

AI Engineering Cockpit runs on both Windows and Mac, but they are not treated
identically:

| | Windows | Mac |
|---|---|---|
| Cloud APIs (Gemini/OpenAI/Anthropic) | Yes | Yes |
| Local models via Ollama | Not supported | Supported (optional) |
| Mode | Cloud-only | Hybrid (local + cloud) |

## Why the split exists

Ollama's local-model runtime and the broader "run an LLM on your own GPU/CPU"
tooling ecosystem is most mature and best-supported on macOS and Linux.
Rather than pretend Windows support is equivalent (and let people hit broken,
half-tested local-model code), this repo draws the line explicitly:

- **Windows**: treated as cloud-only. Every example runs the same, calling
  Gemini, OpenAI, or Anthropic over the network. `OLLAMA_HOST` in `.env` is
  present for consistency but unused on Windows.
- **Mac**: treated as hybrid. In addition to the cloud providers, Mac users
  can run models locally through Ollama and route between local and cloud
  in the same application — see `projects/05-hybrid-orchestrator`.

This keeps Windows setup simple (no GPU driver / local-runtime debugging
required) while still letting Mac users exploit local inference for
cost-free experimentation, offline development, or privacy-sensitive testing.

## What this means in practice

- If you're on Windows, ignore the Ollama-related sections of `models/ollama/`
  and `projects/05-hybrid-orchestrator` — everything else works identically.
- If you're on Mac and want local models, install Ollama separately
  (`scripts/setup-mac.sh` offers to install it) and see
  `models/ollama/README.md` for how to point the platform at it.
- `cockpit/config/settings.py`'s `ollama_host` setting defaults to
  `http://localhost:11434` on both platforms — it's simply never called from
  Windows code paths.
- No code in `cockpit/` hard-codes an OS check; the split is enforced by
  which examples you choose to run, not by a runtime branch. This keeps the
  shared framework code identical across platforms.

## Adding a new hybrid example

If you're extending the repo with a new example that wants local-model
support, follow `projects/05-hybrid-orchestrator`'s pattern: keep the local
and cloud code paths in separate modules (`local_ollama.py`,
`cloud_gemini.py`) behind a small router, so Windows users can simply never
import the local module.
