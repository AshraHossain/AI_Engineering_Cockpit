"""Tracer provider setup and the Phoenix exporter's endpoint."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tracing import PHOENIX_ENDPOINT, build_tracer_provider, phoenix_exporter, to_ns


def test_spans_carry_the_phoenix_project_name() -> None:
    exporter = InMemorySpanExporter()
    build_tracer_provider(exporter, batch=False).get_tracer("tests").start_span("s").end()
    [span] = exporter.get_finished_spans()
    assert span.resource.attributes["openinference.project.name"] == "16-production-agent"
    assert span.resource.attributes["service.name"] == "16-production-agent"


def test_phoenix_on_localhost_is_the_default_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    assert phoenix_exporter()._endpoint == PHOENIX_ENDPOINT


def test_the_standard_variable_overrides_the_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://collector:4318/v1/traces")
    assert phoenix_exporter()._endpoint == "http://collector:4318/v1/traces"


def test_to_ns_converts_seconds_to_integer_nanoseconds() -> None:
    assert to_ns(1.5) == 1_500_000_000
