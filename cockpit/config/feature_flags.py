"""Enable/disable switches for the cockpit's optional frameworks.

Tier 1 (MVP) ships testing and security live; evaluation, red-teaming,
monitoring, and governance are Tier 2/3 and default off. Frameworks should
check ``is_enabled()`` before doing real work so they degrade to a no-op
when disabled rather than erroring.
"""

from __future__ import annotations

FRAMEWORKS_ENABLED: dict[str, bool] = {
    "testing": True,
    "evaluation": True,
    "red_teaming": True,
    "security": True,
    "monitoring": True,
    "governance": False,  # Tier 3
}


def is_enabled(framework: str) -> bool:
    """Check whether a named framework is currently enabled.

    Args:
        framework: One of the keys in ``FRAMEWORKS_ENABLED``.

    Returns:
        True if the framework is enabled.

    Raises:
        KeyError: If ``framework`` is not a recognized framework name.
    """
    return FRAMEWORKS_ENABLED[framework]
