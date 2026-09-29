"""Write docs/flights-measurement.json from a verified recording made by record_flights.py.

    uv run python scripts/summarize_recording.py <chosen-take> [<other-take> ...]

Every take is listed with its outcome and time; the chosen one supplies the detailed record.
"""

import argparse
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(folder):
    return json.loads((Path(folder) / "state.json").read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("chosen", type=Path, help="recording folder the video is rendered from")
    parser.add_argument("others", type=Path, nargs="*", help="other takes recorded in the same session")
    parser.add_argument("--selection", default="The median-time verified take of the session.")
    args = parser.parse_args()
    state = load(args.chosen)
    assert state["verification"]["passed"] and not state["recording_errors"]
    decisions = state["decisions"]
    latencies = [d["latency_ms"] for d in decisions]
    takes = [(folder, load(folder)) for folder in [args.chosen, *args.others]]
    summary = {
        "elapsed_ms": state["elapsed_ms"],
        "decision_requests": len(decisions),
        "decision_median_ms": round(statistics.median(latencies)),
        "decision_total_ms": sum(latencies),
        "browser_actions": len(state["history"]),
        "wait_actions": sum(h["kind"] == "wait" for h in state["history"]),
        "models": {
            "decision": sorted({d["model"] for d in decisions}),
            "text": sorted({t["model"] for t in state["text_calls"]}),
        },
        "viewport": {"width": state["page"]["w"], "height": state["page"]["h"]},
        "text_calls": state["text_calls"],
        "verification": state["verification"],
        "source_hashes": state["source_hashes"],
        "tokens": {
            key: sum(d.get("usage", {}).get(key, 0) for d in decisions) for key in ("input_tokens", "output_tokens")
        },
        "task": state["goal"],
        "timing": "First prediction after initial homepage observation through final DONE; setup and independent "
        "final verification excluded.",
        "video": {
            "source_frames": len(list((args.chosen / "screencast").glob("*.jpg"))) + 1,
            "speed": 1,
            "intro_hold_ms": 0,
            "end_hold_ms": 500,
            "crop": "Top 64 px account/navigation strip removed from every frame.",
        },
        "selection": args.selection,
        "takes": [
            {"take": folder.name, "elapsed_ms": take["elapsed_ms"], "verified": take["verification"]["passed"],
             "chosen": folder == args.chosen}
            for folder, take in takes
        ],
        "actions": [
            {k: h.get(k) for k in ("operation", "target", "action", "kind", "text", "executed_ms", "elapsed_ms",
                                   "latency_ms", "text_latency_ms")}
            for h in state["history"]
        ],
    }
    (ROOT / "docs" / "flights-measurement.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: summary[k] for k in ("elapsed_ms", "decision_requests", "decision_median_ms", "models",
                                              "viewport", "takes")}, indent=2))


if __name__ == "__main__":
    main()
