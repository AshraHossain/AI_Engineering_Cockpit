"""Example unit tests demonstrating how to test cockpit code with pytest.

Run with:
    uv run pytest cockpit/testing/unit_tests.py -v

These are illustrative examples (a starting pattern for project authors),
not an exhaustive test suite for the whole platform.
"""

from __future__ import annotations

import pytest

from cockpit.config.feature_flags import is_enabled
from cockpit.testing.fixtures import FakeApiClient, make_mock_gemini_response
from cockpit.utils.helpers import env_bool, truncate
from cockpit.utils.rate_limiting import RateLimiter


def test_is_enabled_returns_bool_for_known_framework() -> None:
    """`is_enabled` should return a plain bool for a registered framework."""
    result = is_enabled("testing")
    assert isinstance(result, bool)


def test_is_enabled_raises_for_unknown_framework() -> None:
    """`is_enabled` should raise KeyError for names outside the registry."""
    with pytest.raises(KeyError):
        is_enabled("not_a_real_framework")


def test_truncate_leaves_short_text_untouched() -> None:
    """Text shorter than max_length should pass through unchanged."""
    assert truncate("hello", 10) == "hello"


def test_truncate_cuts_long_text_with_suffix() -> None:
    """Text longer than max_length should be cut and end with the suffix."""
    result = truncate("hello world", 8, suffix="...")
    assert result == "hello..."
    assert len(result) == 8


def test_env_bool_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset environment variable should fall back to the default."""
    monkeypatch.delenv("COCKPIT_TEST_FLAG", raising=False)
    assert env_bool("COCKPIT_TEST_FLAG", default=True) is True


def test_env_bool_parses_true_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Common truthy strings should coerce to True."""
    monkeypatch.setenv("COCKPIT_TEST_FLAG", "yes")
    assert env_bool("COCKPIT_TEST_FLAG") is True


def test_fake_api_client_returns_queued_response() -> None:
    """FakeApiClient should return responses in FIFO order and log calls."""
    client = FakeApiClient(responses=[make_mock_gemini_response("hi there")])
    result = client.call("say hi")

    assert result["candidates"][0]["content"]["parts"][0]["text"] == "hi there"
    assert client.calls == ["say hi"]


def test_rate_limiter_allows_burst_up_to_capacity() -> None:
    """A fresh RateLimiter should allow exactly `capacity` immediate calls."""
    limiter = RateLimiter(capacity=3, refill_rate_per_second=1.0)

    results = [limiter.try_acquire() for _ in range(4)]

    assert results == [True, True, True, False]
