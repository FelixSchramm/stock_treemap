"""Start page before login: Login top right, market clock, breadth, treemap, sparklines, tiles.

A click on a world-index tile opens its companies (fixed members, see
``index_members``); the URL parameter ``index`` keeps that view, and a back
button returns to the overview.

Public market data only: nothing here reads secrets, the Sheet or depot loaders.
Quotes are shared by all visitors for ``CACHE_SECONDS``, so the number of
provider requests does not grow with the number of visitors.
"""

import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from html import escape
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import streamlit as st

from portfolio_dashboard import (
    economic_calendar,
    index_members,
    indices_view,
    market_overview,
)
from portfolio_dashboard.design import DARK, DOWN, UP, dashboard_css, style_figure
from portfolio_dashboard.economic_calendar import BERLIN, EconomicEvent
from portfolio_dashboard.index_members import FixedIndexReader
from portfolio_dashboard.indices import (
    PROVIDER_FAILURE_NOTE,
    IndexBundle,
    IndexMarketData,
    IndexRow,
    IndicesLoader,
)
from portfolio_dashboard.market_overview import MarketQuote, breadth, market_clock
from portfolio_dashboard.treemap import _german, color_for, color_scale, format_percent
from portfolio_dashboard.watchlist_quotes import PERIODS, WatchlistEntryStatus

CACHE_SECONDS = 900
AREA = "Märkte"
# All visitors share one host IP, and the depot hits the same Yahoo rate limit:
# at most INDEX_WORKERS requests in parallel, one index load at a time.
INDEX_WORKERS = 6
_INDEX_LOADS = threading.BoundedSemaphore(1)
# A visitor waits at most this long for another index load before seeing the
# "not available" notice; nothing is cached for that rerun.
LOAD_WAIT_SECONDS = 60
# Share of rows without data above which a load counts as an outage.
UNAVAILABLE_SHARE = 0.1
# Closing prices settle a little after the bell before a closed market is cached
# until its next session.
SETTLE = timedelta(minutes=20)
ASSETS_GROUP = "Devisen & Rohstoffe"
GROUP_KEY = "start.group"
PERIOD_KEY = "start.period"
TREEMAP_KEY = "start.treemap"
INDEX_PARAM = "index"
_INDEX_BY_SYMBOL = {item.symbol: item.name for item in market_overview.WORLD_INDICES}
_INDEX_BY_NAME = {item.name: item for item in market_overview.WORLD_INDICES}
NOT_AVAILABLE_TEXT = "Marktdaten sind zurzeit nicht verfügbar. Später erneut versuchen."
_STATUS_COLORS = {"offen": UP, "Mittagspause": "#D9A441", "geschlossen": DARK["ink_muted"]}
START_CSS = f"""<style>
.start-clock {{background: {DARK["surface"]}; border: 1px solid {DARK["border"]};
  border-radius: 12px; padding: 10px 14px; margin-bottom: 1rem; font-variant-numeric: tabular-nums;}}
.start-clock b {{font-size: 15px;}}
.start-clock span.time {{color: {DARK["ink_secondary"]}; font-size: 13px;}}
.start-dot {{display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px;}}
.start-year {{font-size: 13px; margin: -0.6rem 0 0.8rem; font-variant-numeric: tabular-nums;}}
.start-event {{border-left: 3px solid {DARK["border"]}; padding: 2px 0 2px 10px; margin-bottom: 8px;
  font-variant-numeric: tabular-nums;}}
.start-event.rate {{border-left-color: #D9A441;}}
.start-event .when {{color: {DARK["ink_secondary"]}; font-size: 13px;}}
.start-event .meta {{color: {DARK["ink_muted"]}; font-size: 12px;}}
</style>"""
EVENTS_KEY = "start.events"
EVENTS_NOT_AVAILABLE = "Termine sind zurzeit nicht verfügbar."
_WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
TILE_HELP = ("Pfeil: Veränderung zum vorherigen Handelstag bzw. Tagesbalken bei Yahoo Finance. "
             "Zeile darunter und Kurve: Entwicklung über ein Jahr aus Tagesschlusskursen.")
