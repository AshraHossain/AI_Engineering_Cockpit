"""Decides whether a prompt should run against the local Ollama model or
cloud Gemini, with a documented, automatic fallback path.

Routing policy
--------------
1. **Availability first.** If the local backend is not reachable (no
   Ollama installed, not running — the normal case on Windows, or a Mac
   where the user hasn't started it), every prompt routes to cloud. This
   is checked with a cheap, short-timeout probe
   (:meth:`LocalOllamaClient.is_available`) rather than by inspecting the
   platform, so the same code path is exercised on every OS and the
   behavior is verifiable in tests without mocking ``sys.platform``.
2. **Complexity heuristic, when local is available.** Short prompts (at
   or under ``max_local_chars``) are routed to the local model, on the
   assumption that small/local models handle short, simple prompts
   adequately while longer or more complex prompts benefit from a larger
   cloud model. This is a heuristic, not a guarantee — callers who need
   different behavior can subclass or reimplement :meth:`HybridRouter.decide`.
3. **Runtime fallback.** Even when local generation is attempted, if it
   raises :class:`~local_ollama.OllamaUnavailableError` partway through
   (e.g. Ollama was available at the availability check but crashed or
   the model wasn't pulled), the router catches it and retries the same
   prompt against cloud rather than propagating the error to the caller.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Protocol

from local_ollama import OllamaUnavailableError

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_MAX_PROMPT_CHARS = 400

BACKEND_LOCAL = "local"
BACKEND_CLOUD = "cloud"


class LocalBackend(Protocol):
    """Structural interface for the local model client."""

    def is_available(self) -> bool: ...

    def generate(self, prompt: str) -> str: ...


class CloudBackend(Protocol):
    """Structural interface for the cloud model client."""

    def generate(self, prompt: str) -> str: ...


@dataclass
class HybridResult:
    """Outcome of routing and running one prompt.

    Attributes:
        text: The generated response.
        backend_used: Which backend actually produced the response —
            :data:`BACKEND_LOCAL` or :data:`BACKEND_CLOUD`.
        fallback_occurred: ``True`` if local was preferred/attempted but
            the router fell back to cloud (Ollama unavailable, or it
            failed mid-generation).
        reason: Short human-readable explanation of the routing decision,
            useful for logging/debugging.
    """

    text: str
    backend_used: str
    fallback_occurred: bool
    reason: str


class HybridRouter:
    """Routes prompts between a local Ollama client and cloud Gemini client.

    Args:
        local_client: Object implementing :class:`LocalBackend`.
        cloud_client: Object implementing :class:`CloudBackend`.
        max_local_chars: Prompts at or under this length prefer the local
            backend (when available). Falls back to the
            ``HYBRID_LOCAL_MAX_PROMPT_CHARS`` environment variable, then
            :data:`DEFAULT_LOCAL_MAX_PROMPT_CHARS`.
    """

    def __init__(
        self,
        local_client: LocalBackend,
        cloud_client: CloudBackend,
        max_local_chars: int | None = None,
    ) -> None:
        self._local = local_client
        self._cloud = cloud_client
        self.max_local_chars = max_local_chars or int(
            os.getenv("HYBRID_LOCAL_MAX_PROMPT_CHARS", DEFAULT_LOCAL_MAX_PROMPT_CHARS)
        )

    def decide(self, prompt: str) -> tuple[str, str]:
        """Decide which backend *should* handle ``prompt``, without running it.

        Args:
            prompt: The prompt that would be sent to a model.

        Returns:
            A ``(backend, reason)`` tuple. ``backend`` is
            :data:`BACKEND_LOCAL` or :data:`BACKEND_CLOUD``; ``reason``
            explains why.
        """
        if not self._local.is_available():
            return BACKEND_CLOUD, "ollama_unavailable"

        if len(prompt) <= self.max_local_chars:
            return BACKEND_LOCAL, "short_prompt_local_capable"

        return BACKEND_CLOUD, "prompt_exceeds_local_threshold"

    def generate(self, prompt: str) -> HybridResult:
        """Route ``prompt`` to the appropriate backend and generate a response.

        Args:
            prompt: The prompt to generate a response for.

        Returns:
            A :class:`HybridResult` describing which backend actually
            produced the response and whether a fallback occurred.

        Raises:
            RuntimeError: If both the (attempted) local backend and the
                cloud backend fail. This only happens when cloud itself
                is broken (e.g. bad API key) — an unavailable/failing
                local backend alone never raises, it falls back.
        """
        backend, reason = self.decide(prompt)

        if backend == BACKEND_LOCAL:
            try:
                text = self._local.generate(prompt)
                return HybridResult(
                    text=text,
                    backend_used=BACKEND_LOCAL,
                    fallback_occurred=False,
                    reason=reason,
                )
            except OllamaUnavailableError as exc:
                logger.warning(
                    "Local generation failed after passing availability check "
                    "(%s); falling back to cloud.",
                    exc,
                )
                backend, reason = BACKEND_CLOUD, "local_failed_mid_generation"

        text = self._cloud.generate(prompt)
        return HybridResult(
            text=text,
            backend_used=BACKEND_CLOUD,
            fallback_occurred=(reason != "prompt_exceeds_local_threshold"),
            reason=reason,
        )
