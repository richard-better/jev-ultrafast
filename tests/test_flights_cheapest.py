"""Offline checks for the cheapest-flight task's independent verification. No browsers or model calls."""

import base64

import pytest

from examples.flights_cheapest import DEPARTURE, GOALS, flight, verify, verify_settled

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


class Pages:
    """A browser that shows each page in turn, the last one repeatedly."""

    def __init__(self, *pages):
        self.pages = list(pages)

    def observe(self, screenshot=True):
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]


def test_a_booking_page_that_finishes_loading_passes_and_says_it_was_late():
    result = verify_settled(Pages(booking_page()), booking_page(loading=True), SELECTED, lambda: CHEAPEST)
    assert result["passed"]
    assert result["strict_passed"] is False
    assert result["settled_after_ms"] is not None


def test_a_booking_page_that_never_finishes_loading_still_fails(monkeypatch):
    result = verify_settled(Pages(booking_page(loading=True)), booking_page(loading=True), SELECTED,
                            lambda: CHEAPEST, settle_s=1)
    assert not result["passed"]
    assert result["settled_after_ms"] is None


def test_an_immediate_pass_is_strict():
    result = verify_settled(Pages(), booking_page(), SELECTED, lambda: CHEAPEST)
    assert result["passed"] and result["strict_passed"]


def test_an_unreadable_cheapest_list_is_retried_then_reported_as_unverifiable():
    reads = []

    def unreadable():
        reads.append(1)

    result = verify_settled(Pages(), booking_page(), SELECTED, unreadable)
    assert len(reads) == 3
    assert result["cheapest_unavailable"] and not result["passed"]
    assert verify_settled(Pages(), booking_page(), SELECTED, iter([None, CHEAPEST]).__next__)["passed"]
