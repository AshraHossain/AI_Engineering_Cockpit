"""Tests for the RAG demo: chunking, retrieval ranking, prompt building,
and the embedding/generation wrappers.

No network calls and no real API key are used anywhere in this module --
the ``google.genai`` SDK is stubbed out with ``unittest.mock``, and
retrieval ranking is tested with a small deterministic fake embedding
function instead of a real embedding API.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import embeddings
import main
import pytest
from embeddings import EmbeddingError, cosine_similarity, embed_text
from retrieval import chunk_text, top_k_chunks


def _fake_sdk() -> tuple[Any, dict[str, Any]]:
    """Build a stand-in for the ``google.genai`` SDK and its sys.modules patch.

    Both ``embeddings.build_client`` and ``main.build_client`` do
    ``from google import genai``, so the stub is installed as the ``google``
    package with a ``genai`` attribute.

    Returns:
        A ``(fake_genai, modules)`` pair, where ``fake_genai`` is the mock
        standing in for the ``google.genai`` module and ``modules`` is the
        mapping to hand to ``patch.dict("sys.modules", ...)``.
    """
    fake_genai = MagicMock()
    fake_google = MagicMock()
    fake_google.genai = fake_genai
    return fake_genai, {"google": fake_google, "google.genai": fake_genai}


def _embedding_response(values: list[float] | None) -> Any:
    """Build an ``embed_content`` response shaped like the google-genai one.

    Args:
        values: Float values for the single returned embedding, or None to
            simulate a response carrying no embeddings at all.

    Returns:
        A mock exposing ``.embeddings[0].values``.
    """
    response = MagicMock()
    if values is None:
        response.embeddings = []
    else:
        embedding = MagicMock()
        embedding.values = values
        response.embeddings = [embedding]
    return response


def _fake_client(text: str | None = "ok") -> Any:
    """Build a ``genai.Client``-shaped mock whose response carries ``text``.

    Args:
        text: Value for ``response.text`` on the generated response.

    Returns:
        A mock exposing ``models.generate_content(model=..., contents=...)``.
    """
    client = MagicMock()
    client.models.generate_content.return_value = MagicMock(text=text)
    return client


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


def test_embedding_model_is_a_current_model_id() -> None:
    """Guard against regressing to the retired text-embedding-004 id (now 404s)."""
    assert embeddings.EMBEDDING_MODEL == "gemini-embedding-001"


def test_embeddings_build_client_constructs_client_with_api_key() -> None:
    """embeddings.build_client authenticates a genai.Client with the supplied key."""
    fake_genai, modules = _fake_sdk()

    with patch.dict("sys.modules", modules):
        client = embeddings.build_client("fake-key")  # pragma: allowlist secret

    fake_genai.Client.assert_called_once_with(api_key="fake-key")  # pragma: allowlist secret
    assert client is fake_genai.Client.return_value


def test_embed_text_returns_vector_from_sdk() -> None:
    """embed_text sends the text and unwraps .embeddings[0].values."""
    fake_genai, modules = _fake_sdk()
    fake_client = fake_genai.Client.return_value
    fake_client.models.embed_content.return_value = _embedding_response([0.1, 0.2, 0.3])

    with patch.dict("sys.modules", modules):
        vector = embed_text("some text", "fake-key")  # pragma: allowlist secret

    fake_genai.Client.assert_called_once_with(api_key="fake-key")  # pragma: allowlist secret
    fake_client.models.embed_content.assert_called_once_with(
        model=embeddings.EMBEDDING_MODEL, contents="some text"
    )
    assert vector == [0.1, 0.2, 0.3]


def test_embed_text_omits_config_when_no_task_type() -> None:
    """Without a task_type the call carries no config kwarg at all."""
    fake_genai, modules = _fake_sdk()
    fake_client = fake_genai.Client.return_value
    fake_client.models.embed_content.return_value = _embedding_response([1.0])

    with patch.dict("sys.modules", modules):
        embed_text("some text", "fake-key")  # pragma: allowlist secret

    assert "config" not in fake_client.models.embed_content.call_args.kwargs


def test_embed_text_passes_task_type_through_config() -> None:
    """A task_type is forwarded to the SDK inside an EmbedContentConfig."""
    fake_genai, modules = _fake_sdk()
    fake_client = fake_genai.Client.return_value
    fake_client.models.embed_content.return_value = _embedding_response([1.0])
    fake_types = MagicMock()
    modules["google.genai.types"] = fake_types
    fake_genai.types = fake_types

    with patch.dict("sys.modules", modules):
        embed_text(
            "some text",
            "fake-key",  # pragma: allowlist secret
            task_type=embeddings.TASK_TYPE_QUERY,
        )

    fake_types.EmbedContentConfig.assert_called_once_with(task_type=embeddings.TASK_TYPE_QUERY)
    assert (
        fake_client.models.embed_content.call_args.kwargs["config"]
        is fake_types.EmbedContentConfig.return_value
    )


def test_embed_text_honours_model_override() -> None:
    """An explicit model name is forwarded to the SDK instead of the default."""
    fake_genai, modules = _fake_sdk()
    fake_client = fake_genai.Client.return_value
    fake_client.models.embed_content.return_value = _embedding_response([1.0])

    with patch.dict("sys.modules", modules):
        embed_text(
            "some text", "fake-key", model="gemini-embedding-001"
        )  # pragma: allowlist secret

    assert fake_client.models.embed_content.call_args.kwargs["model"] == "gemini-embedding-001"


def test_embed_text_raises_on_missing_embedding() -> None:
    """A response carrying no embeddings raises EmbeddingError."""
    fake_genai, modules = _fake_sdk()
    fake_genai.Client.return_value.models.embed_content.return_value = _embedding_response(None)

    with patch.dict("sys.modules", modules), pytest.raises(EmbeddingError, match="no vector"):
        embed_text("some text", "fake-key")  # pragma: allowlist secret


def test_embed_text_raises_on_empty_values() -> None:
    """An embedding whose .values is empty is also treated as a failure."""
    fake_genai, modules = _fake_sdk()
    fake_genai.Client.return_value.models.embed_content.return_value = _embedding_response([])

    with patch.dict("sys.modules", modules), pytest.raises(EmbeddingError, match="no vector"):
        embed_text("some text", "fake-key")  # pragma: allowlist secret


def test_embed_text_wraps_sdk_exceptions() -> None:
    """Exceptions from the SDK call are wrapped in EmbeddingError."""
    fake_genai, modules = _fake_sdk()
    fake_genai.Client.return_value.models.embed_content.side_effect = RuntimeError("quota exceeded")

    with patch.dict("sys.modules", modules), pytest.raises(EmbeddingError, match="quota exceeded"):
        embed_text("some text", "fake-key")  # pragma: allowlist secret


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


def test_default_model_is_a_current_model_id() -> None:
    """Guard against regressing to a retired model id (the 1.5 family now 404s)."""
    assert main.DEFAULT_MODEL == "gemini-2.5-flash"


def test_generate_answer_sends_prompt_and_model_and_returns_text() -> None:
    """generate_answer passes model+contents through and returns response.text."""
    fake_client = _fake_client("RAG grounds answers in context.")

    result = main.generate_answer(fake_client, "some prompt")

    fake_client.models.generate_content.assert_called_once_with(
        model=main.DEFAULT_MODEL, contents="some prompt"
    )
    assert result == "RAG grounds answers in context."


def test_generate_answer_honours_model_override() -> None:
    """An explicit model_name is forwarded to the SDK instead of the default."""
    fake_client = _fake_client("pro answer")

    result = main.generate_answer(fake_client, "some prompt", model_name="gemini-2.5-pro")

    fake_client.models.generate_content.assert_called_once_with(
        model="gemini-2.5-pro", contents="some prompt"
    )
    assert result == "pro answer"


def test_generate_answer_raises_on_empty_text() -> None:
    """An empty .text is treated as a generation error."""
    fake_client = _fake_client("")

    with pytest.raises(main.GenerationError, match="empty response"):
        main.generate_answer(fake_client, "some prompt")


def test_generate_answer_wraps_sdk_exceptions() -> None:
    """Exceptions raised during generation are wrapped in GenerationError."""
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = RuntimeError("boom")

    with pytest.raises(main.GenerationError, match="boom"):
        main.generate_answer(fake_client, "some prompt")


def test_main_build_client_constructs_client_with_api_key() -> None:
    """main.build_client authenticates a genai.Client with the supplied key."""
    fake_genai, modules = _fake_sdk()

    with patch.dict("sys.modules", modules):
        client = main.build_client("fake-key")  # pragma: allowlist secret

    fake_genai.Client.assert_called_once_with(api_key="fake-key")  # pragma: allowlist secret
    assert client is fake_genai.Client.return_value


# ---------------------------------------------------------------------------
# main.answer_question (full pipeline, everything mocked/injected)
# ---------------------------------------------------------------------------


def test_answer_question_runs_full_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """answer_question chunks the corpus, retrieves, builds a prompt, and generates."""
    corpus = "Paragraph about cats.\n\nParagraph about cosine similarity and vectors."
    fake_client = _fake_client("Cosine similarity is a metric.")

    seen_task_types: list[str | None] = []

    def fake_embed(text: str, api_key: str, task_type: str | None = None) -> list[float]:
        seen_task_types.append(task_type)
        return [1.0 if "cosine" in text.lower() else 0.0]

    monkeypatch.setattr(main, "embed_text", fake_embed)
    monkeypatch.setattr(main, "build_client", lambda api_key: fake_client)

    result = main.answer_question(
        "What is cosine similarity?",
        api_key="fake-key",
        corpus=corpus,
        top_k=1,  # pragma: allowlist secret
    )

    assert result == "Cosine similarity is a metric."
    # The query and the corpus chunks must be embedded under *different* task
    # types -- that asymmetry is the whole point of passing task_type through.
    assert embeddings.TASK_TYPE_QUERY in seen_task_types
    assert embeddings.TASK_TYPE_DOCUMENT in seen_task_types
    fake_client.models.generate_content.assert_called_once()
    call = fake_client.models.generate_content.call_args
    assert call.kwargs["model"] == main.DEFAULT_MODEL
    sent_prompt = call.kwargs["contents"]
    assert "What is cosine similarity?" in sent_prompt
    # Only the retrieved (top-1) chunk should be augmented into the prompt.
    assert "cosine similarity and vectors" in sent_prompt
    assert "Paragraph about cats." not in sent_prompt


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
