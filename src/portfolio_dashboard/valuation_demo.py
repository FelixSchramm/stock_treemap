"""Synthetic inputs of the valuation overview for the demo mode (#79).

Offline and invented: the demo depot is re-read with the demo registry so one of
its positions resolves to a verified listing (the shared security of the demo
Watchlist), and the demo scenario reader serves an invented, clearly synthetic
document. Nothing here touches the protected store, a provider or a real identity.
"""

from dataclasses import replace

from portfolio_dashboard.demo import DEMO_TIMESTAMP, demo_snapshot
from portfolio_dashboard.fundamentals_demo import (
    DEMO_VALUED_ID,
    DemoScenarioSource,
    demo_fundamentals_loader,
    demo_registry,
)
from portfolio_dashboard.valuation_overview import OverviewSource
from portfolio_dashboard.valuations import PortfolioValuation, value_portfolio

DEMO_PORTFOLIO_STOCK = "Demo-Aktie A"


def demo_overview_valuation() -> PortfolioValuation:
    """The demo depot with its stock tied to the verified Frankfurt demo listing.

    Only this overview reads it; the Depot view keeps the unchanged demo snapshot.
    """
    registry = demo_registry()
    listing = next(
        item
        for item in registry
        if item.instrument.instrument_id == DEMO_VALUED_ID
        and item.instrument.trading_venue == "FRA"
    )
    snapshot = demo_snapshot()
    positions = tuple(
        replace(
            position,
            instrument=replace(
                listing.instrument, display_ticker=position.instrument.display_ticker
            ),
        )
        if position.name == DEMO_PORTFOLIO_STOCK
        else position
        for position in snapshot.positions
    )
    return value_portfolio(replace(snapshot, positions=positions), registry)


def demo_valuation_source() -> OverviewSource:
    return OverviewSource(
        demo_registry(),
        DemoScenarioSource(demo_fundamentals_loader()).get,
        lambda: DEMO_TIMESTAMP,
        demo_overview_valuation(),
        True,
    )