# Closes must span nearly a year before a one-year change is derived from them.
YEAR_MIN_SPAN_DAYS = 350


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def cached_quotes(group: str) -> tuple[datetime, tuple[MarketQuote, ...]]:
    """Shared for all visitors; ``group`` is a ``GROUPS`` key or ``ASSETS_GROUP``."""
    now = datetime.now(UTC)
    symbols = market_overview.ASSETS if group == ASSETS_GROUP else market_overview.GROUPS[group]
    return now, market_overview.load_quotes(symbols, now)


@st.cache_resource
def index_market() -> IndexMarketData:
    """One provider for all visitors: identity and market caps cached per UTC day."""
    return IndexMarketData(cache_info=True)


class IndexUnsettled(Exception):
    """A load with missing prices: never kept until the next session."""


def has_prices(row: IndexRow) -> bool:
    """:return: True if Yahoo delivered at least one period return for the row."""
    return any(row.percent(period) is not None for period in PERIODS)


def degraded(bundle: IndexBundle) -> bool:
    """Outage or throttling: no prices, a provider error, or many rows without data.

    Rows with a cached identity but failed history carry an UNAVAILABLE quote
    instead of a note. A single suspended or delisted member does the same for
    good, so UNAVAILABLE counts only above ``UNAVAILABLE_SHARE`` of the rows.
    """
    rows = bundle.rows
    if not bundle.available or not any(has_prices(row) for row in rows):
        return True
    if any(row.note == PROVIDER_FAILURE_NOTE for row in rows):
        return True
    missing = sum(
        row.quote is not None and row.quote.status is WatchlistEntryStatus.UNAVAILABLE
        for row in rows
    )
    return missing > UNAVAILABLE_SHARE * len(rows)


@st.cache_data(ttl=CACHE_SECONDS, max_entries=60, show_spinner=False)
def _recent_index(name: str, running_slot: str) -> IndexBundle:
    """One Yahoo load per index and 15-minute slot, healthy or not."""
    if not _INDEX_LOADS.acquire(timeout=LOAD_WAIT_SECONDS):
        # Not cached: the next rerun tries again instead of keeping the failure.
        raise TimeoutError("index load queue busy")
    try:
        return IndicesLoader(FixedIndexReader(), index_market(), workers=INDEX_WORKERS).load(name)
    finally:
        _INDEX_LOADS.release()


@st.cache_data(ttl=7 * 24 * 3600, max_entries=60, show_spinner=False)
def _settled_index(name: str, slot: str, _running_slot: str) -> IndexBundle:
    """Kept for the whole closed period, but only a complete load.

    ``_running_slot`` is not part of the cache key (leading underscore). A
    degraded load raises, Streamlit does not cache exceptions, and the caller
    falls back to the 15-minute entry, so an outage is retried soon.
    """
    bundle = _recent_index(name, _running_slot)
    if degraded(bundle):
        raise IndexUnsettled(name)
    return bundle


def cached_index(name: str, now: datetime) -> IndexBundle:
    """Shared for all visitors: fixed members with Yahoo prices and market caps.

    While the index's exchange trades, a new load per 15-minute slot; while it
    rests, one complete load until the next session (see ``index_slot``).
    """
    running = running_slot(now)
    slot = index_slot(name, now, cached_calendars(now.date()))
    if slot == running:
        return _recent_index(name, running)
    # Own key for resting loads: a load from before the settle time ends (same
    # 15 minutes) must not become the resting state.
    resting = f"{running} {slot}"
    try:
        return _settled_index(name, slot, resting)
    except IndexUnsettled:
        return _recent_index(name, resting)


def running_slot(now: datetime) -> str:
    """:return: UTC 15-minute slot, e.g. ``"2026-11-13 14:15"``."""
    utc = now.astimezone(UTC)
    return f"{utc:%Y-%m-%d %H}:{utc.minute // 15 * 15:02d}"


