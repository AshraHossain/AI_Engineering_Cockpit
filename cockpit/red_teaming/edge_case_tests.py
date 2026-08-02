"""Edge-case and boundary-condition testing for model-facing code (Tier 2 — skeleton).

Disabled by default; gated by ``feature_flags.is_enabled("red_teaming")``.
Every function is a typed skeleton pending Tier 2 implementation. See
``docs/ARCHITECTURE.md`` for the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ModelCallable(Protocol):
    """Protocol for a callable that sends a prompt to a model and returns text."""

    def __call__(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class EdgeCase:
    """A single edge-case input to test.

    Attributes:
        case_id: Unique identifier for the case.
        description: Human-readable description of what is being tested
            (e.g. "empty prompt", "extremely long input", "unicode/emoji
            heavy input", "non-English input").
        input_value: The actual input to send.
    """

    case_id: str
    description: str
    input_value: str


def generate_edge_cases() -> list[EdgeCase]:
    """Generate a standard set of boundary-condition edge cases.

    Returns:
        A list of :class:`EdgeCase` instances covering common boundary
        conditions (empty input, max-length input, malformed encoding,
        unusual whitespace, etc.).

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")


def run_edge_case_suite(cases: list[EdgeCase], model_call: ModelCallable) -> dict[str, bool]:
    """Run edge cases against a model and record pass/fail per case.

    A case "passes" if the model handles it without raising, timing out,
    or returning an empty/degenerate response.

    Args:
        cases: Edge cases to execute.
        model_call: Callable that sends an input to the target model and
            returns its response text.

    Returns:
        A mapping of ``case_id`` to pass/fail boolean.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")
