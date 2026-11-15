"""Upcoming market-moving dates for the USA and Europe: public data, no secrets.

Two sources are combined:

* ``economic_events.csv``: rate decisions (Fed, ECB), US CPI, the US jobs report
  and US PCE inflation, copied from the official calendars (Fed, ECB, BLS, BEA)
  on ``SOURCE_DATE``. They make sure the next rate decision is always shown,
  even weeks ahead or when Yahoo fails.
* Yahoo's economic calendar (``yfinance``): about four weeks ahead, with the
  prior and, once released, the actual value. Yahoo gives no forecasts. Only the
  series in ``YAHOO_EVENTS`` are used; everything else is noise for this page.

The Yahoo calendar is an unofficial interface. Every failure returns an empty
result, so the page falls back to the fixed list.
"""

import csv
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

from portfolio_dashboard.fundamentals_provider import _quiet_provider_logs

EVENTS_FILE = Path(__file__).with_name("economic_events.csv")
SOURCE_DATE = "15.11.2026"
BERLIN = ZoneInfo("Europe/Berlin")
REGIONS = ("US", "EU", "DE")
COLUMNS = {"USA": ("US",), "Europa": ("EU", "DE")}
GROUPS = ("Zinsen", "Inflation", "Arbeitsmarkt", "Konjunktur")
RATE_KEYS = ("fed_rate", "ecb_rate")
LOOKAHEAD_DAYS = 28
LOOKBACK_DAYS = 7
# Warn when the fixed list covers fewer days than this: time to add the next year.
LIST_WARNING_DAYS = 60
PAGE_SIZE = 100  # Yahoo caps one calendar page at 100 rows
MAX_PAGES = 10


@dataclass(frozen=True)
class EventType:
    """One tracked release: region, group, German name and value unit."""

    region: str
    group: str
    name: str
    unit: str


EVENT_TYPES = {
    "fed_rate": EventType("US", "Zinsen", "Fed-Zinsentscheid", "% Leitzins (Obergrenze)"),
    "us_cpi": EventType("US", "Inflation", "US-Inflation (CPI)", "% ggü. Vormonat"),
    "us_pce": EventType("US", "Inflation", "US-PCE-Inflation", "% ggü. Vorjahr"),
    "us_jobs": EventType("US", "Arbeitsmarkt", "US-Arbeitsmarktbericht", "Tsd. neue Stellen"),
    "us_unemployment": EventType("US", "Arbeitsmarkt", "US-Arbeitslosenquote", "%"),
    "us_gdp": EventType("US", "Konjunktur", "US-BIP (erste Schätzung)", "% annualisiert"),
    "us_gdp2": EventType("US", "Konjunktur", "US-BIP (zweite Schätzung)", "% annualisiert"),
    "us_ism_mfg": EventType("US", "Konjunktur", "ISM Industrie", "Punkte"),
    "us_ism_srv": EventType("US", "Konjunktur", "ISM Dienstleister", "Punkte"),
    "ecb_rate": EventType("EU", "Zinsen", "EZB-Zinsentscheid", "% Hauptrefinanzierungssatz"),
    "ez_hicp_flash": EventType("EU", "Inflation", "Inflation Eurozone (Schnellschätzung)", "% ggü. Vorjahr"),
    "ez_hicp_final": EventType("EU", "Inflation", "Inflation Eurozone (endgültig)", "% ggü. Vorjahr"),
    "ez_unemployment": EventType("EU", "Arbeitsmarkt", "Arbeitslosenquote Eurozone", "%"),
    "ez_gdp": EventType("EU", "Konjunktur", "BIP Eurozone (Schnellschätzung)", "% ggü. Vorquartal"),
    "de_cpi": EventType("DE", "Inflation", "Inflation Deutschland (vorläufig)", "% ggü. Vorjahr"),
    "de_unemployment": EventType("DE", "Arbeitsmarkt", "Arbeitslosenquote Deutschland", "%"),
    "de_gdp": EventType("DE", "Konjunktur", "BIP Deutschland (Schnellschätzung)", "% ggü. Vorquartal"),
    "de_zew": EventType("DE", "Konjunktur", "ZEW-Konjunkturerwartungen", "Punkte"),
}
# (Yahoo region, Yahoo event name) -> key of EVENT_TYPES; names as seen in Nov 2026.
YAHOO_EVENTS = {
    ("US", "FOMC Decision Fed Funds Tgt Rate"): "fed_rate",
    ("US", "CPI MM, SA"): "us_cpi",
    ("US", "PCE Price Index YY *"): "us_pce",
    ("US", "Non-Farm Payrolls"): "us_jobs",
    ("US", "Unemployment Rate"): "us_unemployment",
    ("US", "GDP Advance"): "us_gdp",
    ("US", "GDP 2nd Estimate"): "us_gdp2",
    ("US", "ISM Manufacturing PMI"): "us_ism_mfg",
    ("US", "ISM N-Mfg PMI"): "us_ism_srv",
    ("EU", "ECB Refinancing Rate *"): "ecb_rate",
    ("EU", "HICP Flash YY *"): "ez_hicp_flash",
    ("EU", "HICP Final YY *"): "ez_hicp_final",
    ("EU", "Unemployment Rate"): "ez_unemployment",
    ("EU", "GDP Flash Estimate QQ *"): "ez_gdp",
    ("DE", "CPI Prelim YY *"): "de_cpi",
    ("DE", "Unemployment Rate SA"): "de_unemployment",
    ("DE", "GDP Flash QQ SA*"): "de_gdp",
    ("DE", "ZEW Economic Sentiment"): "de_zew",
}


