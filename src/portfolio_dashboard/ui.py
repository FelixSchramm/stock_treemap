"""Small native Streamlit helpers for the German dashboard shell."""

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from html import escape

import plotly.graph_objects as go
import streamlit as st

from portfolio_dashboard import valuation_view, watchlist_view
from portfolio_dashboard.fundamentals_loader import (
    LOADER_STATE_KEY as FUNDAMENTALS_LOADER_KEY,
)
from portfolio_dashboard.models import PortfolioSnapshot, SnapshotStatus
from portfolio_dashboard.refresh import (
    DATA_UNAVAILABLE_TEXT,
    DashboardBundle,
    PortfolioRefreshCoordinator,
    format_snapshot_age,
)
from portfolio_dashboard.returns import PortfolioReturns, portfolio_returns
from portfolio_dashboard.sheets_adapter import SheetsConfigurationError
from portfolio_dashboard.sheets_session import SheetsCredentialError
from portfolio_dashboard.treemap import (
    ALL_FILTER,
    MISSING_COLOR,
    NOT_AVAILABLE,
    SORT_BY_VALUE,
    SORT_FIELDS,
    TILE_TEXT_COLOR,
    WEIGHT_COLUMN_LABEL,
    FilterCriteria,
    Performance,
    PortfolioKpis,
    PositionTableRow,
    TreemapModel,
    available_categories,
    build_kpis,
    build_position_details,
    build_status_notes,
    build_table_rows,
    build_treemap,
    color_for,
    filter_groups,
    format_eur,
    format_percent,
    sort_table_rows,
)
from portfolio_dashboard.valuation_overview import OverviewSource, collect_subjects
from portfolio_dashboard.valuations import PortfolioValuation
from portfolio_dashboard.watchlist_quotes import WatchlistBundle, WatchlistEntry
from portfolio_dashboard.watchlist_session import WatchlistLoader, session_loader

COURSE_VIEW = "Kursentwicklung"
PERSONAL_VIEW = "Meine Entwicklung"
COURSE_PERIODS = ("Heute", "Woche", "Monat", "Jahr")
SINCE_PURCHASE = "Seit Kauf"
TYPE_FILTER_OPTIONS = (ALL_FILTER, "Aktie", "ETF", "Derivat")
SORT_DIRECTION_DESC = "Absteigend"
SORT_DIRECTION_ASC = "Aufsteigend"
SORT_DIRECTION_OPTIONS = (SORT_DIRECTION_DESC, SORT_DIRECTION_ASC)

# Public keys are the retained session state. COURSE_PERIOD_KEY holds the
# selected Zeitraum (a course period or "Seit Kauf"); the radio keeps its own
# widget key and is re-seeded from it on every run.
COURSE_PERIOD_KEY = "dashboard.course_period"
_PERIOD_WIDGET_KEY = "_dashboard.course_period"
PERIOD_RADIO_KEY = _PERIOD_WIDGET_KEY  # For AppTest lookups; the radio is no longer first.
HIDE_MISSING_COURSE_KEY = "dashboard.hide_missing_course"
_HIDE_MISSING_WIDGET_KEY = "_dashboard.hide_missing_course"
FILTER_QUERY_KEY = "dashboard.filter_query"
FILTER_TYPE_KEY = "dashboard.filter_type"
FILTER_CATEGORY_KEY = "dashboard.filter_category"
SORT_FIELD_KEY = "dashboard.sort_field"
SORT_ORDER_KEY = "dashboard.sort_order"
REFRESH_BUTTON_KEY = "dashboard.refresh"
BUNDLE_STATE_KEY = "dashboard.bundle"

# Top-level Ansicht. "Kursentwicklung" and "Meine Entwicklung" stay derived from the
# Zeitraum inside the Depot; the Watchlist is a second area with its own state.
DEPOT_AREA = "Depot"
WATCHLIST_AREA = "Watchlist"
AREAS = (DEPOT_AREA, WATCHLIST_AREA)
VALUATION_AREA = valuation_view.VALUATION_AREA
AREA_KEY = "dashboard.area"
_AREA_WIDGET_KEY = "_dashboard.area"
AREA_RADIO_KEY = _AREA_WIDGET_KEY
WATCHLIST_BUNDLE_KEY = "dashboard.watchlist_bundle"
WATCHLIST_FILTER_QUERY_KEY = "watchlist.filter_query"
WATCHLIST_FILTER_TYPE_KEY = "watchlist.filter_type"
WATCHLIST_FILTER_CATEGORY_KEY = "watchlist.filter_category"
WATCHLIST_SORT_FIELD_KEY = "watchlist.sort_field"
WATCHLIST_SORT_ORDER_KEY = "watchlist.sort_order"
# Streamlit drops the state of widgets that are not rendered in a run; the area
# that is hidden keeps its filter and sort choices by re-assigning them.
_DEPOT_WIDGET_KEYS = (
    FILTER_QUERY_KEY,
    FILTER_TYPE_KEY,
    FILTER_CATEGORY_KEY,
    SORT_FIELD_KEY,
    SORT_ORDER_KEY,
)
_WATCHLIST_WIDGET_KEYS = (
    WATCHLIST_FILTER_QUERY_KEY,
    WATCHLIST_FILTER_TYPE_KEY,
    WATCHLIST_FILTER_CATEGORY_KEY,
    WATCHLIST_SORT_FIELD_KEY,
    WATCHLIST_SORT_ORDER_KEY,
)

