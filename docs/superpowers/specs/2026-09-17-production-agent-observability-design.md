# 16 — Production Agent with Observability

**Date:** 2026-09-17 · **Status:** design approved; not built · **Project:** `projects/16-production-agent/`

## Purpose

Projects 08 and 09 put numbers on model calls and on an agent loop. Neither
answers the question a production team actually asks: *when a new version is
worse, how fast do we find out, and does traffic get back to a good version
before a user notices?*

Project 16 answers it with one tool-using agent and four mechanisms:

1. **Tracing.** Every run is a trace in Arize Phoenix, with one span for each
   model call and one for each tool call.
2. **Latency and cost dashboards.** Broken down by version, both in Phoenix and in
   the cockpit's text dashboard.
3. **Alerting on loops and failures.** A loop guard stops a runaway run while
   it happens. Alert rules watch the recent runs of each version.
4. **Canary testing with rollback.** A new version gets 10% of traffic and is
   judged against the stable version. It is promoted only if its metrics hold
   *and* two humans approve, and it is rolled back automatically if it degrades
   after promotion.

**Local, but built like production.** It runs on a laptop using the mechanics
you would deploy: OTLP export to a real tracing backend, alert rules evaluated on
live metrics, hash-based canary routing, and rollback through the model
registry. It is not deployed: there is no container, hosted endpoint, CI/CD or
paging.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Home | New project 16 | Doesn't wait on ORION's phases 1–6; keeps 09 about one idea |
| Deployment | Local, built like production | No cloud accounts or bill; tests stay offline |
| Tracing backend | Arize Phoenix, self-hosted in Docker | No account needed; built on OpenTelemetry, so moving to hosted Arize is a configuration change |
| Span creation | Spans created by hand, using OpenInference attribute names | Canary group, version, loop and outcome attributes stay under our control; tests don't depend on an auto-instrumentor's internals |
| Model provider | Claude, through the Anthropic SDK's Tool Runner | Same approach as ORION, so the tracing and alerting code carries over |
| Versions | stable `claude-opus-5`, canary `claude-sonnet-5` | Identical request settings on both; the canary costs 2.5× less per token |
| Where code lives | Inside project 16; the only cockpit changes are Claude prices and one test fix | Only one project uses it; move it into `cockpit/` when a second one does |
| Promotion | Metrics plus 2 human approvals, prompted during the run | Uses the cockpit `ApprovalWorkflow`; nothing to persist between runs |
| Abort and rollback | Automatic, no approval | A rollback has to work at 3am, and the version it restores was already approved on its way in (same reasoning as project 12) |
| Refusal fallbacks | Off | They would answer some requests with a different model and blur which model each version's metrics describe; refusal rate is tracked instead |
| Implementation | Claude writes the code; the user reviews at checkpoints | |

## Architecture

### Components

```
projects/16-production-agent/
├── pyproject.toml      uv, package = false, pythonpath = ["src", "../.."]
├── .env.example        ANTHROPIC_API_KEY
├── README.md
├── src/
│   ├── main.py         CLI wiring, dashboard and timeline output
│   ├── tools.py        simulated order data, three read-only tools, sample questions
│   ├── agent.py        AgentVersion, RunRecord, run_agent: one run through the Tool Runner
│   ├── tracing.py      tracer provider and exporter selection
│   ├── alerts.py       LoopGuard, RunWindow, alert rules, AlertMonitor, alert sinks
│   ├── canary.py       route, judge, RolloutController
│   ├── fake_model.py   httpx.MockTransport that serves scripted Messages API responses
│   └── scenarios.py    the two dry-run scenarios: workloads, scripted model behavior, approvers
└── tests/
```

