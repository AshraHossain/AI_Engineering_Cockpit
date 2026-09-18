"""Unit tests for cockpit/monitoring/{cost_tracking,performance_metrics,dashboard}.py."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from cockpit.config import feature_flags
from cockpit.monitoring import cost_tracking, performance_metrics
from cockpit.monitoring.cost_tracking import (
    DEFAULT_PRICING,
    CostEntry,
    CostTracker,
    ModelPricing,
    UnknownModelError,
    estimate_cost,
    get_cost_by_model,
    get_total_cost,
    record_cost,
)
from cockpit.monitoring.dashboard import (
    DashboardSnapshot,
    build_dashboard_snapshot,
    render_dashboard_text,
)
from cockpit.monitoring.performance_metrics import (
    CallOutcome,
    LatencySample,
    PerformanceSummary,
    PerformanceTracker,
    percentile,
    record_latency,
    summarize_performance,
    summarize_samples,
)

FIXED_TIME = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)


def fake_clock(values: list[float]) -> Callable[[], float]:
    """Build a deterministic stand-in for ``time.perf_counter``.

    Args:
        values: Readings to return, in order. ``measure`` consumes two per
            timed block (start and end).

    Returns:
        A zero-argument callable yielding the next reading each call.
    """
    readings = iter(values)
    return lambda: next(readings)


@pytest.fixture
def monitoring_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force the ``monitoring`` feature flag on for one test.

    Yields:
        None.
    """
    monkeypatch.setitem(feature_flags.FRAMEWORKS_ENABLED, "monitoring", True)
    yield


