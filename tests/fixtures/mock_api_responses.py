"""Reusable fake API response objects for tests across the repo.

Lightweight stand-ins for the shapes returned by the Gemini, OpenAI, and
Anthropic SDKs -- enough to exercise calling code without a network call or
a real API key. These are not full mocks of the SDKs; they only reproduce
the specific attributes most calling code reads (e.g. ``response.text`` for
Gemini, ``response.choices[0].message.content`` for OpenAI). Import from
here in project-level test suites rather than re-defining these shapes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MockGeminiResponse:
    """Stand-in for google.generativeai's GenerateContentResponse.

    Attributes:
        text: The generated text, mirroring the real response's `.text` property.
    """

    text: str


def make_gemini_response(text: str = "This is a mock Gemini response.") -> MockGeminiResponse:
    """Build a mock Gemini response with the given text.

    Args:
        text: The response text to embed.

    Returns:
        A MockGeminiResponse exposing `.text`.
    """
    return MockGeminiResponse(text=text)


@dataclass
class _MockOpenAIMessage:
    content: str
    role: str = "assistant"


@dataclass
class _MockOpenAIChoice:
    message: _MockOpenAIMessage
    finish_reason: str = "stop"
    index: int = 0


@dataclass
class MockOpenAIResponse:
    """Stand-in for openai's ChatCompletion response.

    Access the generated text via ``response.choices[0].message.content``,
    matching the real SDK's shape.

    Attributes:
        choices: List of completion choices (mock only ever returns one).
        model: Model name echoed back, as the real API does.
        id: A fake completion id.
    """

    choices: list[_MockOpenAIChoice]
    model: str = "gpt-4o-mini"
    id: str = "chatcmpl-mock"


def make_openai_response(content: str = "This is a mock OpenAI response.") -> MockOpenAIResponse:
    """Build a mock OpenAI chat completion response with the given content.

    Args:
        content: The assistant message text to embed.

    Returns:
        A MockOpenAIResponse exposing `.choices[0].message.content`.
    """
    return MockOpenAIResponse(
        choices=[_MockOpenAIChoice(message=_MockOpenAIMessage(content=content))]
    )


@dataclass
class _MockAnthropicBlock:
    text: str
    type: str = "text"


@dataclass
class MockAnthropicResponse:
    """Stand-in for anthropic's Messages API response.

    Access the generated text via ``response.content[0].text``, matching
    the real SDK's shape.

    Attributes:
        content: List of content blocks (mock only ever returns one text block).
        model: Model name echoed back, as the real API does.
        id: A fake message id.
    """

    content: list[_MockAnthropicBlock]
    model: str = "claude-sonnet"
    id: str = "msg_mock"


def make_anthropic_response(text: str = "This is a mock Claude response.") -> MockAnthropicResponse:
    """Build a mock Anthropic message response with the given text.

    Args:
        text: The response text to embed.

    Returns:
        A MockAnthropicResponse exposing `.content[0].text`.
    """
    return MockAnthropicResponse(content=[_MockAnthropicBlock(text=text)])


def make_streaming_chunks(full_text: str = "This is a streamed mock response.") -> list[str]:
    """Split full_text into word-level chunks, mimicking a streaming response.

    Args:
        full_text: The complete text to split into chunks.

    Returns:
        A list of string chunks that, joined, reconstruct full_text with a
        single trailing space after each word.
    """
    return [word + " " for word in full_text.split()]
