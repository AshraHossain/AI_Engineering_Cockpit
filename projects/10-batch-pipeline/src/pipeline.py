"""Bulk-process many records without letting one bad record kill the run.

The four things that make a batch job survivable in production, wired to the
cockpit's Tier 1 utils and Tier 2 monitoring rather than re-implemented here:

* **Rate limiting** -- every record acquires a token from a
  :class:`~cockpit.utils.rate_limiting.RateLimiter` before it is processed,
  so a 5,000-record file cannot machine-gun the provider into a 429 storm.
* **Retries** -- each attempt is wrapped in
  :func:`~cockpit.utils.error_handling.retry_with_backoff`, which retries
  only :class:`~cockpit.utils.error_handling.TransientError`. A
  ``ValueError`` from bad input is a bug, not a blip, and is not retried.
* **Partial-failure tolerance** -- a record that exhausts its retries is
  recorded as a failure and the loop moves on. The batch always finishes and
  always reports which records did not.
* **Cost accounting** -- real token counts from each result are priced into a
  ``CostTracker``, and each attempt is timed into a ``PerformanceTracker``.

The work itself is **injected** as ``process_fn``. Nothing here imports an
SDK at module scope, so the whole test suite runs offline.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from cockpit.monitoring.cost_tracking import CostTracker, ModelPricing
from cockpit.monitoring.performance_metrics import Measurement, PerformanceTracker
from cockpit.utils.error_handling import ConfigurationError, TransientError, retry_with_backoff

logger = logging.getLogger(__name__)

DEFAULT_PROJECT = "10-batch-pipeline"
DEFAULT_OPERATION = "batch.process"
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_INPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "inputs.jsonl"

_OFFLINE_CHARS_PER_TOKEN = 4
_NEGATIVE_CUES = ("died", "crash", "stopped", "never", "done with", "too slow", "without any")
_POSITIVE_CUES = ("early", "spotless", "best", "clear", "good value", "quieter", "no argument")


class RateLimitTimeoutError(RuntimeError):
    """Raised when a record could not acquire a rate-limit token in time."""


class RateLimiterLike(Protocol):
    """The slice of :class:`RateLimiter` this pipeline depends on.

    Declared as a Protocol so tests can inject a deterministic fake limiter
    without subclassing or monkeypatching the real one.
    """

    def try_acquire(self, tokens: int = 1) -> bool:
        """Attempt to consume tokens without blocking.

        Args:
            tokens: Number of tokens to consume.

        Returns:
            True if the tokens were available and consumed.
        """
        ...

    def acquire(self, tokens: int = 1, timeout_seconds: float | None = None) -> bool:
        """Block until tokens are available or a timeout elapses.

        Args:
            tokens: Number of tokens to consume.
            timeout_seconds: Maximum time to wait; ``None`` waits forever.

        Returns:
            True if the tokens were acquired before the timeout.
        """
        ...


@dataclass(frozen=True)
class InputRecord:
    """One row of the input file.

    Attributes:
        record_id: Stable identifier, used to report failures.
        text: The text to process.
    """

    record_id: str
    text: str


@dataclass(frozen=True)
class ProcessOutput:
    """What an injected ``process_fn`` returns for one record.

    Attributes:
        text: The produced output (a label, a summary, whatever the job is).
        model: Model the work was billed against, for cost attribution.
        input_tokens: Prompt tokens consumed.
        output_tokens: Completion tokens produced.
    """

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


ProcessFn = Callable[[InputRecord], ProcessOutput]


class RecordStatus(StrEnum):
    """Terminal state of a single record.

    Attributes:
        SUCCEEDED: The record produced an output within its retry budget.
        FAILED: The record exhausted its retries or raised a non-retryable
            error. The batch continued regardless.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class RecordResult:
    """The outcome of processing one record.

    Attributes:
        record_id: Identifier of the record.
        status: Whether it succeeded or failed.
        attempts: How many times ``process_fn`` was invoked for it.
        latency_seconds: Duration of its final attempt.
        output: The produced output, or ``None`` when the record failed.
        cost_usd: Priced cost of the record, 0.0 when it failed.
        error: ``"ExceptionType: message"`` when it failed, else ``None``.
    """

    record_id: str
    status: RecordStatus
    attempts: int
    latency_seconds: float
    output: ProcessOutput | None = None
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """Whether this record completed successfully.

        Returns:
            True when :attr:`status` is :attr:`RecordStatus.SUCCEEDED`.
        """
        return self.status is RecordStatus.SUCCEEDED


