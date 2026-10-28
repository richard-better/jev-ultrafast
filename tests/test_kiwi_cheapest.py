"""Offline checks for the Kiwi cheapest-flight verification. No browsers or model calls."""

import base64
import json
from datetime import datetime, time, timezone

import pytest

from examples.kiwi_cheapest import DEPARTURE, GOALS, verify
from jev_ultrafast import url_map

CHEAPEST = {"price": 29652}
SELECTED = [{"action": "Cheapest 29652 Ft"}, {"action": "Select"}]
MIDDAY = int(datetime.combine(DEPARTURE.date, time(10, 20), tzinfo=timezone.utc).timestamp())


def trip_page(origin="ZRH", destination="LGW", cabin="economy", kind="OW", price="29,652", epoch=MIDDAY,
              continue_button=True, path="/en/search/results/zurich-switzerland/london-united-kingdom/x/no-return/"):
    record = {"r": f"U2:8472:{origin}:{epoch}:{destination}:{epoch + 6300}:{cabin}:False:::U2", "t": kind}
    token = base64.b64encode(json.dumps(record).encode()).decode()
    return {
        "url": f"https://www.kiwi.com{path}?prebooking={token}",
        "text": f"One-way\nEconomy\n1\nPassenger\nTrip details\nZürich\nLondon\n{price} Ft\nContinue",
        "actions": [{"label": "Close"}, *([{"label": "Continue"}] if continue_button else [])],
    }


def test_the_goal_names_the_outcome_and_no_controls():
    assert "Zurich" in GOALS and DEPARTURE.long in GOALS
    assert "Continue" not in GOALS.split("Do not")[0]


def test_the_cheapest_flight_selected_on_its_trip_details_passes():
    result = verify(trip_page(), SELECTED, CHEAPEST)
    assert result["passed"], result["checks"]
    assert (result["selected_price"], result["lowest_listed_price"]) == (29652, 29652)


@pytest.mark.parametrize(("page", "history", "cheapest", "failed"), [
    (trip_page(price="38,629"), SELECTED, CHEAPEST, "cheapest"),
    (trip_page(), SELECTED, None, "cheapest"),
    (trip_page(destination="CDG"), SELECTED, CHEAPEST, "route"),
    (trip_page(origin="BSL"), SELECTED, CHEAPEST, "route"),
    (trip_page(epoch=MIDDAY + 86400), SELECTED, CHEAPEST, "date"),
    (trip_page(kind="RT"), SELECTED, CHEAPEST, "one_way"),
    (trip_page(cabin="business"), SELECTED, CHEAPEST, "economy"),
    (trip_page(continue_button=False), SELECTED, CHEAPEST, "continue_offered"),
    (trip_page(), SELECTED + [{"action": "Continue"}], CHEAPEST, "not_continued"),
    (trip_page(path="/en/booking"), SELECTED, CHEAPEST, "trip_details_page"),
])
def test_each_requirement_is_checked(page, history, cheapest, failed):
    result = verify(page, history, cheapest)
    assert not result["passed"]
    assert not result["checks"][failed]


def test_a_results_page_with_no_selected_flight_fails():
    page = trip_page()
    page["url"] = page["url"].split("?")[0]
    assert not verify(page, SELECTED, CHEAPEST)["passed"]


def test_the_kiwi_map_is_offered_on_kiwi_and_builds_its_url_on_kiwi():
    entries = url_map.load("examples/maps/kiwi.json")
    assert [a["shortcut"] for a in url_map.actions(entries, "https://www.kiwi.com/en/")] == ["kiwi_one_way_results"]
    assert not url_map.actions(entries, "https://www.google.com/travel/flights")
    values = {"origin": "zurich-switzerland", "destination": "london-united-kingdom", "date": DEPARTURE.iso}
    url = url_map.build(entries[0], values)
    assert url == (f"https://www.kiwi.com/en/search/results/zurich-switzerland/london-united-kingdom/"
                   f"{DEPARTURE.iso}/no-return/")
    with pytest.raises(url_map.UrlMapError):
        url_map.build(entries[0], {"origin": "Zurich", "destination": "london-united-kingdom", "date": DEPARTURE.iso})