def index_slot(name: str, now: datetime, calendars: dict) -> str:
    """Cache slot of an index: 15 minutes during trading, else until the next session.

    :param name: world-index name with fixed members.
    :param now: current time.
    :param calendars: ``market_overview.market_calendars`` of the day.
    :return: ``running_slot`` while the session runs (plus ``SETTLE``);
        ``"pause YYYY-MM-DD"`` during a midday break; ``"ruht nach YYYY-MM-DD"``
        (last finished session) otherwise.
    """
    item = _INDEX_BY_NAME.get(name)
    calendar = calendars.get(item.calendar) if item is not None and item.calendar else None
    running = running_slot(now)
    if calendar is None:
        return running  # unknown trading hours: keep the short cache
    local = now.astimezone(ZoneInfo(item.timezone))
    today = local.date()
    if not calendar.first_day <= today <= calendar.last_day:
        return running
    if today in calendar.sessions:
        opens = datetime.combine(today, calendar.open_time, local.tzinfo)
        settled = datetime.combine(today, calendar.close_time, local.tzinfo) + SETTLE
        if opens <= local < settled:
            # Tokyo and Hong Kong pause at midday; prices stand still meanwhile.
            return f"pause {today:%Y-%m-%d}" if calendar.in_break(local.time()) else running
        if local >= settled:
            return f"ruht nach {today:%Y-%m-%d}"
    # Before the open or on a closed day: rests since the last finished session.
    # Euro Stoxx follows the German calendar; Paris/Madrid half days (24./31.12.)
    # are not treated as sessions.
    earlier = [day for day in calendar.sessions if day < today]
    return f"ruht nach {max(earlier):%Y-%m-%d}" if earlier else running


@st.cache_data(ttl=3600, show_spinner=False)
def cached_calendars(day: date):
    return market_overview.market_calendars(day)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_events(day: date) -> tuple[tuple[EconomicEvent, ...], bool]:
    """Shared for all visitors: official dates plus Yahoo values, one hour."""
    return economic_calendar.load_events(day)


def _number(value: Decimal | None, places: int = 2) -> str:
    return "NA" if value is None else _german(value, places)


def build_market_figure(quotes: tuple[MarketQuote, ...], period: str) -> go.Figure:
    """Equal tiles grouped by region or sector family; color = period return."""
    groups = list(dict.fromkeys(q.item.group for q in quotes))
    grouped = len(groups) > 1
    scale, _ = color_scale([q.percent(period) for q in quotes])
    ids = [*(groups if grouped else []), *(q.item.symbol for q in quotes)]
    figure = go.Figure(go.Treemap(
        ids=ids,
        labels=[*(groups if grouped else []), *(escape(q.item.name) for q in quotes)],
        parents=[*([""] * len(groups) if grouped else []),
                 *((q.item.group if grouped else "") for q in quotes)],
        values=[*([0] * len(groups) if grouped else []), *([1] * len(quotes))],
        customdata=[*([["", ""]] * len(groups) if grouped else []),
                    *([format_percent(q.percent(period)), escape(q.item.symbol)] for q in quotes)],
        marker=dict(
            colors=[*([DARK["surface"]] * len(groups) if grouped else []),
                    *(color_for(q.percent(period), scale) for q in quotes)],
            line=dict(width=2, color=DARK["page"]),
        ),
        texttemplate="<b>%{label}</b><br>%{customdata[0]}",
        hovertemplate=f"<b>%{{label}}</b> · %{{customdata[1]}}<br>{period}: %{{customdata[0]}}<extra></extra>",
        textfont=dict(color="white"),
        # A click on a region zooms in; the path bar is the way back out.
        pathbar=dict(visible=grouped),
        tiling=dict(pad=3),
    ))
    style_figure(figure)
    figure.update_layout(height=320, margin=dict(l=0, r=0, t=6, b=0))
    return figure


def _sparkline(quote: MarketQuote) -> list[float] | None:
    return [float(close) for _, close in quote.closes] if len(quote.closes) > 1 else None


