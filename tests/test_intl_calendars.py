"""Calendars of the Asian and further European venues from exchange_calendars (#134)."""

from datetime import UTC, date, datetime, time

import pandas as pd
import pytest

from portfolio_dashboard import local_source
from portfolio_dashboard.local_source import (
    INTL_VENUES,
    US_VENUE_HOURS,
    VENUE_HOURS,
    venue_calendars,
)
from portfolio_dashboard.watchlist_refresh import session_running
from portfolio_dashboard.watchlist_sheet import EXCHANGES


@pytest.fixture(scope="module")
def calendars():
    return venue_calendars(date(2026, 10, 28))


def test_every_new_venue_has_a_calendar_in_its_own_time_zone(calendars):
    assert set(INTL_VENUES) == set(EXCHANGES) - set(VENUE_HOURS) - set(US_VENUE_HOURS)
    hours = {
        "JPX": (time(9), time(15, 30)),
        "HKG": (time(9, 30), time(16)),
        "NSI": (time(9, 15), time(15, 30)),
        "KSC": (time(9), time(15, 30)),
        "TAI": (time(9), time(13, 30)),
    }
    for venue in INTL_VENUES:
        calendar = calendars[venue]
        expected = hours.get(venue, (time(9), time(17, 30)))
        assert (calendar.open_time, calendar.close_time) == expected
        assert date(2026, 10, 28) in calendar.sessions  # ordinary Wednesday
        assert all(day.weekday() < 5 for day in calendar.sessions)


@pytest.mark.parametrize(
    ("venue", "holiday", "trading_day"),
    [
        ("JPX", date(2026, 1, 2), date(2026, 1, 5)),  # New Year closure
        ("HKG", date(2026, 2, 17), date(2026, 2, 23)),  # Lunar New Year
        ("NSI", date(2026, 1, 26), date(2026, 1, 27)),  # Republic Day
        ("KSC", date(2026, 1, 1), date(2026, 1, 2)),
        ("TAI", date(2026, 1, 1), date(2026, 1, 2)),
        ("PAR", date(2026, 4, 6), date(2026, 4, 7)),  # Easter Monday
        ("AMS", date(2026, 4, 6), date(2026, 4, 7)),
        ("MIL", date(2026, 4, 6), date(2026, 4, 7)),
        ("MCE", date(2026, 4, 6), date(2026, 4, 7)),
        ("EBS", date(2026, 5, 14), date(2026, 5, 15)),  # Ascension Day
    ],
)
def test_holidays_are_no_sessions(calendars, venue, holiday, trading_day):
    assert holiday not in calendars[venue].sessions
    assert trading_day in calendars[venue].sessions


def test_calendar_span_is_clipped_to_the_recorded_holiday_years(calendars):
    full = calendars["JPX"]
    assert (full.first_day, full.last_day) == (date(2025, 9, 23), date(2027, 10, 29))
    bound = INTL_VENUES["NSI"][2].bound_max()
    if bound is not None and bound.date() < date(2027, 10, 29):
        assert calendars["NSI"].last_day == bound.date()
        assert max(calendars["NSI"].sessions) <= bound.date()


class _UnrecordedCalendar:
    """Library calendar whose holidays end long before the requested span."""

    @staticmethod
    def bound_min():
        return pd.Timestamp("2000-01-01")

    @staticmethod
    def bound_max():
        return pd.Timestamp("2001-12-31")


def test_venue_without_recorded_years_gets_no_calendar(monkeypatch):
    venues = {**INTL_VENUES, "ZZZ": (time(9), time(15), _UnrecordedCalendar)}
    monkeypatch.setattr(local_source, "INTL_VENUES", venues)
    calendars = venue_calendars(date(2026, 10, 28))
    assert "ZZZ" not in calendars and "JPX" in calendars


def test_new_venues_do_not_widen_the_depot_registry():
    assert not set(INTL_VENUES) & set(local_source.DEPOT_VENUES)


@pytest.mark.parametrize(
    ("moment", "running"),
    [
        (datetime(2026, 10, 27, 23, 59, tzinfo=UTC), False),  # 08:59 JST
        (datetime(2026, 10, 28, 0, 0, tzinfo=UTC), True),  # 09:00 JST
        (datetime(2026, 10, 28, 6, 29, tzinfo=UTC), True),  # 15:29 JST
        (datetime(2026, 10, 28, 6, 30, tzinfo=UTC), False),  # 15:30 JST
    ],
)
def test_tokyo_session_is_judged_in_tokyo_time(calendars, moment, running):
    assert session_running(calendars["JPX"], "Asia/Tokyo", moment) is running
