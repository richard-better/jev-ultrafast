"""Offline checks for the trace report's exclusive time split. No browsers or model calls."""

import json

import pytest

from jev_ultrafast import trace_report


def span(span_id, name, start, end, parent=None, **attributes):
    return {"id": span_id, "parent": parent, "name": name, "start": start, "end": end, "error": False,
            "attributes": attributes}


def owned(segments):
    return [(s["start"], s["end"], s["category"]) for s in segments]


def test_nested_spans_are_never_double_counted_and_gaps_stay_visible():
    spans = [
        span("r", "jev.run", 0, 100),
        span("c", "jev.command", 10, 90, "r"),
        span("o", "browser.stage", 10, 40, "c", stage="observe"),
        span("s", "browser.stage", 15, 30, "o", stage="snapshot"),
        span("m", "gen_ai.chat", 50, 80, "c", **{"jev.purpose": "choose"}),
        span("w", "jev.wait", 60, 70, "m", reason="retry"),
    ]
    segments = trace_report.partition(spans, 0, 100)
    # Same-category neighbours stay separate when the stage differs, so tooltips name it.
    assert [(s["start"], s["end"], s["category"], s["label"]) for s in segments] == [
        (0, 10, "other", "Uninstrumented"),
        (10, 15, "observe", "browser observe"),
        (15, 30, "observe", "browser snapshot"),
        (30, 40, "observe", "browser observe"),
        (40, 50, "other", "Uninstrumented"),
        (50, 60, "choose", "model choose"),
        (60, 70, "wait", "wait retry"),
        (70, 80, "choose", "model choose"),
        (80, 100, "other", "Uninstrumented"),
    ]
    assert sum(s["end"] - s["start"] for s in segments) == 100


def test_overlapping_independent_spans_are_marked_concurrent():
    spans = [
        span("r", "jev.run", 0, 30),
        span("a", "gen_ai.chat", 0, 20, "r", **{"jev.purpose": "text"}),
        span("b", "browser.stage", 10, 30, "r", stage="act"),
    ]
    assert owned(trace_report.partition(spans, 0, 30)) == [(0, 10, "text"), (10, 20, "concurrent"), (20, 30, "act")]


@pytest.mark.parametrize(("stage", "expected"), [
    ("snapshot", "observe"), ("settle", "observe"), ("input", "act"), ("target", "act"), ("start", "browser"),
    ("fresh", "browser"),
])
def test_browser_stages_map_to_their_category(stage, expected):
    assert trace_report.category(span("x", "browser.stage", 0, 1, stage=stage)) == expected


def test_a_report_renders_every_run_with_its_verdict(tmp_path):
    def encoded(span_id, name, start, end, parent=None, **attributes):
        value = {True: {"boolValue": True}, False: {"boolValue": False}}
        raw = {
            "traceId": "a" * 32, "spanId": span_id * 16, "name": name,
            "startTimeUnixNano": str(start * 1_000_000), "endTimeUnixNano": str(end * 1_000_000),
            "attributes": [{"key": k, "value": value.get(v) or {"stringValue": v}} for k, v in attributes.items()],
        }
        if parent:
            raw["parentSpanId"] = parent * 16
        return raw

    line = {"resourceSpans": [{"scopeSpans": [{"spans": [
        encoded("1", "jev.run", 0, 2000, **{"jev.verified": True, "jev.status": "done"}),
        encoded("2", "gen_ai.chat", 100, 400, "1", **{"jev.purpose": "choose"}),
    ]}]}]}
    (tmp_path / "trace.jsonl").write_text(json.dumps(line) + "\n")
    output = tmp_path / "report.html"
    trace_report.main([str(tmp_path), "-o", str(output)])
    page = output.read_text()
    assert "✓ Pass" in page and "2.00 s" in page and "Action choice request" in page