HEATMAP_MIN_TEXT_SIZE = 10  # Pixels; smaller tile text is hidden, not shrunk.
HEATMAP_MAX_HEIGHT = 1400  # Pixels; more tiles get smaller tiles, not a longer page.
WATCHLIST_NOT_CONFIGURED_TEXT = (
    "Die Watchlist ist nicht eingerichtet: PORTFOLIO_WATCHLIST_RANGE ist nicht gesetzt. "
    "Trage den Bereich des Watchlist-Blatts ein, z. B. im Format Blattname!A1:J300 "
    "(Grundspalten A–D, optionale Zusatzspalten bis T; siehe "
    "docs/DATA_SOURCE.md, Abschnitt „Watchlist-Blatt“). Das Depot ist davon nicht betroffen."
)
WATCHLIST_SINCE_PURCHASE_TEXT = (
    "„Seit Kauf“ gibt es in der Watchlist nicht (keine Einstände): angezeigt wird „Heute“. "
    "Die Auswahl im Depot bleibt erhalten, bis hier ein Zeitraum gewählt wird."
)
WATCHLIST_SPINNER_TEXT = (
    "Watchlist-Kurse werden abgerufen … Der erste Abruf kann bei vielen Einträgen "
    "mehrere Minuten dauern."
)


def _today() -> date:
    """Local calendar day; replaced in tests."""
    return date.today()

MOBILE_ACCESSIBILITY_CSS = """
<style>
/* Touch targets >= 44px (2.75rem) for iPhone/mobile usability */
div.stButton > button,
div[role="radiogroup"] label,
div[data-baseweb="select"] > div,
div.stTextInput input,
details > summary {
    min-height: 2.75rem;
}
div[role="radiogroup"] {
    flex-wrap: wrap;
    row-gap: 0.4rem;
}
/* Keyboard focus ring visibility */
div.stButton > button:focus-visible,
div[role="radiogroup"] input:focus-visible + div,
div.stTextInput input:focus-visible,
details > summary:focus-visible {
    outline: 2px solid #8ab4f8 !important;
    outline-offset: 2px !important;
}
/* Responsive wrapping on narrow iPhone viewports */
@media (max-width: 640px) {
    div[data-testid="stMetricValue"] {
        font-size: 1.25rem !important;
        word-break: break-word;
    }
    div[data-testid="stHorizontalBlock"] {
        flex-wrap: wrap;
    }
}
</style>
"""


def render_accessibility_styles() -> None:
    """Inject mobile touch-target, wrapping and keyboard-focus styles."""
    st.markdown(MOBILE_ACCESSIBILITY_CSS, unsafe_allow_html=True)


def _remember_course_period() -> None:
    """Persist the selected Zeitraum in the public session key."""
    st.session_state[COURSE_PERIOD_KEY] = st.session_state[_PERIOD_WIDGET_KEY]


def _remember_area() -> None:
    """Persist the selected Ansicht in the public session key."""
    st.session_state[AREA_KEY] = st.session_state[_AREA_WIDGET_KEY]


def _keep_state(keys: Sequence[str]) -> None:
    """Retain widget values of the area that is not rendered in this run."""
    for key in keys:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def render_area_control(areas: Sequence[str] = AREAS) -> str:
    """Render the Ansicht radio and keep the choice in the session.

    :param areas: Offered areas; ``VALUATION_AREA`` joins Depot and Watchlist when
        the valuation overview is wired.
    :return: The selected area.
    """
    if st.session_state.get(AREA_KEY) not in areas:
        st.session_state[AREA_KEY] = DEPOT_AREA
    st.session_state[_AREA_WIDGET_KEY] = st.session_state[AREA_KEY]
    return st.radio(
        "Ansicht",
        tuple(areas),
        key=_AREA_WIDGET_KEY,
        on_change=_remember_area,
        horizontal=True,
    )


def render_view_controls(*, watchlist: bool = False) -> tuple[str, str]:
    """Render the Zeitraum radio and derive the view from the selection.

    The remembered Zeitraum is shared by Depot and Watchlist. The Watchlist offers
    only the four course periods: a remembered ``"Seit Kauf"`` is shown as ``"Heute"``
    there without overwriting the Depot choice (it changes only if the user picks a
    period in the Watchlist).

    :param watchlist: Offer only the four course periods (Watchlist area).
    :return: Tuple of (derived view, selected course period or ``"Seit Kauf"``).
    """
    options = COURSE_PERIODS if watchlist else (*COURSE_PERIODS, SINCE_PURCHASE)
    st.session_state.setdefault(COURSE_PERIOD_KEY, COURSE_PERIODS[0])
    remembered = st.session_state[COURSE_PERIOD_KEY]
    st.session_state[_PERIOD_WIDGET_KEY] = (
        remembered if remembered in options else COURSE_PERIODS[0]
    )
    period = st.radio(
        "Zeitraum",
        options,
        key=_PERIOD_WIDGET_KEY,
        on_change=_remember_course_period,
        horizontal=True,
    )
    view = PERSONAL_VIEW if period == SINCE_PURCHASE else COURSE_VIEW
    return view, period


def _remember_missing_course_filter() -> None:
    """Retain the course-only toggle while the since-purchase view is active."""
    st.session_state[HIDE_MISSING_COURSE_KEY] = st.session_state[_HIDE_MISSING_WIDGET_KEY]


