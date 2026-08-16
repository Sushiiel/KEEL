"""OpenTelemetry integration — optional, zero-cost when absent.

If the `opentelemetry` packages are installed and OTEL_EXPORTER_OTLP_ENDPOINT
is set, every verification emits one trace with a span per plane (substrate →
certificate), attributed with incident id, verdict, PN, and timing — ready for
Jaeger/Tempo/Grafana or any OTLP backend. Without them, `span()` is a no-op
context manager and KEEL runs exactly as before.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

_tracer = None
_enabled = False

try:
    from opentelemetry import trace as _trace

    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter)

        provider = TracerProvider(resource=Resource.create(
            {"service.name": os.environ.get("OTEL_SERVICE_NAME", "keel")}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        _trace.set_tracer_provider(provider)
        _enabled = True
    _tracer = _trace.get_tracer("keel")
except ImportError:                                     # otel not installed
    _tracer = None


def enabled() -> bool:
    return _enabled


@contextmanager
def span(name: str, attrs: dict[str, Any] | None = None) -> Iterator[None]:
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name) as sp:
        for k, v in (attrs or {}).items():
            try:
                sp.set_attribute(k, v)
            except Exception:
                pass
        yield