@dataclass(frozen=True)
class EconomicEvent:
    """One dated release; ``last`` and ``actual`` come from Yahoo only."""

    key: str
    when: datetime  # timezone-aware
    last: Decimal | None = None
    actual: Decimal | None = None
    period: str | None = None
    tentative: bool = False

    @property
    def kind(self) -> EventType:
        """Region, group, German name and unit of this release."""
        return EVENT_TYPES[self.key]


@cache
def load_fixed_events() -> tuple[EconomicEvent, ...]:
    """Read the official dates from ``economic_events.csv``.

    :return: Events in file order with local release time converted to aware datetimes.
    """
    with EVENTS_FILE.open(encoding="utf-8", newline="") as handle:
        return tuple(
            EconomicEvent(
                row["key"],
                datetime.combine(date.fromisoformat(row["date"]), time.fromisoformat(row["time_local"]),
                                 ZoneInfo(row["timezone"])),
                tentative=row["tentative"] == "1",
            )
            for row in csv.DictReader(handle)
        )


def _decimal(value) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _default_fetch(start: date, end: date, offset: int):
    """One page of Yahoo's economic calendar for US, EU and DE.

    The public ``get_economic_events_calendar`` cannot filter by country; without
    the filter one week holds about 600 rows from all countries instead of ~50.

    :param start: First day.
    :param end: Last day.
    :param offset: Row offset for paging.
    :return: DataFrame indexed by event name with Region, Event Time, For, Actual, Last.
    """
    import yfinance
    from yfinance.calendars import CalendarQuery

    query = CalendarQuery("and", [
        CalendarQuery("gte", ["startdatetime", start.isoformat()]),
        CalendarQuery("lte", ["startdatetime", end.isoformat()]),
        CalendarQuery("or", [CalendarQuery("eq", ["country_code", region]) for region in REGIONS]),
    ])
    return yfinance.Calendars()._get_data("economic_event", query, limit=PAGE_SIZE, offset=offset)


def load_yahoo_events(start: date, end: date, *, fetch: Callable | None = None) -> tuple[EconomicEvent, ...]:
    """Tracked releases from Yahoo between ``start`` and ``end``.

    :param start: First day.
    :param end: Last day.
    :param fetch: Page loader ``(start, end, offset) -> DataFrame``; replaced in tests.
    :return: Events of ``YAHOO_EVENTS``; empty on any provider failure.
    """
    fetch = fetch or _default_fetch
    events = []
    try:
        with _quiet_provider_logs():
            for page in range(MAX_PAGES):
                frame = fetch(start, end, page * PAGE_SIZE)
                if frame is None or frame.empty:
                    break
                for name, row in frame.iterrows():
                    key = YAHOO_EVENTS.get((row.get("Region"), str(name)))
                    stamp = row.get("Event Time")
                    if key is None or stamp is None or getattr(stamp, "tzinfo", None) is None:
                        continue
                    period = row.get("For")
                    events.append(EconomicEvent(
                        key, stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else stamp,
                        _decimal(row.get("Last")), _decimal(row.get("Actual")),
                        str(period) if isinstance(period, str) and period not in ("", "N/A") else None,
                    ))
                if len(frame) < PAGE_SIZE:
                    break
    except Exception:
        return ()
    return tuple(events)


