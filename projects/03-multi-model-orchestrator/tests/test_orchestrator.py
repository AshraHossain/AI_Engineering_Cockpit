"""Tests for the orchestration/comparison logic.

Both provider clients are mocked with :class:`unittest.mock.MagicMock` so
these tests never perform network calls or require live API keys — only
``Orchestrator`` and its dataclasses are under test.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from orchestrator import Orchestrator


def make_client(response: str | None = None, error: Exception | None = None) -> MagicMock:
    """Build a mock model client that either returns ``response`` or raises ``error``."""
    client = MagicMock()
    if error is not None:
        client.generate.side_effect = error
    else:
        client.generate.return_value = response
    return client


def test_run_calls_every_configured_client_with_the_prompt() -> None:
    gemini = make_client(response="gemini says hi")
    openai = make_client(response="openai says hi")

    orchestrator = Orchestrator({"gemini": gemini, "openai": openai})
    report = orchestrator.run("hello")

    gemini.generate.assert_called_once_with("hello")
    openai.generate.assert_called_once_with("hello")
    assert report.prompt == "hello"
    assert len(report.results) == 2


def test_successful_result_captures_provider_response_and_latency() -> None:
    orchestrator = Orchestrator({"gemini": make_client(response="42")})
    report = orchestrator.run("what is the answer?")

    result = report.results[0]
    assert result.provider == "gemini"
    assert result.response == "42"
    assert result.succeeded is True
    assert result.error is None
    assert result.latency_seconds >= 0


def test_one_client_failing_does_not_stop_the_others() -> None:
    failing = make_client(error=RuntimeError("quota exceeded"))
    healthy = make_client(response="fine")

    orchestrator = Orchestrator({"broken": failing, "healthy": healthy})
    report = orchestrator.run("ping")

    broken_result = next(r for r in report.results if r.provider == "broken")
    healthy_result = next(r for r in report.results if r.provider == "healthy")

    assert broken_result.succeeded is False
    assert broken_result.response is None
    assert broken_result.error == "quota exceeded"

    assert healthy_result.succeeded is True
    assert healthy_result.response == "fine"


def test_fastest_ignores_failed_results() -> None:
    orchestrator = Orchestrator(
        {
            "broken": make_client(error=RuntimeError("nope")),
            "healthy": make_client(response="ok"),
        }
    )
    report = orchestrator.run("ping")

    fastest = report.fastest()
    assert fastest is not None
    assert fastest.provider == "healthy"


def test_fastest_returns_none_when_everything_failed() -> None:
    orchestrator = Orchestrator({"broken": make_client(error=RuntimeError("nope"))})
    report = orchestrator.run("ping")

    assert report.fastest() is None


def test_run_raises_without_any_configured_clients() -> None:
    orchestrator = Orchestrator({})
    with pytest.raises(ValueError):
        orchestrator.run("ping")


def test_results_preserve_insertion_order() -> None:
    orchestrator = Orchestrator(
        {
            "openai": make_client(response="a"),
            "gemini": make_client(response="b"),
        }
    )
    report = orchestrator.run("ping")

    assert [r.provider for r in report.results] == ["openai", "gemini"]
