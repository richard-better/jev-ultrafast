"""Live Kiwi.com: choose the cheapest flight and stop at its trip details. Never continues to booking.

The same route, date and outcome as examples/flights_cheapest.py on another site: the route and date
are given, but which flight is cheapest has to be read from the results. Kiwi's trip details panel,
with its Continue button, is where the run stops.
"""

import base64
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, urlparse

from browser_harness.helpers import cdp

from jev_ultrafast.browser import Browser
from jev_ultrafast.flight_date import flight_departure

URL = "https://www.kiwi.com/en/"
DEPARTURE = flight_departure()
GOALS = (
    f"Find the cheapest one-way flight from Zurich to London on {DEPARTURE.long}, for one adult in economy. "
    "Select it and stop where its trip details are shown with the option to continue to booking. "
    "Do not continue and do not book."
)
# Only used by the independent check, never by the agent.
SEARCH = f"https://www.kiwi.com/en/search/results/zurich-switzerland/london-united-kingdom/{quote(DEPARTURE.iso)}/no-return/"
LONDON = {"LHR", "LGW", "LCY", "LTN", "STN", "SEN"}
# Hosts the tab may visit without having left the search for a booking.
ALLOWED_HOSTS = {"www.kiwi.com"}
# The check whose result says the run reached the page it should stop on.
REACHED = "trip_details_page"
CHEAPEST_TAB = re.compile(r"Cheapest\s+([\d,]+)\s*Ft")
SELECTED_PRICE = re.compile(r"([\d,]+)\s*Ft\s+Continue\b")


def isolate(browser_module):
    """Open a fresh browser context and make every tab `browser_module` opens live in it.

    Kiwi remembers the last search for the browser profile and prefills its form with it, which no
    clearing of cookies or storage undid, and which would hand a run the answer to its own goal. A fresh
    context starts as a first-time visitor, with an empty form and the consent dialog, the same for
    every arm. Returns a function that closes the context.
    """
    context = cdp("Target.createBrowserContext")["browserContextId"]
    inner = browser_module.cdp

    def in_context(method, *args, **kwargs):
        if method == "Target.createTarget":
            kwargs["browserContextId"] = context
        return inner(method, *args, **kwargs)

    browser_module.cdp = in_context

    def close():
        browser_module.cdp = inner
        cdp("Target.disposeBrowserContext", browserContextId=context)

    return close


def cheapest_listed():
    """The lowest listed price, read from Kiwi's own Cheapest tab in a separate tab after the run."""
    browser = Browser(SEARCH)
    try:
        for _ in range(60):
            text = (browser.evaluate("document.body.innerText") or "").replace("\xa0", " ")
            if match := CHEAPEST_TAB.search(text):
                return {"price": int(match.group(1).replace(",", ""))}
            time.sleep(0.5)
        return None
    finally:
        browser.close()


def prebooking(url):
    """The itinerary Kiwi encoded into the selected flight's link, or None."""
    token = parse_qs(urlparse(url).query).get("prebooking", [""])[0]
    try:
        return json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    except ValueError:
        return None


def verify(page, history, cheapest):
    """Independent checks on the final page and the executed actions, not the model's DONE answer."""
    parsed = urlparse(page["url"])
    text = re.sub(r"\s+", " ", page["text"].replace("\xa0", " "))
    trip = prebooking(page["url"]) or {}
    fields = str(trip.get("r", "")).split(":")
    fields += [""] * (7 - len(fields))
    origin, departs, destination, cabin = fields[2], fields[3], fields[4], fields[6]
    try:
        date = datetime.fromtimestamp(int(departs), timezone.utc).date().isoformat()
    except ValueError:
        date = None
    price = SELECTED_PRICE.search(text)
    selected_price = int(price.group(1).replace(",", "")) if price else None
    continued = [h["action"] for h in history if h["action"].strip().startswith("Continue")]
    checks = {
        # A selected flight opens its trip details on the results page; continuing would leave it.
        REACHED: parsed.hostname == "www.kiwi.com" and parsed.path.startswith("/en/search/results")
        and bool(trip) and "Trip details" in text,
        "route": origin == "ZRH" and destination in LONDON,
        "date": date == DEPARTURE.iso,
        "one_way": trip.get("t") == "OW" and "One-way" in text,
        "passengers": "1 Passenger" in text,
        "economy": cabin == "economy",
        "continue_offered": any(a["label"].strip() == "Continue" for a in page["actions"]),
        "not_continued": not continued,
        "cheapest": bool(cheapest) and selected_price == cheapest["price"],
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "selected_flight": trip.get("r"),
        "selected_price": selected_price,
        "lowest_listed_price": cheapest["price"] if cheapest else None,
    }
