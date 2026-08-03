"""Tests for the instrumented client and the rendered monitoring dashboard.

No API key, no network, no sleeping. Every model call is an injected fake,
and every latency assertion is exact because the ``PerformanceTracker``'s
clock is injected too -- a fake clock that advances a fixed step per read, so
each measured call is recorded with a latency we chose rather than one the
machine happened to produce.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import main
import pytest
from instrumented_client import (
    InstrumentedClient,
    ModelCallError,
    extract_text,
    extract_usage,
)
from workload import (
    DEFAULT_WORKLOAD,
    OfflineResponse,
    OfflineUsage,
    WorkloadItem,
    make_offline_clock,
    make_offline_generate_fn,
    run_workload,
)

from cockpit.monitoring.cost_tracking import CostTracker, ModelPricing, estimate_cost
from cockpit.monitoring.dashboard import build_dashboard_snapshot, render_dashboard_text
from cockpit.monitoring.performance_metrics import CallOutcome, PerformanceTracker

FLASH = "gemini-2.5-flash"
LITE = "gemini-2.5-flash-lite"
STEP = 0.25


@dataclass
class ScriptedClock:
    """A fake clock that returns a pre-scripted sequence of readings.

    ``PerformanceTracker.measure`` reads the clock exactly twice per call
    (start, then end), so a list of ``[s1, e1, s2, e2, ...]`` pins every
    measured duration exactly.

    Attributes:
        readings: The values to return, in order.
    """

    readings: list[float]
    _index: int = 0

    def __call__(self) -> float:
        """Return the next scripted reading.

        Returns:
            The next value in :attr:`readings`, repeating the last one if the
            script runs out.
        """
        value = self.readings[min(self._index, len(self.readings) - 1)]
        self._index += 1
        return value


def _response(text: str, input_tokens: int, output_tokens: int) -> OfflineResponse:
    """Build a canned response with explicit token counts.

    Args:
        text: Response text.
        input_tokens: Prompt tokens to report.
        output_tokens: Completion tokens to report.

    Returns:
        The canned :class:`OfflineResponse`.
    """
    return OfflineResponse(
        text=text,
        usage_metadata=OfflineUsage(
            prompt_token_count=input_tokens,
            candidates_token_count=output_tokens,
            total_token_count=input_tokens + output_tokens,
        ),
    )


def _fixed_generate_fn(input_tokens: int, output_tokens: int) -> Callable[[str, str], Any]:
    """Build a fake ``generate_fn`` that always reports the same token counts.

    Args:
        input_tokens: Prompt tokens every call reports.
        output_tokens: Completion tokens every call reports.

    Returns:
        A ``(model, prompt) -> OfflineResponse`` callable.
    """

    def generate(model: str, prompt: str) -> OfflineResponse:
        return _response(f"answer from {model}", input_tokens, output_tokens)

    return generate


def _client(
    generate_fn: Callable[[str, str], Any],
    clock: Callable[[], float] | None = None,
    pricing: dict[str, ModelPricing] | None = None,
) -> tuple[InstrumentedClient, CostTracker, PerformanceTracker]:
    """Assemble an instrumented client over fresh trackers and a fake clock.

    Args:
        generate_fn: The fake model call to inject.
        clock: Clock for the performance tracker. Defaults to a fixed-step
            fake clock so latencies are exact.
        pricing: Optional price-table override.

    Returns:
        A ``(client, cost_tracker, performance_tracker)`` triple.
    """
    costs = CostTracker()
    perf = PerformanceTracker(clock=clock or make_offline_clock(STEP))
    client = InstrumentedClient(
        generate_fn=generate_fn,
        cost_tracker=costs,
        performance_tracker=perf,
        project="test-project",
        pricing=pricing,
    )
    return client, costs, perf


# --------------------------------------------------------------------------
# Usage extraction
# --------------------------------------------------------------------------


def test_extract_usage_reads_provider_counts() -> None:
    usage = extract_usage(_response("hi", 120, 40))
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (120, 40, 160)


def test_extract_usage_without_metadata_yields_zeros() -> None:
    usage = extract_usage(object())
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (0, 0, 0)


def test_extract_usage_falls_back_to_sum_when_total_missing() -> None:
    class _PartialUsage:
        prompt_token_count = 30
        candidates_token_count = 12
        total_token_count = None

    class _PartialResponse:
        usage_metadata = _PartialUsage()

    usage = extract_usage(_PartialResponse())
    assert usage.total_tokens == 42


def test_extract_text_rejects_empty_response() -> None:
    with pytest.raises(ModelCallError):
        extract_text(_response("", 1, 1))


# --------------------------------------------------------------------------
# Instrumented client: one call, both signals
# --------------------------------------------------------------------------


def test_generate_records_exact_latency_and_priced_cost() -> None:
    client, costs, perf = _client(_fixed_generate_fn(1_000, 500))

    result = client.generate(FLASH, "prompt")

    expected_cost = estimate_cost(FLASH, 1_000, 500)
    assert result.latency_seconds == pytest.approx(STEP)
    assert result.cost_usd == pytest.approx(expected_cost)
    # $0.30/1M in + $2.50/1M out => 1000 * 3e-7 + 500 * 2.5e-6
    assert result.cost_usd == pytest.approx(0.00155)
    assert costs.total_cost() == pytest.approx(expected_cost)
    assert costs.total_tokens() == (1_000, 500)
    assert perf.summary(f"{FLASH}.generate").p50_seconds == pytest.approx(STEP)
    assert perf.error_rate() == 0.0


def test_generate_attributes_cost_to_the_configured_project() -> None:
    client, costs, _ = _client(_fixed_generate_fn(10, 5))
    client.generate(FLASH, "prompt")
    assert costs.cost_by_project() == {"test-project": pytest.approx(estimate_cost(FLASH, 10, 5))}


def test_generate_honours_a_pricing_override() -> None:
    override = {FLASH: ModelPricing(input_per_1m=1_000_000.0, output_per_1m=0.0, provider="test")}
    client, costs, _ = _client(_fixed_generate_fn(3, 7), pricing=override)

    result = client.generate(FLASH, "prompt")

    assert result.cost_usd == pytest.approx(3.0)
    assert costs.total_cost() == pytest.approx(3.0)


def test_generate_failure_books_an_error_sample_and_no_cost() -> None:
    def exploding(model: str, prompt: str) -> Any:
        raise RuntimeError("upstream 503")

    client, costs, perf = _client(exploding)

    with pytest.raises(ModelCallError):
        client.generate(FLASH, "prompt")

    assert costs.entries == []
    assert costs.total_cost() == 0.0
    assert perf.call_count() == 1
    assert perf.error_rate() == 1.0
    assert perf.samples[0].outcome is CallOutcome.ERROR


def test_generate_separates_operations_per_model() -> None:
    client, _, perf = _client(_fixed_generate_fn(10, 10))
    client.generate(FLASH, "a")
    client.generate(LITE, "b")
    assert set(perf.operations()) == {f"{FLASH}.generate", f"{LITE}.generate"}
    assert perf.call_count(f"{FLASH}.generate") == 1
    assert perf.call_count(f"{LITE}.generate") == 1


def test_scripted_clock_pins_the_latency_distribution() -> None:
    # Three calls of 0.1s, 0.3s and 0.2s, in that order.
    clock = ScriptedClock([0.0, 0.1, 1.0, 1.3, 2.0, 2.2])
    client, _, perf = _client(_fixed_generate_fn(1, 1), clock=clock)

    for _ in range(3):
        client.generate(FLASH, "prompt")

    summary = perf.summary(f"{FLASH}.generate")
    assert summary.sample_count == 3
    assert summary.min_seconds == pytest.approx(0.1)
    assert summary.max_seconds == pytest.approx(0.3)
    assert summary.p50_seconds == pytest.approx(0.2)
    assert summary.total_seconds == pytest.approx(0.6)


# --------------------------------------------------------------------------
# Workload
# --------------------------------------------------------------------------


def test_offline_generate_fn_is_deterministic() -> None:
    generate = make_offline_generate_fn()
    first = generate(FLASH, "a reasonably long prompt for token estimation")
    second = generate(FLASH, "a reasonably long prompt for token estimation")
    assert first == second
    assert first.usage_metadata.prompt_token_count > 0


def test_run_workload_totals_across_models() -> None:
    client, costs, perf = _client(_fixed_generate_fn(100, 50))
    items = DEFAULT_WORKLOAD[:3]

    responses = run_workload(client, items=items, models=[FLASH, LITE])

    assert len(responses) == 6
    assert perf.call_count() == 6
    assert costs.total_tokens() == (600, 300)
    expected = 3 * estimate_cost(FLASH, 100, 50) + 3 * estimate_cost(LITE, 100, 50)
    assert costs.total_cost() == pytest.approx(expected)
    assert set(costs.cost_by_model()) == {FLASH, LITE}


def test_run_workload_tolerates_a_failing_prompt() -> None:
    def flaky(model: str, prompt: str) -> Any:
        if "boom" in prompt:
            raise RuntimeError("provider refused")
        return _response("ok", 10, 5)

    client, costs, perf = _client(flaky)
    items = [
        WorkloadItem("good", "fine"),
        WorkloadItem("bad", "boom"),
        WorkloadItem("also", "fine"),
    ]

    responses = run_workload(client, items=items, models=[FLASH])

    assert len(responses) == 2
    assert len(costs.entries) == 2
    assert perf.call_count() == 3
    assert perf.error_rate() == pytest.approx(1 / 3)


# --------------------------------------------------------------------------
# Dashboard rendering
# --------------------------------------------------------------------------


def test_rendered_dashboard_contains_every_section() -> None:
    client, costs, perf = _client(_fixed_generate_fn(100, 50))
    run_workload(client, items=DEFAULT_WORKLOAD[:2], models=[FLASH, LITE])

    report = render_dashboard_text(build_dashboard_snapshot(costs, perf))

    sections = ("COST", "Total spend", "By model", "By project", "PERFORMANCE", "RECENT COSTS")
    for section in sections:
        assert section in report
    assert FLASH in report
    assert "test-project" in report
    assert "Total calls" in report
    assert "No monitoring data recorded yet." not in report


def test_rendered_dashboard_reports_the_error_rate() -> None:
    def always_fails(model: str, prompt: str) -> Any:
        raise RuntimeError("nope")

    client, costs, perf = _client(always_fails)
    run_workload(client, items=DEFAULT_WORKLOAD[:2], models=[FLASH])

    report = render_dashboard_text(build_dashboard_snapshot(costs, perf))

    assert "Error rate" in report
    assert "100.00%" in report


def test_rendered_dashboard_handles_the_empty_state() -> None:
    snapshot = build_dashboard_snapshot(CostTracker(), PerformanceTracker())

    report = render_dashboard_text(snapshot)

    assert snapshot.is_empty
    assert "No monitoring data recorded yet." in report
    assert "COST" not in report


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_dry_run_cli_renders_a_populated_dashboard(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main.main(["--dry-run", "--limit", "2", "--models", FLASH])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Monitoring Dashboard" in out
    assert "PERFORMANCE" in out
    assert FLASH in out


def test_dry_run_cli_exits_2_when_over_budget(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main.main(["--dry-run", "--limit", "1", "--models", FLASH, "--budget", "0"])

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "OVER BUDGET" in out


def test_dry_run_cli_exits_0_when_within_budget(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main.main(["--dry-run", "--limit", "1", "--models", FLASH, "--budget", "5"])

    assert exit_code == 0
    assert "WITHIN BUDGET" in capsys.readouterr().out


def test_cli_rejects_an_empty_model_list() -> None:
    assert main.main(["--dry-run", "--models", " , "]) == 1


def test_cli_reports_a_missing_api_key_without_running() -> None:
    with pytest.raises(main.MissingAPIKeyError):
        main.get_api_key({"GEMINI_API_KEY": "  "})
