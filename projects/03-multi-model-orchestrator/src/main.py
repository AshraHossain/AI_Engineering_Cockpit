"""CLI entry point: run a prompt against Gemini and/or OpenAI and print a comparison."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv
from gemini_client import GeminiClient, GeminiClientError
from openai_client import OpenAIClient, OpenAIClientError
from orchestrator import ComparisonReport, ModelClient, Orchestrator

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


def build_clients(enable_gemini: bool, enable_openai: bool) -> dict[str, ModelClient]:
    """Construct the enabled clients, skipping any that fail to initialize.

    Args:
        enable_gemini: Whether to attempt constructing a Gemini client.
        enable_openai: Whether to attempt constructing an OpenAI client.

    Returns:
        A mapping of provider name to client, containing only the
        providers that were both enabled and successfully constructed
        (e.g. had an API key present).
    """
    clients: dict[str, ModelClient] = {}

    if enable_gemini:
        try:
            clients["gemini"] = GeminiClient()
        except GeminiClientError as exc:
            logger.warning("Skipping Gemini: %s", exc)

    if enable_openai:
        try:
            clients["openai"] = OpenAIClient()
        except OpenAIClientError as exc:
            logger.warning("Skipping OpenAI: %s", exc)

    return clients


def print_report(report: ComparisonReport) -> None:
    """Print a human-readable comparison report to stdout."""
    print(f"Prompt: {report.prompt}\n")
    for result in report.results:
        print(f"--- {result.provider} ({result.latency_seconds:.3f}s) ---")
        if result.succeeded:
            print(result.response)
        else:
            print(f"[error] {result.error}")
        print()

    fastest = report.fastest()
    if fastest is not None:
        print(f"Fastest: {fastest.provider} ({fastest.latency_seconds:.3f}s)")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Compare Gemini and OpenAI responses to the same prompt."
    )
    parser.add_argument("prompt", help="Prompt to send to the enabled models.")
    parser.add_argument("--no-gemini", action="store_true", help="Disable the Gemini client.")
    parser.add_argument("--no-openai", action="store_true", help="Disable the OpenAI client.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        Process exit code: 0 on success, 1 if no clients could be built.
    """
    load_dotenv()
    configure_logging()
    args = parse_args(argv)

    clients = build_clients(enable_gemini=not args.no_gemini, enable_openai=not args.no_openai)
    if not clients:
        logger.error("No clients available (missing API keys or all disabled).")
        return 1

    report = Orchestrator(clients).run(args.prompt)
    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
