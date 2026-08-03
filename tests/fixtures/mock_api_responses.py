"""Reusable fake API response objects for tests across the repo.

Lightweight stand-ins for the shapes returned by the Gemini, OpenAI, and
Anthropic SDKs -- enough to exercise calling code without a network call or
a real API key. These are not full mocks of the SDKs; they only reproduce
the specific attributes most calling code reads (e.g. ``response.text`` for
Gemini, ``response.choices[0].message.content`` for OpenAI). Import from
here in project-level test suites rather than re-defining these shapes.

The Gemini mocks track the ``google-genai`` SDK (``from google import genai``;
``client.models.generate_content(...)``), not the end-of-life
``google-generativeai`` package.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MockGeminiUsageMetadata:
    """Stand-in for google-genai's GenerateContentResponseUsageMetadata.

    Attributes:
        prompt_token_count: Tokens consumed by the request.
        candidates_token_count: Tokens produced in the response.
        total_token_count: Sum of the two above.
    """

    prompt_token_count: int = 0
    candidates_token_count: int = 0
    total_token_count: int = 0


@dataclass
class MockGeminiResponse:
    """Stand-in for google-genai's GenerateContentResponse.

    Matches the shape returned by
    ``client.models.generate_content(model=..., contents=...)``.

    Attributes:
        text: The generated text, mirroring the real response's `.text` property.
        usage_metadata: Token accounting, mirroring `.usage_metadata`.
    """

    text: str
    usage_metadata: MockGeminiUsageMetadata = field(default_factory=MockGeminiUsageMetadata)


def make_gemini_response(
    text: str = "This is a mock Gemini response.",
    prompt_tokens: int = 12,
    output_tokens: int = 8,
) -> MockGeminiResponse:
    """Build a mock Gemini response with the given text and token counts.

    Args:
        text: The response text to embed.
        prompt_tokens: Value for `usage_metadata.prompt_token_count`.
        output_tokens: Value for `usage_metadata.candidates_token_count`.

    Returns:
        A MockGeminiResponse exposing `.text` and `.usage_metadata`.
    """
    return MockGeminiResponse(
        text=text,
        usage_metadata=MockGeminiUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
            total_token_count=prompt_tokens + output_tokens,
        ),
    )


@dataclass
class MockGeminiStreamChunk:
    """One chunk yielded by ``client.models.generate_content_stream(...)``.

    Attributes:
        text: The chunk's text. `None` for chunks that carry no text part —
            real streams do emit these, and calling code must tolerate them.
    """

    text: str | None


def make_gemini_stream_chunks(
    full_text: str = "This is a streamed mock response.",
    include_empty_chunk: bool = True,
) -> list[MockGeminiStreamChunk]:
    """Split full_text into chunk objects shaped like a Gemini stream.

    Args:
        full_text: The complete text to split into word-level chunks.
        include_empty_chunk: If True, append a trailing chunk whose `.text` is
            None, so tests exercise the empty-chunk guard that real streams
            require.

    Returns:
        A list of MockGeminiStreamChunk, each exposing `.text`.
    """
    chunks = [MockGeminiStreamChunk(text=word + " ") for word in full_text.split()]
    if include_empty_chunk:
        chunks.append(MockGeminiStreamChunk(text=None))
    return chunks


@dataclass
class _MockGeminiEmbedding:
    values: list[float]


@dataclass
class MockGeminiEmbedResponse:
    """Stand-in for google-genai's EmbedContentResponse.

    Access the vector via ``response.embeddings[0].values``, matching the real
    SDK's shape.

    Attributes:
        embeddings: List of embedding objects (mock returns one per input).
    """

    embeddings: list[_MockGeminiEmbedding]


def make_gemini_embed_response(dimensions: int = 3072) -> MockGeminiEmbedResponse:
    """Build a mock Gemini embedding response.

    Args:
        dimensions: Length of the returned vector. Defaults to 3072, the
            output size of `gemini-embedding-001` (see models/registry.json).

    Returns:
        A MockGeminiEmbedResponse exposing `.embeddings[0].values`.
    """
    return MockGeminiEmbedResponse(embeddings=[_MockGeminiEmbedding(values=[0.1] * dimensions)])


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

    Provider-agnostic: yields plain strings, matching the ``Iterator[str]``
    contract in docs/recipes/streaming-response.md. For chunks shaped like a
    real Gemini stream (objects with a possibly-None ``.text``), use
    :func:`make_gemini_stream_chunks` instead.

    Args:
        full_text: The complete text to split into chunks.

    Returns:
        A list of string chunks that, joined, reconstruct full_text with a
        single trailing space after each word.
    """
    return [word + " " for word in full_text.split()]
