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
