"""One live cheapest-flight run driven by Browser Use Pi (ultrafast mode), verified like the Jev arms.

Jev's Browser opens and positions the tab exactly as measure_flights.py does (same viewport, same
start page), Pi drives that tab, and the same independent check runs on the final page. Pi's clock
covers agent.run() only: tab setup, Pi's browser connection and the verification are outside it.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from examples.flights_cheapest import verify_settled  # noqa: E402
from jev_ultrafast import browser as browser_module  # noqa: E402
from jev_ultrafast.browser import Browser  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--model", default="opencode-go/glm-5.3-flash")
parser.add_argument("--reasoning", default="off")
parser.add_argument("--effort", default="low", help="reasoning_effort sent to OpenCode Go")
parser.add_argument("--task", choices=["cheapest", "kiwi"], default="cheapest")
args = parser.parse_args()
if args.task == "kiwi":
    from examples import kiwi_cheapest as task  # noqa: E402

    check, reached = task.verify, task.REACHED
else:
    from examples import flights_cheapest as task  # noqa: E402

    check, reached = task.verify, "booking_page"
GOALS, URL, cheapest_listed = task.GOALS, task.URL, task.cheapest_listed
allowed_hosts = getattr(task, "ALLOWED_HOSTS", {"www.google.com", "consent.google.com"})
args.output.mkdir(parents=True, exist_ok=False)

close_context = task.isolate(browser_module) if hasattr(task, "isolate") else None
browser = Browser(URL)
state = {"task": args.task, "arm": "pi", "goal": GOALS, "start_url": URL}
try:
    completed = subprocess.run(
        ["node", "run.mjs", "--target", browser.target, "--goal", GOALS, "--start-url", URL, "--model", args.model,
         "--reasoning", args.reasoning, "--effort", args.effort],
        cwd=root / "scripts" / "pi", capture_output=True, text=True, env=os.environ, timeout=600,
    )
    (args.output / "pi-stderr.txt").write_text(completed.stderr)
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    state["pi"] = json.loads(lines[-1]) if lines else None
    state["error"] = None if lines and completed.returncode == 0 else f"pi exited {completed.returncode}"
    state["viewport"] = browser.evaluate("[innerWidth, innerHeight]")
    # Fresh observation of the tab Pi left behind, then the same independent check the Jev arms use.
    page = browser.observe(screenshot=True)
    history = browser.call("Page.getNavigationHistory")["entries"]
    visited = {urlparse(entry["url"]).hostname or "" for entry in history}
    hosts = sorted(visited - {""} - allowed_hosts)
    # The Jev arms count executed "Continue to book" actions; Pi has no such log, so a visit to any
    # host other than Google in this tab's history stands in for it.
    verification = verify_settled(browser, page, [], cheapest_listed, check=check, reached=reached)
    verification["checks"]["not_booked"] = not hosts
    verification["passed"] = all(verification["checks"].values())
    verification["strict_passed"] = verification["strict_passed"] and not hosts
    verification["left_google"] = hosts
    state["final_page"] = page
    state["verification"] = verification
    state["elapsed_ms"] = state["pi"]["wall_ms"] if state["pi"] else None
finally:
    (args.output / "state.json").write_text(json.dumps(state, indent=2))
    browser.close()
    if close_context:
        close_context()
print("VERIFIED", state.get("verification", {}).get("passed"), "ERROR", state.get("error"), flush=True)
