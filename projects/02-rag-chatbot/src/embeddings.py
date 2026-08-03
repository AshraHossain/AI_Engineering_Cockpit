"""Text embedding generation for the RAG demo.

Design tradeoff (documented here and in the project README): this module
calls the real Gemini embeddings endpoint (``gemini-embedding-001``) rather
than implementing a from-scratch local embedding (e.g. TF-IDF). That keeps
the demo genuinely representative of how RAG works in production --
retrieval quality depends on real semantic vectors, not just word overlap.
The cost is that ``embed_text``/``embed_texts`` need network access and an
API key at runtime. To keep this testable offline, every function that
calls the SDK is a thin, mockable wrapper, and the pure-math pieces
(``cosine_similarity``) have zero dependency on the network and are tested
directly.
"""

from __future__ import annotations

import math
from typing import Any

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 3072

# Gemini embeddings are task-asymmetric: a question and the passage that
# answers it are *not* the same kind of text, and telling the model which is
# which measurably improves retrieval. Verified live against the API on
# 2026-08-03 -- the same string embedded under these two task types returns
# different vectors.
TASK_TYPE_QUERY = "RETRIEVAL_QUERY"
TASK_TYPE_DOCUMENT = "RETRIEVAL_DOCUMENT"


class EmbeddingError(RuntimeError):
    """Raised when generating an embedding fails or returns no vector."""


def build_client(api_key: str) -> Any:
    """Construct an authenticated Gemini API client for embedding calls.

    The ``google.genai`` import happens lazily inside this function so tests
    can stub the SDK out via ``sys.modules`` without the real package ever
    making a network call.

    Args:
        api_key: Gemini API key to authenticate with.

    Returns:
        A configured ``google.genai.Client`` instance.
    """
    from google import genai

    return genai.Client(api_key=api_key)


def embed_text(
    text: str,
    api_key: str,
    model: str = EMBEDDING_MODEL,
    task_type: str | None = None,
) -> list[float]:
    """Embed a single piece of text using the Gemini embeddings endpoint.

    Args:
        text: The text to embed.
        api_key: Gemini API key to authenticate with.
        model: Embedding model name. Defaults to ``gemini-embedding-001``,
            which returns ``EMBEDDING_DIMENSIONS``-length vectors.
        task_type: Optional retrieval role for this text — use
            :data:`TASK_TYPE_QUERY` when embedding a user's question and
            :data:`TASK_TYPE_DOCUMENT` when embedding corpus passages. ``None``
            leaves the model at its default (symmetric) behavior.

    Returns:
        The embedding as a list of floats.

    Raises:
        EmbeddingError: If the SDK call fails or returns no embedding.
    """
    client = build_client(api_key)
    try:
        if task_type is None:
            result = client.models.embed_content(model=model, contents=text)
        else:
            from google.genai import types

            result = client.models.embed_content(
                model=model,
                contents=text,
                config=types.EmbedContentConfig(task_type=task_type),
            )
    except Exception as exc:  # SDK raises assorted google.genai.errors.APIError types
        raise EmbeddingError(f"Embedding request failed: {exc}") from exc

    embeddings = getattr(result, "embeddings", None)
    values = getattr(embeddings[0], "values", None) if embeddings else None
    if not values:
        raise EmbeddingError("Embedding response contained no vector.")
    return list(values)


def embed_texts(
    texts: list[str],
    api_key: str,
    model: str = EMBEDDING_MODEL,
    task_type: str | None = None,
) -> list[list[float]]:
    """Embed multiple texts, one Gemini call per text.

    Args:
        texts: Texts to embed.
        api_key: Gemini API key to authenticate with.
        model: Embedding model name.
        task_type: Optional retrieval role applied to every text — see
            :func:`embed_text`. Corpus passages should use
            :data:`TASK_TYPE_DOCUMENT`.

    Returns:
        A list of embedding vectors, in the same order as ``texts``.

    Raises:
        EmbeddingError: If any individual embedding request fails.
    """
    return [embed_text(text, api_key, model=model, task_type=task_type) for text in texts]


def cosine_similarity(vector_a: list[float], vector_b: list[float]) -> float:
    """Compute cosine similarity between two equal-length vectors.

    Args:
        vector_a: First vector.
        vector_b: Second vector.

    Returns:
        A similarity score in [-1.0, 1.0]. Returns 0.0 if either vector has
        zero magnitude.

    Raises:
        ValueError: If the vectors have different lengths.
    """
    if len(vector_a) != len(vector_b):
        raise ValueError(
            f"Vectors must be the same length, got {len(vector_a)} and {len(vector_b)}."
        )

    dot_product = sum(a * b for a, b in zip(vector_a, vector_b))
    magnitude_a = math.sqrt(sum(a * a for a in vector_a))
    magnitude_b = math.sqrt(sum(b * b for b in vector_b))

    if magnitude_a == 0.0 or magnitude_b == 0.0:
        return 0.0
    return dot_product / (magnitude_a * magnitude_b)
