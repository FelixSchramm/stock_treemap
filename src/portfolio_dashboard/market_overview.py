"""Public market overview for the start page: fixed symbols, no Sheet, no secrets.

Every symbol below was checked once against Yahoo's history metadata
(exchangeName, exchange time zone, currency); ``YFinanceProvider`` rejects each
response that differs, and the time zone is checked here. Index and sector-ETF
returns use the same exchange calendars and price engine as the Watchlist.
Values stay in their own currency or index points: nothing is converted or added.
Sector ETFs stand in for sector indices, which Yahoo does not carry reliably.
"""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
from exchange_calendars.exchange_calendar_xlon import XLONExchangeCalendar

from portfolio_dashboard.fundamentals_provider import _quiet_provider_logs
from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.local_source import _library_sessions, venue_calendars
from portfolio_dashboard.market_data import YFinanceProvider, _ticker
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    ProviderMapping,
    QuoteUnit,
    ValueStatus,
)
from portfolio_dashboard.price_periods import (
    PricePeriod,
    SessionCalendar,
    price_period_returns,
    session_calendar,
)
from portfolio_dashboard.refresh import PERIOD_KEY_TO_ENUM
from portfolio_dashboard.watchlist_quotes import PERIODS
from portfolio_dashboard.watchlist_refresh import session_running

NY, BERLIN = "America/New_York", "Europe/Berlin"
SPARKLINE_POINTS = 260  # About one year of daily closes.


@dataclass(frozen=True)
class MarketSymbol:
    """One checked Yahoo symbol; ``calendar`` None marks a 24-hour market."""

    name: str
    symbol: str
    group: str
    exchange: str  # Yahoo exchangeName
    currency: str
    timezone: str  # Yahoo exchangeTimezoneName
    calendar: str | None  # key of market_calendars()


WORLD_INDICES = (
    MarketSymbol("DAX", "^GDAXI", "Europa", "GER", "EUR", BERLIN, "GER"),
    MarketSymbol("MDAX", "^MDAXI", "Europa", "GER", "EUR", BERLIN, "GER"),
    # STOXX indices follow the Eurex trading days, which equal the German closures.
    MarketSymbol("Euro Stoxx 50", "^STOXX50E", "Europa", "ZRH", "EUR", "Europe/Zurich", "GER"),
    MarketSymbol("STOXX Europe 600", "^STOXX", "Europa", "ZRH", "EUR", "Europe/Zurich", "GER"),
    MarketSymbol("FTSE 100", "^FTSE", "Europa", "FGI", "GBP", "Europe/London", "LSE"),
    MarketSymbol("CAC 40", "^FCHI", "Europa", "PAR", "EUR", "Europe/Paris", "PAR"),
    MarketSymbol("SMI", "^SSMI", "Europa", "EBS", "CHF", "Europe/Zurich", "EBS"),
    MarketSymbol("S&P 500", "^GSPC", "Amerika", "SNP", "USD", NY, "NYQ"),
    MarketSymbol("Nasdaq 100", "^NDX", "Amerika", "NIM", "USD", NY, "NYQ"),
    MarketSymbol("Dow Jones", "^DJI", "Amerika", "DJI", "USD", NY, "NYQ"),
    MarketSymbol("Russell 2000", "^RUT", "Amerika", "WCB", "USD", NY, "NYQ"),
    MarketSymbol("Nikkei 225", "^N225", "Asien", "OSA", "JPY", "Asia/Tokyo", "JPX"),
    MarketSymbol("Hang Seng", "^HSI", "Asien", "HKG", "HKD", "Asia/Hong_Kong", "HKG"),
    MarketSymbol("KOSPI", "^KS11", "Asien", "KSC", "KRW", "Asia/Seoul", "KSC"),
    MarketSymbol("Nifty 50", "^NSEI", "Asien", "NSI", "INR", "Asia/Kolkata", "NSI"),
)
# Select Sector SPDR ETFs (NYSE Arca) for the eleven S&P 500 sectors.
US_SECTORS = tuple(
    MarketSymbol(name, symbol, "USA", "PCX", "USD", NY, "PCX")
    for name, symbol in (
        ("Technologie", "XLK"), ("Finanzen", "XLF"), ("Energie", "XLE"),
        ("Gesundheit", "XLV"), ("Industrie", "XLI"), ("Zyklischer Konsum", "XLY"),
        ("Basiskonsum", "XLP"), ("Versorger", "XLU"), ("Grundstoffe", "XLB"),
        ("Immobilien", "XLRE"), ("Kommunikation", "XLC"),
    )
)
# iShares STOXX Europe 600 sector ETFs on Xetra.
EUROPE_SECTORS = tuple(
    MarketSymbol(name, symbol, "Europa", "GER", "EUR", BERLIN, "GER")
    for name, symbol in (
        ("Banken", "EXV1.DE"), ("Telekommunikation", "EXV2.DE"), ("Technologie", "EXV3.DE"),
        ("Gesundheit", "EXV4.DE"), ("Automobile", "EXV5.DE"), ("Rohstoffe", "EXV6.DE"),
        ("Chemie", "EXV7.DE"), ("Bau & Material", "EXV8.DE"), ("Reisen & Freizeit", "EXV9.DE"),
        ("Öl & Gas", "EXH1.DE"), ("Finanzdienstleister", "EXH2.DE"),
        ("Nahrungsmittel & Getränke", "EXH3.DE"), ("Industriegüter", "EXH4.DE"),
        ("Versicherungen", "EXH5.DE"), ("Medien", "EXH6.DE"), ("Konsumgüter", "EXH7.DE"),
        ("Einzelhandel", "EXH8.DE"), ("Versorger", "EXH9.DE"), ("Immobilien", "EXI5.DE"),
    )
)
GROUPS = {
    "Weltindizes": WORLD_INDICES,
    "Branchen USA": US_SECTORS,
    "Branchen Europa": EUROPE_SECTORS,
}
# Currencies, commodities and crypto trade around the clock: change to the prior daily bar.
ASSETS = (
    MarketSymbol("EUR/USD", "EURUSD=X", "Devisen", "CCY", "USD", "Europe/London", None),
    MarketSymbol("Gold", "GC=F", "Rohstoffe", "CMX", "USD", NY, None),
    MarketSymbol("Öl (Brent)", "BZ=F", "Rohstoffe", "NYM", "USD", NY, None),
    MarketSymbol("Bitcoin", "BTC-EUR", "Krypto", "CCC", "EUR", "UTC", None),
)
SPARKLINES = ("DAX", "S&P 500")
# Börsenuhr: label, calendar key, exchange time zone.
CLOCK = (
    ("Tokio", "JPX", "Asia/Tokyo"),
    ("Hongkong", "HKG", "Asia/Hong_Kong"),
    ("Frankfurt", "GER", BERLIN),
    ("London", "LSE", "Europe/London"),
    ("New York", "NYQ", NY),
)
LONDON_HOURS = (time(8), time(16, 30))  # London Stock Exchange, Europe/London


