"""Post new alert transitions from ``outputs/alerts.jsonl`` to a Slack incoming webhook.

Examples:
    # SLACK_WEBHOOK_URL in .env or the environment.
    uv run python src/slack_alerts.py

    # During a live rollout, every minute.
    watch -n 60 uv run python src/slack_alerts.py

Lines already posted are recorded in ``<alerts file>.posted``, so each run sends
only what is new. Posting stops at the first failure, so a ``resolved`` never
reaches Slack ahead of its ``fired``; the next run resumes from there.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parents[1]
ALERTS_PATH = PROJECT_DIR / "outputs" / "alerts.jsonl"

_logger = logging.getLogger(__name__)


def message(event: dict[str, Any]) -> dict[str, str]:
    """Slack payload for one alert transition.

    Args:
        event: One ``alerts.jsonl`` line, as written by ``alerts.jsonl_sink``.

    Returns:
        A webhook payload.
    """
    icon = ":rotating_light:" if event["state"] == "fired" else ":white_check_mark:"
    when = datetime.fromtimestamp(event["timestamp"], UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return {
        "text": (
            f"{icon} *{event['rule']}* {event['state']} for `{event['version']}`: "
            f"{event['value']:.3g} (threshold {event['threshold']:.3g}), "
            f"at {event['request_id']}, {when}"
        )
    }


def post_new(alerts: Path, webhook: str, client: httpx2.Client) -> tuple[int, bool]:
    """Post every alert line not posted before, in order.

    Args:
        alerts: The ``alerts.jsonl`` file.
        webhook: Slack incoming-webhook URL.
        client: HTTP client to post with.

    Returns:
        ``(posted, ok)``: lines posted this call, and False if a post failed.
    """
    record = alerts.with_name(alerts.name + ".posted")
    done = set(record.read_text(encoding="utf-8").splitlines()) if record.exists() else set()
    posted = 0
    with record.open("a", encoding="utf-8") as log:
        for line in alerts.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line in done:
                continue
            try:
                payload = message(json.loads(line))
            except (ValueError, KeyError, TypeError):
                _logger.warning("skipping a malformed alert line: %.80s", line)
                continue
            try:
                client.post(webhook, json=payload).raise_for_status()
            except httpx2.HTTPError as exc:
                # The URL is a secret: report the failure, not the request.
                _logger.error("Slack post failed (%s); stopping here", type(exc).__name__)
                return posted, False
            log.write(line + "\n")
            done.add(line)
            posted += 1
    return posted, True


def main(argv: Sequence[str] | None = None) -> int:
    """Post new alerts; exit 1 if a post failed, 2 if no webhook is set.

    Args:
        argv: Optional alerts file path; defaults to ``outputs/alerts.jsonl``.

    Returns:
        Process exit code.
    """
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    load_dotenv(PROJECT_DIR / ".env")
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        print("SLACK_WEBHOOK_URL is not set.", file=sys.stderr)
        return 2
    args = list(sys.argv[1:] if argv is None else argv)
    alerts = Path(args[0]) if args else ALERTS_PATH
    if not alerts.exists():
        print(f"No alerts yet ({alerts} does not exist).")
        return 0
    with httpx2.Client(timeout=10.0) as client:
        posted, ok = post_new(alerts, webhook, client)
    print(f"Posted {posted} new alert(s) to Slack.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
