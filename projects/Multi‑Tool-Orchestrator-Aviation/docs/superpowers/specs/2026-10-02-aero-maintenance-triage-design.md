# AERO — Multi-Tool Orchestration for Aviation Maintenance Alert Triage

**Project:** `Multi‑Tool-Orchestrator-Aviation`  
**Repository:** Local at `projects/Multi‑Tool-Orchestrator-Aviation/`, own git repo  
**Working copy:** Nested in AI_Engineering_Cockpit for convenience  
**Date:** 2026-10-02  
**Status:** Approved design, ready for Phase 0 scaffold

AERO is the agent's name and appears throughout the code and documentation. The folder name describes what it does, following the cockpit convention.

## Purpose

AERO is a multi-tool orchestration system for aircraft maintenance alert triage. Given one alert — from onboard sensors, ACARS text messages, or ground telemetry — it produces:

1. A **triage decision** (Clear, Schedule maintenance, Ground aircraft, Escalate to engineer)
2. The **reasoning** behind that decision
3. A complete **audit trail** of every tool call and conflict resolution
4. A **deterministic workflow** that guarantees the same alert always produces the same decision

The domain is aviation maintenance: conflicting sensor readings are normal, every action must be auditable, safety overrides convenience, and regulations are immovable. These constraints make AERO's mechanisms something genuine to do.

AERO is distinct from the cockpit's multi-model orchestrators (projects 3, 5): AERO orchestrates **tools**, not models. It demonstrates deterministic, workflow-driven orchestration as the opposite of model-driven (where the model picks tools and runs may vary).

### Secondary purpose: learning

The user is building this to learn the mechanics. That constraint shapes the design: mechanisms are small, separately testable units rather than bundled into a framework. Conflict resolution and decision rules are pure functions with table-driven tests — the best place to learn by writing, and the worst place to accept code you didn't reason through.

## The four mechanisms

AERO exists to demonstrate four things. Everything in the design serves one of them:

1. **Dynamic tool registry** — tools are entries in a list that can be added and removed while the system runs, not hardcoded function calls
2. **Parallel execution** — read-only tools run concurrently; writes run one at a time
3. **Permission scoping** — each tool declares what data it may receive; calls outside that scope are refused before execution
4. **Conflict resolution** — when tools disagree, an explicit, inspectable procedure settles it — or declines to, escalating instead

## Architecture

### Surface: deterministic workflow engine + Claude narrator

The six-phase workflow is orchestrated by code, not by a model. Each phase has a fixed purpose, fixed outputs from the prior phase, and a fixed tool set. The model's role is reserved for the final step: Claude writes the explanatory prose. It sees the decision but cannot change it.

**Why this surface.** ORION showed that the Anthropic Tool Runner runs a turn's tools one-at-a-time (not parallel) and lets the model decide the sequence (not deterministic). The brief demands both. A deterministic, parallel workflow lives in code; Claude's explanatory prose is best-effort, audited but not critical.

**Model & narrator.** Claude Opus 5.5 for the narrator — structured output with a JSON schema. If the narrator fails or fails its schema check, the rule trace becomes the reasoning, and the call is logged like any other tool failure.

### The six phases

| Phase | Input | Job | Parallelism | Output |
|---|---|---|---|---|
| 1 Ingest | Alert file (JSON or ACARS text) | Parse, validate schema, normalize → `MaintenanceAlertEnvelope` | — | Validated envelope + ATA domain mapping |
| 2 Diagnose | Envelope + domain | Router picks tools; telemetry, fault classification, history, predictive models run | Read-only tools concurrent | Structured claims with confidence scores |
| 3 Comply | Envelope + domain + diagnostic claims | MEL checker, safety rules engine, regulatory compliance → concurrent reads | Read-only tools concurrent | Compliance claims, evidence gaps |
| (Resolve + Decide) | All claims | Apply R1-R4; produce decision + rule trace | — | Decision (CLEAR/SCHEDULE/GROUND/ESCALATE) + trace |
| 5 Act | Decision + playbook | Actions one at a time; approval policy applied; permission policy enforced | Writes sequential | Executed actions, approval requests, refusals |
| 6 Report | Decision, reasoning, claims, audit trail | Claude narrator writes the reasoning; validate against rule trace; write JSON output | — | Stdout report + outputs/triage-{id}.json |

