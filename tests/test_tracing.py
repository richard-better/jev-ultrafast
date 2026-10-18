"""Offline contracts for the opt-in OTLP JSON file exporter. No model calls or browsers."""

import json

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExportResult

from jev_ultrafast import tracing


@pytest.fixture
def traced(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_TRACE_DIR", str(tmp_path))
    yield tmp_path
    monkeypatch.delenv("JEV_TRACE_DIR")
    tracing.configure(None)


def spans_in(directory):
    spans = []
    for path in directory.glob("*.jsonl"):
        for line in path.read_text().splitlines():
            for resource in json.loads(line)["resourceSpans"]:
                for scope in resource["scopeSpans"]:
                    spans.extend(scope["spans"])
    return spans


def attributes(span):
    return {a["key"]: next(iter(a["value"].values())) for a in span.get("attributes", [])}


def test_spans_are_written_as_otlp_json_with_hex_ids_and_parent_links(traced):
    root = tracing.start_root("jev.run", **{"jev.max_steps": 3})
    with tracing.within(root):
        with tracing.span("browser.stage", stage="observe", ratio=0.5, ready=True):
            pass
    root.end()

    files = list(traced.glob("*.jsonl"))
    assert len(files) == 1
    spans = {s["name"]: s for s in spans_in(traced)}
    run, stage = spans["jev.run"], spans["browser.stage"]
    assert files[0].name == run["traceId"] + ".jsonl"
    assert len(run["traceId"]) == 32 and len(run["spanId"]) == 16
    int(run["traceId"], 16), int(run["spanId"], 16)
    assert "parentSpanId" not in run
    assert stage["parentSpanId"] == run["spanId"] and stage["traceId"] == run["traceId"]
    assert int(stage["startTimeUnixNano"]) <= int(stage["endTimeUnixNano"])
    assert attributes(run) == {"jev.max_steps": "3"}
    assert attributes(stage) == {"stage": "observe", "ratio": 0.5, "ready": True}
    line = json.loads(files[0].read_text().splitlines()[0])
    resource = {a["key"]: a["value"] for a in line["resourceSpans"][0]["resource"]["attributes"]}
    assert resource["service.name"] == {"stringValue": "jev-ultrafast"}


def test_failures_record_the_exception_type_but_never_its_message(traced):
    with pytest.raises(RuntimeError):
        with tracing.span("gen_ai.chat"):
            raise RuntimeError("HTTP 400; provider said: secret page text")
    (span,) = spans_in(traced)
    assert span["status"]["code"] == 2
    assert "secret" not in json.dumps(span)
    assert "events" not in span


def test_a_failed_write_never_fails_the_traced_work(tmp_path, monkeypatch):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("")
    monkeypatch.setenv("JEV_TRACE_DIR", str(blocked))
    try:
        with tracing.span("browser.stage", stage="act"):
            result = "done"
    finally:
        tracing.configure(None)
    assert result == "done"
    finished = TracerProvider().get_tracer("test").start_span("browser.stage")
    finished.end()
    assert tracing.FileSpanExporter(blocked).export([finished]) is SpanExportResult.FAILURE


def test_tracing_is_off_without_the_environment_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("JEV_TRACE_DIR", raising=False)
    tracing.configure(None)
    with tracing.span("browser.stage", stage="observe") as span:
        assert not span.is_recording()
    assert not list(tmp_path.iterdir())

