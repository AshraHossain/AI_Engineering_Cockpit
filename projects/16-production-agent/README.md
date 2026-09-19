# 16 - Production Agent with Observability

A Claude tool-use agent run the way you would run it in production: every
request traced into Arize Phoenix, alerts on loops and failures, and a new
model version rolled out as a canary that is promoted only when its metrics
hold **and** two people approve, then rolled back automatically if it
degrades afterwards.

The agent answers order-support questions with three read-only tools
(`lookup_order`, `track_shipment`, `refund_policy`). They read built-in sample
data by default, or your own support API when you point them at one. Stable is
`claude-opus-5`; the canary is `claude-sonnet-5`, sent identical requests apart
from the model.

Design: [`docs/superpowers/specs/2026-09-17-production-agent-observability-design.md`](../../docs/superpowers/specs/2026-09-17-production-agent-observability-design.md).

## Run it

```bash
uv sync --all-groups
uv run pytest -v                                        # offline, no API key
uv run python src/main.py --dry-run --scenario bad-canary
uv run python src/main.py --dry-run --scenario late-regression
```

The dry runs use scripted models on a fake clock, so the output is the same
every time. Their dollar figures are what those scripted token counts would
cost at real prices; nothing is billed. Dry runs always use the sample data,
even when a support API is configured.

| Scenario | What happens |
| --- | --- |
| `bad-canary` | The canary keeps re-checking a shipment that never moves. The loop guard stops the run on the third identical call, `loop_detected` fires, and the canary is aborted. |
| `late-regression` | The canary is healthy for 30 runs, two scripted approvers sign off, and it is promoted. Then traffic starts including bare legacy order numbers the canary never saw; it fails to normalize them, `tool_error_rate` fires during the watch, and production rolls back to `v1`. |

Each run prints the cockpit dashboard (latency and cost per version, model and
tool), the rollout timeline, which version ended up in production, and whether
the governance trail verifies.

### See the traces in Phoenix

```bash
docker run -p 6006:6006 -p 4317:4317 -i -t arizephoenix/phoenix:<version>
uv run python src/main.py --dry-run --scenario late-regression --phoenix
```

Pin `<version>` to a current Phoenix release rather than `latest`. Open
<http://localhost:6006>: traces land in the `16-production-agent` project, one
`agent.run` per request with its `llm.call` and `tool.*` spans, alerts and
rollout decisions as span events. Set `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` to
send them somewhere else.

### Live mode

```bash
cp .env.example .env    # then add ANTHROPIC_API_KEY
uv run python src/main.py --requests 60 --canary-percent 50 --phoenix
```

This calls the real API and **costs money**: a stable run is three model turns,
roughly $0.03-0.06 on Opus 5. The canary needs 30 runs before it can be
promoted, so use a high `--canary-percent` for a short demo. When it is ready,
the CLI prompts for two approvers at the terminal.

| Option | Default | Meaning |
| --- | --- | --- |
| `--requests N` | 20 | Requests to send, cycling through the questions |
| `--canary-percent P` | 10 | Share of requests routed to the canary while it is on trial |
| `--questions FILE` | built-in samples | Customer questions, one per line; blank lines are skipped |
| `--phoenix` | off | Export traces to Phoenix |

## Connect your support API

Set `SUPPORT_API_URL` (and `SUPPORT_API_TOKEN`, if it needs one) in `.env`,
then give live mode questions about orders your API actually has:

```bash
uv run python src/main.py --requests 60 --canary-percent 50 --questions questions.txt --phoenix
```

The CLI prints `Tools: support API at <url>` so you can tell which backend a run
used. Without `--questions`, it warns you: the built-in questions name sample
orders (`ORD-10042` and so on), so every lookup would fail, `tool_error_rate`
would fire, and the canary would be aborted for a reason that has nothing to
do with the model.

### The contract

The tools send `GET` requests with `Accept: application/json` and, when a token
is set, `Authorization: Bearer <token>`. Paths are appended to the base URL, so
`https://support.example.com/v1` works.

| Tool | Request | Response fields |
| --- | --- | --- |
| `lookup_order` | `GET {base}/orders/{order_id}` | `item`, `status`, `tracking_id` (string or null), `category` |
| `track_shipment` | `GET {base}/shipments/{tracking_id}` | `status`, `last_scan` |
| `refund_policy` | `GET {base}/refund-policies/{category}` | `policy` |

Each field must be a string or null (`tracking_id` is null until an order
ships). An unknown ID returns 404. If your services don't look like this (say orders come from your
shop and shipments from a carrier), put a thin gateway in front of them that
does. Changing the tools instead would change the tool schemas both versions
are compared on.

### What the tools do with the answer

| The API... | The tool... | The model sees |
| --- | --- | --- |
| answers 200 with the contract fields | passes on **only** those fields; anything extra (customer email, internal notes) is dropped | the order, shipment or policy |
| answers 404 | reports an unknown ID, exactly as with the sample data | `No order with ID ORD-123.` |
| times out, drops the connection, or answers 429 or 5xx | retries up to 3 attempts in all, waiting 0.25s and then 0.5s, with a 5s timeout per attempt | `The orders service is unavailable right now (HTTP 503).` if all three fail |
| answers another 4xx (401, 403, 400) | fails at once without retrying, since this is a configuration problem, and logs an error | `The orders service rejected the request (HTTP 401).` |
| answers 200 with a missing field, a wrong type, or no JSON at all | refuses the answer and logs the field names it got, never the values | `The shipments service returned an unexpected response.` |

