"""Deterministic German and US exchange calendars and exchange-local time (#84)."""

from datetime import UTC, date, datetime, time

import pytest

from portfolio_dashboard import local_source
from portfolio_dashboard.local_source import (
    US_VENUE_HOURS,
    VENUE_HOURS,
    ListingsFileError,
    parse_listings,
    us_holidays,
    venue_calendars,
)
from portfolio_dashboard.watchlist_refresh import session_running
from portfolio_dashboard.watchlist_sheet import EXCHANGES

US_VENUES = ("NMS", "NGM", "NCM", "NYQ", "ASE", "PCX")
GERMAN_VENUES = ("GER", "FRA", "STU", "MUN", "DUS")

# Closures of the published NYSE calendars, including observed dates.
EXPECTED = {
    2020: [
        (1, 1), (1, 20), (2, 17), (4, 10), (5, 25), (7, 3), (9, 7), (11, 26), (12, 25),
    ],
    2021: [
        (1, 1), (1, 18), (2, 15), (4, 2), (5, 31), (7, 5), (9, 6), (11, 25), (12, 24),
    ],
    # 1 January 2022 was a Saturday: 31 December 2021 stayed open.
    2022: [
        (1, 17), (2, 21), (4, 15), (5, 30), (6, 20), (7, 4), (9, 5), (11, 24), (12, 26),
    ],
    2023: [
        (1, 2), (1, 16), (2, 20), (4, 7), (5, 29), (6, 19), (7, 4), (9, 4), (11, 23),
        (12, 25),
    ],
    2024: [
        (1, 1), (1, 15), (2, 19), (3, 29), (5, 27), (6, 19), (7, 4), (9, 2), (11, 28),
        (12, 25),
    ],
    2025: [
        (1, 1), (1, 20), (2, 17), (4, 18), (5, 26), (6, 19), (7, 4), (9, 1), (11, 27),
        (12, 25),
    ],
    2026: [
        (1, 1), (1, 19), (2, 16), (4, 3), (5, 25), (6, 19), (7, 3), (9, 7), (11, 26),
        (12, 25),
    ],
    # Juneteenth on a Saturday closes Friday; Christmas on a Saturday as well.
    2027: [
        (1, 1), (1, 18), (2, 15), (3, 26), (5, 31), (6, 18), (7, 5), (9, 6), (11, 25),
        (12, 24),
    ],
    # 1 January 2028 is a Saturday: no closure on 31 December 2027.
    2028: [
        (1, 17), (2, 21), (4, 14), (5, 29), (6, 19), (7, 4), (9, 4), (11, 23), (12, 25),
    ],
}


@pytest.mark.parametrize("year", sorted(EXPECTED))
def test_us_holidays_match_the_published_exchange_calendar(year):
    assert us_holidays(year) == {date(year, m, d) for m, d in EXPECTED[year]}


def test_good_friday_is_closed_in_every_year_and_follows_easter():
    # Easter Sunday: 2024-03-31, 2025-04-20, 2026-04-05, 2027-03-28.
    for good_friday in (
        date(2024, 3, 29),
        date(2025, 4, 18),
        date(2026, 4, 3),
        date(2027, 3, 26),
    ):
        assert good_friday in us_holidays(good_friday.year)


def test_weekend_substitution_saturday_to_friday_and_sunday_to_monday():
    # Independence Day: Saturday 2026, Sunday 2021 and 2027, Tuesday 2023.
    assert date(2026, 7, 3) in us_holidays(2026) and date(2026, 7, 4) not in us_holidays(2026)
    assert date(2021, 7, 5) in us_holidays(2021)
    assert date(2027, 7, 5) in us_holidays(2027)
    assert date(2023, 7, 4) in us_holidays(2023)
    # Christmas: Saturday 2027 -> Friday 24th, Sunday 2022 -> Monday 26th.
    assert date(2027, 12, 24) in us_holidays(2027)
    assert date(2022, 12, 26) in us_holidays(2022)
    # New Year's Day on a Sunday moves to Monday, on a Saturday it is not observed.
    assert date(2023, 1, 2) in us_holidays(2023)
    assert date(2021, 12, 31) not in us_holidays(2021)
    assert date(2027, 12, 31) not in us_holidays(2027)


