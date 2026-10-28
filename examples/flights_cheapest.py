"""Live Google Flights: choose the cheapest flight and stop at its booking options. Never books.

The route and date are given, but which flight is cheapest is not known in advance: the agent has to
read the results, pick one, and follow it to the page that lists where it can be bought.
"""

import base64
import re
import time
from urllib.parse import parse_qs, quote, urlparse

from jev_ultrafast.browser import Browser
from jev_ultrafast.flight_date import flight_departure

URL = "https://www.google.com/travel/flights?hl=en"
DEPARTURE = flight_departure()
GOALS = (
    f"Find the cheapest one-way flight from Zurich to London on {DEPARTURE.long}, for one adult in economy. "
    "Select it and stop on the page that lists where it can be booked. "
    "Do not continue to any booking site and do not book."
)
# Only used by the independent check, never by the agent.
SEARCH = "https://www.google.com/travel/flights?hl=en&q=" + quote(
    f"Flights from Zurich to London on {DEPARTURE.iso} one way economy 1 adult"
)
LONDON = {"LHR", "LGW", "LCY", "LTN", "STN", "SEN"}
PRICE = re.compile(r"\bfor (\d+) ")
LISTED = re.compile(r"^From (\d+) ")
FLIGHT = re.compile(r"Leaves (.+?) at (\d{1,2}:\d{2} [AP]M) on .+? and arrives at (.+?) at (\d{1,2}:\d{2} [AP]M)")


def flight(label):
    """A flight's identity from a label: departure airport and time, arrival airport and time."""
    match = FLIGHT.search(re.sub(r"\s+", " ", label))
    return match.groups() if match else None


def cheapest_listed():
    """The lowest listed price and the flights listed at it, read from a separate tab after the run.

    The same fare can show a slightly different amount on the results and booking pages, so the
    check compares which flight was selected, not the two pages' numbers.
    """
    browser = Browser(SEARCH)
    try:
        for _ in range(40):
            labels = browser.evaluate(
                "[...document.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label'))"
                ".filter(l => /Select flight/.test(l))"
            ) or []
            listed = [(int(m.group(1)), flight(label)) for label in labels if (m := LISTED.match(label))]
            if listed:
                price = min(p for p, _ in listed)
                return {"price": price, "flights": sorted({f for p, f in listed if p == price and f})}
            time.sleep(0.25)
        return None
    finally:
        browser.close()


def verify(page, history, cheapest):
    """Independent checks on the final page and the executed actions, not the model's DONE answer."""
    parsed = urlparse(page["url"])
    encoded = parse_qs(parsed.query).get("tfs", [""])[0]
    try:
        trip = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except ValueError:
        trip = b""
    offers = [a["label"] for a in page["actions"] if a["label"].startswith("Continue to book")]
    prices = [int(m.group(1)) for label in offers if (m := PRICE.search(label))]
    booked = [h["action"] for h in history if h["action"].startswith("Continue to book")]
    selected = [flight(a["label"]) for a in page["actions"] if a["label"].startswith("Flight details. Departing")]
    selected = [f for f in selected if f]
    checks = {
        "booking_page": parsed.hostname == "www.google.com" and parsed.path == "/travel/flights/booking",
        "route": b"ZRH" in trip and any(code.encode() in trip for code in LONDON),
        "date": DEPARTURE.iso.encode() in trip,
        "one_way": "One way" in page["text"],
        "booking_options": "Booking options" in page["text"] and bool(offers),
        "not_booked": not booked,
        "cheapest": bool(cheapest) and len(selected) == 1 and selected[0] in {tuple(f) for f in cheapest["flights"]},
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "selected_flight": selected,
        "cheapest_listed_flights": cheapest["flights"] if cheapest else None,
        "selected_price": min(prices) if prices else None,
        "lowest_listed_price": cheapest["price"] if cheapest else None,
        "booking_offers": offers,
    }


def verify_settled(browser, page, history, listed=cheapest_listed, settle_s=10, check=None,
                   reached="booking_page"):
    """verify() on the page as the run left it, then again while a booking page is still filling in.

    `strict_passed` is the first, immediate result. A page that reached the final page's URL (the check
    named by `reached`) but had not finished loading gets up to `settle_s` seconds to pass;
    `settled_after_ms` says how long that took,
    so a run that stopped early is visible instead of being either hidden or failed for the check's
    timing. An unreadable cheapest-flight list is retried, then reported as `cheapest_unavailable`
    (the run could not be checked) rather than as an agent failure.
    """
    check = check or verify
    cheapest = None
    for _ in range(3):
        cheapest = listed()
        if cheapest:
            break
    result = check(page, history, cheapest)
    result.update(strict_passed=result["passed"], settled_after_ms=None, cheapest_unavailable=cheapest is None)
    if result["passed"] or cheapest is None or not result["checks"][reached]:
        return result
    start = time.monotonic()
    while time.monotonic() - start < settle_s:
        time.sleep(0.5)
        settled = check(browser.observe(screenshot=False), history, cheapest)
        if settled["passed"]:
            settled.update(strict_passed=False, cheapest_unavailable=False)
            settled["settled_after_ms"] = round((time.monotonic() - start) * 1000)
            return settled
    return result
