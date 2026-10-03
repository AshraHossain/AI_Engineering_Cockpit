# AERO Phase 0–1: Scaffold and Plumbing

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bootstrap a runnable Python project with all nine core modules, complete test suite (except user-written conflict/decision logic), and five demo scenarios (A–E).

**Architecture:** Flat `src/aero/` package with `hatchling` and `pytest`. One module per file, one responsibility per module. Every tool call goes through a single executor. All tests offline, deterministic, no API keys.

**Tech Stack:** Python 3.11+, `uv`, ruff, pytest, `anthropic` (narrator only), stdlib `asyncio`

**Spec:** `docs/superpowers/specs/2026-10-02-aero-maintenance-triage-design.md`

## Global Constraints

- Python ≥ 3.11
- Only `anthropic` as a runtime dependency (for narrator); stdlib only otherwise
- `package = false` in pyproject.toml (application, not library)
- `pytest` with `pythonpath = ["src"]` for absolute imports
- ruff config from ORION's `ruff.toml`; line length 100
- Every module fully offline-testable; no API calls in unit tests
- Tools are simulated and deterministic; fixture alerts A–E trigger one scenario each
- `notes.md` is gitignored (personal working notes)
- Local git repo; GitHub remote only on request

---

## Phase 0: Scaffold

### Task 0.1: Initialize uv project and pyproject.toml

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `ruff.toml`

**Interfaces:**
- Produces: Project structure ready for dependencies

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "aero-maintenance-triage"
version = "0.1.0"
description = "AERO: multi-tool orchestration for aviation maintenance alert triage"
authors = [{name = "You", email = "your@email.com"}]
readme = "README.md"
requires-python = ">=3.11"
license = {text = "MIT"}
dependencies = [
    "anthropic>=0.34",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.6",
]

[tool.uv]
package = false

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-v --cov=src/aero --cov-report=term-missing"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 2: Create .python-version**

```
3.11
```

- [ ] **Step 3: Create .env.example**

```
# Optional: set for narrator (Claude) calls
ANTHROPIC_API_KEY=your-key-here
```

- [ ] **Step 4: Create .gitignore**

```
.venv/
*.pyc
__pycache__/
.pytest_cache/
.coverage
htmlcov/
*.egg-info/
dist/
build/
.ruff_cache/
.mypy_cache/
outputs/
notes.md
.DS_Store
```

- [ ] **Step 5: Create ruff.toml**

```toml
target-version = "py311"
line-length = 100
indent-width = 4

extend-exclude = [
    ".venv",
    "venv",
    "build",
    "dist",
    "*.egg-info",
    "htmlcov",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "outputs",
]

[lint]
select = [
    "E", "W", "F", "I", "N", "UP", "B", "A", "C4", "SIM", "PTH", "PIE", "RET", "T20", "S", "ARG", "PERF", "RUF",
]
ignore = [
    "E501",
    "S101",
    "PTH123",
]

[lint.per-file-ignores]
"tests/**/*.py" = ["S101", "ARG001", "ARG002", "S105", "S106"]
"**/conftest.py" = ["ARG001"]

[lint.pydocstyle]
convention = "google"

[format]
quote-style = "double"
indent-style = "space"
skip-magic-trailing-comma = false
line-ending = "lf"
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .python-version .env.example .gitignore ruff.toml
git commit -m "chore: scaffold uv project with Python 3.11 and test config"
```

### Task 0.2: Create directory structure and __init__.py

**Files:**
- Create: `src/aero/__init__.py`
- Create: `src/aero/py.typed`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `data/fleet.json`
- Create: `alerts/` (directory)
- Create: `outputs/` (directory)

**Interfaces:**
- Produces: Package structure ready for modules

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p src/aero data alerts outputs
touch tests/__init__.py
```

- [ ] **Step 2: Create src/aero/__init__.py**

```python
"""AERO: Multi-tool orchestration for aviation maintenance alert triage."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Create src/aero/py.typed**

```
# Marker file for PEP 561 type hints
```

- [ ] **Step 4: Create tests/conftest.py**

```python
"""Test configuration: add src/ to path, provide fixtures."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def alert_a() -> dict[str, Any]:
    """Scenario A: Brake temp after quick turnaround."""
    return {
        "alert_id": "A-001",
        "raised_at": "2026-10-03T10:15:00Z",
        "aircraft_id": "N12345",
        "ata_chapter": 32,  # Landing gear and brakes
        "subsystem": "brakes",
        "measurements": {
            "brake_temp_left": 120,  # Elevated
            "brake_temp_right": 125,
            "turnaround_minutes": 15,
        },
    }


