"""CLI entry point: route a prompt between local Ollama and cloud Gemini.

On Mac with Ollama installed and running, short prompts are served
locally and longer ones go to the cloud. On Windows (or any machine
without Ollama running), every prompt transparently falls back to the
cloud model — see ``README.md`` for the full routing/fallback
explanation.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from cloud_gemini import CloudGeminiClient, CloudGeminiError
from dotenv import load_dotenv
from hybrid_router import HybridRouter

logger = logging.getLogger(__name__)


def configure_logging(level: str | None = None) -> None:
    """Configure root logging once for CLI usage.

    Args:
        level: Log level name (e.g. ``"INFO"``). Falls back to the
            ``LOG_LEVEL`` environment variable, then ``"INFO"``.
    """
    level_name = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        stream=sys.stderr,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Route a prompt to local Ollama or cloud Gemini, with fallback."
    )
    parser.add_argument("prompt", help="Prompt to generate a response for.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        Process exit code: 0 on success, 1 if generation failed entirely.
    """
    load_dotenv()
    configure_logging()
    args = parse_args(argv)

    # Imported here (not at module scope) so the CLI still fails cleanly
    # if Ollama-related config is missing, rather than at import time.
    from local_ollama import LocalOllamaClient

    local_client = LocalOllamaClient()
    try:
        cloud_client = CloudGeminiClient()
    except CloudGeminiError as exc:
        logger.error("Cannot start: %s", exc)
        return 1

    router = HybridRouter(local_client, cloud_client)

    try:
        result = router.generate(args.prompt)
    except Exception as exc:
        logger.error("Generation failed on both backends: %s", exc)
        return 1

    print(result.text)
    logger.info(
        "backend=%s fallback=%s reason=%s",
        result.backend_used,
        result.fallback_occurred,
        result.reason,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
