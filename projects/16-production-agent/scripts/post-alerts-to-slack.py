#!/usr/bin/env python3
"""Post alert events from alerts.jsonl to a Slack webhook.

Usage:
    export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
    python scripts/post-alerts-to-slack.py outputs/alerts.jsonl
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import urllib.request
    import urllib.error
except ImportError:
    print("urllib not available", file=sys.stderr)
    sys.exit(1)


def format_alert(event: dict[str, Any]) -> dict[str, Any]:
    """Format an alert event as a Slack message block."""
    state = event.get("state", "unknown").upper()
    color = "#ef4444" if state == "FIRED" else "#10b981"
    emoji = "🚨" if state == "FIRED" else "✅"

    value = event.get("value", 0)
    threshold = event.get("threshold", 0)
    if isinstance(value, float):
        value = f"{value:.3g}"
    if isinstance(threshold, float):
        threshold = f"{threshold:.3g}"

    return {
        "color": color,
        "title": f"{emoji} {event.get('rule', 'unknown').title()}",
        "text": f"*{state}* for {event.get('version', '?')}",
        "fields": [
            {"title": "Value", "value": str(value), "short": True},
            {"title": "Threshold", "value": str(threshold), "short": True},
            {"title": "Request ID", "value": event.get("request_id", "–"), "short": True},
            {"title": "Time", "value": event.get("timestamp", "?"), "short": True},
        ],
        "footer": "Project 16 Alerts",
        "ts": int(datetime.fromisoformat(event.get("timestamp", datetime.now().isoformat())).timestamp())
        if "timestamp" in event
        else int(datetime.now().timestamp()),
    }


def post_to_slack(webhook: str, attachment: dict[str, Any]) -> bool:
    """POST an attachment to a Slack webhook."""
    payload = {"attachments": [attachment]}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.URLError as e:
        print(f"Failed to post to Slack: {e}", file=sys.stderr)
        return False


def main() -> int:
    """Read alerts.jsonl and post new events to Slack."""
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        print("Error: SLACK_WEBHOOK_URL env var not set", file=sys.stderr)
        return 1

    if len(sys.argv) < 2:
        print("Usage: python post-alerts-to-slack.py <alerts.jsonl>", file=sys.stderr)
        return 1

    alerts_path = Path(sys.argv[1])
    if not alerts_path.exists():
        print(f"Error: {alerts_path} not found", file=sys.stderr)
        return 1

    state_file = Path(f"{alerts_path}.state")
    seen = set()
    if state_file.exists():
        seen = set(state_file.read_text().strip().split("\n"))

    new_count = 0
    try:
        with open(alerts_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line in seen:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                attachment = format_alert(event)
                if post_to_slack(webhook, attachment):
                    seen.add(line)
                    new_count += 1
                    print(f"Posted: {event.get('rule')} ({event.get('state')})")
                else:
                    print(f"Failed to post: {event.get('rule')}", file=sys.stderr)

        state_file.write_text("\n".join(sorted(seen)))
        print(f"Total posted: {new_count}")
        return 0

    except (OSError, IOError) as e:
        print(f"Error reading alerts: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
