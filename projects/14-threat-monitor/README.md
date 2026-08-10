# 14 — Threat Monitor

Turns an audit event stream into ranked, investigable findings using
`cockpit.security.threat_detection`. Reads either a saved JSONL stream or a
live `AuditLog` hash chain.

## What the detectors look for

| Detector | Signal |
|---|---|
| **Brute force** | Repeated authentication failures for one actor inside a window |
| **Data exfiltration** | One actor reading or exporting far more than their norm |
| **Privilege escalation** | Actions attempted beyond an actor's role, or a role change followed immediately by sensitive access |
| **Anomalous rate** | Request volume well above that actor's own baseline |

## Every finding carries its evidence

A finding names the specific events that triggered it. An alert you cannot
investigate is noise — you cannot action "suspicious activity detected"
without knowing which requests, when, and by whom. When evidence is long
the middle is elided, and the marker tells you how to see the rest
(`--evidence 0`).

## These are heuristics, and thresholds need tuning

Behavioral detection trades false positives against misses, and the right
balance depends on your traffic. The defaults here suit the bundled sample
stream, not your production. **Tune them.**

Because the false-positive direction matters as much as the true-positive
one, the bundled stream contains substantial benign traffic, and one test
asserts a benign-only stream produces **zero** findings. A detector that
fires on everything is as useless as one that fires on nothing.

## Run it

```bash
uv run python src/main.py --dry-run
```

No API key, no network.

| Flag | Effect |
|---|---|
| `--actor` | Scope to one principal |
| `--category` | Scope to one threat category |
| `--since` | Only events after a timestamp |
| `--evidence` | Max evidence lines per finding; `0` shows all |
| `--fail-at` | Exit non-zero at or above a severity |
| `--events` / `--chain` | Read a JSONL stream, or an audit-log chain |

Scoping with `--actor` narrows the whole report, including the actor
roster — a report claiming to be about one principal should not enumerate
who else was in the stream.

## Tests

```bash
uv run pytest
```

56 tests, deterministic (all timestamps injected, nothing sleeps). Each
detector is tested in four directions: it fires above threshold, does *not*
fire below it, respects the time window, and its finding carries the events
that triggered it.
