"""Tests for the RAG demo: chunking, retrieval ranking, prompt building,
and the embedding/generation wrappers.

No network calls and no real API key are used anywhere in this module --
the ``google.generativeai`` SDK is stubbed out with ``unittest.mock``, and
retrieval ranking is tested with a small deterministic fake embedding
function instead of a real embedding API.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import main
import pytest
from embeddings import EmbeddingError, cosine_similarity, embed_text
from retrieval import chunk_text, top_k_chunks

# ---------------------------------------------------------------------------
# retrieval.chunk_text
# ---------------------------------------------------------------------------


def test_chunk_text_splits_on_blank_lines() -> None:
    """Paragraphs separated by a blank line become separate chunks."""
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    assert chunk_text(text) == ["First paragraph.", "Second paragraph.", "Third paragraph."]


def test_chunk_text_strips_whitespace_and_drops_empty_chunks() -> None:
    """Leading/trailing whitespace is trimmed and empty chunks are dropped."""
    text = "  Alpha  \n\n\n\n   \n\nBeta\n"
    assert chunk_text(text) == ["Alpha", "Beta"]


def test_chunk_text_empty_string_returns_empty_list() -> None:
    """An empty document produces no chunks."""
    assert chunk_text("") == []


# ---------------------------------------------------------------------------
# embeddings.cosine_similarity
# ---------------------------------------------------------------------------


def test_cosine_similarity_identical_vectors_is_one() -> None:
    """Identical vectors have similarity 1.0."""
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_is_zero() -> None:
    """Perpendicular vectors have similarity 0.0."""
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors_is_negative_one() -> None:
    """Opposite-direction vectors have similarity -1.0."""
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_zero_magnitude_vector_returns_zero() -> None:
    """A zero vector yields similarity 0.0 rather than a divide-by-zero error."""
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_cosine_similarity_mismatched_lengths_raises() -> None:
    """Vectors of different lengths are rejected."""
    with pytest.raises(ValueError, match="same length"):
        cosine_similarity([1.0, 2.0], [1.0])


# ---------------------------------------------------------------------------
# retrieval.top_k_chunks (uses a fake, deterministic embed_fn)
# ---------------------------------------------------------------------------


def _word_overlap_embed_fn(vocabulary: list[str]):
    """Build a fake embed_fn producing a one-hot-ish word-presence vector.

    This avoids any real embedding API: the "vector" for a text is just a
    0/1 indicator per vocabulary word, which is enough to deterministically
    test that top_k_chunks ranks more-relevant chunks higher.
    """

    def embed(text: str) -> list[float]:
        lowered = text.lower()
        return [1.0 if word in lowered else 0.0 for word in vocabulary]

    return embed


def test_top_k_chunks_ranks_most_relevant_first() -> None:
    """The chunk sharing the most vocabulary with the query ranks first."""
    chunks = [
        "Cats are small domesticated animals.",
        "Cosine similarity measures the angle between two vectors.",
        "The weather today is sunny and warm.",
    ]
    embed_fn = _word_overlap_embed_fn(["cosine", "similarity", "vectors", "angle"])

    results = top_k_chunks("What is cosine similarity?", chunks, embed_fn, k=2)

    assert len(results) == 2
    assert results[0][0] == "Cosine similarity measures the angle between two vectors."
    assert results[0][1] >= results[1][1]


def test_top_k_chunks_respects_k() -> None:
    """No more than k results are returned."""
    chunks = ["a", "b", "c", "d"]
    embed_fn = lambda text: [float(len(text))]  # noqa: E731 - simple test fake

    results = top_k_chunks("query", chunks, embed_fn, k=2)

    assert len(results) == 2


def test_top_k_chunks_empty_corpus_returns_empty_list() -> None:
    """No chunks means no results, regardless of k."""
    embed_fn = lambda text: [1.0]  # noqa: E731 - simple test fake
    assert top_k_chunks("query", [], embed_fn, k=3) == []


def test_top_k_chunks_rejects_non_positive_k() -> None:
    """k must be at least 1."""
    embed_fn = lambda text: [1.0]  # noqa: E731 - simple test fake
    with pytest.raises(ValueError, match="k must be"):
        top_k_chunks("query", ["a"], embed_fn, k=0)


# ---------------------------------------------------------------------------
# embeddings.embed_text (mocked SDK)
# ---------------------------------------------------------------------------


def test_embed_text_returns_vector_from_sdk() -> None:
    """embed_text extracts the embedding list from the SDK's dict response."""
    fake_genai = MagicMock()
    fake_genai.embed_content.return_value = {"embedding": [0.1, 0.2, 0.3]}

    with patch.dict("sys.modules", {"google.generativeai": fake_genai}):
        vector = embed_text("some text", "fake-key")

    fake_genai.configure.assert_called_once_with(api_key="fake-key")  # pragma: allowlist secret
    assert vector == [0.1, 0.2, 0.3]