def test_juneteenth_is_a_holiday_only_from_2022():
    assert date(2021, 6, 18) not in us_holidays(2021)
    assert date(2021, 6, 19) not in us_holidays(2021)
    assert date(2022, 6, 20) in us_holidays(2022)


def test_us_calendars_share_hours_and_sessions_and_skip_weekends_and_holidays():
    calendars = venue_calendars(date(2026, 10, 28))
    reference = calendars["NYQ"]
    assert (reference.open_time, reference.close_time) == (time(9, 30), time(16))
    for venue in US_VENUES:
        calendar = calendars[venue]
        assert calendar.sessions == reference.sessions
        assert (calendar.open_time, calendar.close_time) == (time(9, 30), time(16))
        assert (calendar.first_day, calendar.last_day) == (
            date(2025, 9, 23),
            date(2027, 10, 29),
        )
    assert all(day.weekday() < 5 for day in reference.sessions)
    closed = [
        date(2025, 11, 27),  # Thanksgiving
        date(2025, 12, 25),
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),  # Good Friday
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),  # observed Independence Day
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2027, 3, 26),  # Good Friday 2027
    ]
    assert not set(closed) & reference.sessions
    # Early closes are not modelled: they stay regular sessions.
    assert {date(2025, 11, 28), date(2025, 12, 24)} <= reference.sessions


def test_special_unscheduled_closures_are_not_sessions():
    calendars = venue_calendars(date(2025, 1, 30))
    assert date(2025, 1, 9) not in calendars["NMS"].sessions  # national day of mourning
    assert date(2025, 1, 8) in calendars["NMS"].sessions
    assert date(2025, 1, 9) in calendars["GER"].sessions
    old = venue_calendars(date(2012, 11, 15))["NYQ"].sessions
    assert not {date(2012, 10, 29), date(2012, 10, 30)} & old
    assert date(2012, 10, 31) in old


def test_german_and_us_calendars_differ_by_their_own_holidays():
    calendars = venue_calendars(date(2026, 10, 28))
    # US Thanksgiving: German venues trade, US venues do not.
    assert date(2026, 11, 26) in calendars["GER"].sessions
    assert date(2026, 11, 26) not in calendars["NMS"].sessions
    # 1 May and Easter Monday: German venues are closed, US venues trade.
    for day in (date(2026, 5, 1), date(2026, 4, 6)):
        assert day not in calendars["GER"].sessions
        assert day in calendars["NMS"].sessions
    # Good Friday closes both.
    assert date(2026, 4, 3) not in calendars["GER"].sessions | calendars["NMS"].sessions
    assert (calendars["GER"].open_time, calendars["GER"].close_time) == (
        time(9),
        time(17, 30),
    )
    assert set(calendars) == set(GERMAN_VENUES) | set(US_VENUES)


def test_calendars_are_deterministic_and_cover_every_watchlist_exchange():
    first = venue_calendars(date(2026, 10, 28))
    assert first == venue_calendars(date(2026, 10, 28))
    assert set(first) == set(EXCHANGES)
    for venue in EXCHANGES:
        assert first[venue].first_day < date(2025, 10, 28) < first[venue].last_day


def test_us_venues_do_not_widen_the_depot_listing_registry():
    assert set(US_VENUE_HOURS) == set(US_VENUES) and set(VENUE_HOURS) == set(GERMAN_VENUES)
    entry = {
        "type": "stock",
        "isin": "DE000SYN0001",
        "symbol": "SYN",
        "venue": "NMS",
        "equity_form": "share",
    }
    with pytest.raises(ListingsFileError):
        parse_listings([entry])
    assert parse_listings([{**entry, "venue": "GER"}])
    assert local_source.VENUE_HOURS is VENUE_HOURS


