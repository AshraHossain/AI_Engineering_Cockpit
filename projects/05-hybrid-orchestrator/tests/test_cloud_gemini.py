"""Tests for the cloud Gemini wrapper used as the hybrid orchestrator's fallback.

The mock mirrors the ``google-genai`` client shape: a client object whose
``.models.generate_content(model=..., contents=...)`` does the work,
rather than the legacy per-model ``GenerativeModel`` object.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from cloud_gemini import DEFAULT_GEMINI_MODEL, CloudGeminiClient, CloudGeminiError


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


def test_generate_returns_stripped_text() -> None:
    sdk_client = make_genai_client(response=MagicMock(text="  hello  "))

    client = CloudGeminiClient(client=sdk_client)
    assert client.generate("hi") == "hello"
    sdk_client.models.generate_content.assert_called_once_with(
        model=DEFAULT_GEMINI_MODEL,
        contents="hi",
    )


def test_generate_passes_configured_model_id() -> None:
    """The model id travels per-call in the new SDK, not at construction."""
    sdk_client = make_genai_client(response=MagicMock(text="ok"))

    client = CloudGeminiClient(client=sdk_client, model="gemini-2.5-flash-lite")
    client.generate("hi")

    sdk_client.models.generate_content.assert_called_once_with(
        model="gemini-2.5-flash-lite",
        contents="hi",
    )


def test_default_model_is_not_a_retired_1_5_id() -> None:
    """``gemini-1.5-*`` ids now 404 -- guard against regressing to one."""
    assert not DEFAULT_GEMINI_MODEL.startswith("gemini-1.5")


def test_generate_rejects_empty_prompt() -> None:
    client = CloudGeminiClient(client=make_genai_client())
    with pytest.raises(CloudGeminiError):
        client.generate("")


def test_generate_wraps_sdk_errors() -> None:
    sdk_client = make_genai_client(error=RuntimeError("boom"))

    client = CloudGeminiClient(client=sdk_client)
    with pytest.raises(CloudGeminiError):
        client.generate("hi")


def test_generate_raises_when_response_has_no_text() -> None:
    sdk_client = make_genai_client(response=MagicMock(text=None))

    client = CloudGeminiClient(client=sdk_client)
    with pytest.raises(CloudGeminiError):
        client.generate("hi")


def test_requires_api_key_when_no_client_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(CloudGeminiError):
        CloudGeminiClient()
