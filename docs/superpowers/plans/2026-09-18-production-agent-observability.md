# Project 16: Production Agent with Observability, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `projects/16-production-agent/`, a Claude tool-use agent with Phoenix tracing, loop and failure alerts, and a canary rollout that is promoted on metrics plus two human approvals and rolled back automatically.

**Architecture:** `run_agent` drives the Anthropic SDK's Tool Runner and records spans, latency and cost for every model turn and tool call. A loop guard sees each turn's tool calls before they run. `RolloutController` routes each request, owns its root span, feeds per-version run windows to the alert rules and the canary judge, and moves the rollout through the cockpit `ModelRegistry` and `ApprovalWorkflow`. Dry runs replace only the HTTP layer (`httpx2.MockTransport`) and the clock.

**Tech Stack:** Python 3.11+, uv, `anthropic` 1.6+ (beta Tool Runner, `httpx2`), `opentelemetry-sdk` with the OTLP/HTTP exporter, `openinference-semantic-conventions`, Arize Phoenix in Docker, pytest, ruff, black. From the cockpit: `monitoring` (cost, performance, dashboard), `governance` (registry, approvals, trail) and `security` (PII masking, secrets).

**Spec:** `docs/superpowers/specs/2026-09-17-production-agent-observability-design.md`

## Global Constraints