def render_kpis(
    kpis: PortfolioKpis,
    *,
    is_personal_view: bool = False,
    missing_course_count: int = 0,
    missing_course_eur: Decimal = Decimal(0),
) -> bool:
    """Render portfolio-wide header KPIs from the unfiltered valuation snapshot.

    :param kpis: Calculated PortfolioKpis object.
    :param is_personal_view: Whether to include the personal cost-basis comparison.
    :param missing_course_count: Number of valued tiles without the selected course return.
    :param missing_course_eur: Full EUR value of those tiles, before search/type filters.
    :return: Whether to hide tiles without the selected course return.
    """
    hide_missing = False
    cols = st.columns(4)
    with cols[0]:
        st.metric(
            "Bewerteter Depotwert",
            kpis.total_eur_label,
            delta=kpis.total_status_note,
            delta_color="off",
        )
        if not is_personal_view:
            st.session_state.setdefault(HIDE_MISSING_COURSE_KEY, True)
            st.session_state[_HIDE_MISSING_WIDGET_KEY] = st.session_state[HIDE_MISSING_COURSE_KEY]
            hide_missing = st.toggle(
                "Positionen ohne Kursentwicklung ausblenden",
                key=_HIDE_MISSING_WIDGET_KEY,
                on_change=_remember_missing_course_filter,
            )
            if hide_missing:
                st.caption(
                    f"{missing_course_count} Positionen ({format_eur(missing_course_eur)}) "
                    "ohne Kursentwicklung ausgeblendet"
                )
    with cols[1]:
        st.metric(
            "Aktive Positionen",
            kpis.active_positions_label,
            delta=kpis.coverage_label,
            delta_color="off",
        )
    with cols[2]:
        st.metric(
            "Größter Gewinner",
            kpis.winner_label,
            delta=kpis.winner_note,
            delta_color="off",
            delta_arrow="off",
            help=kpis.winner.name if kpis.winner is not None else None,
        )
    with cols[3]:
        st.metric(
            "Größter Verlierer",
            kpis.loser_label,
            delta=kpis.loser_note,
            delta_color="off",
            delta_arrow="off",
            help=kpis.loser.name if kpis.loser is not None else None,
        )
    st.caption(
        f"Depotkennzahlen (ungefilterter Gesamtbestand) · {kpis.total_status_note} · "
        f"{kpis.performance_reference_label}."
    )

    if is_personal_view:
        pcols = st.columns(3)
        with pcols[0]:
            st.metric("G/V seit Kauf (EUR)", kpis.personal_gain_label or NOT_AVAILABLE)
        with pcols[1]:
            st.metric("Rendite seit Kauf (%)", kpis.personal_return_label or NOT_AVAILABLE)
        with pcols[2]:
            st.metric(
                "Bewerteter Einstand",
                format_eur(kpis.personal_cost_basis_eur),
            )
        if kpis.personal_coverage_label:
            st.caption(f"Einstandsvergleich: {kpis.personal_coverage_label}.")
    return hide_missing


def render_filter_controls(
    valuation: PortfolioValuation,
) -> tuple[FilterCriteria, str, bool]:
    """Render shared search, type/category filters and table sort controls.

    :param valuation: Portfolio valuation snapshot to derive category options.
    :return: Tuple of (FilterCriteria, sort_by column name, descending flag).
    """
    st.session_state.setdefault(FILTER_QUERY_KEY, "")
    st.session_state.setdefault(FILTER_TYPE_KEY, ALL_FILTER)
    st.session_state.setdefault(FILTER_CATEGORY_KEY, ALL_FILTER)
    st.session_state.setdefault(SORT_FIELD_KEY, SORT_BY_VALUE)
    st.session_state.setdefault(SORT_ORDER_KEY, SORT_DIRECTION_DESC)

    categories = available_categories(valuation)
    if st.session_state.get(FILTER_CATEGORY_KEY) not in categories:
        st.session_state[FILTER_CATEGORY_KEY] = ALL_FILTER

    fcols = st.columns((2, 1, 1, 1, 1))
    with fcols[0]:
        query = st.text_input(
            "Suche (Name, Ticker, Kennung, Kategorie)",
            key=FILTER_QUERY_KEY,
        )
    with fcols[1]:
        instrument_type = st.selectbox(
            "Instrumenttyp",
            TYPE_FILTER_OPTIONS,
            key=FILTER_TYPE_KEY,
        )
    with fcols[2]:
        category = st.selectbox(
            "Kategorie",
            categories,
            key=FILTER_CATEGORY_KEY,
        )
    with fcols[3]:
        sort_by = st.selectbox(
            "Sortierung",
            SORT_FIELDS,
            key=SORT_FIELD_KEY,
        )
    with fcols[4]:
        sort_order = st.selectbox(
            "Reihenfolge",
            SORT_DIRECTION_OPTIONS,
            key=SORT_ORDER_KEY,
        )

    criteria = FilterCriteria(
        query=query,
        instrument_type=instrument_type,
        category=category,
    )
    descending = sort_order == SORT_DIRECTION_DESC
    return criteria, sort_by, descending


def treemap_figure(
    model: TreemapModel, *, height: int = 480, min_text_size: int | None = None
) -> go.Figure:
    """Draw the prepared tiles; all values and colors come from the model.

    :param model: Prepared TreemapModel.
    :param height: Figure height in pixels.
    :param min_text_size: If set, tile text that would need a smaller font is hidden
        instead of shrunk to an unreadable size (values stay in tooltip and table).
    :return: Plotly Figure containing the treemap trace.
    """
    figure = go.Figure(
        go.Treemap(
            ids=[f"tile-{index}" for index in range(len(model.tiles))],
            labels=[tile.name for tile in model.tiles],
            parents=[""] * len(model.tiles),
            values=[float(tile.amount_eur) for tile in model.tiles],
            text=["<br>".join(escape(line) for line in tile.label) for tile in model.tiles],
            textinfo="text",
            hovertext=["<br>".join(escape(line) for line in tile.tooltip) for tile in model.tiles],
            hoverinfo="text",
            insidetextfont={"color": TILE_TEXT_COLOR},
            marker={"colors": [tile.color for tile in model.tiles], "line": {"width": 2}},
            sort=False,
        )
    )
    figure.update_layout(margin={"t": 0, "l": 0, "r": 0, "b": 0}, height=height)
    if min_text_size is not None:
        figure.update_layout(uniformtext={"minsize": min_text_size, "mode": "hide"})
    return figure


