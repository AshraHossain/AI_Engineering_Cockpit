"""
Realistic integration stubs for Project 17 (Event Automation Agent).

These implementations use local storage (files, JSON) to simulate production
backends without requiring external services. Swap them out in a single line
when you wire real backends (SQS, PostgreSQL, Slack, etc.).

Usage:
    from integrations import FileEventSource, FileIdempotencyStore, LogWorkflow

    source = FileEventSource("./events.jsonl")
    store = FileIdempotencyStore("./claims.json")
    workflows = [LogWorkflow(), SlackNotifier(webhook_url="...")]
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from agent import (
    Claim,
    DeadLetterQueue,
    Event,
    EventSource,
    IdempotencyStore,
    PermanentError,
    Workflow,
    WorkflowContext,
)
from circuit_breaker import CircuitBreaker
from structured_logging import bind

log = logging.getLogger("integrations")


# --------------------------------------------------------------------------- #
# Event Source (File-based for testing/dev)
# --------------------------------------------------------------------------- #


class FileEventSource(EventSource):
    """Reads events from a JSON Lines file.

    Each line is one Event, deserialized on read. Simulates a queue where
    ack() removes the line (in production, it would acknowledge to the broker).

    Usage:
        source = FileEventSource("events.jsonl")
        # In tests, write events to events.jsonl first
        async for event in source.events():
            print(event)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._processed: set[str] = set()

    async def events(self) -> AsyncIterator[Event]:
        """Yield events from file. Only yield once per ack()."""
        while True:
            if not self.path.exists():
                await asyncio.sleep(0.5)
                continue

            try:
                with open(self.path) as f:  # noqa: ASYNC230 -- stdlib-only stub; swap for aiofiles or a real queue client in production
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        data = json.loads(line)
                        event = Event(
                            id=data["id"],
                            source=data["source"],
                            type=data["type"],
                            payload=data.get("payload", {}),
                        )
                        if event.id not in self._processed:
                            yield event
            except json.JSONDecodeError as e:
                log.error("malformed event in source file", extra={"path": str(self.path), "error": str(e)})
            except FileNotFoundError:
                pass

            await asyncio.sleep(1.0)

    async def ack(self, event: Event) -> None:
        """Mark event as processed (won't re-yield it)."""
        self._processed.add(event.id)
        bind(log, correlation_id=event.id).info("event acknowledged", extra={"action": "ack"})

    async def nack(self, event: Event, reason: BaseException) -> None:
        """Un-process the event (will be re-yielded on next poll)."""
        self._processed.discard(event.id)
        bind(log, correlation_id=event.id).warning(
            "event returned for redelivery", extra={"action": "nack", "reason": type(reason).__name__}
        )


# --------------------------------------------------------------------------- #
# Idempotency Store (File-based for testing/dev)
# --------------------------------------------------------------------------- #


