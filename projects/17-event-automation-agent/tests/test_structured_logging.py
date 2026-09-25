"""Behavioural tests for JSON formatting and correlation-ID binding."""

from __future__ import annotations

import io
import json
import logging

from structured_logging import JsonFormatter, bind


def _capture(name: str) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger, stream


def _last_record(stream: io.StringIO) -> dict:
    lines = stream.getvalue().strip().splitlines()
    return json.loads(lines[-1])


def test_emits_valid_json_with_core_fields():
    logger, stream = _capture("test.core")
    logger.info("hello world")
    record = _last_record(stream)
    assert record["message"] == "hello world"
    assert record["level"] == "INFO"
    assert record["component"] == "test.core"
    assert "timestamp" in record


def test_includes_extra_fields_verbatim():
    logger, stream = _capture("test.extra")
    logger.info("workflow ran", extra={"workflow": "contain", "attempt": 2})
    record = _last_record(stream)
    assert record["workflow"] == "contain"
    assert record["attempt"] == 2


def test_includes_exception_info():
    logger, stream = _capture("test.exc")
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("failed")
    record = _last_record(stream)
    assert "ValueError: boom" in record["exc_info"]


def test_bind_attaches_context_to_every_call():
    logger, stream = _capture("test.bind")
    adapter = bind(logger, correlation_id="evt-123")
    adapter.info("processing")
    record = _last_record(stream)
    assert record["correlation_id"] == "evt-123"


def test_bind_merges_call_site_extra_with_bound_context():
    logger, stream = _capture("test.merge")
    adapter = bind(logger, correlation_id="evt-123")
    adapter.info("running workflow", extra={"workflow": "contain"})
    record = _last_record(stream)
    assert record["correlation_id"] == "evt-123"
    assert record["workflow"] == "contain"


def test_bind_does_not_leak_context_across_different_adapters():
    logger, stream = _capture("test.isolated")
    bind(logger, correlation_id="evt-1").info("first")
    bind(logger, correlation_id="evt-2").info("second")
    lines = [json.loads(line) for line in stream.getvalue().strip().splitlines()]
    assert lines[0]["correlation_id"] == "evt-1"
    assert lines[1]["correlation_id"] == "evt-2"
