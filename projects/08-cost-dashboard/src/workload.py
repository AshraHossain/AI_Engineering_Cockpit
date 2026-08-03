"""The prompt workload the dashboard is built from, plus an offline stand-in.

Two things live here:

* :data:`DEFAULT_WORKLOAD` -- a small, fixed batch of prompts. Fixed on
  purpose: a dashboard is only interesting if you can re-run the same
  workload and compare the numbers.
* :func:`make_offline_generate_fn` and :func:`make_offline_clock` -- a canned
  fake model and a fake clock, so ``--dry-run`` produces a fully populated,
  byte-for-byte reproducible dashboard with no API key and no network.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from instrumented_client import (
    GenerateFn,
    InstrumentedClient,
    InstrumentedResponse,
    ModelCallError,
)

logger = logging.getLogger(__name__)

DEFAULT_MODELS: tuple[str, ...] = ("gemini-2.5-flash", "gemini-2.5-flash-lite")

# Rough characters-per-token ratio used only by the offline fake.
_OFFLINE_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class WorkloadItem:
    """One named prompt in the batch.

    Attributes:
        name: Short label used in logs, e.g. ``"summarize"``.
        prompt: The prompt text sent to the model.
    """

    name: str
    prompt: str


DEFAULT_WORKLOAD: tuple[WorkloadItem, ...] = (
    WorkloadItem(
        name="summarize",
        prompt="Summarize in one sentence: a token bucket refills at a fixed rate "
        "and each request consumes one token.",
    ),
    WorkloadItem(
        name="classify",
        prompt="Classify the sentiment of this review as positive, negative, or "
        "neutral, and answer with one word: 'The battery died in two hours.'",
    ),
    WorkloadItem(
        name="extract",
        prompt="Extract every dollar amount from this text as a comma-separated "
        "list: 'We spent $4.20 on embeddings and $18.75 on generation.'",
    ),
    WorkloadItem(
        name="rewrite",
        prompt="Rewrite this sentence for a non-technical reader: 'The p95 latency "
        "regressed after we disabled response caching.'",
    ),
    WorkloadItem(
        name="explain",
        prompt="Explain in two sentences why per-call token counts are a better "
        "basis for cost accounting than request counts.",
    ),
)


@dataclass(frozen=True)
class OfflineUsage:
    """Canned stand-in for the SDK's ``usage_metadata`` object.

    Attributes:
        prompt_token_count: Prompt tokens the fake claims to have billed.
        candidates_token_count: Completion tokens the fake claims to have
            produced.
        total_token_count: Combined total.
    """

    prompt_token_count: int
    candidates_token_count: int
    total_token_count: int


@dataclass(frozen=True)
class OfflineResponse:
    """Canned stand-in for an SDK generation response.

    Attributes:
        text: The fabricated response text.
        usage_metadata: The fabricated token counts.
    """

    text: str
    usage_metadata: OfflineUsage


def make_offline_generate_fn(output_ratio: float = 0.75) -> GenerateFn:
    """Build a deterministic offline ``generate_fn``.

    Token counts are derived from the prompt length rather than randomised,
    so two ``--dry-run`` invocations produce identical cost figures. This is
    a demo fake, not a tokenizer -- it exists so the wiring can be shown
    working, not to predict real spend.

    Args:
        output_ratio: Completion tokens produced per prompt token.

    Returns:
        A ``(model, prompt) -> OfflineResponse`` callable.
    """

    def generate(model: str, prompt: str) -> OfflineResponse:
        input_tokens = max(1, len(prompt) // _OFFLINE_CHARS_PER_TOKEN)
        output_tokens = max(1, int(input_tokens * output_ratio))
        return OfflineResponse(
            text=f"[offline:{model}] canned answer for a {input_tokens}-token prompt.",
            usage_metadata=OfflineUsage(
                prompt_token_count=input_tokens,
                candidates_token_count=output_tokens,
                total_token_count=input_tokens + output_tokens,
            ),
        )

    return generate


def make_offline_clock(step_seconds: float = 0.25) -> Callable[[], float]:
    """Build a fake monotonic clock that advances a fixed step per read.

    The performance tracker reads its clock exactly twice per measured call,
    so every offline call is recorded with a latency of exactly
    ``step_seconds``. That keeps ``--dry-run`` output reproducible instead of
    reflecting how fast the machine happened to be.

    Args:
        step_seconds: Seconds to advance on each read. Must be non-negative.

    Returns:
        A zero-argument callable returning a monotonically increasing float.

    Raises:
        ValueError: If ``step_seconds`` is negative.
    """
    if step_seconds < 0:
        raise ValueError("step_seconds must be non-negative.")

    state = {"now": 0.0}

    def clock() -> float:
        current = state["now"]
        state["now"] = current + step_seconds
        return current

    return clock


def run_workload(
    client: InstrumentedClient,
    items: Sequence[WorkloadItem] = DEFAULT_WORKLOAD,
    models: Sequence[str] = DEFAULT_MODELS,
) -> list[InstrumentedResponse]:
    """Run every prompt against every model, instrumenting each call.

    Failures are tolerated: a call that raises is logged and skipped, and the
    batch continues. The failed call is still booked as an error latency
    sample by the client, so the dashboard's error rate reflects it.

    Args:
        client: The instrumented client to route calls through.
        items: Prompts to run.
        models: Models to run each prompt against.

    Returns:
        The successful responses, in call order.
    """
    responses: list[InstrumentedResponse] = []
    for model in models:
        for item in items:
            try:
                responses.append(client.generate(model, item.prompt))
            except ModelCallError:
                logger.warning("Workload item %r failed on model %s; skipping.", item.name, model)
    return responses
