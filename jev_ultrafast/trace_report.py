"""Turn JEV_TRACE_DIR files into one self-contained HTML report of where run time goes.

    uv run python -m jev_ultrafast.trace_report <trace files or folders> [-o report.html]

Each run's wall time is split exclusively: at every instant the innermost active spans own the
time, so nested spans never double count and gaps stay visible as uninstrumented time.
"""

import argparse
import html
import json
import statistics
from pathlib import Path

# Fixed categorical order; the colors live in the page stylesheet, keyed by slot.
CATEGORIES = [
    ("choose", "Action choice"),
    ("text", "Text model"),
    ("observe", "Observe page"),
    ("act", "Act on page"),
    ("browser", "Browser setup & checks"),
    ("wait", "Wait"),
    ("concurrent", "Concurrent"),
    ("other", "Uninstrumented"),
]
LABELS = dict(CATEGORIES)
OBSERVE = {"observe", "snapshot", "screenshot", "settle", "frames"}
ACT = {"act", "target", "input"}


def _value(value):
    (kind, raw), = value.items()
    return int(raw) if kind == "intValue" else raw


def load(paths):
    """Spans grouped by trace: {trace_id: [span, ...]} with times in ms and flat attributes."""
    files = []
    for path in map(Path, paths):
        files += sorted(path.glob("**/*.jsonl")) if path.is_dir() else [path]
    traces = {}
    for file in files:
        for line in file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            for resource in json.loads(line).get("resourceSpans", []):
                for scope in resource.get("scopeSpans", []):
                    for raw in scope.get("spans", []):
                        span = {
                            "id": raw["spanId"],
                            "parent": raw.get("parentSpanId"),
                            "name": raw["name"],
                            "start": int(raw["startTimeUnixNano"]) / 1e6,
                            "end": int(raw["endTimeUnixNano"]) / 1e6,
                            "error": raw.get("status", {}).get("code") == 2,
                            "attributes": {a["key"]: _value(a["value"]) for a in raw.get("attributes", [])},
                        }
                        group = traces.setdefault(raw["traceId"], {})
                        group[span["id"]] = span
    return {trace_id: list(spans.values()) for trace_id, spans in traces.items()}


def category(span):
    attributes = span["attributes"]
    if span["name"] == "gen_ai.chat":
        return "text" if attributes.get("jev.purpose") in {"text", "shortcut"} else "choose"
    if span["name"] == "browser.stage":
        stage = attributes.get("stage")
        return "observe" if stage in OBSERVE else "act" if stage in ACT else "browser"
    if span["name"] == "jev.wait":
        return "wait"
    return "other"


def detail(span):
    """A finer label for tables and tooltips: the browser stage or model purpose."""
    attributes = span["attributes"]
    if span["name"] == "browser.stage":
        return f"browser {attributes.get('stage', '?')}"
    if span["name"] == "gen_ai.chat":
        return f"model {attributes.get('jev.purpose', '?')}"
    if span["name"] == "jev.wait":
        return f"wait {attributes.get('reason', '?')}"
    return span["name"]


def partition(spans, start, end):
    """Exclusive segments covering [start, end]; each instant belongs to the innermost active spans."""
    children = {}
    for span in spans:
        children.setdefault(span["parent"], []).append(span)
    bounds = sorted({start, end, *(t for s in spans for t in (s["start"], s["end"]) if start <= t <= end)})
    segments = []
    for left, right in zip(bounds, bounds[1:]):
        if right <= left:
            continue
        middle = (left + right) / 2
        active = [s for s in spans if s["start"] <= middle < s["end"]]
        active_ids = {s["id"] for s in active}
        leaves = [s for s in active if not any(c["id"] in active_ids for c in children.get(s["id"], []))]
        if len(leaves) > 1:
            kind, owner = "concurrent", None
        elif leaves:
            kind, owner = category(leaves[0]), leaves[0]
        else:
            kind, owner = "other", None
        # Time a run or command spends outside any child span is agent code: uninstrumented.
        if owner is None or owner["name"] in {"jev.run", "jev.command"}:
            label = LABELS[kind]
        else:
            label = detail(owner)
        previous = segments[-1] if segments else None
        if previous and previous["category"] == kind and previous["label"] == label and previous["end"] == left:
            previous["end"] = right
        else:
            segments.append({"start": left, "end": right, "category": kind, "label": label})
    return segments


