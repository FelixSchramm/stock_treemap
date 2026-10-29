"""Refresh coordinator for the owner's own data (local and production mode).

Values, weights and returns since purchase come from the owner's Sheet. Private
listings assign owner-verified Yahoo EUR listings by the Sheet ISIN; only those
positions receive provider quotes and course periods. Local mode reads the key
and listings from files, production from the server-side secrets.
"""

import tomllib
from dataclasses import replace
from datetime import date, time, timedelta
from decimal import Decimal

from dateutil.easter import easter

from portfolio_dashboard.config import AppConfig
from portfolio_dashboard.instruments import EquityForm, VerifiedListing, resolve_listing
from portfolio_dashboard.market_data import YFinanceProvider
from portfolio_dashboard.models import (
    DataWarning,
    Instrument,
    InstrumentType,
    PortfolioSnapshot,
    ProviderMapping,
    QuoteUnit,
    SnapshotStatus,
)
from portfolio_dashboard.price_periods import SessionCalendar, session_calendar
from portfolio_dashboard.quote_cache import QuoteCache
from portfolio_dashboard.refresh import PortfolioRefreshCoordinator
from portfolio_dashboard.sheet_parser import (
    NumberLocale,
    SheetMetadata,
    SheetParseResult,
)
from portfolio_dashboard.sheets_adapter import SheetsAdapter
from portfolio_dashboard.sheets_session import (
    build_readonly_session,
    build_readonly_session_from_info,
)

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
# US equity exchanges (Yahoo exchangeName) for the Watchlist (ADR 0011). They are
# deliberately not part of VENUE_HOURS, which also validates the depot's listings.
US_VENUE_HOURS = {
    venue: (time(9, 30), time(16))  # America/New_York
    for venue in ("NMS", "NGM", "NCM", "NYQ", "ASE", "PCX")
}
# Unscheduled full-day closures (national days of mourning, 9/11, storm).
US_SPECIAL_CLOSURES = frozenset(
    {
        *(date(2001, 9, day) for day in range(11, 15)),
        date(2004, 6, 11),
        date(2007, 1, 2),
        date(2012, 10, 29),
        date(2012, 10, 30),
        date(2018, 12, 5),
        date(2025, 1, 9),
    }
)


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


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def us_holidays(year: int) -> set[date]:
    """Regular NYSE/Nasdaq weekday closures of one year, observed dates included.

    A fixed-date holiday on Saturday closes the Friday before, on Sunday the
    Monday after. New Year's Day is the exception: on a Saturday the exchanges
    stay open on 31 December, so that year contributes no closure for it.
    """
    may_31 = date(year, 5, 31)
    closed = {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Presidents' Day
        easter(year) - timedelta(days=2),  # Good Friday
        may_31 - timedelta(days=may_31.weekday()),  # Memorial Day (last Monday)
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
    }
    fixed = [(date(year, 7, 4), True), (date(year, 12, 25), True)]
    if year >= 2022:
        fixed.append((date(year, 6, 19), True))  # Juneteenth
    for day, friday_substitute in fixed:
        if day.weekday() == 5 and friday_substitute:
            closed.add(day - timedelta(days=1))
        elif day.weekday() == 6:
            closed.add(day + timedelta(days=1))
        elif day.weekday() < 5:
            closed.add(day)
    new_year = date(year, 1, 1)
    if new_year.weekday() < 5:
        closed.add(new_year)
    elif new_year.weekday() == 6:
        closed.add(new_year + timedelta(days=1))
    return closed


def venue_calendars(today: date) -> dict[str, SessionCalendar]:
    """Calendars spanning more than a year back and a year ahead of ``today``.

    German venues use Europe/Berlin dates and hours, US venues America/New_York;
    early closes are modelled for neither.
    """
    first, last = today - timedelta(days=400), today + timedelta(days=366)
    years = range(first.year, last.year + 1)
    german_closed = set().union(*(_holidays(year) for year in years))
    us_closed = set().union(*(us_holidays(year) for year in years))
    us_closed |= US_SPECIAL_CLOSURES
    days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
    calendars = {}
    for hours, closed in ((VENUE_HOURS, german_closed), (US_VENUE_HOURS, us_closed)):
        sessions = [day for day in days if day.weekday() < 5 and day not in closed]
        for venue, (opens, closes) in hours.items():
            calendars[venue] = session_calendar(sessions, opens, closes, first, last)
    return calendars


def load_listings(path: str) -> tuple[VerifiedListing, ...]:
    """Read the ``[[listing]]`` entries of a private TOML file."""
    try:
        with open(path, "rb") as file:
            entries = tomllib.load(file).get("listing", [])
    except (OSError, tomllib.TOMLDecodeError):
        raise ListingsFileError("Zuordnungsdatei fehlt oder ist ungültig.") from None
    return parse_listings(entries)


def parse_listings(entries: object) -> tuple[VerifiedListing, ...]:
    """Validate entries with isin, type, symbol, venue and form/class."""
    if not isinstance(entries, list):
        raise ListingsFileError("Zuordnung enthält keine Liste [[listing]].")
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
                f"Zuordnung: Eintrag {number} ist unvollständig oder ungültig."
            ) from None
    isins = [listing.isin for listing in listings]
    symbols = [listing.instrument.provider_mapping.symbol for listing in listings]
    if len(set(isins)) < len(isins) or len(set(symbols)) < len(symbols):
        raise ListingsFileError("Zuordnung: ISIN oder Symbol mehrfach vergeben.")
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


