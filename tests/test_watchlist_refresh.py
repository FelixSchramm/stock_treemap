"""Offline Watchlist refresh: Sheet read, metadata check, periods, fallbacks (#84).

Synthetic symbols, tabs and prices only. The real ``YFinanceProvider`` runs on a
fake ticker, so metadata checks, cache behaviour and period maths are the
production code; pytest blocks IP sockets throughout.
"""

import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest
from requests.exceptions import Timeout

from portfolio_dashboard import watchlist_refresh
from portfolio_dashboard.config import CacheTTLs, SourceConfig, SourceKind, load_config
from portfolio_dashboard.local_source import LOCAL_SHEET_METADATA, venue_calendars
from portfolio_dashboard.market_data import YFinanceProvider
from portfolio_dashboard.models import SnapshotStatus
from portfolio_dashboard.sheet_parser import NumberLocale
from portfolio_dashboard.sheets_adapter import SheetsAdapter, SheetsConfigurationError
from portfolio_dashboard.watchlist_quotes import (
    DATA_UNAVAILABLE_TEXT,
    PERIODS,
    WatchlistEntryStatus,
    WatchlistOrigin,
    WatchlistPeriodValue,
)
from portfolio_dashboard.watchlist_refresh import (
    MAX_WATCHLIST_ROWS,
    SHEET_REQUEST_PARAMS,
    WatchlistRefreshCoordinator,
    WatchlistSheetsReader,
    build_local_watchlist_coordinator,
    build_production_watchlist_coordinator,
    watchlist_listing,
)
from portfolio_dashboard.watchlist_sheet import (
    BASIC_HEADERS,
    OPTIONAL_HEADERS,
    SHEET_VALUE_LABEL,
    WatchlistRowStatus,
    parse_watchlist_rows,
)

FULL_HEADERS = (*BASIC_HEADERS, *OPTIONAL_HEADERS)
CLOSED = datetime(2026, 10, 28, 22, 0, tzinfo=UTC)  # Wed: 18:00 EDT, 23:00 CET
RUNNING = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)  # Wed: 11:00 EDT, 16:00 CET
TIMEZONES = {"America/New_York": ("NMS", "NYQ"), "Europe/Berlin": ("GER", "FRA")}
SECRET = "synthetic-private-detail"


def tz_of(exchange):
    return "America/New_York" if exchange in ("NMS", "NGM", "NCM", "NYQ", "ASE", "PCX") else "Europe/Berlin"


def close_of(day):
    return (10_000 + (day - date(2024, 1, 1)).days) / 100


def pct(current_day, reference_day):
    current, reference = (Decimal(str(close_of(d))) for d in (current_day, reference_day))
    return (current - reference) / reference * 100


def row(
    name="Synthetik AG",
    price="12,50",
    change="1,5",
    net="0,18",
    ticker="SYN",
    kind="Aktie",
    symbol="SYN.DE",
    exchange="GER",
    currency="EUR",
    category="Demo",
):
    return [name, price, change, net, ticker, kind, symbol, exchange, currency, category]


class FakeYahoo:
    """Per-symbol fake ``yfinance.Ticker``: bars from the calendar, metadata, failures."""

    def __init__(self, calendars, clock):
        self.calendars = calendars
        self.clock = clock
        self.specs = {}
        self.calls = []
        self.fail = False
        self.rejected = set()  # symbols the provider refuses (quote and history)

    def add(self, key, exchange, ccy, /, **overrides):
        self.specs[key] = {
            "symbol": key,
            "exchangeName": exchange,
            "currency": ccy,
            "exchangeTimezoneName": tz_of(exchange),
            "frame_exchange": exchange,
            "quote": 111.11,
            **overrides,
        }

    def factory(self, symbol):
        return _FakeTicker(self, symbol)

    def quote_calls(self, symbol):
        return [call for call in self.calls if call == (symbol, "quote")]

    def history_calls(self, symbol):
        return [call for call in self.calls if call == (symbol, "history")]

    def history_calls_all(self):
        return [call for call in self.calls if call[1] == "history"]


class _FakeTicker:
    def __init__(self, yahoo, symbol):
        self.yahoo, self.symbol = yahoo, symbol

    def history(self, **kwargs):
        spec = self.yahoo.specs[self.symbol]
        self.yahoo.calls.append((self.symbol, "quote" if "period" in kwargs else "history"))
        if self.yahoo.fail or self.symbol in self.yahoo.rejected:
            raise RuntimeError(SECRET)
        tz = spec.get("frame_timezone", spec["exchangeTimezoneName"])
        now = self.yahoo.clock()
        local_today = now.astimezone(pd.Timestamp(0, tz=tz).tz).date()
        sessions = self.yahoo.calendars[spec["frame_exchange"]].sessions
        days = sorted(d for d in sessions if d <= local_today)
        if "start" in kwargs:
            start, end = (date.fromisoformat(kwargs[k]) for k in ("start", "end"))
            days = [d for d in days if start <= d < end]
        closes = [close_of(d) for d in days]
        # Optional intraday state: the bar of a running session is not final yet.
        calendar = self.yahoo.calendars[spec["frame_exchange"]]
        local = now.astimezone(pd.Timestamp(0, tz=tz).tz)
        if "intraday" in spec and days and days[-1] == local.date() and local.time() < calendar.close_time:
            closes[-1] = spec["intraday"]
        return pd.DataFrame(
            {"Close": closes, "Stock Splits": [0.0] * len(days)},
            index=pd.DatetimeIndex([pd.Timestamp(d) for d in days], tz=tz),
        )

    def get_history_metadata(self):
        spec = self.yahoo.specs[self.symbol]
        return {
            "symbol": spec["symbol"],
            "exchangeName": spec["exchangeName"],
            "currency": spec["currency"],
            "exchangeTimezoneName": spec["exchangeTimezoneName"],
            "regularMarketPrice": spec["quote"],
            "regularMarketTime": pd.Timestamp(self.yahoo.clock() - timedelta(seconds=60)),
        }


class Env:
    """Mutable clock, fake Yahoo and a rows source around one coordinator."""

    def __init__(self, rows, *, now=CLOSED, headers=FULL_HEADERS, symbols=(), calendars=None):
        self.now = now
        self.rows = rows
        self.headers = headers
        self.reads = 0
        self.read_error = None
        calendars = calendars if calendars is not None else venue_calendars(date(2026, 10, 28))
        self.yahoo = FakeYahoo(venue_calendars(date(2026, 10, 28)), lambda: self.now)
        for symbol, exchange, currency in symbols:
            self.yahoo.add(symbol, exchange, currency)
        self.coordinator = WatchlistRefreshCoordinator(
            self.read,
            calendars=calendars,
            ttls=CacheTTLs(quotes_seconds=60, holdings_seconds=300, history_seconds=3600),
            provider_factory=lambda listings: YFinanceProvider(
                listings, ticker_factory=self.yahoo.factory, clock=lambda: self.now
            ),
            clock=lambda: self.now,
        )

    def read(self, attempted_at):
        self.reads += 1
        if self.read_error is not None:
            raise self.read_error
        return parse_watchlist_rows(
            self.headers,
            self.rows,
            imported_at=attempted_at,
            number_locale=NumberLocale.GERMAN,
        ).snapshot

    def load(self, **kwargs):
        return self.coordinator.load(**kwargs)


def entries_by_name(bundle):
    return {entry.item.name: entry for entry in bundle.entries}


def mapped_rows():
    return [
        row("DE-Wert", symbol="SYN1.DE", exchange="GER", currency="EUR", ticker="D1"),
        row("Frankfurt-Wert", symbol="SYN4.F", exchange="FRA", currency="EUR", ticker="D2"),
        row("US-Wert", symbol="SYN2", exchange="NMS", currency="USD", ticker="U1"),
        row("NYSE-Wert", symbol="SYN3", exchange="NYQ", currency="USD", ticker="U2"),
    ]


MAPPED_SYMBOLS = (
    ("SYN1.DE", "GER", "EUR"),
    ("SYN4.F", "FRA", "EUR"),
    ("SYN2", "NMS", "USD"),
    ("SYN3", "NYQ", "USD"),
)


# --- provider data: all four periods in trading currency ------------------------


