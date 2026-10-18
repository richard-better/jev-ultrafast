"""Render the actual Google Flights screencast at 1x, including every loading wait.

record_flights.py recordings render to docs/demo.* (search task), docs/cheapest-demo.* or, with a
URL map, docs/cheapest-map-demo.* (cheapest-flight task). record_flights_url.py deep-link recordings
render to docs/url-demo.* as a baseline.
"""

import argparse
import json
import re
import statistics
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path, help="Exact verified recording directory")
args = parser.parse_args()
source = args.source.resolve()
state = json.loads((source / "state.json").read_text())
assert state["verification"]["passed"] and not state["recording_errors"]
assert all(h.get("execution") != "unknown" for h in state["history"]), "Cannot render unconfirmed execution timing"
frames = [(0, Image.open(source / "frames/000000.jpg").convert("RGB"))]
frames += sorted((int(p.stem), Image.open(p).convert("RGB")) for p in (source / "screencast").glob("*.jpg"))
end = state["elapsed_ms"]
history = state["history"]
# The departure is computed per run, so label it from the recorded goal, e.g. "29 October".
month, day = re.search(r" on ([A-Z][a-z]+) (\d{1,2}), \d{4}", state["goal"]).groups()
departure = f"{day} {month}"


def acted(match, after=None):
    """When the first matching action executed, optionally only after another step, or None."""
    return next(
        (h["executed_ms"] for h in history if match(h) and (after is None or h["executed_ms"] > after)), None
    )


def text_model():
    return state["text_calls"][0]["model"].split("/")[-1]


# What each recording kind shows: its labels, checklist (label, done-at ms) and output files.
if state.get("mode") == "url":
    design = {
        "outputs": ("url-demo", "url-demo-result.png"),
        "brand": "·  baseline",
        "title": f"Zürich → London. In {end / 1000:.1f} seconds.",
        "tagline": "Known route and date. One URL, no agent.",
        "address": "google.com/travel/flights?q=Flights from Zurich to London…",
        "heading": "DIRECT URL",
        "steps": [("Open deep link", 0), ("Flights verified", end)],
        "waiting_from": 0,
        "final": ("Flights found", "Route + date verified"),
        "metric": lambda t: ("0", "model calls"),
        "footer": "Opened by URL: no agent, no model calls. Original timing from navigation; loading included.",
    }
elif state.get("task") == "cheapest":
    mapped = bool(state.get("url_map"))
    sorted_at = acted(lambda h: h["action"].startswith("Cheapest"))
    first = (
        ("Results by URL map", acted(lambda h: h["kind"] == "shortcut"))
        if mapped
        else ("Search Zürich → London", acted(lambda h: h["action"] == "Search"))
    )
    design = {
        "outputs": ("cheapest-map-demo", "cheapest-map-result.png") if mapped
        else ("cheapest-demo", "cheapest-result.png"),
        "brand": "×  TypeSafe",
        "title": f"Cheapest Zürich → London. {end / 1000:.1f} seconds.",
        "tagline": "A URL map opens the results; Jev compares, selects and stops." if mapped
        else "Search, compare, select. Stops at booking options.",
        "address": "google.com/travel/flights",
        "heading": "JEV ULTRAFAST  ·  URL MAP" if mapped else "JEV ULTRAFAST",
        "steps": [
            first,
            ("Sort by cheapest", sorted_at),
            # Only a flight opened after sorting is the cheapest one.
            ("Open cheapest flight", acted(lambda h: h["action"].startswith("Flight details"), after=sorted_at)),
            ("Select flight", acted(lambda h: h["action"] == "Select flight")),
            ("Booking options", end),
        ],
        "waiting_from": acted(lambda h: h["action"] == "Select flight"),
        "final": ("Booking options", "Cheapest flight verified; not booked"),
        "metric": None,
        "footer": f"Operation + index by Jev. Text by {text_model()}."
        + (" Google Flights URL map." if mapped else "")
        + " Original timing; waits included.",
    }
else:
    design = {
        "outputs": ("demo", "flights-result.png"),
        "brand": "×  TypeSafe",
        "title": f"Zürich → London. In {end / 1000:.1f} seconds.",
        "tagline": "One goal. Dynamic elements. LLM-generated text.",
        "address": "google.com/travel/flights",
        "heading": "JEV ULTRAFAST",
        "steps": [
            ("One way", acted(lambda h: h["action"] == "One way")),
            ("Zürich", acted(lambda h: h["action"] == "Zürich, Switzerland")),
            ("London", acted(lambda h: h["action"] == "London, United Kingdom")),
            (departure, acted(lambda h: h["action"].startswith("Done. Search"))),
            ("Search flights", acted(lambda h: h["action"] == "Search")),
        ],
        "waiting_from": acted(lambda h: h["action"] == "Search"),
        "final": ("Flights found", "Route + date verified"),
        "metric": None,
        "footer": f"Operation + index by Jev. Text by {text_model()}. Original timing; waits included.",
    }