def _render_clock(now: datetime) -> None:
    clocks = market_clock(now, cached_calendars(now.date()))
    for column, clock in zip(st.columns(len(clocks)), clocks, strict=True):
        color = _STATUS_COLORS.get(clock.status, DARK["ink_muted"])
        column.markdown(
            f'<div class="start-clock"><span class="start-dot" style="background:{color}"></span>'
            f"<b>{escape(clock.label)}</b><br><span class=\"time\">{clock.local_time:%H:%M} · "
            f"{escape(clock.status)}</span></div>",
            unsafe_allow_html=True,
        )


def _event_value(event: EconomicEvent, value: Decimal) -> str:
    unit = event.kind.unit
    return f"{_number(value, 0 if unit.startswith('Tsd') else 2)} {unit}"


def _event_html(event: EconomicEvent, now: datetime, *, released: bool = False) -> str:
    """One release as HTML: Berlin time, distance, name, group and value.

    :param event: Release to show.
    :param now: Current time, timezone-aware.
    :param released: True shows the actual value, False the prior value.
    :return: Escaped HTML block.
    """
    local = event.when.astimezone(BERLIN)
    days = (local.date() - now.astimezone(BERLIN).date()).days
    distance = {0: "heute", 1: "morgen", -1: "gestern"}.get(
        days, f"in {days} Tagen" if days > 0 else f"vor {-days} Tagen")
    meta = [event.kind.group]
    if event.period:
        meta.append(f"Zeitraum {event.period}")
    if released and event.actual is not None:
        meta.append(f"Ergebnis {_event_value(event, event.actual)}")
    if event.last is not None:
        meta.append(f"Vorwert {_event_value(event, event.last)}")
    if event.tentative:
        meta.append("Termin vorläufig")
    css = "start-event rate" if event.key in economic_calendar.RATE_KEYS else "start-event"
    return (f'<div class="{css}"><span class="when">{_WEEKDAYS[local.weekday()]} {local:%d.%m., %H:%M}'
            f" · {escape(distance)}</span><br><b>{escape(event.kind.name)}</b><br>"
            f'<span class="meta">{escape(" · ".join(meta))}</span></div>')


def _render_events(now: datetime) -> None:
    """Next releases for the USA and Europe below the market clock."""
    try:
        events, yahoo_ok = cached_events(now.date())
    except Exception:
        st.info(EVENTS_NOT_AVAILABLE)
        return
    st.subheader("Nächste Termine USA & Europa", anchor=False)
    coming = economic_calendar.upcoming(events, now)
    released = economic_calendar.last_released(events, now)
    for column, name in zip(st.columns(len(coming)), coming, strict=True):
        with column:
            st.markdown(f"**{name}**")
            blocks = [_event_html(event, now) for event in coming[name]]
            st.markdown("".join(blocks) or "Keine Termine in den nächsten vier Wochen.",
                        unsafe_allow_html=True)
            if released[name]:
                with st.expander("Zuletzt veröffentlicht"):
                    st.markdown("".join(_event_html(event, now, released=True) for event in released[name]),
                                unsafe_allow_html=True)
    notes = [f"Termine in Berliner Zeit · Zinsentscheide und US-Daten: Fed, EZB, BLS, BEA "
             f"(Stand {economic_calendar.SOURCE_DATE}) · weitere Termine und Vorwerte: Yahoo Finance, "
             "etwa vier Wochen im Voraus, ohne Prognosen."]
    if not yahoo_ok:
        notes.append("Yahoo-Termine und Vorwerte sind zurzeit nicht verfügbar; es fehlen Termine außerhalb der Liste.")
    ends = economic_calendar.list_ends(economic_calendar.load_fixed_events(), now)
    if ends is not None:
        notes.append(f"Terminliste nur bis {ends:%d.%m.%Y}.")
    st.caption(" ".join(notes))


def _render_breadth(quotes: tuple[MarketQuote, ...], period: str, group: str) -> None:
    result = breadth(quotes, period)
    first, second, third = st.columns(3)
    first.metric("Im Plus", f"{result.up} von {result.total}",
                 help=f"{group}, {period}: Werte mit positiver Entwicklung; NA zählt nicht.")
    first.caption(f"{result.down} im Minus · {result.total - result.quoted} ohne Kurs")
    for column, label, quote in ((second, "Stärkster Wert", result.best),
                                 (third, "Schwächster Wert", result.worst)):
        column.metric(label, format_percent(quote.percent(period)) if quote else "NA")
        column.caption(quote.item.name if quote else "Keine Kursentwicklung verfügbar")


