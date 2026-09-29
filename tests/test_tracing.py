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


def test_an_agent_run_exports_one_connected_span_tree(traced, monkeypatch):
    from unittest.mock import Mock

    from jev_ultrafast import agent as loop
    from jev_ultrafast import model

    before = {
        "url": "https://example.test/", "title": "Search", "text": "Search", "scroll": {"y": 0},
        "fingerprint": "before",
        "actions": [{"id": "e1", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20}],
    }
    browser = Mock()
    browser.observe.side_effect = [before, {**before, "fingerprint": "after"}]
    browser.fresh.return_value = True
    browser.act.return_value = {"executed": "e1"}
    monkeypatch.setattr(loop, "Browser", Mock(return_value=browser))
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    answer = {"choice": "CLICK", "confidence": 0.9, "probabilities": {"CLICK": 0.9, "DONE": 0.05, "BLOCKED": 0.05}}
    target = {"choice": "1", "confidence": 1.0, "probabilities": {"1": 1.0}}
    reply = {"model": "test", "answers": {"operation": answer, "click_target": target}, "usage": {"input_tokens": 7}}
    monkeypatch.setattr(model, "post_json", Mock(return_value=reply))

    agent = loop.Agent("https://example.test/", "Press Go")
    agent.command("tick")
    agent.close()

    spans = spans_in(traced)
    assert len({s["traceId"] for s in spans}) == 1
    by_id = {s["spanId"]: s for s in spans}
    (run,) = [s for s in spans if s["name"] == "jev.run"]
    for span in spans:
        if span is not run:
            assert span["parentSpanId"] in by_id, span["name"]
    commands = {attributes(s)["jev.command"]: s for s in spans if s["name"] == "jev.command"}
    assert set(commands) == {"tick", "predict", "act"}
    assert commands["predict"]["parentSpanId"] == commands["tick"]["spanId"]
    assert commands["tick"]["parentSpanId"] == run["spanId"]
    (chat,) = [s for s in spans if s["name"] == "gen_ai.chat"]
    assert chat["parentSpanId"] == commands["predict"]["spanId"]
    assert attributes(chat)["jev.purpose"] == "choose"
    assert attributes(chat)["gen_ai.usage.input_tokens"] == "7"
    act = attributes(commands["act"])
    assert (act["jev.action"], act["jev.action.kind"], act["jev.execution"], act["jev.page_changed"]) == (
        "e1", "click", "confirmed", True,
    )
    assert attributes(run)["jev.status"] == "ready" and attributes(run)["jev.actions"] == "1"
