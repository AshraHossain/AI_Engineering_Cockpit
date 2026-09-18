"""Token-usage and spend accounting across model calls and projects.

Two layers:

* :func:`estimate_cost` is a pure function — given a model, token counts, and
  a price table, it returns dollars. No state, no I/O, trivially testable.
* :class:`CostTracker` accumulates :class:`CostEntry` records and rolls them
  up. Module-level :func:`record_cost` / :func:`get_total_cost` /
  :func:`get_cost_by_model` delegate to a shared default tracker for
  convenience, the same way :mod:`logging` exposes a root logger.

Gated by ``feature_flags.is_enabled("monitoring")`` at the recording entry
point; the pure cost math is always available so callers can price a call
without turning monitoring on.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cockpit.config.feature_flags import is_enabled
from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)

# Prices are USD per 1,000,000 tokens.
#
# ponytail: this table is a *dated default*, not a source of truth. Provider
# pricing changes without notice, so every entry below was verified against the
# official pricing pages on PRICING_VERIFIED_DATE and callers are expected to
# override it (pass `pricing=` explicitly) rather than trust it indefinitely.
# Sources:
#   https://ai.google.dev/gemini-api/docs/pricing
#   https://developers.openai.com/api/docs/pricing
#   https://platform.claude.com/docs/en/about-claude/pricing (Anthropic
#   entries added and verified 2026-09-17; the rest of the table was not
#   re-checked then, so PRICING_VERIFIED_DATE still describes them)
PRICING_VERIFIED_DATE = "2026-08-03"


@dataclass(frozen=True)
class ModelPricing:
    """Per-token pricing for a single model.

    Attributes:
        input_per_1m: USD charged per 1,000,000 input (prompt) tokens.
        output_per_1m: USD charged per 1,000,000 output (completion) tokens.
        provider: Provider that serves the model, e.g. "google" or "openai".
    """

    input_per_1m: float
    output_per_1m: float
    provider: str


DEFAULT_PRICING: dict[str, ModelPricing] = {
    # --- Google Gemini ---
    "gemini-2.5-flash": ModelPricing(0.30, 2.50, "google"),
    "gemini-2.5-flash-lite": ModelPricing(0.10, 0.40, "google"),
    "gemini-2.5-pro": ModelPricing(1.25, 10.00, "google"),
    "gemini-3.5-flash": ModelPricing(1.50, 9.00, "google"),
    "gemini-3.5-flash-lite": ModelPricing(0.30, 2.50, "google"),
    "gemini-3.6-flash": ModelPricing(1.50, 7.50, "google"),
    # Embedding models bill input only; output rate is 0.
    "gemini-embedding-001": ModelPricing(0.15, 0.0, "google"),
    "gemini-embedding-2": ModelPricing(0.20, 0.0, "google"),
    # --- OpenAI ---
    "gpt-4o-mini": ModelPricing(0.15, 0.60, "openai"),
    "gpt-4o": ModelPricing(2.50, 10.00, "openai"),
    "gpt-5-nano": ModelPricing(0.05, 0.40, "openai"),
    "gpt-5-mini": ModelPricing(0.25, 2.00, "openai"),
    "gpt-5": ModelPricing(1.25, 10.00, "openai"),
    "o3": ModelPricing(2.00, 8.00, "openai"),
    "o3-mini": ModelPricing(1.10, 4.40, "openai"),
    "gpt-3.5-turbo": ModelPricing(0.50, 1.50, "openai"),
    # --- Anthropic ---
    "claude-opus-5": ModelPricing(5.00, 25.00, "anthropic"),
    "claude-sonnet-5": ModelPricing(2.00, 10.00, "anthropic"),
}

_TOKENS_PER_PRICING_UNIT = 1_000_000


class UnknownModelError(KeyError):
    """Raised when a model has no entry in the active price table."""


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int = 0,
    pricing: dict[str, ModelPricing] | None = None,
) -> float:
    """Price a single model call from its token counts.

    Pure function: no state is recorded and no feature flag is consulted, so
    it is safe to call for "what would this cost?" questions even with
    monitoring disabled.

    Args:
        model: Model identifier, e.g. ``"gemini-2.5-flash"``.
        input_tokens: Prompt token count. Must be non-negative.
        output_tokens: Completion token count. Must be non-negative.
        pricing: Price table to use. Defaults to :data:`DEFAULT_PRICING`;
            pass your own to override stale or negotiated rates.

    Returns:
        Cost of the call in US dollars.

    Raises:
        ValueError: If either token count is negative.
        UnknownModelError: If ``model`` is absent from the price table.
    """
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("Token counts must be non-negative.")

    table = DEFAULT_PRICING if pricing is None else pricing
    try:
        rates = table[model]
    except KeyError as exc:
        raise UnknownModelError(
            f"No pricing entry for model {model!r}. Pass pricing= with an entry for it."
        ) from exc

    input_cost = (input_tokens / _TOKENS_PER_PRICING_UNIT) * rates.input_per_1m
    output_cost = (output_tokens / _TOKENS_PER_PRICING_UNIT) * rates.output_per_1m
    return input_cost + output_cost


@dataclass(frozen=True)
class CostEntry:
    """A single recorded cost event.

    Attributes:
        timestamp: When the cost was incurred (UTC).
        project: Name of the project/use case that incurred the cost.
        model: Name of the model used.
        cost_usd: Cost of this single call in US dollars.
        input_tokens: Prompt tokens billed, when known.
        output_tokens: Completion tokens billed, when known.
    """

    timestamp: datetime
    project: str
    model: str
    cost_usd: float
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class CostTracker:
    """Accumulates cost events and rolls them up.

    Thread-safe: model calls are commonly fanned out across a thread pool, so
    appends and aggregations are guarded by a lock.

    Attributes:
        entries: All recorded events, oldest first.
    """

    entries: list[CostEntry] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def record(
        self,
        project: str,
        model: str,
        cost_usd: float,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        timestamp: datetime | None = None,
    ) -> CostEntry:
        """Record one cost event.

        Args:
            project: Project/use case that incurred the cost.
            model: Model used.
            cost_usd: Cost of the call in US dollars. Must be non-negative.
            input_tokens: Prompt tokens billed, when known.
            output_tokens: Completion tokens billed, when known.
            timestamp: Event time; defaults to now (UTC). Injectable so tests
                do not have to freeze the clock.

        Returns:
            The recorded :class:`CostEntry`.

        Raises:
            ValueError: If ``cost_usd`` is negative.
        """
        if cost_usd < 0:
            raise ValueError("cost_usd must be non-negative.")

        entry = CostEntry(
            timestamp=timestamp or datetime.now(UTC),
            project=project,
            model=model,
            cost_usd=cost_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        with self._lock:
            self.entries.append(entry)
        return entry

    def record_usage(
        self,
        project: str,
        model: str,
        input_tokens: int,
        output_tokens: int = 0,
        *,
        pricing: dict[str, ModelPricing] | None = None,
        timestamp: datetime | None = None,
    ) -> CostEntry:
        """Price a call from its token counts and record it in one step.

        Args:
            project: Project/use case that incurred the cost.
            model: Model used.
            input_tokens: Prompt token count.
            output_tokens: Completion token count.
            pricing: Optional price table override.
            timestamp: Event time; defaults to now (UTC).

        Returns:
            The recorded :class:`CostEntry`.

        Raises:
            ValueError: If a token count is negative.
            UnknownModelError: If ``model`` is absent from the price table.
        """
        cost = estimate_cost(model, input_tokens, output_tokens, pricing=pricing)
        return self.record(
            project,
            model,
            cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            timestamp=timestamp,
        )

    def total_cost(self, project: str | None = None) -> float:
        """Sum recorded costs, optionally scoped to one project.

        Args:
            project: If given, only sum entries for this project.

        Returns:
            Total cost in US dollars.
        """
        with self._lock:
            return sum(e.cost_usd for e in self.entries if project is None or e.project == project)

    def cost_by_model(self) -> dict[str, float]:
        """Break down total recorded cost per model.

        Returns:
            Mapping of model name to total cost in US dollars.
        """
        totals: dict[str, float] = {}
        with self._lock:
            for entry in self.entries:
                totals[entry.model] = totals.get(entry.model, 0.0) + entry.cost_usd
        return totals

    def cost_by_project(self) -> dict[str, float]:
        """Break down total recorded cost per project.

        Returns:
            Mapping of project name to total cost in US dollars.
        """
        totals: dict[str, float] = {}
        with self._lock:
            for entry in self.entries:
                totals[entry.project] = totals.get(entry.project, 0.0) + entry.cost_usd
        return totals

    def total_tokens(self) -> tuple[int, int]:
        """Sum billed tokens across all recorded entries.

        Returns:
            An ``(input_tokens, output_tokens)`` pair.
        """
        with self._lock:
            return (
                sum(e.input_tokens for e in self.entries),
                sum(e.output_tokens for e in self.entries),
            )

    def reset(self) -> None:
        """Drop all recorded entries."""
        with self._lock:
            self.entries.clear()


# Shared default tracker backing the module-level convenience functions.
_default_tracker = CostTracker()


def get_default_tracker() -> CostTracker:
    """Return the process-wide default :class:`CostTracker`.

    Returns:
        The tracker backing the module-level convenience functions. Tests
        should prefer constructing their own :class:`CostTracker` over
        mutating this one.
    """
    return _default_tracker


def record_cost(
    project: str,
    model: str,
    cost_usd: float,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> CostEntry | None:
    """Record a cost event on the default tracker.

    Args:
        project: Project/use case that incurred the cost.
        model: Model used.
        cost_usd: Cost of the call in US dollars.
        input_tokens: Prompt tokens billed, when known.
        output_tokens: Completion tokens billed, when known.

    Returns:
        The recorded :class:`CostEntry`, or ``None`` if the monitoring
        framework is disabled (in which case nothing is recorded).

    Raises:
        ValueError: If ``cost_usd`` is negative.
    """
    if not is_enabled("monitoring"):
        _logger.debug("Monitoring framework disabled; skipping cost record.")
        return None
    return _default_tracker.record(
        project,
        model,
        cost_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def get_total_cost(project: str | None = None) -> float:
    """Sum costs recorded on the default tracker.

    Args:
        project: If given, only sum entries for this project.

    Returns:
        Total cost in US dollars.
    """
    return _default_tracker.total_cost(project)


def get_cost_by_model() -> dict[str, float]:
    """Break down default-tracker cost per model.

    Returns:
        Mapping of model name to total cost in US dollars.
    """
    return _default_tracker.cost_by_model()
