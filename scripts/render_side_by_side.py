"""Render two verified cheapest-flight recordings side by side on one clock, at 1x.

    uv run python scripts/render_side_by_side.py <without-map-take> <with-map-take>

Both start at their first prediction, like render_demo.py. Each side freezes on its last frame once its
run is done, and the video ends after the slower run plus a hold. Writes docs/cheapest-side-by-side.*.
"""

import argparse
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FPS, HOLD_MS = 30, 1500
WIDTH, MARGIN, PANEL = 1920, 40, 900
ink, muted, green, dark = "#172a20", "#6a766c", "#2a743f", "#202124"


def font(n, bold=False):
    name = "Arial Bold.ttf" if bold else "Arial.ttf"
    return ImageFont.truetype(f"/System/Library/Fonts/Supplemental/{name}", n)


def mono(n):
    return ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", n)


def load(folder, label):
    folder = folder.resolve()
    state = json.loads((folder / "state.json").read_text())
    assert state["verification"]["passed"] and not state["recording_errors"], folder
    assert state.get("task") == "cheapest", "Both takes must be cheapest-flight recordings"
    assert all(h.get("execution") != "unknown" for h in state["history"]), "Cannot render unconfirmed execution timing"
    frames = [(0, Image.open(folder / "frames/000000.jpg").convert("RGB"))]
    frames += sorted((int(p.stem), Image.open(p).convert("RGB")) for p in (folder / "screencast").glob("*.jpg"))
    history = state["history"]

    def acted(match, after=None):
        return next(
            (h["executed_ms"] for h in history if match(h) and (after is None or h["executed_ms"] > after)), None
        )

    sorted_at = acted(lambda h: h["action"].startswith("Cheapest"))
    first = (
        ("Results by URL map", acted(lambda h: h["kind"] == "shortcut"))
        if state.get("url_map")
        else ("Search the route", acted(lambda h: h["action"] == "Search"))
    )
    steps = [
        first,
        ("Sort by cheapest", sorted_at),
        # Only a flight opened after sorting is the cheapest one.
        ("Open cheapest", acted(lambda h: h["action"].startswith("Flight details"), after=sorted_at)),
        ("Select flight", acted(lambda h: h["action"] == "Select flight")),
        ("Booking options", state["elapsed_ms"]),
    ]
    return {"label": label, "state": state, "frames": frames, "end": state["elapsed_ms"], "steps": steps}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("without_map", type=Path)
    parser.add_argument("with_map", type=Path)
    args = parser.parse_args()
    runs = [
        load(args.without_map, "Jev fills the search form"),
        load(args.with_map, "Jev with a Google Flights URL map"),
    ]
    assert not runs[0]["state"].get("url_map") and runs[1]["state"].get("url_map"), "Order: without map, with map"
    view_width, view_height = runs[0]["state"]["page"]["w"], runs[0]["state"]["page"]["h"]
    assert (view_width, view_height) == (runs[1]["state"]["page"]["w"], runs[1]["state"]["page"]["h"])
    # The top 64 CSS pixels hold Google account controls and are cropped out, as in render_demo.py.
    panel_height = round((view_height - 64) * PANEL / view_width) // 2 * 2
    top = 250
    height = (top + panel_height + 150) // 2 * 2  # H.264 needs even sizes.
    folder = args.with_map.resolve().parent / "side-by-side-frames"
    folder.mkdir(exist_ok=False)
    longest = max(run["end"] for run in runs)
    model = runs[0]["state"]["text_calls"][0]["model"].split("/")[-1]

    def view(image):
        scale = image.width / view_width
        cropped = image.crop((0, round(64 * scale), image.width, round(view_height * scale)))
        return cropped.resize((PANEL, panel_height), Image.LANCZOS)

    cache = {}
    for i in range(round((longest + HOLD_MS) * FPS / 1000)):
        t = min(longest, round(i * 1000 / FPS))
        canvas = Image.new("RGB", (WIDTH, height), "#f3f4ec")
        d = ImageDraw.Draw(canvas)
        d.text((MARGIN, 30), "browser use", font=font(23, True), fill=ink)
        d.text((MARGIN + 150, 31), "×  TypeSafe", font=font(22), fill=muted)
        d.rounded_rectangle((WIDTH - MARGIN - 212, 28, WIDTH - MARGIN, 63), radius=17, fill="#dfebd9")
        d.text((WIDTH - MARGIN - 189, 36), "REAL WEB  ·  1× SPEED", font=font(14, True), fill=green)
        d.text((MARGIN, 82), "Cheapest Zürich → London flight, to its booking options.", font=font(40, True), fill=ink)
        d.text((MARGIN + 2, 138), "Same goal, loop and models. Started together; the only difference is the URL map.",
               font=font(20), fill=muted)
        for n, run in enumerate(runs):
            x = MARGIN + n * (PANEL + MARGIN)
            local = min(t, run["end"])
            done = t >= run["end"]
            d.text((x, 190), run["label"], font=font(22, True), fill=ink)
            clock = f"{local / 1000:05.2f} s"
            d.text((x + PANEL - 150, 184), clock, font=mono(30), fill=green if done else ink)
            d.rounded_rectangle((x - 1, top - 16, x + PANEL + 1, top + panel_height + 1), radius=12, fill=dark)
            for j, c in enumerate(["#de8278", "#d6bd6e", "#8dbd8a"]):
                d.ellipse((x + 14 + j * 17, top - 11, x + 22 + j * 17, top - 3), fill=c)
            timestamp, frame = next((ts, im) for ts, im in reversed(run["frames"]) if ts <= local)
            key = (n, timestamp)
            if key not in cache:
                cache.clear() if len(cache) > 8 else None
                cache[key] = view(frame)
            canvas.paste(cache[key], (x, top))
            if done:
                d.rounded_rectangle((x + PANEL - 330, top + 16, x + PANEL - 16, top + 70), radius=14, fill="#dfeeda")
                d.text((x + PANEL - 312, top + 30), f"Done in {run['end'] / 1000:.1f} s  ·  verified",
                       font=font(20, True), fill=green)
            chip_y = top + panel_height + 24
            chip_x = x
            for label, done_at in run["steps"]:
                reached = done_at is not None and done_at <= t
                width = int(d.textlength(label, font=font(16, reached))) + 44
                d.rounded_rectangle((chip_x, chip_y, chip_x + width, chip_y + 34), radius=17,
                                    fill="#dfeeda" if reached else "#e7e9df")
                d.ellipse((chip_x + 10, chip_y + 10, chip_x + 24, chip_y + 24), fill=green if reached else "#cfd4c8")
                d.text((chip_x + 32, chip_y + 8), label, font=font(16, reached), fill=ink if reached else muted)
                chip_x += width + 8
        d.line((MARGIN, height - 44, WIDTH - MARGIN, height - 44), fill="#d3d9cc", width=2)
        d.text((MARGIN, height - 32),
               f"Operation + index by Jev. Text by {model}. Original timing; waits included. "
               "Each side stops on its verified booking page; nothing is booked.",
               font=font(14), fill=muted)
        d.text((WIDTH - MARGIN - 214, height - 32), "github.com/browser-use/jev-ultrafast", font=font(12), fill=muted)
        canvas.save(folder / f"{i:04d}.png")
    canvas.save(ROOT / "docs" / "cheapest-side-by-side.png")
    video = ROOT / "docs" / "cheapest-side-by-side.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(folder / "%04d.png"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", str(video)],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
         "-vf", "fps=10,scale=1280:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
         "-loop", "0", str(ROOT / "docs" / "cheapest-side-by-side.gif")],
        check=True,
    )
    print("Rendered side by side:", [f"{run['label']}: {run['end']} ms" for run in runs])


if __name__ == "__main__":
    main()