def test_mapped_rows_get_all_four_periods_in_trading_currency_when_closed():
    env = Env(mapped_rows(), symbols=MAPPED_SYMBOLS)
    bundle = env.load()
    assert bundle.available and not bundle.using_cached_stand
    last, today = date(2026, 10, 28), date(2026, 10, 27)
    expected = {
        "Heute": pct(last, today),
        "Woche": pct(last, date(2026, 10, 21)),
        "Monat": pct(last, date(2026, 9, 28)),
        "Jahr": pct(last, date(2025, 10, 28)),
    }
    entries = entries_by_name(bundle)
    assert len(entries) == 4
    for name, currency in (("DE-Wert", "EUR"), ("Frankfurt-Wert", "EUR"),
                           ("US-Wert", "USD"), ("NYSE-Wert", "USD")):
        entry = entries[name]
        assert entry.status is WatchlistEntryStatus.LIVE
        assert [p.period for p in entry.periods] == list(PERIODS)
        for label in PERIODS:
            value = entry.period(label)
            assert value.percent == expected[label]
            assert value.origin is WatchlistOrigin.PROVIDER and value.reason is None
        assert entry.price == Decimal(str(close_of(last)))
        assert entry.price_unit.currency == currency  # trading currency, no FX
        assert entry.price_origin is WatchlistOrigin.PROVIDER
        assert entry.current_date == last and entry.market_open is False
        assert entry.age_text is None and entry.status_reason is None
        assert entry.period("Heute").reference_date == today
    # A closed session needs no quote request at all.
    assert not [call for call in env.yahoo.calls if call[1] == "quote"]


def test_running_us_and_german_sessions_use_the_checked_live_quote():
    env = Env(mapped_rows(), now=RUNNING, symbols=MAPPED_SYMBOLS)
    bundle = env.load()
    entries = entries_by_name(bundle)
    for name in ("DE-Wert", "US-Wert"):
        entry = entries[name]
        assert entry.market_open is True and entry.status is WatchlistEntryStatus.LIVE
        assert entry.price == Decimal("111.11")
        current, reference = Decimal("111.11"), Decimal(str(close_of(date(2026, 10, 27))))
        assert entry.period("Heute").percent == (current - reference) / reference * 100
        week_reference = Decimal(str(close_of(date(2026, 10, 21))))
        assert entry.period("Woche").percent == (current - week_reference) / week_reference * 100
        assert "Börse geöffnet" in entry.status_text
    assert len(env.yahoo.quote_calls("SYN2")) == 1


def test_us_session_not_yet_open_while_germany_trades_across_dst_change():
    # 28 Oct 13:15 UTC: 09:15 EDT (closed) but 14:15 CET (open).
    env = Env(mapped_rows(), now=datetime(2026, 10, 28, 13, 15, tzinfo=UTC), symbols=MAPPED_SYMBOLS)
    entries = entries_by_name(env.load())
    assert entries["US-Wert"].market_open is False
    assert entries["US-Wert"].current_date == date(2026, 10, 27)
    assert entries["DE-Wert"].market_open is True
    # 2 Nov 14:15 UTC (after both changed): 09:15 EST closed, 15:15 CET open.
    env = Env(mapped_rows(), now=datetime(2026, 11, 2, 14, 15, tzinfo=UTC), symbols=MAPPED_SYMBOLS)
    entries = entries_by_name(env.load())
    assert entries["US-Wert"].market_open is False
    assert entries["US-Wert"].current_date == date(2026, 10, 30)  # Friday
    assert entries["DE-Wert"].market_open is True
    # 28 Oct 13:45 UTC and 2 Nov 14:45 UTC: both venues running.
    for moment in (datetime(2026, 10, 28, 13, 45, tzinfo=UTC), datetime(2026, 11, 2, 14, 45, tzinfo=UTC)):
        env = Env(mapped_rows(), now=moment, symbols=MAPPED_SYMBOLS)
        entries = entries_by_name(env.load())
        assert entries["US-Wert"].market_open is True and entries["DE-Wert"].market_open is True


def test_us_holiday_uses_last_close_while_frankfurt_still_trades():
    # Thanksgiving 2026, 20:00 UTC: US closed all day; FRA open until 22:00 CET.
    now = datetime(2026, 11, 26, 20, 0, tzinfo=UTC)
    env = Env(mapped_rows(), now=now, symbols=MAPPED_SYMBOLS)
    entries = entries_by_name(env.load())
    us = entries["US-Wert"]
    assert us.market_open is False and us.current_date == date(2026, 11, 25)
    assert us.period("Heute").percent == pct(date(2026, 11, 25), date(2026, 11, 24))
    assert us.price == Decimal(str(close_of(date(2026, 11, 25))))
    frankfurt = entries["Frankfurt-Wert"]
    assert frankfurt.market_open is True and frankfurt.current_date == date(2026, 11, 26)
    assert frankfurt.price == Decimal("111.11")
    xetra = entries["DE-Wert"]  # Xetra closed 17:30 CET = 16:30 UTC
    assert xetra.market_open is False and xetra.current_date == date(2026, 11, 26)


def test_listing_is_a_technical_key_without_identity_data():
    snapshot = parse_watchlist_rows(
        FULL_HEADERS, [row(kind="ETF", symbol="SYN2", exchange="NMS", currency="USD")],
        imported_at=CLOSED, number_locale=NumberLocale.GERMAN,
    ).snapshot
    listing = watchlist_listing(snapshot.items[0])
    instrument = listing.instrument
    assert instrument.instrument_id == "watchlist:NMS:SYN2"
    assert instrument.trading_venue == "NMS"
    assert (instrument.quote_unit.currency, instrument.quote_unit.scale_to_currency) == ("USD", 1)
    assert instrument.provider_mapping.provider == "yfinance"
    assert listing.isin is None and listing.wkn is None
    sheet_only = parse_watchlist_rows(
        BASIC_HEADERS, [["Nur Blatt", "1,00", "1", "0"]],
        imported_at=CLOSED, number_locale=NumberLocale.GERMAN,
    ).snapshot.items[0]
    with pytest.raises(ValueError):
        watchlist_listing(sheet_only)


# --- Sheet fallback --------------------------------------------------------------


def test_four_column_rows_show_the_labelled_sheet_value_and_no_other_periods():
    rows = [["Fonds A", "10,50", "1,25", "0,13"], ["Krypto B", 1500, "-2,30", "-35"]]
    env = Env(rows, headers=BASIC_HEADERS)
    bundle = env.load()
    assert not env.yahoo.calls  # nothing is requested without a mapping
    for entry in bundle.entries:
        assert entry.item.status is WatchlistRowStatus.SHEET_ONLY
        assert entry.status is WatchlistEntryStatus.SHEET_VALUE
        assert entry.status_text == SHEET_VALUE_LABEL == "Sheet-Wert, Kursstand unbekannt"
        heute = entry.period("Heute")
        assert heute.origin is WatchlistOrigin.SHEET
        assert [entry.period(label).percent for label in PERIODS[1:]] == [None] * 3
        assert {entry.period(label).reason for label in PERIODS[1:]} == {"unverified_mapping"}
        assert entry.price_origin is WatchlistOrigin.SHEET
        assert entry.price_unit.currency == "EUR"
    first, second = bundle.entries
    assert first.period("Heute").percent == Decimal("1.25") and first.price == Decimal("10.50")
    assert second.period("Heute").percent == Decimal("-2.30") and second.price == Decimal("1500")


def test_sheet_percentage_value_is_not_rescaled():
    # UNFORMATTED_VALUE delivers 1.5 for "1,5": percentage points stay as read.
    env = Env([["Fonds", 12.5, Decimal("1.5"), 0]], headers=BASIC_HEADERS)
    assert env.load().entries[0].period("Heute").percent == Decimal("1.5")


def test_row_without_any_sheet_value_is_unavailable():
    env = Env([["Nur Name"]], headers=BASIC_HEADERS)
    entry = env.load().entries[0]
    assert entry.status is WatchlistEntryStatus.UNAVAILABLE
    assert entry.status_text == DATA_UNAVAILABLE_TEXT == "Daten nicht verfügbar"
    assert entry.price is None and entry.price_origin is WatchlistOrigin.NONE
    assert entry.period("Heute").reason == "sheet_value_missing"
    assert all(not entry.period(label).available for label in PERIODS)


