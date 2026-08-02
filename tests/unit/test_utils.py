"""Unit tests for cockpit/utils/{error_handling,rate_limiting,helpers,logging_config}.py."""

from __future__ import annotations

import logging

import pytest

from cockpit.utils.error_handling import (
    CockpitError,
    ConfigurationError,
    TransientError,
    retry_with_backoff,
)
from cockpit.utils.helpers import env_bool, env_int, truncate
from cockpit.utils.logging_config import get_logger
from cockpit.utils.rate_limiting import RateLimiter


class TestErrorHandling:
    def test_transient_and_configuration_errors_are_cockpit_errors(self) -> None:
        assert issubclass(TransientError, CockpitError)
        assert issubclass(ConfigurationError, CockpitError)

    def test_retry_with_backoff_rejects_non_positive_max_attempts(self) -> None:
        with pytest.raises(ValueError):
            retry_with_backoff(max_attempts=0)

    def test_retry_with_backoff_succeeds_after_transient_failures(self) -> None:
        calls = {"count": 0}

        @retry_with_backoff(max_attempts=3, initial_delay_seconds=0.0)
        def flaky() -> str:
            calls["count"] += 1
            if calls["count"] < 3:
                raise TransientError("temporary")
            return "ok"

        assert flaky() == "ok"
        assert calls["count"] == 3

    def test_retry_with_backoff_raises_after_exhausting_attempts(self) -> None:
        @retry_with_backoff(max_attempts=2, initial_delay_seconds=0.0)
        def always_fails() -> None:
            raise TransientError("permanent")

        with pytest.raises(TransientError):
            always_fails()

    def test_retry_with_backoff_does_not_retry_unlisted_exceptions(self) -> None:
        calls = {"count": 0}

        @retry_with_backoff(max_attempts=3, initial_delay_seconds=0.0)
        def raises_value_error() -> None:
            calls["count"] += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            raises_value_error()
        assert calls["count"] == 1


class TestRateLimiter:
    def test_rejects_non_positive_capacity(self) -> None:
        with pytest.raises(ValueError):
            RateLimiter(capacity=0, refill_rate_per_second=1.0)

    def test_rejects_non_positive_refill_rate(self) -> None:
        with pytest.raises(ValueError):
            RateLimiter(capacity=5, refill_rate_per_second=0.0)

    def test_try_acquire_within_capacity_succeeds(self) -> None:
        limiter = RateLimiter(capacity=2, refill_rate_per_second=1.0)
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True

    def test_try_acquire_beyond_capacity_fails_without_blocking(self) -> None:
        limiter = RateLimiter(capacity=1, refill_rate_per_second=0.001)
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False

    def test_try_acquire_rejects_non_positive_token_request(self) -> None:
        limiter = RateLimiter(capacity=1, refill_rate_per_second=1.0)
        with pytest.raises(ValueError):
            limiter.try_acquire(tokens=0)

    def test_acquire_times_out_when_tokens_unavailable(self) -> None:
        limiter = RateLimiter(capacity=1, refill_rate_per_second=0.001)
        assert limiter.try_acquire() is True
        assert limiter.acquire(timeout_seconds=0.05) is False


class TestHelpers:
    @pytest.mark.parametrize("value", ["1", "true", "Yes", "ON"])
    def test_env_bool_truthy_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("COCKPIT_TEST_FLAG", value)
        assert env_bool("COCKPIT_TEST_FLAG") is True

    @pytest.mark.parametrize("value", ["0", "false", "No", "OFF"])
    def test_env_bool_falsy_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("COCKPIT_TEST_FLAG", value)
        assert env_bool("COCKPIT_TEST_FLAG") is False

    def test_env_bool_unset_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("COCKPIT_TEST_FLAG", raising=False)
        assert env_bool("COCKPIT_TEST_FLAG", default=True) is True

    def test_env_bool_invalid_value_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COCKPIT_TEST_FLAG", "maybe")
        with pytest.raises(ValueError):
            env_bool("COCKPIT_TEST_FLAG")

    def test_env_int_parses_valid_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COCKPIT_TEST_INT", "42")
        assert env_int("COCKPIT_TEST_INT", default=0) == 42

    def test_env_int_falls_back_on_invalid_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COCKPIT_TEST_INT", "not-a-number")
        assert env_int("COCKPIT_TEST_INT", default=7) == 7

    def test_env_int_falls_back_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("COCKPIT_TEST_INT", raising=False)
        assert env_int("COCKPIT_TEST_INT", default=7) == 7

    def test_truncate_leaves_short_text_unchanged(self) -> None:
        assert truncate("hello", max_length=10) == "hello"

    def test_truncate_cuts_and_appends_suffix(self) -> None:
        assert truncate("hello world", max_length=8) == "hello..."

    def test_truncate_rejects_negative_max_length(self) -> None:
        with pytest.raises(ValueError):
            truncate("hello", max_length=-1)


class TestLoggingConfig:
    def test_get_logger_rejects_empty_name(self) -> None:
        with pytest.raises(ValueError):
            get_logger("")

    def test_get_logger_returns_a_logger(self) -> None:
        logger = get_logger("cockpit.tests.example")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "cockpit.tests.example"

    def test_get_logger_does_not_attach_duplicate_handlers(self) -> None:
        first = get_logger("cockpit.tests.dedupe")
        second = get_logger("cockpit.tests.dedupe")
        assert first is second
        assert len(first.handlers) == 1