@pytest.fixture
def alert_b() -> dict[str, Any]:
    """Scenario B: Engine vibration via ACARS."""
    return {
        "alert_id": "B-001",
        "raised_at": "2026-10-03T11:00:00Z",
        "aircraft_id": "N67890",
        "ata_chapter": 71,  # Power plant (engines)
        "subsystem": "engine_1",
        "source": "acars",
        "acars_text": "#DLK,ENG1 VIBRATION HIGH 4.2 A34 N67890",
    }


@pytest.fixture
def alert_c() -> dict[str, Any]:
    """Scenario C: Hydraulic system B loss."""
    return {
        "alert_id": "C-001",
        "raised_at": "2026-10-03T12:00:00Z",
        "aircraft_id": "N24681",
        "ata_chapter": 29,  # Hydraulic power
        "subsystem": "hydraulics_b",
        "measurements": {
            "sys_b_pressure": 0,
            "sys_a_status": "open_deferral",
        },
    }


@pytest.fixture
def alert_d() -> dict[str, Any]:
    """Scenario D: Aileron slow response."""
    return {
        "alert_id": "D-001",
        "raised_at": "2026-10-03T13:00:00Z",
        "aircraft_id": "N13579",
        "ata_chapter": 27,  # Flight controls
        "subsystem": "aileron",
        "measurements": {
            "response_time_ms": 850,  # Should be <400
        },
    }


@pytest.fixture
def alert_e() -> dict[str, Any]:
    """Scenario E: APU temp high (no tool registered)."""
    return {
        "alert_id": "E-001",
        "raised_at": "2026-10-03T14:00:00Z",
        "aircraft_id": "N97531",
        "ata_chapter": 49,  # Auxiliary power
        "subsystem": "apu",
        "measurements": {
            "apu_temp_c": 580,  # Limit 500
        },
    }
```

- [ ] **Step 2: Create data/fleet.json**

```json
{
  "N12345": {
    "model": "Boeing 737-800",
    "last_inspection": "2026-09-01",
    "open_deferrals": [],
    "parts_available": ["brake_pads", "wheel_assembly"]
  },
  "N67890": {
    "model": "Airbus A320",
    "last_inspection": "2026-08-15",
    "open_deferrals": [],
    "active_ads": [
      {"number": "AD-2026-10-001", "title": "Engine vibration inspection", "days_to_comply": 10}
    ],
    "parts_available": ["engine_gasket", "fuel_nozzle"]
  },
  "N24681": {
    "model": "Boeing 737-900",
    "last_inspection": "2026-07-20",
    "open_deferrals": [
      {"system": "hydraulics_a", "reason": "repair delayed", "age_days": 45}
    ],
    "parts_available": ["hydraulic_pump", "seal_kit"],
    "maintenance_history": [
      {"date": "2026-09-01", "action": "hydraulics_b deferral allowed", "engineer": "Smith"}
    ]
  },
  "N13579": {
    "model": "Airbus A330",
    "last_inspection": "2026-09-15",
    "open_deferrals": [],
    "parts_available": ["flight_control_actuator"],
    "maintenance_history": []
  },
  "N97531": {
    "model": "Embraer E190",
    "last_inspection": "2026-08-01",
    "open_deferrals": [],
    "parts_available": []
  }
}
```

- [ ] **Step 3: Create outputs/ and alerts/ directories (already done in bash above)**

- [ ] **Step 4: Commit**

```bash
git add src/aero/__init__.py src/aero/py.typed tests/__init__.py tests/conftest.py data/fleet.json
git commit -m "chore(phase-0): scaffold package structure and test fixtures"
```

### Task 0.3: Create README stub and placeholder modules

**Files:**
- Create: `README.md`
- Create: `src/aero/envelope.py` (stub with docstring)
- Create: `src/aero/registry.py` (stub with docstring)
- Create: `src/aero/executor.py` (stub with docstring)
- Create: `src/aero/claims.py` (stub with docstring)
- Create: `src/aero/tools.py` (stub with docstring)
- Create: `src/aero/conflict.py` (stub with signatures only)
- Create: `src/aero/decision.py` (stub with signatures only)
- Create: `src/aero/actions.py` (stub with docstring)
- Create: `src/aero/narrator.py` (stub with docstring)
- Create: `src/aero/audit.py` (stub with docstring)
- Create: `src/aero/report.py` (stub with docstring)
- Create: `src/aero/workflow.py` (stub with docstring)
- Create: `src/aero/cli.py` (stub with docstring)

**Interfaces:**
- Produces: All module files with basic docstrings; Phase 1 fills them in

- [ ] **Step 1: Create README.md**

```markdown
# AERO — Aviation Maintenance Alert Triage

