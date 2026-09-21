"""The one departure date every flights-demo surface must agree on.

Google Flights marks past-month days aria-hidden, and snapshot.js excludes
aria-hidden elements from the agent's action space. A goal naming a fixed
date becomes unreachable the day that date passes. Compute it at runtime
instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

DEPARTURE_OFFSET_DAYS = 30


@dataclass(frozen=True)
class FlightDate:
    """A calendar date rendered in every format the flights demo needs."""

    date: date

    @property
    def iso(self) -> str:
        """``2026-10-21``, as it appears in the Google Flights URL."""
        return self.date.isoformat()

    @property
    def long(self) -> str:
        """``October 21, 2026``, for the goal text."""
        return f"{self.date:%B} {self.date.day}, {self.date.year}"

    @property
    def short_weekday(self) -> str:
        """``Wed, Oct 21``, matching the Departure field's rendered value."""
        return f"{self.date:%a}, {self.date:%b} {self.date.day}"

    @property
    def long_weekday(self) -> str:
        """``Wednesday, October 21``, matching a flight result's label."""
        return f"{self.date:%A}, {self.date:%B} {self.date.day}"

    @property
    def goal_text(self) -> str:
        """The flights demo's goal, naming this date."""
        return (
            f"Find one-way flights from Zurich to London on {self.long}, for one adult in "
            "economy. Stop when matching flight options are visible. Do not select or book "
            "a flight."
        )


def flight_departure(today: date | None = None) -> FlightDate:
    """The flights demo's departure date, always far enough ahead to stay indexed.

    Args:
        today: Override for the current date (tests only); defaults to the real date.

    Returns:
        The shared date plus every rendering the demo's surfaces need.
    """
    return FlightDate((today or date.today()) + timedelta(days=DEPARTURE_OFFSET_DAYS))
