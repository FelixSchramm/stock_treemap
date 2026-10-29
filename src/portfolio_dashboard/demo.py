"""Synthetic starter data, independent of UI libraries and external services."""

from datetime import date, datetime, timezone
from decimal import Decimal

from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    PortfolioSnapshot,
    Position,
    Provenance,
    QuoteUnit,
    SnapshotStatus,
    Valuation,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.returns import portfolio_returns
from portfolio_dashboard.sheet_parser import NumberLocale
from portfolio_dashboard.treemap import Performance, personal_performances
from portfolio_dashboard.valuations import PortfolioValuation, value_portfolio
from portfolio_dashboard.watchlist_quotes import (
    WatchlistBundle,
    WatchlistEntry,
    demo_entry,
    sheet_entry,
)
from portfolio_dashboard.watchlist_sheet import (
    BASIC_HEADERS,
    OPTIONAL_HEADERS,
    WatchlistSnapshot,
    parse_watchlist_rows,
)

# A fixed fixture timestamp is never presented as the current clock or live data.
DEMO_TIMESTAMP = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)

# Names, holdings and prices are invented, prefilled demo values: fixed EUR
# prices as a Sheet fallback (no provider, no live quote). Cost bases stay
# unknown, so the personal view shows "n. v.".
_DEMO_POSITIONS = (
    ("Demo-Aktie A", "DEMO-A", InstrumentType.STOCK, "10", "120", 1200.0),
    ("Demo-Fonds B", "DEMO-B", InstrumentType.ETF, "4", "200", 800.0),
)

# Fixed invented course percentages per period, in position order.
_DEMO_COURSE = {
    "Heute": ("1.25", "-0.80"),
    "Woche": ("-2.40", "0.60"),
    "Monat": ("4.10", "-3.30"),
    "Jahr": ("12.50", "7.25"),
}
_DEMO_REFERENCE = {
    "Heute": date(2026, 10, 22),
    "Woche": date(2026, 10, 16),
    "Monat": date(2026, 9, 23),
    "Jahr": date(2025, 10, 23),
}


def demo_snapshot() -> PortfolioSnapshot:
    """Return an offline model fixture with fixed prices and unknown cost basis."""
    return PortfolioSnapshot(
        attempted_at=DEMO_TIMESTAMP,
        status=SnapshotStatus.COMPLETE,
        positions=tuple(
            Position(
                name=name,
                quantity=Decimal(quantity),
                instrument=Instrument(kind, display_ticker=ticker),
                category="Demo",
                source_row=index,
                imported_at=DEMO_TIMESTAMP,
                valuation=Valuation(
                    Decimal(price),
                    ValueStatus.AVAILABLE,
                    Provenance(ValuationSource.SHEET, DEMO_TIMESTAMP),
                    QuoteUnit("EUR", Decimal("1")),
                ),
            )
            for index, (name, ticker, kind, quantity, price, _) in enumerate(
                _DEMO_POSITIONS, start=1
            )
        ),
    )


def demo_rows() -> list[dict[str, str | float]]:
    """Return fresh synthetic rows; never read configuration or contact providers."""
    return [
        {"Instrument": name, "Wert (EUR)": value}
        for name, _, _, _, _, value in _DEMO_POSITIONS
    ]


def demo_valuation(snapshot: PortfolioSnapshot) -> PortfolioValuation:
    """Value a synthetic snapshot offline (EUR only, no listings or FX)."""
    return value_portfolio(snapshot, ())


def demo_course_performances(period: str) -> tuple[Performance, ...]:
    """Fixed invented course percentages, one per valuation group."""
    return tuple(
        Performance(Decimal(percent), _DEMO_REFERENCE[period])
        for percent in _DEMO_COURSE[period]
    )


def demo_personal_performances(
    valuation: PortfolioValuation,
) -> tuple[Performance, ...]:
    return personal_performances(portfolio_returns(valuation))


# Invented Watchlist rows in the sheet layout (A-D plus the optional columns):
# German and US mappings, a Sheet-only value and a row without any value.
_DEMO_WATCHLIST_ROWS = (
    ("Demo-Aktie DE", "45,20", "1,10", "0,50", "DEMO-DE", "Aktie", "DEMOA.DE", "GER", "EUR", "Demo"),
    ("Demo-ETF DE", "80,00", "-0,40", "-0,32", "DEMO-ETF", "ETF", "DEMOB.F", "FRA", "EUR", "Demo"),
    ("Demo-Aktie US", "90,00", "2,15", "1,90", "DEMO-US", "Aktie", "DEMOC", "NMS", "USD", "Demo"),
    ("Demo-Aktie NYSE", "30,50", "-1,75", "-0,54", "DEMO-NY", "Aktie", "DEMOD", "NYQ", "USD", "Demo"),
    ("Demo-Krypto", "1500,00", "-2,30", "-35,00", None, None, None, None, None, None),
    ("Demo-ohne Kurs", None, None, None, None, None, None, None, None, None),
)
# Fixed invented course percentages per mapped row: Heute, Woche, Monat, Jahr.
_DEMO_WATCHLIST_COURSE = {
    "Demo-Aktie DE": ("1.10", "-1.20", "3.40", "9.80"),
    "Demo-ETF DE": ("-0.40", "0.70", "-2.10", "5.60"),
    "Demo-Aktie US": ("2.15", "4.05", "-6.30", "18.90"),
    "Demo-Aktie NYSE": ("-1.75", "-0.55", "1.20", "-4.40"),
}
_DEMO_WATCHLIST_PRICES = {
    "Demo-Aktie DE": ("45.20", "EUR"),
    "Demo-ETF DE": ("80.00", "EUR"),
    "Demo-Aktie US": ("98.40", "USD"),
    "Demo-Aktie NYSE": ("33.10", "USD"),
}


def demo_watchlist_snapshot() -> WatchlistSnapshot:
    """Return the invented Watchlist rows parsed by the real Sheet parser."""
    return parse_watchlist_rows(
        (*BASIC_HEADERS, *OPTIONAL_HEADERS),
        _DEMO_WATCHLIST_ROWS,
        imported_at=DEMO_TIMESTAMP,
        number_locale=NumberLocale.GERMAN,
    ).snapshot


def demo_watchlist_entries() -> tuple[WatchlistEntry, ...]:
    """Mapped demo rows show invented values (origin demo); the others the Sheet value."""
    entries = []
    for item in demo_watchlist_snapshot().items:
        if item.name in _DEMO_WATCHLIST_COURSE:
            price, currency = _DEMO_WATCHLIST_PRICES[item.name]
            entries.append(
                demo_entry(
                    item,
                    (Decimal(p) for p in _DEMO_WATCHLIST_COURSE[item.name]),
                    Decimal(price),
                    QuoteUnit(currency, Decimal("1")),
                    date(2026, 10, 22),
                    DEMO_TIMESTAMP,
                )
            )
        else:
            entries.append(sheet_entry(item))
    return tuple(entries)


def demo_watchlist_bundle() -> WatchlistBundle:
    """Offline Watchlist bundle; no Sheet, provider or clock access."""
    return WatchlistBundle(
        True, demo_watchlist_snapshot(), demo_watchlist_entries(), DEMO_TIMESTAMP
    )
