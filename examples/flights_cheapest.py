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


def lowest_listed_price():
    """The lowest price Google lists for the search, read from a separate tab after the run."""
    browser = Browser(SEARCH)
    try:
        for _ in range(40):
            labels = browser.evaluate(
                "[...document.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label'))"
                ".filter(l => /Select flight/.test(l))"
            ) or []
            prices = [int(m.group(1)) for label in labels if (m := LISTED.match(label))]
            if prices:
                return min(prices)
            time.sleep(0.25)
        return None
    finally:
        browser.close()


def verify(page, history, lowest):
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
    checks = {
        "booking_page": parsed.hostname == "www.google.com" and parsed.path == "/travel/flights/booking",
        "route": b"ZRH" in trip and any(code.encode() in trip for code in LONDON),
        "date": DEPARTURE.iso.encode() in trip,
        "one_way": "One way" in page["text"],
        "booking_options": "Booking options" in page["text"] and bool(offers),
        "not_booked": not booked,
        "cheapest": bool(prices) and lowest is not None and min(prices) <= lowest,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "selected_price": min(prices) if prices else None,
        "lowest_listed_price": lowest,
        "booking_offers": offers,
    }