# --- exchange-local sessions across time zones and DST changes -----------------


def instant(*args):
    return datetime(*args, tzinfo=UTC)


@pytest.fixture(scope="module")
def calendars():
    return venue_calendars(date(2026, 10, 28))


@pytest.mark.parametrize(
    "moment,new_york,berlin",
    [
        # 25 Oct 2026: Europe is on CET, the US still on EDT -> 5 hours apart.
        (instant(2026, 10, 28, 13, 29), False, True),  # 09:29 EDT / 14:29 CET
        (instant(2026, 10, 28, 13, 30), True, True),  # 09:30 EDT / 14:30 CET
        (instant(2026, 10, 28, 19, 59), True, False),  # 15:59 EDT / 20:59 CET
        (instant(2026, 10, 28, 20, 0), False, False),  # 16:00 EDT: closed
        # 1 Nov 2026: US switches to EST -> 6 hours apart again.
        (instant(2026, 11, 2, 14, 29), False, True),  # 09:29 EST / 15:29 CET
        (instant(2026, 11, 2, 14, 30), True, True),  # 09:30 EST
        (instant(2026, 11, 2, 20, 59), True, False),  # 15:59 EST / 21:59 CET
        (instant(2026, 11, 2, 21, 0), False, False),
    ],
)
def test_session_running_uses_each_exchanges_own_time_zone(
    calendars, moment, new_york, berlin
):
    assert session_running(calendars["NYQ"], "America/New_York", moment) is new_york
    # Frankfurt (FRA) trades 08:00-22:00 Berlin time.
    assert session_running(calendars["FRA"], "Europe/Berlin", moment) is (
        moment.hour >= 7 and moment.hour < 21
    )


def test_session_running_summer_time_gap_between_regions(calendars):
    # 15 Oct 2026: both regions on summer time (6 h apart): Xetra opens 09:00 CEST.
    assert not session_running(calendars["GER"], "Europe/Berlin", instant(2026, 10, 15, 6, 59))
    assert session_running(calendars["GER"], "Europe/Berlin", instant(2026, 10, 15, 7, 0))
    # 28 Oct 2026 (CET): Xetra opens 08:00 UTC; the same wall clock is 1 h earlier.
    assert not session_running(calendars["GER"], "Europe/Berlin", instant(2026, 10, 28, 7, 59))
    assert session_running(calendars["GER"], "Europe/Berlin", instant(2026, 10, 28, 8, 0))
    # Xetra closes 17:30 local: 16:30 UTC in CET.
    assert session_running(calendars["GER"], "Europe/Berlin", instant(2026, 10, 28, 16, 29))
    assert not session_running(calendars["GER"], "Europe/Berlin", instant(2026, 10, 28, 16, 30))


def test_session_running_is_false_on_holidays_and_weekends(calendars):
    assert not session_running(
        calendars["NMS"], "America/New_York", instant(2026, 11, 26, 16, 0)
    )  # Thanksgiving, 11:00 EST
    assert session_running(
        calendars["GER"], "Europe/Berlin", instant(2026, 11, 26, 10, 0)
    )  # German venues trade that day
    assert not session_running(
        calendars["NMS"], "America/New_York", instant(2026, 10, 31, 16, 0)
    )  # Saturday


def test_us_date_differs_from_berlin_date_late_in_the_evening(calendars):
    # Friday 20:00 EDT is already Saturday 02:00 in Berlin: the US session of
    # Friday is judged by the New York date, never by the Berlin date.
    moment = instant(2026, 10, 31, 0, 0)  # Sat 02:00 CET, Fri 20:00 EDT
    assert not session_running(calendars["NMS"], "America/New_York", moment)
    just_before_close = instant(2026, 10, 30, 19, 59)  # Fri 15:59 EDT, 20:59 CET
    assert session_running(calendars["NMS"], "America/New_York", just_before_close)