| Unit | Does | Depends on |
|---|---|---|
| `tools.py` | `lookup_order(order_id)`, `track_shipment(tracking_id)`, `refund_policy(category)` over an in-memory order table; invalid input raises the SDK's `ToolError` | nothing |
| `agent.py` | `AgentVersion` (version, model, system prompt, effort, max_tokens; `fingerprint()` is a SHA-256 over those plus the tool schemas). `run_agent(...) -> RunRecord` runs one question and returns what happened | Anthropic client, tracer, `PerformanceTracker`, `CostTracker`, clock, `LoopGuard` |
| `tracing.py` | `build_tracer_provider(exporter)`: `None` for no export, the OTLP exporter for Phoenix, an in-memory exporter in tests | OpenTelemetry SDK |
| `alerts.py` | `LoopGuard.check(tool_uses) -> reason \| None`; `RunWindow` keeps the last 20 `RunRecord`s per version and computes `WindowStats`; `AlertMonitor.observe(version, stats)` returns fired and resolved transitions and delivers them to the sinks | cockpit `percentile` |
| `canary.py` | `route(request_id, percent) -> bool`; `judge(...) -> Verdict` (pure); `RolloutController` owns each request's root span, routing, alert evaluation, and the registry and approval steps | `ModelRegistry`, `ApprovalWorkflow`, `alerts.py`, `agent.py` |
| `fake_model.py` | Answers Messages API requests from a script keyed by model and conversation state; advances the fake clock by the scripted latency | `httpx` (installed with `anthropic`) |
| `main.py` | Parses arguments, builds everything, runs the rollout, renders output | all of the above |

Trackers, the registry and the approval workflow are created in `main.py` and
passed in, never taken from module-level defaults, following the reasoning in
project 08. One injected clock drives span timestamps, tracker latencies and
`RunRecord.duration_s`, so a dry run produces identical numbers every time. In
live mode that clock is `time.time`, because OpenTelemetry timestamps are
wall-clock time.

### How a request flows

1. **Route.** `RolloutController` hashes the request ID. During the canary
   phase, 10% of IDs go to the canary version and the rest to the registry's
   production version, and a given ID always lands in the same group.
2. **Root span.** The controller opens `agent.run` with `request.id`,
   `canary.group`, `agent.version` and `llm.model_name`.
3. **Run.** `run_agent` drives `client.beta.messages.tool_runner(...,
   max_iterations=8)`. For every message the runner yields:
   - it records an `llm.call` span covering the model request, with token
     counts and stop reason; latency goes to the `PerformanceTracker` under
     `model.generate[<version>]` and cost to the `CostTracker`;
   - it passes the message's `tool_use` blocks to `LoopGuard` *before* the
     runner executes them; if the guard trips, `run_agent` breaks out of the
     loop, the pending tools never run, and the outcome is `loop`.
4. **Tools.** Each tool is wrapped before it is registered with the runner. The
   wrapper opens a `tool.<name>` span, counts the call, records latency under
   `tool.<name>`, and on `ToolError` marks the span as an error and counts the
   failure before re-raising. The runner
   then returns an `is_error` tool result and the model can recover.
5. **Record.** `run_agent` returns a `RunRecord`: request ID, version, group,
   outcome, `duration_s`, `cost_usd`, tool calls, tool errors, and input and
   output tokens.
6. **Evaluate.** Still inside the root span, the controller records the run's
   duration under `agent.run[<version>]` (success only when the outcome is
   `ok`), appends the record to the version's window, runs `AlertMonitor`, then the judge (canary phase)
   or the rollback check (watch phase). Alert and rollout transitions become
   events on the root span, and the span then closes with `agent.outcome` and
   `agent.cost_usd`.

**Outcomes.** Each outcome is decided from how the run ended:

| Outcome | When |
|---|---|
| `ok` | final `stop_reason` is `end_turn` |
| `loop` | the loop guard tripped |
| `max_iterations` | the runner stopped at 8 iterations; the final `stop_reason` is still `tool_use` |
| `refusal` | final `stop_reason` is `refusal` |
| `incomplete` | any other final stop reason (`max_tokens`, `model_context_window_exceeded`) |
| `api_error` | the API call failed after the SDK's retries |

A tool error does not end a run. It is counted in the record and feeds the
`tool_error_rate` rule, but it is not an outcome of its own.

### Request settings

Every version sends the same request apart from `model`: adaptive
`thinking`, `output_config.effort = "medium"`, `max_tokens = 16000`, not
streamed, and the same system prompt and tools. All of these are covered by
`AgentVersion.fingerprint()`, which is stored as the registry's
`config_fingerprint`. The system prompt tells the model to normalize a bare
order number `10042` to `ORD-10042` before calling `lookup_order`.

## Tracing

```
agent.run               AGENT   request.id, canary.group, agent.version, llm.model_name,
│                               agent.outcome, agent.cost_usd, input.value, output.value
├── llm.call            LLM     llm.model_name, llm.token_count.prompt,
│                               llm.token_count.completion, llm.stop_reason
├── tool.lookup_order   TOOL    tool.name, input.value, output.value; error status on ToolError
├── llm.call            LLM
└── …
```

