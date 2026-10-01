"""
Realistic integration stubs for Project 18 (RAG Citation Agent).

These implementations use local storage and mock backends to simulate
production systems without requiring external services. Swap them out
in a single line when you integrate real vector DBs, LLM providers,
and search APIs.

Usage:
    from integrations import FileBasedRetriever, MockLLM, SearchFallbackStub, FileMetricsLogger

    retriever = FileBasedRetriever()
    llm = MockLLM()  # or LLMWithClaude() if ANTHROPIC_API_KEY is set
    fallback = SearchFallbackStub()
    metrics = FileMetricsLogger("metrics.jsonl")
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from agent import (
    ABSTAIN_TEXT,
    LLM,
    Answer,
    ContextItem,
    Query,
    RAGAgent,
    Retriever,
    SearchFallback,
)
from circuit_breaker import CircuitBreaker
from rate_limiter import RateLimiter
from structured_logging import bind

log = logging.getLogger("integrations")


# --------------------------------------------------------------------------- #
# Retriever: File-Based with Hardcoded Corpus
# --------------------------------------------------------------------------- #


class FileBasedRetriever(Retriever):
    """Returns results from a hardcoded document corpus.

    In production, embed the query, search a vector DB (Pinecone, Weaviate,
    Milvus), or run a hybrid BM25 + embedding search. This stub provides
    deterministic results for testing.

    To use a custom corpus, subclass and override the `_build_corpus()` method.
    """

    def __init__(self) -> None:
        self.corpus = self._build_corpus()

    async def retrieve(self, query: Query, k: int) -> list[ContextItem]:
        """Return up to k context items from corpus.

        Ranking is naive word-overlap, not embedding similarity: a query is
        a full sentence ("What is Python used for?") and a title/snippet is
        a handful of words, so testing whether one is a *substring* of the
        other -- the previous approach -- essentially never matches. Counting
        shared words is still not real relevance ranking, but it actually
        returns results. Replace with embedding similarity when integrated.
        """
        query_words = set(re.findall(r"\w+", query.text.lower()))
        scored = []

        for item in self.corpus:
            title_words = set(re.findall(r"\w+", item.title.lower()))
            snippet_words = set(re.findall(r"\w+", item.snippet.lower()))
            title_overlap = len(query_words & title_words)
            snippet_overlap = len(query_words & snippet_words)

            if title_overlap or snippet_overlap:
                score = min(0.5 + 0.1 * title_overlap + 0.05 * snippet_overlap, 1.0)
                scored.append((item, score))

        # Sort by score descending, take top k
        sorted_results = sorted(scored, key=lambda x: x[1], reverse=True)
        results = [
            ContextItem(
                id=f"item-{i}",
                source_uri=item.source_uri,
                title=item.title,
                snippet=item.snippet,
                score=score,
            )
            for i, (item, score) in enumerate(sorted_results[:k])
        ]

        bind(log, correlation_id=query.id).info("retrieve", extra={"k": k, "results": len(results)})
        return results

    def _build_corpus(self) -> list[ContextItem]:
        """Build a hardcoded document corpus.

        Replace with real corpus loading (from DB, S3, vector store metadata)
        when integrated. For now, use a few example Wikipedia snippets.
        """
        return [
            ContextItem(
                id="wiki-python",
                source_uri="https://en.wikipedia.org/wiki/Python_(programming_language)",
                title="Python (programming language)",
                snippet=(
                    "Python is a high-level, interpreted programming language known for its simplicity "
                    "and readability. Created by Guido van Rossum and released in 1991, Python emphasizes "
                    "code readability with the use of significant indentation. It supports multiple programming "
                    "paradigms: procedural, object-oriented, and functional. Python has a comprehensive standard "
                    "library and is widely used in web development, data science, artificial intelligence, and automation."
                ),
                score=0.85,
            ),
            ContextItem(
                id="wiki-async",
                source_uri="https://docs.python.org/3/library/asyncio.html",
                title="asyncio — Asynchronous I/O",
                snippet=(
                    "asyncio is a library to write concurrent code using the async/await syntax. "
                    "It provides primitives for writing single-threaded concurrent code using coroutines, "
                    "multiplexing I/O access over sockets and other resources, and running TCP and UDP servers. "
                    "It is designed on top of a single-threaded event loop. The event loop runs one coroutine at a time "
                    "and provides scheduling for other tasks when the current coroutine is waiting on I/O."
                ),
                score=0.80,
            ),
            ContextItem(
                id="wiki-rag",
                source_uri="https://en.wikipedia.org/wiki/Retrieval-augmented_generation",
                title="Retrieval-Augmented Generation",
                snippet=(
                    "Retrieval-augmented generation (RAG) is a technique that enhances large language models (LLMs) "
                    "by retrieving relevant documents or passages from an external knowledge base and using them as context "
                    "for generation. RAG systems typically consist of a retrieval module (which finds relevant documents) "
                    "and a generation module (which produces text based on the retrieved context). This approach helps address "
                    "hallucination problems in LLMs by grounding responses in factual, retrievable information."
                ),
                score=0.82,
            ),
            ContextItem(
                id="wiki-citations",
                source_uri="https://en.wikipedia.org/wiki/Citation",
                title="Citation",
                snippet=(
                    "A citation is a reference to a published or unpublished source. Citations are used in academic, "
                    "journalistic, and professional writing to acknowledge the use of another's words, ideas, or research. "
                    "They serve multiple purposes: giving credit to original authors, allowing readers to verify claims, "
                    "and enabling further research. Common citation formats include APA, MLA, Chicago, and Harvard styles."
                ),
                score=0.75,
            ),
            ContextItem(
                id="wiki-typescript",
                source_uri="https://en.wikipedia.org/wiki/TypeScript",
                title="TypeScript",
                snippet=(
                    "TypeScript is a free and open-source high-level programming language developed by Microsoft. "
                    "It is a superset of JavaScript that adds static types. TypeScript is designed for the development "
                    "of large applications and can be transpiled to JavaScript. It was first made public in October 2012 "
                    "and version 4.1 was released on November 19, 2020. TypeScript adds optional static typing to JavaScript."
                ),
                score=0.70,
            ),
        ]


# --------------------------------------------------------------------------- #
# LLM: Mock + Optional Claude SDK Integration
# --------------------------------------------------------------------------- #


class MockLLM(LLM):
    """Returns canned responses. Use for testing without external APIs.

    Responses cite the [1] and [2] markers from the prompt, which are grounded
    against the actual context by the RAG agent.
    """

    async def complete(self, prompt: str) -> str:
        """Return a mock answer with citations."""
        log.info("llm.complete (mock) prompt_len=%d", len(prompt))

        # Simulate different answers based on query keywords
        if "python" in prompt.lower():
            return (
                "Python is a high-level interpreted programming language known for its simplicity and readability. [1] "
                "It supports asynchronous programming through the asyncio library. [2]"
            )
        elif "rag" in prompt.lower() or "retrieval" in prompt.lower():
            return (
                "Retrieval-augmented generation enhances language models by retrieving relevant documents and using them as context. [3] "
                "Citations are essential for grounding responses in factual information. [4]"
            )
        elif "type" in prompt.lower():
            return "TypeScript is a superset of JavaScript that adds static types. [5]"
        else:
            return "I don't know."


class LLMWithClaude(LLM):
    """Calls Claude via the Anthropic SDK.

    Requires ANTHROPIC_API_KEY environment variable. Falls back to MockLLM if not available.

    Usage:
        llm = LLMWithClaude()  # uses Claude Sonnet by default
        # or
        llm = LLMWithClaude(model="claude-opus-5")
    """

    def __init__(self, model: str = "claude-sonnet-5") -> None:
        self.model = model
        self.client = None

        # Try to import and initialize the Anthropic SDK
        try:
            from anthropic import AsyncAnthropic

            self.client = AsyncAnthropic()
        except ImportError:
            log.warning(
                "Anthropic SDK not installed; falling back to MockLLM. "
                "Install with: pip install anthropic"
            )

    async def complete(self, prompt: str) -> str:
        """Call Claude with the grounding prompt."""
        if self.client is None:
            log.warning("Anthropic SDK not available; using mock response")
            return await MockLLM().complete(prompt)

        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(block.text for block in response.content if hasattr(block, "text"))
            log.info("llm.complete (claude) model=%s tokens=%d", self.model, response.usage.output_tokens)
            return text
        except Exception as e:  # noqa: BLE001 -- any provider failure must degrade, not crash
            log.error("llm.complete failed: %s; falling back to mock", e)
            return await MockLLM().complete(prompt)


# --------------------------------------------------------------------------- #
# Search Fallback: SearXNG + Mock
# --------------------------------------------------------------------------- #


class SearchFallbackStub(SearchFallback):
    """Tries SearXNG public instance; falls back to mock results.

    SearXNG is a privacy-respecting metasearch engine. You can use the public
    instance at https://searxng.org or run your own. Timeout is set to 3s
    to avoid hanging if the instance is slow.

    TODO(integration): Replace with your actual search API (Bing, Brave, etc.)
    and add domain restrictions to prevent prompt injection.
    """

    def __init__(
        self,
        searxng_url: str = "https://searxng.org/search",
        timeout_s: float = 3.0,
    ) -> None:
        self.searxng_url = searxng_url
        self.timeout_s = timeout_s

    async def search(self, query: Query) -> list[ContextItem]:
        """Search via SearXNG or return mock fallback."""
        # Try SearXNG first
        results = await self._search_searxng(query.text)
        bound = bind(log, correlation_id=query.id)
        if results:
            bound.info("search.fallback", extra={"source": "searxng", "results": len(results)})
            return results

        # Fallback to mock
        bound.warning("search.fallback falling back to mock results")
        return self._mock_results(query.text)

    async def _search_searxng(self, query: str) -> list[ContextItem]:
        """Query SearXNG and convert results to ContextItem.

        Returns empty list if network error or timeout.
        """
        try:
            params = urllib.parse.urlencode({
                "q": query,
                "format": "json",
                "engine": "google",
            })
            url = f"{self.searxng_url}?{params}"

            # to_thread() needs a plain sync callable -- an async def here would
            # hand back an un-awaited coroutine and silently return nothing.
            def fetch() -> dict[str, Any]:
                with urllib.request.urlopen(url, timeout=self.timeout_s) as response:
                    return json.loads(response.read())

            data = await asyncio.wait_for(asyncio.to_thread(fetch), timeout=self.timeout_s + 1.0)

            results = []
            for i, result in enumerate(data.get("results", [])[:5]):
                results.append(
                    ContextItem(
                        id=f"search-{i}",
                        source_uri=result.get("url", ""),
                        title=result.get("title", ""),
                        snippet=result.get("content", ""),
                        score=0.7 - (i * 0.1),  # Decay by position
                    )
                )
            return results

        except Exception as e:  # noqa: BLE001 -- any search-backend failure must fall back, not crash
            log.debug("searxng search failed: %s", e)
            return []

    def _mock_results(self, query: str) -> list[ContextItem]:
        """Return mock search results."""
        return [
            ContextItem(
                id="fallback-1",
                source_uri="https://example.com/article1",
                title=f"Search result for '{query}' (mock)",
                snippet=(
                    "This is a mock search result. In production, this would come from your search API "
                    "(SearXNG, Bing, Brave, etc.). The fallback search helps improve confidence when "
                    "the primary retriever returns low-confidence results."
                ),
                score=0.65,
            )
        ]


# --------------------------------------------------------------------------- #
# Metrics Logger: File-Based
# --------------------------------------------------------------------------- #


class FileMetricsLogger:
    """Logs metrics to a JSON Lines file.

    Each metric is one line of JSON with timestamp, name, value, and tags.
    Useful for analysis and debugging; in production, send to a metrics backend
    (Prometheus, DataDog, New Relic, etc.).

    Usage:
        metrics = FileMetricsLogger("metrics.jsonl")
        metrics.observe("rag.confidence", 0.85, stage="primary")
        metrics.increment("rag.fallback")
        metrics.timing("rag.stage", 123.4, stage="retrieve")
    """

    def __init__(self, path: str | Path = "metrics.jsonl") -> None:
        self.path = Path(path)

    def observe(self, name: str, value: float, **tags: str) -> None:
        """Record a value distribution (histogram)."""
        self._write("observe", name, value, tags)

    def increment(self, name: str, **tags: str) -> None:
        """Increment a counter."""
        self._write("increment", name, 1, tags)

    def timing(self, name: str, value_ms: float, **tags: str) -> None:
        """Record a timing (in milliseconds)."""
        self._write("timing", name, value_ms, tags)

    def _write(self, kind: str, name: str, value: float, tags: dict[str, str]) -> None:
        """Append metric to file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "kind": kind,
            "name": name,
            "value": value,
            "tags": tags,
            "timestamp": time.time(),
        }
        try:
            with open(self.path, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:  # noqa: BLE001 -- a metrics write must never break the caller
            log.error("failed to write metric %s: %s", name, e)


# --------------------------------------------------------------------------- #
# Circuit Breaker Wrappers
# --------------------------------------------------------------------------- #
#
# Each wraps one of the three external dependencies (Retriever, LLM,
# SearchFallback) and degrades to the *safe* default for that dependency
# when its circuit is open, rather than raising:
#   - Retriever open  -> empty context (RAGAgent treats this as low-confidence
#                         and routes to fallback search, per the spec).
#   - LLM open        -> abstain immediately (never guess when the model
#                         backing the agent is itself unhealthy).
#   - Fallback open   -> empty search results (primary answer, if any,
#                         still returns; no cascading failure).


class CircuitBreakerRetriever(Retriever):
    """Wraps a Retriever; returns no context while its circuit is open."""

    def __init__(
        self,
        wrapped: Retriever,
        *,
        failure_threshold: float = 0.5,
        min_calls: int = 5,
        reset_after_s: float = 30.0,
        metrics: Any = None,
    ) -> None:
        self._wrapped = wrapped
        self._breaker = CircuitBreaker(
            name="retriever",
            failure_threshold=failure_threshold,
            min_calls=min_calls,
            reset_after_s=reset_after_s,
            metrics=metrics,
        )

    def allow(self) -> bool:
        """True if the circuit is not open -- for health/readiness checks."""
        return self._breaker.allow()

    async def retrieve(self, query: Query, k: int) -> list[ContextItem]:
        if not self._breaker.allow():
            bind(log, correlation_id=query.id).warning("retriever circuit open; returning empty context")
            return []
        try:
            result = await self._wrapped.retrieve(query, k)
        except Exception:
            self._breaker.record_failure()
            raise
        else:
            self._breaker.record_success()
            return result


class CircuitBreakerLLM(LLM):
    """Wraps an LLM; abstains immediately while its circuit is open.

    Abstaining (rather than raising) is deliberate: RAGAgent's error-handling
    contract propagates LLM errors to the caller (an outage should be loud),
    but a *known-bad* circuit is different from a single call failing — we
    already know retrying will fail, so return the safe answer instead of
    making every caller pay the same discovered-dead latency.
    """

    def __init__(
        self,
        wrapped: LLM,
        *,
        failure_threshold: float = 0.5,
        min_calls: int = 5,
        reset_after_s: float = 30.0,
        metrics: Any = None,
    ) -> None:
        self._wrapped = wrapped
        self._breaker = CircuitBreaker(
            name="llm",
            failure_threshold=failure_threshold,
            min_calls=min_calls,
            reset_after_s=reset_after_s,
            metrics=metrics,
        )

    def allow(self) -> bool:
        """True if the circuit is not open -- for health/readiness checks."""
        return self._breaker.allow()

    async def complete(self, prompt: str) -> str:
        # ponytail: LLM.complete() takes a bare prompt string, not a Query, so
        # there's no correlation_id to bind here without widening the core
        # interface -- add one only if per-query LLM tracing turns out to matter.
        if not self._breaker.allow():
            log.warning("llm circuit open; abstaining")
            return ABSTAIN_TEXT
        try:
            result = await self._wrapped.complete(prompt)
        except Exception:
            self._breaker.record_failure()
            raise
        else:
            self._breaker.record_success()
            return result


class CircuitBreakerSearchFallback(SearchFallback):
    """Wraps a SearchFallback; returns no results while its circuit is open."""

    def __init__(
        self,
        wrapped: SearchFallback,
        *,
        failure_threshold: float = 0.5,
        min_calls: int = 5,
        reset_after_s: float = 30.0,
        metrics: Any = None,
    ) -> None:
        self._wrapped = wrapped
        self._breaker = CircuitBreaker(
            name="search_fallback",
            failure_threshold=failure_threshold,
            min_calls=min_calls,
            reset_after_s=reset_after_s,
            metrics=metrics,
        )

    def allow(self) -> bool:
        """True if the circuit is not open -- for health/readiness checks."""
        return self._breaker.allow()

    async def search(self, query: Query) -> list[ContextItem]:
        if not self._breaker.allow():
            bind(log, correlation_id=query.id).warning("search fallback circuit open; returning no results")
            return []
        try:
            result = await self._wrapped.search(query)
        except Exception:
            self._breaker.record_failure()
            raise
        else:
            self._breaker.record_success()
            return result


# --------------------------------------------------------------------------- #
# Input Validation & Rate Limiting
# --------------------------------------------------------------------------- #

MAX_QUERY_LENGTH = 1000


class QueryValidationError(Exception):
    """A query's text violates a length or content rule."""


class QueryRateLimitedError(Exception):
    """A query was rejected because its rate-limit key is over budget."""


def validate_query_text(text: str, max_length: int = MAX_QUERY_LENGTH) -> None:
    """Reject empty or oversized query text before it reaches retrieval.

    A user's question is untrusted input by construction. Without a bound,
    a single pathologically long query inflates embedding cost, prompt
    size, and the LLM bill for no benefit -- and an empty query has no
    retrieval signal to act on at all.
    """
    if not text or not text.strip():
        raise QueryValidationError("query text is empty")
    if len(text) > max_length:
        raise QueryValidationError(f"query is {len(text)} chars, max is {max_length}")


class SecureRAGAgent:
    """Wraps a RAGAgent with input validation and per-key rate limiting.

    Not a RAGAgent subclass: it doesn't reimplement retrieve/generate/ground/
    score, it guards the one public entry point (`answer`) before delegating.
    Rate limiting is keyed by `query.metadata["user_id"]` (falling back to
    "anonymous") -- swap in an API key, tenant ID, or caller IP depending on
    what actually identifies your caller.

    Both `QueryValidationError` and `QueryRateLimitedError` propagate rather
    than being caught here: unlike an event source that can silently drop and
    move on, a synchronous request needs its caller (an HTTP handler, a CLI)
    to turn these into the right response -- a 400 for validation, a 429 for
    rate limiting.

    Usage:
        agent = SecureRAGAgent(RAGAgent(retriever, llm, fallback, ...))
        answer = await agent.answer(Query(text="...", metadata={"user_id": "u1"}))
    """

    def __init__(
        self,
        wrapped: RAGAgent,
        *,
        max_query_length: int = MAX_QUERY_LENGTH,
        capacity: float = 20.0,
        refill_per_s: float = 1.0,
        metrics: Any = None,
    ) -> None:
        self._wrapped = wrapped
        self._max_query_length = max_query_length
        self._limiter = RateLimiter(capacity=capacity, refill_per_s=refill_per_s)
        self._metrics = metrics

    async def answer(self, query: Query) -> Answer:
        validate_query_text(query.text, max_length=self._max_query_length)

        key = query.metadata.get("user_id", "anonymous")
        if not self._limiter.allow(key):
            if self._metrics is not None:
                self._metrics.increment("rag.rate_limited", user=key)
            raise QueryRateLimitedError(f"rate limit exceeded for user {key!r}")

        return await self._wrapped.answer(query)
