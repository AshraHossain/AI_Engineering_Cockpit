"""Chunking and top-k retrieval logic for the RAG demo.

This module contains no SDK calls of its own -- ``top_k_chunks`` takes an
``embed_fn`` callable so the ranking logic can be unit tested with a fake,
deterministic embedding function instead of a real (network-bound) one.
"""

from __future__ import annotations

from collections.abc import Callable

from embeddings import cosine_similarity

EmbedFn = Callable[[str], list[float]]


def chunk_text(text: str) -> list[str]:
    """Split a document into chunks on blank-line (paragraph) boundaries.

    Paragraph-based chunking is the simplest strategy that still keeps each
    chunk topically coherent, which is enough for a small demo corpus. See
    the project README for the tradeoffs against fixed-size or
    sentence-based chunking.

    Args:
        text: The full document text.

    Returns:
        A list of non-empty, whitespace-trimmed chunks, in document order.
    """
    raw_chunks = text.split("\n\n")
    return [chunk.strip() for chunk in raw_chunks if chunk.strip()]


def top_k_chunks(
    query: str,
    chunks: list[str],
    embed_fn: EmbedFn,
    k: int = 3,
) -> list[tuple[str, float]]:
    """Return the k chunks most relevant to the query, ranked by similarity.

    Args:
        query: The user's question.
        chunks: Candidate chunks to search over (e.g. from ``chunk_text``).
        embed_fn: Callable that embeds a single string into a vector. This
            is injected rather than hardcoded so tests can supply a cheap,
            deterministic fake instead of calling a real embedding API.
        k: Maximum number of chunks to return.

    Returns:
        A list of ``(chunk, similarity_score)`` tuples, sorted by
        descending similarity. Empty if ``chunks`` is empty. Length is
        ``min(k, len(chunks))``.

    Raises:
        ValueError: If ``k`` is less than 1.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    if not chunks:
        return []

    query_vector = embed_fn(query)
    scored = [(chunk, cosine_similarity(query_vector, embed_fn(chunk))) for chunk in chunks]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:k]
