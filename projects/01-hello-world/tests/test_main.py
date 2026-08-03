"""Tests for the hello-world Gemini example.

No network calls and no real API key are used anywhere in this module --
the ``google.genai`` SDK is stubbed out with ``unittest.mock`` so we only
exercise our own request-building and response-handling logic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import main
import pytest


def _fake_sdk() -> tuple[Any, dict[str, Any]]:
    """Build a stand-in for the ``google.genai`` SDK and its sys.modules patch.

    ``main.build_client`` does ``from google import genai``, so the stub has
    to be installed as the ``google`` package with a ``genai`` attribute.

    Returns:
        A ``(fake_genai, modules)`` pair, where ``fake_genai`` is the mock
        standing in for the ``google.genai`` module and ``modules`` is the
        mapping to hand to ``patch.dict("sys.modules", ...)``.
    """
    fake_genai = MagicMock()
    fake_google = MagicMock()
    fake_google.genai = fake_genai
    return fake_genai, {"google": fake_google, "google.genai": fake_genai}


def _fake_client(text: str | None = "ok") -> Any:
    """Build a ``genai.Client``-shaped mock whose response carries ``text``.

    Args:
        text: Value for ``response.text`` on the generated response.

    Returns:
        A mock exposing ``models.generate_content(model=..., contents=...)``.
    """
    client = MagicMock()
    client.models.generate_content.return_value = MagicMock(text=text)
    return client


def test_get_api_key_returns_value_when_present() -> None:
    """A non-empty GEMINI_API_KEY is returned as-is."""
    env = {"GEMINI_API_KEY": "test-key-123"}  # pragma: allowlist secret
    assert main.get_api_key(env) == "test-key-123"  # pragma: allowlist secret


def test_get_api_key_strips_whitespace() -> None:
    """Surrounding whitespace on the key is stripped."""
    assert main.get_api_key({"GEMINI_API_KEY": "  test-key  "}) == "test-key"


@pytest.mark.parametrize("env", [{}, {"GEMINI_API_KEY": ""}, {"GEMINI_API_KEY": "   "}])
def test_get_api_key_raises_when_missing(env: dict[str, str]) -> None:
    """A missing, empty, or blank key raises MissingAPIKeyError."""
    with pytest.raises(main.MissingAPIKeyError):
        main.get_api_key(env)


def test_build_client_constructs_client_with_api_key() -> None:
    """build_client should construct a genai.Client authenticated with the key."""
    fake_genai, modules = _fake_sdk()

    with patch.dict("sys.modules", modules):
        client = main.build_client("my-key")

    fake_genai.Client.assert_called_once_with(api_key="my-key")  # pragma: allowlist secret
    assert client is fake_genai.Client.return_value


def test_default_model_is_a_current_model_id() -> None:
    """Guard against regressing to a retired model id (the 1.5 family now 404s)."""
    assert main.DEFAULT_MODEL == "gemini-2.5-flash"


def test_ask_gemini_sends_prompt_and_model_and_returns_text() -> None:
    """ask_gemini passes model+contents through and returns response.text."""
    fake_client = _fake_client("Hello from Gemini!")

    result = main.ask_gemini(fake_client, "say hi")

    fake_client.models.generate_content.assert_called_once_with(
        model=main.DEFAULT_MODEL, contents="say hi"
    )
    assert result == "Hello from Gemini!"


def test_ask_gemini_honours_model_override() -> None:
    """An explicit model_name is forwarded to the SDK instead of the default."""
    fake_client = _fake_client("pro answer")

    result = main.ask_gemini(fake_client, "say hi", model_name="gemini-2.5-pro")

    fake_client.models.generate_content.assert_called_once_with(
        model="gemini-2.5-pro", contents="say hi"
    )
    assert result == "pro answer"


def test_ask_gemini_raises_on_empty_response_text() -> None:
    """An empty/None .text on the response is treated as a request error."""
    fake_client = _fake_client("")

    with pytest.raises(main.GeminiRequestError, match="empty response"):
        main.ask_gemini(fake_client, "say hi")


def test_ask_gemini_wraps_sdk_exceptions() -> None:
    """Exceptions raised by the SDK call are wrapped in GeminiRequestError."""
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = RuntimeError("network exploded")

    with pytest.raises(main.GeminiRequestError, match="network exploded"):
        main.ask_gemini(fake_client, "say hi")


def test_run_happy_path_uses_env_key_and_returns_text() -> None:
    """run() wires get_api_key -> build_client -> ask_gemini together correctly."""
    fake_client = _fake_client("42")

    with patch.object(main, "build_client", return_value=fake_client) as mock_build:
        result = main.run("What is the answer?", env={"GEMINI_API_KEY": "abc"})

    mock_build.assert_called_once_with("abc")
    fake_client.models.generate_content.assert_called_once_with(
        model=main.DEFAULT_MODEL, contents="What is the answer?"
    )
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
    fake_client = _fake_client("hi there")
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
