"""Tests for the hello-world Gemini example.

No network calls and no real API key are used anywhere in this module --
the ``google.generativeai`` SDK is stubbed out with ``unittest.mock`` so we
only exercise our own request-building and response-handling logic.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import main
import pytest


def test_get_api_key_returns_value_when_present() -> None:
    """A non-empty GEMINI_API_KEY is returned as-is."""
    assert main.get_api_key({"GEMINI_API_KEY": "test-key-123"}) == "test-key-123"  # pragma: allowlist secret


def test_get_api_key_strips_whitespace() -> None:
    """Surrounding whitespace on the key is stripped."""
    assert main.get_api_key({"GEMINI_API_KEY": "  test-key  "}) == "test-key"


@pytest.mark.parametrize("env", [{}, {"GEMINI_API_KEY": ""}, {"GEMINI_API_KEY": "   "}])
def test_get_api_key_raises_when_missing(env: dict[str, str]) -> None:
    """A missing, empty, or blank key raises MissingAPIKeyError."""
    with pytest.raises(main.MissingAPIKeyError):
        main.get_api_key(env)


def test_build_client_configures_sdk_and_returns_model() -> None:
    """build_client should configure the SDK and construct a model with the given name."""
    fake_genai = MagicMock()
    fake_model_instance = MagicMock()
    fake_genai.GenerativeModel.return_value = fake_model_instance

    with patch.dict("sys.modules", {"google.generativeai": fake_genai}):
        client = main.build_client("my-key", model_name="gemini-1.5-flash")

    fake_genai.configure.assert_called_once_with(api_key="my-key")  # pragma: allowlist secret
    fake_genai.GenerativeModel.assert_called_once_with("gemini-1.5-flash")
    assert client is fake_model_instance


def test_ask_gemini_returns_response_text() -> None:
    """ask_gemini extracts and returns the .text attribute from the SDK response."""
    fake_client = MagicMock()
    fake_client.generate_content.return_value = MagicMock(text="Hello from Gemini!")

    result = main.ask_gemini(fake_client, "say hi")

    fake_client.generate_content.assert_called_once_with("say hi")
    assert result == "Hello from Gemini!"


def test_ask_gemini_raises_on_empty_response_text() -> None:
    """An empty/None .text on the response is treated as a request error."""
    fake_client = MagicMock()
    fake_client.generate_content.return_value = MagicMock(text="")

    with pytest.raises(main.GeminiRequestError, match="empty response"):
        main.ask_gemini(fake_client, "say hi")


def test_ask_gemini_wraps_sdk_exceptions() -> None:
    """Exceptions raised by the SDK call are wrapped in GeminiRequestError."""
    fake_client = MagicMock()
    fake_client.generate_content.side_effect = RuntimeError("network exploded")

    with pytest.raises(main.GeminiRequestError, match="network exploded"):
        main.ask_gemini(fake_client, "say hi")


def test_run_happy_path_uses_env_key_and_returns_text() -> None:
    """run() wires get_api_key -> build_client -> ask_gemini together correctly."""
    fake_client = MagicMock()
    fake_client.generate_content.return_value = MagicMock(text="42")

    with patch.object(main, "build_client", return_value=fake_client) as mock_build:
        result = main.run("What is the answer?", env={"GEMINI_API_KEY": "abc"})

    mock_build.assert_called_once_with("abc")
    assert result == "42"


def test_run_propagates_missing_api_key_error() -> None:
    """run() propagates MissingAPIKeyError when no key is configured."""
    with pytest.raises(main.MissingAPIKeyError):
        main.run("hi", env={})


def test_main_returns_1_on_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns exit code 1 (not an exception) when the key is missing."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main, "load_dotenv", lambda: None)

    exit_code = main.main("hi")

    assert exit_code == 1


def test_main_returns_0_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns exit code 0 and logs the response on success."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(main, "load_dotenv", lambda: None)
    fake_client = MagicMock()
    fake_client.generate_content.return_value = MagicMock(text="hi there")
    monkeypatch.setattr(main, "build_client", lambda *a, **k: fake_client)

    exit_code = main.main("hi")

    assert exit_code == 0


def test_main_returns_1_on_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns exit code 1 when the Gemini call raises."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(main, "load_dotenv", lambda: None)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise main.GeminiRequestError("boom")

    monkeypatch.setattr(main, "build_client", _boom)

    exit_code = main.main("hi")

    assert exit_code == 1
