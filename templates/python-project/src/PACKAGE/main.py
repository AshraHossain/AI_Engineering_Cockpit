"""{{DESCRIPTION}}

The shape here is the point, not the content. Two patterns are worth
keeping as you replace this with real work:

1. **The provider client is injected, never constructed inside the logic.**
   That is what lets the whole test suite run with no API key and no
   network, in milliseconds, deterministically.
2. **The CLI entry point is thin.** Parsing arguments and printing belong
   in ``main``; everything worth testing lives in functions that take
   arguments and return values.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"

# A generator takes a prompt and returns text. Anything matching this shape
# works -- the real SDK, a canned stub, a recorded fixture.
GenerateFn = Callable[[str], str]


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing."""


class ProviderError(RuntimeError):
    """Raised when the model provider call fails or returns nothing."""


def configure_logging(level: str | None = None) -> None:
    """Set up logging for this script.

    Args:
        level: Level name. Defaults to the LOG_LEVEL env var, else INFO.
    """
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=level or os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def get_api_key(env: dict[str, str] | None = None, var: str = "GEMINI_API_KEY") -> str:
    """Read a required API key from the environment.

    Args:
        env: Mapping to read from instead of ``os.environ`` (for testing).
        var: Environment variable name.

    Returns:
        The key.

    Raises:
        ConfigurationError: If the variable is unset or blank.
    """
    source = env if env is not None else os.environ
    key = source.get(var, "").strip()
    if not key:
        raise ConfigurationError(f"{var} is not set. Copy .env.example to .env and fill it in.")
    return key


def build_generate_fn(api_key: str, model: str = DEFAULT_MODEL) -> GenerateFn:
    """Build a real provider-backed generate function.

    The SDK import is deliberately lazy and local so tests never load it.

    Args:
        api_key: Provider API key.
        model: Model identifier.

    Returns:
        A callable taking a prompt and returning generated text.
    """

    def generate(prompt: str) -> str:
        # Imported lazily and locally so the tests never load the SDK at all.
        from google import genai

        client = genai.Client(api_key=api_key)
        try:
            response: Any = client.models.generate_content(model=model, contents=prompt)
        except Exception as exc:  # provider SDKs raise a wide variety of types
            raise ProviderError(f"Provider call failed: {exc}") from exc

        text = getattr(response, "text", None)
        if not text:
            raise ProviderError("Provider returned an empty response.")
        return str(text)

    return generate


def dry_run_generate(prompt: str) -> str:
    """Stand in for the provider so the project runs with no credentials.

    Keeping a dry-run path means the project is demonstrable the moment it
    is cloned, and gives the test suite something honest to exercise the
    CLI against.

    Args:
        prompt: The prompt that would have been sent.

    Returns:
        A canned response naming the prompt.
    """
    return f"[dry-run] would send: {prompt}"


def run(prompt: str, generate: GenerateFn) -> str:
    """Do the actual work.

    Everything worth testing lives in functions like this one: no I/O, no
    globals, dependencies passed in.

    Args:
        prompt: The prompt to send.
        generate: Injected generation function.

    Returns:
        The generated text.

    Raises:
        ValueError: If the prompt is empty.
    """
    if not prompt.strip():
        raise ValueError("prompt must not be empty.")
    return generate(prompt)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("prompt", nargs="?", default="Say hello in one sentence.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use a canned response instead of calling the provider.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 on success, 1 on a handled error.
    """
    configure_logging()
    load_dotenv()
    args = parse_args(argv)

    try:
        generate: GenerateFn
        if args.dry_run:
            generate = dry_run_generate
        else:
            generate = build_generate_fn(get_api_key(), model=args.model)
        result = run(args.prompt, generate)
    except (ConfigurationError, ProviderError, ValueError) as exc:
        logger.error("%s: %s", type(exc).__name__, exc)
        return 1

    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
