# 02 - RAG Chatbot

A minimal Retrieval-Augmented Generation (RAG) demo: chunk a corpus, embed
the chunks, retrieve the ones most relevant to a question, augment the
prompt with that retrieved context, and generate a grounded answer with
Gemini.

## What is RAG?

A plain language model only knows what it memorized during training. RAG
adds a retrieval step in front of generation so the model can answer using
information it was never trained on:

```
question ─┐
           ├─▶ embed ─▶ compare against embedded chunks ─▶ top-k chunks
corpus ────┘                                                    │
                                                                  ▼
                                          question + retrieved chunks
                                                  │
                                                  ▼
                                      Gemini generates a grounded answer
```

1. **Chunk** -- split the source document(s) into smaller pieces.
2. **Embed** -- convert each chunk (and the question) into a numeric vector.
3. **Retrieve** -- rank chunks by similarity to the question, keep the top k.
4. **Augment** -- insert those chunks into the prompt as context.
5. **Generate** -- ask the model to answer using only that context.

## Design choices / tradeoffs

- **Embeddings**: this demo calls the real Gemini embeddings endpoint
  (`models/text-embedding-004` via `google-generativeai`) rather than
  hand-rolling a local embedding (e.g. TF-IDF with `difflib`). That keeps
  retrieval quality realistic -- it actually captures meaning, not just
  word overlap. The cost is that generating embeddings needs network
  access and an API key at runtime. `src/embeddings.py` isolates every SDK
  call behind small functions so this is still fully unit-testable offline
  (see Testing below).
- **Chunking**: paragraphs (blank-line-separated) are used as chunks. This
  is the simplest strategy that keeps each chunk topically coherent for a
  small demo corpus; it is not appropriate for very long or unstructured
  documents (a production system would use fixed-size or sentence-aware
  chunking with overlap).
- **Retrieval**: this is a brute-force scan -- every chunk's embedding is
  compared against the query embedding with cosine similarity and sorted.
  There is no vector database here on purpose: for a handful of documents,
  a linear scan in plain Python is fast and far simpler than standing up
  Chroma/Pinecone/Weaviate. See `src/retrieval.py`.

## Project layout

```
02-rag-chatbot/
├── pyproject.toml        # isolated deps: google-generativeai, python-dotenv, pytest
├── .python-version         # 3.11
├── src/
│   ├── main.py              # ties embeddings + retrieval together, CLI entry point
│   ├── embeddings.py         # Gemini embedding calls + cosine_similarity
│   └── retrieval.py          # chunk_text + top_k_chunks (pure logic, no SDK calls)
├── tests/test_rag.py       # mocked-SDK + fake-embedding tests, no network needed
├── data/sample_docs.txt    # demo corpus (RAG/Gemini/vector-db background paragraphs)
└── .env.example
```

This project is fully standalone -- it has its own `pyproject.toml` and does
not import anything from the rest of the `AI_Engineering_Cockpit` repo.

## How to run

```bash
cd projects/02-rag-chatbot
uv sync
cp .env.example .env   # then fill in GEMINI_API_KEY
uv run python src/main.py "What is cosine similarity?"
```

### Expected output

```
2026-08-01 12:00:00,000 | INFO     | main | Question: What is cosine similarity?
2026-08-01 12:00:00,000 | INFO     | main | Answer: <Gemini's grounded answer, citing the retrieved paragraph>
```

Running with no arguments asks the default demo question ("What is
Retrieval-Augmented Generation?").

## Running the tests

No API key or network access is required. `tests/test_rag.py` covers:

- `chunk_text` on fixture strings (paragraph splitting, whitespace, empty input)
- `cosine_similarity` (identical / orthogonal / opposite / zero vectors)
- `top_k_chunks` ranking, using a small fake `embed_fn` instead of a real API call
- `embeddings.embed_text` and `main.generate_answer`, with the
  `google.generativeai` SDK stubbed via `unittest.mock`
- `main.build_prompt` and the end-to-end `answer_question` pipeline, with
  every SDK boundary mocked or injected

```bash
cd projects/02-rag-chatbot
uv sync
uv run pytest
```

## Getting a Gemini API key

Create a free key at [Google AI Studio](https://aistudio.google.com/app/apikey).
