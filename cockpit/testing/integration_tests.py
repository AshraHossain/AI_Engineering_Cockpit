"""Example integration test pattern for cockpit-adjacent code.

Unlike ``unit_tests.py``, these examples wire multiple components together
(config + fixtures + a fake client) to demonstrate testing a small
end-to-end flow, still without any real network calls.

Run with:
    uv run pytest cockpit/testing/integration_tests.py -v
"""

from __future__ import annotations

from cockpit.config.use_cases import get_use_case_flags
from cockpit.testing.fixtures import (
    FakeApiClient,
    make_mock_openai_response,
    sample_documents,
)
from cockpit.utils.error_handling import TransientError, retry_with_backoff
from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)


def _naive_keyword_search(query: str, documents: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return documents whose text contains any query keyword (case-insensitive).

    A deliberately simple retrieval stand-in used to demonstrate an
    integration test flow (retrieve -> call model -> assert), not a real
    RAG implementation.

    Args:
        query: The search query.
        documents: Candidate documents with "id" and "text" keys.

    Returns:
        Matching documents, in their original order.
    """
    keywords = [word.lower() for word in query.split() if word]
    return [doc for doc in documents if any(kw in doc["text"].lower() for kw in keywords)]


def test_retrieval_then_generation_flow_with_fake_client() -> None:
    """Simulate a minimal RAG flow: retrieve context, then call a fake LLM."""
    documents = sample_documents()
    matches = _naive_keyword_search("feature flags", documents)
    assert matches, "expected at least one document to match 'feature flags'"

    client = FakeApiClient(responses=[make_mock_openai_response("Flags gate frameworks.")])
    prompt = f"Answer using context: {matches[0]['text']}"
    response = client.call(prompt)

    answer = response["choices"][0]["message"]["content"]
    assert answer == "Flags gate frameworks."
    assert client.calls[0] == prompt


def test_use_case_flags_integrate_with_enterprise_profile() -> None:
    """The 'enterprise' use case should enable every framework."""
    flags = get_use_case_flags("enterprise")
    assert all(flags.values())


def test_retry_with_backoff_recovers_after_transient_failures() -> None:
    """A flaky call should succeed once retries exhaust the failure count."""
    attempts = {"count": 0}

    @retry_with_backoff(max_attempts=3, initial_delay_seconds=0.0)
    def flaky_call() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TransientError("simulated transient failure")
        return "ok"

    result = flaky_call()

    assert result == "ok"
    assert attempts["count"] == 3