def test_incomplete_or_contradictory_mapping_falls_back_with_the_parser_warning():
    rows = [
        row("Ohne Plattform", exchange=None),  # incomplete
        row("Falsche Endung", symbol="SYN.F", exchange="GER"),  # conflict
        row("Derivat", kind="Derivat"),
    ]
    env = Env(rows)
    bundle = env.load()
    codes = [
        {w.code for w in entry.warnings} for entry in bundle.entries
    ]
    assert codes[0] == {"incomplete_mapping"}
    assert codes[1] == {"symbol_exchange_conflict"}
    assert codes[2] == {"unsupported_derivative"}
    assert not env.yahoo.calls
    for entry in bundle.entries:
        assert entry.item.status is WatchlistRowStatus.FALLBACK
        assert entry.status is WatchlistEntryStatus.SHEET_VALUE
        assert entry.period("Heute").origin is WatchlistOrigin.SHEET
        assert entry.period("Jahr").reason == "unverified_mapping"


def test_duplicate_yahoo_symbol_keeps_provider_values_only_on_the_first_row():
    rows = [
        row("Erste", symbol="SYN1.DE", change="9,0"),
        row("Zweite", symbol="SYN1.DE", change="3,0"),
    ]
    env = Env(rows, symbols=[("SYN1.DE", "GER", "EUR")])
    first, second = env.load().entries
    assert first.status is WatchlistEntryStatus.LIVE
    assert first.period("Heute").percent == pct(date(2026, 10, 28), date(2026, 10, 27))
    assert second.item.status is WatchlistRowStatus.FALLBACK
    assert {w.code for w in second.warnings} == {"duplicate_yahoo_symbol"}
    assert second.status is WatchlistEntryStatus.SHEET_VALUE
    assert second.period("Heute").percent == Decimal("3.0")
    assert second.period("Woche").reason == "unverified_mapping"
    assert len(env.yahoo.history_calls("SYN1.DE")) == 1  # fetched once


def test_hand_built_duplicate_mapped_rows_are_not_fetched_twice():
    env = Env([row("Erste", symbol="SYN1.DE"), row("Dublette", symbol="SYN5.DE")],
              symbols=[("SYN1.DE", "GER", "EUR")])
    snapshot = env.read(CLOSED)
    second = replace(snapshot.items[1], mapping=snapshot.items[0].mapping)
    snapshot = replace(snapshot, items=(snapshot.items[0], second))
    env.read = lambda attempted_at: snapshot
    env.coordinator._read = env.read
    first, duplicate = env.load().entries
    assert first.status is WatchlistEntryStatus.LIVE
    assert duplicate.status is WatchlistEntryStatus.SHEET_VALUE
    assert "duplicate_yahoo_symbol" in {w.code for w in duplicate.warnings}


def test_exchange_without_calendar_cannot_compute_periods():
    calendars = {k: v for k, v in venue_calendars(date(2026, 10, 28)).items() if k != "NMS"}
    env = Env(mapped_rows(), symbols=MAPPED_SYMBOLS, calendars=calendars)
    entries = entries_by_name(env.load())
    assert entries["US-Wert"].status is WatchlistEntryStatus.SHEET_VALUE
    assert entries["US-Wert"].status_reason == "calendar_incomplete"
    assert entries["DE-Wert"].status is WatchlistEntryStatus.LIVE
    assert not env.yahoo.history_calls("SYN2")


# --- Yahoo metadata must agree with the Sheet ------------------------------------


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"symbol": "OTHER.DE"}, "instrument_metadata_mismatch"),
        ({"exchangeName": "NMS"}, "instrument_metadata_mismatch"),
        ({"currency": "USD"}, "unknown_or_conflicting_unit"),
        ({"currency": "GBp"}, "unknown_or_conflicting_unit"),
        (
            {"exchangeTimezoneName": "America/New_York", "frame_timezone": "America/New_York"},
            "exchange_timezone_mismatch",
        ),
    ],
)
def test_metadata_conflict_shows_no_provider_value_and_names_the_reason(overrides, code):
    env = Env([row("Konflikt", price="12,50", change="1,5")])
    env.yahoo.add("SYN.DE", "GER", "EUR", **overrides)
    entry = env.load().entries[0]
    assert entry.status is WatchlistEntryStatus.METADATA_MISMATCH
    assert entry.status_reason == code and code in entry.status_text
    assert "kein Anbieterkurs übernommen" in entry.status_text
    # Only the labelled Sheet value remains, never a price of the other instrument.
    assert entry.price == Decimal("12.50") and entry.price_unit.currency == "EUR"
    assert entry.price_origin is WatchlistOrigin.SHEET
    assert entry.period("Heute").origin is WatchlistOrigin.SHEET
    assert entry.period("Heute").percent == Decimal("1.5")
    for label in PERIODS[1:]:
        value = entry.period(label)
        assert value.percent is None and value.reason == code
    assert entry.retrieved_at is None and entry.current_date is None


def test_metadata_conflict_without_sheet_value_is_n_v_with_reason():
    env = Env([row("Konflikt", price=None, change=None, net=None)])
    env.yahoo.add("SYN.DE", "GER", "EUR", symbol="OTHER.DE")
    entry = env.load().entries[0]
    assert entry.status is WatchlistEntryStatus.METADATA_MISMATCH
    assert entry.price is None
    assert all(not entry.period(label).available for label in PERIODS)
    assert {entry.period(label).reason for label in PERIODS} == {"instrument_metadata_mismatch"}


def test_conflict_on_one_row_does_not_affect_other_rows():
    env = Env(mapped_rows(), symbols=MAPPED_SYMBOLS)
    env.yahoo.add("SYN2", "NMS", "USD", currency="EUR")
    entries = entries_by_name(env.load())
    assert entries["US-Wert"].status is WatchlistEntryStatus.METADATA_MISMATCH
    for name in ("DE-Wert", "Frankfurt-Wert", "NYSE-Wert"):
        assert entries[name].status is WatchlistEntryStatus.LIVE


def test_running_session_quote_with_foreign_metadata_is_rejected():
    env = Env([row("US", symbol="SYN2", exchange="NMS", currency="USD")], now=RUNNING)
    env.yahoo.add("SYN2", "NMS", "USD", currency="EUR")
    entry = env.load().entries[0]
    assert entry.status is WatchlistEntryStatus.METADATA_MISMATCH
    assert entry.price == Decimal("12.50") and entry.price_unit.currency == "EUR"


# --- failures: last valid state with age, or "Daten nicht verfügbar" ---------------


def test_failed_refresh_keeps_the_last_valid_provider_state_with_its_age():
    env = Env(mapped_rows(), symbols=MAPPED_SYMBOLS)
    first = env.load()
    started = env.now
    env.now = started + timedelta(minutes=5)
    env.yahoo.fail = True
    second = env.load(refresh=True)
    assert second.available and not second.using_cached_stand
    for before, after in zip(first.entries, second.entries):
        assert after.status is WatchlistEntryStatus.CACHED
        assert [p.percent for p in after.periods] == [p.percent for p in before.periods]
        assert {p.origin for p in after.periods} == {WatchlistOrigin.CACHED_PROVIDER}
        assert after.price_origin is WatchlistOrigin.CACHED_PROVIDER
        assert after.retrieved_at == started
        assert after.age_text == "vor 5 Min."
        assert "letzter gültiger Stand" in after.status_text and "vor 5 Min." in after.status_text
    assert SECRET not in repr(second)
    # A later success returns to live data.
    env.yahoo.fail = False
    env.now += timedelta(hours=2)
    assert {e.status for e in env.load(refresh=True).entries} == {WatchlistEntryStatus.LIVE}


def test_failure_without_earlier_state_falls_back_to_sheet_value_or_unavailable():
    rows = [
        row("Mit Sheet-Wert", symbol="SYN1.DE"),
        row("Ohne Sheet-Wert", symbol="SYN4.F", exchange="FRA", price=None, change=None, net=None),
    ]
    env = Env(rows, symbols=[("SYN1.DE", "GER", "EUR"), ("SYN4.F", "FRA", "EUR")])
    env.yahoo.fail = True
    with_sheet, without = env.load().entries
    assert with_sheet.status is WatchlistEntryStatus.SHEET_VALUE
    assert with_sheet.status_reason == "provider_failure"
    assert with_sheet.status_text == f"{SHEET_VALUE_LABEL}; Anbieterdaten nicht verfügbar (provider_failure)"
    assert with_sheet.period("Heute").percent == Decimal("1.5")
    assert with_sheet.period("Heute").reason == "provider_failure"
    assert without.status is WatchlistEntryStatus.UNAVAILABLE
    assert without.status_text == "Daten nicht verfügbar (provider_failure)"
    assert without.price is None
    assert SECRET not in repr(env.coordinator.last_bundle)


