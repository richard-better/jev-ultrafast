"""Interleaved live comparison on the cheapest-flight task: Jev, Jev with the Google Flights URL map,
and Browser Use Pi (ultrafast mode). Every arm is verified by the same independent check.

Paid API calls. Run with: uv run --env-file .env python scripts/compare_arms.py --runs 5
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
MAPS = {"cheapest": "google-flights.json", "kiwi": "kiwi.json"}
parser = argparse.ArgumentParser()
parser.add_argument("--task", choices=list(MAPS), default="cheapest", help="cheapest: Google Flights; kiwi: Kiwi.com")
parser.add_argument("--runs", type=int, default=5)
parser.add_argument("--arms", nargs="+", default=["jev", "jev-map", "pi"], choices=["jev", "jev-map", "pi"])
parser.add_argument("--output", type=Path, default=root / "artifacts" / "compare" / time.strftime("%Y%m%d-%H%M%S"))
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
MAP = str(root / "examples" / "maps" / MAPS[args.task])
ARMS = {
    "jev": ["scripts/measure_flights.py", "--task", args.task],
    "jev-map": ["scripts/measure_flights.py", "--task", args.task],
    "pi": ["scripts/bench_pi.py", "--task", args.task],
}


def run(arm, index):
    folder = args.output / f"{arm}-{index}"
    env = {key: value for key, value in os.environ.items() if key != "JEV_URL_MAP"}
    if arm == "jev-map":
        env["JEV_URL_MAP"] = MAP
    started = time.perf_counter()
    result = subprocess.run(
        [sys.executable, *ARMS[arm], "--output", str(folder)], cwd=root, env=env, capture_output=True, text=True
    )
    (args.output / f"{arm}-{index}.log").write_text(result.stdout + result.stderr)
    try:
        state = json.loads((folder / "state.json").read_text())
    except (OSError, ValueError):
        state = {}
    row = {
        "arm": arm,
        "run": index,
        "verified": state.get("verification", {}).get("passed") is True,
        "strict_verified": state.get("verification", {}).get("strict_passed") is True,
        "settled_after_ms": state.get("verification", {}).get("settled_after_ms"),
        "unverifiable": state.get("verification", {}).get("cheapest_unavailable") is True,
        "failed_checks": [k for k, v in state.get("verification", {}).get("checks", {}).items() if not v],
        "elapsed_ms": state.get("elapsed_ms"),
        "error": state.get("error") or (None if result.returncode == 0 else f"exit {result.returncode}"),
        "process_s": round(time.perf_counter() - started, 1),
    }
    if arm == "pi":
        pi = state.get("pi") or {}
        row |= {"model_calls": pi.get("steps"), "status": pi.get("status"), "usage": pi.get("usage")}
    else:
        row |= {"model_calls": len(state["decisions"]) if "decisions" in state else None}
    print(json.dumps(row), flush=True)
    return row


rows = []
for index in range(1, args.runs + 1):
    # Rotate the order each round so no arm always runs first or right after the same neighbour.
    shift = (index - 1) % len(args.arms)
    for arm in args.arms[shift:] + args.arms[:shift]:
        rows.append(run(arm, index))

summary = {}
for arm in args.arms:
    mine = [r for r in rows if r["arm"] == arm]
    passed = [r["elapsed_ms"] for r in mine if r["verified"] and r["elapsed_ms"] is not None]
    summary[arm] = {
        "verified": f"{len(passed)}/{len(mine)}",
        "verified_strict": f"{sum(r['strict_verified'] for r in mine)}/{len(mine)}",
        "unverifiable": sum(r["unverifiable"] for r in mine),
        "median_ms_verified": round(statistics.median(passed)) if passed else None,
        "min_ms": min(passed) if passed else None,
        "max_ms": max(passed) if passed else None,
        "median_model_calls": statistics.median(c for r in mine if (c := r.get("model_calls")) is not None)
        if any(r.get("model_calls") is not None for r in mine)
        else None,
    }
(args.output / "summary.json").write_text(json.dumps({"rows": rows, "summary": summary}, indent=2))
print(json.dumps(summary, indent=2))
