# Ollama Models (Mac Hybrid Mode)

This directory holds example [Ollama](https://ollama.com) assets for Mac
users running the platform in hybrid mode (local models + cloud APIs). See
[docs/WINDOWS_VS_MAC.md](../../docs/WINDOWS_VS_MAC.md) for why this is
Mac-only.

## Prerequisites

1. Install Ollama: https://ollama.com/download (or `brew install ollama` on
   Mac).
2. Start the Ollama server (usually automatic after install, or run
   `ollama serve` manually).
3. Pull a base model, e.g.:

   ```bash
   ollama pull llama3
   ```

## Using the example Modelfile

The `Modelfile` in this directory builds a small customization on top of
`llama3` with a project-specific system prompt:

```bash
ollama create cockpit-assistant -f models/ollama/Modelfile
ollama run cockpit-assistant
```

Test it directly from the CLI first — if `ollama run cockpit-assistant`
responds, the model is ready to be called from Python.

## Calling it from Python

```python
import requests

from cockpit.config.settings import get_settings

settings = get_settings()

response = requests.post(
    f"{settings.ollama_host}/api/generate",
    json={"model": "cockpit-assistant", "prompt": "Explain RAG in one sentence.", "stream": False},
    timeout=30,
)
response.raise_for_status()
print(response.json()["response"])
```

`settings.ollama_host` defaults to `http://localhost:11434` and is read from
the `OLLAMA_HOST` environment variable (see `.env.example`).

## See also

- `projects/05-hybrid-orchestrator` — full example routing between this
  local model and a cloud provider.
- `models/registry.json` — tracks this and other available models across
  providers.
- Ollama's own model library for other base models to try:
  https://ollama.com/library