def run_summary(trace_id, spans):
    roots = [s for s in spans if s["name"] == "jev.run"] or [min(spans, key=lambda s: s["start"])]
    root = roots[0]
    start, end = root["start"], root["end"]
    segments = partition(spans, start, end)
    totals = {key: 0.0 for key, _ in CATEGORIES}
    details = {}
    for segment in segments:
        length = segment["end"] - segment["start"]
        totals[segment["category"]] += length
        details[segment["label"]] = details.get(segment["label"], 0.0) + length
    attributes = root["attributes"]
    verified = attributes.get("jev.verified")
    return {
        "trace_id": trace_id,
        "start": start,
        "duration": end - start,
        "status": attributes.get("jev.status", "unknown"),
        "verified": verified,
        "error": attributes.get("jev.error"),
        "segments": segments,
        "totals": totals,
        "details": details,
        "requests": sorted(
            (
                {
                    "kind": category(s),
                    "start": s["start"] - start,
                    "duration": s["end"] - s["start"],
                    "error": s["error"],
                }
                for s in spans
                if s["name"] == "gen_ai.chat"
            ),
            key=lambda r: r["start"],
        ),
        "calls": {
            name: [s["end"] - s["start"] for s in spans if detail(s) == name]
            for name in ("model choose", "model text", "browser observe", "browser act")
        },
        "actions": attributes.get("jev.actions"),
    }


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]


def seconds(ms):
    return "-" if ms is None else f"{ms / 1000:.2f} s"


def millis(ms):
    return "-" if ms is None else f"{ms:,.0f} ms"


def outcome(run):
    if run["verified"] is True:
        return "pass", "✓ Pass"
    if run["verified"] is False:
        return "fail", "✕ Fail"
    return "neutral", run["status"].capitalize()


