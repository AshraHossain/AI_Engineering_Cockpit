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
import tools
from fake_model import FakeClock, ModelProfile, Turn, scripted_client, text
from tools import SupportAPI


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


@pytest.mark.parametrize("content", [None, "\n  \n"], ids=["missing-file", "no-questions"])
def test_an_unusable_questions_file_exits_with_usage(tmp_path: Path, content: str | None) -> None:
    path = tmp_path / "questions.txt"
    if content is not None:
        path.write_text(content)
    with pytest.raises(SystemExit) as exc:
        main.main(["--questions", str(path)])
    assert exc.value.code == 2


def test_live_mode_asks_your_questions_against_your_support_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    asked: list[str] = []

    def answer(messages: list[dict[str, Any]]) -> Turn:
        asked.append(messages[0]["content"])
        return Turn([text("On its way.")])

    secrets = {
        "ANTHROPIC_API_KEY": "sk-test",  # pragma: allowlist secret
        "SUPPORT_API_TOKEN": "t0ken",  # pragma: allowlist secret
    }
    monkeypatch.setattr(main, "get_secret", lambda key, default=None: secrets.get(key, default))
    monkeypatch.setenv("SUPPORT_API_URL", "https://support.example.test/v1")
    fake = scripted_client(
        {"claude-opus-5": ModelProfile(answer), "claude-sonnet-5": ModelProfile(answer)},
        FakeClock(),
    )
    monkeypatch.setattr(main.anthropic, "Anthropic", lambda api_key: fake)
    questions = tmp_path / "questions.txt"
    questions.write_text("Where is ORD-1?\n\nCan I return ORD-2?\n")

    assert main.main(["--requests", "3", "--questions", str(questions)]) == 0
    assert asked == ["Where is ORD-1?", "Can I return ORD-2?", "Where is ORD-1?"]
    assert tools._support_api == SupportAPI("https://support.example.test/v1", "t0ken")
    captured = capsys.readouterr()
    assert "Tools: support API at https://support.example.test/v1" in captured.out
    assert "warning" not in captured.err


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