# --- rejected symbols must not lock out the valid rows behind them ---------------


def bad_good_env(layout, *, now=CLOSED):
    """Rows from a layout string: ``b`` = symbol the provider rejects, ``g`` = valid."""
    rows, symbols, names = [], [], []
    for index, kind in enumerate(layout):
        symbol = f"{kind.upper()}{index}.DE"
        names.append(f"{'Defekt' if kind == 'b' else 'Gueltig'} {index}")
        rows.append(row(names[-1], symbol=symbol, ticker=f"T{index}"))
        symbols.append((symbol, "GER", "EUR"))
    env = Env(rows, now=now, symbols=symbols)
    env.yahoo.rejected = {sym for (sym, _, _), kind in zip(symbols, layout) if kind == "b"}
    return env, names


def assert_valid_live_defective_failed(bundle, names, layout):
    assert [e.item.name for e in bundle.entries] == names  # sheet order
    for entry, kind in zip(bundle.entries, layout):
        if kind == "g":
            assert entry.status is WatchlistEntryStatus.LIVE, entry.item.name
            assert entry.status_reason is None
        else:
            assert entry.status is WatchlistEntryStatus.SHEET_VALUE
            assert entry.status_reason == "provider_failure"


@pytest.mark.parametrize(
    "layout, running",
    [
        ("bbb" + "g" * 12, False),  # three rejected symbols at the start
        ("g" * 5 + "bbb" + "g" * 8, False),  # defective rows in the middle
        ("g" * 4 + "bbb", False),  # defective rows at the end
        ("bbb" + "g" * 12, True),  # running session: quote path as well
        ("g" * 5 + "bbb" + "g" * 8, True),
    ],
)
def test_rejected_symbols_do_not_block_the_valid_rows_behind_them(layout, running):
    env, names = bad_good_env(layout, now=RUNNING if running else CLOSED)
    first = env.load()
    # The cold start may show provider_backoff once; the defective rows already failed.
    assert [e.item.name for e in first.entries] == names
    env.now += timedelta(seconds=20)  # past the first backoff (15 s)
    second = env.load()
    assert_valid_live_defective_failed(second, names, layout)
    env.now += timedelta(seconds=30)
    assert_valid_live_defective_failed(env.load(), names, layout)
    assert SECRET not in repr(second)


def test_valid_rows_are_fetched_before_known_defective_ones_but_listed_in_sheet_order():
    layout = "bbb" + "g" * 10
    env, names = bad_good_env(layout)
    env.load()
    env.now += timedelta(seconds=20)
    env.yahoo.calls.clear()
    bundle = env.load()
    fetched = [symbol for symbol, _ in env.yahoo.history_calls_all()]
    assert fetched[:10] == [f"G{i}.DE" for i in range(3, 13)]
    assert sorted(fetched[10:]) == ["B0.DE", "B1.DE", "B2.DE"]
    assert [e.item.name for e in bundle.entries] == names


def test_failure_counts_survive_loads_and_history_ttl_hits():
    env, names = bad_good_env("g" * 8)
    assert {e.status for e in env.load().entries} == {WatchlistEntryStatus.LIVE}
    # Three rejected rows are inserted in front; the valid histories stay in the TTL cache.
    bad = [row(f"Neu {i}", symbol=f"B9{i}.DE", ticker=f"N{i}") for i in range(3)]
    for i in range(3):
        env.yahoo.add(f"B9{i}.DE", "GER", "EUR")
        env.yahoo.rejected.add(f"B9{i}.DE")
    env.rows = bad + env.rows
    env.now += timedelta(seconds=310)  # holdings TTL over, history TTL (3600 s) not
    mid = env.load()
    assert [e.status for e in mid.entries[3:]] == [WatchlistEntryStatus.LIVE] * 8
    env.now += timedelta(seconds=3600)  # all histories due again, backoff long over
    late = env.load()
    assert [e.status for e in late.entries[3:]] == [WatchlistEntryStatus.LIVE] * 8
    assert all(e.status_reason == "provider_failure" for e in late.entries[:3])


def test_total_outage_still_respects_the_provider_backoff():
    env, _ = bad_good_env("g" * 20)
    env.yahoo.fail = True
    env.load()
    assert len(env.yahoo.calls) == 3  # three failures open the backoff
    env.now += timedelta(seconds=5)
    env.load()
    env.load(refresh=True)
    assert len(env.yahoo.calls) == 3  # no fetch while the backoff runs
    env.now += timedelta(seconds=11)
    bundle = env.load()
    assert len(env.yahoo.calls) == 4  # exactly one recovery probe
    assert {e.status for e in bundle.entries} == {WatchlistEntryStatus.SHEET_VALUE}
    assert {e.status_reason for e in bundle.entries} <= {"provider_failure", "provider_backoff"}


# Warm state: every row loaded fine once, then symbols get rejected after the TTLs.
# Rows are tokens: g/b = valid/rejected, D/U = German/US venue (``gD``, ``bU``).
VENUE = {"D": ("GER", "EUR", ".DE"), "U": ("NMS", "USD", "")}
MIXED_OPEN_DE = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)  # Xetra 10:00 open, NYSE 05:00 closed


def warm_env(layout, *, now):
    tokens = [layout[i : i + 2] for i in range(0, len(layout), 2)]
    rows, symbols = [], []
    for index, token in enumerate(tokens):
        exchange, currency, suffix = VENUE[token[1]]
        symbol = f"{token[0].upper()}{index}{suffix}"
        rows.append(row(f"Zeile {index}", symbol=symbol, exchange=exchange,
                        currency=currency, ticker=f"T{index}"))
        symbols.append((symbol, exchange, currency))
    env = Env(rows, now=now, symbols=symbols)
    env.kinds = [token[0] for token in tokens]
    env.symbols = [symbol for symbol, _, _ in symbols]
    return env


def reject(env):
    env.yahoo.rejected = {s for s, kind in zip(env.symbols, env.kinds) if kind == "b"}


def heal_then_reject_after_ttls(env):
    """All rows valid and LIVE, then the TTLs run out and the ``b`` symbols are rejected."""
    assert {e.status for e in env.load().entries} == {WatchlistEntryStatus.LIVE}
    env.now += timedelta(seconds=3700)  # past the history TTL (3600 s)
    reject(env)


def seconds_until_valid_rows_are_live(
    env, *, step=60, limit=900, rejected_status=WatchlistEntryStatus.CACHED
):
    """Load every ``step`` seconds; returns the seconds until all valid rows are LIVE.

    The rejected rows must show ``rejected_status``: their earlier valid state with its
    age, or the Sheet value once that state belongs to an earlier day.
    """
    start = env.now
    while (env.now - start).total_seconds() <= limit:
        bundle = env.load()
        assert [e.item.name for e in bundle.entries] == [f"Zeile {i}" for i in range(len(env.kinds))]
        valid = [e for e, kind in zip(bundle.entries, env.kinds) if kind == "g"]
        if all(e.status is WatchlistEntryStatus.LIVE for e in valid):
            for entry, kind in zip(bundle.entries, env.kinds):
                if kind == "b":  # earlier valid state stays visible with its age
                    assert entry.status is rejected_status
                    assert rejected_status is not WatchlistEntryStatus.CACHED or entry.age_text
            assert SECRET not in repr(bundle)
            return int((env.now - start).total_seconds())
        env.now += timedelta(seconds=step)
    pytest.fail("valid rows were not refreshed within the limit")


