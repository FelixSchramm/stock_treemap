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
from portfolio_dashboard.treemap import Performance, personal_performances
from portfolio_dashboard.valuations import PortfolioValuation, value_portfolio

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