### Components

Each component has one job and is testable offline (no API key).

**envelope.py** — Parse alert, validate schema, normalize fields into a `MaintenanceAlertEnvelope`. ACARS text is parsed via the `acars_parser` tool. Schema validation failures → ESCALATE with errors listed.

**registry.py** — `ToolSpec` per tool: name, description, input schema, the callable, permission scope, safety tier, latency budget, conflict domains, mutates flag. Rendering for the executor. Add/remove at runtime.

**executor.py** — The single seam where every tool call is guarded:
1. Permission check — request fields against tool's allowed scope; refuse before execution
2. Timeout enforcement — `asyncio.wait_for()` with the tool's latency budget
3. Execution — concurrent for read-only, sequential for writes
4. Audit recording — tool name, arguments, scope verdict, latency, result/error, before/after state
5. Failure handling — record and return, never raise (except for programmer errors)

**claims.py** — Normalize tool results into structured claims: a dimension (what question it answers: `condition`, `fault_source`, `dispatch`), a verdict, confidence score, and source. Unsupported result shapes are logged but don't crash the run.

**conflict.py** — **You write this.** Apply the four rules per disputed dimension:
- **R1 Safety** — if a safety_rule claim exists, its verdict wins
- **R2 Regulatory** — regulatory vs operational history, regulatory wins
- **R3 Evidence** — all sensors ≥ 0.85 confidence vs all models < 0.85, sensors win
- **R4** — anything else is unresolved

Losing claims stay in the output marked `overridden: true` with the rule that overrode them.

**decision.py** — **You write this.** Pure function from resolved claims to verdict, evaluated in order:
1. Invalid alert → ESCALATE
2. `dispatch` no_go → GROUND
3. Unresolved conflict → ESCALATE
4. HIGH-tier evidence gap → ESCALATE
5. Degraded/failed condition or `defer` → SCHEDULE
6. Otherwise → CLEAR

**tools.py** — The brief's 17 simulated tools. Four domain telemetry tools (engine vibration, hydraulic pressure, flight control, brake temp), fault classifier, predictive model, maintenance history, deferred defects, MEL checker, safety rules engine, regulatory checker, technician scheduler, parts inventory, dispatch notification, aircraft status setter, routing optimizer, maintenance log, human approval gate, Claude narrator. Tools return deterministic canned data driven by per-aircraft fleet data in `data/fleet.json`.

**actions.py** — Decision → playbook of actions. Each action has a permission (e.g. `aircraft:ground`, `parts:reserve`) and a safety tier (LOW, MEDIUM, HIGH). Permission refusal turns the action into an approval request item. HIGH-tier actions always go through the approval gate.

**narrator.py** — Claude Opus 5.5 writes prose explaining the result, given the decision and rule trace. Output must validate against a JSON schema before use. Failure falls back to rule trace prose; the call is audited like any tool call.

**audit.py, report.py** — Append-only audit trail per call: tool name, arguments, scope verdict, execution time, result/error, before/after credibility. Final report: decision, reasoning, diagnostic summary, compliance summary, operational actions, full audit trail, approval requests, evidence gaps.

**workflow.py** — Orchestrates phases 1–6 in order. Each phase routes to its tools, invokes the executor, collects results, passes to the next phase.

**cli.py** — `uv run aero alerts/c-hydraulic-loss.json` → triage → stdout report + outputs/triage-{id}.json.

### The simulated tools

17 tools total, all deterministic (canned data + scripted failures). Behavior driven by alert readings vs thresholds and per-aircraft fleet data:

