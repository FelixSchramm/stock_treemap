"""Refresh coordinator for the owner's own data (local and production mode).

Holdings and cost basis come from the Sheet journal (ADR 0012); owner-verified
EUR listings come from the tab Instrumente. Only those positions receive
provider quotes and course periods. Local mode reads the key from a file,
production from the server-side secrets.
"""

from datetime import date, time, timedelta

from dateutil.easter import easter

from portfolio_dashboard.config import AppConfig
from portfolio_dashboard.instruments import VerifiedListing, resolve_listing
from portfolio_dashboard.market_data import YFinanceProvider
from portfolio_dashboard.models import SnapshotStatus
from portfolio_dashboard.price_periods import SessionCalendar, session_calendar
from portfolio_dashboard.quote_cache import QuoteCache
from portfolio_dashboard.refresh import PortfolioRefreshCoordinator
from portfolio_dashboard.sheet_parser import NumberLocale
from portfolio_dashboard.sheets_adapter import JournalSheetsReader
from portfolio_dashboard.sheets_session import (
    build_readonly_session,
    build_readonly_session_from_info,
)

# Owner decision: the Sheet uses German number formatting.
SHEET_NUMBER_LOCALE = NumberLocale.GERMAN
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


# Supported depot venues and their required currency: German EUR listings only.
DEPOT_VENUES = {venue: "EUR" for venue in VENUE_HOURS}


def build_local_coordinator(config: AppConfig) -> PortfolioRefreshCoordinator:
    """Local mode: read-only key from the configured credentials file."""
    return _coordinator(config, build_readonly_session(config.source.credentials_file))


def build_production_coordinator(
    config: AppConfig, service_account: object
) -> PortfolioRefreshCoordinator:
    """Production: ``[sheets_service_account]`` from the server secrets.

    Listings come from the Sheet tab Instrumente (ADR 0012); ``[[listing]]``
    secrets are no longer read.
    """
    return _coordinator(config, build_readonly_session_from_info(service_account))


def _coordinator(config: AppConfig, session) -> PortfolioRefreshCoordinator:
    """Holdings, cost basis and verified listings from one journal read."""
    reader = JournalSheetsReader(config.source, session, DEPOT_VENUES)
    state: dict[str, tuple[VerifiedListing, ...]] = {"listings": ()}

    def read_holdings(attempted_at):
        result = reader.read(attempted_at=attempted_at)
        if result.snapshot.status is not SnapshotStatus.FAILED:
            # Listings always belong to the holdings they were read with.
            state["listings"] = result.listings
        return result.snapshot

    def read_selection(attempted_at):
        # A catalog read must not replace the registry of the cached holdings.
        result = reader.read(attempted_at=attempted_at)
        if result.snapshot.status is SnapshotStatus.FAILED:
            return result.snapshot, {}
        mappings = {}
        for position in result.snapshot.positions:
            listing = resolve_listing(position.instrument, result.listings)
            if listing is not None:
                mappings[position.source_row] = listing
        return result.snapshot, mappings

    def resolve_provider_listing(instrument):
        return resolve_listing(instrument, state["listings"])

    return PortfolioRefreshCoordinator(
        read_holdings,
        QuoteCache(YFinanceProvider((), listing_resolver=resolve_provider_listing), config.ttls),
        listings=lambda: state["listings"],
        selection_reader=read_selection,
        calendars=venue_calendars(date.today()),
    )
