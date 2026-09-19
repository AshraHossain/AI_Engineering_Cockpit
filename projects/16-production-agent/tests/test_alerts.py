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


def test_service_errors_are_not_counted_as_the_models_tool_errors(
    make_record: Callable[..., RunRecord],
) -> None:
    stats = WindowStats.of(
        [
            make_record(tool_calls=4, tool_errors=1, service_errors=2),
            make_record(tool_calls=4, tool_errors=0, service_errors=0),
        ]
    )
    assert stats.tool_error_rate == pytest.approx(1 / 6)
    assert stats.service_error_rate == pytest.approx(2 / 8)


def test_an_all_outage_window_has_no_tool_error_rate(
    make_record: Callable[..., RunRecord],
) -> None:
    stats = WindowStats.of([make_record(tool_calls=1, tool_errors=0, service_errors=1)])
    assert (stats.tool_error_rate, stats.service_error_rate) == (0.0, 1.0)
