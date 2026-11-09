"""Start page before login: Login top right, market clock, breadth, treemap, sparklines, tiles.

Public market data only: nothing here reads secrets, the Sheet or depot loaders.
Quotes are shared by all visitors for ``CACHE_SECONDS``, so the number of
provider requests does not grow with the number of visitors.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from html import escape

import plotly.graph_objects as go
import streamlit as st

from portfolio_dashboard import market_overview
from portfolio_dashboard.design import DARK, UP, dashboard_css, style_figure
from portfolio_dashboard.market_overview import MarketQuote, breadth, market_clock
from portfolio_dashboard.treemap import _german, color_for, color_scale, format_percent
from portfolio_dashboard.watchlist_quotes import PERIODS

CACHE_SECONDS = 900
ASSETS_GROUP = "Devisen & Rohstoffe"
GROUP_KEY = "start.group"
PERIOD_KEY = "start.period"
NOT_AVAILABLE_TEXT = "Marktdaten sind zurzeit nicht verfügbar. Später erneut versuchen."
_STATUS_COLORS = {"offen": UP, "Mittagspause": "#D9A441", "geschlossen": DARK["ink_muted"]}
START_CSS = f"""<style>
.start-clock {{background: {DARK["surface"]}; border: 1px solid {DARK["border"]};
  border-radius: 12px; padding: 10px 14px; margin-bottom: 1rem; font-variant-numeric: tabular-nums;}}
.start-clock b {{font-size: 15px;}}
.start-clock span.time {{color: {DARK["ink_secondary"]}; font-size: 13px;}}
.start-dot {{display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px;}}
</style>"""


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def cached_quotes(group: str) -> tuple[datetime, tuple[MarketQuote, ...]]:
    """Shared for all visitors; ``group`` is a ``GROUPS`` key or ``ASSETS_GROUP``."""
    now = datetime.now(UTC)
    symbols = market_overview.ASSETS if group == ASSETS_GROUP else market_overview.GROUPS[group]
    return now, market_overview.load_quotes(symbols, now)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_calendars(day: date):
    return market_overview.market_calendars(day)


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
        pathbar=dict(visible=False),
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


def _render_sparklines(world: tuple[MarketQuote, ...]) -> None:
    quotes = {q.item.name: q for q in world}
    for column, name in zip(st.columns(len(market_overview.SPARKLINES)), market_overview.SPARKLINES,
                            strict=True):
        quote = quotes.get(name)
        year = quote.percent("Jahr") if quote else None
        column.metric(
            f"{name} · 1 Jahr",
            _number(quote.price) + " Pkt." if quote and quote.price is not None else "NA",
            format_percent(year) if year is not None else None,
            chart_data=_sparkline(quote) if quote else None, chart_type="area",
            help="Aktueller Stand und Entwicklung über ein Jahr; Kurve aus Tagesschlusskursen.",
        )


def _render_assets(assets: tuple[MarketQuote, ...]) -> None:
    places = {"EURUSD=X": 4, "BTC-EUR": 0}
    for column, quote in zip(st.columns(len(assets)), assets, strict=True):
        unit = "" if quote.item.symbol == "EURUSD=X" else f" {quote.item.currency}"
        value = _number(quote.price, places.get(quote.item.symbol, 2))
        change = quote.percent("Heute")
        column.metric(quote.item.name, value + (unit if quote.price is not None else ""),
                      format_percent(change) if change is not None else None,
                      chart_data=_sparkline(quote), chart_type="line",
                      help="Veränderung zum vorherigen Tagesbalken bei Yahoo Finance; Kurve: ein Jahr.")


def render(login_button: Callable[[], None], *, load: Callable | None = None,
           clock: Callable[[], datetime] | None = None) -> None:
    """Render the public start page; ``login_button`` comes from the owner gate."""
    load = load or cached_quotes
    now = (clock or (lambda: datetime.now(UTC)))()
    st.markdown(dashboard_css() + START_CSS, unsafe_allow_html=True)
    heading, action = st.columns((5, 1), vertical_alignment="center")
    with heading:
        st.header("Märkte im Überblick", anchor=False)
        st.caption("Weltindizes, Branchen, Devisen und Rohstoffe · Kurse: Yahoo Finance, teils verzögert")
    with action:
        login_button()
    st.divider()
    _render_clock(now)

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
    st.plotly_chart(build_market_figure(quotes, period), width="stretch", key="start.treemap",
                    config={"displayModeBar": False})
    st.caption("Grün: Gewinn · Rot: Verlust · Grau: NA · gleiche Kachelgrößen, keine Gewichtung. "
               + ("Branchen über Sektor-ETFs (Select Sector SPDR bzw. iShares STOXX Europe 600) als Näherung."
                  if group != "Weltindizes" else "Indexstände in Punkten, ohne Währungsumrechnung."))
    st.divider()
    _render_sparklines(world)
    if assets:
        _render_assets(assets)
    st.caption(f"Stand: {fetched_at:%d.%m.%Y, %H:%M} UTC, für alle Besucher bis zu 15 Minuten zwischengespeichert. "
               "Keine Anlageberatung.")
