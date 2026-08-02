"""Adversarial test-case generation and execution (Tier 2 — skeleton).

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
class AdversarialCase:
    """A single adversarial test case.

    Attributes:
        case_id: Unique identifier for the case.
        prompt: The adversarial prompt to send.
        expected_behavior: Description of the safe/expected response.
    """

    case_id: str
    prompt: str
    expected_behavior: str


@dataclass(frozen=True)
class AdversarialResult:
    """Outcome of running a single adversarial case against a model.

    Attributes:
        case: The :class:`AdversarialCase` that was run.
        response: The model's actual response.
        passed: True if the response matched the expected safe behavior.
    """

    case: AdversarialCase
    response: str
    passed: bool


def generate_adversarial_cases(category: str, count: int = 10) -> list[AdversarialCase]:
    """Generate adversarial test cases for a given attack category.

    Args:
        category: The attack category to generate cases for (e.g.
            "jailbreak", "goal_hijacking").
        count: Number of cases to generate.

    Returns:
        Generated :class:`AdversarialCase` instances.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")


def run_adversarial_suite(
    cases: list[AdversarialCase], model_call: ModelCallable
) -> list[AdversarialResult]:
    """Run a suite of adversarial cases against a model and record outcomes.

    Args:
        cases: Adversarial cases to execute.
        model_call: Callable that sends a prompt to the target model and
            returns its response text.

    Returns:
        One :class:`AdversarialResult` per input case.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")
