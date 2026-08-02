"""Pre-configured framework profiles for common deployment contexts."""

from __future__ import annotations

USE_CASES: dict[str, dict[str, bool]] = {
    "startup": {
        "testing": True,
        "evaluation": False,
        "red_teaming": False,
        "security": False,
        "monitoring": False,
        "governance": False,
    },
    "enterprise": {
        "testing": True,
        "evaluation": True,
        "red_teaming": True,
        "security": True,
        "monitoring": True,
        "governance": True,
    },
    "research": {
        "testing": True,
        "evaluation": True,
        "red_teaming": False,
        "security": False,
        "monitoring": False,
        "governance": False,
    },
    "safety_focused": {
        "testing": True,
        "evaluation": True,
        "red_teaming": True,
        "security": True,
        "monitoring": True,
        "governance": False,
    },
}

DEFAULT_USE_CASE = "enterprise"


def get_use_case_flags(use_case: str) -> dict[str, bool]:
    """Look up the framework flag profile for a named use case.

    Args:
        use_case: One of ``USE_CASES`` keys (startup, enterprise, research, safety_focused).

    Returns:
        A dict of framework name to enabled/disabled.

    Raises:
        KeyError: If ``use_case`` is not a recognized profile.
    """
    return USE_CASES[use_case]
