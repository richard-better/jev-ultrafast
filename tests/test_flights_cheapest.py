"""Offline checks for the cheapest-flight task's independent verification. No browsers or model calls."""

import base64

import pytest

from examples.flights_cheapest import DEPARTURE, GOALS, verify


def booking_page(trip=b"ZRH LGW", price=23299, loading=False):
    tfs = base64.urlsafe_b64encode(DEPARTURE.iso.encode() + b" " + trip).decode().rstrip("=")
    offer = f"Continue to book with easyJet airline for {price} Hungarian forints (Equivalent to 60 Swiss francs)"
    offers = [] if loading else [{"label": offer}]
    status = "Getting prices" if loading else "Booking options\nBook with easyJet"
    text = "Zürich\nLondon\nOne way\nEconomy\n" + status
    return {
        "url": f"https://www.google.com/travel/flights/booking?tfs={tfs}",
        "text": text,
        "actions": [{"label": "Go back"}, *offers],
    }


SELECTED = [{"action": "Cheapest from 23299 Hungarian forints"}, {"action": "Select flight"}]


def test_the_cheapest_flights_booking_options_pass():
    result = verify(booking_page(), SELECTED, lowest=23299)
    assert result["passed"], result["checks"]
    assert result["selected_price"] == 23299


@pytest.mark.parametrize(("page", "history", "lowest", "failed"), [
    (booking_page(price=27982), SELECTED, 23299, "cheapest"),
    (booking_page(), SELECTED + [{"action": "Continue to book with easyJet airline for 23299 Hungarian forints"}],
     23299, "not_booked"),
    (booking_page(loading=True), SELECTED, 23299, "booking_options"),
    (booking_page(trip=b"ZRH CDG"), SELECTED, 23299, "route"),
    (booking_page(), SELECTED, None, "cheapest"),
])
def test_each_requirement_is_checked(page, history, lowest, failed):
    result = verify(page, history, lowest)
    assert not result["passed"]
    assert not result["checks"][failed]


def test_the_results_page_is_not_the_booking_page():
    page = booking_page()
    page["url"] = page["url"].replace("/booking?", "/search?")
    assert not verify(page, SELECTED, 23299)["checks"]["booking_page"]


def test_the_goal_names_the_task_not_a_plan():
    assert DEPARTURE.long in GOALS and "cheapest" in GOALS and "Do not continue to any booking site" in GOALS
    assert "Cheapest tab" not in GOALS and "Select flight" not in GOALS
