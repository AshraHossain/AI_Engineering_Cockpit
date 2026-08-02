"""Prompt-injection attack simulation and resistance scoring (Tier 2 — skeleton).

Complements ``cockpit.security.input_security`` (which detects injection
attempts at request time) by proactively simulating attacks against a
target model to measure resistance. Disabled by default; gated by
``feature_flags.is_enabled("red_teaming")``. Every function is a typed
skeleton pending Tier 2 implementation. See ``docs/ARCHITECTURE.md`` for
the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ModelCallable(Protocol):
    """Protocol for a callable that sends a prompt to a model and returns text."""

    def __call__(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class InjectionAttempt:
    """A single simulated prompt-injection attempt.

    Attributes:
        technique: Name of the injection technique used (e.g.
            "instruction_override", "role_play_jailbreak").
        payload: The full adversarial prompt sent to the model.
    """

    technique: str
    payload: str


@dataclass(frozen=True)
class InjectionResistanceReport:
    """Summary of how a model resisted a batch of injection attempts.

    Attributes:
        total_attempts: Number of attempts run.
        successful_injections: Number of attempts that bypassed defenses.
        resistance_rate: Fraction of attempts successfully resisted, in
            [0.0, 1.0].
        failing_techniques: Techniques that succeeded against the model.
    """

    total_attempts: int
    successful_injections: int
    resistance_rate: float
    failing_techniques: list[str]


def generate_injection_attempts(count: int = 20) -> list[InjectionAttempt]:
    """Generate a batch of simulated prompt-injection attempts.

    Args:
        count: Number of attempts to generate.

    Returns:
        Generated :class:`InjectionAttempt` instances covering a mix of
        known techniques.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")


def evaluate_injection_resistance(
    attempts: list[InjectionAttempt], model_call: ModelCallable
) -> InjectionResistanceReport:
    """Run injection attempts against a model and score its resistance.

    Args:
        attempts: Attempts to execute against the target model.
        model_call: Callable that sends a prompt to the target model and
            returns its response text.

    Returns:
        An :class:`InjectionResistanceReport` summarizing the results.

    Raises:
        NotImplementedError: Tier 2 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 2 feature — see docs/ARCHITECTURE.md")
