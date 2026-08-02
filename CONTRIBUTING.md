# Contributing to AI_Engineering_Cockpit

Thanks for your interest in contributing. This project is an AIDevOps
platform combining testing, evaluation, red teaming, security, monitoring,
and governance frameworks for AI systems — contributions across any of those
areas are welcome, as are example projects, docs, and tooling improvements.

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Getting Started

1. Fork the repository and clone your fork.
2. Run the setup script for your platform:
   - Windows (PowerShell): `powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1`
   - macOS (Bash): `bash scripts/setup-mac.sh`
3. Copy `.env.example` to `.env` and fill in any API keys you need for the
   area you're working on (most contributions don't require any).
4. See [SETUP.md](SETUP.md) for detailed platform-specific instructions and
   troubleshooting.

## Development Workflow

This project uses [uv](https://docs.astral.sh/uv/) for dependency management
and Python 3.11+.

```bash
uv sync --all-groups                             # install root deps + dev tools
uv run pytest -c config/pytest.ini --rootdir=.   # run the root test suite
bash scripts/run-tests.sh                         # run root + every projects/*/ suite with coverage
bash scripts/format-code.sh                       # black + ruff --fix
```

### Code style

- Formatting: [black](https://black.readthedocs.io/) (line length 100)
- Linting: [ruff](https://docs.astral.sh/ruff/) — config at `config/ruff.toml`
- Type checking: [mypy](https://mypy.readthedocs.io/) — config at `config/mypy.ini`
- Security: [bandit](https://bandit.readthedocs.io/) — run via `uv run bandit -r cockpit -ll`

Run everything at once with pre-commit:

```bash
uv run pre-commit install -c config/.pre-commit-config.yaml
uv run pre-commit run --all-files -c config/.pre-commit-config.yaml
```

### Code quality expectations

- No `print()` statements — use the `logging` module.
- Public functions/classes have docstrings (Args, Returns, Raises).
- Type hints on all function signatures.
- Specific exception handling (`except SomeError`, not bare `except:`).
- No hardcoded secrets — use environment variables via `.env` (see
  `.env.example`), never commit real keys.
- New code should include tests; aim for the project's 80% coverage floor.

## Adding a New Example Project

Use the scaffolding script rather than copying an existing project by hand:

```bash
bash scripts/new-project.sh <project-name>
# e.g. bash scripts/new-project.sh function-calling
```

This creates `projects/NN-name/` with an isolated `pyproject.toml`,
`src/main.py`, `tests/`, `README.md`, and `.env.example`, following the same
pattern as `projects/01-hello-world` etc. Fill in the TODOs it generates.

## Working on the Core Platform (`cockpit/`)

The `cockpit/` package is organized by framework: `testing/`,
`evaluation/`, `red_teaming/`, `security/`, `monitoring/`, `governance/`,
`config/`, and `utils/`. Frameworks are toggled independently via
`cockpit/config/feature_flags.py` — check whether the module you're touching
is behind a flag, and keep new functionality flag-gated if it's experimental
or tier-2/3 scoped.

## Submitting Changes

1. Create a branch: `git checkout -b feat/short-description` (or `fix/`,
   `docs/`, `chore/` as appropriate).
2. Make your changes with clear, focused commits. We follow
   [Conventional Commits](https://www.conventionalcommits.org/)
   (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).
3. Run the full check locally before pushing:
   ```bash
   bash scripts/format-code.sh
   bash scripts/run-tests.sh
   uv run mypy --config-file config/mypy.ini cockpit
   uv run bandit -r cockpit -ll
   ```
4. Push and open a pull request against `main`, filling in the
   [PR template](.github/PULL_REQUEST_TEMPLATE/pull_request_template.md).
5. CI (`.github/workflows/test.yml`, `security.yml`, `docs.yml`) must pass
   before merge.

## Reporting Bugs / Requesting Features

Use the issue templates:

- [Bug report](.github/ISSUE_TEMPLATE/bug_report.md)
- [Feature request](.github/ISSUE_TEMPLATE/feature_request.md)
- [Question](.github/ISSUE_TEMPLATE/question.md)

## Reporting Security Issues

Do **not** open a public issue. See [SECURITY.md](SECURITY.md) for the
private disclosure process.

## License

By contributing, you agree that your contributions will be licensed under
the project's [MIT License](LICENSE).