def _year_percent(quote: MarketQuote) -> Decimal | None:
    """Return the one-year change in percent.

    Indices carry it from the price engine. Currencies, commodities and crypto
    only carry the daily change, so it is taken from their daily closes instead.

    :param quote: Market quote with optional ``Jahr`` percent and daily closes.
    :return: Percent change over one year, or None if the closes span less than that.
    """
    year = quote.percent("Jahr")
    if year is not None or len(quote.closes) < 2:
        return year
    (first_day, _), (last_day, last) = quote.closes[0], quote.closes[-1]
    # A few recent bars must not pass for a one-year change.
    if last_day - first_day < timedelta(days=YEAR_MIN_SPAN_DAYS):
        return None
    start = next(close for day, close in quote.closes if day >= last_day - timedelta(days=365))
    return (last - start) / start * 100 if start else None


def _render_trend_tile(column, label: str, value: str, quote: MarketQuote | None,
                       chart_type: str) -> None:
    """Render one tile: value, daily change as delta, one-year line and curve.

    :param column: Streamlit column that holds the tile.
    :param label: Tile title.
    :param value: Formatted current value, ``NA`` if missing.
    :param quote: Market quote, or None if the symbol is missing.
    :param chart_type: Streamlit metric chart type, ``area`` or ``line``.
    """
    day = quote.percent("Heute") if quote else None
    year = _year_percent(quote) if quote else None
    column.metric(label, value, format_percent(day) if day is not None else None,
                  chart_data=_sparkline(quote) if quote else None, chart_type=chart_type,
                  help=TILE_HELP)
    color = DARK["ink_muted"] if year is None else (UP if year >= 0 else DOWN)
    column.markdown(f'<div class="start-year" style="color:{color}">1 Jahr: '
                    f"{escape(format_percent(year))}</div>", unsafe_allow_html=True)


def _render_sparklines(world: tuple[MarketQuote, ...]) -> None:
    quotes = {q.item.name: q for q in world}
    for column, name in zip(st.columns(len(market_overview.SPARKLINES)), market_overview.SPARKLINES,
                            strict=True):
        quote = quotes.get(name)
        value = _number(quote.price) + " Pkt." if quote and quote.price is not None else "NA"
        _render_trend_tile(column, name, value, quote, "area")


def _render_assets(assets: tuple[MarketQuote, ...]) -> None:
    places = {"EURUSD=X": 4, "BTC-EUR": 0}
    for column, quote in zip(st.columns(len(assets)), assets, strict=True):
        unit = "" if quote.item.symbol == "EURUSD=X" else f" {quote.item.currency}"
        value = _number(quote.price, places.get(quote.item.symbol, 2))
        _render_trend_tile(column, quote.item.name, value + (unit if quote.price is not None else ""),
                           quote, "line")


def _open_index() -> None:
    """Treemap click: open a world index with fixed members, else say why not."""
    points = st.session_state[TREEMAP_KEY].selection.points
    name = _INDEX_BY_SYMBOL.get(points[0].get("id")) if points else None
    if name in index_members.members():
        st.query_params[INDEX_PARAM] = name
    elif name is not None:
        st.toast(f"Für {name} sind keine Einzelwerte hinterlegt.")


def _close_index() -> None:
    st.query_params.pop(INDEX_PARAM, None)


