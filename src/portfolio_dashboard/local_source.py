"""Refresh coordinator for the loopback-bound local mode.

Values, weights and returns since purchase come from the owner's Sheet. An
optional private listings file assigns owner-verified Yahoo EUR listings by the
Sheet ISIN; only those positions receive provider quotes and course periods.
"""

import tomllib
from dataclasses import replace
from datetime import date, time, timedelta
from decimal import Decimal

from dateutil.easter import easter

from portfolio_dashboard.config import AppConfig
from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.market_data import YFinanceProvider
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    PortfolioSnapshot,
    ProviderMapping,
    QuoteUnit,
)
from portfolio_dashboard.price_periods import SessionCalendar, session_calendar
from portfolio_dashboard.quote_cache import QuoteCache
from portfolio_dashboard.refresh import PortfolioRefreshCoordinator
from portfolio_dashboard.sheet_parser import NumberLocale, SheetMetadata
from portfolio_dashboard.sheets_adapter import SheetsAdapter
from portfolio_dashboard.sheets_session import build_readonly_session

# Owner decisions: German Sheet; column I holds the cost of remaining units (#20).
LOCAL_SHEET_METADATA = SheetMetadata(
    NumberLocale.GERMAN, cost_basis_verified_remaining=True
)
EUR = QuoteUnit("EUR", Decimal("1"))
# Regular hours (Europe/Berlin) per supported Yahoo exchangeName code.
VENUE_HOURS = {
    "GER": (time(9), time(17, 30)),  # Xetra
    "FRA": (time(8), time(22)),
    "STU": (time(8), time(22)),
    "MUN": (time(8), time(22)),
    "DUS": (time(8), time(22)),
}


class ListingsFileError(ValueError):
    """Safe message naming an entry number, never file content or paths."""


def _holidays(year: int) -> set[date]:
    """Weekday closures shared by the German exchanges."""
    sunday = easter(year)
    return {
        date(year, 1, 1),
        sunday - timedelta(days=2),
        sunday + timedelta(days=1),
        date(year, 5, 1),
        date(year, 12, 24),
        date(year, 12, 25),
        date(year, 12, 26),
        date(year, 12, 31),
    }


def venue_calendars(today: date) -> dict[str, SessionCalendar]:
    """Calendars spanning more than a year back and a year ahead of ``today``."""
    first, last = today - timedelta(days=400), today + timedelta(days=366)
    closed = set().union(
        *(_holidays(year) for year in range(first.year, last.year + 1))
    )
    days = (first + timedelta(days=n) for n in range((last - first).days + 1))
    sessions = [day for day in days if day.weekday() < 5 and day not in closed]
    return {
        venue: session_calendar(sessions, opens, closes, first, last)
        for venue, (opens, closes) in VENUE_HOURS.items()
    }


def load_listings(path: str) -> tuple[VerifiedListing, ...]:
    """Read ``[[listing]]`` entries with isin, type, symbol, venue and form/class."""
    try:
        with open(path, "rb") as file:
            entries = tomllib.load(file).get("listing", [])
    except (OSError, tomllib.TOMLDecodeError):
        raise ListingsFileError("Zuordnungsdatei fehlt oder ist ungültig.") from None
    if not isinstance(entries, list):
        raise ListingsFileError("Zuordnungsdatei enthält keine Liste [[listing]].")
    listings = []
    for number, entry in enumerate(entries, 1):
        try:
            kind = InstrumentType(entry["type"])
            if kind is InstrumentType.DERIVATIVE or entry["venue"] not in VENUE_HOURS:
                raise ValueError
            stock = kind is InstrumentType.STOCK
            listings.append(
                VerifiedListing(
                    Instrument(
                        kind,
                        instrument_id=entry["isin"],
                        trading_venue=entry["venue"],
                        quote_unit=EUR,
                        provider_mapping=ProviderMapping("yfinance", entry["symbol"]),
                    ),
                    equity_form=EquityForm(entry["equity_form"]) if stock else None,
                    isin=entry["isin"],
                    share_class=None if stock else entry.get("share_class"),
                )
            )
        except (KeyError, TypeError, ValueError):
            raise ListingsFileError(
                f"Zuordnungsdatei: Eintrag {number} ist unvollständig oder ungültig."
            ) from None
    isins = [listing.isin for listing in listings]
    symbols = [listing.instrument.provider_mapping.symbol for listing in listings]
    if len(set(isins)) < len(isins) or len(set(symbols)) < len(symbols):
        raise ListingsFileError("Zuordnungsdatei: ISIN oder Symbol mehrfach vergeben.")
    return tuple(listings)


def assign_listings(
    snapshot: PortfolioSnapshot, listings: tuple[VerifiedListing, ...]
) -> PortfolioSnapshot:
    """Give Sheet positions the verified identity matching their ISIN and type.

    The owner chose venue and currency in the listings file; column F describes
    the Sheet's own listing (column E), for example a US home listing in USD.
    """
    verified = {
        (listing.isin, listing.instrument.instrument_type): listing.instrument
        for listing in listings
    }
    positions = []
    for position in snapshot.positions:
        source = position.instrument
        listing = verified.get((source.source_isin, source.instrument_type))
        if listing is not None:
            position = replace(
                position,
                instrument=replace(
                    source,
                    instrument_id=listing.instrument_id,
                    trading_venue=listing.trading_venue,
                    quote_unit=listing.quote_unit,
                ),
            )
        positions.append(position)
    return replace(snapshot, positions=tuple(positions))


def build_local_coordinator(config: AppConfig) -> PortfolioRefreshCoordinator:
    """Read the configured Sheet through the read-only service-account session."""
    listings = (
        load_listings(config.source.listings_file)
        if config.source.listings_file
        else ()
    )
    adapter = SheetsAdapter(
        config.source,
        LOCAL_SHEET_METADATA,
        build_readonly_session(config.source.credentials_file),
    )

    def read_holdings(attempted_at):
        return assign_listings(
            adapter.read(attempted_at=attempted_at).snapshot, listings
        )

    return PortfolioRefreshCoordinator(
        read_holdings,
        QuoteCache(YFinanceProvider(listings), config.ttls),
        listings=listings,
        calendars=venue_calendars(date.today()),
    )
