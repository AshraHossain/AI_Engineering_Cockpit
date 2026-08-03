"""Minimal Retrieval-Augmented Generation (RAG) demo.

Pipeline: chunk a corpus -> embed the chunks and the question -> retrieve
the top-k most relevant chunks -> build a prompt that augments the question
with that retrieved context -> generate an answer with Gemini.

This module is standalone -- it only imports from ``embeddings`` and
``retrieval`` in this same project, not from anything in the repo root.

Run it with:
    uv run python src/main.py "What is cosine similarity?"
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from embeddings import TASK_TYPE_DOCUMENT, TASK_TYPE_QUERY, embed_text
from retrieval import chunk_text, top_k_chunks

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_TOP_K = 3
DEFAULT_CORPUS_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_docs.txt"
DEFAULT_QUESTION = "What is Retrieval-Augmented Generation?"

RAG_PROMPT_TEMPLATE = """Answer the question using only the context below. \
If the context does not contain the answer, say you don't know.

Context:
{context}

Question: {question}

Answer:"""


class MissingAPIKeyError(RuntimeError):
    """Raised when GEMINI_API_KEY is missing or empty in the environment."""


class GenerationError(RuntimeError):
    """Raised when the Gemini generation call fails or returns no text."""


def configure_logging(level: str | None = None) -> None:
    """Configure a minimal stdout logging setup for this script.

    Args:
        level: Logging level name. Defaults to the LOG_LEVEL environment
            variable, or "INFO" if unset.
    """
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=level or os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def get_api_key(env: dict[str, str] | None = None) -> str:
    """Read the Gemini API key from the environment.

    Args:
        env: Optional mapping to read from instead of ``os.environ``
            (mainly for testing).

    Returns:
        The value of the GEMINI_API_KEY environment variable.

    Raises:
        MissingAPIKeyError: If GEMINI_API_KEY is unset or blank.
    """
    source = env if env is not None else os.environ
    api_key = source.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise MissingAPIKeyError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return api_key


def load_corpus(path: Path = DEFAULT_CORPUS_PATH) -> str:
    """Load the sample corpus text from disk.

    Args:
        path: Path to the corpus text file.

    Returns:
        The full file contents as a string.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
    """
    return path.read_text(encoding="utf-8")


def build_prompt(question: str, context_chunks: list[str]) -> str:
    """Build an augmented prompt that grounds the question in retrieved context.

    Args:
        question: The user's question.
        context_chunks: Retrieved chunks to include as context, most
            relevant first.

    Returns:
        The formatted prompt string ready to send to the model.
    """
    context = "\n\n".join(context_chunks) if context_chunks else "(no relevant context found)"
    return RAG_PROMPT_TEMPLATE.format(context=context, question=question)


def build_client(api_key: str) -> Any:
    """Construct an authenticated Gemini API client.

    The ``google.genai`` import is done lazily inside this function so that
    unit tests can stub the SDK without it ever making a network call. In
    the ``google-genai`` SDK the model is chosen per request rather than
    bound to the client, so no model name is taken here -- see
    ``generate_answer``.

    Args:
        api_key: The Gemini API key to authenticate with.

    Returns:
        A configured ``google.genai.Client`` instance.
    """
    from google import genai

    return genai.Client(api_key=api_key)


def generate_answer(client: Any, prompt: str, model_name: str = DEFAULT_MODEL) -> str:
    """Send the augmented prompt to Gemini and return the text answer.

    Args:
        client: A ``genai.Client``-like object exposing
            ``models.generate_content(model=..., contents=...)``.
        prompt: The fully-built, context-augmented prompt.
        model_name: Name of the Gemini model to generate with.

    Returns:
        The text of the model's answer.

    Raises:
        GenerationError: If the SDK call raises, or the response has no text.
    """
    try:
        response = client.models.generate_content(model=model_name, contents=prompt)
    except Exception as exc:  # SDK raises assorted google.genai.errors.APIError types
        raise GenerationError(f"Gemini generation call failed: {exc}") from exc

    text = getattr(response, "text", None)
    if not text:
        raise GenerationError("Gemini API returned an empty response.")
    return text


def answer_question(
    question: str,
    api_key: str,
    corpus: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> str:
    """Run the full RAG pipeline for a single question.

    Args:
        question: The question to answer.
        api_key: Gemini API key used for both embedding and generation.
        corpus: Corpus text to retrieve from. Defaults to the bundled
            sample corpus.
        top_k: Number of chunks to retrieve as context.

    Returns:
        The generated answer text.

    Raises:
        GenerationError: If the generation call fails.
    """
    corpus_text = corpus if corpus is not None else load_corpus()
    chunks = chunk_text(corpus_text)

    def embed_document(text: str) -> list[float]:
        return embed_text(text, api_key, task_type=TASK_TYPE_DOCUMENT)

    def embed_query(text: str) -> list[float]:
        return embed_text(text, api_key, task_type=TASK_TYPE_QUERY)

    ranked = top_k_chunks(
        question, chunks, embed_document, k=top_k, embed_query_fn=embed_query
    )
    context_chunks = [chunk for chunk, _score in ranked]

    prompt = build_prompt(question, context_chunks)
    client = build_client(api_key)
    return generate_answer(client, prompt)


def main(question: str = DEFAULT_QUESTION) -> int:
    """CLI entry point: run the RAG pipeline end-to-end and log the answer.

    Args:
        question: The question to ask. Defaults to a demo question if the
            script is run with no arguments.

    Returns:
        Process exit code: 0 on success, 1 on a configuration or API error.
    """
    configure_logging()
    load_dotenv()

    try:
        api_key = get_api_key()
        answer = answer_question(question, api_key)
    except MissingAPIKeyError as exc:
        logger.error("Configuration error: %s", exc)
        return 1
    except GenerationError as exc:
        logger.error("Generation error: %s", exc)
        return 1
    except FileNotFoundError as exc:
        logger.error("Corpus error: %s", exc)
        return 1

    logger.info("Question: %s", question)
    logger.info("Answer: %s", answer)
    return 0


if __name__ == "__main__":
    cli_question = " ".join(sys.argv[1:]) or DEFAULT_QUESTION
    sys.exit(main(cli_question))