def render_color_legend(
    model: TreemapModel,
    *,
    aria_label: str = "Farblegende der Treemap",
    area_note: str = "Fläche: EUR-Positionswert",
    access_note: str = (
        "Barrierefreiheit: Vorzeichen (+/−) und Text ergänzen die Farbcodierung; "
        "alle Detailwerte stehen ohne Hover in Tabelle und Positionsdetails bereit."
    ),
) -> None:
    """Render visible legend: symmetric around zero, plus the gray n. v. state.

    The defaults describe the Depot treemap; the Watchlist heatmap passes its own texts.

    :param model: Prepared TreemapModel.
    :param aria_label: Accessible name of the legend region.
    :param area_note: Sentence naming what the tile area means.
    :param access_note: Accessibility caption below the legend.
    """
    scale = model.scale_percent
    stops = ", ".join(
        f"{color_for(scale * Decimal(step) / 10, scale)}"
        for step in range(-10, 11)
    )
    st.markdown(
        f'<div role="region" aria-label="{escape(aria_label)}" '
        'style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:0.85rem">'
        '<span>Verlust</span>'
        f"<span>{escape(format_percent(-scale))}</span>"
        f'<div style="flex:1;min-width:120px;height:12px;border-radius:6px;'
        f'background:linear-gradient(to right, {stops})"></div>'
        f"<span>{escape(format_percent(scale))}</span>"
        '<span>Gewinn</span>'
        f'<span style="background:{MISSING_COLOR};color:{TILE_TEXT_COLOR};'
        f'padding:2px 8px;border-radius:4px">{NOT_AVAILABLE} (keine Kennzahl)</span></div>',
        unsafe_allow_html=True,
    )
    note = f"{area_note}. Farbskala symmetrisch um 0 %"
    if model.capped:
        note += "; größere Werte sind nur farblich begrenzt, Zahlen bleiben unverändert"
    st.caption(note + ".")
    st.caption(access_note)


def render_treemap(model: TreemapModel) -> None:
    """Render the treemap chart, color legend and omitted-positions notice.

    :param model: Prepared TreemapModel.
    """
    if not model.tiles:
        st.info("Keine Position für die Treemap sichtbar.")
    else:
        st.plotly_chart(treemap_figure(model), width="stretch")
        render_color_legend(model)
    if model.omitted_count:
        st.caption(
            f"{model.omitted_count} Position(en) ohne positiven EUR-Wert: keine Kachel."
        )


def render_positions_table(
    rows: Sequence[PositionTableRow],
    *,
    total_active_count: int,
    filtered: bool = False,
) -> None:
    """Render the positions table including unvalued positions and portfolio weights.

    :param rows: Sorted sequence of PositionTableRow items.
    :param total_active_count: Total count of active instrument groups before filtering.
    :param filtered: Whether any search, type, or category filter is currently active.
    """
    st.subheader("Positionsübersicht")
    weight_note = (
        f"Spalte „{WEIGHT_COLUMN_LABEL}“ bezieht sich auch bei aktiven Filtern "
        "auf den gesamten bewerteten Depotbestand."
    )
    if filtered:
        st.caption(
            f"{len(rows)} von {total_active_count} aktiven Positionen sichtbar · {weight_note}"
        )
    else:
        st.caption(
            f"Alle {len(rows)} aktiven Positionen inkl. Datenlücken · {weight_note}"
        )
    display_dicts = [row.to_display_dict() for row in rows]
    st.dataframe(display_dicts, hide_index=True, width="stretch")
    render_position_details(rows)


def render_position_details(rows: Sequence[PositionTableRow]) -> None:
    """Render keyboard- and touch-accessible detail cards without requiring hover.

    Provides every tooltip field (and status/category) in a wrapping vertical
    layout for narrow iPhone screens, keyboard users, and tiny/unvalued positions.

    :param rows: Sequence of visible PositionTableRow items.
    """
    detail_blocks = build_position_details(rows)
    with st.expander(
        f"Positionsdetails ohne Hover ({len(detail_blocks)})", expanded=False
    ):
        if not detail_blocks:
            st.caption("Keine Positionen für die aktuelle Filterauswahl.")
        else:
            for block in detail_blocks:
                st.markdown(
                    f"**{escape(block[0])}**  \n"
                    + " · ".join(escape(line) for line in block[1:]),
                )


def render_data_status(notes: Sequence[str]) -> None:
    """Render position-referenced data status notes for fallbacks and gaps.

    :param notes: Sequence of German status strings from ``build_status_notes``.
    """
    with st.expander(f"Datenstatus & Hinweise ({len(notes)})", expanded=False):
        if not notes:
            st.caption("Alle aktiven Positionen sind vollständig mit aktuellen Kursen bewertet.")
        else:
            for note in notes:
                st.caption(f"• {note}")


_markdown_text = valuation_view.markdown_text


def render_watchlist_kpis(kpis: watchlist_view.WatchlistKpis, period: str) -> None:
    """Render the Watchlist header KPIs from the unfiltered entries.

    :param kpis: KPIs for the selected period.
    :param period: Selected course period.
    """
    cols = st.columns(4)
    with cols[0]:
        st.metric("Beobachtete Werte", kpis.count_label)
    with cols[1]:
        st.metric(
            f"Datenabdeckung ({period})",
            kpis.coverage_label,
            delta=kpis.coverage_note,
            delta_color="off",
            delta_arrow="off",
        )
    with cols[2]:
        st.metric(
            "Größter Gewinner",
            kpis.winner_label,
            delta=kpis.winner_note,
            delta_color="off",
            delta_arrow="off",
        )
    with cols[3]:
        st.metric(
            "Größter Verlierer",
            kpis.loser_label,
            delta=kpis.loser_note,
            delta_color="off",
            delta_arrow="off",
        )
    st.caption(
        "Watchlist-Kennzahlen (ungefilterter Gesamtbestand) · reine Kursentwicklung "
        "in Handelswährung, ohne Gewichtung und ohne Bezug zum Depot."
    )
    st.caption(kpis.coverage_detail)


