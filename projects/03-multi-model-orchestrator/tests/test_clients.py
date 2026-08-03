"""Tests for the thin Gemini/OpenAI SDK wrappers.

The underlying SDK objects are injected directly via each client's
``client=`` constructor argument, so no network calls or API keys are
involved.

The Gemini mock mirrors the ``google-genai`` client shape: a client
object whose ``.models.generate_content(model=..., contents=...)`` does
the work, rather than the legacy per-model ``GenerativeModel`` object.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from gemini_client import DEFAULT_GEMINI_MODEL, GeminiClient, GeminiClientError
from openai_client import OpenAIClient, OpenAIClientError


def make_genai_client(response: MagicMock | None = None, error: Exception | None = None):
    """Build a mock ``google.genai.Client`` for the non-streaming path.

    Args:
        response: Object returned by ``models.generate_content``.
        error: Exception ``models.generate_content`` should raise instead.

    Returns:
        A :class:`~unittest.mock.MagicMock` shaped like a genai client.
    """
    sdk_client = MagicMock()
    if error is not None:
        sdk_client.models.generate_content.side_effect = error
    else:
        sdk_client.models.generate_content.return_value = response
    return sdk_client


def test_gemini_generate_returns_stripped_text() -> None:
    sdk_client = make_genai_client(response=MagicMock(text="  hello there  "))

    client = GeminiClient(client=sdk_client)
    assert client.generate("hi") == "hello there"
    sdk_client.models.generate_content.assert_called_once_with(
        model=DEFAULT_GEMINI_MODEL,
        contents="hi",
    )


def test_gemini_generate_passes_configured_model_id() -> None:
    """The model id travels per-call in the new SDK, not at construction."""
    sdk_client = make_genai_client(response=MagicMock(text="ok"))

    client = GeminiClient(client=sdk_client, model="gemini-2.5-flash-lite")
    client.generate("hi")

    sdk_client.models.generate_content.assert_called_once_with(
        model="gemini-2.5-flash-lite",
        contents="hi",
    )


def test_gemini_default_model_is_not_a_retired_1_5_id() -> None:
    """``gemini-1.5-*`` ids now 404 -- guard against regressing to one."""
    assert not DEFAULT_GEMINI_MODEL.startswith("gemini-1.5")


def test_gemini_generate_rejects_empty_prompt() -> None:
    client = GeminiClient(client=make_genai_client())
    with pytest.raises(GeminiClientError):
        client.generate("   ")


def test_gemini_generate_wraps_sdk_errors() -> None:
    sdk_client = make_genai_client(error=RuntimeError("boom"))

    client = GeminiClient(client=sdk_client)
    with pytest.raises(GeminiClientError):
        client.generate("hi")


def test_gemini_generate_raises_when_response_has_no_text() -> None:
    sdk_client = make_genai_client(response=MagicMock(text=None))

    client = GeminiClient(client=sdk_client)
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