def _day(event: EconomicEvent) -> tuple[str, date]:
    return event.key, event.when.astimezone(BERLIN).date()


def merge_events(fixed: Iterable[EconomicEvent], yahoo: Iterable[EconomicEvent]) -> tuple[EconomicEvent, ...]:
    """Combine both sources; one release per key and Berlin calendar day.

    The official time and the tentative flag win; values and period come from Yahoo.

    :param fixed: Events from the official list.
    :param yahoo: Events from Yahoo.
    :return: Events sorted by time.
    """
    merged = {_day(event): event for event in yahoo}
    for event in fixed:
        found = merged.get(_day(event))
        merged[_day(event)] = (
            replace(found, when=event.when, tentative=event.tentative) if found else event
        )
    return tuple(sorted(merged.values(), key=lambda event: (event.when, event.key)))


def upcoming(events: Iterable[EconomicEvent], now: datetime,
             days: int = LOOKAHEAD_DAYS) -> dict[str, list[EconomicEvent]]:
    """Releases from ``now`` on, per column ``USA`` and ``Europa``.

    The next Fed and ECB decision is always included, even beyond ``days``.

    :param events: Merged events.
    :param now: Current time, timezone-aware.
    :param days: Window length in days.
    :return: Column name -> events sorted by time.
    """
    future = sorted((event for event in events if event.when >= now), key=lambda event: event.when)
    chosen = [event for event in future if event.when <= now + timedelta(days=days)]
    for key in RATE_KEYS:
        nxt = next((event for event in future if event.key == key), None)
        if nxt is not None and nxt not in chosen:
            chosen.append(nxt)
    chosen.sort(key=lambda event: event.when)
    return {column: [event for event in chosen if event.kind.region in regions]
            for column, regions in COLUMNS.items()}


def last_released(events: Iterable[EconomicEvent], now: datetime,
                  days: int = LOOKBACK_DAYS) -> dict[str, list[EconomicEvent]]:
    """Releases of the past ``days`` with an actual value, newest first.

    :param events: Merged events.
    :param now: Current time, timezone-aware.
    :param days: Window length in days.
    :return: Column name -> events.
    """
    past = sorted((event for event in events
                   if now - timedelta(days=days) <= event.when < now and event.actual is not None),
                  key=lambda event: event.when, reverse=True)
    return {column: [event for event in past if event.kind.region in regions]
            for column, regions in COLUMNS.items()}


def list_ends(events: Iterable[EconomicEvent], now: datetime) -> date | None:
    """Last day of the official list if it ends within ``LIST_WARNING_DAYS``.

    :param events: Events from the official list.
    :param now: Current time, timezone-aware.
    :return: Last listed day, or None while the list reaches far enough.
    """
    last = max((event.when for event in events), default=None)
    if last is None or last - now < timedelta(days=LIST_WARNING_DAYS):
        return last.astimezone(BERLIN).date() if last else now.astimezone(BERLIN).date()
    return None


def load_events(today: date) -> tuple[tuple[EconomicEvent, ...], bool]:
    """Official list plus Yahoo for the start page.

    :param today: Current day; Yahoo is asked from ``LOOKBACK_DAYS`` before to ``LOOKAHEAD_DAYS`` after.
    :return: Merged events and whether Yahoo delivered anything.
    """
    yahoo = load_yahoo_events(today - timedelta(days=LOOKBACK_DAYS), today + timedelta(days=LOOKAHEAD_DAYS))
    return merge_events(load_fixed_events(), yahoo), bool(yahoo)