| Tool | Returns | Behavior |
|---|---|---|
| `engine_vibration` | Frequency analysis, acceleration levels, trend | Reliable; returns normal or degraded based on threshold |
| `hydraulic_pressure` | System A/B pressures, trends, low-pressure warnings | Reliable; script can produce a loss scenario |
| `flight_control_telemetry` | Surface position, response lag, deflection rates | Reliable; returns normal, slow, or stuck |
| `brake_temperature` | Wheel temps, max/min, thermal signature | Reliable; can fail if turnaround was quick |
| `acars_parser` | Parsed message fields (from raw ACARS text) | Reliable; extracts ATA chapter, urgency codes |
| `fault_classifier` | Probable subsystem, confidence score | Reliable; model-like (0.7–0.99 confidence) |
| `predictive_model` | Failure risk 0–100, confidence | Reliable; can produce high risk + low confidence (conflicts with sensor) |
| `maintenance_history` | Recent work, deferred items, repeat faults | Reliable; script can show "defer was used last time" |
| `deferred_defects` | Open MEL/deferred deferrals, age, MEL reference | Reliable; script shows multi-system deferrals |
| `mel_checker` | Is MEL go/no-go for this config? | Reliable; returns no-go if system A failed + no backup or open deferral |
| `safety_rules_engine` | Is this a safety rule no-go? | Reliable; hard rule: "if system A failed and system B already deferred, no-go" |
| `regulatory_checker` | Active ADs for aircraft model, applicability | Reliable; script can show "inspection required within 10 days" |
| `inspection_schedule` | Available technician slots, parts lead times | Reliable; simulated schedule |
| `parts_reserve` | Reserve part; returns reservation ID | Reliable; simulated inventory |
| `dispatch_notify` | Notify dispatch of decision | Reliable; simulated |
| `aircraft_status_set` | Set aircraft status (maintenance, grounded, etc.) | **Refused by permission gate** — AERO holds no `aircraft:ground` permission |
| `approve_gate` | File human approval request; returns ticket ID | Reliable; simulated |
| `maintenance_log_append` | Log action to aircraft history | Reliable; simulated |

### Four demo scenarios (one per decision, one per conflict rule)

**Scenario A: Brake temp after quick turnaround**
- Alert: Brake temperature elevated after a 15-minute turnaround
- Conflict: Predictive model (0.55, "degraded") vs brake sensor (0.97, "normal")
- Rule: **R3** — high-confidence sensor beats low-confidence model
- Decision: **CLEAR**
- Teaches: confidence trumps model authority; overridden claims stay visible

**Scenario B: Engine vibration (ACARS text)**
- Alert: Raw ACARS message with engine vibration codes
- Conflict: Maintenance history ("nuisance, go") vs active AD ("inspection within 10 days")
- Rule: **R2** — regulatory beats operational convenience
- Decision: **SCHEDULE** (inspection is regulatory-sensitive → approval request filed)
- Teaches: regulations are immovable; conflicts across dimensions (maintenance vs compliance)

**Scenario C: Hydraulic system B loss**
- Alert: Hydraulic system B pressure loss
- Conflict 1: History ("deferred last time") vs safety engine ("no-go") + MEL ("no-go" because system A already deferred)
- Rule 1: **R1** — safety overrides everything
- Conflict 2 (within safety): System A deferral + system B loss = dual failure → escalate
- Escalations: `set_aircraft_status` refused (no permission) → approval request; `repair` (HIGH) → approval request; parts reserved; dispatch notified; log appended
- Decision: **GROUND** (pending approval)
- Teaches: multi-dimension conflicts; permission vs safety; multiple actions one-at-a-time; approval gate; refusals in the trail

**Scenario D: Aileron slow response**
- Alert: Aileron control surface responding slowly
- Conflict: Classifier (0.91, "flight control subsystem") vs telemetry (0.93, "hydraulic supply delay")
- Rule: **R4** — both high-confidence, no rule settles it
- Decision: **ESCALATE** (engineer review filed)
- Teaches: R4 in action; credibility gap too narrow for any rule; escalation is a first-class outcome

