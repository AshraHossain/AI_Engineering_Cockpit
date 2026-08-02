"""Text embedding generation for the RAG demo.

Design tradeoff (documented here and in the project README): this module
calls the real Gemini embeddings endpoint (``models/text-embedding-004``)
rather than implementing a from-scratch local embedding (e.g. TF-IDF). That
keeps the demo genuinely representative of how RAG works in production --
retrieval quality depends on real semantic vectors, not just word overlap.
The cost is that ``embed_text``/``embed_texts`` need network access and an
API key at runtime. To keep this testable offline, every function that
calls the SDK is a thin, mockable wrapper, and the pure-math pieces
(``cosine_similarity``) have zero dependency on the network and are tested
directly.
"""

from __future__ import annotations

import math

EMBEDDING_MODEL = "models/text-embedding-004"


class EmbeddingError(RuntimeError):
    """Raised when generating an embedding fails or returns no vector."""


def embed_text(
    text: str,
    api_key: str,
    model: str = EMBEDDING_MODEL,
    task_type: str = "retrieval_document",
) -> list[float]:
    """Embed a single piece of text using the Gemini embeddings endpoint.

    The ``google.generativeai`` import happens lazily inside this function
    so tests can stub the module out via ``sys.modules`` without requiring
    the real SDK to make a network call.

    Args:
        text: The text to embed.
        api_key: Gemini API key to authenticate with.
        model: Embedding model name.
        task_type: Gemini embedding task type, e.g. "retrieval_document"
            for corpus chunks or "retrieval_query" for the user's question.

    Returns:
        The embedding as a list of floats.

    Raises:
        EmbeddingError: If the SDK call fails or returns no embedding.
    """
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    try:
        result = genai.embed_content(model=model, content=text, task_type=task_type)
    except Exception as exc:  # SDK raises assorted google.api_core errors
        raise EmbeddingError(f"Embedding request failed: {exc}") from exc

    embedding = result.get("embedding") if isinstance(result, dict) else None
    if not embedding:
        raise EmbeddingError("Embedding response contained no vector.")
    return list(embedding)


def embed_texts(
    texts: list[str],
    api_key: str,
    model: str = EMBEDDING_MODEL,
    task_type: str = "retrieval_document",
) -> list[list[float]]:
    """Embed multiple texts, one Gemini call per text.

    Args:
        texts: Texts to embed.
        api_key: Gemini API key to authenticate with.
        model: Embedding model name.
        task_type: Gemini embedding task type.

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
