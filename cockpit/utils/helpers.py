"""Small generic helpers shared across the cockpit platform."""

from __future__ import annotations

import os


def env_bool(name: str, default: bool = False) -> bool:
    """Coerce an environment variable to a boolean.

    Recognizes ``"1"``, ``"true"``, ``"yes"``, ``"on"`` (case-insensitive)
    as True, and ``"0"``, ``"false"``, ``"no"``, ``"off"`` as False.

    Args:
        name: Environment variable name to read.
        default: Value to return if the variable is unset or empty.

    Returns:
        The coerced boolean value.

    Raises:
        ValueError: If the variable is set but not a recognized boolean
            string.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Environment variable {name}={raw!r} is not a recognized boolean value.")


def env_int(name: str, default: int) -> int:
    """Coerce an environment variable to an int, falling back on failure.

    Args:
        name: Environment variable name to read.
        default: Value to return if the variable is unset, empty, or not a
            valid integer.

    Returns:
        The parsed integer, or ``default``.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def truncate(text: str, max_length: int, suffix: str = "...") -> str:
    """Truncate text to a maximum length, appending a suffix if cut.

    Useful for logging long prompts/responses without flooding output.

    Args:
        text: The text to truncate.
        max_length: Maximum length of the returned string, including the
            suffix. Must be non-negative.
        suffix: Marker appended when truncation occurs.

    Returns:
        ``text`` unchanged if it already fits, otherwise a truncated copy
        ending in ``suffix``.

    Raises:
        ValueError: If ``max_length`` is negative.
    """
    if max_length < 0:
        raise ValueError("max_length must be non-negative.")
    if len(text) <= max_length:
        return text
    if max_length <= len(suffix):
        return suffix[:max_length]
    return text[: max_length - len(suffix)] + suffix