**Scenario E: APU alert with no tool registered**
- Alert: Auxiliary power unit temperature high
- Routing: No APU domain tools registered
- Behavior: Fail-closed → ESCALATE
- Teaches: dynamic routing; runtime registration; fail-closed design

All five scenarios pass through the full six-phase workflow. Reproducible: same alert in → same decision out. Claude's prose varies; decision and rule trace don't.

## How a case flows

1. **Load.** Read alert; build registry; load any persisted credibility scores.
2. **Phase 1.** Parse and validate.
3. **Phase 2.** Router picks domain tools; fan-out concurrent; collect claims.
4. **Phase 3.** Router picks compliance tools; fan-out concurrent; collect compliance claims.
5. **Resolve.** Apply rules R1–R4; produce resolved claims + overrides.
6. **Decide.** Apply decision rules D1–D6; produce decision + rule trace.
7. **Phase 5.** Playbook for decision; one action at a time; permissions checked; HIGH-tier actions to approval gate.
8. **Phase 6.** Claude narrator writes reasoning (validated against trace); JSON report to outputs/.

## Safety semantics

**Conflict rules (you write `conflict.py`)**

Applied per dimension when claims disagree:

1. **R1 Safety** — if a `safety_rule` claim exists, its verdict wins (most severe if multiple)
2. **R2 Regulatory** — regulatory claim vs operational history claim, regulatory wins
3. **R3 Evidence** — all sensors ≥ 0.85 confidence vs all models < 0.85, sensor verdict wins
4. **R4** — no rule above applies; conflict is unresolved

Losing claims are never dropped — they stay marked `overridden` with the rule and score gap.

**Decision rules (you write `decision.py`)**

Pure function from resolved claims to verdict (first match wins):

1. Invalid alert → ESCALATE
2. `dispatch` dimension resolves to `no_go` → GROUND
3. Any unresolved conflict → ESCALATE
4. HIGH-tier tool failed/refused/timed-out/unroutable → ESCALATE
5. `condition` degraded/failed OR `dispatch` defer → SCHEDULE
6. Otherwise → CLEAR

GROUND ranks above ESCALATE: once a safety rule says no-go, an unresolved disagreement elsewhere can only make things worse.

**Two gates**

- **Permission gate** (executor) — "may AERO ever do this?" Default deny. Refused before tool runs; turns into an approval request item.
- **Approval gate** (Phase 5) — "AERO recommends it; human signs off." Triggered by GROUND, HIGH-tier actions, regulatory-sensitive claims. One ticket per run; AERO files it and continues (no wait-for-approval; durable resume is out of scope v1).

**Fail closed**

| Failure | Behavior |
|---|---|
| Unreadable or invalid JSON | CLI exit 2 |
| Alert fails schema validation | ESCALATE (D1) with errors in reasoning |
| HIGH-tier tool fails/refused/timeout | ESCALATE (D4) |
| LOW/MEDIUM-tier tool fails | Recorded as evidence gap; decision proceeds |
| Action fails | Listed as "not completed"; decision unchanged |
| Narrator fails or fails schema check | Rule trace becomes reasoning; call audited |

No retries in v1. A failed call is missing evidence, and D4/D5 already handle missing evidence safely.

## Testing

Every test runs offline — no API key, no network. Simulated tools have scripted, deterministic behavior.

