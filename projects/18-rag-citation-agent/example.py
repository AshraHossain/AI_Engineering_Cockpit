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
import os
import sys

from agent import (
    CitationGrounder,
    ConfidenceScorer,
    Query,
    RAGAgent,
)
from health import HealthChecker, serve_health
from integrations import (
    CircuitBreakerLLM,
    CircuitBreakerRetriever,
    CircuitBreakerSearchFallback,
    FileBasedRetriever,
    FileMetricsLogger,
    LLMWithClaude,
    QueryRateLimitedError,
    QueryValidationError,
    SearchFallbackStub,
    SecureRAGAgent,
)
from structured_logging import configure_json_logging

# One JSON object per log line -- grep any query's correlation_id across
# every component it touched. Set EXAMPLE_LOG_FORMAT=text for the old
# human-readable format while developing.
if os.environ.get("EXAMPLE_LOG_FORMAT") == "text":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
else:
    configure_json_logging()

log = logging.getLogger("example")


async def main() -> None:
    """Run the RAG agent on example queries."""
    log.info("=== RAG Citation Agent Example ===")

    # Setup integrations, each wrapped in a circuit breaker: if a dependency
    # starts failing repeatedly, its wrapper degrades to the safe default
    # (empty context / abstain / empty results) instead of raising.
    metrics = FileMetricsLogger("/tmp/rag_metrics.jsonl")
    retriever = CircuitBreakerRetriever(FileBasedRetriever(), metrics=metrics)

    # LLMWithClaude falls back to MockLLM internally if the Anthropic SDK
    # or ANTHROPIC_API_KEY isn't available -- no try/except needed here.
    llm = CircuitBreakerLLM(LLMWithClaude(model="claude-sonnet-5"), metrics=metrics)

    fallback = CircuitBreakerSearchFallback(SearchFallbackStub(), metrics=metrics)

    # Health checks read the same breakers the agent calls through. Ready if
    # the LLM works AND (retriever OR fallback) works -- RAGAgent already
    # routes around either one on its own, so both being down is what
    # actually matters for readiness.
    health = HealthChecker()
    health.register("retriever", retriever.allow)
    health.register("llm", llm.allow)
    health.register("search_fallback", fallback.allow)
    health.require_any("retriever", "search_fallback")
    health_server = serve_health(health, port=0)
    log.info(
        "health endpoints ready",
        extra={
            "healthz": f"http://127.0.0.1:{health_server.server_port}/healthz",
            "readyz": f"http://127.0.0.1:{health_server.server_port}/readyz",
        },
    )

    # Create components (ConfidenceScorer takes no args; threshold lives on RAGAgent)
    grounder = CitationGrounder()
    scorer = ConfidenceScorer()

    # Create the RAG agent (retriever, llm, fallback are positional)
    agent = RAGAgent(
        retriever,
        llm,
        fallback,
        grounder=grounder,
        scorer=scorer,
        metrics=metrics,
        threshold=0.6,
    )

    # Wrap with input validation + per-user rate limiting. Capacity is
    # deliberately tight (3 requests, slow refill) so the demo's own query
    # volume for "demo-user" actually demonstrates a 429-equivalent rather
    # than requiring a much longer query list to hit a realistic budget.
    secure_agent = SecureRAGAgent(agent, capacity=3, refill_per_s=0.5, metrics=metrics)

    # Example queries: one deliberately empty (validation), the rest normal
    # enough to also exercise the rate limit before they all finish.
    queries = [
        "What is Python and what is it used for?",
        "How does asyncio work in Python?",
        "",  # deliberately invalid: SecureRAGAgent rejects before retrieval runs
        "What is retrieval-augmented generation?",
        "What is the meaning of life?",  # Should trigger "I don't know" fallback
        "Explain TypeScript",
    ]

    log.info("Running %d example queries...", len(queries))
    log.info("=" * 60)

    for query_text in queries:
        query = Query(text=query_text, metadata={"user_id": "demo-user"})
        log.info("Query: %r", query_text)

        try:
            answer = await secure_agent.answer(query)
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

        except QueryValidationError as e:
            log.warning("Query rejected (would be HTTP 400): %s", e)
        except QueryRateLimitedError as e:
            log.warning("Query rejected (would be HTTP 429): %s", e)
        except Exception:
            log.exception("Error processing query")

        log.info("-" * 60)

    log.info("=== Results Summary ===")
    log.info("Metrics logged to /tmp/rag_metrics.jsonl")
    log.info("To view metrics: cat /tmp/rag_metrics.jsonl | jq .")

    health_server.shutdown()
    health_server.server_close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(0)