@dataclass(frozen=True)
class MarketQuote:
    item: MarketSymbol
    percents: dict[str, Decimal | None]  # period label -> percent, None = NA
    price: Decimal | None = None
    current_date: date | None = None
    market_open: bool | None = None
    closes: tuple[tuple[date, Decimal], ...] = ()

    def percent(self, period: str) -> Decimal | None:
        return self.percents.get(period)


@dataclass(frozen=True)
class VenueClock:
    label: str
    local_time: datetime
    status: str  # "offen", "Mittagspause", "geschlossen" or "unbekannt"


@dataclass(frozen=True)
class Breadth:
    up: int
    down: int
    quoted: int
    total: int
    best: MarketQuote | None
    worst: MarketQuote | None


def market_calendars(today: date) -> dict[str, SessionCalendar]:
    """``venue_calendars`` plus London, which only the overview uses."""
    calendars = venue_calendars(today)
    window = calendars["GER"]
    span = _library_sessions(XLONExchangeCalendar, window.first_day, window.last_day)
    if span is not None:
        calendars["LSE"] = session_calendar(span[0], *LONDON_HOURS, span[1], span[2])
    return calendars


class _DailyBars:
    """Yahoo ticker that tolerates daily rows without a close.

    Yahoo leaves the newest daily bar of some indices empty (seen for Nikkei 225,
    Hang Seng, KOSPI, Nifty 50) while its quote metadata carries the last value of
    that same session; only then is the newest bar filled from it. Any other row
    without a close counts as a missing bar (e.g. a quiet ETF day): the strict
    provider would reject the whole history, the price engine's gap rules decide
    instead (``MAX_MISSING_SESSIONS``, else NA).
    """

    def __init__(self, ticker):
        self._ticker = ticker

    def history(self, **kwargs):
        frame = self._ticker.history(**kwargs)
        if not isinstance(frame, pd.DataFrame) or frame.empty or "Close" not in frame:
            return frame
        if pd.isna(frame["Close"].iloc[-1]):
            metadata = self._ticker.get_history_metadata()
            price, stamp = metadata.get("regularMarketPrice"), metadata.get("regularMarketTime")
            last = frame.index[-1]
            if (
                isinstance(stamp, datetime) and stamp.tzinfo is not None
                and isinstance(last, pd.Timestamp) and last.tzinfo is not None
                and stamp.astimezone(last.tzinfo).date() == last.date()
            ):
                frame = frame.copy()
                frame.loc[last, "Close"] = price
        return frame.dropna(subset=["Close"])

    def get_history_metadata(self):
        return self._ticker.get_history_metadata()


