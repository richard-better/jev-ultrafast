"""A measured live run with continuous CDP screencast; original timestamps retained."""

import argparse
import base64
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

from browser_harness.helpers import drain_events

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jev_ultrafast import Agent  # noqa: E402
from jev_ultrafast.browser import viewport  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("folder", type=Path, nargs="?", default=Path("artifacts/flights/recorded"))
parser.add_argument("--task", choices=["search", "cheapest"], default="search")
args = parser.parse_args()
if args.task == "cheapest":
    from examples.flights_cheapest import GOALS, URL, cheapest_listed  # noqa: E402
    from examples.flights_cheapest import verify as verify_booking  # noqa: E402

    def verify(page, state):
        # The cheapest listed flights are read in a separate tab after the recording, outside the measured time.
        return verify_booking(page, state["history"], cheapest_listed())
else:
    from examples.flights import GOALS, URL  # noqa: E402
    from examples.flights import verify as verify_search  # noqa: E402

    def verify(page, state):
        return verify_search(page)

folder = args.folder
folder.mkdir(parents=True, exist_ok=False)
source_hashes = {
    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
    for p in (Path(__file__).resolve().parents[1] / "jev_ultrafast").iterdir()
    if p.suffix in {".py", ".js"}
}
agent = Agent(URL, GOALS)
(folder / "frames").mkdir(exist_ok=True)
(folder / "frames" / "000000.jpg").write_bytes(
    base64.b64decode(agent.browser.call("Page.captureScreenshot", format="jpeg", quality=85)["data"])
)
frames = folder / "screencast"
frames.mkdir(exist_ok=True)
stop = threading.Event()
epoch = time.time()
errors = []


def capture():
    try:
        while not stop.is_set():
            for event in drain_events():
                if event["method"] != "Page.screencastFrame" or event.get("session_id") != agent.browser.session:
                    continue
                p = event["params"]
                timestamp = max(0, round((p["metadata"]["timestamp"] - epoch) * 1000))
                (frames / f"{timestamp:06d}.jpg").write_bytes(base64.b64decode(p["data"]))
                agent.browser.call("Page.screencastFrameAck", sessionId=p["sessionId"])
            stop.wait(0.015)
    except Exception as e:
        errors.append(str(e))


width, height = viewport()
agent.browser.call(
    "Page.startScreencast", format="jpeg", quality=80, maxWidth=width, maxHeight=height, everyNthFrame=2
)
worker = threading.Thread(target=capture, daemon=True)
worker.start()
# The first prediction starts the run timer; this anchors video timestamps to it.
epoch = time.time()
try:
    for state in agent.run():
        action = state["history"][-1]["action"] if state["history"] else ""
        print(state["elapsed_ms"], state["status"], action, flush=True)
finally:
    time.sleep(0.08)  # Drain the last frame, outside the reported agent time.
    stop.set()
    worker.join(timeout=3)
    agent.browser.call("Page.stopScreencast")
    state = agent.snapshot()
    state["final_page"] = agent.browser.observe(screenshot=False)
    state["verification"] = verify(state["final_page"], state)
    state["task"] = args.task
    state["url_map"] = os.environ.get("JEV_URL_MAP") or None
    state["source_hashes"] = source_hashes
    state["recording_errors"] = errors
    (folder / "state.json").write_text(json.dumps(state, indent=2))
    (folder / "session.json").write_text(json.dumps({"target": agent.browser.target, "session": agent.browser.session}))
print(json.dumps(state["verification"], indent=2))
print("Screencast frames", len(list(frames.glob("*.jpg"))), "errors", errors)
if not state["verification"]["passed"]:
    raise SystemExit("Final-page verification failed")
