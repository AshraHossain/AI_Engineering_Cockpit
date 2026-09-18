# 16 - Production Agent with Observability

A Claude tool-use agent run the way you would run it in production: every
request traced into Arize Phoenix, alerts on loops and failures, and a new
model version rolled out as a canary that is promoted only when its metrics
hold **and** two people approve, then rolled back automatically if it
degrades afterwards.

The agent answers order-support questions with three read-only tools
(`lookup_order`, `track_shipment`, `refund_policy`) over fixed, simulated data.
Stable is `claude-opus-5`; the canary is `claude-sonnet-5`, sent identical
requests apart from the model.

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
cost at real prices; nothing is billed.

| Scenario | What happens |
| --- | --- |
| `bad-canary` | The canary keeps re-checking a shipment that never moves. The loop guard stops the run on the third identical call, `loop_detected` fires, and the canary is aborted. |
| `late-regression` | The canary is healthy for 30 runs, two scripted approvers sign off, and it is promoted. Then traffic starts including bare legacy order numbers the canary never saw; it fails to normalize them, `tool_error_rate` fires during the watch, and production rolls back to `v1`. |

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

## What each piece does

| Mechanism | Where | Notes |
| --- | --- | --- |
| Tracing | `src/tracing.py`, `src/agent.py` | Spans created by hand with OpenInference attribute names; question and answer text pass through the cockpit's PII masking first |
| Loop guard | `src/alerts.py` `LoopGuard` | Third identical tool call, or fifth call to one tool, stops the run before those tools execute; `max_iterations=8` is the backstop |
| Alerts | `src/alerts.py` `AlertMonitor` | Five rules over the last 20 runs per version; fire once, resolve once; logged, appended to `outputs/alerts.jsonl`, and attached to the span |
| Canary | `src/canary.py` | Hash routing, a judge comparing both versions, promotion through the cockpit `ModelRegistry` after two `ApprovalWorkflow` approvals, automatic rollback during a 30-run watch |
| Dashboards | `cockpit.monitoring` | Per-version run, model and tool latency plus cost, printed at the end of every run |

## Design choices

- **The model call is faked at the HTTP layer only.** `src/fake_model.py`
  answers through `httpx2.MockTransport`, so the SDK's real Tool Runner runs in
  every test and dry run. No SDK internals are mocked.
- **Refusal fallbacks are off.** They would answer some requests with a
  different model and blur which model each version's metrics describe.
  Refusals are an outcome, counted as failures.
- **Promotion needs people; rollback doesn't.** A rollback restores a version
  that was already approved, and it has to work at 3am.
- **Everything is project-local.** Tracing, alerts and the canary live here, not
  in `cockpit/`, until a second project needs them.
