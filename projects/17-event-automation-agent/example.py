#!/usr/bin/env python3
"""
End-to-end example of Project 17 (Event Automation Agent) with integration stubs.

Shows how to:
1. Create an event source (file-based)
2. Define trigger rules
3. Set up workflows with retries and idempotency
4. Handle dead letters and metrics

Run with:
    python example.py

This will:
1. Create example events.jsonl with sample security events
2. Process each event through trigger rules → workflows
3. Log metrics and dead letters to console
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from agent import (
    AutomationAgent,
    EventSource,
    LoggingMetrics,
    RetryPolicy,
    TriggerEvaluator,
    TriggerRule,
    WorkflowExecutor,
)
from health import HealthChecker, serve_health
from integrations import (
    CircuitBreakerWorkflow,
    EnrichmentWorkflow,
    FileDeadLetterQueue,
    FileEventSource,
    FileIdempotencyStore,
    LogWorkflow,
    RateLimitedEventSource,
    SlackNotifierWorkflow,
    ValidatingEventSource,
)
from shutdown import install_signal_handlers
from structured_logging import configure_json_logging

# One JSON object per log line -- grep any event's correlation_id across
# every component it touched. Set EXAMPLE_LOG_FORMAT=text for the old
# human-readable format while developing.
if os.environ.get("EXAMPLE_LOG_FORMAT") == "text":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
else:
    configure_json_logging()

log = logging.getLogger("example")


def create_example_events(path: Path) -> None:
    """Write sample security events to a file."""
    events = [
        {
            "id": "alert-001",
            "source": "edr",
            "type": "malware.detected",
            "payload": {"observable": "C:\\malware.exe", "confidence": 0.98},
        },
        {
            "id": "alert-002",
            "source": "siem",
            "type": "auth.failed_logins",
            "payload": {"target": "admin@corp.local", "attempt_count": 10},
        },
        {
            "id": "alert-003",
            "source": "cloudtrail",
            "type": "s3.suspicious_access",
            "payload": {
                "bucket": "prod-data",
                "actor": "unknown-principal",
                "actions": ["s3:GetObject"],
            },
        },
        {
            # Oversized on purpose: demonstrates ValidatingEventSource dropping
            # a payload that could never be processed safely, rather than
            # letting it reach trigger evaluation, workflows, or the DLQ.
            "id": "alert-004",
            "source": "edr",
            "type": "malware.detected",
            "payload": {"dump": "x" * 20_000},
        },
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.writelines(json.dumps(event) + "\n" for event in events)

    log.info("Created example events in %s", path)


def define_triggers() -> TriggerEvaluator:
    """Define rules mapping events to workflows.

    Example triggers:
    - Malware detected → enrich + contain
    - Failed logins → enrich + log (not contain, because it's a failed auth)
    - Suspicious S3 access → enrich + log
    """

    rules = [
        TriggerRule(
            name="malware_detected",
            matches=lambda e: e.type == "malware.detected",
            workflows=("enrich", "log"),
        ),
        TriggerRule(
            name="failed_auth",
            matches=lambda e: "failed_logins" in e.type or "auth" in e.source,
            workflows=("enrich", "log"),
        ),
        TriggerRule(
            name="suspicious_api",
            matches=lambda e: "suspicious" in e.type.lower(),
            workflows=("log", "enrich"),
        ),
    ]

    return TriggerEvaluator(rules)


async def main() -> None:
    """Run the example end-to-end."""
    log.info("=== Event Automation Agent Example ===")

    # Paths for integration stubs
    events_file = Path("/tmp/events.jsonl")
    claims_file = Path("/tmp/claims.json")
    dlq_file = Path("/tmp/dead_letters.jsonl")

    # Clean up old runs (optional)
    for f in [claims_file, dlq_file]:
        f.unlink(missing_ok=True)

    # Create example events
    create_example_events(events_file)

    # Setup metrics first: everything below reports to the same instance
    metrics = LoggingMetrics()

    # Setup integrations. Source is layered: validate payload shape/size,
    # then rate-limit per Event.source, before anything reaches the agent.
    source: EventSource = FileEventSource(events_file)
    source = ValidatingEventSource(source, metrics=metrics)
    source = RateLimitedEventSource(source, capacity=50, refill_per_s=10, metrics=metrics)
    store = FileIdempotencyStore(claims_file)
    dlq = FileDeadLetterQueue(dlq_file)
    triggers = define_triggers()

    # Wrap each workflow with a circuit breaker: if a workflow's downstream
    # starts failing repeatedly, it fails fast to the dead-letter queue
    # instead of burning through retries against a service that's down.
    workflows = [
        LogWorkflow(),
        CircuitBreakerWorkflow(EnrichmentWorkflow(), metrics=metrics),
        CircuitBreakerWorkflow(SlackNotifierWorkflow(), metrics=metrics),
    ]

    # Health checks read the same breakers workflows run through, so
    # readiness reflects the agent's actual ability to process events --
    # not just "the process is up."
    health = HealthChecker()
    for wf in workflows:
        if isinstance(wf, CircuitBreakerWorkflow):
            health.register(wf.name, wf.allow)
    health_server = serve_health(health, port=0)
    log.info(
        "health endpoints ready",
        extra={"healthz": f"http://127.0.0.1:{health_server.server_port}/healthz",
               "readyz": f"http://127.0.0.1:{health_server.server_port}/readyz"},
    )

    # Setup executor with retry policy (same `metrics` the breakers above report to)
    executor = WorkflowExecutor(
        workflows=workflows,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_s=0.1),
        dlq=dlq,
        metrics=metrics,
    )

    # Create and run the agent. Argument order is
    # (source, evaluator, executor, store, metrics).
    agent = AutomationAgent(source, triggers, executor, store, metrics)

    # install_signal_handlers wires SIGTERM/SIGINT to a clean task.cancel(),
    # which AutomationAgent.run() already turns into a graceful drain (its
    # `finally` block awaits in-flight work before the cancellation
    # propagates). The 5s timeout below exercises that exact same code path
    # so this demo finishes on its own -- a real SIGTERM would do the same.
    task = asyncio.create_task(agent.run())
    install_signal_handlers(task)

    log.info("Starting agent... (will run for 5 seconds, or until Ctrl+C)")
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except (TimeoutError, asyncio.CancelledError):
        log.info("Shutting down: draining in-flight work")

    health_server.shutdown()
    health_server.server_close()

    # Print results
    log.info("=== Results ===")
    log.info("Claims store: %s", dict(store.claims))
    dead_letters = dlq_file.read_text().splitlines() if dlq_file.exists() else []
    log.info("Dead letters: %d", len(dead_letters))
    log.info("Metric counters: %s", metrics.counters)
    log.info("Check %s for dead-letter details", dlq_file)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(0)
