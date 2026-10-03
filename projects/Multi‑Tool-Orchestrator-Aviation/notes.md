# AERO — Working Notes (Q&A and Decision Log)

Running log of every interaction while building AERO, the aviation maintenance
alert triage orchestrator. Captures the reasoning behind decisions, not just
the decisions. Newest entries at the bottom.

---

## Session 2026-10-02 — Planning (brainstorming)

### 1. "Let's build this — planning first"

**What was here:** only the brief, `Multi‑Tool-Orchestrator-Aviation.md`.

**What I checked before asking anything:**

- The brief is the ORION brief (`15-orion-alert-triage`, KYC/AML) re-skinned
  for aviation: tool registry, permission scoping, conflict resolution, audit
  trail. ORION's spec and code are the closest template.
- Cockpit conventions: uv with `package = false`, Python 3.11, ruff, pytest,
  specs under `docs/superpowers/specs/`, each project its own git repo nested
  in `projects/` and ignored by the cockpit repo.
- The folder name contains a non-breaking hyphen (U+2011) in `Multi‑Tool`, not
  an ASCII `-`. Typing the path with a normal hyphen fails. Flagged for the
  design.

**Classification: architectural.** New project, no existing flow to modify, so
the full process applies: questions → approaches → design in sections →
written spec → implementation plan. No code before the spec is approved.

**Key tension spotted:** the brief demands *deterministic workflows* and *real
parallel execution*. ORION's spike proved the Anthropic Tool Runner runs a
turn's tools one after another, and a model choosing tools is not
deterministic — so ORION's architecture can't be copied as-is.

### 2. Q: Who writes the implementation code?

Options offered: Claude builds and you review / ORION-style (you write the
core) / Split (you write only the safety logic).

**Answer: Split, with a twist.** Claude creates the files and the structure;
you put the code in and wire it up, with Claude directing and reviewing
("watching over your shoulder"). Every interaction is logged in this file.

**What that means in practice:**

- Claude writes the plumbing outright: scaffold, simulated tools, parallel
  executor, audit trail, report, CLI.
- For the safety logic — conflict resolver and decision rule — Claude creates
  each module with signatures, docstrings and failing tests; you write the
  implementation and wire it into the workflow; Claude reviews.
