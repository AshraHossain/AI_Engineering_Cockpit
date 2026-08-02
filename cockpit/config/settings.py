"""Runtime settings loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from cockpit.config.use_cases import DEFAULT_USE_CASE

load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Process-wide configuration sourced from the environment.

    Attributes:
        gemini_api_key: API key for Google Gemini, if configured.
        openai_api_key: API key for OpenAI, if configured.
        anthropic_api_key: API key for Anthropic, if configured.
        ollama_host: Base URL for a local Ollama server (Mac hybrid mode).
        use_case: Active pre-configured profile name.
        log_level: Python logging level name.
    """

    gemini_api_key: str | None = field(default_factory=lambda: os.getenv("GEMINI_API_KEY") or None)
    openai_api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY") or None)
    anthropic_api_key: str | None = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None
    )
    ollama_host: str = field(
        default_factory=lambda: os.getenv("OLLAMA_HOST", "http://localhost:11434")
    )
    use_case: str = field(default_factory=lambda: os.getenv("COCKPIT_USE_CASE", DEFAULT_USE_CASE))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))


def get_settings() -> Settings:
    """Build a fresh :class:`Settings` from the current environment.

    Returns:
        A populated ``Settings`` instance.
    """
    return Settings()
