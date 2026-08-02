"""Thin HTTP client for a local Ollama server.

Ollama only ships a native background service on macOS/Linux, so on
Windows (and on any Mac where the user hasn't installed/started it) this
client must fail predictably rather than crash the whole program. Every
public method here turns connection failures into
:class:`OllamaUnavailableError` so callers (in particular
:mod:`hybrid_router`) can catch one exception type and fall back to the
cloud model.
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3"
_AVAILABILITY_TIMEOUT_SECONDS = 1.5
_GENERATE_TIMEOUT_SECONDS = 60.0


class OllamaUnavailableError(RuntimeError):
    """Raised when the local Ollama server can't be reached or errors out.

    This covers both "Ollama isn't installed/running" (connection
    refused, DNS failure, timeout) and "Ollama responded with an error"
    cases, so callers only need to handle one exception type.
    """


class LocalOllamaClient:
    """Minimal, injectable HTTP client for a local Ollama instance.

    Args:
        host: Base URL of the Ollama server. Falls back to the
            ``OLLAMA_HOST`` environment variable, then
            :data:`DEFAULT_OLLAMA_HOST`.
        model: Model name to request. Falls back to the ``OLLAMA_MODEL``
            environment variable, then :data:`DEFAULT_OLLAMA_MODEL`.
        session: Pre-constructed ``requests``-compatible session object.
            Primarily used by tests to inject a mock instead of making
            real HTTP calls.
    """

    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.host = (host or os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
        self._session = session or requests

    def is_available(self) -> bool:
        """Check whether the local Ollama server is reachable.

        This is a best-effort probe (short timeout, any exception means
        "not available") intended for routing decisions, not a
        guarantee that a subsequent :meth:`generate` call will succeed.

        Returns:
            ``True`` if the server responded successfully to
            ``GET /api/tags``, ``False`` otherwise (including if Ollama
            isn't installed, isn't running, or is unreachable).
        """
        try:
            response = self._session.get(
                f"{self.host}/api/tags", timeout=_AVAILABILITY_TIMEOUT_SECONDS
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            logger.info("Ollama not available at %s: %s", self.host, exc)
            return False
        return True

    def generate(self, prompt: str) -> str:
        """Generate a response from the local model.

        Args:
            prompt: The prompt to send to the local model.

        Returns:
            The model's text response.

        Raises:
            OllamaUnavailableError: If the prompt is empty, the server is
                unreachable, the request times out, or the server returns
                an error / unparseable response. Callers should treat
                this as "fall back to cloud", not a fatal error.
        """
        if not prompt or not prompt.strip():
            raise OllamaUnavailableError("Prompt must be a non-empty string.")

        try:
            response = self._session.post(
                f"{self.host}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False},
                timeout=_GENERATE_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.RequestException as exc:
            logger.warning("Ollama request failed (%s): %s", self.host, exc)
            raise OllamaUnavailableError(f"Could not reach Ollama at {self.host}: {exc}") from exc
        except ValueError as exc:  # invalid JSON body
            logger.warning("Ollama returned an unparseable response: %s", exc)
            raise OllamaUnavailableError("Ollama returned an unparseable response.") from exc

        text = payload.get("response")
        if not text:
            raise OllamaUnavailableError("Ollama response contained no text.")

        return text.strip()