@pytest.mark.parametrize(
    "layout, now, limit",
    [
        ("bD" * 3 + "gD" * 7, CLOSED, 120),  # 3 rejected at the start, market closed
        ("bD" * 6 + "gD" * 4, CLOSED, 420),  # 6 rejected: backoff windows double
        ("gD" * 3 + "bD" * 3 + "gD" * 4, CLOSED, 120),  # defective rows in the middle
        ("gD" * 7 + "bD" * 3, CLOSED, 120),  # defective rows at the end
        ("bD" * 3 + "gD" * 7, RUNNING, 120),  # session running: quote path as well
        ("bD" * 6 + "gD" * 4, RUNNING, 420),
        ("gD" * 3 + "bD" * 3 + "gD" * 4, RUNNING, 120),
        ("bD" * 3 + "gD" * 57, CLOSED, 120),  # 60 rows
        ("bD" * 5 + "gD" * 55, CLOSED, 420),
        ("gD" * 20 + "bD" * 3 + "gD" * 37, CLOSED, 120),
        ("bD" * 3 + "gD" * 57, RUNNING, 120),
        # Mixed markets: rejected rows trade on the open German venue (quote path),
        # the valid rows only on the closed US venue (history path only).
        ("bD" * 3 + "gU" * 8, MIXED_OPEN_DE, 120),
        ("bD" * 6 + "gU" * 8, MIXED_OPEN_DE, 420),
        ("gU" * 4 + "bD" * 3 + "gU" * 4, MIXED_OPEN_DE, 120),
    ],
    ids=[
        "closed-3-start", "closed-6-start", "closed-3-middle", "closed-3-end",
        "running-3-start", "running-6-start", "running-3-middle",
        "rows60-closed-3-start", "rows60-closed-5-start", "rows60-closed-3-middle",
        "rows60-running-3-start",
        "mixed-3-start", "mixed-6-start", "mixed-3-middle",
    ],
)
def test_warm_valid_rows_recover_within_minutes_after_rejected_symbols(layout, now, limit):
    env = warm_env(layout, now=now)
    heal_then_reject_after_ttls(env)
    assert seconds_until_valid_rows_are_live(env) <= limit


def test_previously_healed_rows_that_become_defective_again_do_not_block_valid_rows():
    env = warm_env("bD" * 3 + "gD" * 10, now=CLOSED)
    env.yahoo.rejected = {s for s, kind in zip(env.symbols, env.kinds) if kind == "b"}
    for _ in range(6):  # the rejected rows fail repeatedly while the valid ones load
        env.load()
        env.now += timedelta(seconds=60)
    env.yahoo.rejected = set()  # symbols are corrected at the provider
    for _ in range(10):
        env.now += timedelta(seconds=60)
        bundle = env.load()
    assert {e.status for e in bundle.entries} == {WatchlistEntryStatus.LIVE}
    env.now += timedelta(seconds=3700)
    reject(env)  # defective again
    assert seconds_until_valid_rows_are_live(env) <= 120


def test_rank_ignores_results_served_from_the_cache_only_because_of_a_backoff():
    env = warm_env("gD" * 3 + "bD" * 3 + "gD" * 4, now=CLOSED)
    heal_then_reject_after_ttls(env)
    env.load()  # first probe: three rejected rows open the provider-wide backoff
    attempted = {symbol for symbol, _ in env.yahoo.calls if symbol.startswith("B")}
    assert attempted == {"B3.DE", "B4.DE", "B5.DE"}
    env.yahoo.calls.clear()
    env.now += timedelta(seconds=60)
    env.load()
    fetched = [symbol for symbol, _ in env.yahoo.history_calls_all()]
    # G6-G9 were only blocked by the backoff (served from the cache, nothing attempted):
    # they still rank above the rows that really failed, so one of them is the probe.
    assert fetched == ["G6.DE", "G7.DE", "G8.DE", "G9.DE", "B3.DE", "B4.DE", "B5.DE"]


def test_coordinator_keeps_no_failure_state_of_its_own_for_vanished_rows():
    env = warm_env("bD" * 4 + "gD" * 4, now=CLOSED)
    reject(env)
    for _ in range(4):
        env.load()
        env.now += timedelta(seconds=60)
    env.rows = env.rows[4:]  # the rejected rows are removed from the sheet
    env.now += timedelta(seconds=400)
    bundle = env.load()
    assert len(bundle.entries) == 4
    assert {e.status for e in bundle.entries} == {WatchlistEntryStatus.LIVE}
    assert not hasattr(env.coordinator, "_failures")


# Stale quote counters: a quote failure counter only resets on a successful quote in an
# open session, so after the close it stays frozen. With production TTLs (history 6 h) it
# must not hold healed valid rows behind a freshly rejected block until the history TTL.
PRODUCTION_TTLS = CacheTTLs(quotes_seconds=60, holdings_seconds=300, history_seconds=21600)
XETRA_CLOSED = datetime(2026, 10, 28, 16, 40, tzinfo=UTC)  # Xetra closed at 16:30 UTC


def frozen_quote_counters_then_reject(env, flaky):
    """Valid rows go LIVE; ``flaky`` ones are refused for the rest of the open session
    (quote counters climb) and heal after the close (counters stay frozen); then every
    TTL runs out and the ``b`` symbols are rejected."""
    env.coordinator._cache.ttls = PRODUCTION_TTLS
    assert {e.status for e in env.load().entries} == {WatchlistEntryStatus.LIVE}
    env.yahoo.rejected = {env.symbols[i] for i in flaky}
    while env.now < XETRA_CLOSED:
        env.now += timedelta(seconds=60)
        env.load()
    env.yahoo.rejected = set()  # healed after the close; no quote task exists any more
    for _ in range(20):
        env.now += timedelta(seconds=60)
        bundle = env.load()
    assert {e.status for e in bundle.entries} == {WatchlistEntryStatus.LIVE}
    frozen = {
        key[1]: entry.failures
        for key, entry in env.coordinator._cache._entries.items()
        if key[0] == "quote" and entry.failures
    }
    assert len(frozen) == len(flaky) and min(frozen.values()) >= 10  # precondition holds
    env.now += timedelta(seconds=PRODUCTION_TTLS.history_seconds + 100)  # all TTLs over
    reject(env)


@pytest.mark.parametrize(
    "layout, flaky, limit",
    [
        ("gD" * 9 + "bD" * 3, (7, 8), 120),  # rejected block at the end of the sheet
        ("bD" * 3 + "gD" * 9, (10, 11), 120),  # rejected block at the start
        ("gD" * 9 + "bD" * 3, range(9), 120),  # every valid row has a stale quote counter
        ("bD" * 3 + "gD" * 9, range(3, 12), 120),
        ("bD" * 5 + "gD" * 7, range(5, 12), 420),  # larger block: backoff windows double
    ],
    ids=["block-at-end", "block-at-start", "all-stale-block-at-end",
         "all-stale-block-at-start", "all-stale-5-at-start"],
)
def test_stale_quote_counters_do_not_hold_healed_rows_behind_a_rejected_block(
    layout, flaky, limit
):
    env = warm_env(layout, now=RUNNING)
    frozen_quote_counters_then_reject(env, flaky)
    assert seconds_until_valid_rows_are_live(env, limit=limit) <= limit


def test_failure_ranks_are_kept_per_fetch_kind_and_instrument():
    env = warm_env("gD" * 2 + "bD", now=RUNNING)
    env.load()
    assert env.coordinator._failure_ranks() == {}
    env.now += timedelta(seconds=3700)
    env.yahoo.rejected = {env.symbols[2]}
    env.load()
    bad = next(
        key[1] for key in env.coordinator._cache._entries if key[0] == "quote"
        and key[1].provider_mapping.symbol == env.symbols[2]
    )
    ranks = env.coordinator._failure_ranks()
    assert ranks == {("history", bad): 1, ("quote", bad): 1}


# Recovery probe while the provider-wide backoff is open (#111). One probe per window goes
# to the first row of the common order: rows without a failure first, then the one whose
# last attempt lies furthest back. The failure count never ranks.
SHORT_HISTORY_TTLS = CacheTTLs(quotes_seconds=60, holdings_seconds=300, history_seconds=600)


@pytest.mark.parametrize("now", [CLOSED, RUNNING], ids=["closed", "running"])
@pytest.mark.parametrize("neighbours", [2, 5])
def test_repaired_row_with_a_long_failure_history_is_probed_within_one_round(now, neighbours):
    env = warm_env("gD" * (neighbours + 1), now=now)
    env.coordinator._cache.ttls = SHORT_HISTORY_TTLS
    assert {e.status for e in env.load().entries} == {WatchlistEntryStatus.LIVE}
    first, others = env.symbols[0], set(env.symbols[1:])
    env.yahoo.rejected = {first}  # fails alone for a long time, the others keep the provider up
    for _ in range(12):
        env.now += timedelta(minutes=10)
        env.load()
    series = env.coordinator._failure_ranks()
    assert max(count for (kind, _), count in series.items() if kind == "history") >= 12
    env.yahoo.rejected = {first} | others  # now nothing succeeds: the backoff stays open
    healed = None
    for minute in range(0, 600, 10):
        if minute == 40:
            env.yahoo.rejected = others  # the first row is repaired at +40 min
        env.now += timedelta(minutes=10)
        bundle = env.load()
        if minute >= 40 and bundle.entries[0].status is WatchlistEntryStatus.LIVE:
            healed = minute
            break
    # One probe per refresh and every failing row once per round. Ranked by failure
    # count the repaired row waited behind each neighbour until their counts passed its own.
    assert healed is not None and healed - 40 <= neighbours * 10


