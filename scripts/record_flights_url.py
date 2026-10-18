"""The Flights task without an agent: open one deep link and record until results are verified.

A baseline for the agent demo. If the route and date are known up front, a URL does the whole job.
No model calls. The clock starts at navigation, so it includes loading the page; the agent's clock
starts after its initial page has loaded.
"""

import base64
import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

from browser_harness.helpers import drain_events

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from examples.flights import DEPARTURE, GOALS, verify  # noqa: E402
from jev_ultrafast.browser import Browser, StalePage, viewport  # noqa: E402

QUERY = f"Flights from Zurich to London on {DEPARTURE.iso} one way economy 1 adult"
URL = "https://www.google.com/travel/flights?hl=en&q=" + quote(QUERY)
# The deep link keeps its /travel/flights?q= address instead of /travel/flights/search, so the path
# check cannot pass; every check of the page's content must.
CONTENT_CHECKS = ("one_way", "origin", "destination", "date", "year", "results")

folder = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/flights/url-recorded")
folder.mkdir(parents=True, exist_ok=False)
browser = Browser("about:blank")
(folder / "frames").mkdir()
(folder / "frames" / "000000.jpg").write_bytes(
    base64.b64decode(browser.call("Page.captureScreenshot", format="jpeg", quality=85)["data"])
)
frames = folder / "screencast"
frames.mkdir()
stop = threading.Event()
errors = []


def capture():
    try:
        while not stop.is_set():
            for event in drain_events():
                if event["method"] != "Page.screencastFrame" or event.get("session_id") != browser.session:
                    continue
                p = event["params"]
                timestamp = max(0, round((p["metadata"]["timestamp"] - epoch) * 1000))
                (frames / f"{timestamp:06d}.jpg").write_bytes(base64.b64decode(p["data"]))
                browser.call("Page.screencastFrameAck", sessionId=p["sessionId"])
            stop.wait(0.015)
    except Exception as e:
        errors.append(str(e))


width, height = viewport()
browser.call("Page.startScreencast", format="jpeg", quality=80, maxWidth=width, maxHeight=height, everyNthFrame=2)
worker = threading.Thread(target=capture, daemon=True)
epoch = time.time()
worker.start()
started = time.perf_counter()
browser.call("Page.navigate", url=URL)
page, verification, elapsed_ms = None, None, None
try:
    deadline = started + 30
    while time.perf_counter() < deadline:
        try:
            page = browser.observe(screenshot=False)
        except StalePage:
            continue
        verification = verify(page)
        if all(verification["checks"][name] for name in CONTENT_CHECKS):
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            break
        time.sleep(0.05)
finally:
    time.sleep(0.08)  # Drain the last frame, outside the reported time.
    stop.set()
    worker.join(timeout=3)
    browser.call("Page.stopScreencast")
    passed = elapsed_ms is not None
    state = {
        "mode": "url",
        "goal": GOALS,
        "url": URL,
        "elapsed_ms": elapsed_ms if passed else round((time.perf_counter() - started) * 1000),
        "model_calls": 0,
        "page": {"w": width, "h": height},
        "final_page": page,
        "verification": {**(verification or {}), "passed": passed, "content_checks": list(CONTENT_CHECKS)},
        "recording_errors": errors,
        "history": [],
        "decisions": [],
        "text_calls": [],
    }
    (folder / "state.json").write_text(json.dumps(state, indent=2))
    browser.close()
print(json.dumps({k: state[k] for k in ("url", "elapsed_ms")} | {"checks": state["verification"].get("checks")},
                 indent=2))
print("Screencast frames", len(list(frames.glob("*.jpg"))), "errors", errors)
if not passed:
    raise SystemExit("The deep link did not show verified results")