def test_embed_text_raises_on_missing_embedding() -> None:
    """A response with no embedding key raises EmbeddingError."""
    fake_genai = MagicMock()
    fake_genai.embed_content.return_value = {}

    with patch.dict("sys.modules", {"google.generativeai": fake_genai}):
        with pytest.raises(EmbeddingError, match="no vector"):
            embed_text("some text", "fake-key")


def test_embed_text_wraps_sdk_exceptions() -> None:
    """Exceptions from the SDK call are wrapped in EmbeddingError."""
    fake_genai = MagicMock()
    fake_genai.embed_content.side_effect = RuntimeError("quota exceeded")

    with patch.dict("sys.modules", {"google.generativeai": fake_genai}):
        with pytest.raises(EmbeddingError, match="quota exceeded"):
            embed_text("some text", "fake-key")


# ---------------------------------------------------------------------------
# main.build_prompt
# ---------------------------------------------------------------------------


def test_build_prompt_includes_question_and_context() -> None:
    """The prompt embeds both the retrieved context and the question."""
    prompt = main.build_prompt("What is RAG?", ["RAG combines retrieval and generation."])

    assert "What is RAG?" in prompt
    assert "RAG combines retrieval and generation." in prompt


def test_build_prompt_handles_no_context() -> None:
    """An empty context list still produces a well-formed prompt."""
    prompt = main.build_prompt("What is RAG?", [])

    assert "no relevant context found" in prompt


# ---------------------------------------------------------------------------
# main.generate_answer (mocked client)
# ---------------------------------------------------------------------------


def test_generate_answer_returns_text() -> None:
    """generate_answer extracts .text from the client's response."""
    fake_client = MagicMock()
    fake_client.generate_content.return_value = MagicMock(text="RAG grounds answers in context.")

    result = main.generate_answer(fake_client, "some prompt")

    assert result == "RAG grounds answers in context."


def test_generate_answer_raises_on_empty_text() -> None:
    """An empty .text is treated as a generation error."""
    fake_client = MagicMock()
    fake_client.generate_content.return_value = MagicMock(text="")

    with pytest.raises(main.GenerationError, match="empty response"):
        main.generate_answer(fake_client, "some prompt")


def test_generate_answer_wraps_sdk_exceptions() -> None:
    """Exceptions raised during generation are wrapped in GenerationError."""
    fake_client = MagicMock()
    fake_client.generate_content.side_effect = RuntimeError("boom")

    with pytest.raises(main.GenerationError, match="boom"):
        main.generate_answer(fake_client, "some prompt")


# ---------------------------------------------------------------------------
# main.answer_question (full pipeline, everything mocked/injected)
# ---------------------------------------------------------------------------


def test_answer_question_runs_full_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """answer_question chunks the corpus, retrieves, builds a prompt, and generates."""
    corpus = "Paragraph about cats.\n\nParagraph about cosine similarity and vectors."
    fake_client = MagicMock()
    fake_client.generate_content.return_value = MagicMock(text="Cosine similarity is a metric.")

    monkeypatch.setattr(
        main, "embed_text", lambda text, api_key: [1.0 if "cosine" in text.lower() else 0.0]
    )
    monkeypatch.setattr(main, "build_client", lambda api_key: fake_client)

    result = main.answer_question(
        "What is cosine similarity?", api_key="fake-key", corpus=corpus, top_k=1  # pragma: allowlist secret
    )

    assert result == "Cosine similarity is a metric."
    fake_client.generate_content.assert_called_once()
    sent_prompt = fake_client.generate_content.call_args[0][0]
    assert "What is cosine similarity?" in sent_prompt


# ---------------------------------------------------------------------------
# main.get_api_key / main.main (mirrors project 01's config error handling)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", [{}, {"GEMINI_API_KEY": ""}, {"GEMINI_API_KEY": "   "}])
def test_get_api_key_raises_when_missing(env: dict[str, str]) -> None:
    """A missing, empty, or blank key raises MissingAPIKeyError."""
    with pytest.raises(main.MissingAPIKeyError):
        main.get_api_key(env)


def test_main_returns_1_on_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns exit code 1 when GEMINI_API_KEY is not configured."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main, "load_dotenv", lambda: None)

    assert main.main("hi") == 1


def test_main_returns_0_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns exit code 0 when the full pipeline succeeds."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(main, "load_dotenv", lambda: None)
    monkeypatch.setattr(main, "answer_question", lambda *a, **k: "an answer")

    assert main.main("hi") == 0


def test_main_returns_1_on_generation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns exit code 1 when the pipeline raises GenerationError."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(main, "load_dotenv", lambda: None)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise main.GenerationError("boom")

    monkeypatch.setattr(main, "answer_question", _boom)

    assert main.main("hi") == 1