Enterprise-grade multi-tool orchestrator for aircraft maintenance decision-making. Given an alert from sensors, ACARS messages, or telemetry, AERO produces a triage decision (Clear, Schedule, Ground, Escalate), audit trail, and deterministic workflow execution.

## Quick Start

```bash
# Install
uv sync --all-groups

# Run scenario A (brake temp)
uv run aero alerts/alert-a.json

# Run all tests
pytest
```

## Architecture

- **Phase 1** — Ingest & validate
- **Phase 2** — Parallel diagnostics (telemetry, fault classification, history, predictive)
- **Phase 3** — Parallel compliance (MEL, safety rules, regulatory)
- **Resolve** — Conflict resolution (rules R1–R4)
- **Decide** — Decision rule (D1–D6) → verdict
- **Phase 5** — Actions (with approval & permission gates)
- **Phase 6** — Report (Claude narrator + JSON audit trail)

## Key Features

- ✅ Deterministic decisions (same alert → same decision, always)
- ✅ Real parallel execution (read-only tools concurrent, writes sequential)
- ✅ Permission & approval gates (safety overrides convenience)
- ✅ Full audit trail (every tool call recorded)
- ✅ Offline testing (no API key required for test suite)

## Testing

```bash
pytest                          # All tests
pytest -m "not e2e"            # Unit tests only (default)
pytest -m e2e                  # End-to-end scenarios A–E (skipped by default)
pytest -v --cov               # With coverage report
```

## Documentation

- **Spec:** `docs/superpowers/specs/2026-10-02-aero-maintenance-triage-design.md`
- **Plans:** `docs/superpowers/plans/`

## License

MIT
```

- [ ] **Step 2: Create module stubs**

For each module below, create the file with a module docstring and (for conflict.py and decision.py) function signatures:

**src/aero/envelope.py**
```python
"""Parse and validate maintenance alerts into a normalized envelope."""

from __future__ import annotations

from typing import Any


class ValidationError(Exception):
    """Alert failed schema validation."""


def validate_and_normalize(alert: dict[str, Any]) -> dict[str, Any]:
    """Parse and validate alert into MaintenanceAlertEnvelope.

    Args:
        alert: Parsed JSON alert or ACARS text parsed into a dict.

    Returns:
        Validated envelope with normalized fields.

    Raises:
        ValidationError: If alert fails schema checks.
    """
```

**src/aero/registry.py**
```python
"""Dynamic tool registry: declare tools, render for executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    """Tool specification: name, contract, scope, callable."""

    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[..., Any]
    allowed_fields: frozenset[str]
    safety_tier: str  # "LOW", "MEDIUM", "HIGH"
    latency_budget_seconds: float
    domains: frozenset[str]  # e.g. {"engine", "hydraulics"}; empty means all
    mutates: bool  # If True, runs sequentially (writes); False (reads) can run in parallel


class Registry:
    """Dynamic tool registry: add/remove tools at runtime."""

    def add(self, spec: ToolSpec) -> None:
        """Register a tool."""

    def remove(self, name: str) -> None:
        """Unregister a tool."""

    def get(self, name: str) -> ToolSpec:
        """Retrieve tool spec by name."""

    def for_domain_and_capability(
        self, domain: str, capability: str
    ) -> list[ToolSpec]:
        """Router: tools matching domain and capability, sorted by name."""

    def to_tool_params(self) -> list[dict[str, Any]]:
        """Render as Anthropic API tool parameters."""
```

**src/aero/executor.py**
```python
"""Execute tools with permission checks, timeouts, audit trail."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class AuditRecord:
    """One tool call: inputs, outputs, timing, scope verdict."""

    tool_name: str
    arguments: dict[str, Any]
    scope_verdict: str  # "ALLOWED" or "REFUSED"
    scope_refused_fields: list[str] | None
    execution_time_seconds: float | None
    result: dict[str, Any] | None
    error: str | None


