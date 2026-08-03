# Recipe: Basic RAG (Retrieval-Augmented Generation)

The minimal shape of a RAG loop: embed a query, retrieve the most relevant
chunks from your document store, and feed both into the model as context.
See `projects/02-rag-chatbot` for a fuller, runnable implementation.

## The pattern

```python
"""Minimal RAG loop: retrieve relevant chunks, then generate an answer."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def retrieve(query: str, documents: list[str], top_k: int = 3) -> list[str]:
    """Return the top_k documents most relevant to query.

    A real implementation embeds `query` and `documents` and ranks by
    cosine similarity (see projects/02-rag-chatbot/src/embeddings.py and
    retrieval.py for a working example). This stub uses naive keyword
    overlap so the pattern is readable without a model call.
    """
    scored = sorted(
        documents,
        key=lambda doc: len(set(query.lower().split()) & set(doc.lower().split())),
        reverse=True,
    )
    return scored[:top_k]


def build_prompt(query: str, context_chunks: list[str]) -> str:
    """Combine retrieved context with the user's question into one prompt."""
    context = "\n\n".join(context_chunks)
    return (
        "Answer the question using only the context below. "
        "If the answer isn't in the context, say so.\n\n"
        f"Context:\n{context}\n\nQuestion: {query}\nAnswer:"
    )


def answer(query: str, documents: list[str]) -> str:
    """Run the full retrieve -> prompt -> generate loop.

    Raises:
        RuntimeError: If no documents are available to retrieve from.
    """
    if not documents:
        raise RuntimeError("No documents available for retrieval")

    chunks = retrieve(query, documents)
    prompt = build_prompt(query, chunks)
    logger.info("Built RAG prompt with %d context chunks", len(chunks))

    # Swap this for a real client, e.g.:
    #   from google import genai
    #   client = genai.Client(api_key=API_KEY)
    #   resp = client.models.generate_content(
    #       model="gemini-2.5-flash", contents=prompt
    #   )
    #   return resp.text
    return prompt  # placeholder: return the assembled prompt itself
```

## Embedding the query and documents

The retrieval stub above uses keyword overlap. The real version embeds both
sides and ranks by cosine similarity:

```python
from google import genai

client = genai.Client(api_key=API_KEY)

EMBEDDING_MODEL = "gemini-embedding-001"  # 3072-dimensional vectors


def embed(text: str) -> list[float]:
    """Return the embedding vector for a single piece of text."""
    result = client.models.embed_content(model=EMBEDDING_MODEL, contents=text)
    return result.embeddings[0].values
```

Embeddings from different models are not comparable — if you switch embedding
models, re-embed the entire corpus rather than mixing vectors.

## Why this shape

- **Retrieval is separate from generation.** You can swap the retrieval
  implementation (keyword match → embeddings → a vector database) without
  touching how the prompt is built or how the model is called.
- **The prompt explicitly tells the model to stick to the context** and to
  admit when it can't answer — this is the single highest-leverage change
  you can make to reduce hallucination in a RAG system.
- **Guard the empty-documents case.** A RAG call with no retrievable
  documents should fail loudly (`RuntimeError`), not silently ask the model
  to answer from nothing.

## Next steps

- `projects/02-rag-chatbot/src/embeddings.py` — real embedding generation
- `projects/02-rag-chatbot/src/retrieval.py` — similarity-based retrieval
  instead of the keyword-overlap stub above
- Once `evaluation` is enabled (`cockpit/config/feature_flags.py`), score
  RAG answers for groundedness against the retrieved context rather than
  trusting them blindly.
