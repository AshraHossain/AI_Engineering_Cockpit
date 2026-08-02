"""Runs a prompt against one or more model clients and compares results.

The orchestrator does not know anything about Gemini or OpenAI SDKs
directly: it accepts any object exposing a ``.generate(prompt: str) ->
str`` method, which keeps it trivially testable with mocks.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger(__name__)


class ModelClient(Protocol):
    """Structural interface every orchestrated client must satisfy."""

    def generate(self, prompt: str) -> str: ...


@dataclass
class ModelResult:
    """Outcome of running one model client against a prompt.

    Attributes:
        provider: Human-readable name of the provider (e.g. ``"gemini"``).
        response: The model's text response. ``None`` if the call failed.
        latency_seconds: Wall-clock time the call took, in seconds.
        error: The error message if the call failed, otherwise ``None``.
    """

    provider: str
    response: str | None
    latency_seconds: float
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """Whether the call completed without raising."""
        return self.error is None


@dataclass
class ComparisonReport:
    """Aggregate result of comparing multiple providers on one prompt."""

    prompt: str
    results: list[ModelResult] = field(default_factory=list)

    def fastest(self) -> ModelResult | None:
        """Return the quickest successful result, or ``None`` if none succeeded."""
        successful = [r for r in self.results if r.succeeded]
        if not successful:
            return None
        return min(successful, key=lambda r: r.latency_seconds)


class Orchestrator:
    """Runs a prompt against a configurable set of named model clients.

    Args:
        clients: Mapping of provider name to an object implementing
            :class:`ModelClient`. Only entries present here are run —
            "which providers are enabled" is expressed by what you pass
            in, not a separate flag.
    """

    def __init__(self, clients: dict[str, ModelClient]) -> None:
        self._clients = dict(clients)

    def run(self, prompt: str) -> ComparisonReport:
        """Run ``prompt`` against every configured client.

        A failure in one client does not stop the others: each result is
        captured independently, including timing and any error.

        Args:
            prompt: The prompt to send to every enabled client.

        Returns:
            A :class:`ComparisonReport` with one :class:`ModelResult` per
            configured client, in insertion order.

        Raises:
            ValueError: If no clients are configured.
        """
        if not self._clients:
            raise ValueError("Orchestrator has no clients configured.")

        report = ComparisonReport(prompt=prompt)
        for provider, client in self._clients.items():
            report.results.append(self._run_one(provider, client, prompt))
        return report

    @staticmethod
    def _run_one(provider: str, client: ModelClient, prompt: str) -> ModelResult:
        """Invoke a single client and capture timing/errors."""
        start = time.perf_counter()
        try:
            text = client.generate(prompt)
            elapsed = time.perf_counter() - start
            logger.info("%s responded in %.3fs", provider, elapsed)
            return ModelResult(provider=provider, response=text, latency_seconds=elapsed)
        except Exception as exc:
            elapsed = time.perf_counter() - start
            logger.warning("%s failed after %.3fs: %s", provider, elapsed, exc)
            return ModelResult(
                provider=provider,
                response=None,
                latency_seconds=elapsed,
                error=str(exc),
            )