def render_watchlist_filter_controls(
    entries: Sequence[WatchlistEntry],
) -> tuple[FilterCriteria, str, bool]:
    """Render search, type/category filters and sort controls of the Watchlist.

    :param entries: All entries, to derive the category options.
    :return: Tuple of (FilterCriteria, sort field, descending flag).
    """
    st.session_state.setdefault(WATCHLIST_FILTER_QUERY_KEY, "")
    st.session_state.setdefault(WATCHLIST_FILTER_TYPE_KEY, ALL_FILTER)
    st.session_state.setdefault(WATCHLIST_FILTER_CATEGORY_KEY, ALL_FILTER)
    st.session_state.setdefault(WATCHLIST_SORT_FIELD_KEY, watchlist_view.SORT_BY_PERFORMANCE)
    st.session_state.setdefault(WATCHLIST_SORT_ORDER_KEY, SORT_DIRECTION_DESC)
    categories = watchlist_view.available_categories(entries)
    if st.session_state[WATCHLIST_FILTER_CATEGORY_KEY] not in categories:
        st.session_state[WATCHLIST_FILTER_CATEGORY_KEY] = ALL_FILTER

    fcols = st.columns((2, 1, 1, 1, 1))
    with fcols[0]:
        query = st.text_input(
            "Suche (Name, Ticker, Yahoo-Symbol, Kategorie)",
            key=WATCHLIST_FILTER_QUERY_KEY,
        )
    with fcols[1]:
        instrument_type = st.selectbox(
            "Instrumenttyp",
            watchlist_view.TYPE_FILTER_OPTIONS,
            key=WATCHLIST_FILTER_TYPE_KEY,
        )
    with fcols[2]:
        category = st.selectbox("Kategorie", categories, key=WATCHLIST_FILTER_CATEGORY_KEY)
    with fcols[3]:
        sort_by = st.selectbox(
            "Sortierung", watchlist_view.SORT_FIELDS, key=WATCHLIST_SORT_FIELD_KEY
        )
    with fcols[4]:
        sort_order = st.selectbox(
            "Reihenfolge", SORT_DIRECTION_OPTIONS, key=WATCHLIST_SORT_ORDER_KEY
        )
    criteria = FilterCriteria(query=query, instrument_type=instrument_type, category=category)
    return criteria, sort_by, sort_order == SORT_DIRECTION_DESC


def heatmap_height(tile_count: int) -> int:
    """Figure height: 480 px up to 24 tiles, growing so 200+ tiles stay legible."""
    return min(HEATMAP_MAX_HEIGHT, max(480, 480 + (tile_count - 24) * 4))


def render_watchlist_heatmap(
    rows: Sequence[watchlist_view.WatchlistRow], period: str
) -> None:
    """Render the Watchlist heatmap: equal tiles for the filtered rows, then the legend.

    Tile order follows the sorted table; tooltips are a convenience only, the table
    and the details below carry every value without hover.

    :param rows: Filtered and sorted rows for ``period``.
    :param period: Selected course period.
    """
    st.subheader(f"Watchlist-Heatmap · {period}")
    if not rows:
        st.info("Keine Watchlist-Einträge für die Heatmap sichtbar.")
        return
    model = watchlist_view.build_watchlist_treemap(rows)
    st.plotly_chart(
        treemap_figure(
            model, height=heatmap_height(len(model.tiles)), min_text_size=HEATMAP_MIN_TEXT_SIZE
        ),
        width="stretch",
    )
    render_color_legend(
        model,
        aria_label="Farblegende der Watchlist-Heatmap",
        area_note="Fläche: gleich gewichtet je Watchlist-Wert",
        access_note=(
            "Barrierefreiheit: Vorzeichen (+/−) und Text ergänzen die Farbcodierung; "
            "„Sheet-Wert“ und „zwischengespeichert“ stehen in der Kachel. Alle Werte "
            "stehen ohne Hover in der Tabelle und in den Watchlist-Details bereit."
        ),
    )
    st.caption(
        f"{len(model.tiles)} Kacheln in der Reihenfolge der Tabelle (Auswahl „Sortierung“ "
        "und „Reihenfolge“). Bei vielen Einträgen oder schmalen Bildschirmen entfällt der Text kleiner Kacheln; "
        "die Werte stehen dann im Tooltip, in der Tabelle und in den Details."
    )


def render_watchlist_table(
    rows: Sequence[watchlist_view.WatchlistRow],
    *,
    total_count: int,
    filtered: bool,
) -> None:
    """Render the sortable Watchlist table and the details without hover.

    :param rows: Sorted rows for the selected period.
    :param total_count: Number of entries before filtering.
    :param filtered: Whether a search, type or category filter is active.
    """
    st.subheader("Watchlist-Übersicht")
    shown = (
        f"{len(rows)} von {total_count} Einträgen sichtbar"
        if filtered
        else f"Alle {len(rows)} Einträge"
    )
    st.caption(
        f"{shown} · n. v. = nicht verfügbar; fehlende Werte erscheinen nie als 0,00 %. "
        "Spalte „Quelle“ nennt die Herkunft des Werts der gewählten Periode. "
        "Ein Klick auf einen Spaltenkopf sortiert nur die Tabelle und nach Text; die "
        "Auswahl „Sortierung“ ordnet Tabelle und Heatmap numerisch."
    )
    if not rows:
        st.info("Keine Watchlist-Einträge für die aktuelle Filterauswahl.")
    else:
        st.dataframe([row.to_display_dict() for row in rows], hide_index=True, width="stretch")
    render_watchlist_details(rows)