The ID in the path comes from the model, so it is URL-encoded as a single path
segment: `ORD-../admin` is requested as `/orders/ORD-..%2Fadmin`, not
`/admin`.

## Watch the alerts

Every alert transition (fired or resolved) is logged, attached to the run's
span, and appended to `outputs/alerts.jsonl`, one JSON object per line:

```json
{"timestamp": 1767227347.6, "version": "v2", "rule": "tool_error_rate", "state": "fired", "value": 0.333, "threshold": 0.3, "request_id": "req-0338"}
```

`timestamp` is Unix epoch seconds. The file is appended to across runs, so
delete it to start fresh.

### Dashboard

```bash
uv run python -m http.server 8000       # from this directory
# open http://localhost:8000/alerts-dashboard.html
```

The page shows what is still firing (the latest transition for each version
and rule) and every transition in the order it was written. It reads
`outputs/alerts.jsonl` when served like this. Opened straight from disk, it
can't fetch the file, so use its file picker instead. Reload the page to see
new alerts.

### Slack

Create an [incoming webhook](https://api.slack.com/messaging/webhooks), put its
URL in `.env` as `SLACK_WEBHOOK_URL`, and run:

```bash
uv run python src/slack_alerts.py                     # posts what's new, then exits
watch -n 60 uv run python src/slack_alerts.py         # during a live rollout
```

Each new line becomes one message, for example:

```text
:rotating_light: *tool_error_rate* fired for `v2`: 0.333 (threshold 0.3), at req-0338, 2026-01-01 00:29:07 UTC
```

Posted lines are recorded in `outputs/alerts.jsonl.posted`, so re-running sends
only what is new. Posting stops at the first failure, so a `resolved` never
reaches Slack before its `fired`, and the next run picks up from there. The
script exits 1 when a post fails and 2 when no webhook is set, so a scheduler
can notice.

## What each piece does

| Mechanism | Where | Notes |
| --- | --- | --- |
| Tools | `src/tools.py` | Sample data by default; `use_support_api` switches to HTTP with retries and contract checks. Only `main.py` switches it, in live mode |
| Tracing | `src/tracing.py`, `src/agent.py` | Spans created by hand with OpenInference attribute names; question and answer text pass through the cockpit's PII masking first |
| Loop guard | `src/alerts.py` `LoopGuard` | Third identical tool call, or fifth call to one tool, stops the run before those tools execute; `max_iterations=8` is the backstop |
| Alerts | `src/alerts.py` `AlertMonitor` | Five rules over the last 20 runs per version; fire once, resolve once; logged, appended to `outputs/alerts.jsonl`, and attached to the span |
| Canary | `src/canary.py` | Hash routing, a judge comparing both versions, promotion through the cockpit `ModelRegistry` after two `ApprovalWorkflow` approvals, automatic rollback during a 30-run watch |
| Dashboards | `cockpit.monitoring`, `alerts-dashboard.html` | Per-version run, model and tool latency plus cost at the end of every run; alert history in the browser |
| Notifications | `src/slack_alerts.py` | New alert transitions to a Slack webhook, in order, each exactly once |

The five alert rules:

| Rule | Fires when | Needs at least |
| --- | --- | --- |
| `loop_detected` | any run in the window ended in a loop | 1 run |
| `failure_rate` | more than 20% of runs did not end `ok` | 10 runs |
| `tool_error_rate` | more than 30% of tool calls raised | 10 runs |
| `p95_latency` | 95th-percentile run time above 30s | 10 runs |
| `cost_per_run` | mean cost above $0.15 | 10 runs |

## Design choices

- **The model call is faked at the HTTP layer only.** `src/fake_model.py`
  answers through `httpx2.MockTransport`, so the SDK's real Tool Runner runs in
  every test and dry run. No SDK internals are mocked. The support-API tests
  fake the service the same way.
- **The backend is chosen once, at the entry point.** `tools.py` never reads
  the environment. That is how dry runs stay on the sample data even with
  `SUPPORT_API_URL` set.
- **Refusal fallbacks are off.** They would answer some requests with a
  different model and blur which model each version's metrics describe.
  Refusals are an outcome, counted as failures.
- **Promotion needs people; rollback doesn't.** A rollback restores a version
  that was already approved, and it has to work at 3am.
- **Everything is project-local.** Tracing, alerts and the canary live here, not
  in `cockpit/`, until a second project needs them.

## Known limitations

- **A support-API outage counts against the model.** `tool_error_rate` does not
  tell a tool the model misused apart from a service that was down. An outage
  during the canary can abort a healthy canary, and one during the watch can
  roll it back. The spans say which it was (look for `service is unavailable`
  errors). Separating the two needs a second error kind and its own alert.
- **The dashboard doesn't refresh itself.** Reload it.
- **Each tool call opens its own HTTP connection.** A run makes only a few
  calls; share one client if tool latency starts to matter.