@dataclass(frozen=True)
class BatchResult:
    """Aggregate outcome of one batch run.

    Attributes:
        results: Per-record outcomes, in input order.
        total_cost_usd: Sum of the priced cost of every successful record.
        elapsed_seconds: Wall-clock duration of the whole run, from the
            injected clock.
        throttled_count: How many records had to wait on the rate limiter.
    """

    results: tuple[RecordResult, ...]
    total_cost_usd: float
    elapsed_seconds: float
    throttled_count: int = 0

    @property
    def succeeded(self) -> tuple[RecordResult, ...]:
        """Records that completed successfully.

        Returns:
            The successful results, in input order.
        """
        return tuple(r for r in self.results if r.succeeded)

    @property
    def failed(self) -> tuple[RecordResult, ...]:
        """Records that did not complete.

        Returns:
            The failed results, in input order.
        """
        return tuple(r for r in self.results if not r.succeeded)

    @property
    def errors(self) -> tuple[tuple[str, str], ...]:
        """The error list a caller needs to triage a partial run.

        Returns:
            ``(record_id, error_message)`` pairs for every failed record.
        """
        return tuple((r.record_id, r.error or "unknown error") for r in self.failed)

    @property
    def total_attempts(self) -> int:
        """Total ``process_fn`` invocations across every record.

        Returns:
            The summed attempt count, including retries.
        """
        return sum(r.attempts for r in self.results)

    @property
    def records_per_second(self) -> float:
        """Throughput of the run.

        Returns:
            Records processed per second, or 0.0 when no measurable time
            elapsed.
        """
        if self.elapsed_seconds <= 0.0:
            return 0.0
        return len(self.results) / self.elapsed_seconds


@dataclass(frozen=True)
class PipelineConfig:
    """Knobs governing a batch run.

    Attributes:
        project: Project name attributed in the cost ledger.
        operation: Operation name recorded in the performance tracker.
        max_attempts: Total attempts per record, including the first.
        initial_delay_seconds: Delay before the first retry. Tests set this
            to ~1ms so the suite does not actually sleep.
        backoff_multiplier: Factor applied to the delay after each retry.
        rate_limiter: Optional limiter every record must acquire from.
        acquire_timeout_seconds: How long a record may block on the limiter
            before it is failed. ``None`` waits indefinitely.
        pricing: Optional price-table override for cost accounting.
    """

    project: str = DEFAULT_PROJECT
    operation: str = DEFAULT_OPERATION
    max_attempts: int = 3
    initial_delay_seconds: float = 0.5
    backoff_multiplier: float = 2.0
    rate_limiter: RateLimiterLike | None = None
    acquire_timeout_seconds: float | None = 30.0
    pricing: dict[str, ModelPricing] | None = None


@dataclass
class _AttemptState:
    """Mutable per-record scratchpad shared with the retried callable.

    Attributes:
        attempts: Number of times ``process_fn`` has been invoked.
        measurement: Handle for the most recent timed attempt, whose
            duration the tracker fills in when the attempt ends -- success
            or failure.
    """

    attempts: int = 0
    measurement: Measurement | None = None

    @property
    def latency_seconds(self) -> float:
        """Duration of the most recent attempt.

        Returns:
            The measured duration, or 0.0 if no attempt was ever started.
        """
        return self.measurement.duration_seconds if self.measurement else 0.0


