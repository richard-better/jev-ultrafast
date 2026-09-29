"""A URL map shortcut through the real browser path. No model calls: the choice and parameters are scripted.

Checks that the Google Flights example map is offered on its site, that choosing it navigates to the
URL built from validated parameters, that the run logs it before observing, and that the resulting
page shows the requested search.
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from examples.flights import DEPARTURE, verify  # noqa: E402
from jev_ultrafast import agent as loop  # noqa: E402

MAP = Path(__file__).resolve().parents[1] / "examples" / "maps" / "google-flights.json"
VALUES = {"origin": "Zurich", "destination": "London", "date": DEPARTURE.iso, "cabin": "economy", "adults": 1}
CONTENT_CHECKS = ("one_way", "origin", "destination", "date", "year", "results")


def decision(choice, operation):
    return {"choice": choice, "operation": operation, "target": None, "confidence": 1.0,
            "probabilities": {choice: 1.0}, "latency_ms": 0, "usage": {}}


def main():
    # Open the shortcut once, then finish. A DONE made while results still load is rightly
    # discarded as stale and asked again, so answer DONE for every later decision.
    calls = []

    def choose(*_):
        calls.append(1)
        if len(calls) == 1:
            return decision("open_google_flights_one_way", "OPEN_GOOGLE_FLIGHTS_ONE_WAY")
        return decision("DONE", "DONE")

    with patch.object(loop, "choose", side_effect=choose), \
            patch.object(loop, "shortcut_parameters", return_value=(VALUES, {"model": "scripted", "latency_ms": 0})):
        with loop.Agent("https://www.google.com/travel/flights?hl=en", "Scripted", url_map=str(MAP)) as agent:
            offered = [a["id"] for a in agent.state["page"]["actions"] if a["kind"] == "shortcut"]
            assert offered == ["open_google_flights_one_way", "open_google_flights_round_trip"], offered
            print("PASS: both entries offered on Google Flights")
            for state in agent.run():
                pass
            opened = state["history"][0]
            assert opened["kind"] == "shortcut" and opened["execution"] == "confirmed", opened
            assert opened["text"].startswith("https://www.google.com/travel/flights?hl=en&q=Flights%20from%20Zurich")
            assert opened["page_changed"] is True
            print("PASS: the chosen entry navigated to its built URL and was logged as a shortcut")
            # Google's results arrive after the navigation settles; allow them up to 15 seconds.
            deadline = time.monotonic() + 15
            while True:
                checks = verify(agent.browser.observe(screenshot=False))["checks"]
                if all(checks[name] for name in CONTENT_CHECKS) or time.monotonic() > deadline:
                    break
                time.sleep(0.25)
            assert all(checks[name] for name in CONTENT_CHECKS), checks
            print("PASS: the page shows the requested route, date and results")
    elsewhere = loop.url_maps.actions(loop.url_maps.load(str(MAP)), "https://www.google.com/search?q=flights")
    assert elsewhere == [], elsewhere
    print("PASS: not offered outside Google Flights")
    print("4/4 URL map checks passed; no model calls")


if __name__ == "__main__":
    main()