def render_watchlist_details(rows: Sequence[watchlist_view.WatchlistRow]) -> None:
    """Render every table field and all four periods per entry as wrapping text.

    One markdown element keeps 200+ entries fast; no hover or horizontal scrolling.

    :param rows: Visible rows.
    """
    with st.expander(f"Watchlist-Details ohne Hover ({len(rows)})", expanded=False):
        if not rows:
            st.caption("Keine Einträge für die aktuelle Filterauswahl.")
            return
        blocks = []
        for row in rows:
            head, *lines = row.detail_lines()
            blocks.append(
                f"**{_markdown_text(head)}**  \n"
                + "  \n".join(_markdown_text(line) for line in lines)
            )
        st.markdown("\n\n".join(blocks))


def render_watchlist_status(notes: Sequence[str]) -> None:
    """Render the row-referenced data status notes of the Watchlist.

    :param notes: German notes from ``watchlist_view.build_status_notes``.
    """
    with st.expander(f"Datenstatus & Hinweise ({len(notes)})", expanded=False):
        if not notes:
            st.caption("Alle Einträge zeigen aktuelle Anbieterdaten.")
        else:
            st.markdown("\n".join(f"- {_markdown_text(note)}" for note in notes))


def render_watchlist(
    factory: Callable[[], WatchlistLoader | None] | None,
    *,
    refresh_clicked: bool,
    demo: bool,
) -> None:
    """Render the Watchlist area: bundle, period, KPIs, filters, table and status.

    The loader is built once per session and calendar day (``session_loader``) and
    called behind a spinner, never unguarded: a cold start with 200+ rows takes minutes.

    :param factory: Builds the loader; ``None`` or a factory returning ``None`` shows
        the configuration notice (``PORTFOLIO_WATCHLIST_RANGE`` missing).
    :param refresh_clicked: Whether "Aktualisieren" was pressed in this run.
    :param demo: Whether the loader serves synthetic data.
    """
    st.divider()
    loader = None
    if factory is not None:
        try:
            loader, rebuilt = session_loader(st.session_state, factory, _today())
        except (SheetsConfigurationError, SheetsCredentialError) as exc:
            st.error(str(exc))
            return
        if rebuilt:  # New calendar window and cache: the old bundle no longer applies.
            st.session_state.pop(WATCHLIST_BUNDLE_KEY, None)
    if loader is None:
        st.info(WATCHLIST_NOT_CONFIGURED_TEXT)
        return

    if refresh_clicked or WATCHLIST_BUNDLE_KEY not in st.session_state:
        with st.spinner(WATCHLIST_SPINNER_TEXT):
            st.session_state[WATCHLIST_BUNDLE_KEY] = loader.load(refresh=refresh_clicked)
    bundle: WatchlistBundle = st.session_state[WATCHLIST_BUNDLE_KEY]

    st.caption(
        f"{'Demo-Watchlist' if demo else 'Watchlist'} · {len(bundle.entries)} Einträge"
    )
    stamp = bundle.last_attempt_at.strftime("%d.%m.%Y, %H:%M %Z")
    if demo:
        st.caption(f"Datenstand (synthetisch): {stamp}")
    else:
        age = format_snapshot_age(bundle.last_attempt_at, datetime.now(UTC))
        st.caption(f"Datenstand: {stamp} (Alter: {age})")
    if demo:
        st.info("Nur synthetische Beispieldaten. Kein Zugriff auf echte Depot- oder Kursdaten.")
    if not bundle.available:
        st.error(
            bundle.status_message
            or f"{DATA_UNAVAILABLE_TEXT}: Keine gültigen Watchlist-Daten vorhanden."
        )
        return
    if bundle.using_cached_stand and bundle.status_message:
        st.warning(bundle.status_message)
    elif refresh_clicked and bundle.status_message:
        st.caption(bundle.status_message)

    st.divider()
    _, period = render_view_controls(watchlist=True)
    if st.session_state[COURSE_PERIOD_KEY] not in COURSE_PERIODS:
        st.caption(WATCHLIST_SINCE_PURCHASE_TEXT)
    st.subheader(f"Watchlist · {period}")
    entries = bundle.entries
    notes = watchlist_view.build_status_notes(entries, bundle.snapshot.warnings)
    if not entries:
        st.info("Das Watchlist-Blatt enthält keine Einträge.")
        render_watchlist_status(notes)
        return
    render_watchlist_kpis(watchlist_view.build_kpis(entries, period), period)
    criteria, sort_by, descending = render_watchlist_filter_controls(entries)
    rows = watchlist_view.sort_rows(
        watchlist_view.build_rows(entries, period, criteria),
        sort_by,
        descending=descending,
    )
    render_watchlist_heatmap(rows, period)
    render_watchlist_table(
        rows,
        total_count=len(entries),
        filtered=bool(
            criteria.query.strip()
            or criteria.instrument_type != ALL_FILTER
            or criteria.category != ALL_FILTER
        ),
    )
    render_watchlist_status(notes)


def _valuation_watchlist(
    factory: Callable[[], WatchlistLoader | None] | None, *, load: bool
) -> WatchlistBundle | None:
    """The Watchlist bundle of the session; loaded only when ``load`` is set.

    The bundle held by the Watchlist area is reused as it is. A cold start with
    200+ rows takes minutes, so it happens only on an explicit action, behind the
    same spinner as in the Watchlist area.
    """
    held: WatchlistBundle | None = st.session_state.get(WATCHLIST_BUNDLE_KEY)
    if held is not None or not load or factory is None:
        return held
    try:
        loader, _ = session_loader(st.session_state, factory, _today())
    except (SheetsConfigurationError, SheetsCredentialError) as exc:
        st.error(str(exc))
        return None
    if loader is None:
        return None
    with st.spinner(WATCHLIST_SPINNER_TEXT):
        st.session_state[WATCHLIST_BUNDLE_KEY] = loader.load()
    return st.session_state[WATCHLIST_BUNDLE_KEY]