- Span kinds and attribute names follow `openinference-semantic-conventions`,
  which is how Phoenix renders them. Attributes without an OpenInference
  equivalent use the prefixes `agent.`, `canary.`, `request.` and `llm.stop_reason`.
- Before `input.value` and `output.value` are set, they pass through
  `cockpit.security.output_security.mask_pii`, because traces are a common place
  for personal data to leak.
- Every span gets explicit start and end timestamps from the injected clock.
  The dry-run clock starts at `2026-01-01T00:00:00Z`, so traces from a dry run
  appear in order in Phoenix.
- **Export.** With `--phoenix`, spans go through a `BatchSpanProcessor` and
  `OTLPSpanExporter` to `http://localhost:6006/v1/traces`, or to
  `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` when that is set. Without the flag,
  nothing is exported. Tests use `SimpleSpanProcessor` with
  `InMemorySpanExporter`. Export failures are logged by the OpenTelemetry SDK
  and never reach a run. `main.py` calls `provider.shutdown()` on exit so the
  last batch gets sent.
- **Phoenix.** The README gives
  `docker run -p 6006:6006 -p 4317:4317 -i -t arizephoenix/phoenix:<tag>`, with
  the tag pinned to the Phoenix release current when the project is built (Phoenix
  recommends pinning).

## Loop guard

`LoopGuard` is created fresh for each run and checks each yielded message's
`tool_use` blocks before they run:

- **Repeat:** a third call to the same tool with identical input trips it.
  Inputs are compared as `json.dumps(input, sort_keys=True)`, so key order
  doesn't matter.
- **No progress:** a fifth call to the same tool in one run trips it, whatever
  the inputs.
- **Backstop:** the runner's `max_iterations=8` catches anything the two checks
  miss.

When the guard trips, the outcome is `loop`, the root span is marked as an
error, and `agent.loop.reason` is set to `repeat:<tool>` or
`no_progress:<tool>`.

## Alerts

`RunWindow` keeps the last 20 `RunRecord`s **per version**, so after a promotion
the new version's numbers never mix with the old version's. `WindowStats` holds
the run count, loop count, failure rate (outcome other than `ok`), tool error
rate (tool errors ÷ tool calls), p95 duration (via cockpit `percentile`) and
mean cost per run.

| Rule | Fires when | Minimum runs |
|---|---|---|
| `loop_detected` | the window contains a `loop` outcome | 1 |
| `failure_rate` | failure rate > 20% | 10 |
| `tool_error_rate` | tool error rate > 30% | 10 |
| `p95_latency` | p95 duration > 30 s | 10 |
| `cost_per_run` | mean cost per run > $0.15 | 10 |

These thresholds are fixed, like production limits. The canary judge
compares the two versions against each other instead.

`AlertMonitor` tracks whether each (version, rule) pair is firing and reports
transitions only. A rule sends `fired` once when it crosses its threshold and
`resolved` once when it clears, never again on each run in between. Every
transition is:

- logged at WARNING,
- appended to `outputs/alerts.jsonl` as one JSON object (`timestamp`, `version`,
  `rule`, `state`, `value`, `threshold`, `request_id`),
- added as an `alert.fired` or `alert.resolved` event on the root span of the
  run that caused it.

## Canary rollout

### Versions

The registry model name is `order-support-agent`. Each CLI run sets it up from
scratch, because the registry is in memory:

- `v1` is registered with `provider_model_id="claude-opus-5"` and its
  fingerprint, then promoted development → staging → production. That path
  also records it as the version to roll back to later.
- `v2` is registered with `provider_model_id="claude-sonnet-5"` and promoted
  to staging.

### Phases

`RolloutController` moves through `CANARY → (WATCH) → DONE`.

**CANARY.** Requests are routed with
`int(sha256(request_id).hexdigest()[:8], 16) % 100 < canary_percent`
(default 10). After each canary run, `judge` is called with both versions'
`WindowStats`, the canary's total run count and the canary's firing alerts:

1. Any canary alert firing → **abort**, however few runs so far.
2. Fewer than 10 canary runs so far → **continue**.
3. If stable's window has at least 10 runs, **abort** when any of these holds.
   A ratio check is skipped when stable's value is 0.
   - canary failure rate > stable failure rate + 0.05
   - canary p95 > 1.5 × stable p95
   - canary mean cost per run > 1.2 × stable mean cost per run