class FileIdempotencyStore(IdempotencyStore):
    """Tracks event claims in a JSON file.

    Each entry: event_id -> {lease_expires_at, is_settled}.
    Safe for single-process; multiple replicas will race on file writes.
    In production, use Redis (SET NX) or PostgreSQL (unique constraints).

    Usage:
        store = FileIdempotencyStore("claims.json")
        claim = await store.claim("event-123", ttl_s=60)
        if claim == Claim.ACQUIRED:
            # do work
            await store.complete("event-123")
    """

    _SETTLED = float("inf")

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._load_or_init()

    def _load_or_init(self) -> None:
        """Load existing claims file or create empty store."""
        if self.path.exists():
            try:
                with open(self.path) as f:
                    self.claims = json.load(f)
            except json.JSONDecodeError:
                self.claims = {}
        else:
            self.claims = {}

    def _save(self) -> None:
        """Persist claims to file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.claims, f, indent=2)

    async def claim(self, event_id: str, ttl_s: float) -> Claim:
        """Try to take exclusive ownership of an event."""
        self._load_or_init()  # reload in case another process updated
        expires_at = self.claims.get(event_id)
        now = time.time()

        if expires_at == self._SETTLED:
            return Claim.SETTLED
        if expires_at is not None and now < expires_at:
            return Claim.LEASED

        self.claims[event_id] = now + ttl_s
        self._save()
        bind(log, correlation_id=event_id).info("event claimed", extra={"action": "claim", "ttl_s": ttl_s})
        return Claim.ACQUIRED

    async def complete(self, event_id: str) -> None:
        """Mark event as permanently done."""
        self.claims[event_id] = self._SETTLED
        self._save()
        bind(log, correlation_id=event_id).info("event completed", extra={"action": "complete"})

    async def fail(self, event_id: str) -> None:
        """Mark event as permanently failed (dead-lettered)."""
        self.claims[event_id] = self._SETTLED
        self._save()
        bind(log, correlation_id=event_id).info("event failed", extra={"action": "fail"})

    async def release(self, event_id: str) -> None:
        """Drop lease so broker may redeliver."""
        if event_id in self.claims:
            del self.claims[event_id]
            self._save()
        bind(log, correlation_id=event_id).info("event lease released", extra={"action": "release"})


# --------------------------------------------------------------------------- #
# Dead Letter Queue (File-based for testing/dev)
# --------------------------------------------------------------------------- #


class FileDeadLetterQueue(DeadLetterQueue):
    """Writes dead letters to a JSON Lines file for triage.

    In production, send to SQS DLQ, Kafka dead-letter topic, or a database
    table analysts can query and replay from.

    Usage:
        dlq = FileDeadLetterQueue("dead_letters.jsonl")
        # Analyst inspects dead_letters.jsonl, fixes root cause, replays events
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def send(self, letter: Any) -> None:
        """Append dead letter to file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:  # noqa: ASYNC230 -- stdlib-only stub; swap for a real DLQ client in production
            data = {
                "event_id": letter.event.id,
                "workflow": letter.workflow,
                "attempts": letter.attempts,
                "error": letter.error,
                "failed_at": letter.failed_at,
                "event_payload": letter.event.payload,
            }
            f.write(json.dumps(data) + "\n")
        bind(log, correlation_id=letter.event.id).error(
            "event dead-lettered",
            extra={"workflow": letter.workflow, "attempts": letter.attempts, "error": letter.error},
        )


# --------------------------------------------------------------------------- #
# Example Workflows (Use these as templates for your integrations)
# --------------------------------------------------------------------------- #


class LogWorkflow(Workflow):
    """Dummy workflow: just log the event.

    Use this to test the automation pipeline without side effects.
    Replace with real integrations (EDR host isolation, IAM key revoke, etc.).
    """

    name = "log"

    async def run(self, ctx: WorkflowContext) -> None:
        bind(log, correlation_id=ctx.event.id).info(
            "workflow.log", extra={"attempt": ctx.attempt, "payload": ctx.event.payload}
        )


class DelayWorkflow(Workflow):
    """Workflow that sleeps for a fixed duration.

    Useful for testing retry logic and timeout behavior.
    """

    name = "delay"
    duration_s = 0.5

    async def run(self, ctx: WorkflowContext) -> None:
        bind(log, correlation_id=ctx.event.id).info(
            "workflow.delay sleeping", extra={"duration_s": self.duration_s}
        )
        await asyncio.sleep(self.duration_s)


class FailingWorkflow(Workflow):
    """Workflow that raises an exception.

    Triggers retry logic (up to max_attempts), then dead-letters.
    Useful for testing error handling.
    """

    name = "failing"

    async def run(self, ctx: WorkflowContext) -> None:
        bound = bind(log, correlation_id=ctx.event.id)
        bound.info("workflow.failing", extra={"attempt": ctx.attempt})
        if ctx.attempt < 3:
            raise RuntimeError("simulated transient failure")
        bound.info("workflow.failing succeeded after retries", extra={"attempt": ctx.attempt})


class PermanentFailWorkflow(Workflow):
    """Workflow that raises PermanentError on first attempt.

    Skips retries and goes straight to dead-letter queue.
    Use for schema validation errors, policy violations, etc.
    """

    name = "permanent_fail"

    async def run(self, ctx: WorkflowContext) -> None:
        raise PermanentError("this is a permanent error; no retries")


class SlackNotifierWorkflow(Workflow):
    """Sends a Slack message when the event is processed.

    TODO(integration): Replace `webhook_url` with real Slack webhook.
    In production:
        import urllib.request
        req = urllib.request.Request(
            self.webhook_url,
            data=json.dumps({"text": msg}).encode(),
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req)
    """

    name = "slack_notify"

    def __init__(self, webhook_url: str = "https://hooks.slack.com/services/..."):
        self.webhook_url = webhook_url

    async def run(self, ctx: WorkflowContext) -> None:
        msg = f"Event `{ctx.event.id}` ({ctx.event.type}) from {ctx.event.source}"
        bind(log, correlation_id=ctx.event.id).info("workflow.slack_notify", extra={"message": msg})
        # TODO: actually POST to webhook_url when integrated


class EnrichmentWorkflow(Workflow):
    """Enriches an event with external threat intelligence.

    TODO(integration): Call your threat-intel API (VirusTotal, Shodan,
    IP reputation service, etc.) and add results to a database.
    """

    name = "enrich"

    async def run(self, ctx: WorkflowContext) -> None:
        observable = ctx.event.payload.get("observable")
        bind(log, correlation_id=ctx.event.id).info("workflow.enrich", extra={"observable": observable})
        # TODO: call threat-intel API, store results


class CircuitBreakerWorkflow(Workflow):
    """Wraps another Workflow with a circuit breaker.

    When the wrapped workflow's downstream (EDR, IAM, SOAR API) is failing
    repeatedly, the circuit opens and every subsequent call fails fast with
    PermanentError — which WorkflowExecutor sends straight to the dead-letter
    queue instead of burning through RetryPolicy's attempts against a service
    that is already down.

    Usage:
        contain = CircuitBreakerWorkflow(ContainmentWorkflow(), metrics=metrics)
        executor = WorkflowExecutor(workflows=[contain, ...], ...)
    """

    def __init__(
        self,
        wrapped: Workflow,
        *,
        failure_threshold: float = 0.5,
        min_calls: int = 5,
        reset_after_s: float = 30.0,
        metrics: Any = None,
    ) -> None:
        self.name = wrapped.name
        self._wrapped = wrapped
        self._breaker = CircuitBreaker(
            name=wrapped.name,
            failure_threshold=failure_threshold,
            min_calls=min_calls,
            reset_after_s=reset_after_s,
            metrics=metrics,
        )

    def allow(self) -> bool:
        """True if the circuit is not open -- for health/readiness checks."""
        return self._breaker.allow()

    async def run(self, ctx: WorkflowContext) -> None:
        if not self._breaker.allow():
            raise PermanentError(
                f"circuit '{self.name}' is open ({self._breaker.time_until_retry():.0f}s "
                "until retry); downstream is unhealthy, skipping straight to dead-letter"
            )
        try:
            await self._wrapped.run(ctx)
        except PermanentError:
            # A permanent error is a bad event, not an unhealthy dependency —
            # don't count it against the circuit.
            raise
        except Exception:
            self._breaker.record_failure()
            raise
        else:
            self._breaker.record_success()


class ContainmentWorkflow(Workflow):
    """Performs automated containment (isolate host, revoke credentials, etc.).

    TODO(integration): Call your EDR/IAM/SOAR API to:
    - Isolate host (block network, kill processes)
    - Revoke IAM credentials (rotate keys, disable user)
    - Kill active sessions
    - Block C2 domains
    """

    name = "contain"

    async def run(self, ctx: WorkflowContext) -> None:
        action = ctx.event.payload.get("action", "isolate_host")
        target = ctx.event.payload.get("target", "unknown")
        bind(log, correlation_id=ctx.event.id).info(
            "workflow.contain", extra={"action": action, "target": target}
        )
        # TODO: call EDR/IAM/SOAR API for real containment