def render_valuation_area(
    source: OverviewSource,
    *,
    coordinator: PortfolioRefreshCoordinator | None,
    watchlist: Callable[[], WatchlistLoader | None] | None,
    refresh_clicked: bool,
    demo: bool,
) -> None:
    """The «Innerer Wert» area: shared data stand, explicit loading, overview.

    Depot and Watchlist prices are those of their own areas (same bundles, same
    stand); fundamentals come from the shared loader (#74) and are loaded and
    refreshed only by the load button or «Aktualisieren». The demo loads its
    invented data without a click. Storage errors are shown as their fixed message.
    """
    st.divider()
    st.subheader(VALUATION_AREA)
    if demo:
        st.info("Nur synthetische Beispieldaten. Kein Zugriff auf echte Depot- oder Kursdaten.")
    loader = st.session_state.get(FUNDAMENTALS_LOADER_KEY)
    loaded_before = valuation_view.LOADED_STATE_KEY in st.session_state
    clicked = valuation_view.render_load_button(
        loaded=loaded_before, configured=loader is not None
    )
    explicit = clicked or refresh_clicked
    stamps: list[str] = []

    portfolio = source.portfolio
    if portfolio is None and coordinator is not None:
        depot = st.session_state.get(BUNDLE_STATE_KEY)
        if depot is None:
            with st.spinner("Bestands- und Kursdaten werden aktualisiert …"):
                depot = coordinator.load()
                st.session_state[BUNDLE_STATE_KEY] = depot
        if depot.available:
            portfolio = depot.valuation
            stamps.append(f"Kurse Depot: {depot.age_label()}")
        else:
            st.warning(depot.status_message or f"{DATA_UNAVAILABLE_TEXT}: Keine gültigen Depotdaten.")
    elif portfolio is not None and demo:
        stamps.append(f"Kurse Depot (synthetisch): {portfolio.snapshot.attempted_at:%d.%m.%Y, %H:%M %Z}")

    bundle = _valuation_watchlist(watchlist, load=explicit or demo)
    entries = ()
    if bundle is None:
        stamps.append("Kurse Watchlist: noch nicht geladen oder nicht eingerichtet.")
    elif not bundle.available:
        stamps.append(bundle.status_message or "Watchlist-Daten nicht verfügbar.")
    else:
        entries = bundle.entries
        stamp = bundle.last_attempt_at.strftime("%d.%m.%Y, %H:%M %Z")
        if demo:
            stamps.append(f"Kurse Watchlist (synthetisch): {stamp}")
        else:
            age = format_snapshot_age(bundle.last_attempt_at, datetime.now(UTC))
            stamps.append(f"Kurse Watchlist: {stamp} (Alter: {age})")

    subjects = collect_subjects(portfolio, entries, source.listings)
    state, loaded = valuation_view.current_state(
        source, loader, subjects, explicit=explicit, auto=demo
    )
    if loaded is not None and loaded.bundle is not None:
        checked = [e.last_checked_at for e in loaded.bundle.entries.values() if e.last_checked_at]
        if checked:
            stamps.append(
                f"Fundamentaldaten: letzter Abruf {max(checked):%d.%m.%Y, %H:%M %Z} "
                "(Abrufalter; das fachliche Alter der Abschlüsse steht je Aktie)"
            )
    valuation_view.render_overview(
        source,
        portfolio=portfolio,
        entries=entries,
        state=state,
        loaded=loaded,
        stamps=stamps,
    )