- Why this split works: the resolver and decision rule are where the brief's
  safety judgment lives ("safety overrides everything", "if conflict persists,
  escalate"). They are pure functions with table-driven tests — the best place
  to learn by writing, and the worst place to accept code you didn't reason
  through yourself.

### 3. Q: Where does Claude sit inside AERO?

Options offered: deterministic engine, no LLM (recommended) / engine + Claude
narrator / Claude plans the tool calls via Tool Runner (ORION's pattern).

**Answer: Engine + Claude narrator.**

**What that means:**

- The six phases, tool routing, parallel execution, conflict resolution and the
  decision all stay in code. Same alert in → same decision out.
- Claude writes the "Reasoning" prose at the very end. It sees the decision but
  cannot change it. Without an API key, the reasoning is the plain rule trace.

**Why the Tool Runner option was rejected:** the brief demands real parallel
execution and a deterministic workflow. ORION's spike showed the Tool Runner
executes a turn's tools one after another, and a model's choice of tools varies
from run to run. It would also repeat what ORION already taught — AERO teaches
the opposite pattern: workflow-driven orchestration instead of model-driven.

**Consequence to design for — the determinism boundary:** the decision, the rule
trace and the tool-call audit are reproducible; Claude's prose is not. So the
prose gets checked against the rule trace before it's used, its call is audited
like any other tool call, and it's excluded when testing that two runs match.

### 4. Design §1 — Architecture & components: approved

- Six phases run in a fixed order: Ingest → Diagnose → Comply → (Resolve +
  Decide) → Act → Report.
- **One executor for every tool call** — permission check, timeout, audit
  record. Putting all three in one place means no tool call can skip a
  guarantee; there's no second code path to forget.
- Who writes what: Claude writes the plumbing modules; **you write
  `conflict.py` and `decision.py`**, and wire them into `workflow.py` (Claude
  builds its skeleton and directs).
- Conventions: own local git repo in this folder; `src/aero/` package (HITL
  layout); Python 3.11; ruff from ORION; only runtime dependency is
  `anthropic` (for the narrator) — concurrency is stdlib `asyncio`.
- `notes.md` is gitignored as a personal log (Self-Reflective precedent).
- Folder rename to ASCII (e.g. `aero-maintenance-triage`) recommended; you'll do
  it when convenient since VS Code has the folder open.

### 5. Design §2 — Tools, routing & scenarios: approved

- **Every `ToolSpec` field has a runtime job** — `capability` (routing),
  `domains` (routing + which claims get compared), `permission` (default-deny
  gate), `safety_tier` (HIGH diagnostic fails → escalate; HIGH action → needs
  approval), `latency_budget` (timeout), `mutates` (only read-only tools run
  concurrently). A field with no job would be decoration, and decoration in a
  safety system reads as protection without being any.
- **Routing:** ATA chapter → domain (ATA 29 = hydraulics, etc.); each phase
  asks for capabilities; router returns matching tools sorted by name
  (deterministic). A required capability with no tool → escalate. Registering a
  tool at runtime makes the alert routable — that's the "dynamic" in dynamic
  routing.
- **Catalog:** one registry entry per *operation* (e.g. `dispatch.notify` vs
  `dispatch.set_aircraft_status`) so each entry carries exactly one permission.
  Covers all 17 tools in the brief, plus the Claude narrator.
- **Scenarios — one per decision, one per conflict rule:**
  A brake temp → R3 → CLEAR · B engine vibration via ACARS → R2 → SCHEDULE ·
  C hydraulic loss → R1 → GROUND (pending approval) · D aileron → R4 →
  ESCALATE · E APU with no tool → fail-closed routing.
- **Lesson carried over from ORION:** claims conflict only when they answer the
  *same question* (`condition`, `fault_source`, `dispatch`). ORION's flagship
  "conflict" was two true statements about different facts.

### 6. Design §3 — Safety semantics: approved (R1 = hard findings)

**The ambiguity in the brief, and how it was settled.** The brief's first
example conflict is "predictive model says high risk, telemetry says normal".
If rule 1 ("safety overrides everything") meant *any risk claim wins*, rule 3
could never decide the brief's own example. **Decision: R1 covers hard safety
findings** (the safety rules engine). Risk *estimates* are evidence, weighed by
R3. Overridden claims are never dropped — they stay in the report, marked with
the rule that overrode them. (Rejected alternative: an overridden risk claim
still forces SCHEDULE — more conservative, but then "overrides" wouldn't mean
anything.)

**Conflict rules — `conflict.py`, you write it** (applied in order per
disputed dimension): R1 safety_rule present → it wins · R2 regulatory vs
operational history → regulatory wins · R3 sensor (all ≥ 0.85) vs model (all
< 0.85) → sensor wins · R4 anything else → unresolved.

**Decision rule — `decision.py`, you write it** (first match wins):
D1 invalid alert → ESCALATE · D2 dispatch no_go → GROUND · D3 unresolved
conflict → ESCALATE · D4 HIGH-tier evidence gap → ESCALATE · D5 degraded /
failed / defer → SCHEDULE · D6 → CLEAR.
GROUND sits above ESCALATE: once a safety rule says no-go, an unresolved
disagreement elsewhere can only make things worse.

**Two gates, two questions:**
- Permission gate — "may AERO *ever* do this?" Default deny. AERO holds no
  `aircraft:ground` / `flight_plan:modify`, so it can't ground an aircraft even
  when a tool that can is registered.
- Approval gate — "AERO recommends it; a human signs off." Triggered by GROUND,
  HIGH-tier actions, regulatory-sensitive claims. One pending ticket per run;
  AERO never waits or resumes (HITL project already covers that).

**Fail closed:** HIGH-tier tool failure → ESCALATE; low/medium failure →
recorded gap; no retries in v1, because a failed call is missing evidence and
D4 already handles missing evidence safely.

### 7. Design §4-5 — Testing & phases: approved

**Testing strategy:** Every test runs offline, deterministic. Simulated tools with
scripted failures. Test-first: tests + signatures written before each module.
Conflict/decision rules (yours): one test per rule type, table-driven. One
end-to-end test per scenario (A/B/C/D/E), skipped by default (no token spend).

**Phases:**
0. Scaffold (uv + pyproject + ruff + fixtures + harness + README).
1. Plumbing (9 modules + CLI + all tests passing) — Claude writes it.
2. Safety logic — **you write `conflict.py` and `decision.py`**, wire them in.
3. Docs (spec commit, README, examples).
4. Git repo + CI.

Each phase: tests → review → commit. Hidden complexity upgrades the phase, not
compresses it.

**Out of scope v1:** real APIs (simulated for determinism), checkpoint/resume
(HITL already does it), batch (project 10), dashboard (project 8).