4. At least 30 canary runs → **ready**.
5. Otherwise → **continue**.

The cost check is there because a cheaper model that needs more turns is not
actually cheaper. Alerts for the stable version during this phase are delivered
but don't affect the judge.

**Abort:** `v2` moves staging → development, all traffic goes to `v1`,
and the phase becomes DONE.

**Ready → approval.** Traffic pauses while the controller submits
`ApprovalWorkflow.submit(requested_by="canary-judge", description=<summary>,
required_approvals=2)`. The summary puts both versions' run counts, failure rate,
p95 and cost per run side by side. An injected approver callback then collects
decisions:

- **Interactive:** prompts for an approver name, then approve or reject with an
  optional comment, until the request is no longer pending or the name is left
  blank.
- **Dry run:** the scenario's scripted decisions.

The cockpit workflow enforces one vote per person and makes rejection final.
Because the requester is `canary-judge`, no human is blocked by the
no-self-approval rule. If the request ends `APPROVED`,
`registry.promote(v2, PRODUCTION, actor_id="canary-judge")` runs and the phase
becomes WATCH. Anything else, whether rejected or still pending when prompting
ends, is treated as an abort. Requests do not expire, because approval happens
inside the run.

**WATCH.** For the next 30 runs all traffic goes to `v2`. The first alert that
fires for `v2` calls `registry.rollback_production("order-support-agent",
actor_id="canary-judge")`: `v1` goes back to production, `v2` to staging, and
the phase becomes DONE. Thirty runs without an alert also end in DONE.

**DONE.** Traffic goes to the production version. Alerts still fire and are
delivered but trigger no action.

The registry and approval workflow write every stage change and approval
decision to the cockpit governance trail.

### Dry-run scenarios

Request counts and scripted latencies live in `scenarios.py`, sized so each
scenario reaches its final state. Tests assert the final state, not the counts.

**`bad-canary`.** Some orders have a shipment whose tracking status never
changes. For those, scripted `v2` keeps calling `track_shipment` with the same
tracking ID, and the third identical call trips the loop guard.
`loop_detected` fires for `v2` and the judge aborts. Scripted `v1` reports the
status and stops. *Final state:* `v1` in production, `v2` in development.

**`late-regression`.** During the canary phase every request uses modern order
IDs (`ORD-10042`). `v2` matches `v1` on failures and latency at lower cost, is
ready after 30 canary runs, gets two scripted approvals, and is promoted. After
promotion the workload starts including bare legacy order numbers (`10042`),
something the canary traffic never contained. Scripted `v1` normalizes them as
the system prompt instructs; scripted `v2` passes them through unchanged, so
`lookup_order` raises `ToolError`. `tool_error_rate` fires during WATCH and `v2`
is rolled back. *Final state:* `v1` in production, `v2` in staging, governance
trail intact.

## Error handling

- **Tool failures:** as described in the request flow. The runner turns a
  `ToolError` into an `is_error` result and the run continues.
- **API errors:** the SDK's default `max_retries=2` retries 429 and 5xx
  responses. `run_agent` catches the most specific error first:
  `AuthenticationError` is re-raised and the CLI exits with code 2 and a message,
  because it's a configuration fault and says nothing about the canary.
  `RateLimitError`, `APIStatusError` and `APIConnectionError` produce outcome
  `api_error`, and the rollout moves on to the next request.
- **Refusals:** outcome `refusal`, counted as a failure. Server-side refusal
  fallbacks are deliberately not enabled (see Decisions). To add them later,
  tag each `llm.call` span with the model that actually answered and group
  metrics by that model.
- **Tracing:** export runs in the background and can never fail a request.

## CLI and output

```bash
uv run python src/main.py --dry-run --scenario bad-canary
uv run python src/main.py --dry-run --scenario late-regression --phoenix
uv run python src/main.py --requests 200 --canary-percent 10 --phoenix
```

- `--dry-run --scenario {bad-canary,late-regression}` uses the fake transport,
  fake clock and scripted approvers; it needs no API key and no network.
- Live mode picks `--requests` questions from `SAMPLE_QUESTIONS` in order,
  wrapping around, with IDs `req-0001` onward. `ANTHROPIC_API_KEY` is read
  through `cockpit.security.secrets_manager.get_secret`. Approvals are prompted
  interactively.
