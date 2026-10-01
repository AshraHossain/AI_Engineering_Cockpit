"""
JSON logging with correlation IDs, stdlib only.

Production debugging means grepping one ID (an Event's, here) across every
component it touched. Plain text logs make that a regex archaeology
exercise; one JSON object per line makes it `jq 'select(.correlation_id ==
"evt-123")'`.

Usage:
    from structured_logging import configure_json_logging, bind

    configure_json_logging()  # once, at process startup

    log = bind(logging.getLogger("p17.workflow"), correlation_id=event.id)
    log.info("running workflow", extra={"workflow": name, "attempt": attempt})
    # -> {"timestamp":"...","level":"INFO","component":"p17.workflow",
    #     "message":"running workflow","correlation_id":"evt-1",
    #     "workflow":"contain","attempt":1}
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

# Attributes every LogRecord already carries, plus the two Formatter adds
# at format time. logging.Logger.makeRecord raises KeyError if `extra`
# collides with any of these, so the formatter must also treat them as
# "not user context" when flattening a record into JSON.
_RESERVED_KEYS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",  # 3.12+; harmless to reserve on 3.11
    }
)


class JsonFormatter(logging.Formatter):
    """Renders each LogRecord as one JSON line.

    Any `extra={...}` passed at the call site appears verbatim in the
    output -- that's stdlib logging's own extension point, so this needs
    no dependency to thread arbitrary context through.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "component": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_KEYS:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _MergingAdapter(logging.LoggerAdapter):
    """LoggerAdapter that merges bound context with per-call `extra`.

    The stdlib LoggerAdapter.process() *replaces* kwargs["extra"] with
    self.extra, silently dropping whatever the call site passed. Merging
    is what you actually want: the bound correlation_id survives, and a
    call site can still add its own fields (workflow, attempt, ...).
    """

    def process(self, msg: Any, kwargs: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        merged = {**self.extra, **kwargs.get("extra", {})}
        kwargs["extra"] = merged
        return msg, kwargs


def configure_json_logging(level: int = logging.INFO) -> None:
    """Install the JSON formatter on the root logger.

    Call once at process startup. Idempotent -- safe to call more than
    once (re-applies the formatter rather than stacking handlers).
    """
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for handler in root.handlers:
        handler.setFormatter(JsonFormatter())


def bind(logger: logging.Logger, **context: Any) -> logging.LoggerAdapter:
    """Return a logger-shaped object that attaches `context` to every call.

    Typically `correlation_id=event.id` or `correlation_id=query.id`, so
    every log line for one request carries the same key across every
    component it passes through.
    """
    return _MergingAdapter(logger, context)