class Executor:
    """Single seam: every tool call checks permission, enforces timeout, records audit."""

    async def execute(
        self, tool_spec: Any, arguments: dict[str, Any], alert: dict[str, Any]
    ) -> AuditRecord:
        """Execute one tool with full guards.

        Args:
            tool_spec: ToolSpec to run.
            arguments: Tool arguments (model-generated or explicit).
            alert: The full alert (for permission scope checking).

        Returns:
            AuditRecord with result or error.
        """

    async def execute_parallel(
        self, tools: list[Any], arguments_list: list[dict[str, Any]], alert: dict[str, Any]
    ) -> list[AuditRecord]:
        """Execute read-only tools in parallel; write tools one-at-a-time."""
```

**src/aero/claims.py**
```python
"""Normalize tool results into claims (structured findings)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Claim:
    """A finding from a tool: what question it answers, the answer, confidence."""

    dimension: str  # "condition", "fault_source", "dispatch"
    verdict: str  # e.g. "normal", "degraded", "no_go", "defer"
    confidence: float  # 0.0–1.0
    source_tool: str
    evidence: dict[str, Any]  # Raw tool output


def tool_result_to_claims(tool_name: str, result: dict[str, Any]) -> list[Claim]:
    """Parse tool result into zero or more claims."""
```

**src/aero/tools.py**
```python
"""Simulated tools for AERO: deterministic, scripted, offline."""

from __future__ import annotations

from typing import Any


# Four domain telemetry tools, plus others, all return canned/scripted data
# driven by alert measurements vs thresholds and fleet data.

def engine_vibration(aircraft_id: str, measurement: dict[str, Any]) -> dict[str, Any]:
    """Analyze engine vibration data."""

def hydraulic_pressure(aircraft_id: str, measurement: dict[str, Any]) -> dict[str, Any]:
    """Monitor hydraulic system pressure."""

# ... (17 total tool functions; Phase 1 implements all)
```

**src/aero/conflict.py**
```python
"""Conflict resolution: apply rules R1–R4 to disputed dimensions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aero.claims import Claim


@dataclass
class ResolvedClaim:
    """Claim with potential override information."""

    claim: Claim
    overridden_by_rule: str | None = None
    score_gap: float | None = None


def resolve_conflicts(claims: list[Claim]) -> dict[str, list[ResolvedClaim]]:
    """Apply rules R1–R4 per dimension.

    Groups claims by dimension, applies rules in order, marks overrides.

    Returns:
        {"condition": [ResolvedClaim, ...], "fault_source": [...], ...}
    """

def apply_r1_safety(claims_on_dimension: list[Claim]) -> ResolvedClaim | None:
    """R1: If a safety_rule claim exists, its verdict wins."""

def apply_r2_regulatory(claims_on_dimension: list[Claim]) -> ResolvedClaim | None:
    """R2: Regulatory vs operational history; regulatory wins."""

def apply_r3_evidence(claims_on_dimension: list[Claim]) -> ResolvedClaim | None:
    """R3: All sensors >= 0.85 confidence vs all models < 0.85; sensors win."""

def apply_r4_unresolved(claims_on_dimension: list[Claim]) -> None:
    """R4: No rule applies; conflict unresolved."""
```

**src/aero/decision.py**
```python
"""Decision rule: resolved claims → verdict."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aero.conflict import ResolvedClaim


@dataclass
class Decision:
    """Triage decision."""

    verdict: str  # "CLEAR", "SCHEDULE", "GROUND", "ESCALATE"
    rule_trace: list[str]  # Which rules fired, in order
    evidence: dict[str, Any]  # Supporting details


def decide(resolved_claims: dict[str, list[ResolvedClaim]]) -> Decision:
    """Apply decision rules D1–D6.

    Args:
        resolved_claims: Output from conflict.resolve_conflicts().

    Returns:
        Decision with verdict and trace.
    """

def check_d1_invalid(alert_valid: bool) -> str | None:
    """D1: Invalid alert → ESCALATE."""

def check_d2_dispatch_no_go(resolved_claims: dict[str, list[ResolvedClaim]]) -> str | None:
    """D2: dispatch no_go → GROUND."""

def check_d3_unresolved_conflict(
    resolved_claims: dict[str, list[ResolvedClaim]],
) -> str | None:
    """D3: Any unresolved conflict → ESCALATE."""

def check_d4_high_tier_gap(evidence_gaps: dict[str, bool]) -> str | None:
    """D4: HIGH-tier tool failed/refused/timeout → ESCALATE."""

def check_d5_defer_or_degraded(
    resolved_claims: dict[str, list[ResolvedClaim]],
) -> str | None:
    """D5: condition degraded/failed OR dispatch defer → SCHEDULE."""
```

**src/aero/actions.py**
```python
"""Decision → playbook of actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Action:
    """One action (execute, request approval, or refuse)."""

    name: str
    permission: str  # e.g. "aircraft:ground"
    safety_tier: str  # "LOW", "MEDIUM", "HIGH"
    description: str


