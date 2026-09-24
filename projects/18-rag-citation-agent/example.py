#!/usr/bin/env python3
"""
End-to-end example of Project 18 (RAG Citation Agent) with integration stubs.

Shows how to:
1. Create a retriever with a document corpus
2. Set up an LLM (mock or real Claude)
3. Ground citations and score confidence
4. Trigger fallback search when needed
5. Log metrics

Run with:
    python example.py

This will:
1. Ask a few questions
2. Retrieve context from the hardcoded corpus
3. Generate answers with citations (using mock or Claude)
4. Ground citations and score confidence
5. Trigger fallback search if confidence is low
6. Return grounded answers or "I don't know"
"""

import asyncio
import logging
import sys

from agent import (
    CitationGrounder,
    ConfidenceScorer,
    Query,
    RAGAgent,
)
from integrations import (
    FileBasedRetriever,
    FileMetricsLogger,
    LLMWithClaude,
    SearchFallbackStub,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

log = logging.getLogger("example")


async def main() -> None:
    """Run the RAG agent on example queries."""
    log.info("=== RAG Citation Agent Example ===")

    # Setup integrations
    retriever = FileBasedRetriever()

    # LLMWithClaude falls back to MockLLM internally if the Anthropic SDK
    # or ANTHROPIC_API_KEY isn't available -- no try/except needed here.
    llm = LLMWithClaude(model="claude-sonnet-5")

    fallback = SearchFallbackStub()
    metrics = FileMetricsLogger("/tmp/rag_metrics.jsonl")

    # Create components
    grounder = CitationGrounder()
    scorer = ConfidenceScorer(threshold=0.6)

    # Create the RAG agent
    agent = RAGAgent(
        retriever=retriever,
        llm=llm,
        grounder=grounder,
        scorer=scorer,
        fallback=fallback,
        metrics=metrics,
    )

    # Example queries
    queries = [
        "What is Python and what is it used for?",
        "How does asyncio work in Python?",
        "What is retrieval-augmented generation?",
        "What is the meaning of life?",  # Should trigger "I don't know" fallback
        "Explain TypeScript",
    ]

    log.info("Running %d example queries...", len(queries))
    log.info("=" * 60)

    for query_text in queries:
        query = Query(text=query_text)
        log.info("Query: %s", query_text)

        try:
            answer = await agent.answer(query)
            log.info("Answer: %s", answer.text)
            log.info(
                "Confidence: %.2f | Citations: %d | Fallback: %s",
                answer.confidence,
                len(answer.citations),
                answer.used_fallback,
            )

            if answer.citations:
                for i, citation in enumerate(answer.citations, 1):
                    log.info("  [%d] %s (%s)", i, citation.title, citation.source_uri)

        except Exception:
            log.exception("Error processing query")

        log.info("-" * 60)

    log.info("=== Results Summary ===")
    log.info("Metrics logged to /tmp/rag_metrics.jsonl")
    log.info("To view metrics: cat /tmp/rag_metrics.jsonl | jq .")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(0)