def load_records(path: Path = DEFAULT_INPUT_PATH, limit: int | None = None) -> list[InputRecord]:
    """Load input records from a JSON Lines file.

    Args:
        path: Path to the ``.jsonl`` file. Blank lines are skipped.
        limit: If given, keep only the first N records.

    Returns:
        The parsed records, in file order.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ConfigurationError: If a line is not a JSON object with ``id`` and
            ``text`` fields.
    """
    records: list[InputRecord] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"{path.name} line {line_number} is not valid JSON.") from exc
        if not isinstance(payload, dict) or "id" not in payload or "text" not in payload:
            raise ConfigurationError(
                f"{path.name} line {line_number} needs both an 'id' and a 'text' field."
            )
        records.append(InputRecord(record_id=str(payload["id"]), text=str(payload["text"])))
        if limit is not None and len(records) >= limit:
            break
    return records


def _await_capacity(config: PipelineConfig) -> bool:
    """Acquire one rate-limit token, blocking only if the bucket is empty.

    Args:
        config: The run configuration holding the limiter and its timeout.

    Returns:
        True if the call had to wait for capacity, False if a token was
        immediately available (or no limiter is configured).

    Raises:
        RateLimitTimeoutError: If no token became available before
            ``acquire_timeout_seconds`` elapsed.
    """
    limiter = config.rate_limiter
    if limiter is None or limiter.try_acquire():
        return False
    if not limiter.acquire(timeout_seconds=config.acquire_timeout_seconds):
        raise RateLimitTimeoutError(
            f"No rate-limit capacity within {config.acquire_timeout_seconds}s."
        )
    return True


def _build_attempt(
    record: InputRecord,
    process_fn: ProcessFn,
    config: PipelineConfig,
    performance_tracker: PerformanceTracker,
    state: _AttemptState,
) -> Callable[[], ProcessOutput]:
    """Wrap one record's work in timing and retry-with-backoff.

    Args:
        record: The record to process.
        process_fn: The injected unit of work.
        config: Retry and operation-naming configuration.
        performance_tracker: Tracker each attempt is timed into.
        state: Scratchpad the attempt counter and timing handle are written
            to, so the caller can read them after a failure too.

    Returns:
        A zero-argument callable that runs the record and returns its output,
        retrying transient failures.
    """

    @retry_with_backoff(
        max_attempts=config.max_attempts,
        initial_delay_seconds=config.initial_delay_seconds,
        backoff_multiplier=config.backoff_multiplier,
        retry_on=(TransientError,),
    )
    def attempt() -> ProcessOutput:
        state.attempts += 1
        with performance_tracker.measure(config.operation) as call:
            # Stored immediately so a raising attempt still leaves a handle
            # behind; the tracker fills in its duration on the way out.
            state.measurement = call
            output = process_fn(record)
            call.input_tokens = output.input_tokens
            call.output_tokens = output.output_tokens
        return output

    return attempt


