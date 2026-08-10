"""Tests for {{PACKAGE_NAME}}.

No API key, no network, no sleeping. Every one of these runs in
milliseconds and gives the same answer every time, which is the only kind
of test people actually keep running.
"""

from __future__ import annotations

import pytest

from {{PACKAGE_NAME}} import main as m


class TestGetApiKey:
    def test_returns_the_key(self) -> None:
        assert m.get_api_key({"GEMINI_API_KEY": "abc123"}) == "abc123"  # pragma: allowlist secret

    def test_strips_whitespace(self) -> None:
        assert m.get_api_key({"GEMINI_API_KEY": "  abc  "}) == "abc"

    @pytest.mark.parametrize("env", [{}, {"GEMINI_API_KEY": ""}, {"GEMINI_API_KEY": "   "}])
    def test_missing_or_blank_raises(self, env: dict[str, str]) -> None:
        with pytest.raises(m.ConfigurationError):
            m.get_api_key(env)


class TestRun:
    def test_passes_the_prompt_through_and_returns_the_result(self) -> None:
        calls: list[str] = []

        def fake_generate(prompt: str) -> str:
            calls.append(prompt)
            return "generated"

        assert m.run("hello", fake_generate) == "generated"
        assert calls == ["hello"]

    @pytest.mark.parametrize("prompt", ["", "   ", "\n"])
    def test_empty_prompt_is_rejected_before_any_call(self, prompt: str) -> None:
        """Validation must happen before the expensive call, not after."""

        def fail_if_called(_prompt: str) -> str:  # pragma: no cover
            raise AssertionError("the provider should never have been called")

        with pytest.raises(ValueError):
            m.run(prompt, fail_if_called)


class TestCli:
    def test_dry_run_succeeds_without_any_credentials(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert m.main(["--dry-run", "hi"]) == 0
        assert "hi" in capsys.readouterr().out

    def test_missing_key_exits_1_rather_than_raising(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setattr(m, "load_dotenv", lambda: None)
        assert m.main(["hi"]) == 1

    def test_provider_failure_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(m, "load_dotenv", lambda: None)
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")  # pragma: allowlist secret

        def boom(*_args: object, **_kwargs: object) -> m.GenerateFn:
            raise m.ProviderError("upstream is down")

        monkeypatch.setattr(m, "build_generate_fn", boom)
        assert m.main(["hi"]) == 1