def _render_index(name: str, now: datetime) -> None:
    """Companies of one world index: breadcrumb, back button, KPIs, treemap, table."""
    back, path = st.columns((1, 3), vertical_alignment="center")
    back.button("← Zurück zur Übersicht", key="start.back", on_click=_close_index)
    path.caption(f"Märkte im Überblick › Weltindizes › {name}")
    period = st.segmented_control("Zeitraum", PERIODS, default="Heute", key=PERIOD_KEY) or "Heute"
    try:
        with st.spinner(f"{name}: Unternehmen, Kurse und Marktkapitalisierungen werden geladen …"):
            bundle = cached_index(name, now)
    except Exception:
        st.info(NOT_AVAILABLE_TEXT)
        return
    st.subheader(f"{name} · {period}", anchor=False)
    # A Yahoo outage yields rows that are all NA: say so instead of an empty table.
    if not bundle.available or not any(has_prices(row) for row in bundle.rows):
        st.info(NOT_AVAILABLE_TEXT)
        return
    st.caption(f"Die bis zu {index_members.TOP_N} größten Mitglieder nach Marktkapitalisierung "
               f"(Wikipedia und Yahoo, Stand {index_members.SOURCE_DATE}) · "
               "Kurse und Marktkapitalisierung: Yahoo Finance · "
               f"Stand: {bundle.fetched_at:%d.%m.%Y, %H:%M} UTC; während der Handelszeit bis zu "
               "15 Minuten, sonst bis zur nächsten Sitzung zwischengespeichert.")
    indices_view.render_bundle(bundle, period)


def render(login_button: Callable[[], None], *, load: Callable | None = None,
           clock: Callable[[], datetime] | None = None) -> None:
    """Render the public start page; ``login_button`` comes from the owner gate."""
    st.markdown(dashboard_css(), unsafe_allow_html=True)
    heading, action = st.columns((5, 1), vertical_alignment="center")
    with heading:
        st.header("Märkte im Überblick", anchor=False)
        st.caption("Weltindizes, Branchen, Devisen und Rohstoffe · Kurse: Yahoo Finance, teils verzögert")
    with action:
        login_button()
    st.divider()
    render_markets(load=load, clock=clock)


def render_markets(*, load: Callable | None = None, clock: Callable[[], datetime] | None = None) -> None:
    """Market clock, breadth, treemap, curves and tiles: start page and «Märkte» area."""
    load = load or cached_quotes
    now = (clock or (lambda: datetime.now(UTC)))()
    st.markdown(START_CSS, unsafe_allow_html=True)
    _render_clock(now)
    selected = st.query_params.get(INDEX_PARAM)
    if selected in index_members.members():
        _render_index(selected, now)
        return
    _render_events(now)

    controls = st.columns((3, 2))
    with controls[0]:
        group = st.segmented_control("Ansicht", tuple(market_overview.GROUPS), default="Weltindizes",
                                     key=GROUP_KEY) or "Weltindizes"
    with controls[1]:
        period = st.segmented_control("Zeitraum", PERIODS, default="Heute", key=PERIOD_KEY) or "Heute"
    try:
        with st.spinner("Marktdaten werden geladen …"):
            fetched_at, world = load("Weltindizes")
            quotes = world if group == "Weltindizes" else load(group)[1]
            assets = load(ASSETS_GROUP)[1]
    except Exception:
        st.info(NOT_AVAILABLE_TEXT)
        return
    if not quotes:
        st.info(NOT_AVAILABLE_TEXT)
        return
    _render_breadth(quotes, period, group)
    st.plotly_chart(build_market_figure(quotes, period), width="stretch", key=TREEMAP_KEY,
                    config={"displayModeBar": False}, on_select=_open_index, selection_mode="points")
    st.caption("Grün: Gewinn · Rot: Verlust · Grau: NA · gleiche Kachelgrößen, keine Gewichtung. "
               + ("Branchen über Sektor-ETFs (Select Sector SPDR bzw. iShares STOXX Europe 600) als Näherung."
                  if group != "Weltindizes" else "Indexstände in Punkten, ohne Währungsumrechnung. "
                  f"Klick auf {', '.join(index_members.members())} zeigt die einzelnen Unternehmen."))
    st.divider()
    _render_sparklines(world)
    if assets:
        _render_assets(assets)
    st.caption(f"Stand: {fetched_at:%d.%m.%Y, %H:%M} UTC, für alle Besucher bis zu 15 Minuten zwischengespeichert. "
               "Keine Anlageberatung.")