- `--phoenix` turns on OTLP export.

At the end, the CLI prints the cockpit's `render_dashboard_text` built from both
trackers, followed by a **rollout timeline**: canary started, each alert fired or
resolved, the approval request and each decision, and the final
promote/abort/rollback. It then prints the governance trail check
(`verify_trail_integrity()`) and the path to `outputs/alerts.jsonl`.

## Testing

Everything is offline and deterministic except the final row. The fake
transport runs the SDK's real Tool Runner against scripted HTTP responses; no
SDK internals are mocked.

| Target | Test |
|---|---|
| `route` | The same ID always gets the same answer; about 10% of 1,000 IDs go to canary (between 5% and 15%) |
| `LoopGuard` | Trips on the 3rd identical call; varied inputs trip only at the 5th; key order in the input is ignored |
| Outcomes | Table-driven over final stop reasons, loop trip and iteration cap |
| Alert rules | Table-driven per rule at and around thresholds; minimum runs respected; one `fired` and one `resolved` per crossing |
| `judge` | Table-driven: alert abort, below minimum, each relative check, zero stable values, ready |
| Approval | Ready opens a 2-approval request; approved → promoted and WATCH; rejected → abort; still pending when prompting ends → abort |
| Rollback | Alert during WATCH → `v1` back in production; alert after DONE → delivered, no action |
| Tracing | In-memory exporter: span tree and parent links; group, version, model and outcome attributes; loop run has error status and reason; alert appears as a span event; PII in the question is masked in `input.value` |
| Resilience | A tool raising `ToolError` still produces a `RunRecord` with the error counted; an exporter that raises does not fail the run; `AuthenticationError` propagates |
| Scenarios | `bad-canary` and `late-regression` reach their final states; the governance trail verifies |
| Cockpit pricing | `estimate_cost` prices `claude-opus-5` and `claude-sonnet-5` |
| Live | One real run through the Tool Runner, marked `live_provider` and skipped by default |

## Changes outside the project

- **`cockpit/monitoring/cost_tracking.py`:** add an Anthropic section with
  `claude-opus-5` at `ModelPricing(5.00, 25.00, "anthropic")` and
  `claude-sonnet-5` at `ModelPricing(2.00, 10.00, "anthropic")`, checked on
  2026-09-17 against Anthropic's models overview. Add
  `https://platform.claude.com/docs/en/about-claude/pricing` to the sources
  comment and move `PRICING_VERIFIED_DATE` only if the whole table is rechecked
  at that point. Otherwise, note the Anthropic check date in the comment.
- **`tests/e2e/test_examples.py`:** discovery uses `glob("0*-*")`, which already
  skips projects 10–14 and would skip 16. Change it to `glob("[0-9][0-9]-*")`.
- **`README.md`:** add project 16 to the project table and update the project
  count wherever the text gives it.

## Conventions and dependencies

Same conventions as cockpit projects 06–14: flat `src/`, `tests/` with
`conftest.py`, `uv` with `package = false`, `pythonpath = ["src", "../.."]`,
pytest with pytest-cov, ruff, Python 3.11+, and a `--dry-run` that works with no
credentials.

Runtime dependencies: `anthropic>=1.6`, `opentelemetry-sdk`,
`opentelemetry-exporter-otlp-proto-http`, `openinference-semantic-conventions`,
`python-dotenv`. Dev dependencies: `pytest`, `pytest-cov`, `ruff`. `httpx` comes
with `anthropic`. Phoenix runs in Docker, not as a Python dependency.

## Out of scope

- **Real deployment:** container image, hosted endpoint, CI/CD, secrets in CI.
- **Real alert delivery** (Slack, email, PagerDuty). Each would be another sink
  receiving the same transition events.
- **Stepwise traffic ramps** (10% → 50% → 100%). The WATCH phase catches
  regressions that only show at full traffic; a ramp becomes worthwhile with
  real traffic volumes, where it limits how many users see a bad version.
- **Rollout state that survives between runs**, and approving from a separate
  command.
- **Role checks on approvers.** Projects 11 and 12 cover role-based access.
- **Auto-instrumentation** (`openinference-instrumentation-anthropic`).
- **Moving tracing, alerts or canary logic into `cockpit/`** before a second
  project needs them.