| Target | Test |
|---|---|
| Registry | Add/remove mid-run; tool list stable (sorted) |
| Executor | Permission check before execution; timeout enforced; audit complete; parallel vs sequential by tool type |
| Claims | Parse tool results → claims; unsupported shapes logged, run continues |
| Conflict (R1-R4) | Table-driven per rule type; losing claims marked overridden; gap recorded |
| Decision (D1-D6) | Table-driven per condition; order matters; first match wins |
| Workflow | Phases run in order; each gets prior's results; routing works; all tool calls audited |
| Actions | Playbook built; permissions checked; approval gate policy; failures recorded |
| Narrator | Request shape valid; schema check; fall back to rule trace on failure; audited |
| Audit & report | Complete trail after full run; JSON renders; approval requests listed |
| CLI | File read; alert schema validation; workflow invoked; output files written |

**End-to-end tests:** One per scenario (A, B, C, D, E). Skipped by default (no token spend). Check decision and rule trace against expected.

**Method:** Test-first. Tests written before implementation. For `conflict.py` and `decision.py`, I create the test file with empty test functions; you write the implementation to pass the tests.

## Conventions

Flat `src/aero/` package with `hatchling`, `pytest`, ruff. Python 3.11+. Matches the cockpit pattern (projects 15 ORION, HITL, etc.).

- `uv` with `package = false` (application, not a library)
- pytest with `pythonpath = ["src"]` so imports are absolute (`from aero.registry import ...`)
- ruff config copied from ORION
- Local git repo in this folder; GitHub remote added later if you ask
- Dependencies: only `anthropic` (for narrator); everything else is stdlib (asyncio for concurrency)
- `notes.md` is gitignored as a personal log

## Phases

| # | Phase | Deliverable | Author | Notes |
|---|---|---|---|---|
| 0 | Scaffold | uv project, pyproject.toml, ruff.toml, test harness, alert fixtures (A–E), README stub | Claude | `src/`, `tests/`, `docs/`, `alerts/` structure |
| 1 | Plumbing | 9 modules + CLI; 60+ tests passing | Claude | Writes all modules except `conflict.py` and `decision.py` |
| 2 | Safety logic | `conflict.py` (R1–R4), `decision.py` (D1–D6); wire into workflow.py | **You** | Claude provides test file + signatures; you write implementations |
| 3 | Documentation | Design spec commit, README finalized, examples, graphify knowledge graph | Claude | One commit per output |
| 4 | Git & CI | Initialize git, GitHub remote, push, GitHub Actions CI (pytest on push) | Claude | Only on your ask |

Each phase: tests → code review → one commit. Hidden complexity discovered mid-phase upgrades the phase, never compresses it.

## Open questions

**None at this time.** The design is complete. Phases 0–1 have no ambiguity. Phase 2 depends on you writing the logic after seeing the tests.

## Out of scope for v1

Deliberately deferred. Each is a real capability, parked because v1 doesn't need it:

- **Real external APIs** (ACARS parser, real technician schedules, parts inventory, aircraft databases) — simulated data keeps the suite deterministic and offline
- **Durable approve-and-resume** — checkpoint/restore of state — your HITL project already covers that; AERO files one approval request per run and continues
- **Batch processing** — cockpit project 10 covers that
- **Dashboard or UI** — cockpit project 8 covers that
- **Credibility scoring and persistence** — ORION teaches it; AERO teaches orchestration and conflict resolution instead
- **Fallback to a different tool on failure** — no tool has a backup; single retry on same tool is enough for v1
- **Concurrent actions** — actions run one at a time (safer for writes); concurrency is read-only tools in diagnostics/compliance
- **Mid-run escalation without approval gate** — approval requests cover the case

## Tooling

- **GSD** — roadmap and phase artifacts under `.planning/` (may be added later if desired)
- **superpowers** — brainstorming (done) → writing-plans (next) → test-driven development → code review → verification
- **ponytail** — review each phase for what should not exist (stdlib over custom, one-liners over boilerplate)
- **graphify** — build a knowledge graph of the project once code exists (Phase 1 complete)
- **caveman** — context compression (optional, used if context becomes large)

## Attribution

Spec written with Claude (brainstorming design-in-chat). Implementation will be split: Claude writes plumbing and test scaffolds; user writes the safety logic (conflict resolver and decision rule) with coaching.
