"""CLI: ask a question, let the model call local tools, print the trace and the bill.

Examples:
    # No API key, no network -- canned offline client.
    uv run python src/main.py --dry-run

    # Manual mode against the real API: every tool call is authorized first.
    uv run python src/main.py --manual "What is 17 * 23?"

    # Automatic mode: the SDK executes the tools itself.
    uv run python src/main.py "How many days until 2026-12-25 from 2026-08-03?"
"""

from __future__ import annotations

import sys
from pathlib import Path

# The repo root is package=false by design, so it isn't installed into this
# project's venv -- put it on sys.path to import the cockpit frameworks.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import argparse
import logging
import os

from dotenv import load_dotenv

from agent import (
    AgentError,
    AgentResult,
    ToolCallMode,
    ToolUseAgent,
)
from fake_client import DEMO_QUESTION, build_demo_client
from instrumented import AgentInstrumentation
from tools import build_registry

logger = logging.getLogger(__name__)

DEFAULT_QUESTION = "What is 17 * 23, and what is that in kilometres if it were metres?"


class MissingAPIKeyError(RuntimeError):
    """Raised when GEMINI_API_KEY is missing or empty in the environment."""


def configure_logging(level: str | None = None) -> None:
    """Configure a minimal stderr logging setup for this script.

    Args:
        level: Logging level name. Defaults to the ``LOG_LEVEL`` environment
            variable, or ``"INFO"`` if unset.
    """
    logging.basicConfig(
        level=(level or os.environ.get("LOG_LEVEL", "INFO")).upper(),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        stream=sys.stderr,
    )


def get_api_key(env: dict[str, str] | None = None) -> str:
    """Read the Gemini API key from the environment.

    Args:
        env: Optional mapping to read from instead of ``os.environ``.

    Returns:
        The value of the ``GEMINI_API_KEY`` environment variable.

    Raises:
        MissingAPIKeyError: If ``GEMINI_API_KEY`` is unset or blank.
    """
    source = env if env is not None else os.environ
    api_key = source.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise MissingAPIKeyError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key, "
            "or pass --dry-run to run against the canned offline client."
        )
    return api_key


def build_live_client(api_key: str) -> object:
    """Construct the real Gemini client.

    The ``google.genai`` import is lazy so that ``--dry-run`` (and the test
    suite) never depend on the SDK being importable.

    Args:
        api_key: The Gemini API key to authenticate with.

    Returns:
        A configured ``google.genai.Client``.
    """
    from google import genai

    return genai.Client(api_key=api_key)


def format_trace(result: AgentResult) -> str:
    """Render the tool-call trace as plain text.

    Args:
        result: The finished agent run.

    Returns:
        One line per attempted tool call, or a placeholder if the model
        answered without calling anything.
    """
    if not result.invocations:
        return "  (no tools were called)"

    lines: list[str] = []
    for index, invocation in enumerate(result.invocations, start=1):
        args = ", ".join(f"{key}={value!r}" for key, value in invocation.args.items())
        if not invocation.authorized:
            status = "REFUSED (not in registry)"
        elif invocation.error is not None:
            status = f"ERROR: {invocation.error}"
        else:
            status = f"-> {invocation.result}"
        redaction = " [output redacted]" if invocation.redacted else ""
        lines.append(
            f"  {index}. {invocation.name}({args}) "
            f"[{invocation.duration_seconds:.4f}s]{redaction}\n     {status}"
        )
    return "\n".join(lines)


def print_result(result: AgentResult) -> None:
    """Print the answer, the trace, and the cost/latency summary.

    Args:
        result: The finished agent run.
    """
    print(f"Question: {result.question}")
    print(f"Mode:     {result.mode.value} | model: {result.model}")
    print()
    print("Answer:")
    print(f"  {result.answer}")
    print()
    print(f"Tool trace ({len(result.invocations)} call(s)):")
    print(format_trace(result))
    print()
    print(
        f"Stopped because: {result.stop_reason.value} "
        f"| model round trips: {result.model_calls}"
        + (" | answer read as a refusal" if result.refused else "")
    )
    print()
    print("Cost & latency:")
    for line in result.report.render().splitlines():
        print(f"  {line}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Args:
        argv: Argument vector, or ``None`` to read ``sys.argv``.

    Returns:
        The parsed namespace.
    """
    parser = argparse.ArgumentParser(
        description="Ask a question and let the model call local tools to answer it."
    )
    parser.add_argument(
        "question",
        nargs="?",
        default=None,
        help="The question to answer. Defaults to a built-in demo question.",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Use manual function calling: authorize each proposed tool call before running it.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run against a canned offline client -- no API key and no network needed.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model id to call (default: gemini-2.5-flash).",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Maximum model round trips before giving up.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument vector, or ``None`` to read ``sys.argv``.

    Returns:
        Process exit code: 0 on success, 1 on a configuration or run error.
    """
    load_dotenv()
    configure_logging()
    args = parse_args(argv)

    mode = ToolCallMode.MANUAL if args.manual else ToolCallMode.AUTOMATIC

    if args.dry_run:
        client: object = build_demo_client(manual=args.manual)
        question = args.question or DEMO_QUESTION
        if args.question:
            logger.warning(
                "--dry-run replays a canned script, so the answer will describe the "
                "demo question rather than yours."
            )
    else:
        try:
            client = build_live_client(get_api_key())
        except MissingAPIKeyError as exc:
            logger.error("Configuration error: %s", exc)
            return 1
        question = args.question or DEFAULT_QUESTION

    agent_kwargs: dict[str, object] = {
        "client": client,
        "registry": build_registry(),
        "instrumentation": AgentInstrumentation(),
    }
    if args.model:
        agent_kwargs["model"] = args.model
    if args.max_iterations is not None:
        agent_kwargs["max_iterations"] = args.max_iterations

    try:
        agent = ToolUseAgent(**agent_kwargs)  # type: ignore[arg-type]
        result = agent.run(question, mode=mode)
    except (AgentError, ValueError) as exc:
        logger.error("Agent run failed: %s", exc)
        return 1

    print_result(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