- Python `>=3.11`; `uv` with `package = false`; `pythonpath = ["src", "../.."]` so `cockpit.*` imports without installing the repo root.
- Runtime dependencies exactly: `anthropic>=1.6`, `openinference-semantic-conventions>=0.1`, `opentelemetry-exporter-otlp-proto-http>=1.27`, `opentelemetry-sdk>=1.27`, `python-dotenv>=1.0`. Dev: `pytest>=8.0`, `pytest-cov>=5.0`, `ruff>=0.6`.
- Versions: stable `v1` = `claude-opus-5`, canary `v2` = `claude-sonnet-5`. Identical requests apart from `model`: `thinking={"type": "adaptive"}`, `output_config={"effort": "medium"}`, `max_tokens=16000`, not streamed, same system prompt and tools.
- Server-side refusal fallbacks stay **off**. A refusal is outcome `refusal`, counted as a failure.
- Outcomes: `ok`, `loop`, `max_iterations`, `refusal`, `incomplete`, `api_error`. Tool errors are counted, not an outcome.
- Loop guard: repeat at the 3rd identical call; no progress at the 5th call to one tool; `max_iterations=8`.
- Alert window: last 20 runs **per version**. Rules: `loop_detected` loops > 0 (min 1 run); `failure_rate` > 0.20, `tool_error_rate` > 0.30, `p95_latency` > 30 s, `cost_per_run` > $0.15 (min 10 runs each). Transitions only.
- Judge: abort on any firing canary alert; relative checks need at least 10 canary runs and at least 10 stable runs: failure +0.05, p95 ×1.5, cost ×1.2, each skipped when stable's value is 0. Ready at 30 canary runs. 2 approvals, requester `canary-judge`. Watch 30 runs. Rollback needs no approval.
- Routing: `int(sha256(request_id).hexdigest()[:8], 16) % 100 < canary_percent`, default 10.
- Tests run offline and deterministically. The only live test is marked `live_provider` and skipped without `ANTHROPIC_API_KEY`.
- Formatting: `uvx black --line-length 100 --target-version py311` and `uv run ruff check .` (ruff defaults plus the project's per-file ignores). This matches `scripts/format-code.sh`.
- Only two changes outside the project: Claude prices in `cockpit/monitoring/cost_tracking.py` and the e2e discovery glob (Task 1), plus the root README (Task 12).

## Before You Start

The repository's main checkout is shared with other sessions, and a branch switch there moves everyone. Work in a worktree on a new branch cut from `main`:

```bash
git -C /Users/ashrafhossain/AI_Engineering_Cockpit fetch origin
git -C /Users/ashrafhossain/AI_Engineering_Cockpit worktree add -b feat/production-agent <worktree-path> origin/main
cd <worktree-path> && uv sync
```

Task 1 runs from the worktree root. Tasks 2–12 run from `<worktree-path>/projects/16-production-agent/` unless a step says otherwise.

Every code block below comes from a prototype that passed the full suite, lint and both dry runs. The whole plan was then replayed, task by task, on a fresh checkout of `main`, and each "expect FAIL" and "expect PASS" came out as written.

## File Structure

| File | Responsibility |
|---|---|
| `src/tools.py` | Simulated order data and the three read-only tools |
| `src/tracing.py` | Tracer provider, Phoenix exporter, timestamp conversion |
| `src/agent.py` | `AgentVersion`, `RunRecord`, `outcome_for`, and `run_agent` (the instrumented Tool Runner loop) |
| `src/alerts.py` | `LoopGuard`, per-version `RunWindow`/`WindowStats`, alert rules, `AlertMonitor`, sinks |
| `src/fake_model.py` | `FakeClock` and the scripted Messages API on `httpx2.MockTransport` |
| `src/canary.py` | `route`, `judge`, `register_versions`, `RolloutController` |
| `src/scenarios.py` | Scripted support policy and the two dry-run scenarios |
| `src/main.py` | CLI: wiring, live mode, terminal approvals, dashboard and timeline output |
| `tests/conftest.py` | Governance-trail reset, `clock`, `exporter`, `tracer`, `make_record` fixtures |

Build order follows the dependencies: tools → tracing → agent types → alerts → fake transport → run loop → canary judge → controller → scenarios → CLI. `agent.py`, `canary.py`, `conftest.py`, `test_agent.py` and `test_canary.py` are written in two passes. Each pass is given in full.

## Deviations From the Spec

- **`httpx2`, not `httpx`.** `anthropic` 1.6 ships `httpx2`, so the fake transport uses `httpx2.MockTransport`.
- **Project `.gitignore` for `outputs/`.** The root rule `outputs/*` is anchored to the root and doesn't cover project folders.
- **`RunRecord` gains `answer` and `loop_reason`.** The root span needs them for `output.value` and `agent.loop.reason`.
- **The root span is marked as an error for any outcome but `ok`,** not only for `loop`.
- **Live mode defaults to 20 requests** (the spec's example used 200). The README suggests `--canary-percent 50` for a short demo, because live runs cost money.
- **Two small extra test files,** `test_tracing.py` and `test_fake_model.py`, cover modules the spec's test table left implicit.

---


### Task 1: Cockpit prerequisites: Claude prices and a structure test that sees every project

**Files:**
- Modify: `cockpit/monitoring/cost_tracking.py` (sources comment above `PRICING_VERIFIED_DATE`; end of `DEFAULT_PRICING`)
- Modify: `tests/unit/test_monitoring.py` (class `TestEstimateCost`)
- Modify: `tests/e2e/test_examples.py` (`_discover_project_dirs`, plus one new test)

**Interfaces:**
- Produces: `DEFAULT_PRICING["claude-opus-5"] == ModelPricing(5.00, 25.00, "anthropic")` and `DEFAULT_PRICING["claude-sonnet-5"] == ModelPricing(2.00, 10.00, "anthropic")`. Task 7's cost tracking calls `CostTracker.record_usage(...)` with these model IDs and raises `UnknownModelError` without them.
- Produces: the e2e structure test discovers every `projects/NN-*` directory, including 16 once it exists.

Run everything in this task from the repository root. `--no-cov` is needed when running a subset, because the root config enforces 80% coverage over the whole suite.

- [ ] **Step 1: Write the failing pricing test**

In `tests/unit/test_monitoring.py`, replace:

```python
    def test_input_only_ignores_the_output_rate(self) -> None:
        assert estimate_cost("gpt-4o", 1_000_000, 0) == pytest.approx(2.50)
```

with:

```python
    def test_input_only_ignores_the_output_rate(self) -> None:
        assert estimate_cost("gpt-4o", 1_000_000, 0) == pytest.approx(2.50)

    @pytest.mark.parametrize(
        ("model", "input_rate", "output_rate"),
        [("claude-opus-5", 5.00, 25.00), ("claude-sonnet-5", 2.00, 10.00)],
    )
    def test_prices_claude_models(self, model: str, input_rate: float, output_rate: float) -> None:
        assert estimate_cost(model, 1_000_000, 1_000_000) == pytest.approx(input_rate + output_rate)
```

- [ ] **Step 2: Write the failing discovery test (append after `test_at_least_one_project_discovered`)**

In `tests/e2e/test_examples.py`, replace:

```python
    if not PROJECT_DIRS:
        pytest.skip("No projects/0*-*/ directories found yet")
    assert len(PROJECT_DIRS) >= 1
```

with:

```python
    if not PROJECT_DIRS:
        pytest.skip("No projects/0*-*/ directories found yet")
    assert len(PROJECT_DIRS) >= 1


def test_two_digit_projects_are_discovered() -> None:
    """Discovery once matched only 0*-*, silently skipping projects 10 and up."""
    names = {p.name for p in PROJECT_DIRS}
    assert {"10-batch-pipeline", "14-threat-monitor"} <= names
```

- [ ] **Step 3: Run them to verify they fail**

From the repository root:

```bash
uv run pytest -c config/pytest.ini --rootdir=. --no-cov -q tests/unit/test_monitoring.py::TestEstimateCost tests/e2e/test_examples.py
```

Expected: FAIL. The two `test_prices_claude_models` cases raise `UnknownModelError`, and `test_two_digit_projects_are_discovered` fails because only `01-`…`09-` directories are found.

- [ ] **Step 4: Record the Anthropic pricing source**

In `cockpit/monitoring/cost_tracking.py`, replace:

```python
#   https://developers.openai.com/api/docs/pricing
PRICING_VERIFIED_DATE = "2026-08-03"
```

with:

```python
#   https://developers.openai.com/api/docs/pricing
#   https://platform.claude.com/docs/en/about-claude/pricing (Anthropic
#   entries added and verified 2026-09-17; the rest of the table was not
#   re-checked then, so PRICING_VERIFIED_DATE still describes them)
PRICING_VERIFIED_DATE = "2026-08-03"
```

- [ ] **Step 5: Add the Claude prices at the end of `DEFAULT_PRICING`**

In `cockpit/monitoring/cost_tracking.py`, replace:

```python
    "gpt-3.5-turbo": ModelPricing(0.50, 1.50, "openai"),
}
```

with:

```python
    "gpt-3.5-turbo": ModelPricing(0.50, 1.50, "openai"),
    # --- Anthropic ---
    "claude-opus-5": ModelPricing(5.00, 25.00, "anthropic"),
    "claude-sonnet-5": ModelPricing(2.00, 10.00, "anthropic"),
}
```

- [ ] **Step 6: Widen project discovery**

In `tests/e2e/test_examples.py`, replace:

```python
    return sorted((p for p in PROJECTS_DIR.glob("0*-*") if p.is_dir()), key=lambda p: p.name)
```

with:

```python
    return sorted(
        (p for p in PROJECTS_DIR.glob("[0-9][0-9]-*") if p.is_dir()), key=lambda p: p.name
    )
```

- [ ] **Step 7: Update the helper's docstring to match**

In `tests/e2e/test_examples.py`, replace:

```python
    """Return all currently-present projects/0*-*/ directories, sorted by name."""
```

with:

```python
    """Return all currently-present projects/NN-*/ directories, sorted by name."""
```

- [ ] **Step 8: Run the tests to verify they pass**

From the repository root:

```bash
uv run pytest -c config/pytest.ini --rootdir=. --no-cov -q tests/unit/test_monitoring.py::TestEstimateCost tests/e2e/test_examples.py
```

Expected: PASS.

- [ ] **Step 9: Run the whole root suite**

From the repository root:

```bash
uv run pytest -c config/pytest.ini --rootdir=. -q
```

Expected: all pass and `Required test coverage of 80% reached`.

- [ ] **Step 10: Commit**

From the repository root:

```bash
git add cockpit/monitoring/cost_tracking.py tests/unit/test_monitoring.py tests/e2e/test_examples.py
git commit -m "feat(cockpit): price Claude Opus 5 and Sonnet 5; discover projects 10+ in e2e test"
```

---

### Task 2: Project scaffold and the simulated tools

**Files:**
- Create: `projects/16-production-agent/pyproject.toml`
- Create: `projects/16-production-agent/.env.example`
- Create: `projects/16-production-agent/.gitignore`
- Create: `projects/16-production-agent/src/tools.py`
- Test: `projects/16-production-agent/tests/test_tools.py`

**Interfaces:**
- Produces: `lookup_order(order_id: str) -> str`, `track_shipment(tracking_id: str) -> str`, `refund_policy(category: str) -> str`. Each returns a JSON string and raises `anthropic.lib.tools.ToolError` on bad input.
- Produces: `TOOL_FUNCTIONS` (the three functions, in that order), `ORDERS`, `SHIPMENTS`, `REFUND_POLICIES`, `SAMPLE_QUESTIONS`.

From this task on, run commands from `projects/16-production-agent/` unless a step says otherwise. The pyproject already lists every dependency and the `live_provider` marker, so later tasks never touch it. Two data facts matter to the scenarios: `TRK-5503` never gets a new scan, and `lookup_order` accepts only `ORD-12345`.

- [ ] **Step 1: Create `pyproject.toml`**

Create `projects/16-production-agent/pyproject.toml`:

```toml
[project]
name = "production-agent"
version = "0.1.0"
description = "A Claude tool-use agent shipped like production: Phoenix tracing, loop and failure alerts, and a canary rollout with human-approved promotion and automatic rollback."
authors = [{ name = "Ash", email = "AshraHossain@users.noreply.github.com" }]
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
dependencies = [
    "anthropic>=1.6",
    "openinference-semantic-conventions>=0.1",
    "opentelemetry-exporter-otlp-proto-http>=1.27",
    "opentelemetry-sdk>=1.27",
    "python-dotenv>=1.0",
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
pythonpath = ["src", "../.."]
testpaths = ["tests"]
markers = ["live_provider: calls the real Anthropic API; skipped unless ANTHROPIC_API_KEY is set"]

[tool.ruff.lint.per-file-ignores]
# T201: src/main.py prints the CLI's actual output (dashboard, timeline and
#   approval prompts), not diagnostics.
# E402: src/main.py must put the repo root on sys.path *before* importing the
#   modules that import `cockpit.*`, so those imports cannot sit at the top.
"src/main.py" = ["T201", "E402"]
```

- [ ] **Step 2: Create `.env.example`**

Create `projects/16-production-agent/.env.example`:

```text
# Copy this file to .env and fill in your key. Never commit .env.
#
# Not needed for `--dry-run`, which plays a scripted scenario and makes no
# network calls.

ANTHROPIC_API_KEY=
```

- [ ] **Step 3: Create `.gitignore`**

Create `projects/16-production-agent/.gitignore`:

```text
# Written by the CLI (alerts.jsonl). Local run output, never committed.
outputs/
```

- [ ] **Step 4: Install**

From `projects/16-production-agent/`:

```bash
uv sync --all-groups
```

Expected: resolves `anthropic` 1.6+ (which brings `httpx2`), `opentelemetry-sdk`, the OTLP HTTP exporter and `openinference-semantic-conventions`.

- [ ] **Step 5: Write the failing test**

Create `projects/16-production-agent/tests/test_tools.py`:

```python
"""The simulated backend: fixed data, strict input, ToolError on bad input."""

from __future__ import annotations

import json

import pytest
from anthropic.lib.tools import ToolError

from tools import ORDERS, SAMPLE_QUESTIONS, lookup_order, refund_policy, track_shipment


def test_lookup_order_returns_the_order_as_json() -> None:
    assert json.loads(lookup_order("ORD-10042")) == {
        "order_id": "ORD-10042",
        "item": "Trail running shoes",
        "status": "shipped",
        "tracking_id": "TRK-5501",
        "category": "footwear",
    }


@pytest.mark.parametrize("order_id", ["10042", "ord-10042", "A-10042"])
def test_lookup_order_rejects_ids_not_in_the_ord_form(order_id: str) -> None:
    with pytest.raises(ToolError, match="expected the form ORD-12345"):
        lookup_order(order_id)


def test_lookup_order_rejects_unknown_orders() -> None:
    with pytest.raises(ToolError, match="No order with ID ORD-99999"):
        lookup_order("ORD-99999")


def test_the_stuck_shipment_never_gets_a_new_scan() -> None:
    assert json.loads(track_shipment("TRK-5503"))["status"] == "in transit"


def test_track_shipment_rejects_unknown_ids() -> None:
    with pytest.raises(ToolError, match="No shipment"):
        track_shipment("TRK-0000")


def test_every_order_category_has_a_refund_policy() -> None:
    for order in ORDERS.values():
        assert json.loads(refund_policy(str(order["category"])))["policy"]


def test_refund_policy_rejects_unknown_categories() -> None:
    with pytest.raises(ToolError, match="No refund policy"):
        refund_policy("groceries")


def test_every_sample_question_names_a_known_order() -> None:
    for question in SAMPLE_QUESTIONS:
        assert any(order_id in question for order_id in ORDERS), question
```

- [ ] **Step 6: Run it to verify it fails**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_tools.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tools'`.

- [ ] **Step 7: Write the tools**

Create `projects/16-production-agent/src/tools.py`:

```python
"""Simulated order-support backend: three read-only tools over in-memory data.

The data is fixed so every run behaves the same way. Two entries matter to the
dry-run scenarios: shipment ``TRK-5503`` never gets a new scan (the canary
loops on it), and ``lookup_order`` accepts only the ``ORD-12345`` form (the
promoted canary passes bare legacy numbers through and fails).

Tools raise the SDK's ``ToolError`` on bad input. The Tool Runner turns that
into an ``is_error`` tool result the model can recover from.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Final

from anthropic.lib.tools import ToolError

ORDERS: Final[dict[str, dict[str, str | None]]] = {
    "ORD-10042": {
        "item": "Trail running shoes",
        "status": "shipped",
        "tracking_id": "TRK-5501",
        "category": "footwear",
    },
    "ORD-10043": {
        "item": "Rain jacket",
        "status": "processing",
        "tracking_id": None,
        "category": "apparel",
    },
    "ORD-10044": {
        "item": "Headlamp",
        "status": "shipped",
        "tracking_id": "TRK-5503",
        "category": "electronics",
    },
}

SHIPMENTS: Final[dict[str, dict[str, str]]] = {
    "TRK-5501": {"status": "delivered", "last_scan": "2026-09-14, Denver CO"},
    "TRK-5503": {"status": "in transit", "last_scan": "no update since 2026-09-10"},
}

REFUND_POLICIES: Final[dict[str, str]] = {
    "footwear": "Unworn footwear can be returned within 30 days for a full refund.",
    "apparel": "Apparel can be returned within 60 days, tags attached.",
    "electronics": "Electronics can be returned within 14 days if unopened.",
}

SAMPLE_QUESTIONS: Final[tuple[str, ...]] = (
    "Where is my order ORD-10042?",
    "Has order ORD-10043 shipped yet?",
    "Where is my order ORD-10044? It seems stuck.",
    "Can I return the shoes from order ORD-10042?",
    "What's the return policy for order ORD-10044?",
)


def lookup_order(order_id: str) -> str:
    """Look up an order's item, status, tracking ID and product category.

    Args:
        order_id: Order ID in the form ORD-12345.
    """
    if not order_id.startswith("ORD-"):
        raise ToolError(f"Invalid order ID {order_id!r}: expected the form ORD-12345.")
    order = ORDERS.get(order_id)
    if order is None:
        raise ToolError(f"No order with ID {order_id}.")
    return json.dumps({"order_id": order_id, **order})


def track_shipment(tracking_id: str) -> str:
    """Get the latest carrier scan for a shipment.

    Args:
        tracking_id: Tracking ID from the order, in the form TRK-1234.
    """
    shipment = SHIPMENTS.get(tracking_id)
    if shipment is None:
        raise ToolError(f"No shipment with tracking ID {tracking_id}.")
    return json.dumps({"tracking_id": tracking_id, **shipment})


def refund_policy(category: str) -> str:
    """Get the return and refund policy for a product category.

    Args:
        category: Product category from the order, e.g. footwear.
    """
    policy = REFUND_POLICIES.get(category)
    if policy is None:
        raise ToolError(f"No refund policy for category {category!r}.")
    return json.dumps({"category": category, "policy": policy})


TOOL_FUNCTIONS: Final[tuple[Callable[[str], str], ...]] = (
    lookup_order,
    track_shipment,
    refund_policy,
)
```

- [ ] **Step 8: Run it to verify it passes**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_tools.py
```

Expected: PASS.

- [ ] **Step 9: Lint**

From `projects/16-production-agent/`:

```bash
uvx black --line-length 100 --target-version py311 --check -q src tests && uv run ruff check .
```

Expected: no output from black, `All checks passed!` from ruff. If black reports files, run `uvx black --line-length 100 --target-version py311 src tests` and re-run.

- [ ] **Step 10: Commit**

From the repository root:

```bash
git add projects/16-production-agent/pyproject.toml projects/16-production-agent/uv.lock projects/16-production-agent/.env.example projects/16-production-agent/.gitignore projects/16-production-agent/src/tools.py projects/16-production-agent/tests/test_tools.py
git commit -m "feat(16): scaffold production-agent project with simulated order tools"
```

> **Checkpoint:** Scaffold in place. Show the user `src/tools.py` and the dependency list before continuing.

---

### Task 3: Tracer provider for Phoenix

**Files:**
- Create: `projects/16-production-agent/src/tracing.py`
- Test: `projects/16-production-agent/tests/test_tracing.py`

**Interfaces:**
- Produces: `build_tracer_provider(exporter: SpanExporter | None, *, batch: bool = True) -> TracerProvider`. Tests pass `batch=False` with an `InMemorySpanExporter`.
- Produces: `phoenix_exporter() -> OTLPSpanExporter`, `to_ns(seconds: float) -> int`, `SERVICE_NAME = "16-production-agent"`, `PHOENIX_ENDPOINT = "http://localhost:6006/v1/traces"`.

The provider is always passed around explicitly and never set as the global provider, so each test can own one. The `openinference.project.name` resource attribute is what makes Phoenix file the traces under a named project.

- [ ] **Step 1: Write the failing test**

Create `projects/16-production-agent/tests/test_tracing.py`:

```python
"""Tracer provider setup and the Phoenix exporter's endpoint."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tracing import PHOENIX_ENDPOINT, build_tracer_provider, phoenix_exporter, to_ns


def test_spans_carry_the_phoenix_project_name() -> None:
    exporter = InMemorySpanExporter()
    build_tracer_provider(exporter, batch=False).get_tracer("tests").start_span("s").end()
    [span] = exporter.get_finished_spans()
    assert span.resource.attributes["openinference.project.name"] == "16-production-agent"
    assert span.resource.attributes["service.name"] == "16-production-agent"


def test_phoenix_on_localhost_is_the_default_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    assert phoenix_exporter()._endpoint == PHOENIX_ENDPOINT


def test_the_standard_variable_overrides_the_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://collector:4318/v1/traces")
    assert phoenix_exporter()._endpoint == "http://collector:4318/v1/traces"


def test_to_ns_converts_seconds_to_integer_nanoseconds() -> None:
    assert to_ns(1.5) == 1_500_000_000
```

- [ ] **Step 2: Run it to verify it fails**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_tracing.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tracing'`.

- [ ] **Step 3: Write `tracing.py`**

Create `projects/16-production-agent/src/tracing.py`:

```python
"""OpenTelemetry setup: one tracer provider, exporting to Phoenix or nowhere.

Spans use OpenInference attribute names so Phoenix renders them as agent, LLM
and tool spans. The provider is passed around explicitly rather than
installed globally, so tests can each build their own with an in-memory
exporter.
"""

from __future__ import annotations

import os
from typing import Final

from openinference.semconv.resource import ResourceAttributes
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)

SERVICE_NAME: Final = "16-production-agent"
PHOENIX_ENDPOINT: Final = "http://localhost:6006/v1/traces"


def build_tracer_provider(exporter: SpanExporter | None, *, batch: bool = True) -> TracerProvider:
    """Build a tracer provider tagged for this project.

    Args:
        exporter: Where spans go; None records spans but exports nothing.
        batch: Export in a background batch (production) or synchronously
            on span end (tests).

    Returns:
        The provider. Call ``shutdown()`` on exit to flush the last batch.
    """
    resource = Resource.create(
        {"service.name": SERVICE_NAME, ResourceAttributes.PROJECT_NAME: SERVICE_NAME}
    )
    provider = TracerProvider(resource=resource)
    if exporter is not None:
        processor = BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
        provider.add_span_processor(processor)
    return provider


def phoenix_exporter() -> OTLPSpanExporter:
    """OTLP/HTTP exporter for a local Phoenix.

    Returns:
        An exporter targeting ``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`` when set,
        otherwise Phoenix's default collector on localhost.
    """
    return OTLPSpanExporter(
        endpoint=os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", PHOENIX_ENDPOINT)
    )


def to_ns(seconds: float) -> int:
    """Convert clock seconds to the integer nanoseconds OpenTelemetry expects.

    Args:
        seconds: Epoch seconds.

    Returns:
        Nanoseconds since the epoch.
    """
    return int(seconds * 1_000_000_000)
```

- [ ] **Step 4: Run it to verify it passes**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_tracing.py
```

Expected: PASS.

- [ ] **Step 5: Lint**

From `projects/16-production-agent/`:

```bash
uvx black --line-length 100 --target-version py311 --check -q src tests && uv run ruff check .
```

Expected: no output from black, `All checks passed!` from ruff. If black reports files, run `uvx black --line-length 100 --target-version py311 src tests` and re-run.

- [ ] **Step 6: Commit**

From the repository root:

```bash
git add projects/16-production-agent/src/tracing.py projects/16-production-agent/tests/test_tracing.py
git commit -m "feat(16): tracer provider exporting to Phoenix over OTLP/HTTP"
```

---

### Task 4: Agent versions and run records

**Files:**
- Create: `projects/16-production-agent/src/agent.py` (first half; Task 7 completes it)
- Test: `projects/16-production-agent/tests/test_agent.py` (first half; Task 7 replaces it)

**Interfaces:**
- Consumes: `TOOL_FUNCTIONS` (Task 2).
- Produces: `AgentVersion(version: str, model: str, system_prompt: str = SYSTEM_PROMPT, effort: str = "medium", max_tokens: int = 16000)` with `.fingerprint() -> str`; `STABLE = AgentVersion("v1", "claude-opus-5")`; `CANARY = AgentVersion("v2", "claude-sonnet-5")`.
- Produces: `RunRecord(request_id: str, version: str, group: str, outcome: str, duration_s: float, cost_usd: float, tool_calls: int, tool_errors: int, input_tokens: int, output_tokens: int, answer: str = "", loop_reason: str | None = None)` (frozen).
- Produces: `outcome_for(stop_reason: str | None) -> str`, `PROJECT = "16-production-agent"`, `MAX_ITERATIONS = 8`, `SYSTEM_PROMPT`.

`agent.py` is built in two passes. This task writes the data types that `alerts.py` (Task 5) and the fake transport tests depend on. Task 7 replaces the file with the full version, which adds the run loop.

- [ ] **Step 1: Write the failing test**

Create `projects/16-production-agent/tests/test_agent.py`:

```python
"""Version identity and outcome mapping (Task 7 replaces this file with the full test)."""

from __future__ import annotations

import pytest

from agent import CANARY, STABLE, AgentVersion, outcome_for


def test_a_fingerprint_is_stable_and_covers_the_model() -> None:
    assert STABLE.fingerprint() == AgentVersion("v1", "claude-opus-5").fingerprint()
    assert STABLE.fingerprint() != CANARY.fingerprint()
    assert STABLE.fingerprint() != AgentVersion("v1", "claude-opus-5", effort="high").fingerprint()


@pytest.mark.parametrize(
    ("stop_reason", "outcome"),
    [
        ("end_turn", "ok"),
        ("tool_use", "max_iterations"),
        ("refusal", "refusal"),
        ("max_tokens", "incomplete"),
        ("model_context_window_exceeded", "incomplete"),
        (None, "incomplete"),
    ],
)
def test_outcome_for_each_final_stop_reason(stop_reason: str | None, outcome: str) -> None:
    assert outcome_for(stop_reason) == outcome
```

- [ ] **Step 2: Run it to verify it fails**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_agent.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agent'`.

- [ ] **Step 3: Write the first half of `agent.py`**

Create `projects/16-production-agent/src/agent.py`:

```python
"""One agent run: the SDK Tool Runner loop, instrumented.

Everything a run produces is measured once, here: an ``llm.call`` span, a
latency sample and a cost entry for every model turn, and a ``tool.<name>``
span and latency sample for every tool call. The loop guard sees each turn's
tool calls *before* the runner executes them; breaking out of the loop at that
point means those tools never run.

The root ``agent.run`` span belongs to the caller (the rollout controller),
which also decides what the run means for alerts and the canary. This module
only reports what happened, as a :class:`RunRecord`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final

from anthropic import beta_tool

from tools import TOOL_FUNCTIONS

PROJECT: Final = "16-production-agent"
MAX_ITERATIONS: Final = 8

SYSTEM_PROMPT: Final = (
    "You are the order-support assistant for an outdoor gear shop. Answer questions "
    "about orders, shipments and returns using the tools, and keep answers short.\n\n"
    "Order IDs have the form ORD-12345. If a customer gives a bare order number such "
    "as 10042, call lookup_order with ORD-10042. If a tool returns an error, tell the "
    "customer plainly what went wrong instead of guessing."
)


@dataclass(frozen=True)
class AgentVersion:
    """Everything that defines one deployable version of the agent.

    Attributes:
        version: Registry version identifier, e.g. ``"v1"``.
        model: Claude model ID.
        system_prompt: System prompt sent on every request.
        effort: ``output_config.effort`` level.
        max_tokens: Per-response output cap.
    """

    version: str
    model: str
    system_prompt: str = SYSTEM_PROMPT
    effort: str = "medium"
    max_tokens: int = 16000

    def fingerprint(self) -> str:
        """Digest of every setting that shapes this version's requests.

        Returns:
            Hex SHA-256 over model, prompt, thinking mode, effort, output cap
            and tool schemas.
        """
        payload = {
            "model": self.model,
            "system": self.system_prompt,
            "thinking": "adaptive",
            "effort": self.effort,
            "max_tokens": self.max_tokens,
            "tools": [beta_tool(fn).to_dict() for fn in TOOL_FUNCTIONS],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


STABLE: Final = AgentVersion("v1", "claude-opus-5")
CANARY: Final = AgentVersion("v2", "claude-sonnet-5")


@dataclass(frozen=True)
class RunRecord:
    """What one run did, as alerts and the canary judge see it.

    Attributes:
        request_id: Request identifier.
        version: Version that served it.
        group: ``"stable"`` or ``"canary"``.
        outcome: ``ok``, ``loop``, ``max_iterations``, ``refusal``,
            ``incomplete`` or ``api_error``.
        duration_s: Wall time of the whole run, in seconds.
        cost_usd: Cost of every model turn in the run.
        tool_calls: Tool calls executed.
        tool_errors: Tool calls that raised.
        input_tokens: Prompt tokens across all turns.
        output_tokens: Output tokens across all turns.
        answer: Text of the final assistant turn.
        loop_reason: Loop guard verdict when the outcome is ``loop``.
    """

    request_id: str
    version: str
    group: str
    outcome: str
    duration_s: float
    cost_usd: float
    tool_calls: int
    tool_errors: int
    input_tokens: int
    output_tokens: int
    answer: str = ""
    loop_reason: str | None = None


def outcome_for(stop_reason: str | None) -> str:
    """Map the final turn's stop reason to a run outcome.

    Args:
        stop_reason: ``stop_reason`` of the last message the runner yielded.

    Returns:
        ``ok`` for ``end_turn``; ``max_iterations`` for ``tool_use`` (the
        runner stopped with tool calls still pending); ``refusal``; otherwise
        ``incomplete``.
    """
    if stop_reason == "end_turn":
        return "ok"
    if stop_reason == "tool_use":
        return "max_iterations"
    if stop_reason == "refusal":
        return "refusal"
    return "incomplete"
```

- [ ] **Step 4: Run it to verify it passes**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_agent.py
```

Expected: PASS.

- [ ] **Step 5: Lint**

From `projects/16-production-agent/`:

```bash
uvx black --line-length 100 --target-version py311 --check -q src tests && uv run ruff check .
```

Expected: no output from black, `All checks passed!` from ruff. If black reports files, run `uvx black --line-length 100 --target-version py311 src tests` and re-run.

- [ ] **Step 6: Commit**

From the repository root:

```bash
git add projects/16-production-agent/src/agent.py projects/16-production-agent/tests/test_agent.py
git commit -m "feat(16): agent versions with config fingerprints and run records"
```

---

### Task 5: Loop guard, run windows and alert rules

**Files:**
- Create: `projects/16-production-agent/src/alerts.py`
- Create: `projects/16-production-agent/tests/conftest.py` (first version)
- Test: `projects/16-production-agent/tests/test_alerts.py`

**Interfaces:**
- Consumes: `RunRecord` (Task 4), `cockpit.monitoring.performance_metrics.percentile`.
- Produces: `LoopGuard(repeat_limit=3, per_tool_limit=5).check(calls: Iterable[tuple[str, Mapping[str, Any]]]) -> str | None`, which returns `"repeat:<tool>"` or `"no_progress:<tool>"`.
- Produces: `WindowStats(runs, loops, failure_rate, tool_error_rate, p95_s, mean_cost)` with `WindowStats.of(records)`; `RunWindow(size=20)` with `.add(record) -> WindowStats` and `.stats(version) -> WindowStats`.
- Produces: `AlertRule`, `RULES`; `AlertEvent(timestamp, version, rule, state, value, threshold, request_id)`; `AlertMonitor(sinks=(), rules=RULES)` with `.observe(version, stats, *, request_id, timestamp) -> list[AlertEvent]` and `.firing(version) -> set[str]`; `log_sink(event)`; `jsonl_sink(path) -> AlertSink`.
- Produces (fixture): `make_record(**overrides) -> RunRecord`.

Thresholds are the spec's values, copied verbatim: repeat at 3, no progress at 5, a window of 20 runs, loop > 0 (1 run), failure rate > 20%, tool error rate > 30%, p95 > 30 s, mean cost > $0.15 (10 runs each). `conftest.py` grows over the next tasks. Each version is given in full.

- [ ] **Step 1: Create `tests/conftest.py`**

Create `projects/16-production-agent/tests/conftest.py`:

```python
"""Shared fixtures.

``pythonpath = ["src", "../.."]`` in ``pyproject.toml`` makes both this
project's ``src/`` and the repo root importable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from agent import RunRecord


@pytest.fixture
def make_record() -> Callable[..., RunRecord]:
    """Build a :class:`RunRecord` with harmless defaults for the fields a test doesn't care about."""

    def make(**overrides: Any) -> RunRecord:
        fields: dict[str, Any] = {
            "request_id": "req-0001",
            "version": "v1",
            "group": "stable",
            "outcome": "ok",
            "duration_s": 2.0,
            "cost_usd": 0.01,
            "tool_calls": 2,
            "tool_errors": 0,
            "input_tokens": 1000,
            "output_tokens": 200,
        }
        fields.update(overrides)
        return RunRecord(**fields)

    return make
```

- [ ] **Step 2: Write the failing test**

Create `projects/16-production-agent/tests/test_alerts.py`:

```python
"""Loop guard, window stats, alert rules, the monitor and its sinks."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent import RunRecord
from alerts import (
    RULES,
    AlertEvent,
    AlertMonitor,
    LoopGuard,
    RunWindow,
    WindowStats,
    jsonl_sink,
    log_sink,
)


def stats(**overrides: Any) -> WindowStats:
    fields: dict[str, Any] = {
        "runs": 0,
        "loops": 0,
        "failure_rate": 0.0,
        "tool_error_rate": 0.0,
        "p95_s": 0.0,
        "mean_cost": 0.0,
    }
    fields.update(overrides)
    return WindowStats(**fields)


# ---------------------------------------------------------------- loop guard


def test_the_third_identical_call_trips_the_repeat_check() -> None:
    guard = LoopGuard()
    call = [("track_shipment", {"tracking_id": "TRK-5503"})]
    assert guard.check(call) is None
    assert guard.check(call) is None
    assert guard.check(call) == "repeat:track_shipment"


def test_key_order_inside_the_input_does_not_matter() -> None:
    guard = LoopGuard()
    guard.check([("t", {"a": 1, "b": 2})])
    guard.check([("t", {"b": 2, "a": 1})])
    assert guard.check([("t", {"a": 1, "b": 2})]) == "repeat:t"


def test_varied_inputs_trip_only_at_the_fifth_call_to_one_tool() -> None:
    guard = LoopGuard()
    verdicts = [guard.check([("lookup_order", {"order_id": f"ORD-1000{n}"})]) for n in range(5)]
    assert verdicts == [None, None, None, None, "no_progress:lookup_order"]


def test_calls_within_one_turn_are_counted_individually() -> None:
    assert LoopGuard().check([("t", {"x": 1})] * 3) == "repeat:t"


def test_each_tool_has_its_own_count() -> None:
    guard = LoopGuard()
    for n in range(4):
        assert guard.check([("a", {"n": n}), ("b", {"n": n})]) is None


# -------------------------------------------------------------- window stats


def test_an_empty_window_is_all_zeros() -> None:
    assert WindowStats.of([]) == stats()


def test_window_stats_summarize_the_runs(make_record: Callable[..., RunRecord]) -> None:
    records = [
        make_record(outcome="ok", duration_s=1.0, cost_usd=0.01, tool_calls=2, tool_errors=0),
        make_record(outcome="loop", duration_s=3.0, cost_usd=0.03, tool_calls=2, tool_errors=1),
        make_record(
            outcome="api_error", duration_s=2.0, cost_usd=0.02, tool_calls=0, tool_errors=0
        ),
        make_record(outcome="ok", duration_s=4.0, cost_usd=0.04, tool_calls=4, tool_errors=1),
    ]
    assert WindowStats.of(records) == pytest.approx(
        stats(runs=4, loops=1, failure_rate=0.5, tool_error_rate=0.25, p95_s=4.0, mean_cost=0.025)
    )


def test_tool_error_rate_is_zero_when_no_tools_ran(make_record: Callable[..., RunRecord]) -> None:
    assert WindowStats.of([make_record(tool_calls=0, tool_errors=0)]).tool_error_rate == 0.0


def test_the_window_keeps_only_the_latest_runs_per_version(
    make_record: Callable[..., RunRecord],
) -> None:
    window = RunWindow(size=3)
    for n in range(5):
        window.add(make_record(version="v1", outcome="loop" if n < 2 else "ok"))
    window.add(make_record(version="v2", outcome="loop"))
    assert window.stats("v1").runs == 3
    assert window.stats("v1").loops == 0
    assert window.stats("v2").loops == 1
    assert window.stats("v3").runs == 0


# --------------------------------------------------------------------- rules


@pytest.mark.parametrize(
    ("rule_name", "window", "breached"),
    [
        ("loop_detected", stats(runs=1, loops=1), True),
        ("loop_detected", stats(runs=20, loops=0), False),
        ("failure_rate", stats(runs=10, failure_rate=0.21), True),
        ("failure_rate", stats(runs=10, failure_rate=0.20), False),
        ("failure_rate", stats(runs=9, failure_rate=0.90), False),
        ("tool_error_rate", stats(runs=10, tool_error_rate=0.31), True),
        ("tool_error_rate", stats(runs=10, tool_error_rate=0.30), False),
        ("p95_latency", stats(runs=10, p95_s=30.1), True),
        ("p95_latency", stats(runs=10, p95_s=30.0), False),
        ("cost_per_run", stats(runs=10, mean_cost=0.151), True),
        ("cost_per_run", stats(runs=10, mean_cost=0.15), False),
    ],
)
def test_rule_thresholds(rule_name: str, window: WindowStats, breached: bool) -> None:
    rule = next(r for r in RULES if r.name == rule_name)
    assert rule.breached(window) is breached


# ------------------------------------------------------------------- monitor


def test_the_monitor_reports_only_transitions() -> None:
    delivered: list[AlertEvent] = []
    monitor = AlertMonitor([delivered.append])
    looping, clean = stats(runs=1, loops=1), stats(runs=1)

    fired = monitor.observe("v2", looping, request_id="req-0001", timestamp=10.0)
    assert [(e.rule, e.state) for e in fired] == [("loop_detected", "fired")]
    assert monitor.observe("v2", looping, request_id="req-0002", timestamp=11.0) == []
    assert monitor.firing("v2") == {"loop_detected"}

    resolved = monitor.observe("v2", clean, request_id="req-0003", timestamp=12.0)
    assert [(e.rule, e.state) for e in resolved] == [("loop_detected", "resolved")]
    assert monitor.firing("v2") == set()
    assert delivered == fired + resolved


def test_an_event_carries_what_caused_it() -> None:
    [event] = AlertMonitor().observe(
        "v2", stats(runs=1, loops=1), request_id="req-0042", timestamp=99.0
    )
    assert event == AlertEvent(
        timestamp=99.0,
        version="v2",
        rule="loop_detected",
        state="fired",
        value=1.0,
        threshold=0,
        request_id="req-0042",
    )


def test_versions_fire_independently() -> None:
    monitor = AlertMonitor()
    monitor.observe("v2", stats(runs=1, loops=1), request_id="r", timestamp=0.0)
    assert monitor.firing("v1") == set()


# --------------------------------------------------------------------- sinks


def _event(state: str = "fired") -> AlertEvent:
    return AlertEvent(1.5, "v2", "tool_error_rate", state, 0.33, 0.3, "req-0338")


def test_the_jsonl_sink_appends_one_object_per_event(tmp_path: Path) -> None:
    path = tmp_path / "outputs" / "alerts.jsonl"
    sink = jsonl_sink(path)
    sink(_event("fired"))
    sink(_event("resolved"))
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["state"] for line in lines] == ["fired", "resolved"]
    assert lines[0] == {
        "timestamp": 1.5,
        "version": "v2",
        "rule": "tool_error_rate",
        "state": "fired",
        "value": 0.33,
        "threshold": 0.3,
        "request_id": "req-0338",
    }


def test_the_log_sink_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        log_sink(_event())
    assert "alert fired: tool_error_rate for v2" in caplog.text
```

- [ ] **Step 3: Run it to verify it fails**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_alerts.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'alerts'`.

- [ ] **Step 4: Write `alerts.py`**

Create `projects/16-production-agent/src/alerts.py`:

```python
"""Loop guard, per-version run windows, alert rules and their delivery.

Two different time scales:

* :class:`LoopGuard` works *inside* one run. It sees each turn's tool calls
  before they execute and stops a run that is going in circles.
* :class:`AlertMonitor` works *across* runs. It evaluates fixed rules over the
  last :data:`WINDOW_SIZE` runs of each version and reports only transitions,
  so a breached rule fires once rather than on every run.

Windows are keyed by version, not by canary group, so after a promotion the
new version's numbers never mix with the old version's.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from cockpit.monitoring.performance_metrics import percentile

if TYPE_CHECKING:
    from agent import RunRecord

_logger = logging.getLogger(__name__)

REPEAT_LIMIT: Final = 3
PER_TOOL_LIMIT: Final = 5
WINDOW_SIZE: Final = 20


@dataclass
class LoopGuard:
    """Stops a run that repeats itself. Create a fresh one per run.

    Attributes:
        repeat_limit: Identical calls (same tool, same input) that trip it.
        per_tool_limit: Calls to one tool, whatever the input, that trip it.
    """

    repeat_limit: int = REPEAT_LIMIT
    per_tool_limit: int = PER_TOOL_LIMIT
    _identical: Counter[tuple[str, str]] = field(default_factory=Counter)
    _per_tool: Counter[str] = field(default_factory=Counter)

    def check(self, calls: Iterable[tuple[str, Mapping[str, Any]]]) -> str | None:
        """Count one turn's tool calls and report the first limit reached.

        Args:
            calls: ``(tool_name, input)`` pairs from one assistant turn, in order.

        Returns:
            ``"repeat:<tool>"`` or ``"no_progress:<tool>"`` when a limit is
            reached, otherwise None.
        """
        for name, tool_input in calls:
            key = (name, json.dumps(tool_input, sort_keys=True))
            self._identical[key] += 1
            self._per_tool[name] += 1
            if self._identical[key] >= self.repeat_limit:
                return f"repeat:{name}"
            if self._per_tool[name] >= self.per_tool_limit:
                return f"no_progress:{name}"
        return None


@dataclass(frozen=True)
class WindowStats:
    """Summary of one version's most recent runs.

    Attributes:
        runs: Runs in the window.
        loops: Runs that ended with outcome ``loop``.
        failure_rate: Share of runs whose outcome is not ``ok``.
        tool_error_rate: Tool errors divided by tool calls (0.0 with no calls).
        p95_s: 95th-percentile run duration, in seconds.
        mean_cost: Mean cost per run, in US dollars.
    """

    runs: int
    loops: int
    failure_rate: float
    tool_error_rate: float
    p95_s: float
    mean_cost: float

    @classmethod
    def of(cls, records: Sequence[RunRecord]) -> WindowStats:
        """Summarize a sequence of run records.

        Args:
            records: The runs to summarize, in any order.

        Returns:
            The summary; all zeros for an empty sequence.
        """
        runs = len(records)
        if runs == 0:
            return cls(0, 0, 0.0, 0.0, 0.0, 0.0)
        tool_calls = sum(r.tool_calls for r in records)
        return cls(
            runs=runs,
            loops=sum(r.outcome == "loop" for r in records),
            failure_rate=sum(r.outcome != "ok" for r in records) / runs,
            tool_error_rate=(
                (sum(r.tool_errors for r in records) / tool_calls) if tool_calls else 0.0
            ),
            p95_s=percentile([r.duration_s for r in records], 95),
            mean_cost=sum(r.cost_usd for r in records) / runs,
        )


class RunWindow:
    """Keeps the last :data:`WINDOW_SIZE` run records for each version."""

    def __init__(self, size: int = WINDOW_SIZE) -> None:
        """Create an empty window.

        Args:
            size: Runs kept per version.
        """
        self._runs: defaultdict[str, deque[RunRecord]] = defaultdict(lambda: deque(maxlen=size))

    def add(self, record: RunRecord) -> WindowStats:
        """Append a record to its version's window.

        Args:
            record: The finished run.

        Returns:
            The version's stats including this run.
        """
        self._runs[record.version].append(record)
        return self.stats(record.version)

    def stats(self, version: str) -> WindowStats:
        """Summarize a version's window.

        Args:
            version: Version identifier.

        Returns:
            Its stats; all zeros if it has no runs yet.
        """
        return WindowStats.of(list(self._runs[version]))


@dataclass(frozen=True)
class AlertRule:
    """A threshold on one window metric.

    Attributes:
        name: Rule identifier used in events and span attributes.
        metric: Reads the value to compare from a :class:`WindowStats`.
        threshold: The rule is breached when the value is strictly above this.
        min_runs: Runs the window must hold before the rule can breach.
    """

    name: str
    metric: Callable[[WindowStats], float]
    threshold: float
    min_runs: int

    def breached(self, stats: WindowStats) -> bool:
        """Whether the rule is breached for these stats.

        Args:
            stats: Window summary to test.

        Returns:
            True when the window is large enough and the value exceeds the threshold.
        """
        return stats.runs >= self.min_runs and self.metric(stats) > self.threshold


RULES: Final[tuple[AlertRule, ...]] = (
    AlertRule("loop_detected", lambda s: s.loops, 0, 1),
    AlertRule("failure_rate", lambda s: s.failure_rate, 0.20, 10),
    AlertRule("tool_error_rate", lambda s: s.tool_error_rate, 0.30, 10),
    AlertRule("p95_latency", lambda s: s.p95_s, 30.0, 10),
    AlertRule("cost_per_run", lambda s: s.mean_cost, 0.15, 10),
)


@dataclass(frozen=True)
class AlertEvent:
    """One alert transition.

    Attributes:
        timestamp: When it happened, in clock seconds.
        version: Version the rule was evaluated for.
        rule: Rule name.
        state: ``"fired"`` or ``"resolved"``.
        value: Metric value at the transition.
        threshold: The rule's threshold.
        request_id: Request whose run caused the transition.
    """

    timestamp: float
    version: str
    rule: str
    state: str
    value: float
    threshold: float
    request_id: str


AlertSink = Callable[[AlertEvent], None]


class AlertMonitor:
    """Evaluates rules per version and delivers only state changes."""

    def __init__(self, sinks: Sequence[AlertSink] = (), rules: Sequence[AlertRule] = RULES) -> None:
        """Create a monitor with nothing firing.

        Args:
            sinks: Called with every transition, in order.
            rules: Rules to evaluate.
        """
        self._sinks = tuple(sinks)
        self._rules = tuple(rules)
        self._firing: set[tuple[str, str]] = set()

    def observe(
        self, version: str, stats: WindowStats, *, request_id: str, timestamp: float
    ) -> list[AlertEvent]:
        """Evaluate every rule for one version and deliver any transitions.

        Args:
            version: Version the stats describe.
            stats: That version's current window stats.
            request_id: Request whose run produced these stats.
            timestamp: Current clock time.

        Returns:
            The transitions, in rule order; empty when nothing changed.
        """
        events = []
        for rule in self._rules:
            key = (version, rule.name)
            breached = rule.breached(stats)
            if breached == (key in self._firing):
                continue
            if breached:
                self._firing.add(key)
            else:
                self._firing.discard(key)
            event = AlertEvent(
                timestamp=timestamp,
                version=version,
                rule=rule.name,
                state="fired" if breached else "resolved",
                value=float(rule.metric(stats)),
                threshold=rule.threshold,
                request_id=request_id,
            )
            events.append(event)
            for sink in self._sinks:
                sink(event)
        return events

    def firing(self, version: str) -> set[str]:
        """Names of the rules currently firing for a version.

        Args:
            version: Version identifier.

        Returns:
            Rule names; empty when none are firing.
        """
        return {rule for v, rule in self._firing if v == version}


def log_sink(event: AlertEvent) -> None:
    """Log an alert transition at WARNING.

    Args:
        event: The transition.
    """
    _logger.warning(
        "alert %s: %s for %s (value %.4g, threshold %.4g, request %s)",
        event.state,
        event.rule,
        event.version,
        event.value,
        event.threshold,
        event.request_id,
    )


def jsonl_sink(path: Path) -> AlertSink:
    """Build a sink that appends each transition to a JSON Lines file.

    Args:
        path: File to append to; its parent directory is created if missing.

    Returns:
        The sink.
    """

    def sink(event: AlertEvent) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event)) + "\n")

    return sink
```

- [ ] **Step 5: Run it to verify it passes**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_alerts.py
```

Expected: PASS.

- [ ] **Step 6: Lint**

From `projects/16-production-agent/`:

```bash
uvx black --line-length 100 --target-version py311 --check -q src tests && uv run ruff check .
```

Expected: no output from black, `All checks passed!` from ruff. If black reports files, run `uvx black --line-length 100 --target-version py311 src tests` and re-run.

- [ ] **Step 7: Commit**

From the repository root:

```bash
git add projects/16-production-agent/src/alerts.py projects/16-production-agent/tests/conftest.py projects/16-production-agent/tests/test_alerts.py
git commit -m "feat(16): loop guard, per-version run windows and transition-only alert rules"
```

---

### Task 6: Scripted Messages API behind httpx2.MockTransport

**Files:**
- Create: `projects/16-production-agent/src/fake_model.py`
- Modify: `projects/16-production-agent/tests/conftest.py` (add the `clock` fixture)
- Test: `projects/16-production-agent/tests/test_fake_model.py`

**Interfaces:**
- Produces: `FakeClock(now: float = DRY_RUN_EPOCH)`, which is callable and has `.advance(seconds)`. `DRY_RUN_EPOCH = 1_767_225_600.0` (2026-01-01T00:00:00Z).
- Produces: `Turn(content: list[dict], stop_reason: str = "end_turn")`; `Policy = Callable[[list[dict]], Turn | httpx2.Response]`; `ModelProfile(policy, latency_s=1.0, input_tokens=1200, output_tokens=300)`. Each earlier assistant turn adds 400 prompt tokens.
- Produces: `text(value) -> dict`, `tool_call(name, **tool_input) -> dict`, `tool_history(messages) -> list[tuple[str, dict, str, bool]]`, `scripted_client(profiles: Mapping[str, ModelProfile], clock: FakeClock) -> anthropic.Anthropic`.
- Produces (fixture): `clock -> FakeClock`.

`anthropic` 1.6 uses `httpx2`, not `httpx`, so the mock transport and the client's `http_client` both come from `httpx2`. Only the HTTP layer is fake: the SDK's real Tool Runner drives every test and dry run from Task 7 onward.

- [ ] **Step 1: Replace `tests/conftest.py`**

Replace `projects/16-production-agent/tests/conftest.py`:

```python
"""Shared fixtures.

``pythonpath = ["src", "../.."]`` in ``pyproject.toml`` makes both this
project's ``src/`` and the repo root importable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from agent import RunRecord
from fake_model import FakeClock


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_record() -> Callable[..., RunRecord]:
    """Build a :class:`RunRecord` with harmless defaults for the fields a test doesn't care about."""

    def make(**overrides: Any) -> RunRecord:
        fields: dict[str, Any] = {
            "request_id": "req-0001",
            "version": "v1",
            "group": "stable",
            "outcome": "ok",
            "duration_s": 2.0,
            "cost_usd": 0.01,
            "tool_calls": 2,
            "tool_errors": 0,
            "input_tokens": 1000,
            "output_tokens": 200,
        }
        fields.update(overrides)
        return RunRecord(**fields)

    return make
```

- [ ] **Step 2: Write the failing test**

Create `projects/16-production-agent/tests/test_fake_model.py`:

```python
"""The scripted Messages API: real SDK objects out, fake clock advanced."""

from __future__ import annotations

from typing import Any

import anthropic
import httpx2
import pytest

from fake_model import (
    DRY_RUN_EPOCH,
    FakeClock,
    ModelProfile,
    Policy,
    Turn,
    scripted_client,
    text,
    tool_call,
    tool_history,
)


def ask(policy: Policy, clock: FakeClock, messages: list[dict[str, Any]] | None = None) -> Any:
    client = scripted_client({"m": ModelProfile(policy, latency_s=2.0)}, clock)
    return client.messages.create(
        model="m", max_tokens=100, messages=messages or [{"role": "user", "content": "Hi"}]
    )


def test_the_clock_moves_only_when_advanced(clock: FakeClock) -> None:
    assert clock() == DRY_RUN_EPOCH
    clock.advance(1.5)
    assert clock() == DRY_RUN_EPOCH + 1.5


def test_a_scripted_turn_comes_back_as_a_real_message(clock: FakeClock) -> None:
    def policy(messages: list[dict[str, Any]]) -> Turn:
        return Turn(
            [text("Checking."), tool_call("lookup_order", order_id="ORD-10042")], "tool_use"
        )

    message = ask(policy, clock)
    assert message.stop_reason == "tool_use"
    assert message.content[0].text == "Checking."
    call = message.content[1]
    assert (call.name, call.input) == ("lookup_order", {"order_id": "ORD-10042"})
    assert call.id.startswith("toolu_")
    assert (message.usage.input_tokens, message.usage.output_tokens) == (1200, 300)
    assert clock() == DRY_RUN_EPOCH + 2.0


def test_prompt_tokens_grow_with_each_earlier_assistant_turn(clock: FakeClock) -> None:
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello."},
        {"role": "user", "content": "Where is ORD-10042?"},
    ]
    message = ask(lambda m: Turn([text("Shipped.")]), clock, messages)
    assert message.usage.input_tokens == 1600


def test_a_policy_can_answer_with_an_http_error(clock: FakeClock) -> None:
    def overloaded(messages: list[dict[str, Any]]) -> httpx2.Response:
        return httpx2.Response(
            529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
        )

    with pytest.raises(anthropic.APIStatusError):
        ask(overloaded, clock)


def test_tool_history_pairs_each_call_with_its_result() -> None:
    messages = [
        {"role": "user", "content": "Where is 10042?"},
        {
            "role": "assistant",
            "content": [{**tool_call("lookup_order", order_id="10042"), "id": "t1"}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": [{"type": "text", "text": "Invalid order ID"}],
                    "is_error": True,
                }
            ],
        },
    ]
    assert tool_history(messages) == [
        ("lookup_order", {"order_id": "10042"}, "Invalid order ID", True)
    ]
```

- [ ] **Step 3: Run it to verify it fails**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_fake_model.py
```

Expected: FAIL. `conftest.py` cannot import `fake_model` yet.

- [ ] **Step 4: Write `fake_model.py`**

Create `projects/16-production-agent/src/fake_model.py`:

```python
"""A scripted Messages API behind ``httpx2.MockTransport``.

The Anthropic client is real and so is its Tool Runner; only the HTTP layer is
replaced. Each model has a :class:`ModelProfile`: a policy that reads the
conversation so far and returns the next assistant turn, plus the latency and
token counts to report. Latency advances the shared :class:`FakeClock`, so
dry-run durations, alerts and dashboards come out identical on every run.

A policy may also return an ``httpx2.Response`` directly, which is how tests
simulate API errors.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import anthropic
import httpx2

DRY_RUN_EPOCH: Final = 1_767_225_600.0  # 2026-01-01T00:00:00Z
TOKENS_PER_EARLIER_TURN: Final = 400


@dataclass
class FakeClock:
    """A clock that moves only when told to.

    Attributes:
        now: Current time, in epoch seconds.
    """

    now: float = DRY_RUN_EPOCH

    def __call__(self) -> float:
        """Current time, in epoch seconds."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move time forward.

        Args:
            seconds: How far to move.
        """
        self.now += seconds


@dataclass(frozen=True)
class Turn:
    """One scripted assistant turn.

    Attributes:
        content: Content blocks; ``tool_use`` blocks get their ``id`` filled in.
        stop_reason: The turn's stop reason.
    """

    content: list[dict[str, Any]]
    stop_reason: str = "end_turn"


Policy = Callable[[list[dict[str, Any]]], "Turn | httpx2.Response"]


@dataclass(frozen=True)
class ModelProfile:
    """How one scripted model behaves.

    Attributes:
        policy: Chooses the next turn from the request's messages.
        latency_s: Seconds each call takes on the fake clock.
        input_tokens: Prompt tokens reported for the first turn; each earlier
            assistant turn in the conversation adds :data:`TOKENS_PER_EARLIER_TURN`.
        output_tokens: Output tokens reported per turn.
    """

    policy: Policy
    latency_s: float = 1.0
    input_tokens: int = 1200
    output_tokens: int = 300


def text(value: str) -> dict[str, Any]:
    """A text content block."""
    return {"type": "text", "text": value}


def tool_call(name: str, **tool_input: Any) -> dict[str, Any]:
    """A ``tool_use`` content block; the transport assigns its ID."""
    return {"type": "tool_use", "name": name, "input": tool_input}


def tool_history(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], str, bool]]:
    """Pair each tool call in a conversation with its result.

    Args:
        messages: The request's ``messages`` array.

    Returns:
        ``(tool_name, input, result_text, is_error)`` per completed call, oldest first.
    """
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    history = []
    for message in messages:
        if not isinstance(message["content"], list):
            continue
        for block in message["content"]:
            if block["type"] == "tool_use":
                calls[block["id"]] = (block["name"], block["input"])
            elif block["type"] == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = "".join(part.get("text", "") for part in content)
                name, tool_input = calls[block["tool_use_id"]]
                history.append((name, tool_input, content, bool(block.get("is_error"))))
    return history


@dataclass
class _Transport:
    profiles: Mapping[str, ModelProfile]
    clock: FakeClock
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        profile = self.profiles[body["model"]]
        self.clock.advance(profile.latency_s)
        turn = profile.policy(body["messages"])
        if isinstance(turn, httpx2.Response):
            return turn
        content = [
            (
                {**block, "id": f"toolu_{next(self._ids):05d}"}
                if block["type"] == "tool_use"
                else block
            )
            for block in turn.content
        ]
        earlier_turns = sum(m["role"] == "assistant" for m in body["messages"])
        return httpx2.Response(
            200,
            json={
                "id": f"msg_{next(self._ids):05d}",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "content": content,
                "stop_reason": turn.stop_reason,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": profile.input_tokens + TOKENS_PER_EARLIER_TURN * earlier_turns,
                    "output_tokens": profile.output_tokens,
                },
            },
        )


def scripted_client(profiles: Mapping[str, ModelProfile], clock: FakeClock) -> anthropic.Anthropic:
    """A real Anthropic client whose HTTP calls are answered by the profiles.

    Args:
        profiles: Behaviour per model ID.
        clock: Clock advanced by each call's scripted latency.

    Returns:
        The client. It never touches the network and needs no API key.
    """
    transport = httpx2.MockTransport(_Transport(profiles, clock))
    return anthropic.Anthropic(
        api_key="dry-run", max_retries=0, http_client=httpx2.Client(transport=transport)
    )
```

- [ ] **Step 5: Run the whole suite**

From `projects/16-production-agent/`:

```bash
uv run pytest -q
```

Expected: PASS. Every test so far, including `tests/test_fake_model.py`.

- [ ] **Step 6: Lint**

From `projects/16-production-agent/`:

```bash
uvx black --line-length 100 --target-version py311 --check -q src tests && uv run ruff check .
```

Expected: no output from black, `All checks passed!` from ruff. If black reports files, run `uvx black --line-length 100 --target-version py311 src tests` and re-run.

- [ ] **Step 7: Commit**

From the repository root:

```bash
git add projects/16-production-agent/src/fake_model.py projects/16-production-agent/tests/conftest.py projects/16-production-agent/tests/test_fake_model.py
git commit -m "feat(16): scripted Messages API on httpx2.MockTransport with a fake clock"
```

---

### Task 7: The instrumented run loop

**Files:**
- Modify: `projects/16-production-agent/src/agent.py` (replace with the full version)
- Modify: `projects/16-production-agent/tests/conftest.py` (add `exporter` and `tracer` fixtures)
- Test: `projects/16-production-agent/tests/test_agent.py` (replace with the full version)
- Test: `projects/16-production-agent/tests/test_live.py`

**Interfaces:**
- Consumes: `LoopGuard` (Task 5), `to_ns` (Task 3), `TOOL_FUNCTIONS` (Task 2), `CostTracker.record_usage` with Claude prices (Task 1), `mask_pii`, and the fake transport (Task 6) in tests.
- Produces: `run_agent(client: anthropic.Anthropic, version: AgentVersion, question: str, *, request_id: str, group: str, tracer: Tracer, perf: PerformanceTracker, costs: CostTracker, clock: Callable[[], float], max_iterations: int = 8) -> RunRecord`. It raises `anthropic.AuthenticationError`; every other API failure becomes outcome `api_error`.
- Produces (spans, children of whatever span is current): `llm.call` (kind LLM, with `llm.model_name`, `llm.token_count.prompt`, `llm.token_count.completion` and `llm.stop_reason`) and `tool.<name>` (kind TOOL, with `tool.name` and masked `input.value`/`output.value`, error status on failure).
- Produces (latency): `model.generate[<version>]` and `tool.<name>` in the `PerformanceTracker`.
- Produces (fixtures): `exporter -> InMemorySpanExporter`, `tracer -> Tracer` (synchronous export).

Tools are wrapped with `functools.wraps` before `beta_tool` sees them, so the schema and the SDK's pydantic argument validation both come from the original function. The loop guard checks each yielded message before the runner executes its tools, so breaking out of the loop means those tools never run. SDK 1.6's runner turns any exception a tool raises into an `is_error` result, and a run cut off by `max_iterations` ends on stop reason `tool_use`. Both were checked against the installed SDK.

- [ ] **Step 1: Replace `tests/conftest.py`**

Replace `projects/16-production-agent/tests/conftest.py`:

```python
"""Shared fixtures.

``pythonpath = ["src", "../.."]`` in ``pyproject.toml`` makes both this
project's ``src/`` and the repo root importable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer

from agent import RunRecord
from fake_model import FakeClock
from tracing import build_tracer_provider


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def tracer(exporter: InMemorySpanExporter) -> Tracer:
    return build_tracer_provider(exporter, batch=False).get_tracer("tests")


@pytest.fixture
def make_record() -> Callable[..., RunRecord]:
    """Build a :class:`RunRecord` with harmless defaults for the fields a test doesn't care about."""

    def make(**overrides: Any) -> RunRecord:
        fields: dict[str, Any] = {
            "request_id": "req-0001",
            "version": "v1",
            "group": "stable",
            "outcome": "ok",
            "duration_s": 2.0,
            "cost_usd": 0.01,
            "tool_calls": 2,
            "tool_errors": 0,
            "input_tokens": 1000,
            "output_tokens": 200,
        }
        fields.update(overrides)
        return RunRecord(**fields)

    return make
```

- [ ] **Step 2: Replace `tests/test_agent.py` with the full test**

Replace `projects/16-production-agent/tests/test_agent.py`:

```python
"""One run through the SDK's real Tool Runner, against scripted HTTP responses."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

import anthropic
import httpx2
import pytest
from cockpit.monitoring.cost_tracking import CostTracker
from cockpit.monitoring.performance_metrics import PerformanceTracker
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode, Tracer

from agent import CANARY, STABLE, AgentVersion, RunRecord, outcome_for, run_agent
from fake_model import (
    DRY_RUN_EPOCH,
    FakeClock,
    ModelProfile,
    Policy,
    Turn,
    scripted_client,
    text,
    tool_call,
    tool_history,
)
from tracing import build_tracer_provider


def where_is_order(messages: list[dict[str, Any]]) -> Turn:
    """lookup_order, then track_shipment, then answer."""
    history = tool_history(messages)
    if not history:
        return Turn(
            [text("Let me check."), tool_call("lookup_order", order_id="ORD-10042")], "tool_use"
        )
    if history[-1][0] == "lookup_order":
        return Turn([tool_call("track_shipment", tracking_id="TRK-5501")], "tool_use")
    return Turn([text("It was delivered on 2026-09-14.")])


def run(
    policy: Policy,
    *,
    clock: FakeClock,
    tracer: Tracer,
    version: AgentVersion = STABLE,
    **kwargs: Any,
) -> tuple[RunRecord, PerformanceTracker, CostTracker]:
    perf, costs = PerformanceTracker(clock=clock), CostTracker()
    client = scripted_client({version.model: ModelProfile(policy, latency_s=2.0)}, clock)
    record = run_agent(
        client,
        version,
        "Where is my order ORD-10042?",
        request_id="req-0001",
        group="stable",
        tracer=tracer,
        perf=perf,
        costs=costs,
        clock=clock,
        **kwargs,
    )
    return record, perf, costs


def by_name(spans: Sequence[ReadableSpan], name: str) -> list[ReadableSpan]:
    return [s for s in spans if s.name == name]


# ------------------------------------------------------------ version identity


def test_a_fingerprint_is_stable_and_covers_the_model() -> None:
    assert STABLE.fingerprint() == AgentVersion("v1", "claude-opus-5").fingerprint()
    assert STABLE.fingerprint() != CANARY.fingerprint()
    assert STABLE.fingerprint() != AgentVersion("v1", "claude-opus-5", effort="high").fingerprint()


@pytest.mark.parametrize(
    ("stop_reason", "outcome"),
    [
        ("end_turn", "ok"),
        ("tool_use", "max_iterations"),
        ("refusal", "refusal"),
        ("max_tokens", "incomplete"),
        ("model_context_window_exceeded", "incomplete"),
        (None, "incomplete"),
    ],
)
def test_outcome_for_each_final_stop_reason(stop_reason: str | None, outcome: str) -> None:
    assert outcome_for(stop_reason) == outcome


# ------------------------------------------------------------------- happy path


def test_a_clean_run_is_recorded_in_full(clock: FakeClock, tracer: Tracer) -> None:
    record, _, costs = run(where_is_order, clock=clock, tracer=tracer)
    # Three turns at 2 s each. Prompt tokens grow 400 per earlier turn:
    # 1200 + 1600 + 2000 in, 3 x 300 out, at $5 / $25 per million.
    assert record == RunRecord(
        request_id="req-0001",
        version="v1",
        group="stable",
        outcome="ok",
        duration_s=6.0,
        cost_usd=pytest.approx(0.0465),
        tool_calls=2,
        tool_errors=0,
        input_tokens=4800,
        output_tokens=900,
        answer="It was delivered on 2026-09-14.",
    )
    assert costs.total_cost() == pytest.approx(0.0465)


def test_every_turn_and_tool_call_gets_a_span(
    clock: FakeClock, tracer: Tracer, exporter: InMemorySpanExporter
) -> None:
    run(where_is_order, clock=clock, tracer=tracer)
    spans = exporter.get_finished_spans()
    assert [s.name for s in spans] == [
        "llm.call",
        "tool.lookup_order",
        "llm.call",
        "tool.track_shipment",
        "llm.call",
    ]

    first_llm = spans[0]
    assert first_llm.attributes["openinference.span.kind"] == "LLM"
    assert first_llm.attributes["llm.model_name"] == "claude-opus-5"
    assert first_llm.attributes["llm.token_count.prompt"] == 1200
    assert first_llm.attributes["llm.token_count.completion"] == 300
    assert first_llm.attributes["llm.stop_reason"] == "tool_use"
    assert (first_llm.start_time, first_llm.end_time) == (
        int(DRY_RUN_EPOCH * 1e9),
        int((DRY_RUN_EPOCH + 2) * 1e9),
    )

    lookup = spans[1]
    assert lookup.attributes["openinference.span.kind"] == "TOOL"
    assert lookup.attributes["tool.name"] == "lookup_order"
    assert json.loads(lookup.attributes["input.value"]) == {"order_id": "ORD-10042"}
    assert json.loads(lookup.attributes["output.value"])["tracking_id"] == "TRK-5501"


def test_spans_nest_under_the_callers_current_span(
    clock: FakeClock, tracer: Tracer, exporter: InMemorySpanExporter
) -> None:
    with tracer.start_as_current_span("agent.run") as root:
        run(where_is_order, clock=clock, tracer=tracer)
    children = [s for s in exporter.get_finished_spans() if s.name != "agent.run"]
    assert {s.parent.span_id for s in children} == {root.get_span_context().span_id}


def test_latency_is_recorded_per_model_version_and_per_tool(
    clock: FakeClock, tracer: Tracer
) -> None:
    _, perf, _ = run(where_is_order, clock=clock, tracer=tracer)
    assert perf.call_count("model.generate[v1]") == 3
    assert perf.summary("model.generate[v1]").p50_seconds == 2.0
    assert perf.call_count("tool.lookup_order") == 1
    assert perf.call_count("tool.track_shipment") == 1


# ---------------------------------------------------------------- failure modes


def test_the_loop_guard_stops_the_run_before_the_third_identical_call(
    clock: FakeClock, tracer: Tracer
) -> None:
    def stuck(messages: list[dict[str, Any]]) -> Turn:
        return Turn([tool_call("track_shipment", tracking_id="TRK-5503")], "tool_use")

    record, _, _ = run(stuck, clock=clock, tracer=tracer)
    assert record.outcome == "loop"
    assert record.loop_reason == "repeat:track_shipment"
    assert record.tool_calls == 2


def test_a_failing_tool_is_counted_and_the_model_sees_the_error(
    clock: FakeClock, tracer: Tracer, exporter: InMemorySpanExporter
) -> None:
    def bare_id(messages: list[dict[str, Any]]) -> Turn:
        history = tool_history(messages)
        if not history:
            return Turn([tool_call("lookup_order", order_id="10042")], "tool_use")
        _, _, result, is_error = history[-1]
        return Turn([text(f"is_error={is_error}: {result}")])

    record, perf, _ = run(bare_id, clock=clock, tracer=tracer)
    assert (record.outcome, record.tool_calls, record.tool_errors) == ("ok", 1, 1)
    assert record.answer.startswith("is_error=True: Invalid order ID '10042'")
    [span] = by_name(exporter.get_finished_spans(), "tool.lookup_order")
    assert span.status.status_code is StatusCode.ERROR
    assert perf.summary("tool.lookup_order").error_count == 1


def test_the_iteration_cap_ends_a_run_with_calls_pending(clock: FakeClock, tracer: Tracer) -> None:
    orders = iter(["ORD-10042", "ORD-10043", "ORD-10044"])

    def keeps_going(messages: list[dict[str, Any]]) -> Turn:
        return Turn([tool_call("lookup_order", order_id=next(orders))], "tool_use")

    record, _, _ = run(keeps_going, clock=clock, tracer=tracer, max_iterations=3)
    assert record.outcome == "max_iterations"


@pytest.mark.parametrize(
    ("stop_reason", "outcome"), [("refusal", "refusal"), ("max_tokens", "incomplete")]
)
def test_other_final_stop_reasons(
    clock: FakeClock, tracer: Tracer, stop_reason: str, outcome: str
) -> None:
    record, _, _ = run(lambda messages: Turn([], stop_reason), clock=clock, tracer=tracer)
    assert record.outcome == outcome


def _error(status: int, kind: str) -> Callable[[list[dict[str, Any]]], httpx2.Response]:
    return lambda messages: httpx2.Response(
        status, json={"type": "error", "error": {"type": kind, "message": kind}}
    )


@pytest.mark.parametrize(
    "policy",
    [_error(429, "rate_limit_error"), _error(500, "api_error"), _error(529, "overloaded_error")],
    ids=["429", "500", "529"],
)
def test_api_failures_become_an_api_error_outcome(
    clock: FakeClock, tracer: Tracer, policy: Policy
) -> None:
    record, _, _ = run(policy, clock=clock, tracer=tracer)
    assert record.outcome == "api_error"


def test_a_connection_failure_is_an_api_error(clock: FakeClock, tracer: Tracer) -> None:
    def unreachable(messages: list[dict[str, Any]]) -> Turn:
        raise httpx2.ConnectError("connection refused")

    record, _, _ = run(unreachable, clock=clock, tracer=tracer)
    assert record.outcome == "api_error"


def test_a_rejected_api_key_propagates(clock: FakeClock, tracer: Tracer) -> None:
    with pytest.raises(anthropic.AuthenticationError):
        run(_error(401, "authentication_error"), clock=clock, tracer=tracer)


class _BrokenExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise RuntimeError("collector unreachable")


def test_a_broken_exporter_never_fails_a_run(clock: FakeClock) -> None:
    tracer = build_tracer_provider(_BrokenExporter(), batch=False).get_tracer("tests")
    record, _, _ = run(where_is_order, clock=clock, tracer=tracer)
    assert record.outcome == "ok"


# ------------------------------------------------------------ request settings


def _capture_request(version: AgentVersion, clock: FakeClock, tracer: Tracer) -> dict[str, Any]:
    bodies: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": version.model,
                "content": [{"type": "text", "text": "Hi."}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    client = anthropic.Anthropic(
        api_key="test",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handle)),
    )
    run_agent(
        client,
        version,
        "Hello",
        request_id="r",
        group="stable",
        tracer=tracer,
        perf=PerformanceTracker(clock=clock),
        costs=CostTracker(),
        clock=clock,
    )
    [body] = bodies
    return body


def test_both_versions_send_the_same_request_apart_from_the_model(
    clock: FakeClock, tracer: Tracer
) -> None:
    stable = _capture_request(STABLE, clock, tracer)
    canary = _capture_request(CANARY, clock, tracer)
    assert stable.pop("model") == "claude-opus-5"
    assert canary.pop("model") == "claude-sonnet-5"
    assert stable == canary
    assert stable["thinking"] == {"type": "adaptive"}
    assert stable["output_config"] == {"effort": "medium"}
    assert stable["max_tokens"] == 16000
    assert [tool["name"] for tool in stable["tools"]] == [
        "lookup_order",
        "track_shipment",
        "refund_policy",
    ]
```

- [ ] **Step 3: Add the live test (skipped without a key)**

Create `projects/16-production-agent/tests/test_live.py`:

```python
"""The one test that calls the real API. Skipped unless ANTHROPIC_API_KEY is set."""

from __future__ import annotations

import os
import time

import anthropic
import pytest
from cockpit.monitoring.cost_tracking import CostTracker
from cockpit.monitoring.performance_metrics import PerformanceTracker
from opentelemetry.trace import Tracer

from agent import CANARY, run_agent


@pytest.mark.live_provider
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_a_real_run_through_the_tool_runner(tracer: Tracer) -> None:
    record = run_agent(
        anthropic.Anthropic(),
        CANARY,
        "Where is my order ORD-10042?",
        request_id="live-0001",
        group="canary",
        tracer=tracer,
        perf=PerformanceTracker(),
        costs=CostTracker(),
        clock=time.time,
    )
    assert record.outcome == "ok"
    assert record.tool_calls >= 2
    assert "deliver" in record.answer.lower()
```

- [ ] **Step 4: Run it to verify it fails**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_agent.py
```

Expected: FAIL with `ImportError: cannot import name 'run_agent' from 'agent'`.

- [ ] **Step 5: Replace `src/agent.py` with the full version**

Replace `projects/16-production-agent/src/agent.py`:

```python
"""One agent run: the SDK Tool Runner loop, instrumented.

Everything a run produces is measured once, here: an ``llm.call`` span, a
latency sample and a cost entry for every model turn, and a ``tool.<name>``
span and latency sample for every tool call. The loop guard sees each turn's
tool calls *before* the runner executes them; breaking out of the loop at that
point means those tools never run.

The root ``agent.run`` span belongs to the caller (the rollout controller),
which also decides what the run means for alerts and the canary. This module
only reports what happened, as a :class:`RunRecord`.
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

import anthropic
from anthropic import beta_tool
from anthropic.lib.tools import BetaFunctionTool
from anthropic.types.beta import BetaMessage
from cockpit.monitoring.cost_tracking import CostTracker
from cockpit.monitoring.performance_metrics import CallOutcome, PerformanceTracker
from cockpit.security.output_security import mask_pii
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry.trace import Status, StatusCode, Tracer

from alerts import LoopGuard
from tools import TOOL_FUNCTIONS
from tracing import to_ns

PROJECT: Final = "16-production-agent"
MAX_ITERATIONS: Final = 8

SYSTEM_PROMPT: Final = (
    "You are the order-support assistant for an outdoor gear shop. Answer questions "
    "about orders, shipments and returns using the tools, and keep answers short.\n\n"
    "Order IDs have the form ORD-12345. If a customer gives a bare order number such "
    "as 10042, call lookup_order with ORD-10042. If a tool returns an error, tell the "
    "customer plainly what went wrong instead of guessing."
)

KIND: Final = SpanAttributes.OPENINFERENCE_SPAN_KIND


@dataclass(frozen=True)
class AgentVersion:
    """Everything that defines one deployable version of the agent.

    Attributes:
        version: Registry version identifier, e.g. ``"v1"``.
        model: Claude model ID.
        system_prompt: System prompt sent on every request.
        effort: ``output_config.effort`` level.
        max_tokens: Per-response output cap.
    """

    version: str
    model: str
    system_prompt: str = SYSTEM_PROMPT
    effort: str = "medium"
    max_tokens: int = 16000

    def fingerprint(self) -> str:
        """Digest of every setting that shapes this version's requests.

        Returns:
            Hex SHA-256 over model, prompt, thinking mode, effort, output cap
            and tool schemas.
        """
        payload = {
            "model": self.model,
            "system": self.system_prompt,
            "thinking": "adaptive",
            "effort": self.effort,
            "max_tokens": self.max_tokens,
            "tools": [beta_tool(fn).to_dict() for fn in TOOL_FUNCTIONS],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


STABLE: Final = AgentVersion("v1", "claude-opus-5")
CANARY: Final = AgentVersion("v2", "claude-sonnet-5")


@dataclass(frozen=True)
class RunRecord:
    """What one run did, as alerts and the canary judge see it.

    Attributes:
        request_id: Request identifier.
        version: Version that served it.
        group: ``"stable"`` or ``"canary"``.
        outcome: ``ok``, ``loop``, ``max_iterations``, ``refusal``,
            ``incomplete`` or ``api_error``.
        duration_s: Wall time of the whole run, in seconds.
        cost_usd: Cost of every model turn in the run.
        tool_calls: Tool calls executed.
        tool_errors: Tool calls that raised.
        input_tokens: Prompt tokens across all turns.
        output_tokens: Output tokens across all turns.
        answer: Text of the final assistant turn.
        loop_reason: Loop guard verdict when the outcome is ``loop``.
    """

    request_id: str
    version: str
    group: str
    outcome: str
    duration_s: float
    cost_usd: float
    tool_calls: int
    tool_errors: int
    input_tokens: int
    output_tokens: int
    answer: str = ""
    loop_reason: str | None = None


def outcome_for(stop_reason: str | None) -> str:
    """Map the final turn's stop reason to a run outcome.

    Args:
        stop_reason: ``stop_reason`` of the last message the runner yielded.

    Returns:
        ``ok`` for ``end_turn``; ``max_iterations`` for ``tool_use`` (the
        runner stopped with tool calls still pending); ``refusal``; otherwise
        ``incomplete``.
    """
    if stop_reason == "end_turn":
        return "ok"
    if stop_reason == "tool_use":
        return "max_iterations"
    if stop_reason == "refusal":
        return "refusal"
    return "incomplete"


@dataclass
class _Run:
    """Per-run state shared between the loop and the instrumented tools."""

    tracer: Tracer
    clock: Callable[[], float]
    perf: PerformanceTracker
    mark: float
    tool_calls: int = 0
    tool_errors: int = 0

    def call_tool(self, fn: Callable[..., str], kwargs: dict[str, Any]) -> str:
        """Run one tool inside a span, counting calls, errors and latency.

        Exceptions are re-raised: the Tool Runner turns them into
        ``is_error`` tool results.
        """
        name = fn.__name__
        start = self.clock()
        span = self.tracer.start_span(
            f"tool.{name}",
            start_time=to_ns(start),
            attributes={
                KIND: OpenInferenceSpanKindValues.TOOL.value,
                SpanAttributes.TOOL_NAME: name,
                SpanAttributes.INPUT_VALUE: mask_pii(json.dumps(kwargs)),
            },
        )
        self.tool_calls += 1
        outcome = CallOutcome.SUCCESS
        try:
            result = fn(**kwargs)
        except Exception as exc:
            outcome = CallOutcome.ERROR
            self.tool_errors += 1
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            span.set_attribute(SpanAttributes.OUTPUT_VALUE, mask_pii(result))
            return result
        finally:
            end = self.clock()
            self.perf.record(f"tool.{name}", end - start, outcome=outcome)
            span.end(end_time=to_ns(end))
            self.mark = end


def _instrument(fn: Callable[..., str], run: _Run) -> BetaFunctionTool[Any]:
    """Wrap a tool so each call is traced; ``wraps`` keeps its schema and validation."""

    @functools.wraps(fn)
    def wrapper(**kwargs: Any) -> str:
        return run.call_tool(fn, kwargs)

    return beta_tool(wrapper)


def _llm_span(
    tracer: Tracer, model: str, message: BetaMessage, *, start: float, end: float
) -> None:
    tracer.start_span(
        "llm.call",
        start_time=to_ns(start),
        attributes={
            KIND: OpenInferenceSpanKindValues.LLM.value,
            SpanAttributes.LLM_MODEL_NAME: model,
            SpanAttributes.LLM_TOKEN_COUNT_PROMPT: message.usage.input_tokens,
            SpanAttributes.LLM_TOKEN_COUNT_COMPLETION: message.usage.output_tokens,
            "llm.stop_reason": message.stop_reason or "",
        },
    ).end(end_time=to_ns(end))


def run_agent(
    client: anthropic.Anthropic,
    version: AgentVersion,
    question: str,
    *,
    request_id: str,
    group: str,
    tracer: Tracer,
    perf: PerformanceTracker,
    costs: CostTracker,
    clock: Callable[[], float],
    max_iterations: int = MAX_ITERATIONS,
) -> RunRecord:
    """Answer one question with the Tool Runner and report what happened.

    Spans are created under whatever span is current, so the caller should
    make its ``agent.run`` span current first.

    Args:
        client: Anthropic client (real, or the dry-run fake).
        version: Version serving this request.
        question: The customer's question.
        request_id: Request identifier, copied into the record.
        group: Canary group, copied into the record.
        tracer: Tracer for ``llm.call`` and ``tool.*`` spans.
        perf: Receives ``model.generate[<version>]`` and ``tool.<name>`` latencies.
        costs: Receives one cost entry per model turn.
        clock: Epoch-seconds clock used for every timestamp and duration.
        max_iterations: Hard cap on model turns.

    Returns:
        The run's record.

    Raises:
        anthropic.AuthenticationError: The API key was rejected. Every
            other API failure becomes outcome ``api_error``.
    """
    start = clock()
    run = _Run(tracer=tracer, clock=clock, perf=perf, mark=start)
    guard = LoopGuard()
    last: BetaMessage | None = None
    outcome: str | None = None
    loop_reason: str | None = None
    cost = 0.0
    input_tokens = output_tokens = 0

    runner = client.beta.messages.tool_runner(
        model=version.model,
        max_tokens=version.max_tokens,
        system=version.system_prompt,
        thinking={"type": "adaptive"},
        output_config={"effort": version.effort},
        tools=[_instrument(fn, run) for fn in TOOL_FUNCTIONS],
        messages=[{"role": "user", "content": question}],
        max_iterations=max_iterations,
    )
    try:
        for message in runner:
            end = clock()
            usage = message.usage
            _llm_span(tracer, version.model, message, start=run.mark, end=end)
            perf.record(
                f"model.generate[{version.version}]",
                end - run.mark,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            )
            cost += costs.record_usage(
                PROJECT, version.model, usage.input_tokens, usage.output_tokens
            ).cost_usd
            input_tokens += usage.input_tokens
            output_tokens += usage.output_tokens
            last = message
            run.mark = end
            loop_reason = guard.check(
                (b.name, b.input) for b in message.content if b.type == "tool_use"
            )
            if loop_reason:
                outcome = "loop"
                break
    except anthropic.AuthenticationError:
        raise
    except (anthropic.APIStatusError, anthropic.APIConnectionError):
        outcome = "api_error"

    if outcome is None:
        outcome = outcome_for(last.stop_reason if last else None)
    answer = "".join(b.text for b in last.content if b.type == "text") if last else ""
    return RunRecord(
        request_id=request_id,
        version=version.version,
        group=group,
        outcome=outcome,
        duration_s=clock() - start,
        cost_usd=cost,
        tool_calls=run.tool_calls,
        tool_errors=run.tool_errors,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        answer=answer,
        loop_reason=loop_reason,
    )
```

- [ ] **Step 6: Run the whole suite**

From `projects/16-production-agent/`:

```bash
uv run pytest -q
```

Expected: PASS, with 1 skipped (`test_live.py`, no key).

- [ ] **Step 7: Lint**

From `projects/16-production-agent/`:

```bash
uvx black --line-length 100 --target-version py311 --check -q src tests && uv run ruff check .
```

Expected: no output from black, `All checks passed!` from ruff. If black reports files, run `uvx black --line-length 100 --target-version py311 src tests` and re-run.

- [ ] **Step 8: Commit**

From the repository root:

```bash
git add projects/16-production-agent/src/agent.py projects/16-production-agent/tests/conftest.py projects/16-production-agent/tests/test_agent.py projects/16-production-agent/tests/test_live.py
git commit -m "feat(16): run loop with llm/tool spans, loop guard, costs and error outcomes"
```

> **Checkpoint:** The agent runs end to end against the real Tool Runner. Show the user a trace: `uv run pytest -q tests/test_agent.py -k spans -s` passes, then summarize the span tree before continuing.

---

### Task 8: Canary routing, the judge, and version registration

**Files:**
- Create: `projects/16-production-agent/src/canary.py` (first half; Task 9 completes it)
- Modify: `projects/16-production-agent/tests/conftest.py` (final version: reset the governance trail)
- Test: `projects/16-production-agent/tests/test_canary.py` (first half; Task 9 replaces it)

**Interfaces:**
- Consumes: `AgentVersion`, `STABLE`, `CANARY` (Task 4); `WindowStats` (Task 5); `cockpit.governance.model_versioning.ModelRegistry`/`ModelStage`.
- Produces: `route(request_id: str, percent: int) -> bool`; `Verdict` (`CONTINUE`/`ABORT`/`READY`); `Judgement(verdict, reason)`; `judge(canary: WindowStats, stable: WindowStats, *, canary_total: int, canary_firing: Collection[str]) -> Judgement`; `register_versions(registry, stable, canary) -> None`.
- Produces (constants): `MODEL_NAME = "order-support-agent"`, `ACTOR = "canary-judge"`, `DEFAULT_CANARY_PERCENT = 10`, `MIN_CANARY_RUNS = 10`, `MIN_STABLE_RUNS = 10`, `READY_AFTER = 30`, `FAILURE_MARGIN = 0.05`, `P95_RATIO = 1.5`, `COST_RATIO = 1.2`, `REQUIRED_APPROVALS = 2`, `WATCH_RUNS = 30`.

The registry records every stage change on the cockpit's process-wide governance trail, so from here on `conftest.py` resets that trail around every test, as project 12 does.

- [ ] **Step 1: Replace `tests/conftest.py` with its final version**

Replace `projects/16-production-agent/tests/conftest.py`:

```python
"""Shared fixtures.

``pythonpath = ["src", "../.."]`` in ``pyproject.toml`` makes both this
project's ``src/`` and the repo root importable. The cockpit registry and
approval workflow always write to the process-wide default governance trail,
so every test starts and ends with that trail empty, as in project 12.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from cockpit.governance.audit_trail import get_default_trail
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer

from agent import RunRecord
from fake_model import FakeClock
from tracing import build_tracer_provider


@pytest.fixture(autouse=True)
def _fresh_governance_trail() -> Iterator[None]:
    trail = get_default_trail()
    trail.reset()
    yield
    trail.reset()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def tracer(exporter: InMemorySpanExporter) -> Tracer:
    return build_tracer_provider(exporter, batch=False).get_tracer("tests")


@pytest.fixture
def make_record() -> Callable[..., RunRecord]:
    """Build a :class:`RunRecord` with harmless defaults for the fields a test doesn't care about."""

    def make(**overrides: Any) -> RunRecord:
        fields: dict[str, Any] = {
            "request_id": "req-0001",
            "version": "v1",
            "group": "stable",
            "outcome": "ok",
            "duration_s": 2.0,
            "cost_usd": 0.01,
            "tool_calls": 2,
            "tool_errors": 0,
            "input_tokens": 1000,
            "output_tokens": 200,
        }
        fields.update(overrides)
        return RunRecord(**fields)

    return make
```

- [ ] **Step 2: Write the failing test**

Create `projects/16-production-agent/tests/test_canary.py`:

```python
"""Routing, the judge and version registration (Task 9 replaces this file with the full test)."""

from __future__ import annotations

from typing import Any

import pytest
from cockpit.governance.model_versioning import ModelRegistry, ModelStage

from agent import CANARY, STABLE
from alerts import WindowStats
from canary import MODEL_NAME, READY_AFTER, Verdict, judge, register_versions, route


def stats(runs: int = 20, **overrides: Any) -> WindowStats:
    fields: dict[str, Any] = {
        "loops": 0,
        "failure_rate": 0.0,
        "tool_error_rate": 0.0,
        "p95_s": 5.0,
        "mean_cost": 0.04,
    }
    fields.update(overrides)
    return WindowStats(runs=runs, **fields)


# ---------------------------------------------------------------------- route


def test_routing_is_deterministic() -> None:
    assert all(route(f"req-{n:04d}", 10) == route(f"req-{n:04d}", 10) for n in range(200))


def test_about_the_requested_share_goes_to_the_canary() -> None:
    share = sum(route(f"req-{n:04d}", 10) for n in range(1000)) / 1000
    assert 0.05 <= share <= 0.15


@pytest.mark.parametrize(("percent", "expected"), [(0, False), (100, True)])
def test_zero_and_full_rollouts(percent: int, expected: bool) -> None:
    assert {route(f"req-{n}", percent) for n in range(100)} == {expected}


# ---------------------------------------------------------------------- judge


@pytest.mark.parametrize(
    ("canary", "stable", "total", "firing", "verdict"),
    [
        (stats(runs=1), stats(), 1, {"loop_detected"}, Verdict.ABORT),
        (stats(runs=9), stats(), 9, set(), Verdict.CONTINUE),
        (stats(failure_rate=0.11), stats(failure_rate=0.05), 12, set(), Verdict.ABORT),
        (stats(failure_rate=0.10), stats(failure_rate=0.05), 12, set(), Verdict.CONTINUE),
        (stats(p95_s=7.6), stats(p95_s=5.0), 12, set(), Verdict.ABORT),
        (stats(p95_s=7.5), stats(p95_s=5.0), 12, set(), Verdict.CONTINUE),
        (stats(mean_cost=0.049), stats(mean_cost=0.04), 12, set(), Verdict.ABORT),
        (stats(mean_cost=0.048), stats(mean_cost=0.04), 12, set(), Verdict.CONTINUE),
        (
            stats(p95_s=9.0, mean_cost=9.0),
            stats(p95_s=0.0, mean_cost=0.0),
            12,
            set(),
            Verdict.CONTINUE,
        ),
        (stats(failure_rate=0.9), stats(runs=9), 12, set(), Verdict.CONTINUE),
        (stats(), stats(), READY_AFTER, set(), Verdict.READY),
        (stats(), stats(), READY_AFTER - 1, set(), Verdict.CONTINUE),
    ],
    ids=[
        "alert-aborts-at-any-count",
        "below-minimum-runs",
        "failure-margin-exceeded",
        "failure-margin-met",
        "p95-ratio-exceeded",
        "p95-ratio-met",
        "cost-ratio-exceeded",
        "cost-ratio-met",
        "zero-stable-values-skip-ratios",
        "thin-stable-window-skips-comparison",
        "ready",
        "one-short-of-ready",
    ],
)
def test_judge(
    canary: WindowStats, stable: WindowStats, total: int, firing: set[str], verdict: Verdict
) -> None:
    assert judge(canary, stable, canary_total=total, canary_firing=firing).verdict is verdict


def test_an_abort_says_why() -> None:
    reason = judge(stats(runs=1), stats(), canary_total=1, canary_firing={"loop_detected"}).reason
    assert reason == "alert firing: loop_detected"


# ------------------------------------------------------------------ registry


def test_register_versions_puts_stable_in_production_and_canary_in_staging() -> None:
    registry = ModelRegistry()
    register_versions(registry, STABLE, CANARY)
    production = registry.get_production(MODEL_NAME)
    assert (production.version, production.provider_model_id) == ("v1", "claude-opus-5")
    assert production.config_fingerprint == STABLE.fingerprint()
    assert registry.get(MODEL_NAME, "v2").stage is ModelStage.STAGING
```

- [ ] **Step 3: Run it to verify it fails**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_canary.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'canary'`.

- [ ] **Step 4: Write the first half of `canary.py`**

Create `projects/16-production-agent/src/canary.py`:

```python
"""Canary rollout: routing, the judge, and the controller that acts on it.

The phases run ``CANARY -> (WATCH) -> DONE``:

* **CANARY:** a fixed share of requests goes to the canary version. After
  each canary run, :func:`judge` compares both versions' windows.
* **Ready:** traffic pauses while the controller opens an approval request
  that needs :data:`REQUIRED_APPROVALS` distinct humans. Only ``APPROVED``
  promotes; anything else aborts.
* **WATCH:** the promoted version serves everything for :data:`WATCH_RUNS`
  runs. Its first alert rolls production back automatically, with no
  approval, the same as project 12: a rollback has to work at 3am.

Every stage change goes through the cockpit ``ModelRegistry`` and every
approval through ``ApprovalWorkflow``, so both land on the governance trail.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection
from enum import StrEnum
from typing import Final, NamedTuple

from cockpit.governance.model_versioning import ModelRegistry, ModelStage

from agent import AgentVersion
from alerts import WindowStats

MODEL_NAME: Final = "order-support-agent"
ACTOR: Final = "canary-judge"
DEFAULT_CANARY_PERCENT: Final = 10
MIN_CANARY_RUNS: Final = 10
MIN_STABLE_RUNS: Final = 10
READY_AFTER: Final = 30
FAILURE_MARGIN: Final = 0.05
P95_RATIO: Final = 1.5
COST_RATIO: Final = 1.2
REQUIRED_APPROVALS: Final = 2
WATCH_RUNS: Final = 30


def route(request_id: str, percent: int) -> bool:
    """Whether a request belongs to the canary group.

    Args:
        request_id: Request identifier; the same ID always gets the same answer.
        percent: Share of requests routed to the canary, 0-100.

    Returns:
        True for the canary group.
    """
    return int(hashlib.sha256(request_id.encode()).hexdigest()[:8], 16) % 100 < percent


class Verdict(StrEnum):
    """What the judge decided after a canary run."""

    CONTINUE = "continue"
    ABORT = "abort"
    READY = "ready"


class Judgement(NamedTuple):
    """A verdict and the reason for it."""

    verdict: Verdict
    reason: str


def judge(
    canary: WindowStats,
    stable: WindowStats,
    *,
    canary_total: int,
    canary_firing: Collection[str],
) -> Judgement:
    """Decide whether the canary continues, is aborted, or is ready to promote.

    Args:
        canary: The canary version's window stats.
        stable: The stable version's window stats.
        canary_total: Canary runs so far, across the whole rollout.
        canary_firing: Alert rules currently firing for the canary.

    Returns:
        The judgement. Ratio checks are skipped when the stable value is 0.
    """
    if canary_firing:
        return Judgement(Verdict.ABORT, "alert firing: " + ", ".join(sorted(canary_firing)))
    if canary_total < MIN_CANARY_RUNS:
        return Judgement(Verdict.CONTINUE, f"{canary_total}/{MIN_CANARY_RUNS} canary runs")
    if stable.runs >= MIN_STABLE_RUNS:
        if canary.failure_rate > stable.failure_rate + FAILURE_MARGIN:
            return Judgement(
                Verdict.ABORT,
                f"failure rate {canary.failure_rate:.0%} vs stable {stable.failure_rate:.0%}",
            )
        if stable.p95_s > 0 and canary.p95_s > P95_RATIO * stable.p95_s:
            return Judgement(
                Verdict.ABORT, f"p95 {canary.p95_s:.1f}s vs stable {stable.p95_s:.1f}s"
            )
        if stable.mean_cost > 0 and canary.mean_cost > COST_RATIO * stable.mean_cost:
            return Judgement(
                Verdict.ABORT,
                f"cost/run ${canary.mean_cost:.4f} vs stable ${stable.mean_cost:.4f}",
            )
    if canary_total >= READY_AFTER:
        return Judgement(Verdict.READY, f"{canary_total} canary runs, within margins")
    return Judgement(Verdict.CONTINUE, "within margins")


def register_versions(registry: ModelRegistry, stable: AgentVersion, canary: AgentVersion) -> None:
    """Put ``stable`` in production and ``canary`` in staging.

    Args:
        registry: The registry to populate; must not hold these versions yet.
        stable: Version to serve production.
        canary: Version to trial.
    """
    for version in (stable, canary):
        registry.register(
            MODEL_NAME,
            version.version,
            provider_model_id=version.model,
            config_fingerprint=version.fingerprint(),
            actor_id=ACTOR,
        )
        registry.promote(MODEL_NAME, version.version, ModelStage.STAGING, actor_id=ACTOR)
    registry.promote(MODEL_NAME, stable.version, ModelStage.PRODUCTION, actor_id=ACTOR)
```

- [ ] **Step 5: Run it to verify it passes**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_canary.py
```

Expected: PASS.

- [ ] **Step 6: Lint**

From `projects/16-production-agent/`:

```bash
uvx black --line-length 100 --target-version py311 --check -q src tests && uv run ruff check .
```

Expected: no output from black, `All checks passed!` from ruff. If black reports files, run `uvx black --line-length 100 --target-version py311 src tests` and re-run.

- [ ] **Step 7: Commit**

From the repository root:

```bash
git add projects/16-production-agent/src/canary.py projects/16-production-agent/tests/conftest.py projects/16-production-agent/tests/test_canary.py
git commit -m "feat(16): canary hash routing, judge and version registration"
```

---

### Task 9: The rollout controller: abort, approval, promotion, watch, rollback

**Files:**
- Modify: `projects/16-production-agent/src/canary.py` (replace with the full version)
- Test: `projects/16-production-agent/tests/test_canary.py` (replace with the full version)

**Interfaces:**
- Consumes: `RunRecord`, `AgentVersion` (Task 4); `AlertMonitor`, `RunWindow` (Task 5); `to_ns` (Task 3); cockpit `ApprovalWorkflow`, `ApprovalStatus`, `PerformanceTracker`, `mask_pii`.
- Produces: `Phase` (`CANARY`/`WATCH`/`DONE`); `RunFn = Callable[[AgentVersion, str, str, str], RunRecord]` (version, question, request_id, group); `ApproveFn = Callable[[ApprovalWorkflow, ApprovalRequest], None]`.
- Produces: `RolloutController(registry, workflow, stable, canary, run, approve, tracer, monitor, perf, clock, canary_percent=10)` with `.handle(request_id: str, question: str) -> RunRecord`, `.phase`, `.timeline: list[tuple[str, str]]` and `.window`.
- Produces (root span `agent.run`, kind AGENT): `request.id`, `canary.group`, `agent.version`, `llm.model_name`, masked `input.value`/`output.value`, `agent.outcome`, `agent.cost_usd`, `agent.loop.reason`; error status for any outcome but `ok`; events `alert.fired`, `alert.resolved`, `canary.aborted`, `canary.promoted` and `canary.rolled_back`.

The controller owns each request's root span, makes it current while `run` executes (so `run_agent`'s spans nest under it), then evaluates alerts and the judge before closing it. Approval blocks traffic and happens inside the run, so requests never expire. Anything short of `APPROVED` aborts. Rollback needs no approval.

- [ ] **Step 1: Replace `tests/test_canary.py` with the full test**

Replace `projects/16-production-agent/tests/test_canary.py`:

```python
"""Routing, the judge, and the rollout controller (with a fake run function)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from cockpit.governance.approval_workflow import ApprovalRequest, ApprovalWorkflow
from cockpit.governance.audit_trail import verify_trail_integrity
from cockpit.governance.model_versioning import ModelRegistry, ModelStage
from cockpit.monitoring.performance_metrics import PerformanceTracker
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode, Tracer

from agent import CANARY, STABLE, AgentVersion, RunRecord
from alerts import AlertMonitor, WindowStats
from canary import (
    MODEL_NAME,
    READY_AFTER,
    WATCH_RUNS,
    Phase,
    RolloutController,
    Verdict,
    judge,
    register_versions,
    route,
)
from fake_model import FakeClock


def stats(runs: int = 20, **overrides: Any) -> WindowStats:
    fields: dict[str, Any] = {
        "loops": 0,
        "failure_rate": 0.0,
        "tool_error_rate": 0.0,
        "p95_s": 5.0,
        "mean_cost": 0.04,
    }
    fields.update(overrides)
    return WindowStats(runs=runs, **fields)


# ---------------------------------------------------------------------- route


def test_routing_is_deterministic() -> None:
    assert all(route(f"req-{n:04d}", 10) == route(f"req-{n:04d}", 10) for n in range(200))


def test_about_the_requested_share_goes_to_the_canary() -> None:
    share = sum(route(f"req-{n:04d}", 10) for n in range(1000)) / 1000
    assert 0.05 <= share <= 0.15


@pytest.mark.parametrize(("percent", "expected"), [(0, False), (100, True)])
def test_zero_and_full_rollouts(percent: int, expected: bool) -> None:
    assert {route(f"req-{n}", percent) for n in range(100)} == {expected}


# ---------------------------------------------------------------------- judge


@pytest.mark.parametrize(
    ("canary", "stable", "total", "firing", "verdict"),
    [
        (stats(runs=1), stats(), 1, {"loop_detected"}, Verdict.ABORT),
        (stats(runs=9), stats(), 9, set(), Verdict.CONTINUE),
        (stats(failure_rate=0.11), stats(failure_rate=0.05), 12, set(), Verdict.ABORT),
        (stats(failure_rate=0.10), stats(failure_rate=0.05), 12, set(), Verdict.CONTINUE),
        (stats(p95_s=7.6), stats(p95_s=5.0), 12, set(), Verdict.ABORT),
        (stats(p95_s=7.5), stats(p95_s=5.0), 12, set(), Verdict.CONTINUE),
        (stats(mean_cost=0.049), stats(mean_cost=0.04), 12, set(), Verdict.ABORT),
        (stats(mean_cost=0.048), stats(mean_cost=0.04), 12, set(), Verdict.CONTINUE),
        (
            stats(p95_s=9.0, mean_cost=9.0),
            stats(p95_s=0.0, mean_cost=0.0),
            12,
            set(),
            Verdict.CONTINUE,
        ),
        (stats(failure_rate=0.9), stats(runs=9), 12, set(), Verdict.CONTINUE),
        (stats(), stats(), READY_AFTER, set(), Verdict.READY),
        (stats(), stats(), READY_AFTER - 1, set(), Verdict.CONTINUE),
    ],
    ids=[
        "alert-aborts-at-any-count",
        "below-minimum-runs",
        "failure-margin-exceeded",
        "failure-margin-met",
        "p95-ratio-exceeded",
        "p95-ratio-met",
        "cost-ratio-exceeded",
        "cost-ratio-met",
        "zero-stable-values-skip-ratios",
        "thin-stable-window-skips-comparison",
        "ready",
        "one-short-of-ready",
    ],
)
def test_judge(
    canary: WindowStats, stable: WindowStats, total: int, firing: set[str], verdict: Verdict
) -> None:
    assert judge(canary, stable, canary_total=total, canary_firing=firing).verdict is verdict


def test_an_abort_says_why() -> None:
    reason = judge(stats(runs=1), stats(), canary_total=1, canary_firing={"loop_detected"}).reason
    assert reason == "alert firing: loop_detected"


# ------------------------------------------------------------------ registry


def test_register_versions_puts_stable_in_production_and_canary_in_staging() -> None:
    registry = ModelRegistry()
    register_versions(registry, STABLE, CANARY)
    production = registry.get_production(MODEL_NAME)
    assert (production.version, production.provider_model_id) == ("v1", "claude-opus-5")
    assert production.config_fingerprint == STABLE.fingerprint()
    assert registry.get(MODEL_NAME, "v2").stage is ModelStage.STAGING


# ---------------------------------------------------------------- controller


class FakeRuns:
    """Stands in for run_agent: returns records whose outcome the test controls."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.outcomes: dict[str, str] = {"v1": "ok", "v2": "ok"}
        self.calls: list[tuple[str, str]] = []

    def __call__(
        self, version: AgentVersion, question: str, request_id: str, group: str
    ) -> RunRecord:
        self.clock.advance(2.0)
        self.calls.append((version.version, group))
        outcome = self.outcomes[version.version]
        return RunRecord(
            request_id=request_id,
            version=version.version,
            group=group,
            outcome=outcome,
            duration_s=2.0,
            cost_usd=0.02,
            tool_calls=2,
            tool_errors=0,
            input_tokens=1000,
            output_tokens=200,
            answer="Delivered.",
            loop_reason="repeat:track_shipment" if outcome == "loop" else None,
        )


def approvals(*decisions: tuple[str, bool]) -> Callable[[ApprovalWorkflow, ApprovalRequest], None]:
    def approve(workflow: ApprovalWorkflow, request: ApprovalRequest) -> None:
        for principal, approved in decisions:
            workflow.decide(request.request_id, approved, principal)

    return approve


APPROVED = approvals(("alice", True), ("bob", True))


def controller(
    clock: FakeClock,
    tracer: Tracer,
    runs: FakeRuns,
    *,
    approve: Callable[[ApprovalWorkflow, ApprovalRequest], None] = APPROVED,
    percent: int = 100,
) -> RolloutController:
    registry = ModelRegistry()
    register_versions(registry, STABLE, CANARY)
    return RolloutController(
        registry=registry,
        workflow=ApprovalWorkflow(),
        stable=STABLE,
        canary=CANARY,
        run=runs,
        approve=approve,
        tracer=tracer,
        monitor=AlertMonitor(),
        perf=PerformanceTracker(clock=clock),
        clock=clock,
        canary_percent=percent,
    )


def serve(ctl: RolloutController, count: int, start: int = 1) -> None:
    for n in range(start, start + count):
        ctl.handle(f"req-{n:04d}", "Where is my order ORD-10042?")


def stage(ctl: RolloutController, version: str) -> ModelStage:
    return ctl.registry.get(MODEL_NAME, version).stage


def test_the_canary_gets_its_share_and_stable_the_rest(clock: FakeClock, tracer: Tracer) -> None:
    runs = FakeRuns(clock)
    ctl = controller(clock, tracer, runs, percent=10)
    serve(ctl, 100)
    groups = [group for _, group in runs.calls]
    assert groups.count("canary") == sum(route(f"req-{n:04d}", 10) for n in range(1, 101))
    assert set(runs.calls) == {("v1", "stable"), ("v2", "canary")}


def test_a_canary_alert_aborts_the_rollout(clock: FakeClock, tracer: Tracer) -> None:
    runs = FakeRuns(clock)
    runs.outcomes["v2"] = "loop"
    ctl = controller(clock, tracer, runs)
    serve(ctl, 3)
    assert ctl.phase is Phase.DONE
    assert stage(ctl, "v2") is ModelStage.DEVELOPMENT
    assert runs.calls == [("v2", "canary"), ("v1", "stable"), ("v1", "stable")]
    assert ctl.timeline[-1] == (
        "req-0001",
        "canary aborted (alert firing: loop_detected): v2 back to development",
    )


def test_ready_asks_two_humans_then_promotes(clock: FakeClock, tracer: Tracer) -> None:
    seen: list[ApprovalRequest] = []

    def approve(workflow: ApprovalWorkflow, request: ApprovalRequest) -> None:
        seen.append(request)
        APPROVED(workflow, request)

    ctl = controller(clock, tracer, FakeRuns(clock), approve=approve)
    serve(ctl, READY_AFTER)
    [request] = seen
    assert request.required_approvals == 2
    assert request.requested_by == "canary-judge"
    assert request.description.startswith(
        "Promote order-support-agent v2 (claude-sonnet-5) to production."
    )
    assert ctl.phase is Phase.WATCH
    assert ctl.registry.get_production(MODEL_NAME).version == "v2"
    assert [event for _, event in ctl.timeline[-3:]] == [
        "alice approved",
        "bob approved",
        "promoted: v2 to production; watching 30 runs",
    ]


@pytest.mark.parametrize(
    "approve",
    [approvals(("alice", True), ("bob", False)), approvals(("alice", True))],
    ids=["rejected", "still-pending"],
)
def test_anything_short_of_approval_aborts(
    clock: FakeClock, tracer: Tracer, approve: Callable[[ApprovalWorkflow, ApprovalRequest], None]
) -> None:
    ctl = controller(clock, tracer, FakeRuns(clock), approve=approve)
    serve(ctl, READY_AFTER)
    assert ctl.phase is Phase.DONE
    assert ctl.registry.get_production(MODEL_NAME).version == "v1"
    assert stage(ctl, "v2") is ModelStage.DEVELOPMENT


def test_an_alert_during_the_watch_rolls_back(clock: FakeClock, tracer: Tracer) -> None:
    runs = FakeRuns(clock)
    ctl = controller(clock, tracer, runs)
    serve(ctl, READY_AFTER)
    runs.outcomes["v2"] = "loop"
    serve(ctl, 1, start=READY_AFTER + 1)
    assert ctl.phase is Phase.DONE
    assert ctl.registry.get_production(MODEL_NAME).version == "v1"
    assert stage(ctl, "v2") is ModelStage.STAGING
    assert ctl.timeline[-1] == ("req-0031", "rolled back (loop_detected): v1 back in production")
    assert verify_trail_integrity()


def test_a_clean_watch_keeps_the_new_version(clock: FakeClock, tracer: Tracer) -> None:
    ctl = controller(clock, tracer, FakeRuns(clock))
    serve(ctl, READY_AFTER + WATCH_RUNS)
    assert ctl.phase is Phase.DONE
    assert ctl.registry.get_production(MODEL_NAME).version == "v2"
    assert ctl.timeline[-1][1] == "watch passed: v2 stays in production"


def test_alerts_after_the_rollout_only_notify(clock: FakeClock, tracer: Tracer) -> None:
    runs = FakeRuns(clock)
    ctl = controller(clock, tracer, runs)
    serve(ctl, READY_AFTER + WATCH_RUNS)
    runs.outcomes["v2"] = "loop"
    serve(ctl, 1, start=READY_AFTER + WATCH_RUNS + 1)
    assert ctl.registry.get_production(MODEL_NAME).version == "v2"
    assert ctl.timeline[-1][1] == "alert fired: loop_detected for v2 (1)"


# ---------------------------------------------------------------- root spans


def test_each_request_gets_a_root_span(
    clock: FakeClock, tracer: Tracer, exporter: InMemorySpanExporter
) -> None:
    ctl = controller(clock, tracer, FakeRuns(clock))
    ctl.handle("req-0001", "Where is ORD-10042? Email me at jane.doe@example.com")
    [span] = exporter.get_finished_spans()
    attributes = span.attributes
    assert span.name == "agent.run"
    assert attributes["openinference.span.kind"] == "AGENT"
    assert attributes["request.id"] == "req-0001"
    assert attributes["canary.group"] == "canary"
    assert attributes["agent.version"] == "v2"
    assert attributes["llm.model_name"] == "claude-sonnet-5"
    assert attributes["agent.outcome"] == "ok"
    assert attributes["agent.cost_usd"] == 0.02
    assert "jane.doe@example.com" not in attributes["input.value"]
    assert "ORD-10042" in attributes["input.value"]
    assert span.status.status_code is not StatusCode.ERROR


def test_a_looping_run_carries_the_reason_the_alert_and_the_abort(
    clock: FakeClock, tracer: Tracer, exporter: InMemorySpanExporter
) -> None:
    runs = FakeRuns(clock)
    runs.outcomes["v2"] = "loop"
    ctl = controller(clock, tracer, runs)
    serve(ctl, 1)
    [span] = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["agent.loop.reason"] == "repeat:track_shipment"
    assert [event.name for event in span.events] == ["alert.fired", "canary.aborted"]
    assert span.events[0].attributes["rule"] == "loop_detected"


def test_run_latency_is_recorded_per_version(clock: FakeClock, tracer: Tracer) -> None:
    ctl = controller(clock, tracer, FakeRuns(clock), percent=10)
    serve(ctl, 20)
    assert ctl.perf.call_count("agent.run[v1]") + ctl.perf.call_count("agent.run[v2]") == 20
```

- [ ] **Step 2: Run it to verify it fails**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_canary.py
```

Expected: FAIL with `ImportError: cannot import name 'Phase' from 'canary'`.

- [ ] **Step 3: Replace `src/canary.py` with the full version**

Replace `projects/16-production-agent/src/canary.py`:

```python
"""Canary rollout: routing, the judge, and the controller that acts on it.

The phases run ``CANARY -> (WATCH) -> DONE``:

* **CANARY:** a fixed share of requests goes to the canary version. After
  each canary run, :func:`judge` compares both versions' windows.
* **Ready:** traffic pauses while the controller opens an approval request
  that needs :data:`REQUIRED_APPROVALS` distinct humans. Only ``APPROVED``
  promotes; anything else aborts.
* **WATCH:** the promoted version serves everything for :data:`WATCH_RUNS`
  runs. Its first alert rolls production back automatically, with no
  approval, the same as project 12: a rollback has to work at 3am.

Every stage change goes through the cockpit ``ModelRegistry`` and every
approval through ``ApprovalWorkflow``, so both land on the governance trail.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, NamedTuple

from cockpit.governance.approval_workflow import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalWorkflow,
)
from cockpit.governance.model_versioning import ModelRegistry, ModelStage
from cockpit.monitoring.performance_metrics import CallOutcome, PerformanceTracker
from cockpit.security.output_security import mask_pii
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from agent import AgentVersion, RunRecord
from alerts import AlertMonitor, RunWindow, WindowStats
from tracing import to_ns

MODEL_NAME: Final = "order-support-agent"
ACTOR: Final = "canary-judge"
DEFAULT_CANARY_PERCENT: Final = 10
MIN_CANARY_RUNS: Final = 10
MIN_STABLE_RUNS: Final = 10
READY_AFTER: Final = 30
FAILURE_MARGIN: Final = 0.05
P95_RATIO: Final = 1.5
COST_RATIO: Final = 1.2
REQUIRED_APPROVALS: Final = 2
WATCH_RUNS: Final = 30


def route(request_id: str, percent: int) -> bool:
    """Whether a request belongs to the canary group.

    Args:
        request_id: Request identifier; the same ID always gets the same answer.
        percent: Share of requests routed to the canary, 0-100.

    Returns:
        True for the canary group.
    """
    return int(hashlib.sha256(request_id.encode()).hexdigest()[:8], 16) % 100 < percent


class Verdict(StrEnum):
    """What the judge decided after a canary run."""

    CONTINUE = "continue"
    ABORT = "abort"
    READY = "ready"


class Judgement(NamedTuple):
    """A verdict and the reason for it."""

    verdict: Verdict
    reason: str


def judge(
    canary: WindowStats,
    stable: WindowStats,
    *,
    canary_total: int,
    canary_firing: Collection[str],
) -> Judgement:
    """Decide whether the canary continues, is aborted, or is ready to promote.

    Args:
        canary: The canary version's window stats.
        stable: The stable version's window stats.
        canary_total: Canary runs so far, across the whole rollout.
        canary_firing: Alert rules currently firing for the canary.

    Returns:
        The judgement. Ratio checks are skipped when the stable value is 0.
    """
    if canary_firing:
        return Judgement(Verdict.ABORT, "alert firing: " + ", ".join(sorted(canary_firing)))
    if canary_total < MIN_CANARY_RUNS:
        return Judgement(Verdict.CONTINUE, f"{canary_total}/{MIN_CANARY_RUNS} canary runs")
    if stable.runs >= MIN_STABLE_RUNS:
        if canary.failure_rate > stable.failure_rate + FAILURE_MARGIN:
            return Judgement(
                Verdict.ABORT,
                f"failure rate {canary.failure_rate:.0%} vs stable {stable.failure_rate:.0%}",
            )
        if stable.p95_s > 0 and canary.p95_s > P95_RATIO * stable.p95_s:
            return Judgement(
                Verdict.ABORT, f"p95 {canary.p95_s:.1f}s vs stable {stable.p95_s:.1f}s"
            )
        if stable.mean_cost > 0 and canary.mean_cost > COST_RATIO * stable.mean_cost:
            return Judgement(
                Verdict.ABORT,
                f"cost/run ${canary.mean_cost:.4f} vs stable ${stable.mean_cost:.4f}",
            )
    if canary_total >= READY_AFTER:
        return Judgement(Verdict.READY, f"{canary_total} canary runs, within margins")
    return Judgement(Verdict.CONTINUE, "within margins")


def register_versions(registry: ModelRegistry, stable: AgentVersion, canary: AgentVersion) -> None:
    """Put ``stable`` in production and ``canary`` in staging.

    Args:
        registry: The registry to populate; must not hold these versions yet.
        stable: Version to serve production.
        canary: Version to trial.
    """
    for version in (stable, canary):
        registry.register(
            MODEL_NAME,
            version.version,
            provider_model_id=version.model,
            config_fingerprint=version.fingerprint(),
            actor_id=ACTOR,
        )
        registry.promote(MODEL_NAME, version.version, ModelStage.STAGING, actor_id=ACTOR)
    registry.promote(MODEL_NAME, stable.version, ModelStage.PRODUCTION, actor_id=ACTOR)


class Phase(StrEnum):
    """Rollout phase."""

    CANARY = "canary"
    WATCH = "watch"
    DONE = "done"


RunFn = Callable[[AgentVersion, str, str, str], RunRecord]
"""``(version, question, request_id, group) -> RunRecord``."""

ApproveFn = Callable[[ApprovalWorkflow, ApprovalRequest], None]
"""Collects decisions on an open request; returns when it has no more to give."""


def _stats_line(label: str, stats: WindowStats) -> str:
    return (
        f"{label}: {stats.runs} recent runs, failure {stats.failure_rate:.0%}, "
        f"p95 {stats.p95_s:.1f}s, cost/run ${stats.mean_cost:.4f}"
    )


@dataclass
class RolloutController:
    """Routes each request, runs it under a root span, and moves the rollout on.

    Attributes:
        registry: Holds both versions; production is read from here.
        workflow: Where promotion approval requests are opened.
        stable: The version in production when the rollout starts.
        canary: The version being trialled.
        run: Runs one request (``run_agent`` with its dependencies bound).
        approve: Collects approval decisions (a prompt, or a script).
        tracer: Tracer for the root ``agent.run`` spans.
        monitor: Alert rules, evaluated after every run.
        perf: Receives ``agent.run[<version>]`` latencies.
        clock: Epoch-seconds clock.
        canary_percent: Share of requests routed to the canary.
        window: Recent runs per version.
        phase: Current phase.
        timeline: ``(request_id, event)`` pairs, in order.
    """

    registry: ModelRegistry
    workflow: ApprovalWorkflow
    stable: AgentVersion
    canary: AgentVersion
    run: RunFn
    approve: ApproveFn
    tracer: Tracer
    monitor: AlertMonitor
    perf: PerformanceTracker
    clock: Callable[[], float]
    canary_percent: int = DEFAULT_CANARY_PERCENT
    window: RunWindow = field(default_factory=RunWindow)
    phase: Phase = Phase.CANARY
    timeline: list[tuple[str, str]] = field(default_factory=list)
    _canary_total: int = 0
    _watch_runs: int = 0

    def handle(self, request_id: str, question: str) -> RunRecord:
        """Serve one request and apply its consequences.

        Args:
            request_id: Request identifier; also the routing key.
            question: The customer's question.

        Returns:
            The run's record.
        """
        use_canary = self.phase is Phase.CANARY and route(request_id, self.canary_percent)
        version = self.canary if use_canary else self._production()
        group = "canary" if use_canary else "stable"
        if not self.timeline:
            self._note(
                request_id,
                f"canary started: {self.canary.version} on {self.canary_percent}% of traffic",
            )

        span = self.tracer.start_span(
            "agent.run",
            start_time=to_ns(self.clock()),
            attributes={
                SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.AGENT.value,
                SpanAttributes.LLM_MODEL_NAME: version.model,
                SpanAttributes.INPUT_VALUE: mask_pii(question),
                "request.id": request_id,
                "canary.group": group,
                "agent.version": version.version,
            },
        )
        with trace.use_span(span, end_on_exit=False):
            record = self.run(version, question, request_id, group)

        self.perf.record(
            f"agent.run[{record.version}]",
            record.duration_s,
            outcome=CallOutcome.SUCCESS if record.outcome == "ok" else CallOutcome.ERROR,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
        )
        stats = self.window.add(record)
        for event in self.monitor.observe(
            record.version, stats, request_id=request_id, timestamp=self.clock()
        ):
            span.add_event(
                f"alert.{event.state}",
                {
                    "rule": event.rule,
                    "version": event.version,
                    "value": event.value,
                    "threshold": event.threshold,
                },
                timestamp=to_ns(event.timestamp),
            )
            self._note(
                request_id,
                f"alert {event.state}: {event.rule} for {event.version} ({event.value:.3g})",
            )
        self._advance(record, span)

        span.set_attribute("agent.outcome", record.outcome)
        span.set_attribute("agent.cost_usd", record.cost_usd)
        span.set_attribute(SpanAttributes.OUTPUT_VALUE, mask_pii(record.answer))
        if record.loop_reason:
            span.set_attribute("agent.loop.reason", record.loop_reason)
        if record.outcome != "ok":
            span.set_status(Status(StatusCode.ERROR, record.outcome))
        span.end(end_time=to_ns(self.clock()))
        return record

    def _production(self) -> AgentVersion:
        serving = self.registry.get_production(MODEL_NAME).version
        return self.canary if serving == self.canary.version else self.stable

    def _note(self, request_id: str, event: str) -> None:
        self.timeline.append((request_id, event))

    def _advance(self, record: RunRecord, span: Span) -> None:
        if self.phase is Phase.CANARY and record.group == "canary":
            self._canary_total += 1
            verdict, reason = judge(
                self.window.stats(self.canary.version),
                self.window.stats(self.stable.version),
                canary_total=self._canary_total,
                canary_firing=self.monitor.firing(self.canary.version),
            )
            if verdict is Verdict.ABORT:
                self._abort(record.request_id, reason, span)
            elif verdict is Verdict.READY:
                self._seek_approval(record.request_id, reason, span)
        elif self.phase is Phase.WATCH:
            self._watch_runs += 1
            firing = self.monitor.firing(self.canary.version)
            if firing:
                self._rollback(record.request_id, ", ".join(sorted(firing)), span)
            elif self._watch_runs >= WATCH_RUNS:
                self.phase = Phase.DONE
                self._note(
                    record.request_id, f"watch passed: {self.canary.version} stays in production"
                )

    def _abort(self, request_id: str, reason: str, span: Span) -> None:
        self.registry.promote(
            MODEL_NAME, self.canary.version, ModelStage.DEVELOPMENT, actor_id=ACTOR
        )
        self.phase = Phase.DONE
        span.add_event("canary.aborted", {"reason": reason}, timestamp=to_ns(self.clock()))
        self._note(
            request_id, f"canary aborted ({reason}): {self.canary.version} back to development"
        )

    def _seek_approval(self, request_id: str, reason: str, span: Span) -> None:
        self._note(request_id, f"canary ready ({reason}); approval requested")
        description = "\n".join(
            [
                f"Promote {MODEL_NAME} {self.canary.version} ({self.canary.model}) to production.",
                _stats_line(
                    f"canary {self.canary.version}", self.window.stats(self.canary.version)
                ),
                _stats_line(
                    f"stable {self.stable.version}", self.window.stats(self.stable.version)
                ),
            ]
        )
        request = self.workflow.submit(ACTOR, description, required_approvals=REQUIRED_APPROVALS)
        self.approve(self.workflow, request)
        final = self.workflow.get(request.request_id)
        for decision in final.decisions:
            verb = "approved" if decision.approved else "rejected"
            self._note(request_id, f"{decision.principal_id} {verb}")
        if final.status is ApprovalStatus.APPROVED:
            self.registry.promote(
                MODEL_NAME, self.canary.version, ModelStage.PRODUCTION, actor_id=ACTOR
            )
            self.phase = Phase.WATCH
            span.add_event("canary.promoted", timestamp=to_ns(self.clock()))
            self._note(
                request_id,
                f"promoted: {self.canary.version} to production; watching {WATCH_RUNS} runs",
            )
        else:
            self._abort(request_id, f"approval {final.status}", span)

    def _rollback(self, request_id: str, reason: str, span: Span) -> None:
        restored = self.registry.rollback_production(MODEL_NAME, actor_id=ACTOR)
        self.phase = Phase.DONE
        span.add_event("canary.rolled_back", {"reason": reason}, timestamp=to_ns(self.clock()))
        self._note(request_id, f"rolled back ({reason}): {restored.version} back in production")
```

- [ ] **Step 4: Run it to verify it passes**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_canary.py
```

Expected: PASS.

- [ ] **Step 5: Lint**

From `projects/16-production-agent/`:

```bash
uvx black --line-length 100 --target-version py311 --check -q src tests && uv run ruff check .
```

Expected: no output from black, `All checks passed!` from ruff. If black reports files, run `uvx black --line-length 100 --target-version py311 src tests` and re-run.

- [ ] **Step 6: Commit**

From the repository root:

```bash
git add projects/16-production-agent/src/canary.py projects/16-production-agent/tests/test_canary.py
git commit -m "feat(16): rollout controller with approval-gated promotion and automatic rollback"
```

> **Checkpoint:** The canary mechanics are complete. Show the user the timeline test output (`uv run pytest -q tests/test_canary.py -v`) before building the scenarios.

---

### Task 10: Dry-run scenarios

**Files:**
- Create: `projects/16-production-agent/src/scenarios.py`
- Test: `projects/16-production-agent/tests/test_scenarios.py`

**Interfaces:**
- Consumes: `STABLE`, `CANARY`, `run_agent` (Tasks 4, 7); `route`, `DEFAULT_CANARY_PERCENT`, `READY_AFTER`, `WATCH_RUNS`, `RolloutController`, `register_versions` (Tasks 8, 9); `ModelProfile`, `Turn`, `text`, `tool_call`, `tool_history`, `scripted_client` (Task 6).
- Produces: `support_policy(*, normalize_ids: bool = True, recheck_stuck: bool = False) -> Policy`; `scripted_approvals(*decisions: tuple[str, bool, str]) -> ApproveFn`; `Scenario(name, requests: list[tuple[str, str]], profiles: dict[str, ModelProfile], approve)`; `SCENARIOS` keyed `"bad-canary"` and `"late-regression"`.

The `late-regression` request list is built from `route`, so bare legacy order numbers start exactly after the canary's 30th run, when it is promoted. The scenario tests pin each full timeline, which also proves the dry runs are deterministic.

- [ ] **Step 1: Write the failing test**

Create `projects/16-production-agent/tests/test_scenarios.py`:

```python
"""The scripted support policy and both dry-run scenarios, end to end."""

from __future__ import annotations

from typing import Any

from cockpit.governance.approval_workflow import ApprovalWorkflow
from cockpit.governance.audit_trail import verify_trail_integrity
from cockpit.governance.model_versioning import ModelRegistry, ModelStage
from cockpit.monitoring.cost_tracking import CostTracker
from cockpit.monitoring.performance_metrics import PerformanceTracker
from opentelemetry.trace import Tracer

from agent import CANARY, STABLE, AgentVersion, RunRecord, run_agent
from alerts import AlertMonitor
from canary import (
    DEFAULT_CANARY_PERCENT,
    MODEL_NAME,
    READY_AFTER,
    Phase,
    RolloutController,
    register_versions,
    route,
)
from fake_model import FakeClock, scripted_client, text, tool_call
from scenarios import SCENARIOS, Scenario, support_policy
from tools import track_shipment


def conversation(
    question: str, *steps: tuple[str, dict[str, Any], str, bool]
) -> list[dict[str, Any]]:
    """Build a request's messages from completed tool calls."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    for n, (name, tool_input, result, is_error) in enumerate(steps):
        messages.append(
            {"role": "assistant", "content": [{**tool_call(name, **tool_input), "id": f"t{n}"}]}
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"t{n}",
                        "content": result,
                        "is_error": is_error,
                    }
                ],
            }
        )
    return messages


# ------------------------------------------------------------------- policy


def test_the_first_turn_normalizes_a_bare_order_number() -> None:
    turn = support_policy()(conversation("Where is my order 10042?"))
    assert turn.content[-1] == tool_call("lookup_order", order_id="ORD-10042")


def test_without_normalizing_the_bare_number_goes_straight_through() -> None:
    turn = support_policy(normalize_ids=False)(conversation("Where is my order 10042?"))
    assert turn.content[-1] == tool_call("lookup_order", order_id="10042")


def test_a_stuck_shipment_is_reported_or_rechecked() -> None:
    messages = conversation(
        "Where is my order ORD-10044?",
        ("track_shipment", {"tracking_id": "TRK-5503"}, track_shipment("TRK-5503"), False),
    )
    assert support_policy()(messages).stop_reason == "end_turn"
    assert support_policy(recheck_stuck=True)(messages).content[-1] == tool_call(
        "track_shipment", tracking_id="TRK-5503"
    )


def test_a_tool_error_ends_with_an_apology() -> None:
    messages = conversation(
        "Where is 10042?", ("lookup_order", {"order_id": "10042"}, "Invalid order ID", True)
    )
    assert support_policy()(messages).content == [
        text("Sorry, I couldn't look that up: Invalid order ID")
    ]


# ---------------------------------------------------------------- scenarios


def play(scenario: Scenario, clock: FakeClock, tracer: Tracer) -> RolloutController:
    client = scripted_client(scenario.profiles, clock)
    perf, costs = PerformanceTracker(clock=clock), CostTracker()
    registry = ModelRegistry()
    register_versions(registry, STABLE, CANARY)

    def run(version: AgentVersion, question: str, request_id: str, group: str) -> RunRecord:
        return run_agent(
            client,
            version,
            question,
            request_id=request_id,
            group=group,
            tracer=tracer,
            perf=perf,
            costs=costs,
            clock=clock,
        )

    ctl = RolloutController(
        registry=registry,
        workflow=ApprovalWorkflow(),
        stable=STABLE,
        canary=CANARY,
        run=run,
        approve=scenario.approve,
        tracer=tracer,
        monitor=AlertMonitor(),
        perf=perf,
        clock=clock,
    )
    for request_id, question in scenario.requests:
        ctl.handle(request_id, question)
    return ctl


def test_bad_canary_is_aborted_by_the_loop_alert(clock: FakeClock, tracer: Tracer) -> None:
    ctl = play(SCENARIOS["bad-canary"], clock, tracer)
    assert ctl.timeline == [
        ("req-0001", "canary started: v2 on 10% of traffic"),
        ("req-0062", "alert fired: loop_detected for v2 (1)"),
        ("req-0062", "canary aborted (alert firing: loop_detected): v2 back to development"),
    ]
    assert ctl.phase is Phase.DONE
    assert ctl.registry.get_production(MODEL_NAME).version == "v1"
    assert ctl.registry.get(MODEL_NAME, "v2").stage is ModelStage.DEVELOPMENT
    assert verify_trail_integrity()


def test_late_regression_is_promoted_then_rolled_back(clock: FakeClock, tracer: Tracer) -> None:
    ctl = play(SCENARIOS["late-regression"], clock, tracer)
    assert ctl.timeline == [
        ("req-0001", "canary started: v2 on 10% of traffic"),
        ("req-0323", "canary ready (30 canary runs, within margins); approval requested"),
        ("req-0323", "alice approved"),
        ("req-0323", "bob approved"),
        ("req-0323", "promoted: v2 to production; watching 30 runs"),
        ("req-0338", "alert fired: tool_error_rate for v2 (0.333)"),
        ("req-0338", "rolled back (tool_error_rate): v1 back in production"),
    ]
    assert ctl.registry.get_production(MODEL_NAME).version == "v1"
    assert ctl.registry.get(MODEL_NAME, "v2").stage is ModelStage.STAGING
    assert verify_trail_integrity()


def test_legacy_order_numbers_appear_only_after_the_canary_is_ready() -> None:
    requests = SCENARIOS["late-regression"].requests
    canary_runs = 0
    for request_id, question in requests:
        assert "ORD-" in question, f"{request_id} carries a legacy number before promotion"
        canary_runs += route(request_id, DEFAULT_CANARY_PERCENT)
        if canary_runs == READY_AFTER:
            break
    assert canary_runs == READY_AFTER
```

- [ ] **Step 2: Run it to verify it fails**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_scenarios.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scenarios'`.

- [ ] **Step 3: Write `scenarios.py`**

Create `projects/16-production-agent/src/scenarios.py`:

```python
"""The two dry-run scenarios: scripted traffic, scripted models, scripted approvers.

Both models follow the same support policy, answering from the tools. Each
scenario changes one behaviour of the canary model:

* ``bad-canary``: v2 keeps re-checking a shipment whose status never
  changes, so the loop guard trips on the third identical call and the canary
  is aborted.
* ``late-regression``: v2 passes bare legacy order numbers (``10042``)
  straight to ``lookup_order`` instead of normalizing them. Legacy numbers
  only appear in traffic after promotion, so the canary looks healthy, gets
  approved and promoted, then trips ``tool_error_rate`` during the watch and
  is rolled back.

Request lists are built from :func:`canary.route`, so the ``late-regression``
traffic switches to legacy numbers exactly when the canary is promoted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from cockpit.governance.approval_workflow import ApprovalRequest, ApprovalWorkflow

from agent import CANARY, STABLE
from canary import DEFAULT_CANARY_PERCENT, READY_AFTER, WATCH_RUNS, route
from fake_model import ModelProfile, Policy, Turn, text, tool_call, tool_history

_ORDER_NUMBER = re.compile(r"\b(ORD-)?(\d{5})\b")


def support_policy(*, normalize_ids: bool = True, recheck_stuck: bool = False) -> Policy:
    """The scripted assistant's behaviour.

    Args:
        normalize_ids: Turn a bare ``10042`` into ``ORD-10042`` before lookup.
        recheck_stuck: Keep calling ``track_shipment`` while a shipment is in transit.

    Returns:
        A policy for :class:`fake_model.ModelProfile`.
    """

    def policy(messages: list[dict[str, Any]]) -> Turn:
        question = messages[0]["content"]
        history = tool_history(messages)
        if not history:
            match = _ORDER_NUMBER.search(question)
            if match is None:
                return Turn([text("Which order number is this about?")])
            order_id = (
                match.group(0) if match.group(1) or not normalize_ids else f"ORD-{match.group(2)}"
            )
            return Turn(
                [text("Let me look that up."), tool_call("lookup_order", order_id=order_id)],
                "tool_use",
            )

        name, _, result, is_error = history[-1]
        if is_error:
            return Turn([text(f"Sorry, I couldn't look that up: {result}")])
        data = json.loads(result)
        if name == "lookup_order":
            if "return" in question.lower():
                return Turn([tool_call("refund_policy", category=data["category"])], "tool_use")
            if data["tracking_id"]:
                return Turn(
                    [tool_call("track_shipment", tracking_id=data["tracking_id"])], "tool_use"
                )
            return Turn([text(f"Your {data['item']} is {data['status']} and hasn't shipped yet.")])
        if name == "track_shipment":
            if recheck_stuck and data["status"] == "in transit":
                return Turn(
                    [
                        text("Let me check that again."),
                        tool_call("track_shipment", tracking_id=data["tracking_id"]),
                    ],
                    "tool_use",
                )
            return Turn(
                [text(f"Your shipment is {data['status']} (last scan: {data['last_scan']}).")]
            )
        return Turn([text(data["policy"])])

    return policy


_MODERN: Final = (
    "Where is my order ORD-10042?",
    "Has order ORD-10043 shipped yet?",
    "Where is my order ORD-10044? It seems stuck.",
    "Can I return the shoes from order ORD-10042?",
)
_AFTER_PROMOTION: Final = (
    "Where is my order 10042?",
    "Can I return order 10044?",
    "Where is my order ORD-10042?",
)


def _request_id(n: int) -> str:
    return f"req-{n:04d}"


def _bad_canary_requests() -> list[tuple[str, str]]:
    return [(_request_id(n), _MODERN[n % len(_MODERN)]) for n in range(1, 121)]


def _late_regression_requests() -> list[tuple[str, str]]:
    requests: list[tuple[str, str]] = []
    canary_runs = 0
    n = 0
    while canary_runs < READY_AFTER:
        n += 1
        if route(_request_id(n), DEFAULT_CANARY_PERCENT):
            canary_runs += 1
        requests.append((_request_id(n), _MODERN[n % len(_MODERN)]))
    for offset in range(1, WATCH_RUNS + 11):
        requests.append((_request_id(n + offset), _AFTER_PROMOTION[offset % len(_AFTER_PROMOTION)]))
    return requests


def scripted_approvals(
    *decisions: tuple[str, bool, str]
) -> Callable[[ApprovalWorkflow, ApprovalRequest], None]:
    """An approver that records fixed decisions in order.

    Args:
        decisions: ``(approver, approved, comment)`` triples.

    Returns:
        A callable for ``RolloutController.approve``.
    """

    def approve(workflow: ApprovalWorkflow, request: ApprovalRequest) -> None:
        for principal, approved, comment in decisions:
            workflow.decide(request.request_id, approved, principal, comment=comment)

    return approve


@dataclass(frozen=True)
class Scenario:
    """A complete dry run.

    Attributes:
        name: CLI name.
        requests: ``(request_id, question)`` in order.
        profiles: Scripted behaviour per model ID.
        approve: Scripted approvers.
    """

    name: str
    requests: list[tuple[str, str]]
    profiles: dict[str, ModelProfile]
    approve: Callable[[ApprovalWorkflow, ApprovalRequest], None]


_STABLE_PROFILE: Final = ModelProfile(support_policy(), latency_s=2.0)
_APPROVERS: Final = scripted_approvals(
    ("alice", True, "canary numbers look good"),
    ("bob", True, "cheaper at the same quality"),
)

SCENARIOS: Final[dict[str, Scenario]] = {
    "bad-canary": Scenario(
        name="bad-canary",
        requests=_bad_canary_requests(),
        profiles={
            STABLE.model: _STABLE_PROFILE,
            CANARY.model: ModelProfile(support_policy(recheck_stuck=True), latency_s=1.2),
        },
        approve=_APPROVERS,
    ),
    "late-regression": Scenario(
        name="late-regression",
        requests=_late_regression_requests(),
        profiles={
            STABLE.model: _STABLE_PROFILE,
            CANARY.model: ModelProfile(support_policy(normalize_ids=False), latency_s=1.2),
        },
        approve=_APPROVERS,
    ),
}
```

- [ ] **Step 4: Run it to verify it passes**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_scenarios.py
```

Expected: PASS. `bad-canary` aborts at `req-0062`; `late-regression` is promoted at `req-0323` and rolled back at `req-0338`.

- [ ] **Step 5: Lint**

From `projects/16-production-agent/`:

```bash
uvx black --line-length 100 --target-version py311 --check -q src tests && uv run ruff check .
```

Expected: no output from black, `All checks passed!` from ruff. If black reports files, run `uvx black --line-length 100 --target-version py311 src tests` and re-run.

- [ ] **Step 6: Commit**

From the repository root:

```bash
git add projects/16-production-agent/src/scenarios.py projects/16-production-agent/tests/test_scenarios.py
git commit -m "feat(16): bad-canary and late-regression dry-run scenarios"
```

---

### Task 11: The CLI

**Files:**
- Create: `projects/16-production-agent/src/main.py`
- Test: `projects/16-production-agent/tests/test_main.py`

**Interfaces:**
- Consumes: everything above, plus cockpit `build_dashboard_snapshot`, `render_dashboard_text`, `verify_trail_integrity` and `get_secret`.
- Produces: `main(argv: Sequence[str] | None = None) -> int` (0 on success; 2 for a missing or rejected key; argparse exits 2 on bad flags); `parse_args(argv)`; `prompt_approvals(workflow, request)`; `ALERTS_PATH = <project>/outputs/alerts.jsonl`; `DEFAULT_REQUESTS = 20`.

`main.py` puts the repo root on `sys.path` before importing anything from `cockpit`, which is why the pyproject ignores E402 for it. Tests redirect `ALERTS_PATH` into `tmp_path`, so they never write into the repo.

- [ ] **Step 1: Write the failing test**

Create `projects/16-production-agent/tests/test_main.py`:

```python
"""The CLI: argument checks, the dry run, the live-mode guards, the approval prompt."""

from __future__ import annotations

import builtins
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from cockpit.governance.approval_workflow import ApprovalStatus, ApprovalWorkflow

import main
from fake_model import FakeClock, ModelProfile, scripted_client


@pytest.fixture(autouse=True)
def alerts_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "alerts.jsonl"
    monkeypatch.setattr(main, "ALERTS_PATH", path)
    return path


@pytest.mark.parametrize(
    "argv",
    [["--scenario", "bad-canary"], ["--dry-run"], ["--canary-percent", "101"], ["--requests", "0"]],
    ids=["scenario-without-dry-run", "dry-run-without-scenario", "percent-too-high", "no-requests"],
)
def test_bad_arguments_exit_with_usage(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main.main(argv)
    assert exc.value.code == 2


def test_a_dry_run_prints_the_dashboard_and_the_timeline(
    capsys: pytest.CaptureFixture[str], alerts_file: Path
) -> None:
    assert main.main(["--dry-run", "--scenario", "bad-canary"]) == 0
    out = capsys.readouterr().out
    assert "agent.run[v1]" in out
    assert "canary aborted (alert firing: loop_detected): v2 back to development" in out
    assert "Phase: done. In production: v1." in out
    assert "Governance trail intact: True" in out
    [alert] = [json.loads(line) for line in alerts_file.read_text().splitlines()]
    assert (alert["rule"], alert["state"], alert["version"]) == ("loop_detected", "fired", "v2")


def test_live_mode_without_a_key_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(main, "get_secret", lambda key, default=None: None)
    assert main.main(["--requests", "1"]) == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err


def test_a_rejected_key_stops_the_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def rejected(messages: list[dict[str, Any]]) -> httpx2.Response:
        return httpx2.Response(
            401,
            json={
                "type": "error",
                "error": {"type": "authentication_error", "message": "invalid x-api-key"},
            },
        )

    monkeypatch.setattr(
        main, "get_secret", lambda key, default=None: "sk-test"
    )  # pragma: allowlist secret
    fake = scripted_client(
        {"claude-opus-5": ModelProfile(rejected), "claude-sonnet-5": ModelProfile(rejected)},
        FakeClock(),
    )
    monkeypatch.setattr(main.anthropic, "Anthropic", lambda api_key: fake)
    assert main.main(["--requests", "1"]) == 2
    assert "Authentication failed: the API key was rejected" in capsys.readouterr().err


def _answers(monkeypatch: pytest.MonkeyPatch, *replies: str) -> None:
    replies_left: Iterator[str] = iter(replies)
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(replies_left))


def test_the_prompt_collects_decisions_until_the_request_is_decided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = ApprovalWorkflow()
    request = workflow.submit("canary-judge", "Promote v2", required_approvals=2)
    _answers(monkeypatch, "alice", "a", "", "bob", "a", "looks good")
    main.prompt_approvals(workflow, request)
    assert workflow.get(request.request_id).status is ApprovalStatus.APPROVED


def test_the_prompt_reports_refused_votes_and_stops_on_a_blank_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = ApprovalWorkflow()
    request = workflow.submit("canary-judge", "Promote v2", required_approvals=2)
    _answers(monkeypatch, "canary-judge", "a", "", "")
    main.prompt_approvals(workflow, request)
    assert workflow.get(request.request_id).status is ApprovalStatus.PENDING
    assert "not recorded" in capsys.readouterr().out
```

- [ ] **Step 2: Run it to verify it fails**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_main.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'main'`.

- [ ] **Step 3: Write `main.py`**

Create `projects/16-production-agent/src/main.py`:

```python
"""CLI: put the order-support agent through a canary rollout and report.

Examples:
    # No API key, no network: a scripted scenario on a fake clock.
    uv run python src/main.py --dry-run --scenario bad-canary

    # Same, with every trace sent to a local Phoenix (see README).
    uv run python src/main.py --dry-run --scenario late-regression --phoenix

    # Real Claude calls. Spends money: see README before raising --requests.
    uv run python src/main.py --requests 60 --canary-percent 50 --phoenix
"""

from __future__ import annotations

import sys
from pathlib import Path

# The repo root is package=false by design, so it isn't installed into this
# project's venv -- put it on sys.path to import the cockpit frameworks.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import argparse
import logging
import time
from collections.abc import Callable, Sequence

import anthropic
from cockpit.governance.approval_workflow import (
    ApprovalError,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalWorkflow,
)
from cockpit.governance.audit_trail import verify_trail_integrity
from cockpit.governance.model_versioning import ModelRegistry
from cockpit.monitoring.cost_tracking import CostTracker
from cockpit.monitoring.dashboard import build_dashboard_snapshot, render_dashboard_text
from cockpit.monitoring.performance_metrics import PerformanceTracker
from cockpit.security.secrets_manager import get_secret
from dotenv import load_dotenv

from agent import CANARY, STABLE, AgentVersion, RunRecord, run_agent
from alerts import AlertMonitor, jsonl_sink, log_sink
from canary import (
    DEFAULT_CANARY_PERCENT,
    MODEL_NAME,
    RolloutController,
    register_versions,
)
from fake_model import FakeClock, scripted_client
from scenarios import SCENARIOS
from tools import SAMPLE_QUESTIONS
from tracing import build_tracer_provider, phoenix_exporter

PROJECT_DIR = Path(__file__).resolve().parents[1]
ALERTS_PATH = PROJECT_DIR / "outputs" / "alerts.jsonl"
DEFAULT_REQUESTS = 20


def prompt_approvals(workflow: ApprovalWorkflow, request: ApprovalRequest) -> None:
    """Ask at the terminal for decisions until the request is decided or no one is left.

    Args:
        workflow: Workflow holding the request.
        request: The open promotion request.
    """
    print(f"\nApproval needed ({request.required_approvals} approvers):\n{request.description}")
    while workflow.get(request.request_id).status is ApprovalStatus.PENDING:
        name = input("Approver name (blank to stop): ").strip()
        if not name:
            return
        approved = input("Approve or reject? [a/r]: ").strip().lower() == "a"
        comment = input("Comment (optional): ").strip() or None
        try:
            workflow.decide(request.request_id, approved, name, comment=comment)
        except ApprovalError as exc:
            print(f"  not recorded: {exc}")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse and cross-check the command line.

    Args:
        argv: Arguments, or None for ``sys.argv[1:]``.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="scripted models, fake clock, no API key"
    )
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), help="dry-run scenario to play")
    parser.add_argument(
        "--requests", type=int, default=DEFAULT_REQUESTS, help="live mode: requests to send"
    )
    parser.add_argument(
        "--canary-percent",
        type=int,
        default=DEFAULT_CANARY_PERCENT,
        help="live mode: share routed to the canary",
    )
    parser.add_argument("--phoenix", action="store_true", help="export traces to a local Phoenix")
    args = parser.parse_args(argv)
    if args.dry_run != (args.scenario is not None):
        parser.error("--dry-run and --scenario go together")
    if not 0 <= args.canary_percent <= 100:
        parser.error("--canary-percent must be between 0 and 100")
    if args.requests < 1:
        parser.error("--requests must be at least 1")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Run the rollout and print the dashboard, timeline and trail check.

    Args:
        argv: Arguments, or None for ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 on success, 2 for a missing or rejected API key.
    """
    args = parse_args(argv)
    load_dotenv(PROJECT_DIR / ".env")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    clock: Callable[[], float]
    if args.dry_run:
        scenario = SCENARIOS[args.scenario]
        clock = FakeClock()
        client = scripted_client(scenario.profiles, clock)
        requests = scenario.requests
        approve = scenario.approve
        percent = DEFAULT_CANARY_PERCENT
    else:
        api_key = get_secret("ANTHROPIC_API_KEY")
        if not api_key:
            print(
                "ANTHROPIC_API_KEY is not set. Use --dry-run, or add the key to .env.",
                file=sys.stderr,
            )
            return 2
        clock = time.time
        client = anthropic.Anthropic(api_key=api_key)
        requests = [
            (f"req-{n:04d}", SAMPLE_QUESTIONS[(n - 1) % len(SAMPLE_QUESTIONS)])
            for n in range(1, args.requests + 1)
        ]
        approve = prompt_approvals
        percent = args.canary_percent

    provider = build_tracer_provider(phoenix_exporter() if args.phoenix else None)
    tracer = provider.get_tracer("production-agent")
    perf = PerformanceTracker(clock=clock)
    costs = CostTracker()
    registry = ModelRegistry()
    register_versions(registry, STABLE, CANARY)

    def run(version: AgentVersion, question: str, request_id: str, group: str) -> RunRecord:
        return run_agent(
            client,
            version,
            question,
            request_id=request_id,
            group=group,
            tracer=tracer,
            perf=perf,
            costs=costs,
            clock=clock,
        )

    controller = RolloutController(
        registry=registry,
        workflow=ApprovalWorkflow(),
        stable=STABLE,
        canary=CANARY,
        run=run,
        approve=approve,
        tracer=tracer,
        monitor=AlertMonitor([log_sink, jsonl_sink(ALERTS_PATH)]),
        perf=perf,
        clock=clock,
        canary_percent=percent,
    )
    try:
        for request_id, question in requests:
            controller.handle(request_id, question)
    except anthropic.AuthenticationError:
        print(
            "Authentication failed: the API key was rejected. Check ANTHROPIC_API_KEY.",
            file=sys.stderr,
        )
        return 2
    finally:
        provider.shutdown()

    print(render_dashboard_text(build_dashboard_snapshot(costs, perf)))
    print("\nRollout timeline")
    for request_id, event in controller.timeline:
        print(f"  {request_id}  {event}")
    print(
        f"\nPhase: {controller.phase}. In production: {registry.get_production(MODEL_NAME).version}."
    )
    print(f"Governance trail intact: {verify_trail_integrity()}")
    print(f"Alert log: {ALERTS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run it to verify it passes**

From `projects/16-production-agent/`:

```bash
uv run pytest -q tests/test_main.py
```

Expected: PASS.

- [ ] **Step 5: Run the CLI for real**

From `projects/16-production-agent/`:

```bash
uv run python src/main.py --dry-run --scenario late-regression
```

Expected: the cockpit dashboard (with `agent.run[v1]`, `agent.run[v2]`, `model.generate[...]` and `tool.*` rows), then a timeline ending `req-0338  rolled back (tool_error_rate): v1 back in production`, `Phase: done. In production: v1.` and `Governance trail intact: True`. One WARNING line on stderr for the alert.

- [ ] **Step 6: Lint**

From `projects/16-production-agent/`:

```bash
uvx black --line-length 100 --target-version py311 --check -q src tests && uv run ruff check .
```

Expected: no output from black, `All checks passed!` from ruff. If black reports files, run `uvx black --line-length 100 --target-version py311 src tests` and re-run.

- [ ] **Step 7: Commit**

From the repository root:

```bash
git add projects/16-production-agent/src/main.py projects/16-production-agent/tests/test_main.py
git commit -m "feat(16): CLI with dry-run scenarios, live mode and terminal approvals"
```

---

### Task 12: Documentation and final verification

**Files:**
- Create: `projects/16-production-agent/README.md`
- Modify: `README.md` (project count, layout note, table row, dry-run note)

**Interfaces:**
- Consumes: the finished project.

Another session is adding `projects/17-event-automation-agent` in its own PR, which may touch the same root README lines. If both PRs are open, whichever merges second resolves the conflict by keeping both rows and updating the count.

- [ ] **Step 1: Write the project README**

Create `projects/16-production-agent/README.md`:

````markdown
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
````

- [ ] **Step 2: Root README edit 1**

In `README.md`, replace:

```markdown
plus a set of 14 runnable example projects that demonstrate each framework
```

with:

```markdown
plus a set of 15 runnable example projects that demonstrate each framework
```

- [ ] **Step 3: Root README edit 2**

In `README.md`, replace:

```markdown
projects/             14 standalone example projects (01-hello-world through
                      14-threat-monitor). Each has its own pyproject.toml.
```

with:

```markdown
projects/             15 standalone example projects (01-hello-world through
                      16-production-agent; 15 is ORION, its own repository).
                      Each has its own pyproject.toml.
```

- [ ] **Step 4: Root README edit 3**

In `README.md`, replace:

```markdown
## 📚 The 14 Example Projects
```

with:

```markdown
## 📚 The 15 Example Projects
```

- [ ] **Step 5: Root README edit 4**

In `README.md`, replace:

```markdown
Projects 01-05 are standalone. Projects 06-14 import `cockpit/` frameworks.
```

with:

```markdown
Projects 01-05 are standalone. Projects 06-14 and 16 import `cockpit/` frameworks.
```

- [ ] **Step 6: Root README edit 5**

In `README.md`, replace:

```markdown
| 14 | [threat-monitor](projects/14-threat-monitor) | Brute force, exfiltration, privilege-escalation detection |
```

with:

```markdown
| 14 | [threat-monitor](projects/14-threat-monitor) | Brute force, exfiltration, privilege-escalation detection |
| 16 | [production-agent](projects/16-production-agent) | Claude agent traced in Phoenix, loop/failure alerts, canary with approval-gated promotion and automatic rollback |
```

- [ ] **Step 7: Root README edit 6**

In `README.md`, replace:

```markdown
Projects 06-14 support `--dry-run` to see them work before adding credentials.
```

with:

```markdown
Projects 06-14 and 16 support `--dry-run` to see them work before adding credentials.
```

- [ ] **Step 8: Project suite**

From `projects/16-production-agent/`:

```bash
uv run pytest -q
```

Expected: all pass, 1 skipped (live).

- [ ] **Step 9: Lint**

From `projects/16-production-agent/`:

```bash
uvx black --line-length 100 --target-version py311 --check -q src tests && uv run ruff check .
```

Expected: no output from black, `All checks passed!` from ruff. If black reports files, run `uvx black --line-length 100 --target-version py311 src tests` and re-run.

- [ ] **Step 10: Dry run: bad-canary**

From `projects/16-production-agent/`:

```bash
uv run python src/main.py --dry-run --scenario bad-canary
```

Expected: timeline ends `req-0062  canary aborted (alert firing: loop_detected): v2 back to development`; `In production: v1.`

- [ ] **Step 11: Root suite (from the repository root)**

From the repository root:

```bash
uv run pytest -c config/pytest.ini --rootdir=. -q
```

Expected: all pass, coverage gate met, and `test_examples.py` now includes `16-production-agent`.

- [ ] **Step 12: Commit**

From the repository root:

```bash
git add projects/16-production-agent/README.md README.md
git commit -m "docs(16): project README and root project table"
```

> **Checkpoint:** Done. Show the user both dry-run timelines and, if Docker is available, the Phoenix view, then finish the branch with superpowers:finishing-a-development-branch.

---

## Spec Coverage

| Spec requirement | Task |
|---|---|
| Tracing to Phoenix, OpenInference names, explicit timestamps, PII masking | 3, 7, 9 |
| Latency and cost dashboards per version | 1, 7, 9, 11 |
| Loop guard (repeat, no progress, `max_iterations`) | 5, 7 |
| Five alert rules, per-version windows, transitions only, three sinks | 5, 9, 11 |
| Routing, judge, abort | 8, 9 |
| Two-approval promotion, terminal prompt, scripted approvers | 9, 10, 11 |
| Watch and automatic rollback | 9 |
| Error handling: tool errors, API errors, 401, refusal, exporter failure | 7, 11 |
| Same request apart from model; no refusal fallbacks | 7 |
| Dry-run scenarios `bad-canary`, `late-regression` | 10, 11 |
| CLI flags, output, `outputs/alerts.jsonl` | 11 |
| Cockpit pricing, e2e glob, root README | 1, 12 |
| Live test, skipped by default | 7 |

