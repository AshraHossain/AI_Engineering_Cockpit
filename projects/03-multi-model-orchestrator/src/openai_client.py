"""Thin wrapper around the OpenAI SDK.

Mirrors :class:`gemini_client.GeminiClient`'s interface so orchestration
code can treat both providers identically.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


class OpenAIClientError(RuntimeError):
    """Raised when the OpenAI client cannot produce a response."""


class OpenAIClient:
    """Minimal, injectable wrapper around the OpenAI SDK.

    Args:
        api_key: OpenAI API key. Falls back to the ``OPENAI_API_KEY``
            environment variable if not provided.
        model: Model id to use. Falls back to the ``OPENAI_MODEL``
            environment variable, then :data:`DEFAULT_OPENAI_MODEL`.
        client: Pre-constructed SDK client object. Primarily used by
            tests to inject a mock instead of hitting the real API.

    Raises:
        OpenAIClientError: If no API key is available and no ``client``
            was injected.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.model_name = model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        self._client = client

        if self._client is None:
            resolved_key = api_key or os.getenv("OPENAI_API_KEY")
            if not resolved_key:
                raise OpenAIClientError("OPENAI_API_KEY is not set and no client was injected.")
            self._client = self._build_client(resolved_key)

    @staticmethod
    def _build_client(api_key: str) -> Any:
        """Construct the real ``openai.OpenAI`` client.

        Imported lazily so the SDK is only required at runtime, not at
        test-collection time.
        """
        from openai import OpenAI

        return OpenAI(api_key=api_key)

    def generate(self, prompt: str) -> str:
        """Generate a single text response for ``prompt``.

        Args:
            prompt: The user prompt to send to the model.

        Returns:
            The model's text response, stripped of leading/trailing
            whitespace.

        Raises:
            OpenAIClientError: If the prompt is empty or the SDK call
                fails / returns no usable text.
        """
        if not prompt or not prompt.strip():
            raise OpenAIClientError("Prompt must be a non-empty string.")

        try:
            response = self._client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            logger.error("OpenAI chat.completions.create failed: %s", exc)
            raise OpenAIClientError(f"OpenAI request failed: {exc}") from exc

        try:
            text = response.choices[0].message.content
        except (AttributeError, IndexError) as exc:
            raise OpenAIClientError("OpenAI response contained no text.") from exc

        if not text:
            raise OpenAIClientError("OpenAI response contained no text.")

        return text.strip()
