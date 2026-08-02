"""Thin wrapper around the Google Generative AI (Gemini) SDK.

Keeps the SDK object construction and call site in one small, easily
mockable class so orchestration code never touches ``google.generativeai``
directly.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"


class GeminiClientError(RuntimeError):
    """Raised when the Gemini client cannot produce a response."""


class GeminiClient:
    """Minimal, injectable wrapper around the Gemini SDK.

    Args:
        api_key: Gemini API key. Falls back to the ``GEMINI_API_KEY``
            environment variable if not provided.
        model: Model id to use. Falls back to the ``GEMINI_MODEL``
            environment variable, then :data:`DEFAULT_GEMINI_MODEL`.
        client: Pre-constructed SDK model object. Primarily used by tests
            to inject a mock instead of hitting the real API.

    Raises:
        GeminiClientError: If no API key is available and no ``client``
            was injected.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.model_name = model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self._client = client

        if self._client is None:
            resolved_key = api_key or os.getenv("GEMINI_API_KEY")
            if not resolved_key:
                raise GeminiClientError("GEMINI_API_KEY is not set and no client was injected.")
            self._client = self._build_client(resolved_key, self.model_name)

    @staticmethod
    def _build_client(api_key: str, model_name: str) -> Any:
        """Construct the real ``google.generativeai`` model object.

        Imported lazily so the SDK is only required at runtime, not at
        test-collection time.
        """
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        return genai.GenerativeModel(model_name)

    def generate(self, prompt: str) -> str:
        """Generate a single text response for ``prompt``.

        Args:
            prompt: The user prompt to send to the model.

        Returns:
            The model's text response, stripped of leading/trailing
            whitespace.

        Raises:
            GeminiClientError: If the prompt is empty or the SDK call
                fails / returns no usable text.
        """
        if not prompt or not prompt.strip():
            raise GeminiClientError("Prompt must be a non-empty string.")

        try:
            response = self._client.generate_content(prompt)
        except Exception as exc:
            logger.error("Gemini generate_content failed: %s", exc)
            raise GeminiClientError(f"Gemini request failed: {exc}") from exc

        text = getattr(response, "text", None)
        if not text:
            raise GeminiClientError("Gemini response contained no text.")

        return text.strip()