@pytest.fixture
def monitoring_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force the ``monitoring`` feature flag off for one test.

    Yields:
        None.
    """
    monkeypatch.setitem(feature_flags.FRAMEWORKS_ENABLED, "monitoring", False)
    yield


@pytest.fixture
def clean_default_trackers() -> Iterator[None]:
    """Reset the shared default trackers around a test.

    The module-level convenience functions write to process-wide trackers;
    without this the tests would leak state into each other.

    Yields:
        None.
    """
    cost_tracking.get_default_tracker().reset()
    performance_metrics.get_default_tracker().reset()
    yield
    cost_tracking.get_default_tracker().reset()
    performance_metrics.get_default_tracker().reset()


class TestEstimateCost:
    def test_prices_a_known_model_from_both_token_counts(self) -> None:
        # gemini-2.5-flash: $0.30/1M input, $2.50/1M output.
        cost = estimate_cost("gemini-2.5-flash", 1_000, 500)
        assert cost == pytest.approx(0.0003 + 0.00125)

    def test_one_million_input_tokens_equals_the_listed_rate(self) -> None:
        expected = DEFAULT_PRICING["gpt-4o-mini"].input_per_1m
        assert estimate_cost("gpt-4o-mini", 1_000_000) == pytest.approx(expected)

    def test_zero_tokens_costs_nothing(self) -> None:
        assert estimate_cost("gpt-4o", 0, 0) == 0.0

    def test_input_only_ignores_the_output_rate(self) -> None:
        assert estimate_cost("gpt-4o", 1_000_000, 0) == pytest.approx(2.50)

    @pytest.mark.parametrize(
        ("model", "input_rate", "output_rate"),
        [("claude-opus-5", 5.00, 25.00), ("claude-sonnet-5", 2.00, 10.00)],
    )
    def test_prices_claude_models(self, model: str, input_rate: float, output_rate: float) -> None:
        assert estimate_cost(model, 1_000_000, 1_000_000) == pytest.approx(input_rate + output_rate)

    def test_output_only_ignores_the_input_rate(self) -> None:
        assert estimate_cost("gpt-4o", 0, 1_000_000) == pytest.approx(10.00)

    def test_embedding_model_bills_input_only(self) -> None:
        assert estimate_cost("gemini-embedding-001", 0, 1_000_000) == 0.0

    def test_output_tokens_default_to_zero(self) -> None:
        assert estimate_cost("gpt-4o", 1_000) == estimate_cost("gpt-4o", 1_000, 0)

    @pytest.mark.parametrize(
        ("input_tokens", "output_tokens"),
        [(-1, 0), (0, -1), (-5, -5)],
    )
    def test_negative_tokens_raise_value_error(self, input_tokens: int, output_tokens: int) -> None:
        with pytest.raises(ValueError):
            estimate_cost("gpt-4o", input_tokens, output_tokens)

    def test_unknown_model_raises_unknown_model_error(self) -> None:
        with pytest.raises(UnknownModelError):
            estimate_cost("no-such-model", 100)

    def test_unknown_model_error_is_a_key_error(self) -> None:
        assert issubclass(UnknownModelError, KeyError)

    def test_custom_pricing_table_is_used_instead_of_defaults(self) -> None:
        pricing = {"house-model": ModelPricing(1_000.0, 2_000.0, "internal")}
        cost = estimate_cost("house-model", 1_000_000, 1_000_000, pricing=pricing)
        assert cost == pytest.approx(3_000.0)

    def test_custom_pricing_table_overrides_a_default_model(self) -> None:
        pricing = {"gemini-2.5-flash": ModelPricing(999.0, 0.0, "negotiated")}
        assert estimate_cost("gemini-2.5-flash", 1_000_000, pricing=pricing) == pytest.approx(999.0)

    def test_custom_pricing_table_does_not_fall_back_to_defaults(self) -> None:
        pricing = {"house-model": ModelPricing(1.0, 1.0, "internal")}
        with pytest.raises(UnknownModelError):
            estimate_cost("gpt-4o", 100, pricing=pricing)

    def test_is_pure_and_records_nothing(self, clean_default_trackers: None) -> None:
        estimate_cost("gpt-4o", 1_000, 1_000)
        assert cost_tracking.get_default_tracker().entries == []


class TestCostTracker:
    def test_record_returns_the_entry_and_stores_it(self) -> None:
        tracker = CostTracker()
        entry = tracker.record("rag", "gpt-4o", 0.5, input_tokens=10, output_tokens=5)
        assert isinstance(entry, CostEntry)
        assert tracker.entries == [entry]
        assert entry.project == "rag"
        assert entry.model == "gpt-4o"

    def test_record_stamps_utc_time_by_default(self) -> None:
        tracker = CostTracker()
        entry = tracker.record("rag", "gpt-4o", 0.5)
        assert entry.timestamp.tzinfo is not None

    def test_record_accepts_an_injected_timestamp(self) -> None:
        tracker = CostTracker()
        entry = tracker.record("rag", "gpt-4o", 0.5, timestamp=FIXED_TIME)
        assert entry.timestamp == FIXED_TIME

    def test_negative_cost_is_rejected(self) -> None:
        tracker = CostTracker()
        with pytest.raises(ValueError):
            tracker.record("rag", "gpt-4o", -0.01)

    def test_zero_cost_is_allowed(self) -> None:
        tracker = CostTracker()
        assert tracker.record("rag", "gpt-4o", 0.0).cost_usd == 0.0

    def test_total_cost_sums_every_entry(self) -> None:
        tracker = CostTracker()
        tracker.record("rag", "gpt-4o", 1.0)
        tracker.record("agent", "gpt-4o", 0.5)
        assert tracker.total_cost() == pytest.approx(1.5)

    def test_total_cost_scopes_to_a_project(self) -> None:
        tracker = CostTracker()
        tracker.record("rag", "gpt-4o", 1.0)
        tracker.record("agent", "gpt-4o", 0.5)
        assert tracker.total_cost(project="rag") == pytest.approx(1.0)
        assert tracker.total_cost(project="agent") == pytest.approx(0.5)

    def test_total_cost_of_unknown_project_is_zero(self) -> None:
        tracker = CostTracker()
        tracker.record("rag", "gpt-4o", 1.0)
        assert tracker.total_cost(project="nope") == 0.0

    def test_total_cost_of_empty_tracker_is_zero(self) -> None:
        assert CostTracker().total_cost() == 0

    def test_cost_by_model_aggregates_per_model(self) -> None:
        tracker = CostTracker()
        tracker.record("rag", "gpt-4o", 1.0)
        tracker.record("agent", "gpt-4o", 0.5)
        tracker.record("rag", "gemini-2.5-flash", 0.25)
        assert tracker.cost_by_model() == pytest.approx({"gpt-4o": 1.5, "gemini-2.5-flash": 0.25})

    def test_cost_by_project_aggregates_per_project(self) -> None:
        tracker = CostTracker()
        tracker.record("rag", "gpt-4o", 1.0)
        tracker.record("agent", "gpt-4o", 0.5)
        tracker.record("rag", "gemini-2.5-flash", 0.25)
        assert tracker.cost_by_project() == pytest.approx({"rag": 1.25, "agent": 0.5})

    def test_breakdowns_of_empty_tracker_are_empty_dicts(self) -> None:
        tracker = CostTracker()
        assert tracker.cost_by_model() == {}
        assert tracker.cost_by_project() == {}

    def test_total_tokens_returns_input_output_pair(self) -> None:
        tracker = CostTracker()
        tracker.record("rag", "gpt-4o", 1.0, input_tokens=100, output_tokens=20)
        tracker.record("rag", "gpt-4o", 1.0, input_tokens=50, output_tokens=5)
        assert tracker.total_tokens() == (150, 25)

    def test_total_tokens_of_empty_tracker_is_zero_pair(self) -> None:
        assert CostTracker().total_tokens() == (0, 0)

    def test_record_usage_prices_and_records_in_one_step(self) -> None:
        tracker = CostTracker()
        entry = tracker.record_usage("rag", "gemini-2.5-flash", 1_000, 500)
        assert entry.cost_usd == pytest.approx(estimate_cost("gemini-2.5-flash", 1_000, 500))
        assert tracker.total_tokens() == (1_000, 500)

    def test_record_usage_honors_a_custom_price_table(self) -> None:
        tracker = CostTracker()
        pricing = {"house-model": ModelPricing(1_000.0, 0.0, "internal")}
        entry = tracker.record_usage("rag", "house-model", 1_000_000, pricing=pricing)
        assert entry.cost_usd == pytest.approx(1_000.0)

    def test_record_usage_rejects_unknown_models(self) -> None:
        tracker = CostTracker()
        with pytest.raises(UnknownModelError):
            tracker.record_usage("rag", "no-such-model", 10)

    def test_reset_drops_every_entry(self) -> None:
        tracker = CostTracker()
        tracker.record("rag", "gpt-4o", 1.0)
        tracker.reset()
        assert tracker.entries == []
        assert tracker.total_cost() == 0

    def test_trackers_are_independent(self) -> None:
        first, second = CostTracker(), CostTracker()
        first.record("rag", "gpt-4o", 1.0)
        assert second.total_cost() == 0

    def test_concurrent_records_are_not_lost(self) -> None:
        tracker = CostTracker()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: tracker.record("rag", "gpt-4o", 0.01), range(200)))
        assert len(tracker.entries) == 200
        assert tracker.total_cost() == pytest.approx(2.0)


class TestCostModuleFunctions:
    def test_record_cost_returns_none_when_monitoring_disabled(
        self, monitoring_off: None, clean_default_trackers: None
    ) -> None:
        assert record_cost("rag", "gpt-4o", 1.0) is None
        assert cost_tracking.get_default_tracker().entries == []

    def test_record_cost_records_when_monitoring_enabled(
        self, monitoring_on: None, clean_default_trackers: None
    ) -> None:
        entry = record_cost("rag", "gpt-4o", 1.0, input_tokens=10, output_tokens=2)
        assert entry is not None
        assert get_total_cost() == pytest.approx(1.0)
        assert get_total_cost(project="rag") == pytest.approx(1.0)
        assert get_cost_by_model() == pytest.approx({"gpt-4o": 1.0})

    def test_record_cost_still_validates_when_enabled(
        self, monitoring_on: None, clean_default_trackers: None
    ) -> None:
        with pytest.raises(ValueError):
            record_cost("rag", "gpt-4o", -1.0)

    def test_get_default_tracker_is_a_singleton(self) -> None:
        assert cost_tracking.get_default_tracker() is cost_tracking.get_default_tracker()


class TestPercentile:
    def test_empty_sequence_returns_zero_without_crashing(self) -> None:
        assert percentile([], 50.0) == 0.0
        assert percentile([], 95.0) == 0.0
        assert percentile([], 99.0) == 0.0

    @pytest.mark.parametrize("rank", [0.0, 50.0, 95.0, 99.0, 100.0])
    def test_single_sample_is_its_own_percentile_at_every_rank(self, rank: float) -> None:
        assert percentile([0.42], rank) == pytest.approx(0.42)

    def test_known_values_over_ten_samples(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        # Nearest rank: index = ceil(p/100 * n), 1-based, clamped to [1, n].
        assert percentile(values, 50.0) == 5.0
        assert percentile(values, 90.0) == 9.0
        assert percentile(values, 95.0) == 10.0
        assert percentile(values, 99.0) == 10.0

    def test_rank_zero_and_one_hundred_clamp_to_min_and_max(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        assert percentile(values, 0.0) == 1.0
        assert percentile(values, 100.0) == 10.0

    def test_two_samples(self) -> None:
        assert percentile([1.0, 3.0], 50.0) == 1.0
        assert percentile([1.0, 3.0], 95.0) == 3.0

    def test_four_samples(self) -> None:
        values = [40.0, 10.0, 30.0, 20.0]
        assert percentile(values, 50.0) == 20.0
        assert percentile(values, 95.0) == 40.0

    def test_input_order_does_not_matter(self) -> None:
        assert percentile([9.0, 1.0, 5.0], 50.0) == percentile([1.0, 5.0, 9.0], 50.0)

    def test_input_sequence_is_not_mutated(self) -> None:
        values = [3.0, 1.0, 2.0]
        percentile(values, 50.0)
        assert values == [3.0, 1.0, 2.0]

    def test_exact_rank_boundary_is_not_pushed_up_by_float_error(self) -> None:
        # 10% of 30 is exactly 3; naive `p/100 * n` yields 3.0000000000000004
        # and would wrongly select the 4th value.
        values = [float(i) for i in range(1, 31)]
        assert percentile(values, 10.0) == 3.0

    @pytest.mark.parametrize("rank", [-0.1, 100.1, -50.0, 200.0])
    def test_out_of_range_rank_raises_value_error(self, rank: float) -> None:
        with pytest.raises(ValueError):
            percentile([1.0], rank)


class TestSummarizeSamples:
    def test_empty_samples_produce_the_zero_summary(self) -> None:
        summary = summarize_samples("gemini.generate", [])
        assert summary == PerformanceSummary.empty("gemini.generate")
        assert summary.sample_count == 0
        assert summary.mean_seconds == 0.0
        assert summary.p50_seconds == 0.0
        assert summary.error_rate == 0.0
        assert summary.tokens_per_second == 0.0

    def test_single_sample_summary(self) -> None:
        summary = summarize_samples("op", [LatencySample("op", 0.5)])
        assert summary.sample_count == 1
        assert summary.total_seconds == pytest.approx(0.5)
        assert summary.mean_seconds == pytest.approx(0.5)
        assert summary.min_seconds == pytest.approx(0.5)
        assert summary.max_seconds == pytest.approx(0.5)
        assert summary.p50_seconds == pytest.approx(0.5)
        assert summary.p95_seconds == pytest.approx(0.5)
        assert summary.p99_seconds == pytest.approx(0.5)

    def test_known_multi_sample_summary(self) -> None:
        durations = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        samples = [LatencySample("op", d) for d in durations]
        summary = summarize_samples("op", samples)
        assert summary.sample_count == 10
        assert summary.total_seconds == pytest.approx(55.0)
        assert summary.mean_seconds == pytest.approx(5.5)
        assert summary.min_seconds == 1.0
        assert summary.max_seconds == 10.0
        assert summary.p50_seconds == 5.0
        assert summary.p95_seconds == 10.0
        assert summary.p99_seconds == 10.0

    def test_unsorted_samples_are_ordered_before_percentiles(self) -> None:
        samples = [LatencySample("op", d) for d in (9.0, 1.0, 5.0)]
        summary = summarize_samples("op", samples)
        assert summary.min_seconds == 1.0
        assert summary.max_seconds == 9.0
        assert summary.p50_seconds == 5.0

    def test_success_and_error_counts_and_rate(self) -> None:
        samples = [
            LatencySample("op", 1.0),
            LatencySample("op", 1.0, outcome=CallOutcome.ERROR),
            LatencySample("op", 1.0, outcome=CallOutcome.ERROR),
            LatencySample("op", 1.0),
        ]
        summary = summarize_samples("op", samples)
        assert summary.success_count == 2
        assert summary.error_count == 2
        assert summary.error_rate == pytest.approx(0.5)

    def test_token_totals_and_throughput(self) -> None:
        samples = [
            LatencySample("op", 1.0, input_tokens=100, output_tokens=20),
            LatencySample("op", 1.0, input_tokens=50, output_tokens=30),
        ]
        summary = summarize_samples("op", samples)
        assert summary.total_input_tokens == 150
        assert summary.total_output_tokens == 50
        assert summary.tokens_per_second == pytest.approx(100.0)

    def test_throughput_is_zero_when_no_tokens_recorded(self) -> None:
        summary = summarize_samples("op", [LatencySample("op", 2.0)])
        assert summary.tokens_per_second == 0.0

    def test_throughput_does_not_divide_by_zero_duration(self) -> None:
        samples = [LatencySample("op", 0.0, input_tokens=10)]
        assert summarize_samples("op", samples).tokens_per_second == 0.0

    def test_summary_is_frozen(self) -> None:
        summary = PerformanceSummary.empty("op")
        with pytest.raises(FrozenInstanceError):
            summary.sample_count = 5  # type: ignore[misc]


class TestPerformanceTracker:
    def test_record_stores_a_sample(self) -> None:
        tracker = PerformanceTracker()
        sample = tracker.record("op", 0.25)
        assert sample.operation == "op"
        assert sample.duration_seconds == 0.25
        assert sample.outcome is CallOutcome.SUCCESS
        assert tracker.samples == [sample]

    def test_record_rejects_negative_duration(self) -> None:
        with pytest.raises(ValueError):
            PerformanceTracker().record("op", -0.1)

    @pytest.mark.parametrize(("in_tokens", "out_tokens"), [(-1, 0), (0, -1)])
    def test_record_rejects_negative_tokens(self, in_tokens: int, out_tokens: int) -> None:
        with pytest.raises(ValueError):
            PerformanceTracker().record("op", 0.1, input_tokens=in_tokens, output_tokens=out_tokens)

    def test_zero_duration_is_allowed(self) -> None:
        assert PerformanceTracker().record("op", 0.0).duration_seconds == 0.0

    def test_measure_records_the_elapsed_duration(self) -> None:
        tracker = PerformanceTracker(clock=fake_clock([10.0, 10.25]))
        with tracker.measure("gemini.generate"):
            pass
        assert tracker.samples[0].duration_seconds == pytest.approx(0.25)
        assert tracker.samples[0].outcome is CallOutcome.SUCCESS

    def test_measure_yields_a_handle_for_token_counts(self) -> None:
        tracker = PerformanceTracker(clock=fake_clock([0.0, 2.0]))
        with tracker.measure("gemini.generate") as call:
            call.input_tokens = 400
            call.output_tokens = 100
        sample = tracker.samples[0]
        assert (sample.input_tokens, sample.output_tokens) == (400, 100)
        assert tracker.summary("gemini.generate").tokens_per_second == pytest.approx(250.0)

    def test_measure_accepts_token_counts_up_front(self) -> None:
        tracker = PerformanceTracker(clock=fake_clock([0.0, 1.0]))
        with tracker.measure("op", input_tokens=7, output_tokens=3):
            pass
        assert tracker.samples[0].input_tokens == 7
        assert tracker.samples[0].output_tokens == 3

    def test_measure_records_an_error_and_reraises(self) -> None:
        tracker = PerformanceTracker(clock=fake_clock([0.0, 0.5]))
        with pytest.raises(RuntimeError, match="boom"), tracker.measure("op"):
            raise RuntimeError("boom")
        assert len(tracker.samples) == 1
        assert tracker.samples[0].outcome is CallOutcome.ERROR
        assert tracker.samples[0].duration_seconds == pytest.approx(0.5)

    def test_measure_honors_a_manually_flagged_soft_failure(self) -> None:
        tracker = PerformanceTracker(clock=fake_clock([0.0, 0.1]))
        with tracker.measure("op") as call:
            call.outcome = CallOutcome.ERROR
        assert tracker.samples[0].outcome is CallOutcome.ERROR

    def test_measure_with_the_real_clock_produces_a_non_negative_duration(self) -> None:
        tracker = PerformanceTracker()
        with tracker.measure("op"):
            pass
        assert tracker.samples[0].duration_seconds >= 0.0

    def test_timed_decorator_records_each_call(self) -> None:
        tracker = PerformanceTracker(clock=fake_clock([0.0, 1.0, 5.0, 8.0]))

        @tracker.timed("embed")
        def embed(text: str) -> int:
            return len(text)

        assert embed("abc") == 3
        assert embed("abcd") == 4
        summary = tracker.summary("embed")
        assert summary.sample_count == 2
        assert summary.total_seconds == pytest.approx(4.0)

    def test_timed_decorator_preserves_metadata(self) -> None:
        tracker = PerformanceTracker()

        @tracker.timed()
        def documented() -> None:
            """Docstring survives."""

        documented()
        assert documented.__name__ == "documented"
        assert documented.__doc__ == "Docstring survives."

    def test_timed_decorator_defaults_to_the_function_name(self) -> None:
        tracker = PerformanceTracker()

        @tracker.timed()
        def my_operation() -> None:
            return None

        my_operation()
        assert any("my_operation" in name for name in tracker.operations())

    def test_timed_decorator_books_exceptions_as_errors(self) -> None:
        tracker = PerformanceTracker(clock=fake_clock([0.0, 0.2]))

        @tracker.timed("flaky")
        def flaky() -> None:
            raise ValueError("nope")

        with pytest.raises(ValueError):
            flaky()
        assert tracker.summary("flaky").error_count == 1

    def test_samples_for_filters_by_operation(self) -> None:
        tracker = PerformanceTracker()
        tracker.record("a", 1.0)
        tracker.record("b", 2.0)
        assert [s.operation for s in tracker.samples_for("a")] == ["a"]
        assert tracker.samples_for("missing") == []

    def test_samples_for_returns_a_copy(self) -> None:
        tracker = PerformanceTracker()
        tracker.record("a", 1.0)
        tracker.samples_for("a").clear()
        assert len(tracker.samples) == 1

    def test_operations_are_unique_and_sorted(self) -> None:
        tracker = PerformanceTracker()
        tracker.record("zeta", 1.0)
        tracker.record("alpha", 1.0)
        tracker.record("zeta", 1.0)
        assert tracker.operations() == ["alpha", "zeta"]

    def test_summary_of_unknown_operation_is_empty_not_an_error(self) -> None:
        assert PerformanceTracker().summary("never-called").sample_count == 0

    def test_summaries_group_by_operation(self) -> None:
        tracker = PerformanceTracker()
        tracker.record("a", 1.0)
        tracker.record("a", 3.0)
        tracker.record("b", 2.0)
        summaries = tracker.summaries()
        assert list(summaries) == ["a", "b"]
        assert summaries["a"].sample_count == 2
        assert summaries["b"].total_seconds == pytest.approx(2.0)

    def test_summaries_of_empty_tracker_is_empty_dict(self) -> None:
        assert PerformanceTracker().summaries() == {}

    def test_call_count_overall_and_scoped(self) -> None:
        tracker = PerformanceTracker()
        tracker.record("a", 1.0)
        tracker.record("b", 1.0)
        tracker.record("a", 1.0)
        assert tracker.call_count() == 3
        assert tracker.call_count("a") == 2
        assert tracker.call_count("missing") == 0

    def test_error_rate_overall_and_scoped(self) -> None:
        tracker = PerformanceTracker()
        tracker.record("a", 1.0)
        tracker.record("a", 1.0, outcome=CallOutcome.ERROR)
        tracker.record("b", 1.0)
        assert tracker.error_rate() == pytest.approx(1 / 3)
        assert tracker.error_rate("a") == pytest.approx(0.5)
        assert tracker.error_rate("b") == 0.0

    def test_error_rate_of_empty_tracker_is_zero(self) -> None:
        assert PerformanceTracker().error_rate() == 0.0
        assert PerformanceTracker().error_rate("missing") == 0.0

    def test_total_tokens_pair(self) -> None:
        tracker = PerformanceTracker()
        tracker.record("a", 1.0, input_tokens=10, output_tokens=1)
        tracker.record("a", 1.0, input_tokens=5, output_tokens=2)
        assert tracker.total_tokens() == (15, 3)

    def test_reset_drops_every_sample(self) -> None:
        tracker = PerformanceTracker()
        tracker.record("a", 1.0)
        tracker.reset()
        assert tracker.samples == []
        assert tracker.call_count() == 0

    def test_concurrent_records_are_not_lost(self) -> None:
        tracker = PerformanceTracker()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: tracker.record("op", 0.01), range(200)))
        assert tracker.call_count("op") == 200


class TestPerformanceModuleFunctions:
    def test_record_latency_returns_none_when_monitoring_disabled(
        self, monitoring_off: None, clean_default_trackers: None
    ) -> None:
        assert record_latency("op", 1.0) is None
        assert performance_metrics.get_default_tracker().samples == []

    def test_record_latency_records_when_monitoring_enabled(
        self, monitoring_on: None, clean_default_trackers: None
    ) -> None:
        sample = record_latency("op", 1.0, input_tokens=10)
        assert sample is not None
        assert summarize_performance("op").sample_count == 1

    def test_record_latency_still_validates_when_enabled(
        self, monitoring_on: None, clean_default_trackers: None
    ) -> None:
        with pytest.raises(ValueError):
            record_latency("op", -1.0)

    def test_summarize_performance_is_not_flag_gated(
        self, monitoring_off: None, clean_default_trackers: None
    ) -> None:
        summary = summarize_performance("op")
        assert summary.sample_count == 0
        assert summary.operation == "op"

    def test_get_default_tracker_is_a_singleton(self) -> None:
        assert (
            performance_metrics.get_default_tracker() is performance_metrics.get_default_tracker()
        )


def populated_trackers() -> tuple[CostTracker, PerformanceTracker]:
    """Build a pair of trackers holding a small, known workload.

    Returns:
        A ``(cost_tracker, performance_tracker)`` pair.
    """
    costs = CostTracker()
    costs.record_usage("rag", "gemini-2.5-flash", 1_000, 500, timestamp=FIXED_TIME)
    costs.record_usage("rag", "gpt-4o-mini", 2_000, 100, timestamp=FIXED_TIME)
    costs.record_usage("agent", "gpt-4o-mini", 500, 50, timestamp=FIXED_TIME)

    perf = PerformanceTracker()
    for duration in (0.1, 0.2, 0.3):
        perf.record("gemini.generate", duration, input_tokens=100, output_tokens=50)
    perf.record("gemini.generate", 0.4, outcome=CallOutcome.ERROR)
    perf.record("openai.embed", 0.05)
    return costs, perf


class TestDashboardSnapshot:
    def test_snapshot_rolls_up_both_trackers(self) -> None:
        costs, perf = populated_trackers()
        snapshot = build_dashboard_snapshot(costs, perf, generated_at=FIXED_TIME)

        assert isinstance(snapshot, DashboardSnapshot)
        assert snapshot.generated_at == FIXED_TIME
        assert snapshot.total_cost_usd == pytest.approx(costs.total_cost())
        assert set(snapshot.cost_by_model) == {"gemini-2.5-flash", "gpt-4o-mini"}
        assert set(snapshot.cost_by_project) == {"rag", "agent"}
        assert snapshot.total_input_tokens == 3_500
        assert snapshot.total_output_tokens == 650
        assert snapshot.total_tokens == 4_150
        assert snapshot.total_calls == 5
        assert snapshot.error_rate == pytest.approx(0.2)
        assert set(snapshot.performance_by_operation) == {"gemini.generate", "openai.embed"}
        assert not snapshot.is_empty

    def test_snapshot_percentiles_match_the_tracker(self) -> None:
        costs, perf = populated_trackers()
        snapshot = build_dashboard_snapshot(costs, perf)
        summary = snapshot.performance_by_operation["gemini.generate"]
        assert summary.sample_count == 4
        assert summary.p50_seconds == pytest.approx(0.2)
        assert summary.p95_seconds == pytest.approx(0.4)
        assert summary.error_count == 1

    def test_recent_costs_are_limited_to_the_newest_entries(self) -> None:
        costs, perf = populated_trackers()
        snapshot = build_dashboard_snapshot(costs, perf, recent_limit=2)
        assert len(snapshot.recent_costs) == 2
        assert snapshot.recent_costs[-1] is costs.entries[-1]

    def test_recent_limit_zero_yields_no_entries(self) -> None:
        costs, perf = populated_trackers()
        assert build_dashboard_snapshot(costs, perf, recent_limit=0).recent_costs == []

    def test_negative_recent_limit_raises(self) -> None:
        with pytest.raises(ValueError):
            build_dashboard_snapshot(CostTracker(), PerformanceTracker(), recent_limit=-1)

    def test_snapshot_defaults_to_now(self) -> None:
        snapshot = build_dashboard_snapshot(CostTracker(), PerformanceTracker())
        assert snapshot.generated_at.tzinfo is not None

    def test_empty_trackers_produce_an_empty_snapshot(self) -> None:
        snapshot = build_dashboard_snapshot(CostTracker(), PerformanceTracker())
        assert snapshot.is_empty
        assert snapshot.total_cost_usd == 0
        assert snapshot.total_tokens == 0
        assert snapshot.total_calls == 0
        assert snapshot.error_rate == 0.0
        assert snapshot.cost_by_model == {}
        assert snapshot.performance_by_operation == {}

    def test_snapshot_does_not_alias_the_tracker_entry_list(self) -> None:
        costs, perf = populated_trackers()
        snapshot = build_dashboard_snapshot(costs, perf)
        costs.reset()
        assert snapshot.recent_costs


class TestRenderDashboardText:
    def test_render_returns_a_string_and_does_not_print(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        costs, perf = populated_trackers()
        text = render_dashboard_text(build_dashboard_snapshot(costs, perf))
        assert isinstance(text, str)
        assert capsys.readouterr().out == ""

    def test_render_includes_the_headline_sections(self) -> None:
        costs, perf = populated_trackers()
        text = render_dashboard_text(build_dashboard_snapshot(costs, perf, generated_at=FIXED_TIME))
        assert "Monitoring Dashboard" in text
        assert "COST" in text
        assert "PERFORMANCE" in text
        assert "RECENT COSTS" in text
        assert "gemini-2.5-flash" in text
        assert "gemini.generate" in text
        assert "rag" in text

    def test_render_shows_token_totals_and_error_rate(self) -> None:
        costs, perf = populated_trackers()
        text = render_dashboard_text(build_dashboard_snapshot(costs, perf))
        assert "3,500" in text
        assert "20.00%" in text

    def test_render_columns_are_aligned(self) -> None:
        costs, perf = populated_trackers()
        text = render_dashboard_text(build_dashboard_snapshot(costs, perf))
        rows = [line for line in text.splitlines() if "gemini.generate" in line]
        assert len(rows) == 1
        header = next(line for line in text.splitlines() if "Operation" in line)
        assert len(rows[0]) == len(header)

    def test_render_empty_state_does_not_crash(self) -> None:
        snapshot = build_dashboard_snapshot(CostTracker(), PerformanceTracker())
        text = render_dashboard_text(snapshot)
        assert "No monitoring data recorded yet." in text
        assert "nan" not in text.lower()

    def test_render_handles_costs_without_any_timed_calls(self) -> None:
        costs = CostTracker()
        costs.record("rag", "gpt-4o", 1.0, input_tokens=10)
        text = render_dashboard_text(build_dashboard_snapshot(costs, PerformanceTracker()))
        assert "(no timed calls recorded)" in text
        assert "0.00%" in text

    def test_render_handles_timed_calls_without_any_costs(self) -> None:
        perf = PerformanceTracker()
        perf.record("op", 0.1)
        text = render_dashboard_text(build_dashboard_snapshot(CostTracker(), perf))
        assert "$0.000000" in text
        assert "(none)" in text

    def test_render_is_not_flag_gated(self, monitoring_off: None) -> None:
        costs, perf = populated_trackers()
        text = render_dashboard_text(build_dashboard_snapshot(costs, perf))
        assert "PERFORMANCE" in text
