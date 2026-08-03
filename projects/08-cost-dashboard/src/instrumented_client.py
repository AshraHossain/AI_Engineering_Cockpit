"""Instrument a single model call once and get cost *and* latency out of it.

This is the interesting piece of this project. A raw SDK call gives you a
response; it does not give you a spend figure or a latency distribution. The
usual failure mode is to bolt those on in two unrelated places -- a timer
here, a hand-rolled token-price multiplication there -- and then discover the
two disagree about how many calls actually happened.

:class:`InstrumentedClient` wraps the call exactly once:

* :meth:`~cockpit.monitoring.performance_metrics.PerformanceTracker.measure`
  times it and books it as a success or an error,
* the *real* token counts from ``response.usage_metadata`` are priced by
  :meth:`~cockpit.monitoring.cost_tracking.CostTracker.record_usage`,
* both land in trackers the caller owns, so
  :func:`~cockpit.monitoring.dashboard.build_dashboard_snapshot` can render
  them together.

The model call itself is **injected** as ``generate_fn``. Nothing in this
module imports an SDK at module scope, so every test in this project runs
with no API key and no network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cockpit.monitoring.cost_tracking import CostTracker, ModelPricing
from cockpit.monitoring.performance_metrics import PerformanceTracker

DEFAULT_PROJECT = "08-cost-dashboard"

# (model, prompt) -> a response object exposing `.text` and `.usage_metadata`.
# Matches `client.models.generate_content(model=..., contents=...)` from the
# google-genai SDK, but deliberately narrower so a fake can satisfy it.
GenerateFn = Callable[[str, str], Any]


class ModelCallError(RuntimeError):
    """Raised when the injected ``generate_fn`` fails or returns no text."""


@dataclass(frozen=True)
class TokenUsage:
    """Token counts billed for a single model call.

    Attributes:
        input_tokens: Prompt tokens consumed.
        output_tokens: Completion tokens produced.
        total_tokens: Combined total as reported by the provider, falling
            back to ``input_tokens + output_tokens`` when absent.
    """

    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class InstrumentedResponse:
    """One model call, with its measured cost and latency attached.

    Attributes:
        model: Model the call was routed to.
        text: The generated text.
        usage: Token counts reported by the provider.
        cost_usd: Priced cost of this single call, in US dollars.
        latency_seconds: Wall-clock duration of the call, from the
            performance tracker's (injectable) clock.
    """

    model: str
    text: str
    usage: TokenUsage
    cost_usd: float
    latency_seconds: float


def _as_int(value: Any) -> int:
    """Coerce a provider-reported token count to a non-negative int.

    Providers return ``None`` for fields they did not populate, so a plain
    ``int(...)`` would raise on responses that are otherwise perfectly valid.

    Args:
        value: The raw attribute value, possibly ``None``.

    Returns:
        The value as an int, or 0 when it is missing or not numeric.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(int(value), 0)


def extract_usage(response: Any) -> TokenUsage:
    """Read token counts off a model response.

    Pure and defensive: a response with no ``usage_metadata`` yields zeros
    rather than raising, so an unusual response shape degrades the dashboard
    instead of breaking the run.

    Args:
        response: A response object, ideally exposing ``usage_metadata`` with
            ``prompt_token_count`` / ``candidates_token_count`` /
            ``total_token_count``.

    Returns:
        The extracted :class:`TokenUsage`.
    """
    usage = getattr(response, "usage_metadata", None)
    input_tokens = _as_int(getattr(usage, "prompt_token_count", 0))
    output_tokens = _as_int(getattr(usage, "candidates_token_count", 0))
    total = _as_int(getattr(usage, "total_token_count", 0))
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total or input_tokens + output_tokens,
    )


def extract_text(response: Any) -> str:
    """Read the generated text off a model response.

    Args:
        response: A response object exposing ``.text``.

    Returns:
        The response text.

    Raises:
        ModelCallError: If the response carries no usable text.
    """
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text:
        raise ModelCallError("Model returned an empty response.")
    return text


@dataclass
class InstrumentedClient:
    """A model client that records cost and latency for every call.

    Attributes:
        generate_fn: The injected ``(model, prompt) -> response`` callable.
        cost_tracker: Tracker that spend is recorded into.
        performance_tracker: Tracker that latency is recorded into.
        project: Project name attributed in the cost ledger.
        pricing: Optional price-table override handed to the cost tracker.
            Leave as ``None`` to use the framework's dated default table.
    """

    generate_fn: GenerateFn
    cost_tracker: CostTracker
    performance_tracker: PerformanceTracker
    project: str = DEFAULT_PROJECT
    pricing: dict[str, ModelPricing] | None = None

    def operation_name(self, model: str) -> str:
        """Name the performance-tracker operation for a model.

        Args:
            model: Model identifier.

        Returns:
            The operation label, e.g. ``"gemini-2.5-flash.generate"``.
        """
        return f"{model}.generate"

    def generate(self, model: str, prompt: str) -> InstrumentedResponse:
        """Run one model call, timing it and pricing its real token usage.

        A failing call is still booked as a latency sample (with outcome
        ``ERROR``), so failures show up in the dashboard's error rate instead
        of silently vanishing. No cost is recorded for a failed call --
        there are no billable token counts to price.

        Args:
            model: Model identifier to route the call to.
            prompt: The prompt to send.

        Returns:
            The :class:`InstrumentedResponse` for this call.

        Raises:
            ModelCallError: If ``generate_fn`` raises, or the response has no
                text.
            UnknownModelError: If ``model`` has no entry in the active price
                table (raised by the cost tracker, after the call succeeded).
        """
        with self.performance_tracker.measure(self.operation_name(model)) as call:
            try:
                response = self.generate_fn(model, prompt)
            except ModelCallError:
                raise
            except Exception as exc:  # SDKs raise assorted provider-specific errors
                raise ModelCallError(f"Model call to {model!r} failed: {exc}") from exc

            usage = extract_usage(response)
            call.input_tokens = usage.input_tokens
            call.output_tokens = usage.output_tokens
            text = extract_text(response)

        # `call` is the mutable handle the context manager populated on exit,
        # so the duration is available here without re-reading the tracker.
        entry = self.cost_tracker.record_usage(
            self.project,
            model,
            usage.input_tokens,
            usage.output_tokens,
            pricing=self.pricing,
        )
        return InstrumentedResponse(
            model=model,
            text=text,
            usage=usage,
            cost_usd=entry.cost_usd,
            latency_seconds=call.duration_seconds,
        )


def build_gemini_generate_fn(api_key: str) -> GenerateFn:
    """Build a live ``generate_fn`` backed by the google-genai SDK.

    The SDK import is deliberately lazy (inside the function) so importing
    this module never requires ``google-genai`` to be installed, and the
    test suite never touches the network.

    Args:
        api_key: Gemini API key to authenticate with.

    Returns:
        A ``(model, prompt) -> response`` callable suitable for
        :class:`InstrumentedClient`.
    """
    from google import genai

    client = genai.Client(api_key=api_key)

    def generate(model: str, prompt: str) -> Any:
        return client.models.generate_content(model=model, contents=prompt)

    return generate
