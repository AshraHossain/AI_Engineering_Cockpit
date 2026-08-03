"""Minimal "hello world" example for the Gemini API.

Demonstrates the smallest possible working call to the Gemini API: load an
API key from the environment, send one prompt, and log the model's
response. This module is intentionally self-contained -- it does not import
anything from the rest of the AI_Engineering_Cockpit repo, so it can be
copied out and run on its own.

Run it with:
    uv run python src/main.py
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_PROMPT = "In one sentence, explain what makes the Gemini API useful for developers."


class MissingAPIKeyError(RuntimeError):
    """Raised when GEMINI_API_KEY is missing or empty in the environment."""


class GeminiRequestError(RuntimeError):
    """Raised when the Gemini API call fails or returns an unusable response."""


def configure_logging(level: str | None = None) -> None:
    """Configure a minimal stdout logging setup for this script.

    This project is standalone, so it wires up a small local logging
    configuration instead of depending on any shared/platform logging
    module from the repo root.

    Args:
        level: Logging level name (e.g. "INFO", "DEBUG"). Defaults to the
            LOG_LEVEL environment variable, or "INFO" if unset.
    """
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=level or os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def get_api_key(env: dict[str, str] | None = None) -> str:
    """Read the Gemini API key from the environment.

    Args:
        env: Optional mapping to read from instead of ``os.environ``
            (mainly for testing).

    Returns:
        The value of the GEMINI_API_KEY environment variable.

    Raises:
        MissingAPIKeyError: If GEMINI_API_KEY is unset or blank.
    """
    source = env if env is not None else os.environ
    api_key = source.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise MissingAPIKeyError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return api_key


def build_client(api_key: str) -> Any:
    """Construct an authenticated Gemini API client.

    The ``google.genai`` import is done lazily inside this function so that
    unit tests can mock this function (or the module) directly without
    requiring the real SDK to make network calls.

    Note that in the ``google-genai`` SDK the model is not bound to the
    client -- it is passed per request -- so this takes no model name. See
    ``ask_gemini`` for where the model is selected.

    Args:
        api_key: The Gemini API key to authenticate with.

    Returns:
        A configured ``google.genai.Client`` instance.
    """
    from google import genai

    return genai.Client(api_key=api_key)


def ask_gemini(client: Any, prompt: str, model_name: str = DEFAULT_MODEL) -> str:
    """Send a single prompt to Gemini and return the text response.

    Args:
        client: A ``genai.Client``-like object exposing
            ``models.generate_content(model=..., contents=...)``.
        prompt: The prompt text to send.
        model_name: Name of the Gemini model to generate with.

    Returns:
        The text of the model's response.

    Raises:
        GeminiRequestError: If the SDK call raises an exception, or the
            response contains no usable text.
    """
    try:
        response = client.models.generate_content(model=model_name, contents=prompt)
    except Exception as exc:  # SDK raises assorted google.genai.errors.APIError types
        raise GeminiRequestError(f"Gemini API call failed: {exc}") from exc

    text = getattr(response, "text", None)
    if not text:
        raise GeminiRequestError("Gemini API returned an empty response.")
    return text


def run(prompt: str = DEFAULT_PROMPT, env: dict[str, str] | None = None) -> str:
    """Execute the hello-world flow and return the response text.

    Args:
        prompt: The prompt to send to Gemini.
        env: Optional environment mapping override (mainly for testing).

    Returns:
        The text of the model's response.

    Raises:
        MissingAPIKeyError: If GEMINI_API_KEY is not configured.
        GeminiRequestError: If the API call fails.
    """
    api_key = get_api_key(env)
    client = build_client(api_key)
    return ask_gemini(client, prompt)


def main(prompt: str = DEFAULT_PROMPT) -> int:
    """CLI entry point: run the hello-world example end-to-end.

    Args:
        prompt: The prompt to send to Gemini.

    Returns:
        Process exit code: 0 on success, 1 on a configuration or API error.
    """
    configure_logging()
    load_dotenv()

    try:
        answer = run(prompt)
    except MissingAPIKeyError as exc:
        logger.error("Configuration error: %s", exc)
        return 1
    except GeminiRequestError as exc:
        logger.error("Request error: %s", exc)
        return 1

    logger.info("Prompt: %s", prompt)
    logger.info("Gemini response: %s", answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
