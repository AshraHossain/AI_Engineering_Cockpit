# {{PROJECT_NAME}}

{{DESCRIPTION}}

## Quick start

```bash
uv sync --all-groups
cp .env.example .env      # then fill in your keys
uv run python -m {{PACKAGE_NAME}}.main --dry-run
```

`--dry-run` needs no credentials, so the project is demonstrable before
anyone has a key.

## Development

```bash
make check     # everything CI runs: lint, types, security, tests
make test      # tests with coverage
make format    # black + ruff --fix
```

`make check` runs the same commands in the same order as
`.github/workflows/ci.yml`. Keep them in sync — a local check that passes
while CI fails is worse than no local check at all.

## Layout

```
src/{{PACKAGE_NAME}}/   application code
tests/                  tests -- offline, deterministic, no API key
pyproject.toml          deps AND all tool config (see note below)
```

## Conventions this starts you with

- **Tool config lives in `pyproject.toml`**, not a `config/` directory.
  pytest, ruff, and mypy only auto-discover config at the project root;
  moving it elsewhere means every invocation needs explicit flags, and the
  day one gets forgotten the tool runs with different settings than CI.
- **Provider clients are injected, never constructed inside the logic.**
  That is what lets the tests run with no API key, no network, and no
  flakiness.
- **`black` and `ruff` share one line length.** They fight forever if not.
- **`.env.example` is committed; `.env` is not.**
- **Coverage starts at 70%.** Raise it as the project earns it — a gate you
  cannot meet gets deleted, a gate slightly above where you are gets met.

## Reusing the cockpit frameworks

Rather than reimplementing PII masking, prompt-injection detection, cost
tracking, RBAC, or audit logging, see
[AI_Engineering_Cockpit](https://github.com/AshraHossain/AI_Engineering_Cockpit).
