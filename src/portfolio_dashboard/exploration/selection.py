"""Read-only company catalog from the existing, authorized Sheet adapters.

No display ticker is ever promoted into a provider mapping. This catalog carries
no quantities, cost bases, spreadsheet identifiers or account information.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from portfolio_dashboard.instruments import EquityForm
from portfolio_dashboard.models import InstrumentType, PositionActivity, SnapshotStatus


@dataclass(frozen=True, repr=False)
class Company:
    name: str
    symbol: str | None
    exchange: str | None
    currency: str | None
    origins: tuple[str, ...]
    row_key: str
    scale: Decimal | None = None
    equity_form: EquityForm | None = None
    reason: str | None = None

    @property
    def key(self):
        return (
            (self.symbol, self.exchange, self.currency, self.scale)
            if self.symbol
            else (self.row_key,)
        )

    @property
    def label(self):
        listing = f" · {self.symbol} / {self.exchange}" if self.symbol else " · n. v."
        return self.name + listing + " · " + " + ".join(self.origins)


# Public starting selection from the original analysis, independent of holdings.
# Live Yahoo metadata must still confirm each listing before any prices are used.
DEFAULT_COMPANIES = tuple(
    Company(
        name,
        symbol,
        exchange,
        currency,
        ("Standardauswahl",),
        f"default:{symbol}",
        Decimal("1"),
        EquityForm.SHARE,
    )
    for name, symbol, exchange, currency in (
        ("Microsoft", "MSFT", "NMS", "USD"),
        ("Oracle", "ORCL", "NYQ", "USD"),
        ("Alibaba", "9988.HK", "HKG", "HKD"),
        ("SAP", "SAP.DE", "GER", "EUR"),
    )
)


def combine_companies(*groups):
    merged = {}
    for group in groups:
        for item in group:
            if item.key not in merged:
                merged[item.key] = item
            else:
                first = merged[item.key]
                merged[item.key] = replace(
                    first,
                    origins=tuple(dict.fromkeys(first.origins + item.origins)),
                    equity_form=first.equity_form or item.equity_form,
                )
    return tuple(merged.values())


@dataclass(frozen=True, repr=False)
class Catalog:
    depot: tuple[Company, ...] = ()
    watchlist: tuple[Company, ...] = ()
    notes: tuple[str, ...] = ()
    depot_at: datetime | None = None
    watchlist_at: datetime | None = None

    @property
    def companies(self):
        return combine_companies(self.depot, self.watchlist)


def portfolio_companies(snapshot, mappings):
    result = []
    for position in snapshot.positions:
        if position.activity is not PositionActivity.ACTIVE:
            continue
        listing = mappings.get(position.source_row)
        instrument = listing.instrument if listing else position.instrument
        mapping = instrument.provider_mapping if listing else None
        reason = None
        if instrument.instrument_type is not InstrumentType.STOCK:
            reason = "n. v. – Analyse ist nur für einzelne Aktien angebunden."
        elif mapping is None or mapping.provider != "yfinance":
            reason = "n. v. – Geprüfte Yahoo-Zuordnung fehlt. Anzeigename und Ticker allein reichen nicht."
        unit = instrument.quote_unit
        result.append(
            Company(
                position.name,
                mapping.symbol if reason is None else None,
                instrument.trading_venue,
                unit.currency if unit else None,
                ("Depot",),
                f"depot:{position.source_row}",
                unit.scale_to_currency if unit else None,
                listing.equity_form if listing else None,
                reason,
            )
        )
    return tuple(result)


def watchlist_companies(snapshot):
    result = []
    for item in snapshot.items:
        mapping = item.mapping
        kind = (mapping.instrument_type if mapping else None) or item.instrument_type
        reason = None
        if kind is not InstrumentType.STOCK:
            reason = (
                "n. v. – Aktienart fehlt oder das Instrument ist keine einzelne Aktie."
            )
        elif mapping is None:
            reason = (
                "n. v. – Yahoo-Zuordnung im Watchlist-Sheet fehlt oder ist ungültig."
            )
        result.append(
            Company(
                item.name,
                mapping.symbol if reason is None else None,
                mapping.exchange if mapping else None,
                mapping.currency if mapping else None,
                ("Watchlist",),
                f"watchlist:{item.source_row}",
                Decimal("1") if mapping else None,
                reason=reason,
            )
        )
    return tuple(result)


def load_catalog(coordinator, watchlist_loader, previous=None):
    """Read only Sheet snapshots, never a bulk quote refresh; preserve failed sources."""
    previous = previous or Catalog()
    depot, watchlist = previous.depot, previous.watchlist
    depot_at, watchlist_at = previous.depot_at, previous.watchlist_at
    notes = []
    if coordinator is not None:
        try:
            snapshot, mappings = coordinator.read_selection()
            if snapshot.status is SnapshotStatus.FAILED:
                raise ValueError("failed")
            depot, depot_at = (
                portfolio_companies(snapshot, mappings),
                snapshot.attempted_at,
            )
            if snapshot.status is SnapshotStatus.PARTIAL:
                notes.append(
                    "Depot-Liste ist unvollständig; ungültige Sheet-Zeilen wurden ausgelassen."
                )
        except Exception:
            notes.append(
                "Depot-Sheet nicht lesbar. Ein zuvor geladener Listenstand bleibt erhalten."
            )
    else:
        notes.append("Depot-Sheet nicht eingerichtet.")
    if watchlist_loader is not None:
        try:
            snapshot = watchlist_loader.read_selection()
            if snapshot.status is SnapshotStatus.FAILED:
                raise ValueError("failed")
            watchlist, watchlist_at = (
                watchlist_companies(snapshot),
                snapshot.attempted_at,
            )
            if snapshot.status is SnapshotStatus.PARTIAL:
                notes.append(
                    "Watchlist-Liste ist unvollständig; ungültige Sheet-Zeilen wurden ausgelassen."
                )
        except Exception:
            notes.append(
                "Watchlist-Sheet nicht lesbar. Ein zuvor geladener Listenstand bleibt erhalten."
            )
    else:
        notes.append("Watchlist-Sheet nicht eingerichtet.")
    return Catalog(depot, watchlist, tuple(notes), depot_at, watchlist_at)


def demo_catalog():
    """Use the dashboard's existing synthetic Depot and Watchlist, never real holdings."""
    from portfolio_dashboard.demo import demo_watchlist_snapshot
    from portfolio_dashboard.instruments import resolve_listing
    from portfolio_dashboard.valuation_demo import demo_valuation_source

    source = demo_valuation_source()
    snapshot = source.portfolio.snapshot
    mappings = {
        p.source_row: resolve_listing(p.instrument, source.listings)
        for p in snapshot.positions
    }
    return Catalog(
        portfolio_companies(snapshot, mappings),
        watchlist_companies(demo_watchlist_snapshot()),
        (),
        datetime.now(UTC),
        datetime.now(UTC),
    )
