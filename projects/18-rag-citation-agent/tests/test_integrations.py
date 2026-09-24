"""Behavioural tests for the circuit-breaker wrappers around Retriever, LLM,
and SearchFallback: the one piece of integrations.py with logic worth
pinning (the corpus/mock/file stubs are thin, deterministic data)."""

from __future__ import annotations

import asyncio

from agent import ABSTAIN_TEXT, ContextItem, Query
from integrations import (
    CircuitBreakerLLM,
    CircuitBreakerRetriever,
    CircuitBreakerSearchFallback,
)

ITEM = ContextItem(id="x", source_uri="u", title="t", snippet="s", score=0.9)


class FlakyRetriever:
    def __init__(self) -> None:
        self.calls = 0

    async def retrieve(self, query: Query, k: int) -> list[ContextItem]:
        self.calls += 1
        raise RuntimeError("vector db unreachable")


class FlakyLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, prompt: str) -> str:
        self.calls += 1
        raise RuntimeError("provider 500")


class FlakySearch:
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, query: Query) -> list[ContextItem]:
        self.calls += 1
        raise RuntimeError("search api timeout")


class WorkingRetriever:
    async def retrieve(self, query: Query, k: int) -> list[ContextItem]:
        return [ITEM]


def test_retriever_passes_through_results_while_closed():
    breaker = CircuitBreakerRetriever(WorkingRetriever(), min_calls=5)
    result = asyncio.run(breaker.retrieve(Query(text="q"), k=3))
    assert result == [ITEM]


def test_retriever_open_returns_empty_context_not_raise():
    wrapped = FlakyRetriever()
    breaker = CircuitBreakerRetriever(wrapped, min_calls=2, failure_threshold=0.5)

    for _ in range(2):
        try:
            asyncio.run(breaker.retrieve(Query(text="q"), k=3))
        except RuntimeError:
            pass

    calls_before = wrapped.calls
    result = asyncio.run(breaker.retrieve(Query(text="q"), k=3))
    assert result == []
    assert wrapped.calls == calls_before  # short-circuited, never called through


def test_llm_open_abstains_instead_of_raising():
    wrapped = FlakyLLM()
    breaker = CircuitBreakerLLM(wrapped, min_calls=2, failure_threshold=0.5)

    for _ in range(2):
        try:
            asyncio.run(breaker.complete("prompt"))
        except RuntimeError:
            pass

    calls_before = wrapped.calls
    result = asyncio.run(breaker.complete("prompt"))
    assert result == ABSTAIN_TEXT
    assert wrapped.calls == calls_before


def test_search_fallback_open_returns_no_results():
    wrapped = FlakySearch()
    breaker = CircuitBreakerSearchFallback(wrapped, min_calls=2, failure_threshold=0.5)

    for _ in range(2):
        try:
            asyncio.run(breaker.search(Query(text="q")))
        except RuntimeError:
            pass

    calls_before = wrapped.calls
    result = asyncio.run(breaker.search(Query(text="q")))
    assert result == []
    assert wrapped.calls == calls_before


def test_single_failure_does_not_open_circuit_below_min_calls():
    wrapped = FlakyLLM()
    breaker = CircuitBreakerLLM(wrapped, min_calls=5, failure_threshold=0.5)
    try:
        asyncio.run(breaker.complete("prompt"))
    except RuntimeError:
        pass
    # Still closed: next call should reach the wrapped LLM and raise again,
    # not silently abstain.
    try:
        asyncio.run(breaker.complete("prompt"))
        raised = False
    except RuntimeError:
        raised = True
    assert raised is True
    assert wrapped.calls == 2
