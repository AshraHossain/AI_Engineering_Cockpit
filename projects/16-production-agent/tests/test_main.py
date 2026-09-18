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
