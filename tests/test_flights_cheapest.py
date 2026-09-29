"""Offline checks for the cheapest-flight task's independent verification. No browsers or model calls."""

import base64

import pytest

from examples.flights_cheapest import DEPARTURE, GOALS, flight, verify

EASYJET = ("Leaves Zurich Airport at 11:20 AM on Thursday, October 29 and arrives at London Gatwick Airport "
           "at 12:05 PM on Thursday, October 29.")
BRITISH = ("Leaves Zurich Airport at 6:15 AM on Thursday, October 29 and arrives at Heathrow Airport at 7:10 AM on "
           "Thursday, October 29.")
# The results page listed the same easyJet fare 20 forints lower than its booking page showed.
CHEAPEST = {"price": 23279, "flights": [flight(EASYJET)]}


def booking_page(trip=b"ZRH LGW", selected=EASYJET, price=23299, loading=False):
    tfs = base64.urlsafe_b64encode(DEPARTURE.iso.encode() + b" " + trip).decode().rstrip("=")
    offer = f"Continue to book with easyJet airline for {price} Hungarian forints (Equivalent to 60 Swiss francs)"
    offers = [] if loading else [{"label": offer}]
    status = "Getting prices" if loading else "Booking options\nBook with easyJet"
    return {
        "url": f"https://www.google.com/travel/flights/booking?tfs={tfs}",
        "text": "Zürich\nLondon\nOne way\nEconomy\n" + status,
        "actions": [
            {"label": "Go back"},
            {"label": f"Flight details. Departing flight on Thursday, October 29. {selected}"},
            *offers,
        ],
    }


SELECTED = [{"action": "Cheapest from 23279 Hungarian forints"}, {"action": "Select flight"}]


def test_flights_are_identified_by_their_airports_and_times():
    assert flight(EASYJET) == ("Zurich Airport", "11:20 AM", "London Gatwick Airport", "12:05 PM")
    assert flight("Carbon emissions estimate: 86 kilograms") is None


def test_the_cheapest_flight_passes_even_when_its_pages_show_different_amounts():
    result = verify(booking_page(), SELECTED, CHEAPEST)
    assert result["passed"], result["checks"]
    assert (result["selected_price"], result["lowest_listed_price"]) == (23299, 23279)


@pytest.mark.parametrize(("page", "history", "cheapest", "failed"), [
    (booking_page(selected=BRITISH), SELECTED, CHEAPEST, "cheapest"),
    (booking_page(), SELECTED + [{"action": "Continue to book with easyJet airline for 23299 Hungarian forints"}],
     CHEAPEST, "not_booked"),
    (booking_page(loading=True), SELECTED, CHEAPEST, "booking_options"),
    (booking_page(trip=b"ZRH CDG"), SELECTED, CHEAPEST, "route"),
    (booking_page(), SELECTED, None, "cheapest"),
])
def test_each_requirement_is_checked(page, history, cheapest, failed):
    result = verify(page, history, cheapest)
    assert not result["passed"]
    assert not result["checks"][failed]


def test_the_results_page_is_not_the_booking_page():
    page = booking_page()
    page["url"] = page["url"].replace("/booking?", "/search?")
    assert not verify(page, SELECTED, CHEAPEST)["checks"]["booking_page"]


def test_the_goal_names_the_task_not_a_plan():
    assert DEPARTURE.long in GOALS and "cheapest" in GOALS and "Do not continue to any booking site" in GOALS
    assert "Cheapest tab" not in GOALS and "Select flight" not in GOALS
