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
import sys
from pathlib import Path

from agent import (
    AutomationAgent,
    LoggingMetrics,
    RetryPolicy,
    TriggerEvaluator,
    TriggerRule,
    WorkflowExecutor,
)
from integrations import (
    EnrichmentWorkflow,
    FileDeadLetterQueue,
    FileEventSource,
    FileIdempotencyStore,
    LogWorkflow,
    SlackNotifierWorkflow,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

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

    # Setup integrations
    source = FileEventSource(events_file)
    store = FileIdempotencyStore(claims_file)
    dlq = FileDeadLetterQueue(dlq_file)
    triggers = define_triggers()

    # Setup workflows
    workflows = [
        LogWorkflow(),
        EnrichmentWorkflow(),
        SlackNotifierWorkflow(),
    ]

    # Setup executor with retry policy
    metrics = LoggingMetrics()
    executor = WorkflowExecutor(
        workflows=workflows,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_s=0.1),
        dlq=dlq,
        metrics=metrics,
    )

    # Create and run the agent. Argument order is
    # (source, evaluator, executor, store, metrics).
    agent = AutomationAgent(source, triggers, executor, store, metrics)

    # Run for a few seconds to process the events
    log.info("Starting agent... (will run for 5 seconds)")
    try:
        await asyncio.wait_for(agent.run(), timeout=5.0)
    except TimeoutError:
        log.info("Timeout reached; stopping agent")

    # Print results
    log.info("=== Results ===")
    log.info("Claims store: %s", dict(store.claims))
    dead_letters = dlq_file.read_text().splitlines() if dlq_file.exists() else []
    log.info("Dead letters: %d", len(dead_letters))
    log.info("Metrics: %s", dict(metrics.counters))
    log.info("Check %s for dead-letter details", dlq_file)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(0)
