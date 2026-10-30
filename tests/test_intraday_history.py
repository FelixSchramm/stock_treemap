"""A daily history fetched during a session is not fresh after that session closed (#110).

Synthetic provider and injected clocks; no network. The provider returns an
intraday state as the last bar while the session runs and the final close
afterwards, like a real daily feed. The 6 h history TTL alone would keep the
intraday bar for hours after the close.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from portfolio_dashboard.config import CacheTTLs
from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.local_source import venue_calendars
from portfolio_dashboard.market_data import (
    AdjustmentBasis,
    FxObservation,
    HistoryPoint,
    PriceHistory,
    PriceObservation,
    VerifiedFxPair,
)
from portfolio_dashboard.models import (
    DataWarning,
    Instrument,
    InstrumentType,
    PortfolioSnapshot,
    Position,
    Provenance,
    ProviderMapping,
    QuoteUnit,
    SnapshotStatus,
    Valuation,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.price_periods import session_closed_between
from portfolio_dashboard.quote_cache import QuoteCache
from portfolio_dashboard.refresh import PortfolioRefreshCoordinator

CALENDARS = venue_calendars(date(2026, 10, 28))
TIMEZONES = {"GER": "Europe/Berlin", "FRA": "Europe/Berlin", "NMS": "America/New_York"}
CURRENCIES = {"GER": "EUR", "FRA": "EUR", "NMS": "USD"}
PREVIOUS, INTRADAY, FINAL = Decimal("110.3"), Decimal("110.0"), Decimal("99.0")
# Issue #110: 110,0 shown as close gives -0,27 %, the real close -10,24 %.
FINAL_PERCENT = (FINAL - PREVIOUS) / PREVIOUS * 100


def utc(month, day, hour, minute=0, year=2026):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# --- session_closed_between: close instants in the exchange's own time ---------

CLOSE_CASES = [
    # German summer time: Xetra closes 17:30 CEST = 15:30 UTC.
    ("GER", utc(10, 19, 11), utc(10, 19, 15, 15), False),
    ("GER", utc(10, 19, 11), utc(10, 19, 15, 45), True),
    # After the DST end on 25 Oct: 17:30 CET = 16:30 UTC.
    ("GER", utc(10, 26, 11), utc(10, 26, 16, 15), False),
    ("GER", utc(10, 26, 11), utc(10, 26, 16, 45), True),
    # DST start on 29 Mar: Friday still CET (16:30 UTC), Monday CEST (15:30 UTC).
    ("GER", utc(3, 27, 15), utc(3, 27, 16), False),
    ("GER", utc(3, 27, 15), utc(3, 27, 16, 45), True),
    ("GER", utc(3, 30, 14), utc(3, 30, 15, 15), False),
    ("GER", utc(3, 30, 14), utc(3, 30, 15, 45), True),
    # Frankfurt trades until 22:00 local; Xetra's close does not matter there.
    ("FRA", utc(10, 28, 11), utc(10, 28, 16, 45), False),
    ("FRA", utc(10, 28, 11), utc(10, 28, 20, 45), False),
    ("FRA", utc(10, 28, 11), utc(10, 28, 21, 5), True),
    # US: 16:00 local. DST starts 8 Mar: Friday EST (21:00 UTC), Monday EDT (20:00 UTC).
    ("NMS", utc(3, 6, 19), utc(3, 6, 20, 30), False),
    ("NMS", utc(3, 6, 19), utc(3, 6, 21, 30), True),
    ("NMS", utc(3, 9, 19, 30), utc(3, 9, 20, 30), True),
    ("NMS", utc(3, 9, 19, 30), utc(3, 9, 19, 50), False),
    # DST ends 1 Nov: Friday EDT (20:00 UTC), Monday EST (21:00 UTC).
    ("NMS", utc(10, 30, 15), utc(10, 30, 20, 30), True),
    ("NMS", utc(11, 2, 16), utc(11, 2, 20, 30), False),
    ("NMS", utc(11, 2, 16), utc(11, 2, 21, 5), True),
    # Holidays and weekends have no session, hence no close.
    ("GER", utc(4, 3, 10), utc(4, 3, 18), False),  # Good Friday
    ("NMS", utc(11, 26, 15), utc(11, 26, 23), False),  # Thanksgiving
    ("GER", utc(10, 24, 10), utc(10, 25, 10), False),  # Saturday to Sunday
    # A fetch after the close is final; the next close is a new one.
    ("NMS", utc(10, 28, 22), utc(10, 29, 15), False),
    ("NMS", utc(10, 28, 22), utc(10, 29, 21), True),
    # Fetched before the open of a session day: that day's bar is still missing.
    ("GER", utc(10, 28, 5), utc(10, 28, 17), True),
    # Fetched exactly at the close: already final. Over a weekend the Friday close counts.
    ("GER", utc(10, 28, 16, 30), utc(10, 28, 18), False),
    ("GER", utc(10, 23, 12), utc(10, 26, 8), True),
]


@pytest.mark.parametrize(("venue", "since", "until", "expected"), CLOSE_CASES)
def test_session_close_between_two_instants(venue, since, until, expected):
    assert session_closed_between(CALENDARS[venue], TIMEZONES[venue], since, until) is expected


# --- synthetic provider -------------------------------------------------------


class SessionProvider:
    """Intraday last bar during a session, final close afterwards; counts fetches."""

    def __init__(self, clock, venue):
        self.clock = clock
        self.venue = venue
        self.calendar = CALENDARS[venue]
        self.zone = ZoneInfo(TIMEZONES[venue])
        self.unit = QuoteUnit(CURRENCIES[venue], Decimal(1))
        self.history_calls = 0
        self.fail = False

    def _provenance(self):
        return Provenance(
            ValuationSource.PROVIDER, self.clock(), self.clock(), provider="yfinance"
        )

    def history(self, instrument, start, end, *, minimum_points=2):
        self.history_calls += 1
        if self.fail:
            return PriceHistory(
                ValueStatus.MISSING,
                self._provenance(),
                warnings=(DataWarning("provider_failure", "down"),),
            )
        local = self.clock().astimezone(self.zone)
        points = []
        for day in sorted(self.calendar.sessions):
            if day > local.date() or day < start:
                continue
            close = PREVIOUS
            if day == local.date():
                if local.time() < self.calendar.open_time:
                    continue  # No bar yet before the open.
                close = INTRADAY if local.time() < self.calendar.close_time else FINAL
            points.append(HistoryPoint(day, close, Decimal(0)))
        return PriceHistory(
            ValueStatus.AVAILABLE,
            self._provenance(),
            self.unit,
            self.zone.key,
            AdjustmentBasis.YAHOO_SPLIT_ADJUSTED_PRICE,
            tuple(points),
        )

    def latest_quote(self, instrument):
        observed = self.clock() - timedelta(seconds=30)
        return PriceObservation(
            Valuation(
                INTRADAY,
                ValueStatus.AVAILABLE,
                Provenance(ValuationSource.PROVIDER, self.clock(), observed, "yfinance"),
                self.unit,
            ),
            AdjustmentBasis.YAHOO_SPLIT_ADJUSTED_PRICE,
        )

    def fx(self, pair):
        return FxObservation(
            pair, Decimal("0.9"), ValueStatus.AVAILABLE, self._provenance()
        )


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


# Fetched while the session runs; shown after the close, within the 6 h TTL.
SESSION_THEN_CLOSE = [
    pytest.param("GER", utc(10, 28, 11), utc(10, 28, 16, 45), id="xetra-winter"),
    pytest.param("GER", utc(10, 19, 11), utc(10, 19, 15, 45), id="xetra-summer"),
    pytest.param("GER", utc(3, 30, 14), utc(3, 30, 15, 45), id="xetra-after-dst-start"),
    pytest.param("FRA", utc(10, 28, 16), utc(10, 28, 21, 5), id="frankfurt-evening"),
    pytest.param("NMS", utc(10, 28, 15), utc(10, 28, 20, 30), id="us-summer"),
    pytest.param("NMS", utc(3, 9, 15), utc(3, 9, 20, 30), id="us-after-dst-start"),
    pytest.param("NMS", utc(3, 6, 16), utc(3, 6, 21, 30), id="us-before-dst-start"),
    pytest.param("NMS", utc(11, 2, 16), utc(11, 2, 21, 5), id="us-after-dst-end"),
]


def test_cases_stay_inside_the_history_ttl():
    ttl = timedelta(seconds=CacheTTLs().history_seconds)
    for case in SESSION_THEN_CLOSE:
        _, fetched, shown = case.values
        assert shown - fetched < ttl


@pytest.mark.parametrize(("venue", "fetched", "shown"), SESSION_THEN_CLOSE)
def test_cache_refetches_a_history_fetched_before_the_close(venue, fetched, shown):
    clock = Clock(fetched)
    provider = SessionProvider(clock, venue)
    cache = QuoteCache(provider, clock=clock)
    calendar = CALENDARS[venue]
    instrument = Instrument(InstrumentType.STOCK, trading_venue=venue)

    def history():
        return cache.history(
            instrument, calendar.first_day, calendar.last_day, sessions=calendar
        )

    assert history().points[-1].close == INTRADAY
    clock.now = fetched + timedelta(minutes=30)  # still running (or at least cached)
    if not session_closed_between(calendar, TIMEZONES[venue], fetched, clock.now):
        history()
        assert provider.history_calls == 1
    clock.now = shown
    assert history().points[-1].close == FINAL
    assert provider.history_calls == 2
    history()  # fetched after the close: fresh for the TTL again
    assert provider.history_calls == 2


def test_without_sessions_only_the_ttl_applies():
    clock = Clock(utc(10, 28, 15))
    provider = SessionProvider(clock, "NMS")
    cache = QuoteCache(provider, clock=clock)
    calendar = CALENDARS["NMS"]
    instrument = Instrument(InstrumentType.STOCK, trading_venue="NMS")
    cache.history(instrument, calendar.first_day, calendar.last_day)
    clock.now = utc(10, 28, 20, 30)
    assert cache.history(instrument, calendar.first_day, calendar.last_day).points[-1].close == INTRADAY
    assert provider.history_calls == 1


def test_manual_refresh_and_cache_stay_valid_when_no_session_closed():
    clock = Clock(utc(10, 28, 15))  # US session running
    provider = SessionProvider(clock, "NMS")
    cache = QuoteCache(provider, clock=clock)
    calendar = CALENDARS["NMS"]
    instrument = Instrument(InstrumentType.STOCK, trading_venue="NMS")

    def history(**kwargs):
        return cache.history(
            instrument, calendar.first_day, calendar.last_day, sessions=calendar, **kwargs
        )

    history()
    clock.now = utc(10, 28, 19, 59)
    history()
    assert provider.history_calls == 1
    history(refresh=True)
    assert provider.history_calls == 2


def test_failed_refetch_after_the_close_keeps_the_old_history_flagged_with_backoff():
    clock = Clock(utc(10, 28, 15))
    provider = SessionProvider(clock, "NMS")
    cache = QuoteCache(provider, clock=clock)
    calendar = CALENDARS["NMS"]
    instrument = Instrument(InstrumentType.STOCK, trading_venue="NMS")
    first = cache.history(instrument, calendar.first_day, calendar.last_day, sessions=calendar)

    def after_close():
        return cache.history(
            instrument, calendar.first_day, calendar.last_day, sessions=calendar
        )

    provider.fail = True
    clock.now = utc(10, 28, 20, 30)
    shown = after_close()
    assert provider.history_calls == 2
    assert shown.provenance.source is ValuationSource.CACHED_PROVIDER
    assert shown.provenance.retrieved_at == first.provenance.retrieved_at
    assert "cached_provider_value" in [w.code for w in shown.warnings]
    clock.now += timedelta(seconds=5)  # inside the 15 s backoff: no new attempt
    assert after_close().provenance.source is ValuationSource.CACHED_PROVIDER
    assert provider.history_calls == 2
    provider.fail = False
    clock.now += timedelta(seconds=30)
    healed = after_close()
    assert provider.history_calls == 3
    assert healed.points[-1].close == FINAL
    assert healed.provenance.source is ValuationSource.PROVIDER


# --- Depot: PortfolioRefreshCoordinator -----------------------------------------


def depot(venue, clock):
    unit = QuoteUnit(CURRENCIES[venue], Decimal(1))
    identity = Instrument(
        InstrumentType.STOCK,
        instrument_id="SYNTHETIC-INTRADAY",
        trading_venue=venue,
        quote_unit=unit,
    )
    listing = VerifiedListing(
        Instrument(
            InstrumentType.STOCK,
            instrument_id=identity.instrument_id,
            trading_venue=venue,
            quote_unit=unit,
            provider_mapping=ProviderMapping("yfinance", "SYN.X"),
        ),
        equity_form=EquityForm.SHARE,
    )
    sheet_value = Valuation(
        Decimal(100),
        ValueStatus.AVAILABLE,
        Provenance(ValuationSource.SHEET, utc(10, 1, 12)),
        QuoteUnit("EUR", Decimal(1)),
    )
    snapshot = PortfolioSnapshot(
        utc(10, 1, 12),
        SnapshotStatus.COMPLETE,
        (
            Position(
                name="Synthetische Position",
                quantity=Decimal(2),
                instrument=identity,
                category=None,
                source_row=2,
                imported_at=utc(10, 1, 12),
                valuation=sheet_value,
            ),
        ),
    )
    provider = SessionProvider(clock, venue)
    pairs = {}
    if CURRENCIES[venue] != "EUR":
        eur = QuoteUnit("EUR", Decimal(1))
        pairs["USD"] = VerifiedFxPair(unit, eur, ProviderMapping("yfinance", "FX.X"), "SYNTH-FX")
    coordinator = PortfolioRefreshCoordinator(
        lambda attempted_at: snapshot,
        QuoteCache(provider, clock=clock),
        listings=(listing,),
        fx_pairs=pairs,
        calendars={venue: CALENDARS[venue]},
        clock=clock,
    )
    return coordinator, provider


@pytest.mark.parametrize(("venue", "fetched", "shown"), SESSION_THEN_CLOSE)
def test_depot_shows_the_final_close_after_a_fetch_during_the_session(venue, fetched, shown):
    clock = Clock(fetched)
    coordinator, provider = depot(venue, clock)
    bundle = coordinator.load()
    # The running session judges by the live quote, so the intraday bar is not used yet.
    live = bundle.performances_for("Kursentwicklung", "Heute")[0].percent
    assert live == (INTRADAY - PREVIOUS) / PREVIOUS * 100
    assert provider.history_calls == 1

    clock.now = shown
    bundle = coordinator.load()
    assert bundle.performances_for("Kursentwicklung", "Heute")[0].percent == FINAL_PERCENT
    assert provider.history_calls == 2
    coordinator.load()
    assert provider.history_calls == 2


def test_depot_keeps_a_history_fetched_after_the_close_for_the_ttl():
    clock = Clock(utc(10, 28, 22))  # 18:00 EDT, US closed
    coordinator, provider = depot("NMS", clock)
    assert coordinator.load().performances_for("Kursentwicklung", "Heute")[0].percent == FINAL_PERCENT
    clock.now = utc(10, 29, 3)  # night, no session in between
    coordinator.load()
    assert provider.history_calls == 1