@pytest.mark.parametrize("rows", [60, 250])
def test_total_outage_keeps_the_provider_backoff_for_60_and_250_rows(rows):
    env = warm_env("gD" * rows, now=CLOSED)
    env.yahoo.fail = True
    for _ in range(360):  # two hours, a refresh every 20 s
        env.load()
        env.now += timedelta(seconds=20)
    assert len(env.yahoo.calls) == 30  # backoff windows 15 s ... 300 s, one probe each
    assert len({symbol for symbol, _ in env.yahoo.calls}) == 30  # went round, no repeats
    env.yahoo.fail = False
    assert seconds_until_valid_rows_are_live(env, limit=900) <= 300


@pytest.mark.parametrize(
    "layout, flaky, limit",
    [
        ("gD" * 9 + "bD" * 3, range(9), 300),  # every valid row carries a quote counter
        ("bD" * 3 + "gD" * 9, range(3, 12), 300),  # of the previous day's session
        ("bD" * 5 + "gD" * 7, range(5, 12), 300),
        ("gD" * 3 + "bD" * 3 + "gD" * 6, range(6, 12), 300),  # block in the middle
    ],
    ids=["block-at-end", "block-at-start", "five-rejected", "block-in-the-middle"],
)
def test_next_day_valid_rows_with_frozen_quote_counters_are_live_within_five_minutes(
    layout, flaky, limit
):
    env = warm_env(layout, now=RUNNING)
    frozen_quote_counters_then_reject(env, flaky)
    # The rejected block shows up in the next day's running session, production TTLs.
    env.now = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
    assert seconds_until_valid_rows_are_live(
        env, limit=limit, rejected_status=WatchlistEntryStatus.SHEET_VALUE
    ) <= limit


def test_quote_counters_of_the_same_session_keep_their_weight_after_a_long_pause():
    # Rows whose quote keeps failing in the open session stay behind the others after a
    # pause of hours inside that session: no way back to the cold-start recovery table.
    env = warm_env("bU" * 4 + "gU" * 8, now=datetime(2026, 10, 28, 14, 0, tzinfo=UTC))
    env.coordinator._cache.ttls = PRODUCTION_TTLS
    assert {e.status for e in env.load().entries} == {WatchlistEntryStatus.LIVE}
    reject(env)
    for _ in range(30):
        env.now += timedelta(seconds=60)
        env.load()
    assert min(
        count for (kind, _), count in env.coordinator._failure_ranks().items() if kind == "quote"
    ) >= 3
    env.now += timedelta(hours=3)  # NYSE session (14:30-21:00 UTC) still running
    env.yahoo.calls.clear()
    env.load()
    first = [symbol for symbol, _ in env.yahoo.calls][:4]
    assert not {symbol for symbol in first if symbol.startswith("B")}  # valid rows first


def test_a_quote_counter_from_before_the_close_is_not_counted_after_it():
    env = warm_env("bU" * 3 + "gU" * 5, now=datetime(2026, 10, 28, 14, 0, tzinfo=UTC))
    env.coordinator._cache.ttls = PRODUCTION_TTLS
    env.load()
    reject(env)
    for _ in range(10):
        env.now += timedelta(seconds=60)
        env.load()
    items = env.coordinator.last_bundle.snapshot.items
    plan = [
        watchlist_refresh._Planned(
            item, watchlist_listing(item), env.coordinator._calendars["NMS"], True
        )
        for item in items
    ]
    rejected = {watchlist_listing(item).instrument for item in items[:3]}
    during = env.coordinator._probe_order(plan, env.now)
    assert all(during[inst][0] == 1 for inst in rejected)  # failing: behind the others
    assert all(key[0] == 0 for inst, key in during.items() if inst not in rejected)
    after_close = datetime(2026, 10, 28, 22, 0, tzinfo=UTC)  # NYSE closed at 21:00 UTC
    after = env.coordinator._probe_order(plan, after_close)
    assert {key[0] for key in after.values()} == {0}  # the frozen series no longer counts


def seed_history_series(env, symbols, failures, attempted_ago, *, due=True):
    """Constructed state: ``symbols`` carry a history failure series (see Seed 164 of #108).

    Reproducing it by chance needs a chaos run; the series is written directly, the rest
    (ordering, backoff, fetching) is the real flow.
    """
    for key, entry in env.coordinator._cache._entries.items():
        if key[0] == "history" and key[1].provider_mapping.symbol in symbols:
            entry.failures = failures
            entry.last_attempt_at = env.now - attempted_ago
            entry.retry_after = None
            if due:
                entry.fetched_at = env.now - timedelta(
                    seconds=env.coordinator._cache.ttls.history_seconds + 1
                )


def test_valid_rows_with_an_older_history_series_beat_freshly_rejected_rows():
    # Valid rows hold a real history series (2 failures); eight freshly rejected rows have
    # only a quote series of 6 and their history is due too. Ranked by history count alone
    # each rejected row took three probes (to 3 > 2) before the valid rows came: 4860 s.
    env = warm_env("gU" * 6 + "bU" * 8, now=datetime(2026, 10, 28, 14, 0, tzinfo=UTC))
    env.coordinator._cache.ttls = CacheTTLs(60, 300, 3600)
    assert {e.status for e in env.load().entries} == {WatchlistEntryStatus.LIVE}
    reject(env)
    for _ in range(20):  # quote series of the rejected rows climb in the open session
        env.now += timedelta(seconds=60)
        env.load()
    series = env.coordinator._failure_ranks()
    assert min(series[("quote", inst)] for (kind, inst) in series if kind == "quote") >= 3
    valid = {s for s, kind in zip(env.symbols, env.kinds) if kind == "g"}
    seed_history_series(env, valid, 2, timedelta(minutes=20))
    for key, entry in env.coordinator._cache._entries.items():  # rejected histories due too
        if key[0] == "history" and key[1].provider_mapping.symbol not in valid:
            entry.fetched_at = env.now - timedelta(seconds=3601)
    assert seconds_until_valid_rows_are_live(env, limit=1800) <= 360


def test_cached_quote_of_the_same_session_is_kept_but_not_across_days():
    env = Env([row("US", symbol="SYN2", exchange="NMS", currency="USD")], now=RUNNING,
              symbols=[("SYN2", "NMS", "USD")])
    env.load()
    first_quote_calls = len(env.yahoo.quote_calls("SYN2"))
    assert first_quote_calls == 1
    # Quote TTL expires, the provider breaks, the cached quote of this session is
    # still today's price and is shown as last valid state.
    env.now += timedelta(minutes=10)
    env.yahoo.fail = True
    entry = env.load().entries[0]
    assert entry.status is WatchlistEntryStatus.CACHED
    assert entry.age_text == "vor 10 Min."
    # A new session day: the old quote is stale, so no period can be shown.
    env.now += timedelta(days=1)
    entry = env.load().entries[0]
    assert entry.status is WatchlistEntryStatus.SHEET_VALUE
    assert entry.period("Jahr").reason in {"stale_quote", "missing_quote"}


