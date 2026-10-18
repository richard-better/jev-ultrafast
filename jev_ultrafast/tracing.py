"""Opt-in OpenTelemetry spans written as OTLP JSON lines, one file per trace.

Set JEV_TRACE_DIR to enable. Spans carry timings, operations and outcomes; never page text,
typed values, prompts or credentials. A failed write never fails a run.
"""

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode

SERVICE = "jev-ultrafast"
_provider = None
_configured_for = None


class FileSpanExporter(SpanExporter):
    """Appends each exported batch as one OTLP JSON `resourceSpans` line to <dir>/<trace id>.jsonl."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self._lock = threading.Lock()

    def export(self, spans):
        try:
            by_trace = {}
            for span in spans:
                by_trace.setdefault(f"{span.context.trace_id:032x}", []).append(span)
            self.directory.mkdir(parents=True, exist_ok=True)
            with self._lock:
                for trace_id, group in by_trace.items():
                    line = json.dumps(_resource_spans(group), separators=(",", ":"))
                    with (self.directory / f"{trace_id}.jsonl").open("a", encoding="utf-8") as file:
                        file.write(line + "\n")
            return SpanExportResult.SUCCESS
        except Exception:
            return SpanExportResult.FAILURE

    def shutdown(self):
        pass


def _value(value):
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def _attributes(attributes):
    return [{"key": key, "value": _value(value)} for key, value in (attributes or {}).items()]


def _span(span):
    encoded = {
        "traceId": f"{span.context.trace_id:032x}",
        "spanId": f"{span.context.span_id:016x}",
        "name": span.name,
        "kind": 1,
        "startTimeUnixNano": str(span.start_time),
        "endTimeUnixNano": str(span.end_time),
        "attributes": _attributes(span.attributes),
        "status": {"code": {StatusCode.UNSET: 0, StatusCode.OK: 1, StatusCode.ERROR: 2}[span.status.status_code]},
    }
    if span.parent is not None:
        encoded["parentSpanId"] = f"{span.parent.span_id:016x}"
    if span.events:
        encoded["events"] = [
            {"name": event.name, "timeUnixNano": str(event.timestamp), "attributes": _attributes(event.attributes)}
            for event in span.events
        ]
    return encoded


def _resource_spans(spans):
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": _attributes(spans[0].resource.attributes)},
                "scopeSpans": [{"scope": {"name": "jev_ultrafast"}, "spans": [_span(s) for s in spans]}],
            }
        ]
    }


def configure(directory):
    """Write spans to `directory` from now on; None disables tracing."""
    global _provider, _configured_for
    if _provider is not None:
        _provider.shutdown()
    _provider, _configured_for = None, directory
    if directory:
        _provider = TracerProvider(resource=Resource.create({"service.name": SERVICE}))
        # Synchronous: a span is on disk when it ends, so an interrupted run keeps its trace.
        _provider.add_span_processor(SimpleSpanProcessor(FileSpanExporter(directory)))


def tracer():
    directory = os.environ.get("JEV_TRACE_DIR") or None
    if directory != _configured_for and (directory or _provider is not None):
        configure(directory)
    return _provider.get_tracer("jev_ultrafast") if _provider else trace.NoOpTracer()


@contextmanager
def span(name, parent=None, **attributes):
    """A span that records failures by exception type only; messages may quote provider or page data."""
    context = trace.set_span_in_context(parent) if parent is not None else None
    with tracer().start_as_current_span(
        name, context=context, attributes=attributes, record_exception=False, set_status_on_exception=False
    ) as current:
        try:
            yield current
        except BaseException as error:
            fail(current, error)
            raise


def fail(span, error):
    span.set_status(Status(StatusCode.ERROR, type(error).__name__))


def current():
    return trace.get_current_span()


def start_root(name, **attributes):
    """A span the caller ends explicitly, for runs that outlive any one call stack."""
    return tracer().start_span(name, attributes=attributes, context=trace.set_span_in_context(trace.INVALID_SPAN))


def wait(reason, seconds):
    with span("jev.wait", reason=reason):
        time.sleep(seconds)


@contextmanager
def within(parent):
    """Make `parent` current so spans started here nest under it; None leaves the context alone."""
    if parent is None:
        yield
        return
    with trace.use_span(parent, end_on_exit=False, record_exception=False, set_status_on_exception=False):
        yield