def render(runs, title):
    runs = sorted(runs, key=lambda r: r["start"])
    scale = max((r["duration"] for r in runs), default=1) or 1
    e = html.escape
    parts = []
    for index, run in enumerate(runs, 1):
        tone, verdict = outcome(run)
        chat = len([r for r in run["requests"] if r["kind"] == "choose"])
        text = len([r for r in run["requests"] if r["kind"] == "text"])
        bar = []
        for segment in run["segments"]:
            left = (segment["start"] - run["start"]) / scale * 100
            width = (segment["end"] - segment["start"]) / scale * 100
            length, offset = segment["end"] - segment["start"], segment["start"] - run["start"]
            tip = f"{segment['label']} · {millis(length)} at {seconds(offset)}"
            bar.append(
                f'<rect class="c-{segment["category"]}" x="{left:.4f}%" y="0" width="{width:.4f}%" height="22" '
                f'data-tip="{e(tip)}"></rect>'
            )
        strip = []
        for row, kind in enumerate(("choose", "text")):
            for request in (r for r in run["requests"] if r["kind"] == kind):
                left = request["start"] / scale * 100
                width = max(request["duration"] / scale * 100, 0.15)
                tip = f"{LABELS[kind]} request · {millis(request['duration'])}{' · failed' if request['error'] else ''}"
                strip.append(
                    f'<rect class="c-{kind}{" failed" if request["error"] else ""}" x="{left:.4f}%" '
                    f'y="{row * 9}" width="{width:.4f}%" height="7" rx="2" data-tip="{e(tip)}"></rect>'
                )
        parts.append(f"""
      <section class="run">
        <div class="run-head">
          <h3>Run {index} <span class="status {tone}">{verdict}</span></h3>
          <p class="meta">{seconds(run['duration'])} · {run['actions'] if run['actions'] is not None else '?'} actions
            · {chat} action choices · {text} text requests · trace {e(run['trace_id'][:12])}
            {f"· {e(str(run['error']))}" if run['error'] else ''}</p>
        </div>
        <div class="lane"><span class="lane-label">Time</span>
          <svg class="bar" width="100%" height="22" preserveAspectRatio="none">{''.join(bar)}</svg></div>
        <div class="lane"><span class="lane-label">Requests</span>
          <svg class="strip" width="100%" height="16" preserveAspectRatio="none">{''.join(strip)}</svg></div>
      </section>""")

    ticks = []
    step = next(s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300) if scale / 1000 / s <= 8)
    t = 0.0
    while t * 1000 <= scale + 1e-6:
        ticks.append(f'<span style="left:{t * 1000 / scale * 100:.3f}%">{t:g}s</span>')
        t += step

    passed = [r for r in runs if r["verified"] is True]
    judged = [r for r in runs if r["verified"] is not None]
    durations = [r["duration"] for r in runs]
    hero = [
        ("Runs", str(len(runs))),
        ("Verified pass", f"{len(passed)} of {len(judged)}" if judged else "-"),
        ("Median run", seconds(statistics.median(durations)) if durations else "-"),
        ("Slowest run", seconds(max(durations)) if durations else "-"),
    ]
    total_time = sum(durations) or 1
    category_rows = []
    for key, label in CATEGORIES:
        per_run = [r["totals"][key] for r in runs]
        if not any(per_run):
            continue
        category_rows.append(
            f'<tr><th scope="row"><span class="swatch c-{key}"></span>{label}</th>'
            f"<td>{sum(per_run) / total_time * 100:.1f}%</td><td>{millis(statistics.median(per_run))}</td>"
            f"<td>{millis(percentile(per_run, 0.95))}</td><td>{millis(max(per_run))}</td></tr>"
        )
    call_rows = []
    for name, label in (
        ("model choose", "Action choice request"),
        ("model text", "Text model request"),
        ("browser observe", "Observe page"),
        ("browser act", "Act on page"),
    ):
        values = [v for r in runs for v in r["calls"][name]]
        if values:
            call_rows.append(
                f"<tr><th scope=\"row\">{label}</th><td>{len(values)}</td><td>{millis(statistics.median(values))}</td>"
                f"<td>{millis(percentile(values, 0.95))}</td><td>{millis(max(values))}</td></tr>"
            )
    detail_totals = {}
    for run in runs:
        for name, value in run["details"].items():
            detail_totals[name] = detail_totals.get(name, 0.0) + value
    detail_rows = "".join(
        f"<tr><th scope=\"row\">{e(name)}</th><td>{value / total_time * 100:.1f}%</td>"
        f"<td>{millis(value / len(runs))}</td></tr>"
        for name, value in sorted(detail_totals.items(), key=lambda item: -item[1])
    )
    legend = "".join(
        f'<li><span class="swatch c-{key}"></span>{label}</li>'
        for key, label in CATEGORIES
        if any(r["totals"][key] for r in runs)
    )
    return PAGE.format(
        title=e(title),
        hero="".join(f'<div class="tile"><span>{k}</span><strong>{v}</strong></div>' for k, v in hero),
        legend=legend,
        ticks="".join(ticks),
        runs="".join(parts),
        category_rows="".join(category_rows),
        call_rows="".join(call_rows),
        detail_rows=detail_rows,
    )


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
.viz-root {{
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --ring: rgba(11,11,11,0.10);
  --choose: #2a78d6; --text: #eb6834; --observe: #1baf7a; --act: #eda100; --browser: #e87ba4;
  --wait: #008300; --concurrent: #4a3aa7; --other: #d7d6cf;
  --good: #006300; --critical: #d03b3b;
}}
@media (prefers-color-scheme: dark) {{
  .viz-root {{
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
    --choose: #3987e5; --text: #d95926; --observe: #199e70; --act: #c98500; --browser: #d55181;
    --wait: #008300; --concurrent: #9085e9; --other: #3d3d3a;
    --good: #0ca30c; --critical: #e66767;
  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; }}
.viz-root {{ background: var(--page); color: var(--ink); min-height: 100vh; padding: 32px;
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1180px; margin: 0 auto; }}
h1 {{ font-size: 24px; margin: 0 0 4px; }}
h2 {{ font-size: 16px; margin: 32px 0 12px; }}
h3 {{ font-size: 14px; margin: 0; display: flex; gap: 10px; align-items: center; }}
p {{ margin: 0; }}
.sub, .meta, .note {{ color: var(--ink-2); }}
.note {{ font-size: 12px; margin-top: 8px; }}
.card {{ background: var(--surface); border: 1px solid var(--ring); border-radius: 12px; padding: 20px; }}
.tiles {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 20px 0; }}
.tile {{ background: var(--surface); border: 1px solid var(--ring); border-radius: 12px; padding: 14px 16px; }}
.tile span {{ color: var(--ink-2); display: block; font-size: 12px; }}
.tile strong {{ font-size: 24px; font-weight: 600; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 6px 18px; list-style: none; padding: 0; margin: 0 0 16px;
  color: var(--ink-2); font-size: 12px; }}
.legend li {{ display: flex; align-items: center; gap: 6px; }}
.swatch {{ display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px;
  vertical-align: -1px; }}
.legend .swatch {{ margin: 0; }}
.axis {{ position: relative; height: 18px; margin-left: 84px; color: var(--muted); font-size: 11px;
  font-variant-numeric: tabular-nums; border-bottom: 1px solid var(--axis); }}
.axis span {{ position: absolute; transform: translateX(-50%); }}
.axis span:first-child {{ transform: none; }}
.run {{ padding: 14px 0; border-bottom: 1px solid var(--grid); }}
.run:last-child {{ border-bottom: 0; }}
.run-head {{ margin-bottom: 8px; }}
.meta {{ font-size: 12px; }}
.lane {{ display: flex; align-items: center; gap: 12px; margin-top: 4px; }}
.lane-label {{ width: 72px; flex: none; color: var(--muted); font-size: 11px; text-align: right; }}
.lane svg {{ flex: 1; display: block; overflow: visible; }}
.bar rect {{ stroke: var(--surface); stroke-width: 1; }}
.bar {{ border-radius: 4px; }}
rect[data-tip]:hover {{ opacity: 0.8; }}
.c-choose {{ fill: var(--choose); background: var(--choose); }}
.c-text {{ fill: var(--text); background: var(--text); }}
.c-observe {{ fill: var(--observe); background: var(--observe); }}
.c-act {{ fill: var(--act); background: var(--act); }}
.c-browser {{ fill: var(--browser); background: var(--browser); }}
.c-wait {{ fill: var(--wait); background: var(--wait); }}
.c-concurrent {{ fill: var(--concurrent); background: var(--concurrent); }}
.c-other {{ fill: var(--other); background: var(--other); }}
.failed {{ stroke: var(--critical); stroke-width: 2; }}
.status {{ font-size: 12px; font-weight: 600; }}
.status.pass {{ color: var(--good); }}
.status.fail {{ color: var(--critical); }}
.status.neutral {{ color: var(--ink-2); }}
table {{ width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }}
th, td {{ text-align: right; padding: 8px 10px; border-bottom: 1px solid var(--grid); white-space: nowrap; }}
th:first-child {{ text-align: left; font-weight: 500; }}
thead th {{ color: var(--ink-2); font-size: 12px; font-weight: 500; }}
.tables {{ display: grid; grid-template-columns: 3fr 2fr; gap: 16px; }}
details {{ margin-top: 16px; }}
summary {{ cursor: pointer; color: var(--ink-2); }}
#tip {{ position: fixed; pointer-events: none; background: var(--ink); color: var(--surface); padding: 6px 9px;
  border-radius: 6px; font-size: 12px; display: none; z-index: 2; max-width: 360px; }}