@pytest.mark.parametrize(
    ("symbol", "exchange", "currency", "fetched", "shown"),
    [
        # Fetched in the running session, shown after the close, inside the 1 h history TTL.
        ("SYN1.DE", "GER", "EUR", datetime(2026, 10, 28, 16, 0, tzinfo=UTC), datetime(2026, 10, 28, 16, 45, tzinfo=UTC)),
        ("SYN1.DE", "GER", "EUR", datetime(2026, 10, 19, 15, 0, tzinfo=UTC), datetime(2026, 10, 19, 15, 45, tzinfo=UTC)),
        ("SYN2", "NMS", "USD", datetime(2026, 10, 28, 19, 45, tzinfo=UTC), datetime(2026, 10, 28, 20, 30, tzinfo=UTC)),
        ("SYN3", "NYQ", "USD", datetime(2026, 3, 9, 19, 30, tzinfo=UTC), datetime(2026, 3, 9, 20, 15, tzinfo=UTC)),
        ("SYN2", "NMS", "USD", datetime(2026, 11, 2, 20, 30, tzinfo=UTC), datetime(2026, 11, 2, 21, 10, tzinfo=UTC)),
    ],
)
def test_history_fetched_during_the_session_is_fetched_again_after_the_close(
    symbol, exchange, currency, fetched, shown
):
    # Intraday state 1.0 vs. the real close: the cached bar must not pass as the close (#110).
    env = Env([row(symbol=symbol, exchange=exchange, currency=currency)], now=fetched,
              symbols=[(symbol, exchange, currency)])
    env.yahoo.specs[symbol]["intraday"] = 1.0
    env.load()
    assert len(env.yahoo.history_calls(symbol)) == 1
    env.now = shown
    entry = env.load().entries[0]
    last = shown.date()
    previous = max(d for d in env.yahoo.calendars[exchange].sessions if d < last)
    assert len(env.yahoo.history_calls(symbol)) == 2
    assert entry.status is WatchlistEntryStatus.LIVE and entry.market_open is False
    assert entry.current_date == last
    assert entry.price == Decimal(str(close_of(last)))
    assert entry.period("Heute").percent == pct(last, previous)
    env.load()
    assert len(env.yahoo.history_calls(symbol)) == 2


def test_sheet_failure_keeps_the_previous_bundle_with_age():
    env = Env(mapped_rows(), symbols=MAPPED_SYMBOLS)
    first = env.load()
    env.now += timedelta(minutes=10)
    env.read_error = RuntimeError(SECRET)
    second = env.load(refresh=True)
    assert second.available and second.using_cached_stand
    assert second.entries == first.entries and second.snapshot == first.snapshot
    assert "Alter: vor 10 Min." in second.status_message
    assert second.last_attempt_at == env.now
    assert SECRET not in repr(second)


def test_sheet_failure_without_earlier_state_reports_data_unavailable():
    env = Env(mapped_rows(), symbols=MAPPED_SYMBOLS)
    env.read_error = RuntimeError(SECRET)
    bundle = env.load()
    assert not bundle.available and bundle.entries == ()
    assert bundle.snapshot.status is SnapshotStatus.FAILED
    assert bundle.status_message.startswith(DATA_UNAVAILABLE_TEXT)
    assert SECRET not in repr(bundle)


def test_sheet_is_read_once_within_the_ttl_and_again_on_refresh():
    env = Env(mapped_rows(), symbols=MAPPED_SYMBOLS)
    env.load()
    env.now += timedelta(seconds=30)
    env.load()
    assert env.reads == 1
    env.load(refresh=True)
    assert env.reads == 2
    env.now += timedelta(seconds=301)
    env.load()
    assert env.reads == 3


def test_refresh_status_message_only_after_a_manual_refresh():
    env = Env(mapped_rows(), symbols=MAPPED_SYMBOLS)
    assert env.load().status_message is None
    assert env.load(refresh=True).status_message.startswith("Daten erfolgreich aktualisiert")


# --- the Sheets reader -----------------------------------------------------------


class Response:
    def __init__(self, payload=None, status=200, raw=None):
        self.status_code = status
        self.body = json.dumps(payload) if raw is None else raw
        self.closed = False

    def json(self, **kwargs):
        return json.loads(self.body, **kwargs)

    def close(self):
        self.closed = True


class Session:
    def __init__(self, *events):
        self.events = iter(events)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        event = next(self.events)
        if isinstance(event, Exception):
            raise event
        return event


def source(cell_range="Watchlist!A1:J300"):
    return SourceConfig(
        SourceKind.GOOGLE_SHEETS,
        "synthetic-private-source",
        "Bestand!A1:M20",
        "/nonexistent/synthetic-credentials.json",
        None,
        cell_range,
    )


def reader(session, cell_range="Watchlist!A1:J300", **kwargs):
    return WatchlistSheetsReader(source(cell_range), session, **kwargs)


def payload(rows, headers=FULL_HEADERS):
    return {"majorDimension": "ROWS", "values": [list(headers), *rows]}


def test_reader_requests_exactly_the_configured_range_with_the_depot_render_options():
    session = Session(Response(payload([row()])))
    snapshot = reader(session).read(CLOSED)
    (url, kwargs), = session.calls
    assert url.endswith("/values/Watchlist%21A1%3AJ300")
    assert kwargs["params"] == SHEET_REQUEST_PARAMS
    assert kwargs["allow_redirects"] is False and kwargs["verify"] is True
    assert kwargs["timeout"] == 10
    assert snapshot.status is SnapshotStatus.COMPLETE and len(snapshot.items) == 1
    assert snapshot.items[0].mapping.symbol == "SYN.DE"


def test_reader_uses_the_same_value_render_options_as_the_depot_adapter():
    depot_session = Session(Response({"majorDimension": "ROWS", "values": []}))
    adapter = SheetsAdapter(
        source("Watchlist!A1:J300"), LOCAL_SHEET_METADATA, depot_session
    )
    adapter.read(attempted_at=CLOSED)
    watchlist_session = Session(Response({"majorDimension": "ROWS", "values": []}))
    reader(watchlist_session).read(CLOSED)
    assert watchlist_session.calls[0][1]["params"] == depot_session.calls[0][1]["params"]
    assert watchlist_session.calls[0][1]["params"]["valueRenderOption"] == "UNFORMATTED_VALUE"
    assert set(depot_session.calls[0][1]) == set(watchlist_session.calls[0][1])


def test_reader_numbers_and_percent_strings_follow_the_depot_semantics():
    rows = [
        ["A", "10,5", "1,5 %", "0,1"],
        ["B", 10.5, 1.5, 0.1],
    ]
    session = Session(Response(payload(rows, BASIC_HEADERS)))
    first, second = reader(session, "Watchlist!A1:D300").read(CLOSED).items
    assert first.sheet_change_percent_points == second.sheet_change_percent_points == Decimal("1.5")
    assert first.sheet_price.price == second.sheet_price.price == Decimal("10.5")


def test_reader_clips_oversized_ranges_and_reports_partial():
    session = Session(Response(payload([row()])))
    snapshot = reader(session, "Watchlist!A1:T99999999").read(CLOSED)
    assert f"A1%3AT{MAX_WATCHLIST_ROWS}" in session.calls[0][0]
    assert snapshot.status is SnapshotStatus.PARTIAL
    assert {w.code for w in snapshot.warnings} == {"watchlist_row_limit"}
    assert len(snapshot.items) == 1
    # A range within the limit is requested unchanged and stays COMPLETE.
    session = Session(Response(payload([row()])))
    exact = reader(session, f"'Meine Liste'!A3:J{MAX_WATCHLIST_ROWS + 2}").read(CLOSED)
    assert exact.status is SnapshotStatus.COMPLETE
    assert "A3%3AJ2002" in session.calls[0][0] and "%27Meine%20Liste%27" in session.calls[0][0]
    assert exact.items[0].source_row == 4  # rows keep their sheet numbers


def test_reader_small_row_limit_bounds_the_request():
    session = Session(Response(payload([row()] * 3)))
    snapshot = reader(session, "Watchlist!A1:J300", max_rows=4).read(CLOSED)
    assert "A1%3AJ4" in session.calls[0][0]
    assert snapshot.status is SnapshotStatus.PARTIAL
    overflow = Session(Response(payload([row()] * 4)))
    assert reader(overflow, "Watchlist!A1:J300", max_rows=4).read(CLOSED).status is SnapshotStatus.FAILED


def test_reader_clipping_does_not_hide_a_failed_schema():
    session = Session(Response({"majorDimension": "ROWS", "values": [["Falsch"]]}))
    snapshot = reader(session, "Watchlist!A1:T99999999").read(CLOSED)
    assert snapshot.status is SnapshotStatus.FAILED and not snapshot.items