def sheet_listing_candidates(
    parsed: SheetParseResult, primary: tuple[VerifiedListing, ...]
) -> tuple[PortfolioSnapshot, dict[int, VerifiedListing]]:
    """Validate complete owner declarations without inferring product identity.

    Bad optional rows are isolated. Alternative symbols at the same venue are
    allowed, but product form/class and a symbol's product identity must agree.
    """
    positions = {p.source_row: p for p in parsed.snapshot.positions}
    candidates: dict[int, VerifiedListing] = {}
    invalid: set[int] = set()
    for entry in parsed.yahoo_listings:
        position = positions[entry.source_row]
        source = position.instrument
        matching = next((item for item in primary if (
            item.isin == source.source_isin
            and item.instrument.instrument_type is source.instrument_type
        )), None)
        try:
            if entry.venue not in VENUE_HOURS or source.source_isin is None:
                raise ValueError
            if any(item.isin == source.source_isin
                   and item.instrument.instrument_type is not source.instrument_type
                   for item in primary):
                raise ValueError
            candidate = VerifiedListing(
                Instrument(
                    source.instrument_type,
                    instrument_id=(matching.instrument.instrument_id if matching
                                   else source.source_isin),
                    trading_venue=entry.venue,
                    quote_unit=EUR,
                    provider_mapping=ProviderMapping("yfinance", entry.symbol),
                ),
                equity_form=EquityForm(entry.equity_form) if entry.equity_form else None,
                isin=source.source_isin,
                share_class=entry.share_class,
            )
            if source.instrument_type is InstrumentType.DERIVATIVE:
                raise ValueError
            if matching and (candidate.equity_form != matching.equity_form
                             or candidate.share_class != matching.share_class):
                raise ValueError
            if any(
                item.instrument.provider_mapping == candidate.instrument.provider_mapping
                and (item.instrument.instrument_id, item.instrument.trading_venue)
                != (candidate.instrument.instrument_id, candidate.instrument.trading_venue)
                for item in primary
            ):
                raise ValueError
            candidates[entry.source_row] = candidate
        except (TypeError, ValueError):
            invalid.add(entry.source_row)

    for row, candidate in candidates.items():
        for other_row, other in candidates.items():
            same_product = candidate.instrument.instrument_id == other.instrument.instrument_id
            same_symbol = candidate.instrument.provider_mapping == other.instrument.provider_mapping
            if (same_product and candidate != other) or (same_symbol and not same_product):
                invalid.update((row, other_row))
    notes = tuple(DataWarning(
        "invalid_yahoo_listing", "Yahoo-Ersatzzuordnung widersprüchlich oder nicht unterstützt; ignoriert.",
        "yahoo_listing", row,
    ) for row in sorted(invalid))
    snapshot = replace(
        parsed.snapshot,
        positions=tuple(replace(p, warnings=p.warnings + tuple(
            note for note in notes if note.source_row == p.source_row
        )) for p in parsed.snapshot.positions),
        warnings=parsed.snapshot.warnings + notes,
    )
    by_product = {
        (candidate.isin, candidate.instrument.instrument_type): candidate
        for row, candidate in candidates.items() if row not in invalid
    }
    return snapshot, {
        p.source_row: by_product[(p.instrument.source_isin, p.instrument.instrument_type)]
        for p in snapshot.positions
        if (p.instrument.source_isin, p.instrument.instrument_type) in by_product
    }


def build_local_coordinator(config: AppConfig) -> PortfolioRefreshCoordinator:
    """Local mode: key and optional listings from the configured files."""
    listings = (
        load_listings(config.source.listings_file)
        if config.source.listings_file
        else ()
    )
    return _coordinator(
        config, build_readonly_session(config.source.credentials_file), listings
    )


def build_production_coordinator(
    config: AppConfig, service_account: object, listing_entries: object
) -> PortfolioRefreshCoordinator:
    """Production: [sheets_service_account] and [[listing]] from server secrets."""
    listings = parse_listings(listing_entries)
    return _coordinator(
        config, build_readonly_session_from_info(service_account), listings
    )


def _coordinator(
    config: AppConfig, session, listings: tuple[VerifiedListing, ...]
) -> PortfolioRefreshCoordinator:
    """Read the configured Sheet through the read-only service-account session."""
    adapter = SheetsAdapter(config.source, LOCAL_SHEET_METADATA, session)
    fallbacks: dict[int, VerifiedListing] = {}
    provider_listings = listings

    def read_holdings(attempted_at):
        nonlocal fallbacks, provider_listings
        result = adapter.read(attempted_at=attempted_at)
        if result.snapshot.status is SnapshotStatus.FAILED:
            return result.snapshot
        snapshot, fallbacks = sheet_listing_candidates(result.parsed, listings)
        provider_listings = tuple(dict.fromkeys((*listings, *fallbacks.values())))
        return assign_listings(snapshot, listings)

    def resolve_provider_listing(instrument):
        # Candidate symbols for the same ID/venue live separately until selection.
        candidates = tuple(item for item in provider_listings
                           if item.instrument.provider_mapping == instrument.provider_mapping)
        return resolve_listing(instrument, candidates)

    return PortfolioRefreshCoordinator(
        read_holdings,
        QuoteCache(YFinanceProvider(listings, listing_resolver=resolve_provider_listing), config.ttls),
        listings=listings,
        fallback_listings=lambda: fallbacks,
        calendars=venue_calendars(date.today()),
    )
