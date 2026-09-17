"""Enable/disable switches for the cockpit's optional frameworks.

All six frameworks ship enabled. The switches exist so a deployment can
trim what it runs, not because anything is unfinished. Frameworks check
``is_enabled()`` at their entry points so they degrade to a no-op when
disabled rather than erroring.
"""

from __future__ import annotations

FRAMEWORKS_ENABLED: dict[str, bool] = {
    "testing": True,
    "evaluation": True,
    "red_teaming": True,
    "security": True,
    "monitoring": True,
    "governance": True,
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
