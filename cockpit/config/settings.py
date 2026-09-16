"""Runtime settings loaded from environment variables and secrets manager.

Supports multiple secret sources:
  1. Environment variables (highest priority)
  2. Encrypted .env.gpg (with GPG)
  3. Plaintext .env (development)
  4. External vaults (extensible)

To encrypt your .env file:
  python -m cockpit.config.secrets_cli encrypt
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from cockpit.config.use_cases import DEFAULT_USE_CASE
from cockpit.security.secrets_manager import get_secrets_manager

# Load from .env first (backward compat)
load_dotenv()

# Initialize secrets manager (loads encrypted/external secrets)
_secrets_manager = get_secrets_manager()
_secrets_manager.load()


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

    gemini_api_key: str | None = field(
        default_factory=lambda: _secrets_manager.get("GEMINI_API_KEY") or None
    )
    openai_api_key: str | None = field(
        default_factory=lambda: _secrets_manager.get("OPENAI_API_KEY") or None
    )
    anthropic_api_key: str | None = field(
        default_factory=lambda: _secrets_manager.get("ANTHROPIC_API_KEY") or None
    )
    ollama_host: str = field(
        default_factory=lambda: _secrets_manager.get("OLLAMA_HOST", "http://localhost:11434")
    )
    use_case: str = field(
        default_factory=lambda: _secrets_manager.get("COCKPIT_USE_CASE", DEFAULT_USE_CASE)
    )
    log_level: str = field(
        default_factory=lambda: _secrets_manager.get("LOG_LEVEL", "INFO")
    )


def get_settings() -> Settings:
    """Build a fresh :class:`Settings` from the current environment.

    Returns:
        A populated ``Settings`` instance.
    """
    return Settings()