@media (max-width: 800px) {{
  .tiles {{ grid-template-columns: 1fr 1fr; }}
  .tables {{ grid-template-columns: 1fr; }}
}}
</style></head>
<body><div class="viz-root"><main>
  <h1>{title}</h1>
  <p class="sub">Exclusive wall-time split per run: each instant belongs to the innermost active span.
    Uninstrumented time is agent code between recorded spans.</p>
  <div class="tiles">{hero}</div>
  <div class="card">
    <ul class="legend">{legend}</ul>
    <div class="axis">{ticks}</div>
    {runs}
    <p class="note">One shared seconds scale. The request strip shows each model request's full duration,
      including retries; a red outline marks a failed request. Hover any mark for details.</p>
  </div>
  <h2>Where the time goes</h2>
  <div class="tables">
    <div class="card"><table>
      <thead><tr><th>Category</th><th>Share of all time</th><th>Median per run</th><th>p95 per run</th>
        <th>Max per run</th></tr></thead>
      <tbody>{category_rows}</tbody></table></div>
    <div class="card"><table>
      <thead><tr><th>Per call</th><th>Count</th><th>Median</th><th>p95</th><th>Max</th></tr></thead>
      <tbody>{call_rows}</tbody></table></div>
  </div>
  <details class="card"><summary>Every stage</summary><table>
    <thead><tr><th>Stage</th><th>Share of all time</th><th>Mean per run</th></tr></thead>
    <tbody>{detail_rows}</tbody></table></details>
</main><div id="tip" role="tooltip"></div></div>
<script>
const tip = document.getElementById("tip");
document.addEventListener("pointermove", (event) => {{
  const mark = event.target.closest("[data-tip]");
  if (!mark) {{ tip.style.display = "none"; return; }}
  tip.textContent = mark.dataset.tip;
  tip.style.display = "block";
  const x = Math.min(event.clientX + 14, innerWidth - tip.offsetWidth - 8);
  tip.style.left = x + "px";
  tip.style.top = event.clientY + 16 + "px";
}});
</script>
</body></html>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="trace .jsonl files or folders containing them")
    parser.add_argument("-o", "--output", default="trace-report.html")
    parser.add_argument("--title", default="Jev run profile")
    args = parser.parse_args(argv)
    traces = load(args.paths)
    if not traces:
        parser.error("no trace spans found")
    runs = [run_summary(trace_id, spans) for trace_id, spans in traces.items()]
    Path(args.output).write_text(render(runs, args.title), encoding="utf-8")
    print(f"{len(runs)} runs -> {args.output}")


if __name__ == "__main__":
    main()
