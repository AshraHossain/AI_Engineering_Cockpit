"""Behavioural tests for the token-bucket rate limiter."""

from __future__ import annotations

import time

from rate_limiter import RateLimiter


def test_allows_up_to_capacity_then_denies():
    limiter = RateLimiter(capacity=3, refill_per_s=0.0)
    assert limiter.allow("a") is True
    assert limiter.allow("a") is True
    assert limiter.allow("a") is True
    assert limiter.allow("a") is False


def test_keys_are_independent():
    limiter = RateLimiter(capacity=1, refill_per_s=0.0)
    assert limiter.allow("a") is True
    assert limiter.allow("b") is True  # separate bucket, unaffected by "a"
    assert limiter.allow("a") is False
    assert limiter.allow("b") is False


def test_refills_over_time():
    limiter = RateLimiter(capacity=1, refill_per_s=100.0)  # fast refill for a quick test
    assert limiter.allow("a") is True
    assert limiter.allow("a") is False
    time.sleep(0.02)  # >= 1 token at 100/s
    assert limiter.allow("a") is True


def test_never_exceeds_capacity_even_after_long_idle():
    limiter = RateLimiter(capacity=2, refill_per_s=1000.0)
    limiter.allow("a")  # touch the bucket, then let it "idle" past capacity
    time.sleep(0.05)
    assert limiter.tokens_remaining("a") <= 2


def test_tokens_remaining_does_not_spend():
    limiter = RateLimiter(capacity=2, refill_per_s=0.0)
    before = limiter.tokens_remaining("a")
    assert limiter.tokens_remaining("a") == before
    assert limiter.allow("a") is True  # still had full capacity to spend


def test_cost_can_exceed_one():
    limiter = RateLimiter(capacity=5, refill_per_s=0.0)
    assert limiter.allow("a", cost=3) is True
    assert limiter.allow("a", cost=3) is False  # only 2 left
    assert limiter.allow("a", cost=2) is True
