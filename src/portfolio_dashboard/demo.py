"""Synthetic starter data, independent of UI libraries and external services."""

from datetime import datetime, timezone
from decimal import Decimal

from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    PortfolioSnapshot,
    Position,
    SnapshotStatus,
)


# A fixed fixture timestamp is never presented as the current clock or live data.
DEMO_TIMESTAMP = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)

# Names, holdings and display amounts are invented, prefilled demo values.
# Valuation and return calculations belong to their own domain features.
_DEMO_POSITIONS = (
    ("Demo-Aktie A", "DEMO-A", InstrumentType.STOCK, "10", 1200.0),
    ("Demo-Fonds B", "DEMO-B", InstrumentType.ETF, "4", 800.0),
)


def demo_snapshot() -> PortfolioSnapshot:
    """Return an offline model fixture with unknown cost basis and no quotes."""
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
            )
            for index, (name, ticker, kind, quantity, _) in enumerate(
                _DEMO_POSITIONS, start=1
            )
        ),
    )


def demo_rows() -> list[dict[str, str | float]]:
    """Return fresh synthetic rows; never read configuration or contact providers."""
    return [
        {"Instrument": name, "Wert (EUR)": value}
        for name, _, _, _, value in _DEMO_POSITIONS
    ]
