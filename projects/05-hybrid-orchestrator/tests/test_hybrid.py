"""Tests for hybrid routing and the local-Ollama HTTP client.

Both backends are mocked throughout: :class:`LocalOllamaClient` gets a
mock ``requests``-style session (no real HTTP calls), and
:class:`HybridRouter` gets plain :class:`~unittest.mock.MagicMock`
objects standing in for the local/cloud clients. The
graceful-fallback-when-Ollama-unavailable path gets its own dedicated
tests since it's the scenario every non-Mac machine (and any Mac without
Ollama running) hits on every single call.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests
from hybrid_router import (
    BACKEND_CLOUD,
    BACKEND_LOCAL,
    HybridRouter,
)
from local_ollama import LocalOllamaClient, OllamaUnavailableError

# --------------------------------------------------------------------------
# LocalOllamaClient: HTTP behavior, mocked at the session boundary.
# --------------------------------------------------------------------------


def make_session(get_result=None, get_error=None, post_result=None, post_error=None):
    """Build a mock requests-style session with configurable get/post outcomes."""
    session = MagicMock()

    if get_error is not None:
        session.get.side_effect = get_error
    else:
        session.get.return_value = get_result or MagicMock(raise_for_status=MagicMock())

    if post_error is not None:
        session.post.side_effect = post_error
    else:
        session.post.return_value = post_result or MagicMock(raise_for_status=MagicMock())

    return session


def test_ollama_is_available_true_when_server_responds() -> None:
    session = make_session()
    client = LocalOllamaClient(session=session)
    assert client.is_available() is True


def test_ollama_is_available_false_on_connection_error() -> None:
    """The core Windows scenario: Ollama isn't installed/running at all."""
    session = make_session(get_error=requests.exceptions.ConnectionError("refused"))
    client = LocalOllamaClient(session=session)
    assert client.is_available() is False


def test_ollama_is_available_false_on_timeout() -> None:
    session = make_session(get_error=requests.exceptions.Timeout("timed out"))
    client = LocalOllamaClient(session=session)
    assert client.is_available() is False


def test_ollama_generate_returns_stripped_text() -> None:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"response": "  hi from llama  "}
    session = make_session(post_result=response)

    client = LocalOllamaClient(session=session)
    assert client.generate("hello") == "hi from llama"


def test_ollama_generate_raises_ollama_unavailable_on_connection_error() -> None:
    session = make_session(post_error=requests.exceptions.ConnectionError("refused"))
    client = LocalOllamaClient(session=session)

    with pytest.raises(OllamaUnavailableError):
        client.generate("hello")


def test_ollama_generate_raises_on_empty_prompt() -> None:
    client = LocalOllamaClient(session=make_session())
    with pytest.raises(OllamaUnavailableError):
        client.generate("   ")


def test_ollama_generate_raises_on_missing_response_field() -> None:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {}
    session = make_session(post_result=response)

    client = LocalOllamaClient(session=session)
    with pytest.raises(OllamaUnavailableError):
        client.generate("hello")


# --------------------------------------------------------------------------
# HybridRouter: routing decisions.
# --------------------------------------------------------------------------


def make_local(
    available: bool, generate_result: str | None = None, generate_error=None
) -> MagicMock:
    local = MagicMock()
    local.is_available.return_value = available
    if generate_error is not None:
        local.generate.side_effect = generate_error
    else:
        local.generate.return_value = generate_result
    return local


def make_cloud(generate_result: str | None = None, generate_error=None) -> MagicMock:
    cloud = MagicMock()
    if generate_error is not None:
        cloud.generate.side_effect = generate_error
    else:
        cloud.generate.return_value = generate_result
    return cloud


def test_decide_routes_short_prompt_local_when_available() -> None:
    router = HybridRouter(make_local(available=True), make_cloud(), max_local_chars=100)
    backend, reason = router.decide("short prompt")
    assert backend == BACKEND_LOCAL
    assert reason == "short_prompt_local_capable"


def test_decide_routes_long_prompt_to_cloud_even_when_local_available() -> None:
    router = HybridRouter(make_local(available=True), make_cloud(), max_local_chars=5)
    backend, reason = router.decide("this prompt is definitely too long")
    assert backend == BACKEND_CLOUD
    assert reason == "prompt_exceeds_local_threshold"


def test_decide_routes_to_cloud_when_local_unavailable_regardless_of_length() -> None:
    """The Windows / no-Ollama case: every prompt routes to cloud."""
    router = HybridRouter(make_local(available=False), make_cloud(), max_local_chars=1000)
    backend, reason = router.decide("even a very short prompt")
    assert backend == BACKEND_CLOUD
    assert reason == "ollama_unavailable"


# --------------------------------------------------------------------------
# HybridRouter.generate(): end-to-end behavior including fallback.
# --------------------------------------------------------------------------


def test_generate_uses_local_when_available_and_prompt_is_short() -> None:
    local = make_local(available=True, generate_result="local answer")
    cloud = make_cloud(generate_result="cloud answer")
    router = HybridRouter(local, cloud, max_local_chars=100)

    result = router.generate("hi")

    assert result.text == "local answer"
    assert result.backend_used == BACKEND_LOCAL
    assert result.fallback_occurred is False
    cloud.generate.assert_not_called()


def test_generate_falls_back_to_cloud_when_ollama_unavailable() -> None:
    """Graceful fallback: Ollama isn't running (e.g. Windows) -> cloud, no crash."""
    local = make_local(available=False)
    cloud = make_cloud(generate_result="cloud answer")
    router = HybridRouter(local, cloud, max_local_chars=1000)

    result = router.generate("hi")

    assert result.text == "cloud answer"
    assert result.backend_used == BACKEND_CLOUD
    assert result.fallback_occurred is True
    assert result.reason == "ollama_unavailable"
    local.generate.assert_not_called()  # never attempted, availability failed first


def test_generate_falls_back_to_cloud_when_local_fails_mid_generation() -> None:
    """Ollama passes the availability probe but then fails on generate()."""
    local = make_local(available=True, generate_error=OllamaUnavailableError("model not pulled"))
    cloud = make_cloud(generate_result="cloud saved the day")
    router = HybridRouter(local, cloud, max_local_chars=100)

    result = router.generate("hi")

    assert result.text == "cloud saved the day"
    assert result.backend_used == BACKEND_CLOUD
    assert result.fallback_occurred is True
    assert result.reason == "local_failed_mid_generation"
    local.generate.assert_called_once_with("hi")
    cloud.generate.assert_called_once_with("hi")


def test_generate_routes_long_prompt_to_cloud_without_calling_local_generate() -> None:
    local = make_local(available=True, generate_result="should not be used")
    cloud = make_cloud(generate_result="cloud answer")
    router = HybridRouter(local, cloud, max_local_chars=3)

    result = router.generate("a much longer prompt than the threshold")

    assert result.backend_used == BACKEND_CLOUD
    assert result.fallback_occurred is False  # deliberate routing, not a fallback
    local.generate.assert_not_called()


def test_generate_propagates_error_when_cloud_also_fails() -> None:
    local = make_local(available=False)
    cloud = make_cloud(generate_error=RuntimeError("cloud is down too"))
    router = HybridRouter(local, cloud, max_local_chars=1000)

    with pytest.raises(RuntimeError):
        router.generate("hi")
