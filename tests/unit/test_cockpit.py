"""Unit tests for cockpit/config/{feature_flags,use_cases,settings}.py.

These three modules are the only cockpit/* modules confirmed to exist at
the time this suite was written. Other cockpit/* frameworks (testing,
evaluation, red_teaming, security, monitoring, governance) are covered by
their own implementer's tests, not here -- see tests/e2e and
tests/integration for structural-only smoke checks of the rest of the repo.
"""

from __future__ import annotations

import pytest

from cockpit.config.feature_flags import FRAMEWORKS_ENABLED, is_enabled
from cockpit.config.settings import get_settings
from cockpit.config.use_cases import DEFAULT_USE_CASE, USE_CASES, get_use_case_flags


class TestFeatureFlags:
    """Tests for cockpit/config/feature_flags.py."""

    def test_testing_enabled_by_default(self) -> None:
        """Tier 1 ships the testing framework enabled."""
        assert is_enabled("testing") is True

    def test_every_framework_is_enabled(self) -> None:
        """All three tiers are implemented, so nothing ships switched off.

        Was previously asserting governance is disabled -- that pinned a
        Tier 1 state of the world, not an intended property.
        """
        assert all(FRAMEWORKS_ENABLED.values())

    def test_unknown_framework_raises_key_error(self) -> None:
        """Looking up an unregistered framework name is a programming error, not a silent False."""
        with pytest.raises(KeyError):
            is_enabled("nonexistent")

    def test_frameworks_enabled_has_expected_keys(self) -> None:
        """FRAMEWORKS_ENABLED covers exactly the six known frameworks."""
        assert set(FRAMEWORKS_ENABLED) == {
            "testing",
            "evaluation",
            "red_teaming",
            "security",
            "monitoring",
            "governance",
        }


class TestUseCases:
    """Tests for cockpit/config/use_cases.py."""

    def test_get_use_case_flags_enterprise_shape(self) -> None:
        """The enterprise profile enables every framework."""
        flags = get_use_case_flags("enterprise")
        assert flags == {
            "testing": True,
            "evaluation": True,
            "red_teaming": True,
            "security": True,
            "monitoring": True,
            "governance": True,
        }

    @pytest.mark.parametrize("use_case", list(USE_CASES))
    def test_every_use_case_profile_matches_framework_flag_keys(self, use_case: str) -> None:
        """Every use-case profile must define exactly the known framework flags."""
        flags = get_use_case_flags(use_case)
        assert set(flags) == set(FRAMEWORKS_ENABLED)

    def test_unknown_use_case_raises_key_error(self) -> None:
        """Looking up an unregistered use-case profile fails loudly."""
        with pytest.raises(KeyError):
            get_use_case_flags("nonexistent")

    def test_default_use_case_is_a_valid_profile(self) -> None:
        """DEFAULT_USE_CASE must itself be a key in USE_CASES."""
        assert DEFAULT_USE_CASE in USE_CASES


class TestSettings:
    """Tests for cockpit/config/settings.py."""

    def test_get_settings_reads_api_keys_from_env(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_settings() picks up API keys set in the environment."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

        settings = get_settings()

        assert settings.gemini_api_key == "test-gemini-key"  # pragma: allowlist secret
        assert settings.openai_api_key == "test-openai-key"  # pragma: allowlist secret
        assert settings.anthropic_api_key == "test-anthropic-key"  # pragma: allowlist secret

    def test_get_settings_missing_api_keys_are_none(self, clean_env: None) -> None:
        """Unset API keys resolve to None (not an empty string) so callers can `if settings.x:`."""
        settings = get_settings()

        assert settings.gemini_api_key is None
        assert settings.openai_api_key is None
        assert settings.anthropic_api_key is None

    def test_get_settings_defaults(self, clean_env: None) -> None:
        """With no environment overrides, settings fall back to their documented defaults."""
        settings = get_settings()

        assert settings.ollama_host == "http://localhost:11434"
        assert settings.use_case == DEFAULT_USE_CASE
        assert settings.log_level == "INFO"

    def test_get_settings_reads_overrides_from_env(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Environment variables override every Settings default."""
        monkeypatch.setenv("OLLAMA_HOST", "http://example.local:9999")
        monkeypatch.setenv("COCKPIT_USE_CASE", "startup")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")

        settings = get_settings()

        assert settings.ollama_host == "http://example.local:9999"
        assert settings.use_case == "startup"
        assert settings.log_level == "DEBUG"

    def test_settings_is_frozen(self, clean_env: None) -> None:
        """Settings is a frozen dataclass; attributes cannot be reassigned after creation."""
        settings = get_settings()
        with pytest.raises(AttributeError):
            settings.log_level = "ERROR"  # type: ignore[misc]