def test_reader_empty_range_is_a_successful_empty_watchlist():
    snapshot = reader(Session(Response({"majorDimension": "ROWS"}))).read(CLOSED)
    assert snapshot.status is SnapshotStatus.COMPLETE and snapshot.items == ()


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "sheet_authentication_failed"),
        (403, "sheet_permission_denied"),
        (404, "sheet_source_unavailable"),
        (429, "sheet_rate_limited"),
        (503, "sheet_unavailable"),
        (400, "sheet_http_error"),
    ],
)
def test_reader_http_failures_are_neutral_and_not_retried(status, code):
    session = Session(Response({"error": SECRET}, status=status))
    snapshot = reader(session).read(CLOSED)
    assert snapshot.status is SnapshotStatus.FAILED and snapshot.items == ()
    assert [w.code for w in snapshot.warnings] == [code]
    assert len(session.calls) == 1 and SECRET not in repr(snapshot)


@pytest.mark.parametrize(
    "event,code",
    [
        (Timeout(SECRET), "sheet_timeout"),
        (ConnectionError(SECRET), "sheet_transport_error"),
        (Response(raw="not json " + SECRET), "sheet_invalid_response"),
        (Response({"majorDimension": "COLUMNS", "values": []}), "sheet_invalid_response"),
        (Response({"majorDimension": "ROWS", "values": [["a"] * 11]}), "sheet_invalid_response"),
        (Response({"majorDimension": "ROWS", "values": [[["nested"]]]}), "sheet_invalid_response"),
        (Response({"majorDimension": "ROWS", "values": ["row"]}), "sheet_invalid_response"),
    ],
)
def test_reader_transport_and_response_problems_yield_failed_snapshots(event, code):
    snapshot = reader(Session(event)).read(CLOSED)
    assert snapshot.status is SnapshotStatus.FAILED
    assert [w.code for w in snapshot.warnings] == [code]
    assert SECRET not in repr(snapshot)
    if isinstance(event, Response):
        assert event.closed


def test_reader_rejects_unusable_configuration_before_any_request():
    session = Session()
    for bad in (
        replace(source(), watchlist_range=None),
        replace(source(), watchlist_range="Watchlist!B1:J300"),
        replace(source(), watchlist_range="A1:J300"),
        replace(source(), spreadsheet_id="bad id/with?chars"),
        replace(source(), kind=SourceKind.SYNTHETIC),
    ):
        with pytest.raises(SheetsConfigurationError):
            WatchlistSheetsReader(bad, session)
    with pytest.raises(SheetsConfigurationError):
        WatchlistSheetsReader(source(), session, max_rows=1)
    with pytest.raises(SheetsConfigurationError):
        WatchlistSheetsReader(source(), session, timeout_seconds=0)
    with pytest.raises(ValueError):
        reader(session).read(datetime(2026, 1, 1))  # naive clock
    assert session.calls == []
    assert "synthetic-private-source" not in repr(reader(session))


def test_reader_feeds_the_coordinator_end_to_end_with_clipped_range_warning():
    session = Session(Response(payload([["Fonds"] + row()[1:4] + [None] * 6])))
    coordinator = WatchlistRefreshCoordinator(
        reader(session, "Watchlist!A1:J99999").read,
        calendars=venue_calendars(date(2026, 10, 28)),
        clock=lambda: CLOSED,
    )
    bundle = coordinator.load()
    assert bundle.available and bundle.snapshot.status is SnapshotStatus.PARTIAL
    assert bundle.entries[0].status is WatchlistEntryStatus.SHEET_VALUE


# --- builders ---------------------------------------------------------------------


def local_values(**extra):
    return {
        "PORTFOLIO_MODE": "local",
        "PORTFOLIO_SHEET_ID": "synthetic_sheet_id_123",
        "PORTFOLIO_SHEET_RANGE": "Bestand!A1:M20",
        "PORTFOLIO_SHEETS_CREDENTIALS_FILE": "/nonexistent/synthetic-credentials.json",
        **extra,
    }


def test_builders_return_nothing_without_a_watchlist_range(monkeypatch):
    def forbidden(*args):
        raise AssertionError("no session without a Watchlist range")

    monkeypatch.setattr(watchlist_refresh, "build_readonly_session", forbidden)
    monkeypatch.setattr(watchlist_refresh, "build_readonly_session_from_info", forbidden)
    config = load_config(local_values())
    assert build_local_watchlist_coordinator(config) is None
    assert build_production_watchlist_coordinator(config, {"type": "service_account"}) is None


def test_local_builder_uses_the_read_only_service_account_session(monkeypatch):
    session = Session(Response(payload([["Fonds", "9,5", "0,5", "0.05"]], BASIC_HEADERS)))
    seen = []
    monkeypatch.setattr(
        watchlist_refresh, "build_readonly_session",
        lambda credentials_file: seen.append(credentials_file) or session,
    )
    config = load_config(local_values(PORTFOLIO_WATCHLIST_RANGE="Watchlist!A1:D300"))
    coordinator = build_local_watchlist_coordinator(config)
    assert seen == ["/nonexistent/synthetic-credentials.json"]
    bundle = coordinator.load()
    assert bundle.available and bundle.entries[0].status is WatchlistEntryStatus.SHEET_VALUE
    assert bundle.entries[0].price == Decimal("9.5")
    assert session.calls[0][1]["params"]["valueRenderOption"] == "UNFORMATTED_VALUE"


def test_production_builder_reads_the_service_account_secret_section(monkeypatch):
    section = {"type": "service_account"}
    session = Session(Response(payload([], BASIC_HEADERS)))
    seen = []
    monkeypatch.setattr(
        watchlist_refresh, "build_readonly_session_from_info",
        lambda info: seen.append(info) or session,
    )
    config = replace(
        load_config(local_values(PORTFOLIO_WATCHLIST_RANGE="Watchlist!A1:D300")),
    )
    coordinator = build_production_watchlist_coordinator(config, section)
    assert seen == [section]
    assert coordinator.load().available
    shared = Session(Response(payload([], BASIC_HEADERS)))
    assert build_local_watchlist_coordinator(config, session=shared).load().available
    assert len(shared.calls) == 1


# --- result model and demo ---------------------------------------------------------


def test_period_value_invariants():
    with pytest.raises(ValueError):
        WatchlistPeriodValue("Quartal", Decimal(1), WatchlistOrigin.PROVIDER)
    with pytest.raises(ValueError):
        WatchlistPeriodValue("Heute", None, WatchlistOrigin.NONE)  # n. v. needs a reason
    with pytest.raises(ValueError):
        WatchlistPeriodValue("Heute", Decimal(1), WatchlistOrigin.NONE)
    assert WatchlistPeriodValue("Heute", Decimal(0), WatchlistOrigin.SHEET).available


def test_demo_watchlist_mixes_german_us_and_sheet_only_rows_offline():
    from portfolio_dashboard.demo import (
        DEMO_TIMESTAMP,
        demo_watchlist_bundle,
        demo_watchlist_snapshot,
    )

    bundle = demo_watchlist_bundle()
    assert bundle.available and bundle.last_attempt_at == DEMO_TIMESTAMP
    assert demo_watchlist_snapshot().status is SnapshotStatus.COMPLETE
    exchanges = {e.item.mapping.exchange for e in bundle.entries if e.item.mapping}
    assert {"GER", "FRA", "NMS", "NYQ"} == exchanges
    demo = [e for e in bundle.entries if e.item.mapping]
    assert {e.status for e in demo} == {WatchlistEntryStatus.DEMO}
    assert all(all(p.available for p in e.periods) for e in demo)
    assert all(p.origin is WatchlistOrigin.DEMO for e in demo for p in e.periods)
    assert {e.price_unit.currency for e in demo} == {"EUR", "USD"}
    sheet = [e for e in bundle.entries if not e.item.mapping]
    assert [e.status for e in sheet] == [
        WatchlistEntryStatus.SHEET_VALUE,
        WatchlistEntryStatus.UNAVAILABLE,
    ]
    assert sheet[0].period("Heute").origin is WatchlistOrigin.SHEET
    assert sheet[0].period("Woche").reason == "unverified_mapping"
    assert len({e.item.name for e in bundle.entries}) == len(bundle.entries)


def test_demo_watchlist_needs_no_ui_provider_or_sheets_packages():
    code = (
        "import sys\n"
        "for name in ('streamlit', 'pandas', 'yfinance', 'requests', 'plotly', 'google'):\n"
        "    sys.modules[name] = None\n"
        "from portfolio_dashboard.demo import demo_watchlist_bundle\n"
        "assert len(demo_watchlist_bundle().entries) == 6\n"
    )
    subprocess.run([sys.executable, "-I", "-c", code], check=True)
