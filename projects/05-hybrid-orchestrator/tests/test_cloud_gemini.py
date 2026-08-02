"""Tests for the cloud Gemini wrapper used as the hybrid orchestrator's fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from cloud_gemini import CloudGeminiClient, CloudGeminiError


def test_generate_returns_stripped_text() -> None:
    sdk_model = MagicMock()
    sdk_model.generate_content.return_value = MagicMock(text="  hello  ")

    client = CloudGeminiClient(client=sdk_model)
    assert client.generate("hi") == "hello"


def test_generate_rejects_empty_prompt() -> None:
    client = CloudGeminiClient(client=MagicMock())
    with pytest.raises(CloudGeminiError):
        client.generate("")


def test_generate_wraps_sdk_errors() -> None:
    sdk_model = MagicMock()
    sdk_model.generate_content.side_effect = RuntimeError("boom")

    client = CloudGeminiClient(client=sdk_model)
    with pytest.raises(CloudGeminiError):
        client.generate("hi")


def test_requires_api_key_when_no_client_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(CloudGeminiError):
        CloudGeminiClient()
