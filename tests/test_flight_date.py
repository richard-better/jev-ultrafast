"""Offline checks for the flights demo's runtime-computed departure date."""

from datetime import date, timedelta

from jev_ultrafast.flight_date import flight_departure


def test_flight_departure_defaults_to_today_plus_thirty_days():
    assert flight_departure().date == date.today() + timedelta(days=30)


def test_flight_departure_formats_have_no_leading_zero_day():
    departure = flight_departure(today=date(2026, 9, 4))
    expected = date(2026, 10, 4)
    assert departure.date == expected
    assert departure.iso == "2026-10-04"
    assert departure.long == "October 4, 2026"
    assert departure.short_weekday == f"{expected:%a}, Oct 4"
    assert departure.long_weekday == f"{expected:%A}, October 4"
    assert "October 4, 2026" in departure.goal_text
    assert "2026-10-04" not in departure.goal_text
