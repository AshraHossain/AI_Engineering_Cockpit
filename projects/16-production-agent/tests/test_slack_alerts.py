"""Slack notifications: real alert lines in, one message per new transition out."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import httpx2
import pytest

import slack_alerts
from alerts import AlertEvent
from fake_model import DRY_RUN_EPOCH

WEBHOOK = "https://hooks.slack.test/services/T0/B0/x"


def _line(rule: str, state: str, request_id: str) -> str:
    event = AlertEvent(DRY_RUN_EPOCH + 90, "v2", rule, state, 0.333, 0.3, request_id)
    return json.dumps(asdict(event))


def _slack(*statuses: int) -> tuple[httpx2.Client, list[str]]:
    replies = iter(statuses)
    texts: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        texts.append(json.loads(request.content)["text"])
        return httpx2.Response(next(replies))

    return httpx2.Client(transport=httpx2.MockTransport(handler)), texts


def test_a_message_reads_the_line_the_sink_writes() -> None:
    text = slack_alerts.message(json.loads(_line("tool_error_rate", "fired", "req-0338")))["text"]
    assert text == (
        ":rotating_light: *tool_error_rate* fired for `v2`: 0.333 (threshold 0.3), "
        "at req-0338, 2026-01-01 00:01:30 UTC"
    )


def test_only_new_lines_are_posted(tmp_path: Path) -> None:
    alerts = tmp_path / "alerts.jsonl"
    alerts.write_text(_line("loop_detected", "fired", "req-1") + "\n")
    client, texts = _slack(200, 200)
    assert slack_alerts.post_new(alerts, WEBHOOK, client) == (1, True)

    with alerts.open("a") as handle:
        handle.write(_line("loop_detected", "resolved", "req-9") + "\n")
    assert slack_alerts.post_new(alerts, WEBHOOK, client) == (1, True)
    assert [t.split("*")[2].split()[0] for t in texts] == ["fired", "resolved"]


def test_a_failed_post_stops_and_is_retried_next_run(tmp_path: Path) -> None:
    alerts = tmp_path / "alerts.jsonl"
    alerts.write_text(
        _line("failure_rate", "fired", "req-1")
        + "\n"
        + _line("failure_rate", "resolved", "req-2")
        + "\n"
    )
    client, texts = _slack(500, 200, 200)
    assert slack_alerts.post_new(alerts, WEBHOOK, client) == (0, False)
    assert slack_alerts.post_new(alerts, WEBHOOK, client) == (2, True)
    assert len(texts) == 3


def test_no_webhook_exits_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(slack_alerts, "PROJECT_DIR", tmp_path)
    assert slack_alerts.main([str(tmp_path / "alerts.jsonl")]) == 2
