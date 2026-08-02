"""Tests for the thin Gemini/OpenAI SDK wrappers.

The underlying SDK objects are injected directly via each client's
``client=`` constructor argument, so no network calls or API keys are
involved.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from gemini_client import GeminiClient, GeminiClientError
from openai_client import OpenAIClient, OpenAIClientError


def test_gemini_generate_returns_stripped_text() -> None:
    sdk_model = MagicMock()
    sdk_model.generate_content.return_value = MagicMock(text="  hello there  ")

    client = GeminiClient(client=sdk_model)
    assert client.generate("hi") == "hello there"
    sdk_model.generate_content.assert_called_once_with("hi")


def test_gemini_generate_rejects_empty_prompt() -> None:
    client = GeminiClient(client=MagicMock())
    with pytest.raises(GeminiClientError):
        client.generate("   ")


def test_gemini_generate_wraps_sdk_errors() -> None:
    sdk_model = MagicMock()
    sdk_model.generate_content.side_effect = RuntimeError("boom")

    client = GeminiClient(client=sdk_model)
    with pytest.raises(GeminiClientError):
        client.generate("hi")


def test_gemini_requires_api_key_when_no_client_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(GeminiClientError):
        GeminiClient()


def test_openai_generate_returns_stripped_text() -> None:
    sdk_client = MagicMock()
    sdk_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="  hi back  "))]
    )

    client = OpenAIClient(client=sdk_client)
    assert client.generate("hi") == "hi back"


def test_openai_generate_rejects_empty_prompt() -> None:
    client = OpenAIClient(client=MagicMock())
    with pytest.raises(OpenAIClientError):
        client.generate("")


def test_openai_generate_wraps_sdk_errors() -> None:
    sdk_client = MagicMock()
    sdk_client.chat.completions.create.side_effect = RuntimeError("boom")

    client = OpenAIClient(client=sdk_client)
    with pytest.raises(OpenAIClientError):
        client.generate("hi")


def test_openai_requires_api_key_when_no_client_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(OpenAIClientError):
        OpenAIClient()
