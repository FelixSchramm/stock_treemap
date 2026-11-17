"""Economic calendar: official list and Yahoo rows, synthetic data only, no network."""

import csv
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from portfolio_dashboard import economic_calendar as calendar
from portfolio_dashboard.economic_calendar import EconomicEvent

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
OFFICIAL_HOSTS = {"www.federalreserve.gov", "www.ecb.europa.eu", "www.bls.gov", "www.bea.gov"}


def frame(rows):
    """Yahoo-like page: index = event name, columns as yfinance renames them."""
    return pd.DataFrame(
        [{"Region": region, "Event Time": pd.Timestamp(stamp), "For": period, "Actual": actual,
          "Expected": float("nan"), "Last": last} for _, region, stamp, period, actual, last in rows],
        index=pd.Index([name for name, *_ in rows], name="Event"),
    )


ROWS = [
    ("FOMC Decision Fed Funds Tgt Rate", "US", "2026-12-09 19:00:00+00:00", "N/A", float("nan"), 4.0),
    ("CPI MM, SA", "US", "2026-11-10 13:30:00+00:00", "Oct", 0.4, 0.6),
    ("Core CPI MM, SA", "US", "2026-11-10 13:30:00+00:00", "Oct", 0.3, 0.3),
    ("Unemployment Rate", "EU", "2026-12-02 10:00:00+00:00", "Oct", float("nan"), 6.5),
    ("ZEW Economic Sentiment", "DE", "2026-11-17 10:00:00+00:00", "Nov", float("nan"), 28.1),
    ("USDA-Cattle On Feed*", "US", "2026-11-20 20:00:00+00:00", "Nov", float("nan"), 100.0),
]


def test_yahoo_rows_are_filtered_to_tracked_series_and_mapped():
    events = calendar.load_yahoo_events(date(2026, 11, 8), date(2026, 12, 13),
                                        fetch=lambda start, end, offset: frame(ROWS) if offset == 0 else None)
    assert [event.key for event in events] == ["fed_rate", "us_cpi", "ez_unemployment", "de_zew"]
    cpi = events[1]
    assert (cpi.actual, cpi.last, cpi.period) == (Decimal("0.4"), Decimal("0.6"), "Oct")
    assert events[0].actual is None and events[0].period is None
    # Same Yahoo name, other region: the EU unemployment rate is not the US one.
    assert events[2].kind.region == "EU"


def test_paging_stops_at_a_short_page():
    offsets = []

    def fetch(start, end, offset):
        offsets.append(offset)
        return frame(ROWS * 20) if offset == 0 else frame(ROWS)

    calendar.load_yahoo_events(date(2026, 11, 8), date(2026, 12, 13), fetch=fetch)
    assert offsets == [0, 100]


def test_provider_failure_returns_nothing():
    def broken(start, end, offset):
        raise RuntimeError("synthetic-provider-error")

    assert calendar.load_yahoo_events(date(2026, 11, 8), date(2026, 12, 13), fetch=broken) == ()


def test_official_time_wins_and_yahoo_values_stay_without_duplicates():
    official = EconomicEvent("fed_rate", datetime(2026, 12, 9, 14, 0, tzinfo=ZoneInfo("America/New_York")))
    yahoo = EconomicEvent("fed_rate", datetime(2026, 12, 9, 18, 0, tzinfo=UTC), last=Decimal(4))
    other = EconomicEvent("de_zew", datetime(2026, 11, 17, 10, 0, tzinfo=UTC), last=Decimal("28.1"))
    merged = calendar.merge_events([official], [yahoo, other])
    assert [event.key for event in merged] == ["de_zew", "fed_rate"]
    assert merged[1].when == official.when and merged[1].last == Decimal(4)


def test_next_rate_decisions_are_shown_beyond_the_window():
    events = [
        EconomicEvent("de_zew", NOW + timedelta(days=2)),
        EconomicEvent("ecb_rate", NOW + timedelta(days=40)),
        EconomicEvent("ecb_rate", NOW + timedelta(days=80)),
        EconomicEvent("fed_rate", NOW + timedelta(days=50)),
        EconomicEvent("us_cpi", NOW - timedelta(hours=1)),
    ]
    result = calendar.upcoming(events, NOW)
    assert [event.key for event in result["USA"]] == ["fed_rate"]
    assert [(event.key, event.when) for event in result["Europa"]] == [
        ("de_zew", NOW + timedelta(days=2)), ("ecb_rate", NOW + timedelta(days=40))]


def test_last_released_needs_an_actual_value():
    events = [
        EconomicEvent("us_cpi", NOW - timedelta(days=5), actual=Decimal("0.4")),
        EconomicEvent("us_jobs", NOW - timedelta(days=2)),
        EconomicEvent("de_cpi", NOW - timedelta(days=9), actual=Decimal("2.8")),
    ]
    result = calendar.last_released(events, NOW)
    assert [event.key for event in result["USA"]] == ["us_cpi"] and result["Europa"] == []


def test_list_end_warning_only_close_to_the_end():
    events = [EconomicEvent("ecb_rate", NOW + timedelta(days=30))]
    assert calendar.list_ends(events, NOW) == (NOW + timedelta(days=30)).date()
    assert calendar.list_ends([EconomicEvent("ecb_rate", NOW + timedelta(days=90))], NOW) is None


def test_official_times_convert_to_berlin_across_daylight_saving():
    events = {(event.key, event.when.date()): event for event in calendar.load_fixed_events()}
    # Fed decision 14:00 New York: 20:00 in Berlin in winter, also 20:00 in July (both on summer time).
    assert events[("fed_rate", date(2026, 12, 9))].when.astimezone(calendar.BERLIN).hour == 20
    assert events[("fed_rate", date(2027, 7, 28))].when.astimezone(calendar.BERLIN).hour == 20
    # 17.03.2027: the USA already switched, Europe not yet, so only 19:00 in Berlin.
    assert events[("fed_rate", date(2027, 3, 17))].when.astimezone(calendar.BERLIN).hour == 19


def test_official_list_is_well_formed():
    with calendar.EVENTS_FILE.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows and set(rows[0]) == {"date", "time_local", "timezone", "key", "tentative", "source_url"}
    seen = set()
    for row in rows:
        assert row["key"] in calendar.EVENT_TYPES and row["tentative"] in {"0", "1"}
        ZoneInfo(row["timezone"])
        assert urlparse(row["source_url"]).hostname in OFFICIAL_HOSTS
        assert (row["key"], row["date"]) not in seen
        seen.add((row["key"], row["date"]))
    assert [row["date"] for row in rows] == sorted(row["date"] for row in rows)


@pytest.mark.parametrize("key", calendar.YAHOO_EVENTS.values())
def test_every_yahoo_series_has_a_known_type(key):
    kind = calendar.EVENT_TYPES[key]
    assert kind.region in calendar.REGIONS and kind.group in calendar.GROUPS
