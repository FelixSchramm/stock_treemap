"""Start-page market data with synthetic Yahoo boundaries; all IP sockets stay blocked."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pandas as pd
import pytest
from test_watchlist_refresh import CLOSED, RUNNING, FakeYahoo, close_of

from portfolio_dashboard import market_overview
from portfolio_dashboard.market_overview import (
    ASSETS,
    CLOCK,
    EUROPE_SECTORS,
    GROUPS,
    US_SECTORS,
    WORLD_INDICES,
    MarketQuote,
    breadth,
    load_quotes,
    market_calendars,
    market_clock,
)
from portfolio_dashboard.watchlist_quotes import PERIODS

CALENDARS = market_calendars(date(2026, 10, 28))
ALL = (*WORLD_INDICES, *US_SECTORS, *EUROPE_SECTORS, *ASSETS)
TOKYO_BREAK = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)  # 12:00 JST


def fake(now, symbols=ALL, **overrides):
    yahoo = FakeYahoo(CALENDARS, lambda: now)
    for item in symbols:
        spec = {"exchangeTimezoneName": item.timezone, "frame_exchange": item.calendar or "GER"}
        yahoo.add(item.symbol, item.exchange, item.currency, **(spec | overrides.get(item.symbol, {})))
    return yahoo


def by_symbol(quotes):
    return {quote.item.symbol: quote for quote in quotes}


def test_symbol_lists_are_unique_and_every_calendar_exists():
    assert len(WORLD_INDICES) == 15 and len(US_SECTORS) == 11 and len(EUROPE_SECTORS) == 19
    assert len({item.symbol for item in ALL}) == len(ALL)
    assert {item.calendar for item in ALL} - {None} <= set(CALENDARS)
    assert {key for _, key, _ in CLOCK} <= set(CALENDARS)
    assert set(GROUPS) == {"Weltindizes", "Branchen USA", "Branchen Europa"}
    assert all(item.calendar is None for item in ASSETS)


def test_london_calendar_has_its_hours_and_holidays():
    london = CALENDARS["LSE"]
    assert (london.open_time, london.close_time) == (time(8), time(16, 30))
    assert date(2026, 10, 28) in london.sessions
    assert date(2026, 4, 3) not in london.sessions  # Good Friday
    assert date(2026, 12, 28) not in london.sessions  # Boxing Day, substitute Monday


def test_closed_markets_get_all_periods_from_the_last_closes():
    quotes = by_symbol(load_quotes(WORLD_INDICES, CLOSED, ticker_factory=fake(CLOSED).factory,
                                   calendars=CALENDARS))
    assert all(quote.percent(period) is not None for quote in quotes.values() for period in PERIODS)
    dax = quotes["^GDAXI"]
    today, previous = close_of(date(2026, 10, 28)), close_of(date(2026, 10, 27))
    assert dax.percent("Heute") == (Decimal(str(today)) - Decimal(str(previous))) / Decimal(str(previous)) * 100
    assert dax.market_open is False and dax.current_date == date(2026, 10, 28)
    assert len(dax.closes) == market_overview.SPARKLINE_POINTS


def test_running_session_uses_the_live_quote_only_where_trading():
    yahoo = fake(RUNNING, WORLD_INDICES)
    quotes = by_symbol(load_quotes(WORLD_INDICES, RUNNING, ticker_factory=yahoo.factory,
                                   calendars=CALENDARS))
    assert quotes["^GSPC"].market_open is True and quotes["^GSPC"].price == Decimal("111.11")
    assert quotes["^N225"].market_open is False  # 00:00 JST, the next day
    assert yahoo.quote_calls("^GSPC") and not yahoo.quote_calls("^N225")


def test_tokyo_midday_break_keeps_the_morning_quote():
    quote = load_quotes(WORLD_INDICES[11:12], TOKYO_BREAK, ticker_factory=fake(TOKYO_BREAK).factory,
                        calendars=CALENDARS)[0]
    assert quote.item.name == "Nikkei 225"
    assert quote.market_open is False and quote.price == Decimal("111.11")
    assert quote.current_date == date(2026, 10, 28)


@pytest.mark.parametrize("override", [{"exchangeName": "OTHER"}, {"currency": "EUR"},
                                      {"exchangeTimezoneName": "Europe/Berlin"}])
def test_mismatching_metadata_turns_only_that_symbol_into_na(override):
    yahoo = fake(CLOSED, WORLD_INDICES, **{"^HSI": override})
    quotes = by_symbol(load_quotes(WORLD_INDICES, CLOSED, ticker_factory=yahoo.factory,
                                   calendars=CALENDARS))
    assert all(value is None for value in quotes["^HSI"].percents.values())
    assert quotes["^HSI"].price is None and not quotes["^HSI"].closes
    assert quotes["^GDAXI"].percent("Heute") is not None


def test_missing_calendar_never_invents_sessions():
    calendars = {key: value for key, value in CALENDARS.items() if key != "LSE"}
    yahoo = fake(CLOSED, WORLD_INDICES[4:5])
    (quote,) = load_quotes(WORLD_INDICES[4:5], CLOSED, ticker_factory=yahoo.factory, calendars=calendars)
    assert quote.item.name == "FTSE 100" and quote.price is None and not yahoo.calls


def test_24_hour_market_compares_with_the_previous_daily_bar():
    (quote,) = load_quotes(ASSETS[3:], CLOSED, ticker_factory=fake(CLOSED, ASSETS).factory,
                           calendars=CALENDARS)
    last, previous = Decimal(str(close_of(date(2026, 10, 28)))), Decimal(str(close_of(date(2026, 10, 27))))
    assert quote.item.name == "Bitcoin" and quote.price == last
    assert quote.percent("Heute") == (last - previous) / previous * 100
    assert quote.percent("Woche") is None and len(quote.closes) > 1


class EmptyBarTicker:
    """Daily bars with an empty close: inside the history and/or as the newest bar."""

    def __init__(self, inner, empty_days, stamp):
        self.inner, self.empty_days, self.stamp = inner, empty_days, stamp

    def history(self, **kwargs):
        frame = self.inner.history(**kwargs)
        frame.loc[[day for day in frame.index if day.date() in self.empty_days], "Close"] = float("nan")
        return frame

    def get_history_metadata(self):
        return {**self.inner.get_history_metadata(), "regularMarketPrice": 222.22,
                "regularMarketTime": self.stamp}


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        (pd.Timestamp("2026-10-28 15:45", tz="Asia/Tokyo"), Decimal("222.22")),  # same session
        (pd.Timestamp("2026-10-27 15:45", tz="Asia/Tokyo"), None),  # another day: no fill
    ],
)
def test_empty_newest_bar_is_filled_only_from_the_same_sessions_quote(stamp, expected):
    now = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)  # 18:00 JST, after the close
    yahoo = fake(now, WORLD_INDICES[11:12])
    empty = {date(2026, 10, 28), date(2026, 9, 1)}  # newest bar and one quiet day

    def factory(symbol):
        return EmptyBarTicker(yahoo.factory(symbol), empty, stamp)

    (quote,) = load_quotes(WORLD_INDICES[11:12], now, ticker_factory=factory, calendars=CALENDARS)
    assert quote.price == expected
    if expected is None:  # the session's close stays missing: NA, not the previous close
        assert quote.percent("Heute") is None and quote.current_date == date(2026, 10, 28)
    else:
        assert quote.percent("Jahr") is not None and date(2026, 9, 1) not in dict(quote.closes)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (TOKYO_BREAK, {"Tokio": "Mittagspause", "Hongkong": "offen", "New York": "geschlossen"}),
        (RUNNING, {"Frankfurt": "offen", "London": "offen", "New York": "offen", "Tokio": "geschlossen"}),
        (datetime(2026, 12, 25, 15, 0, tzinfo=UTC), {"Frankfurt": "geschlossen", "London": "geschlossen"}),
    ],
)
def test_market_clock_judges_each_exchange_in_its_own_time(moment, expected):
    clocks = {clock.label: clock for clock in market_clock(moment, CALENDARS)}
    assert {label: clocks[label].status for label in expected} == expected
    assert clocks["Tokio"].local_time.utcoffset() == timedelta(hours=9)


def test_market_clock_outside_the_calendar_span_is_unknown():
    later = datetime(2030, 1, 7, 12, 0, tzinfo=UTC)
    assert {clock.status for clock in market_clock(later, CALENDARS)} == {"unbekannt"}


def test_breadth_counts_gainers_losers_and_ignores_na():
    def quote(symbol, percent):
        item = market_overview.MarketSymbol(symbol, symbol, "", "GER", "EUR", "Europe/Berlin", "GER")
        return MarketQuote(item, {"Heute": percent})

    quotes = (quote("A", Decimal(2)), quote("B", Decimal(-1)), quote("C", None), quote("D", Decimal(0)))
    result = breadth(quotes, "Heute")
    assert (result.up, result.down, result.quoted, result.total) == (1, 1, 3, 4)
    assert result.best.item.symbol == "A" and result.worst.item.symbol == "B"
    assert breadth((), "Heute").best is None
