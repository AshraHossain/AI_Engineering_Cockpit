"""
Per-key token-bucket rate limiting, stdlib only.

A single misbehaving or compromised source (a SIEM stuck in a retry loop, a
webhook someone is hammering) shouldn't be able to starve the agent's
capacity for every other source. Token buckets allow bursts up to
`capacity` while capping the sustained rate to `refill_per_s` -- unlike a
fixed window counter, a burst right at a window boundary doesn't let twice
the intended rate through.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


@dataclass
class RateLimiter:
    """Tracks one token bucket per key (e.g. `Event.source`, a user ID).

    Usage:
        limiter = RateLimiter(capacity=10, refill_per_s=2.0)
        if limiter.allow("edr"):
            ...  # process the event
        else:
            ...  # drop or defer; "edr" is over its budget right now
    """

    capacity: float = 10.0
    refill_per_s: float = 2.0
    _buckets: dict[str, _Bucket] = field(default_factory=dict, init=False)

    def allow(self, key: str, cost: float = 1.0) -> bool:
        """Try to spend `cost` tokens from `key`'s bucket. True if it had enough."""
        now = time.monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self.capacity, last_refill=now)
            self._buckets[key] = bucket
        else:
            elapsed = now - bucket.last_refill
            bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_per_s)
            bucket.last_refill = now

        if bucket.tokens >= cost:
            bucket.tokens -= cost
            return True
        return False

    def tokens_remaining(self, key: str) -> float:
        """Current token count for `key`, without spending any (for /readyz-style introspection)."""
        bucket = self._buckets.get(key)
        if bucket is None:
            return self.capacity
        elapsed = time.monotonic() - bucket.last_refill
        return min(self.capacity, bucket.tokens + elapsed * self.refill_per_s)
