# Deployment

This repo is an MVP / learning platform, not a production deployment
framework — `projects/` examples are meant to be read, understood, and
*extracted* into your own production codebase rather than deployed as-is
from this repo. This doc is realistic guidance for that extraction, not a
one-click deploy story.

## Before you deploy anything

1. **Pick one project, not the whole repo.** A `projects/0*-*/` directory is
   self-contained (its own `pyproject.toml`, its own dependencies). Deploy
   that directory's code, not the monorepo.
2. **Re-pin dependencies for production.** Run `uv lock` inside the project
   directory to get a fresh `uv.lock`, and build/deploy from that lockfile
   (`uv sync --frozen`) so production installs exactly what you tested.
3. **Decide what "production" means for you.** A container image, a
   serverless function, or a long-running process each have different
   requirements below — pick the section that matches your target.

## Environment and secrets

- Never ship `.env` or commit real API keys — `.gitignore` already excludes
  `.env`; keep it that way.
- In production, set `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`
  (and `COCKPIT_USE_CASE`, `LOG_LEVEL` if you use them) as real environment
  variables injected by your platform:
  - **Container platforms** (Docker/Kubernetes, Fly.io, Render, Railway):
    use the platform's secret/env-var store, not a baked-in `.env` file.
  - **Serverless** (AWS Lambda, Google Cloud Functions, Vercel Functions):
    use the provider's secrets manager (AWS Secrets Manager, GCP Secret
    Manager, etc.) and inject at cold-start, not at build time.
  - **Plain VM/process**: use your init system's environment file
    (e.g. systemd `EnvironmentFile=`) with restricted file permissions —
    still not a copy of the repo's `.env.example` pattern.
- `cockpit/config/settings.py` reads keys via `os.getenv` through
  `get_settings()` — as long as the environment variables are present at
  process start, no code changes are needed to move from local `.env` to a
  real secrets manager.

## Containerizing a project

A minimal pattern for one `projects/0*-*/` directory:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev
COPY src/ ./src/
CMD ["uv", "run", "python", "src/main.py"]
```

Notes:
- `--no-dev` skips `pytest`/`black`/`ruff`/etc. — you don't need dev tooling
  in the production image.
- `--frozen` refuses to update the lockfile at build time, so the image is
  reproducible.
- Build a separate image per project you deploy; don't try to containerize
  the whole monorepo.

## Logging and error handling

- Every module in `cockpit/` and each project uses Python's `logging`
  module (never bare `print()`). Point `LOG_LEVEL` at `INFO` or `WARNING` in
  production; use `DEBUG` only when actively investigating an issue.
- Route logs to your platform's standard collector (stdout is usually
  correct for containers/serverless — most log aggregators tail stdout).

## Cost and rate limits

- Set a real budget alert with your model provider(s) directly — this repo's
  `cockpit/monitoring/` (once enabled) can help you observe cost, but it is
  not a substitute for provider-side spend limits.
- Respect each provider's rate limits; `cockpit/utils/rate_limiting.py`
  (once built out) is a starting point, not a guarantee — load-test before
  trusting it in production.

## What this repo does *not* give you

Being honest about the gap between MVP and production:

- No built-in autoscaling, blue/green deploys, or CI/CD deploy pipeline —
  `.github/workflows/` here only runs tests and lint/security scanning, not
  deployment.
- Secret *storage* and key rotation helpers exist in
  `cockpit/security/data_security.py`, but there is no integration with a
  managed KMS — wire one up rather than holding long-lived keys in process.
- No multi-region or high-availability guidance — add it yourself based on
  your actual traffic and uptime requirements.
- **Every framework is implemented, but implemented is not the same as
  sufficient for your obligations.** Two limits are documented rather than
  hidden, and you should read both before relying on them:
  - The prompt-injection filter catches 24 of 32 corpus payloads and is
    structurally blind to attacks assembled across turns. See
    [SECURITY_COVERAGE.md](SECURITY_COVERAGE.md).
  - `cockpit/security/compliance.py` reports findings and evidence and
    deliberately emits no compliance verdict. It cannot tell you that you
    are GDPR/HIPAA/SOX compliant, because that is a legal determination
    software does not get to make.
- The audit log is tamper-**evident** (a hash chain that reveals
  modification), not tamper-proof. For a real control, anchor the head hash
  somewhere the application cannot rewrite.

## Checklist before going live

- [ ] Re-locked dependencies for the specific project you're shipping
- [ ] Secrets injected via your platform's secret store, not `.env`
- [ ] `LOG_LEVEL` set appropriately (not `DEBUG`)
- [ ] Provider-side spend/rate limits configured
- [ ] You've read the actual code of any `cockpit/` framework you're
      relying on for security/compliance — don't trust the flag name alone
