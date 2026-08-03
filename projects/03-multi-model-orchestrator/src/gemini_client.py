"""Thin wrapper around the Google Gen AI (Gemini) SDK.

Keeps the SDK object construction and call site in one small, easily
mockable class so orchestration code never touches ``google.genai``
directly.

Uses the current ``google-genai`` SDK, where a single
``genai.Client`` is constructed once and the model id is supplied
per-call via ``client.models.generate_content(model=..., contents=...)``.
The legacy ``google-generativeai`` package (``genai.configure()`` plus a
per-model ``GenerativeModel`` object) is end-of-life and no longer used.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


class GeminiClientError(RuntimeError):
    """Raised when the Gemini client cannot produce a response."""


class GeminiClient:
    """Minimal, injectable wrapper around the Gemini SDK.

    Args:
        api_key: Gemini API key. Falls back to the ``GEMINI_API_KEY``
            environment variable if not provided.
        model: Model id to use. Falls back to the ``GEMINI_MODEL``
            environment variable, then :data:`DEFAULT_GEMINI_MODEL`.
        client: Pre-constructed SDK client object exposing
            ``models.generate_content(model=..., contents=...)``.
            Primarily used by tests to inject a mock instead of hitting
            the real API.

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
            self._client = self._build_client(resolved_key)

    @staticmethod
    def _build_client(api_key: str) -> Any:
        """Construct the real ``google.genai`` client.

        Imported lazily so the SDK is only required at runtime, not at
        test-collection time.

        Args:
            api_key: Gemini API key to authenticate the client with.

        Returns:
            A configured ``google.genai.Client`` instance. The model id
            is not bound here — it is passed per request in
            :meth:`generate`.
        """
        from google import genai

        return genai.Client(api_key=api_key)

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
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
        except Exception as exc:
            logger.error("Gemini generate_content failed: %s", exc)
            raise GeminiClientError(f"Gemini request failed: {exc}") from exc

        text = getattr(response, "text", None)
        if not text:
            raise GeminiClientError("Gemini response contained no text.")

        return text.strip()