def render_dashboard(
    snapshot: PortfolioSnapshot,
    rows: list[dict[str, str | float]] | None,
    valuation: PortfolioValuation,
    performances: Callable[[str, str], Sequence[Performance]],
    *,
    returns: PortfolioReturns | None = None,
    coordinator: PortfolioRefreshCoordinator | None = None,
    period_reasons: Callable[[str, str], Sequence[str | None]] | None = None,
    demo: bool = True,
    watchlist: Callable[[], WatchlistLoader | None] | None = None,
    overview: OverviewSource | None = None,
) -> None:
    """Render the shell, refresh action, KPIs, filters, treemap, table and data status.

    The Ansicht radio switches between the Depot (Zeitraum ``Heute`` to ``Jahr`` is the
    course view, ``Seit Kauf`` the personal view) and the Watchlist. "Aktualisieren"
    refreshes the area that is shown; the other area keeps its loaded state.

    KPIs, treemap and positions table always use the same atomic ``valuation``
    snapshot. When ``coordinator`` is provided, clicking "Aktualisieren"
    performs an atomic holdings and market-data refresh, keeping the prior
    consistent snapshot with age on failure or reporting "Daten nicht verfügbar".

    :param snapshot: Portfolio snapshot being displayed.
    :param rows: Deprecated static row list (retained for call compatibility).
    :param valuation: Portfolio valuation derived from ``snapshot``.
    :param performances: Callback returning one Performance per group in ``valuation.groups``.
    :param returns: Optional precomputed PortfolioReturns for the personal view.
    :param coordinator: Optional PortfolioRefreshCoordinator for live/cached refreshes.
    :param period_reasons: Optional callback returning period reason codes per group.
    :param demo: False when ``coordinator`` delivers the owner's own data.
    :param watchlist: Builds the Watchlist loader once per session and day; ``None`` or
        a factory returning ``None`` shows the "PORTFOLIO_WATCHLIST_RANGE" notice.
    :param overview: Wiring of the «Innerer Wert» area (registry, scenario reader,
        clock); ``None`` offers only Depot and Watchlist.
    """
    _ = rows
    render_accessibility_styles()
    header_cols = st.columns((4, 1))
    with header_cols[0]:
        st.title("Depot-Dashboard – Demo" if demo else "Depot-Dashboard")
    with header_cols[1]:
        refresh_clicked = st.button("Aktualisieren", key=REFRESH_BUTTON_KEY)

    areas = AREAS if overview is None else (*AREAS, VALUATION_AREA)
    selected = render_area_control(areas)
    if selected == WATCHLIST_AREA:
        _keep_state(_DEPOT_WIDGET_KEYS + valuation_view.WIDGET_KEYS)
        render_watchlist(watchlist, refresh_clicked=refresh_clicked, demo=demo)
        return
    if selected == VALUATION_AREA:
        _keep_state(_DEPOT_WIDGET_KEYS + _WATCHLIST_WIDGET_KEYS)
        render_valuation_area(
            overview,
            coordinator=coordinator,
            watchlist=watchlist,
            refresh_clicked=refresh_clicked,
            demo=demo,
        )
        return
    _keep_state(_WATCHLIST_WIDGET_KEYS + valuation_view.WIDGET_KEYS)

    active_bundle: DashboardBundle | None = None
    if coordinator is not None:
        if refresh_clicked or BUNDLE_STATE_KEY not in st.session_state:
            with st.spinner("Bestands- und Kursdaten werden aktualisiert …"):
                active_bundle = coordinator.load(refresh=refresh_clicked)
                st.session_state[BUNDLE_STATE_KEY] = active_bundle
        else:
            active_bundle = st.session_state[BUNDLE_STATE_KEY]

        snapshot = active_bundle.snapshot
        valuation = active_bundle.valuation
        returns = active_bundle.returns
        performances = active_bundle.performances_for
        period_reasons = active_bundle.reasons_for

    if demo:
        st.caption(f"Demo-Depot · {len(snapshot.positions)} Beispielpositionen")
    else:
        st.caption(f"Eigenes Depot · {len(snapshot.positions)} Positionen")
    timestamp = snapshot.attempted_at.strftime("%d.%m.%Y, %H:%M %Z")
    if active_bundle is not None:
        st.caption(f"Datenstand: {active_bundle.age_label()}")
    else:
        st.caption(f"Datenstand (synthetisch): {timestamp}")

    if demo:
        st.info("Nur synthetische Beispieldaten. Kein Zugriff auf echte Depot- oder Kursdaten.")

    if active_bundle is not None:
        if not active_bundle.available or snapshot.status is SnapshotStatus.FAILED:
            st.error(
                active_bundle.status_message
                or f"{DATA_UNAVAILABLE_TEXT}: Keine gültigen Depotdaten vorhanden."
            )
            return
        if active_bundle.using_cached_stand and active_bundle.status_message:
            st.warning(active_bundle.status_message)
        elif refresh_clicked and active_bundle.status_message:
            st.caption(active_bundle.status_message)
    elif snapshot.status is SnapshotStatus.FAILED:
        st.error(f"{DATA_UNAVAILABLE_TEXT}: Bestandsabruf fehlgeschlagen.")
        return
    elif refresh_clicked:
        st.caption(f"Synthetischer Demo-Stand geprüft ({timestamp}).")

    st.divider()
    view, period = render_view_controls()
    is_personal = view == PERSONAL_VIEW
    current_performances = tuple(performances(view, period))
    current_reasons = (
        tuple(period_reasons(view, period))
        if period_reasons is not None
        else (None,) * len(valuation.groups)
    )
    resolved_returns = (
        returns
        if returns is not None
        else (portfolio_returns(valuation) if is_personal else None)
    )

    st.subheader(f"{view} · {period}")
    if demo and is_personal:
        st.info("Rendite seit Kauf: n. v. – die Demo berechnet keine persönliche Rendite.")
    elif demo:
        st.caption("Die Demo zeigt feste, erfundene Beispielwerte für die Kursentwicklung.")

    kpis = build_kpis(
        valuation,
        current_performances,
        is_personal_view=is_personal,
        returns=resolved_returns,
    )
    missing_course_groups = [
        group
        for group, performance in zip(valuation.groups, current_performances)
        if performance.percent is None
        and group.amount_eur is not None
        and group.amount_eur > 0
    ]
    hide_missing = render_kpis(
        kpis,
        is_personal_view=is_personal,
        missing_course_count=len(missing_course_groups),
        missing_course_eur=sum((g.amount_eur for g in missing_course_groups), Decimal(0)),
    )

    criteria, sort_by, descending = render_filter_controls(valuation)
    filtered_groups, filtered_performances = filter_groups(
        valuation, current_performances, criteria
    )
    render_treemap(
        build_treemap(
            valuation,
            filtered_performances,
            groups=filtered_groups,
            hide_missing_performance=hide_missing,
        )
    )

    table_rows = build_table_rows(
        valuation,
        current_performances,
        criteria=criteria,
        is_personal_view=is_personal,
        returns=resolved_returns,
        period_reasons=current_reasons,
    )
    sorted_rows = sort_table_rows(table_rows, sort_by=sort_by, descending=descending)
    is_filtered = bool(
        criteria.query.strip()
        or criteria.instrument_type != ALL_FILTER
        or criteria.category != ALL_FILTER
    )
    render_positions_table(
        sorted_rows,
        total_active_count=kpis.active_group_count,
        filtered=is_filtered,
    )

    status_notes = build_status_notes(
        valuation,
        current_performances,
        is_personal_view=is_personal,
        returns=resolved_returns,
        period_reasons=current_reasons,
    )
    render_data_status(status_notes)
