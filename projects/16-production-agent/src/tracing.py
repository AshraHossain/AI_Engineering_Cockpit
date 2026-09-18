"""OpenTelemetry setup: one tracer provider, exporting to Phoenix or nowhere.

Spans use OpenInference attribute names so Phoenix renders them as agent, LLM
and tool spans. The provider is passed around explicitly rather than
installed globally, so tests can each build their own with an in-memory
exporter.
"""

from __future__ import annotations

import os
from typing import Final

from openinference.semconv.resource import ResourceAttributes
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)

SERVICE_NAME: Final = "16-production-agent"
PHOENIX_ENDPOINT: Final = "http://localhost:6006/v1/traces"


def build_tracer_provider(exporter: SpanExporter | None, *, batch: bool = True) -> TracerProvider:
    """Build a tracer provider tagged for this project.

    Args:
        exporter: Where spans go; None records spans but exports nothing.
        batch: Export in a background batch (production) or synchronously
            on span end (tests).

    Returns:
        The provider. Call ``shutdown()`` on exit to flush the last batch.
    """
    resource = Resource.create(
        {"service.name": SERVICE_NAME, ResourceAttributes.PROJECT_NAME: SERVICE_NAME}
    )
    provider = TracerProvider(resource=resource)
    if exporter is not None:
        processor = BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
        provider.add_span_processor(processor)
    return provider


def phoenix_exporter() -> OTLPSpanExporter:
    """OTLP/HTTP exporter for a local Phoenix.

    Returns:
        An exporter targeting ``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`` when set,
        otherwise Phoenix's default collector on localhost.
    """
    return OTLPSpanExporter(
        endpoint=os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", PHOENIX_ENDPOINT)
    )


def to_ns(seconds: float) -> int:
    """Convert clock seconds to the integer nanoseconds OpenTelemetry expects.

    Args:
        seconds: Epoch seconds.

    Returns:
        Nanoseconds since the epoch.
    """
    return int(seconds * 1_000_000_000)