def _listing(item: MarketSymbol) -> VerifiedListing:
    # Technical registry key like the Watchlist's; carries no product identity.
    return VerifiedListing(
        Instrument(
            InstrumentType.STOCK,
            instrument_id=f"market:{item.exchange}:{item.symbol}",
            trading_venue=item.exchange,
            quote_unit=QuoteUnit(item.currency, Decimal(1)),
            provider_mapping=ProviderMapping("yfinance", item.symbol),
        ),
        equity_form=EquityForm.SHARE,
    )


def _fetch(item, now, calendars, ticker_factory) -> MarketQuote:
    empty = MarketQuote(item, dict.fromkeys(PERIODS))
    calendar = calendars.get(item.calendar)
    if item.calendar is not None and calendar is None:
        return empty  # never invent sessions for a venue without a calendar
    try:
        listing = _listing(item)
        provider = YFinanceProvider(
            (listing,), ticker_factory=lambda symbol: _DailyBars(ticker_factory(symbol)),
            clock=lambda: now,
        )
        start = calendar.first_day if calendar else now.date() - timedelta(days=370)
        history = provider.history(listing.instrument, start, now.date() + timedelta(days=1))
        if history.status is not ValueStatus.AVAILABLE or history.exchange_timezone != item.timezone:
            return empty
        closes = tuple((point.session_date, point.close) for point in history.points)
        if calendar is None:
            (_, previous), (day, last) = closes[-2:]
            change = (last - previous) / previous * 100 if previous else None
            return MarketQuote(
                item, {**empty.percents, "Heute": change}, last, day, closes=closes[-SPARKLINE_POINTS:]
            )
        quote = (
            provider.latest_quote(listing.instrument)
            if session_running(calendar, item.timezone, now) else None
        )
        results = price_period_returns(history, quote, calendar, now)
        today = results[PricePeriod.TODAY]
        return MarketQuote(
            item,
            {label: results[PERIOD_KEY_TO_ENUM[label]].percent for label in PERIODS},
            today.current_price,
            today.current_date,
            today.market_open,
            closes[-SPARKLINE_POINTS:],
        )
    except Exception:
        return empty


def load_quotes(
    symbols: Sequence[MarketSymbol],
    now: datetime,
    *,
    ticker_factory: Callable = _ticker,
    calendars: dict[str, SessionCalendar] | None = None,
) -> tuple[MarketQuote, ...]:
    """One bounded request batch; a failing symbol becomes NA, never an error."""
    calendars = calendars if calendars is not None else market_calendars(now.date())
    with _quiet_provider_logs(), ThreadPoolExecutor(max_workers=8) as pool:
        return tuple(pool.map(lambda item: _fetch(item, now, calendars, ticker_factory), symbols))


def market_clock(now: datetime, calendars: dict[str, SessionCalendar]) -> tuple[VenueClock, ...]:
    """Status of each ``CLOCK`` exchange at ``now`` in its own time; no data access."""
    result = []
    for label, key, timezone in CLOCK:
        local = now.astimezone(ZoneInfo(timezone))
        calendar = calendars.get(key)
        if calendar is None or not calendar.first_day <= local.date() <= calendar.last_day:
            status = "unbekannt"
        elif local.date() in calendar.sessions and calendar.open_time <= local.time() < calendar.close_time:
            status = "Mittagspause" if calendar.in_break(local.time()) else "offen"
        else:
            status = "geschlossen"
        result.append(VenueClock(label, local, status))
    return tuple(result)


def breadth(quotes: Sequence[MarketQuote], period: str) -> Breadth:
    """Gainers, losers and extremes; NA values count as neither."""
    quoted = [q for q in quotes if q.percent(period) is not None]
    return Breadth(
        sum(q.percent(period) > 0 for q in quoted),
        sum(q.percent(period) < 0 for q in quoted),
        len(quoted),
        len(quotes),
        max(quoted, key=lambda q: q.percent(period), default=None),
        min(quoted, key=lambda q: q.percent(period), default=None),
    )
