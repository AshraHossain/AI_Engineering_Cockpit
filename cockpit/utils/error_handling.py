"""Reusable exception types and a retry/backoff decorator for transient errors.

Intended for wrapping calls to flaky external services (LLM APIs, HTTP
retrieval, etc.) without pulling in a third-party retry library.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
from typing import TypeVar

from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)

_T = TypeVar("_T")


class CockpitError(Exception):
    """Base exception for all AI Engineering Cockpit errors."""


class TransientError(CockpitError):
    """A retryable error, e.g. a timeout or rate limit from an external API."""


class ConfigurationError(CockpitError):
    """Raised when the platform is misconfigured (missing keys, bad flags, etc.)."""


def retry_with_backoff(
    max_attempts: int = 3,
    initial_delay_seconds: float = 1.0,
    backoff_multiplier: float = 2.0,
    retry_on: tuple[type[BaseException], ...] = (TransientError,),
) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """Decorator that retries a function with exponential backoff.

    Args:
        max_attempts: Total number of attempts, including the first call.
            Must be at least 1.
        initial_delay_seconds: Delay before the first retry. Doubled (times
            ``backoff_multiplier``) after each subsequent failure.
        backoff_multiplier: Multiplier applied to the delay after each
            failed attempt.
        retry_on: Tuple of exception types that should trigger a retry. Any
            other exception propagates immediately.

    Returns:
        A decorator that wraps the target function with retry behavior.

    Raises:
        ValueError: If ``max_attempts`` is less than 1.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1.")

    def decorator(func: Callable[..., _T]) -> Callable[..., _T]:
        @functools.wraps(func)
        def wrapper(*args: object, **kwargs: object) -> _T:
            delay = initial_delay_seconds
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retry_on as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        _logger.error(
                            "%s failed after %d attempt(s): %s", func.__name__, attempt, exc
                        )
                        raise
                    _logger.warning(
                        "%s failed (attempt %d/%d): %s. Retrying in %.1fs.",
                        func.__name__,
                        attempt,
                        max_attempts,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    delay *= backoff_multiplier
            # Unreachable: the loop either returns or raises on the last attempt.
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