# The panel keeps a fixed width and grows with the recorded viewport's height. The top 64 CSS
# pixels hold Google account controls and are cropped out.
view_width, view_height = state["page"]["w"], state["page"]["h"]
panel_width = 1120
panel_height = round((view_height - 64) * panel_width / view_width) // 2 * 2  # H.264 needs even sizes.
panel_bottom = 226 + panel_height + 1
canvas_height = panel_bottom + 57


def browser_view(image):
    scale = image.width / view_width
    view = image.crop((0, round(64 * scale), image.width, round(view_height * scale)))
    return view if view.size == (panel_width, panel_height) else view.resize((panel_width, panel_height), Image.LANCZOS)


folder = source / "video-frames"
folder.mkdir(parents=True, exist_ok=False)
font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
font_bold = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def font(n, bold=False):
    return ImageFont.truetype(font_bold if bold else font_path, n)


def mono(n):
    return ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", n)


def decision_latency(t):
    latencies = [x["latency_ms"] for x in state["decisions"] if x["elapsed_ms"] <= t]
    return (f"{statistics.median(latencies):.0f} ms" if latencies else "—"), "median decision latency"


ink, muted, green = "#172a20", "#6a766c", "#2a743f"
for i in range(round((end + 500) * 30 / 1000)):
    t = min(end, round(i * 1000 / 30))
    screenshot = next(im for ts, im in reversed(frames) if ts <= t)
    canvas = Image.new("RGB", (1536, canvas_height), "#f3f4ec")
    d = ImageDraw.Draw(canvas)
    d.text((36, 26), "browser use", font=font(23, True), fill=ink)
    d.text((186, 27), design["brand"], font=font(22), fill=muted)
    d.rounded_rectangle((1287, 24, 1499, 59), radius=17, fill="#dfebd9")
    d.text((1310, 32), "REAL WEB  ·  1× SPEED", font=font(14, True), fill=green)
    d.text((36, 80), design["title"], font=font(43, True), fill=ink)
    d.text((38, 139), design["tagline"], font=font(20), fill=muted)
    d.rounded_rectangle((35, 191, 1157, panel_bottom), radius=14, fill="#202124")
    for j, c in enumerate(["#de8278", "#d6bd6e", "#8dbd8a"]):
        d.ellipse((54 + j * 19, 205, 63 + j * 19, 214), fill=c)
    d.text((145, 201), design["address"], font=mono(13), fill="#d4d6d5")
    # Omit Google account controls in every frame. No content from the task area is redrawn.
    canvas.paste(browser_view(screenshot), (36, 226))
    d.text((1192, 206), design["heading"], font=font(16, True), fill=green)
    d.text((1189, 242), f"{t / 1000:05.2f}", font=mono(52), fill=ink)
    d.text((1193, 307), "SECONDS ELAPSED", font=font(13, True), fill=muted)
    for j, (label, done_at) in enumerate(design["steps"]):
        done = done_at is not None and done_at <= t
        y = 370 + j * 54
        d.ellipse((1194, y, 1218, y + 24), fill=green if done else "#e0e4d9")
        if done:
            d.line([(1200, y + 12), (1204, y + 16), (1212, y + 8)], fill="white", width=2)
        d.text((1236, y - 1), label, font=font(21, done), fill=ink if done else muted)
    final = t >= end
    waiting = design["waiting_from"] is not None and design["waiting_from"] <= t < end
    d.rounded_rectangle((1189, 670, 1499, 789), radius=14, fill="#dfeeda" if final else "#e7e9df")
    if final:
        title, subtitle = design["final"]
    elif waiting:
        title, subtitle = "Waiting for Google…", "Loading stays in the video"
    else:
        executed = sum(h["executed_ms"] <= t for h in history)
        title, subtitle = "Choose. Act. Repeat.", f"{executed} actions executed"
    d.text((1209, 691), title, font=font(22, True), fill=green if final else ink)
    d.text((1209, 734), subtitle, font=font(16), fill=muted)
    value, caption = (design["metric"] or decision_latency)(t)
    d.text((1194, 835), value, font=mono(30), fill=ink)
    d.text((1194, 878), caption, font=font(16), fill=muted)
    d.line((37, canvas_height - 40, 1498, canvas_height - 40), fill="#d3d9cc", width=2)
    d.line((37, canvas_height - 40, 37 + (1498 - 37) * t / end, canvas_height - 40), fill=green, width=3)
    d.text((37, canvas_height - 27), design["footer"], font=font(14), fill=muted)
    d.text((1194, canvas_height - 27), "github.com/browser-use/jev-ultrafast", font=font(12), fill=muted)
    canvas.save(folder / f"{i:04d}.png")
name, result = design["outputs"]
canvas.save(ROOT / "docs" / result)
video = ROOT / "docs" / f"{name}.mp4"
subprocess.run(
    [
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", "30", "-i", str(folder / "%04d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-movflags", "+faststart", str(video),
    ],
    check=True,
)
subprocess.run(
    [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
        "-vf", "fps=12,scale=1152:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
        "-loop", "0", str(ROOT / "docs" / f"{name}.gif"),
    ],
    check=True,
)
print("Rendered", len(frames), "source frames at original timing:", end, "ms, plus a 500ms end hold.")
