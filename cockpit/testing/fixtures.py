"""Reusable mock API response fixtures and test data helpers.

These fixtures let projects and platform tests exercise LLM-calling code
paths without hitting real provider APIs (no network, no cost, deterministic
output).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MockChatResponse:
    """A minimal, provider-agnostic mock of a chat completion response.

    Attributes:
        text: The generated response text.
        model: Name of the model that "generated" the response.
        prompt_tokens: Simulated input token count.
        completion_tokens: Simulated output token count.
        finish_reason: Why generation stopped (e.g. "stop", "length").
    """

    text: str
    model: str = "mock-model-1"
    prompt_tokens: int = 10
    completion_tokens: int = 20
    finish_reason: str = "stop"

    @property
    def total_tokens(self) -> int:
        """Sum of prompt and completion tokens.

        Returns:
            The total simulated token count.
        """
        return self.prompt_tokens + self.completion_tokens


def make_mock_gemini_response(text: str = "Hello from mock Gemini!") -> dict[str, Any]:
    """Build a dict shaped like a Gemini ``generateContent`` response.

    Args:
        text: The text to embed as the model's reply.

    Returns:
        A dict mirroring the Gemini API's candidate/content structure.
    """
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": text}], "role": "model"},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 8,
            "candidatesTokenCount": 12,
            "totalTokenCount": 20,
        },
    }


def make_mock_openai_response(text: str = "Hello from mock OpenAI!") -> dict[str, Any]:
    """Build a dict shaped like an OpenAI chat completion response.

    Args:
        text: The text to embed as the assistant's reply.

    Returns:
        A dict mirroring the OpenAI chat completions response structure.
    """
    return {
        "id": "chatcmpl-mock123",
        "object": "chat.completion",
        "model": "gpt-mock",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 9, "completion_tokens": 11, "total_tokens": 20},
    }


def make_mock_anthropic_response(text: str = "Hello from mock Claude!") -> dict[str, Any]:
    """Build a dict shaped like an Anthropic Messages API response.

    Args:
        text: The text to embed as the assistant's reply.

    Returns:
        A dict mirroring the Anthropic Messages API response structure.
    """
    return {
        "id": "msg_mock123",
        "type": "message",
        "role": "assistant",
        "model": "claude-mock",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 7, "output_tokens": 13},
    }


def sample_documents() -> list[dict[str, str]]:
    """A small deterministic corpus for retrieval/RAG-style tests.

    Returns:
        A list of ``{"id": ..., "text": ...}`` document dicts.
    """
    return [
        {"id": "doc-1", "text": "The AI Engineering Cockpit is a modular AIDevOps platform."},
        {"id": "doc-2", "text": "Feature flags gate testing, security, and other frameworks."},
        {"id": "doc-3", "text": "UV manages Python dependencies across the repository."},
    ]


@dataclass
class FakeApiClient:
    """A stand-in API client that returns queued mock responses in order.

    Attributes:
        responses: Queue of responses to return, one per call to ``call``.
        calls: Record of every prompt passed to ``call``, for assertions.
    """

    responses: list[dict[str, Any]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def call(self, prompt: str) -> dict[str, Any]:
        """Simulate an API call, returning the next queued response.

        Args:
            prompt: The prompt that would have been sent to a real API.

        Returns:
            The next queued mock response dict.

        Raises:
            IndexError: If no responses remain in the queue.
        """
        self.calls.append(prompt)
        if not self.responses:
            raise IndexError("FakeApiClient has no queued responses left.")
        return self.responses.pop(0)
