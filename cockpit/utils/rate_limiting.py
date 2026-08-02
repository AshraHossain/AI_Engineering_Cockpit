"""A simple, dependency-free rate limiter for throttling outbound API calls.

Implements a token-bucket algorithm: tokens refill continuously at a fixed
rate up to a capacity, and each call consumes one token. Thread-safety is
provided via a ``threading.Lock`` since API clients are often called from
multiple threads (e.g. a thread pool fanning out requests).
"""

from __future__ import annotations

import threading
import time

from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)


class RateLimiter:
    """Token-bucket rate limiter.

    Attributes:
        capacity: Maximum number of tokens the bucket can hold.
        refill_rate_per_second: Tokens added back per second.
    """

    def __init__(self, capacity: int, refill_rate_per_second: float) -> None:
        """Initialize the rate limiter.

        Args:
            capacity: Maximum burst size (max tokens held at once). Must be
                positive.
            refill_rate_per_second: Sustained rate at which tokens refill.
                Must be positive.

        Raises:
            ValueError: If ``capacity`` or ``refill_rate_per_second`` is not
                positive.
        """
        if capacity <= 0:
            raise ValueError("capacity must be positive.")
        if refill_rate_per_second <= 0:
            raise ValueError("refill_rate_per_second must be positive.")

        self.capacity = capacity
        self.refill_rate_per_second = refill_rate_per_second
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        """Add tokens accrued since the last refill, capped at capacity."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate_per_second)
        self._last_refill = now

    def try_acquire(self, tokens: int = 1) -> bool:
        """Attempt to consume tokens without blocking.

        Args:
            tokens: Number of tokens to consume. Must be positive.

        Returns:
            True if enough tokens were available and consumed, False
            otherwise (caller should back off or drop the request).

        Raises:
            ValueError: If ``tokens`` is not positive.
        """
        if tokens <= 0:
            raise ValueError("tokens must be positive.")

        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def acquire(self, tokens: int = 1, timeout_seconds: float | None = None) -> bool:
        """Block until tokens are available (or a timeout elapses).

        Args:
            tokens: Number of tokens to consume. Must be positive.
            timeout_seconds: Maximum time to wait. ``None`` waits
                indefinitely.

        Returns:
            True if tokens were acquired, False if the timeout elapsed
            first.

        Raises:
            ValueError: If ``tokens`` is not positive.
        """
        start = time.monotonic()
        poll_interval = max(0.01, 1.0 / self.refill_rate_per_second)
        while True:
            if self.try_acquire(tokens):
                return True
            elapsed = time.monotonic() - start
            if timeout_seconds is not None and elapsed >= timeout_seconds:
                _logger.debug("Rate limiter timed out waiting for %d token(s).", tokens)
                return False
            # Never oversleep past the caller's timeout budget, even when the
            # refill rate is slow enough to make poll_interval very large.
            sleep_for = poll_interval
            if timeout_seconds is not None:
                sleep_for = min(sleep_for, max(timeout_seconds - elapsed, 0.0))
            time.sleep(sleep_for)
