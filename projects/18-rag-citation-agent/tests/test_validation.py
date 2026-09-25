"""Behavioural tests for query validation and SecureRAGAgent's rate limiting."""

from __future__ import annotations

import asyncio

import pytest
from agent import Answer, Query
from integrations import (
    MAX_QUERY_LENGTH,
    QueryRateLimitedError,
    QueryValidationError,
    SecureRAGAgent,
    validate_query_text,
)


def test_normal_text_passes():
    validate_query_text("What is Python used for?")


def test_empty_text_rejected():
    with pytest.raises(QueryValidationError):
        validate_query_text("")


def test_whitespace_only_text_rejected():
    with pytest.raises(QueryValidationError):
        validate_query_text("   \n\t  ")


def test_text_within_limit_passes():
    validate_query_text("x" * MAX_QUERY_LENGTH)


def test_text_over_limit_rejected():
    with pytest.raises(QueryValidationError):
        validate_query_text("x" * (MAX_QUERY_LENGTH + 1))


def test_custom_max_length_is_honored():
    validate_query_text("x" * 10, max_length=10)
    with pytest.raises(QueryValidationError):
        validate_query_text("x" * 11, max_length=10)


class StubAgent:
    """Records every query it was asked to answer."""

    def __init__(self) -> None:
        self.calls: list[Query] = []

    async def answer(self, query: Query) -> Answer:
        self.calls.append(query)
        return Answer(text="ok", citations=(), confidence=1.0, used_fallback=False, raw_context_ids=())


def test_secure_agent_delegates_valid_queries():
    stub = StubAgent()
    agent = SecureRAGAgent(stub, capacity=10, refill_per_s=0.0)
    query = Query(text="hello")
    answer = asyncio.run(agent.answer(query))
    assert answer.text == "ok"
    assert stub.calls == [query]


def test_secure_agent_rejects_empty_query_before_delegating():
    stub = StubAgent()
    agent = SecureRAGAgent(stub, capacity=10, refill_per_s=0.0)
    with pytest.raises(QueryValidationError):
        asyncio.run(agent.answer(Query(text="")))
    assert stub.calls == []


def test_secure_agent_rejects_oversized_query_before_delegating():
    stub = StubAgent()
    agent = SecureRAGAgent(stub, max_query_length=10, capacity=10, refill_per_s=0.0)
    with pytest.raises(QueryValidationError):
        asyncio.run(agent.answer(Query(text="x" * 11)))
    assert stub.calls == []


def test_secure_agent_rate_limits_per_user():
    def alice_query() -> Query:
        return Query(text="hi", metadata={"user_id": "alice"})

    stub = StubAgent()
    agent = SecureRAGAgent(stub, capacity=2, refill_per_s=0.0)
    asyncio.run(agent.answer(alice_query()))
    asyncio.run(agent.answer(alice_query()))
    with pytest.raises(QueryRateLimitedError):
        asyncio.run(agent.answer(alice_query()))
    assert len(stub.calls) == 2


def test_secure_agent_tracks_users_independently():
    stub = StubAgent()
    agent = SecureRAGAgent(stub, capacity=1, refill_per_s=0.0)
    asyncio.run(agent.answer(Query(text="hi", metadata={"user_id": "alice"})))
    # Bob has his own budget; alice being exhausted doesn't affect him.
    asyncio.run(agent.answer(Query(text="hi", metadata={"user_id": "bob"})))
    assert len(stub.calls) == 2


def test_secure_agent_defaults_to_anonymous_bucket_without_user_id():
    stub = StubAgent()
    agent = SecureRAGAgent(stub, capacity=1, refill_per_s=0.0)
    asyncio.run(agent.answer(Query(text="hi")))
    with pytest.raises(QueryRateLimitedError):
        asyncio.run(agent.answer(Query(text="hi again")))


def test_secure_agent_reports_rate_limit_hits_to_metrics():
    events: list[tuple[str, dict]] = []

    class Recorder:
        def increment(self, name: str, **tags: str) -> None:
            events.append((name, tags))

    stub = StubAgent()
    agent = SecureRAGAgent(stub, capacity=1, refill_per_s=0.0, metrics=Recorder())
    asyncio.run(agent.answer(Query(text="hi", metadata={"user_id": "alice"})))
    with pytest.raises(QueryRateLimitedError):
        asyncio.run(agent.answer(Query(text="hi", metadata={"user_id": "alice"})))
    assert ("rag.rate_limited", {"user": "alice"}) in events
