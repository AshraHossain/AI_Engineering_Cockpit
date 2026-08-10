# 13 — Compliance Report

Runs the GDPR, HIPAA, and SOX controls in
`cockpit.security.compliance` over a dataset and renders the findings.

## This does not certify compliance

It reports **findings and evidence**, and it deliberately emits no verdict.
There is no pass/fail, no score, and no boolean anywhere in the output —
there is a test asserting that property, mirroring the one in the framework
itself.

The reasoning: software can evidence that specific automated checks ran and
what they found. Compliance is a legal and organizational determination
made by counsel, a privacy officer, or an auditor, working from context a
program cannot see. A tool that printed "COMPLIANT: TRUE" would eventually
be shown to an auditor, and that is a genuinely harmful outcome.

Every rendered report reproduces the framework's `DISCLAIMER` and lists
**controls that did not run**. A report generated from missing inputs must
not be mistakable for a clean one — absence of findings is not evidence of
compliance, and the report says so in those words.

`--fail-on` exists for pipeline use. The exit code reflects *an operator's
chosen threshold*, not a compliance judgment. That distinction is in the
report text too.

## What it checks

- **GDPR** — subject access export, erasure (with legal-hold and retention
  exceptions surfaced, never silently skipped), portability, consent
  validity, retention windows.
- **HIPAA** — PHI in fields not designated to hold it, and a
  minimum-necessary check against a declared purpose.
- **SOX** — segregation of duties (same principal requesting and approving
  a change) and change-audit completeness.

## Run it

```bash
uv run python src/main.py --dry-run
```

No API key, no network.

| Flag | Effect |
|---|---|
| `--framework` | Scope to `gdpr`, `hipaa`, or `sox` |
| `--data` | Point at your own dataset |
| `--subject-export` | Export one data subject's records |
| `--fail-on` | Exit non-zero at or above a severity (operator policy) |
| `--omit` | Withhold findings, print coverage only |

## The dataset is synthetic

`data/records.json` is fabricated. It deliberately contains a record past
its retention window, one under legal hold, PHI in the wrong field, and a
self-approved change, so every control has something to find. All PII is
obviously fake — `example.com` addresses, `555-01xx` numbers, and
placeholder identifiers.

## Tests

```bash
uv run pytest
```

54 tests, offline and deterministic (timestamps injected, so a retention
test cannot go flaky as the clock moves). Subject ids include `subj-1` and
`subj-10` specifically so a substring-matching bug in the export — which
would be a real data breach — gets caught.
