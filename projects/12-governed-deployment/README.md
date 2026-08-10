# 12 — Governed Deployment

A model deployment pipeline you cannot shortcut. Ties together
`cockpit.governance.model_versioning`, `approval_workflow`, and
`audit_trail`.

## The controls, and why each exists

| Control | What it stops |
|---|---|
| **Explicit transition map** | Shipping straight from a developer's branch to live traffic. `development -> production` is simply not in `LEGAL_TRANSITIONS`; reaching production requires passing through staging. |
| **Approval gate** | An unreviewed production promotion. Production requires an *approved* request. |
| **No self-approval** | One person satisfying their own gate. The requester cannot approve their own request — an approval process a single person can complete alone is theatre. |
| **One approver, one vote** | Defeating a 2-of-M threshold by approving twice. Deduplication is by principal, not by call. |
| **One version in production** | Ambiguity about what is actually serving. Promoting a new version demotes the incumbent, atomically. |

Rejection is terminal: a rejected request cannot be walked back to approved
by collecting more signatures afterwards.

## Run it

```bash
uv run python src/main.py --dry-run
```

No API key and no network — this project makes no model calls. The
scenario is scripted with injected timestamps, so the transcript is
identical every run.

The transcript deliberately shows the **refusals**, not just the happy
path: a straight-to-production attempt, an unapproved promotion, and a
self-approval, each blocked with the reason. A pipeline demo where nothing
is ever refused demonstrates nothing.

## Tests

```bash
uv run pytest
```

21 tests. Each control is asserted from both directions — the allowed path
succeeds and the forbidden path raises the specific error. Tests use their
own `ModelRegistry` and `ApprovalWorkflow` instances rather than the
module-level defaults, so state cannot leak between them.

## Note

The registry and workflow write to the module-level default governance
trail automatically. If you build on this, construct your own
`GovernanceTrail` when you need isolation.
