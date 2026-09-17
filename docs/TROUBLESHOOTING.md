# Troubleshooting

Common setup issues and their fixes, roughly in the order you're likely to
hit them.

## `uv` not found

**Symptom:** `uv: command not found` (Mac) or `uv is not recognized as an
internal or external command` (Windows).

**Fix:**

- Re-run the setup script (`scripts/setup-windows.ps1` or
  `scripts/setup-mac.sh`) — it installs `uv` automatically if missing.
- If you installed `uv` manually, make sure its install directory is on your
  `PATH` (typically `~/.local/bin` on Mac/Linux, `%USERPROFILE%\.local\bin`
  on Windows), then open a new terminal so the updated `PATH` takes effect.
- Verify with `uv --version`.

## Missing API key errors

**Symptom:** An example fails with something like `GEMINI_API_KEY not set`
or an authentication error from the provider SDK.

**Fix:**

1. Confirm `.env` exists at the repo root (copy `.env.example` to `.env` if
   not — the setup scripts do this automatically).
2. Open `.env` and make sure the relevant key (`GEMINI_API_KEY`,
   `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) is filled in with no surrounding
   quotes or trailing spaces.
3. Check which key a given example actually needs — see that project's own
   `README.md` (`projects/0*-*/README.md`); you don't need every key filled
   in, only the ones the example you're running calls.
4. `cockpit/config/settings.py` loads `.env` via `python-dotenv` at import
   time — if you edited `.env` while a Python process was already running
   (e.g. a long-lived REPL), restart it so the new value is picked up.

## Virtual environment (venv) issues

**Symptom:** `ModuleNotFoundError` for a dependency you're sure is installed,
or commands silently using the wrong Python.

**Fix:**

- Always run project code through `uv run ...` rather than a bare `python
  ...` — `uv run` guarantees you're using the project's own `.venv` with the
  locked dependencies, not your system Python.
- Each `projects/0*-*/` directory has its **own** `pyproject.toml` and its
  own isolated environment. Running `uv sync` at the repo root does **not**
  install a project's example-specific dependencies — `cd` into the project
  directory first and run `uv sync` there too.
- If a venv seems corrupted, delete `.venv` in the affected directory and
  re-run `uv sync` — `uv` will rebuild it from the lockfile.

## Tests fail immediately on a fresh clone

**Symptom:** `uv run pytest` fails before running any of your own code.

**Fix:**

- Run pytest as `uv run pytest -c config/pytest.ini --rootdir=.` — `pytest.ini`
  lives under `config/`, not the repo root, so pytest won't auto-discover it.
  Without `-c`, `testpaths` and the coverage settings are silently skipped and
  bare `pytest` may try to collect (and fail on) `projects/*/tests`, which
  need their own project-local venv. `--rootdir=.` keeps `testpaths` resolved
  against the repo root instead of `config/`. `scripts/run-tests.sh`, the
  Makefile, and CI already pass both flags — copy that invocation.
- Make sure you ran `uv sync --all-groups` at the repo root first (the setup
  scripts do this) — the `dev` dependency group includes `pytest` itself.
- `tests/unit/test_cockpit.py` only exercises `cockpit/config/*` and needs
  no API keys or network access — if it fails, it's a real cockpit/config
  issue, not a missing-key issue. Re-read the specific assertion that failed.
- `tests/e2e/test_examples.py` only checks that each `projects/0*-*/`
  directory has the expected `pyproject.toml` and `src/` layout; it does not
  run the projects themselves, so it should never fail due to a missing API
  key.

## `cryptography` fails to build on an Intel Mac

**Symptom:** `uv sync` stops at `Building cryptography==50.0.0` and reports
`Failed to build`, usually with a linker error mentioning `xcrun` or
`MacOSX.sdk`.

**Why:** from 49.0.0 onward, `cryptography` publishes no prebuilt wheels for
Intel macOS, so `uv` compiles it from source. That needs a Rust toolchain and a
working macOS SDK. Apple Silicon Macs, Linux and Windows download a prebuilt
wheel and never reach this step.

**Fix:**

- Install Rust if it is missing: <https://rustup.rs>.
- If the link step fails with `xcrun: error: unable to lookup item 'Path' in
  SDK 'macosx'`, or a `dlopen` error inside `CoreDevice.framework`, the selected
  Xcode is broken — common after a macOS update. Build against the standalone
  Command Line Tools for this one command, without changing any system setting:

  ```bash
  DEVELOPER_DIR=/Library/Developer/CommandLineTools uv sync --all-groups
  ```

  To repair Xcode itself, run `xcodebuild -runFirstLaunch`, or reinstall Xcode.
- **Do not** cap `cryptography` below 49 to get a prebuilt wheel. At the time of
  writing (September 2026) 48.0.0 has seven published vulnerabilities, and
  `cockpit/security/data_security.py` depends on it for encryption.

## PowerShell script execution blocked (Windows)

**Symptom:** Running `scripts\setup-windows.ps1` directly errors with
"running scripts is disabled on this system."

**Fix:** Use the execution-policy bypass flag for this one invocation rather
than changing your system-wide policy:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
```

## Still stuck?

- Re-read [GETTING_STARTED.md](GETTING_STARTED.md) end to end — most issues
  come from skipping a step.
- Check the specific project's own `README.md` under `projects/0*-*/` for
  example-specific caveats.
- Open an issue using the templates under `.github/ISSUE_TEMPLATE/`.