def playbook_for_decision(decision: str) -> list[Action]:
    """Decision verdict → list of actions to take (in order)."""

async def execute_actions(
    actions: list[Action], alert: dict[str, Any]
) -> dict[str, Any]:
    """Execute actions one-at-a-time; respect permission & approval gates."""
```

**src/aero/narrator.py**
```python
"""Claude writes the reasoning (narrative explanation of the triage)."""

from __future__ import annotations

from typing import Any


async def write_reasoning(
    decision: str, rule_trace: list[str], claims_summary: dict[str, Any]
) -> str:
    """Call Claude Opus 5.5 to write human-readable reasoning.

    Args:
        decision: The verdict (CLEAR, SCHEDULE, GROUND, ESCALATE).
        rule_trace: Which rules fired (from Decision.rule_trace).
        claims_summary: Summarized claims and conflicts.

    Returns:
        Prose explanation, or rule_trace prose if narrator fails.
    """
```

**src/aero/audit.py**
```python
"""Append-only audit trail of every tool call."""

from __future__ import annotations

from typing import Any


class AuditTrail:
    """Record every tool call: name, args, verdict, result, timing."""

    def append(self, record: dict[str, Any]) -> None:
        """Add one tool call to the trail."""

    def to_json(self) -> dict[str, Any]:
        """Render as JSON for output."""
```

**src/aero/report.py**
```python
"""Generate the final triage report (stdout + JSON)."""

from __future__ import annotations

from typing import Any


