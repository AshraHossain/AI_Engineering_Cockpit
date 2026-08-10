# 11 — Secure Gateway

A policy-enforcing gateway between a caller and a model. Every request runs
the same pipeline, and **each stage can refuse**:

1. **Authorize** — the calling `Principal` must hold `Permission.MODEL_INVOKE`
   (`cockpit.security.access_control`). An `external` principal stops here.
2. **Validate input** — `validate_input` from `cockpit.security.input_security`.
   A prompt-injection hit is refused, or merely flagged with `--flag-only`.
3. **Invoke** — the model call, injected as a `generate_fn` so tests and
   `--dry-run` need no API key.
4. **Sanitize output** — `mask_pii` from `cockpit.security.output_security`
   runs before the response is returned *or logged*.
5. **Audit** — every request lands in the tamper-evident hash chain from
   `cockpit.security.audit_logging`.

## The point

**Refusals are audited too.** A gateway that only records what it allowed
tells you nothing about what someone tried. The denied attempts are the
interesting ones, and the test suite asserts a refused request still
produces an audit record — and that it never reached the model.

API keys are held in a `Secret` (`cockpit.security.data_security`), which
redacts through every string-formatting path, so a stray log line or
traceback cannot leak one.

## Run it

```bash
uv run python src/main.py --dry-run
```

No API key needed for `--dry-run`. For a live call, set `GEMINI_API_KEY`
in `.env` and drop the flag.

Useful flags:

| Flag | Effect |
|---|---|
| `--role` | Caller's role: `external`, `viewer`, `developer`, `admin` |
| `--prompt` | The prompt to send |
| `--flag-only` | Flag suspicious input instead of refusing it |
| `--audit-file` | Persist the audit chain to disk |
| `--principal-id` | Identity recorded in the audit trail |

Try `--role external` to watch authorization refuse before the model is
ever contacted.

## Tests

```bash
uv run pytest
```

26 tests, no network and no API key. The load-bearing ones assert that a
refused request never reaches the injected `generate_fn`, that PII is
masked before the response is returned, and that the audit chain still
verifies after a mixed run of allowed and refused requests.