def run_batch(
    records: Sequence[InputRecord],
    process_fn: ProcessFn,
    *,
    cost_tracker: CostTracker,
    performance_tracker: PerformanceTracker,
    config: PipelineConfig | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> BatchResult:
    """Process every record, tolerating failures and throttling the rate.

    The loop never raises on a per-record failure: an exhausted retry budget,
    a non-retryable exception, or a rate-limit timeout all produce a
    :class:`RecordResult` with :attr:`RecordStatus.FAILED` and the run
    continues. Callers inspect :attr:`BatchResult.errors` afterwards.

    Args:
        records: Records to process, in order.
        process_fn: The injected unit of work for a single record.
        cost_tracker: Tracker successful records are priced into.
        performance_tracker: Tracker every attempt is timed into.
        config: Run configuration. Defaults to :class:`PipelineConfig`.
        clock: Monotonic clock used for the run's elapsed time. Injectable so
            throughput assertions are exact in tests.

    Returns:
        The aggregate :class:`BatchResult`.
    """
    settings = config or PipelineConfig()
    started_at = clock()
    results: list[RecordResult] = []
    total_cost = 0.0
    throttled = 0

    for record in records:
        state = _AttemptState()
        attempt = _build_attempt(record, process_fn, settings, performance_tracker, state)
        try:
            if _await_capacity(settings):
                throttled += 1
            output = attempt()
        except Exception as exc:  # deliberately broad: one bad record must not kill the run
            logger.warning(
                "Record %s failed after %d attempt(s): %s",
                record.record_id,
                state.attempts,
                exc,
            )
            results.append(
                RecordResult(
                    record_id=record.record_id,
                    status=RecordStatus.FAILED,
                    attempts=state.attempts,
                    latency_seconds=state.latency_seconds,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        entry = cost_tracker.record_usage(
            settings.project,
            output.model,
            output.input_tokens,
            output.output_tokens,
            pricing=settings.pricing,
        )
        total_cost += entry.cost_usd
        results.append(
            RecordResult(
                record_id=record.record_id,
                status=RecordStatus.SUCCEEDED,
                attempts=state.attempts,
                latency_seconds=state.latency_seconds,
                output=output,
                cost_usd=entry.cost_usd,
            )
        )

    return BatchResult(
        results=tuple(results),
        total_cost_usd=total_cost,
        elapsed_seconds=max(clock() - started_at, 0.0),
        throttled_count=throttled,
    )


def make_offline_process_fn(
    model: str = DEFAULT_MODEL,
    flaky_ids: Sequence[str] = (),
    failing_ids: Sequence[str] = (),
) -> ProcessFn:
    """Build a deterministic offline ``process_fn`` for ``--dry-run``.

    Classification is a crude keyword rule, not a model -- it exists so the
    pipeline's plumbing can be demonstrated end-to-end with no API key. Token
    counts are derived from the input length, so two dry runs produce
    identical cost figures.

    Args:
        model: Model name to attribute costs to.
        flaky_ids: Record ids that raise :class:`TransientError` on their
            first attempt only, to demonstrate retries succeeding.
        failing_ids: Record ids that always raise, to demonstrate
            partial-failure tolerance.

    Returns:
        A ``InputRecord -> ProcessOutput`` callable.
    """
    flaky = set(flaky_ids)
    failing = set(failing_ids)
    seen: dict[str, int] = {}

    def process(record: InputRecord) -> ProcessOutput:
        seen[record.record_id] = seen.get(record.record_id, 0) + 1
        if record.record_id in failing:
            raise ValueError(f"record {record.record_id} is malformed")
        if record.record_id in flaky and seen[record.record_id] == 1:
            raise TransientError(f"simulated 429 on record {record.record_id}")

        lowered = record.text.lower()
        if any(cue in lowered for cue in _NEGATIVE_CUES):
            label = "negative"
        elif any(cue in lowered for cue in _POSITIVE_CUES):
            label = "positive"
        else:
            label = "neutral"

        input_tokens = max(1, len(record.text) // _OFFLINE_CHARS_PER_TOKEN)
        return ProcessOutput(
            text=label,
            model=model,
            input_tokens=input_tokens,
            output_tokens=len(label) // _OFFLINE_CHARS_PER_TOKEN + 1,
        )

    return process


def build_gemini_process_fn(api_key: str, model: str = DEFAULT_MODEL) -> ProcessFn:
    """Build a live ``process_fn`` backed by the google-genai SDK.

    Every SDK exception is re-raised as
    :class:`~cockpit.utils.error_handling.TransientError` so the pipeline's
    retry policy applies. That is deliberately coarse: distinguishing a
    retryable 429 from a permanent 400 needs provider-specific error
    inspection, which is out of scope for an example project.

    Args:
        api_key: Gemini API key to authenticate with.
        model: Model to send each record to.

    Returns:
        A ``InputRecord -> ProcessOutput`` callable.
    """
    from google import genai

    client = genai.Client(api_key=api_key)
    prompt_template = (
        "Classify the sentiment of this customer review as exactly one word -- "
        "positive, negative, or neutral. Review: {text}"
    )

    def process(record: InputRecord) -> ProcessOutput:
        try:
            response: Any = client.models.generate_content(
                model=model, contents=prompt_template.format(text=record.text)
            )
        except Exception as exc:  # SDKs raise assorted provider-specific errors
            raise TransientError(f"model call failed for {record.record_id}: {exc}") from exc

        usage = getattr(response, "usage_metadata", None)
        text = getattr(response, "text", None)
        if not text:
            raise TransientError(f"empty response for record {record.record_id}")
        return ProcessOutput(
            text=text.strip(),
            model=model,
            input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
        )

    return process