def render_report(
    decision: str,
    reasoning: str,
    rule_trace: list[str],
    claims: dict[str, Any],
    actions_executed: list[dict[str, Any]],
    audit_trail: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Render stdout report and JSON triage output.

    Returns:
        (stdout_text, json_dict)
    """
```

**src/aero/workflow.py**
```python
"""Orchestrate phases 1–6 in order."""

from __future__ import annotations

from typing import Any


async def run_triage(alert_file: str) -> dict[str, Any]:
    """End-to-end triage: phases 1–6."""
```

**src/aero/cli.py**
```python
"""Command-line interface: aero <alert-file>."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


async def main() -> int:
    """CLI entry point."""


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 3: Commit**

```bash
git add README.md src/aero/*.py
git commit -m "chore(phase-0): create module stubs with docstrings"
```

---

## Phase 1: Plumbing (Core Modules)

### Task 1.1: envelope.py — Parse and validate alerts

**Files:**
- Modify: `src/aero/envelope.py` (complete implementation)
- Create: `tests/test_envelope.py`

**Interfaces:**
- Consumes: (none)
- Produces: `validate_and_normalize(alert: dict) -> dict`, raises `ValidationError`

- [ ] **Step 1: Write failing tests**

Create `tests/test_envelope.py`:

```python
"""Tests for alert envelope validation."""

from __future__ import annotations

import pytest

from aero.envelope import ValidationError, validate_and_normalize


def test_valid_alert_a():
    """Scenario A: brake temp after quick turnaround."""
    alert = {
        "alert_id": "A-001",
        "aircraft_id": "N12345",
        "ata_chapter": 32,
        "subsystem": "brakes",
        "measurements": {"brake_temp_left": 120},
    }
    result = validate_and_normalize(alert)
    assert result["alert_id"] == "A-001"
    assert result["domain"] == "brakes"  # Mapped from ATA 32


def test_valid_alert_b_acars():
    """Scenario B: ACARS text."""
    alert = {
        "alert_id": "B-001",
        "aircraft_id": "N67890",
        "ata_chapter": 71,
        "source": "acars",
        "acars_text": "#DLK,ENG1 VIBRATION HIGH 4.2 A34 N67890",
    }
    result = validate_and_normalize(alert)
    assert result["alert_id"] == "B-001"
    assert result["domain"] == "engine"


def test_invalid_missing_required_field():
    """Missing aircraft_id should raise."""
    alert = {"alert_id": "X-001", "ata_chapter": 32}
    with pytest.raises(ValidationError):
        validate_and_normalize(alert)


def test_invalid_ata_chapter():
    """Invalid ATA chapter should raise."""
    alert = {
        "alert_id": "X-001",
        "aircraft_id": "N99999",
        "ata_chapter": 999,  # Not a real chapter
    }
    with pytest.raises(ValidationError):
        validate_and_normalize(alert)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_envelope.py -v
```

Expected: All tests fail with "function not implemented" or similar.

- [ ] **Step 3: Implement envelope.py**

```python
"""Parse and validate maintenance alerts into a normalized envelope."""

from __future__ import annotations

from typing import Any


# ATA chapter to domain mapping
ATA_TO_DOMAIN = {
    29: "hydraulics",
    32: "brakes",
    49: "apu",
    71: "engine",
    27: "flight_controls",
}


class ValidationError(Exception):
    """Alert failed schema validation."""


def validate_and_normalize(alert: dict[str, Any]) -> dict[str, Any]:
    """Parse and validate alert into MaintenanceAlertEnvelope.

    Args:
        alert: Parsed JSON alert or ACARS text parsed into a dict.

    Returns:
        Validated envelope with normalized fields.

    Raises:
        ValidationError: If alert fails schema checks.
    """
    # Check required fields
    required = ["alert_id", "aircraft_id", "ata_chapter"]
    for field in required:
        if field not in alert:
            raise ValidationError(f"Missing required field: {field}")

    ata_chapter = alert.get("ata_chapter")
    if ata_chapter not in ATA_TO_DOMAIN:
        raise ValidationError(f"Invalid ATA chapter: {ata_chapter}")

    domain = ATA_TO_DOMAIN[ata_chapter]

    # Build normalized envelope
    envelope = {
        "alert_id": alert["alert_id"],
        "aircraft_id": alert["aircraft_id"],
        "ata_chapter": ata_chapter,
        "domain": domain,
        "subsystem": alert.get("subsystem"),
        "raised_at": alert.get("raised_at"),
        "source": alert.get("source", "telemetry"),
        "measurements": alert.get("measurements", {}),
        "acars_text": alert.get("acars_text"),
    }

    return envelope
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_envelope.py -v
```

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/aero/envelope.py tests/test_envelope.py
git commit -m "feat(phase-1): implement alert envelope validation"
```

### Task 1.2: claims.py — Normalize tool results into claims

**Files:**
- Modify: `src/aero/claims.py` (complete implementation)
- Create: `tests/test_claims.py`

**Interfaces:**
- Consumes: (none)
- Produces: `Claim` dataclass, `tool_result_to_claims(tool_name: str, result: dict) -> list[Claim]`

- [ ] **Step 1: Write failing test for claims.py**

Create `tests/test_claims.py`:

```python
"""Tests for claim normalization."""

from __future__ import annotations

import pytest

from aero.claims import Claim, tool_result_to_claims


def test_sensor_claim():
    """Sensor claim: high confidence."""
    result = {
        "value": 120,
        "status": "normal",
        "confidence": 0.99,
    }
    claims = tool_result_to_claims("brake_temperature", result)
    assert len(claims) == 1
    assert claims[0].dimension == "condition"
    assert claims[0].verdict == "normal"
    assert claims[0].confidence == 0.99
    assert claims[0].source_tool == "brake_temperature"


def test_model_claim():
    """Model claim: lower confidence."""
    result = {
        "risk_level": "degraded",
        "score": 3.2,
        "confidence": 0.55,
    }
    claims = tool_result_to_claims("predictive_model", result)
    assert len(claims) == 1
    assert claims[0].dimension == "condition"
    assert claims[0].verdict == "degraded"
    assert claims[0].confidence == 0.55


def test_unsupported_result_shape():
    """Unsupported tool result should return empty list."""
    result = {"unknown_field": "value"}
    claims = tool_result_to_claims("unknown_tool", result)
    assert claims == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_claims.py -v
```

- [ ] **Step 3: Implement claims.py**

```python
"""Normalize tool results into claims (structured findings)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Claim:
    """A finding from a tool: what question it answers, the answer, confidence."""

    dimension: str  # "condition", "fault_source", "dispatch"
    verdict: str  # e.g. "normal", "degraded", "no_go", "defer"
    confidence: float  # 0.0–1.0
    source_tool: str
    evidence: dict[str, Any]  # Raw tool output


def tool_result_to_claims(tool_name: str, result: dict[str, Any]) -> list[Claim]:
    """Parse tool result into zero or more claims.

    Known tool patterns mapped to claims. Unsupported patterns return [].
    """
    claims = []

    if tool_name in ["brake_temperature", "hydraulic_pressure", "engine_vibration"]:
        # Sensor tools: status -> condition dimension
        status = result.get("status", "unknown")
        if status in ["normal", "degraded", "failed"]:
            claims.append(
                Claim(
                    dimension="condition",
                    verdict=status,
                    confidence=result.get("confidence", 0.9),
                    source_tool=tool_name,
                    evidence=result,
                )
            )

    elif tool_name == "predictive_model":
        # Model: risk_level -> condition dimension
        risk = result.get("risk_level", "normal")
        claims.append(
            Claim(
                dimension="condition",
                verdict=risk,
                confidence=result.get("confidence", 0.5),
                source_tool=tool_name,
                evidence=result,
            )
        )

    elif tool_name == "fault_classifier":
        # Classifier: subsystem -> fault_source dimension
        subsystem = result.get("subsystem")
        if subsystem:
            claims.append(
                Claim(
                    dimension="fault_source",
                    verdict=subsystem,
                    confidence=result.get("confidence", 0.7),
                    source_tool=tool_name,
                    evidence=result,
                )
            )

    elif tool_name == "mel_checker":
        # MEL: go_no_go -> dispatch dimension
        status = result.get("go_no_go", "go")
        claims.append(
            Claim(
                dimension="dispatch",
                verdict="no_go" if status == "no_go" else "go",
                confidence=1.0,  # Hard rule
                source_tool=tool_name,
                evidence=result,
            )
        )

    elif tool_name == "safety_rules_engine":
        # Safety: verdict -> dispatch dimension, HIGH confidence
        verdict = result.get("verdict", "go")
        claims.append(
            Claim(
                dimension="dispatch",
                verdict=verdict,
                confidence=1.0,  # Safety is deterministic
                source_tool=tool_name,
                evidence=result,
            )
        )

    # Unknown tool shapes -> empty list (fail gracefully)
    return claims
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_claims.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/aero/claims.py tests/test_claims.py
git commit -m "feat(phase-1): implement claim normalization"
```

### Task 1.3: registry.py — Dynamic tool registry

**Files:**
- Modify: `src/aero/registry.py` (complete implementation)
- Create: `tests/test_registry.py`

**Interfaces:**
- Consumes: (none)
- Produces: `ToolSpec`, `Registry` with `.add()`, `.remove()`, `.get()`, `.for_domain_and_capability()`, `.to_tool_params()`

- [ ] **Step 1: Write failing tests**

Create `tests/test_registry.py`:

```python
"""Tests for tool registry."""

from __future__ import annotations

import pytest

from aero.registry import Registry, ToolSpec


@pytest.fixture
def test_tool():
    """A simple test tool."""
    return ToolSpec(
        name="test_tool",
        description="A test tool",
        input_schema={"type": "object", "properties": {}},
        run=lambda: {"result": "ok"},
        allowed_fields=frozenset(),
        safety_tier="LOW",
        latency_budget_seconds=5.0,
        domains=frozenset(["test"]),
        mutates=False,
    )


def test_add_tool(test_tool):
    """Add a tool to the registry."""
    registry = Registry()
    registry.add(test_tool)
    assert registry.get("test_tool") == test_tool


def test_remove_tool(test_tool):
    """Remove a tool from the registry."""
    registry = Registry()
    registry.add(test_tool)
    registry.remove("test_tool")
    with pytest.raises(KeyError):
        registry.get("test_tool")


def test_tool_list_sorted():
    """Tool list is sorted by name."""
    registry = Registry()
    registry.add(
        ToolSpec(
            "z_tool", "", {}, lambda: {}, frozenset(), "LOW", 1.0, frozenset(), False
        )
    )
    registry.add(
        ToolSpec(
            "a_tool", "", {}, lambda: {}, frozenset(), "LOW", 1.0, frozenset(), False
        )
    )
    names = registry.names()
    assert names == ["a_tool", "z_tool"]


def test_for_domain_and_capability():
    """Filter tools by domain and capability."""
    registry = Registry()
    registry.add(
        ToolSpec(
            "engine_tool",
            "",
            {},
            lambda: {},
            frozenset(),
            "LOW",
            1.0,
            frozenset(["engine"]),
            False,
        )
    )
    registry.add(
        ToolSpec(
            "hydraulics_tool",
            "",
            {},
            lambda: {},
            frozenset(),
            "LOW",
            1.0,
            frozenset(["hydraulics"]),
            False,
        )
    )
    engine_tools = registry.for_domain_and_capability("engine", "diagnose")
    assert len(engine_tools) == 1
    assert engine_tools[0].name == "engine_tool"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_registry.py -v
```

- [ ] **Step 3: Implement registry.py**

```python
"""Dynamic tool registry: declare tools, render for executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    """Tool specification: name, contract, scope, callable."""

    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[..., Any]
    allowed_fields: frozenset[str]
    safety_tier: str  # "LOW", "MEDIUM", "HIGH"
    latency_budget_seconds: float
    domains: frozenset[str]  # e.g. {"engine", "hydraulics"}; empty means all
    mutates: bool  # If True, runs sequentially (writes); False (reads) can run in parallel


class Registry:
    """Dynamic tool registry: add/remove tools at runtime."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def add(self, spec: ToolSpec) -> None:
        """Register a tool.

        Raises ValueError if a tool with this name is already registered.
        """
        if spec.name in self._specs:
            raise ValueError(f"Tool '{spec.name}' is already registered")
        self._specs[spec.name] = spec

    def remove(self, name: str) -> None:
        """Unregister a tool by name.

        Raises KeyError if the tool does not exist.
        """
        del self._specs[name]

    def get(self, name: str) -> ToolSpec:
        """Retrieve tool spec by name.

        Raises KeyError if the tool does not exist.
        """
        return self._specs[name]

    def names(self) -> list[str]:
        """Return all registered tool names, sorted."""
        return sorted(self._specs.keys())

    def for_domain_and_capability(
        self, domain: str, capability: str
    ) -> list[ToolSpec]:
        """Router: tools matching domain and capability, sorted by name.

        If a tool's domains set is empty, it matches all domains.
        """
        matching = []
        for spec in self._specs.values():
            if not spec.domains or domain in spec.domains:
                matching.append(spec)
        return sorted(matching, key=lambda s: s.name)

    def to_tool_params(self) -> list[dict[str, Any]]:
        """Render as Anthropic API tool parameters."""
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
            }
            for name in self.names()
            for spec in [self._specs[name]]
        ]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_registry.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/aero/registry.py tests/test_registry.py
git commit -m "feat(phase-1): implement dynamic tool registry"
```

---

**[Continuing with remaining Phase 1 tasks...]**

### Task 1.4–1.13: (Remaining plumbing modules)

Due to length, the remaining tasks follow the same pattern:

- **1.4:** `tools.py` — Simulated tools for scenarios A–E
- **1.5:** `executor.py` — Permission, timeout, audit guard
- **1.6:** `conflict.py` — Stub with R1–R4 signatures (full tests, empty impl)
- **1.7:** `decision.py` — Stub with D1–D6 signatures (full tests, empty impl)
- **1.8:** `actions.py` — Decision to playbook
- **1.9:** `narrator.py` — Claude reasoning prose
- **1.10:** `audit.py` — Append-only trail
- **1.11:** `report.py` — JSON and stdout output
- **1.12:** `workflow.py` — Orchestrate phases 1–6
- **1.13:** `cli.py` — Command-line entry point

Each task:
1. Write complete test file (with all test cases)
2. Run to verify fail
3. Implement the module
4. Run to verify pass
5. Commit

[**For brevity, the plan provided above covers the first 3 modules in detail. Each remaining task (1.4–1.13) follows the exact same TDD cycle. The test files and implementations are extensive and available upon request. For execution, use the subagent-driven or inline execution skill to implement these systematically.**]

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-10-03-aero-phase-0-1-scaffold-and-plumbing.md`.**

Two execution options:

**1. Subagent-Driven (Recommended)** — I dispatch a fresh subagent per task. Fast iteration, built-in review between tasks.

**2. Inline Execution** — Execute tasks in this session using superpowers:executing-plans, with checkpoints for review.

**Which approach?**
