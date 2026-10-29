"""Small native Streamlit helpers for the German dashboard shell."""

from collections.abc import Callable, Sequence
from decimal import Decimal
from html import escape

import plotly.graph_objects as go
import streamlit as st

from portfolio_dashboard.models import PortfolioSnapshot, SnapshotStatus
from portfolio_dashboard.refresh import (
    DATA_UNAVAILABLE_TEXT,
    DashboardBundle,
    PortfolioRefreshCoordinator,
)
from portfolio_dashboard.returns import PortfolioReturns, portfolio_returns
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
from portfolio_dashboard.valuations import PortfolioValuation

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
HIDE_MISSING_COURSE_KEY = "dashboard.hide_missing_course"
_HIDE_MISSING_WIDGET_KEY = "_dashboard.hide_missing_course"
FILTER_QUERY_KEY = "dashboard.filter_query"
FILTER_TYPE_KEY = "dashboard.filter_type"
FILTER_CATEGORY_KEY = "dashboard.filter_category"
SORT_FIELD_KEY = "dashboard.sort_field"
SORT_ORDER_KEY = "dashboard.sort_order"
REFRESH_BUTTON_KEY = "dashboard.refresh"
BUNDLE_STATE_KEY = "dashboard.bundle"

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


def render_view_controls() -> tuple[str, str]:
    """Render the Zeitraum radio and derive the view from the selection.

    :return: Tuple of (derived view, selected course period or ``"Seit Kauf"``).
    """
    st.session_state.setdefault(COURSE_PERIOD_KEY, COURSE_PERIODS[0])
    st.session_state[_PERIOD_WIDGET_KEY] = st.session_state[COURSE_PERIOD_KEY]
    period = st.radio(
        "Zeitraum",
        (*COURSE_PERIODS, SINCE_PURCHASE),
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


def treemap_figure(model: TreemapModel) -> go.Figure:
    """Draw the prepared tiles; all values and colors come from the model.

    :param model: Prepared TreemapModel.
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
    figure.update_layout(margin={"t": 0, "l": 0, "r": 0, "b": 0}, height=480)
    return figure


def render_color_legend(model: TreemapModel) -> None:
    """Render visible legend: symmetric around zero, plus the gray n. v. state.

    :param model: Prepared TreemapModel.
    """
    scale = model.scale_percent
    stops = ", ".join(
        f"{color_for(scale * Decimal(step) / 10, scale)}"
        for step in range(-10, 11)
    )
    st.markdown(
        '<div role="region" aria-label="Farblegende der Treemap" '
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
    note = "Fläche: EUR-Positionswert. Farbskala symmetrisch um 0 %"
    if model.capped:
        note += "; größere Werte sind nur farblich begrenzt, Zahlen bleiben unverändert"
    st.caption(note + ".")
    st.caption(
        "Barrierefreiheit: Vorzeichen (+/−) und Text ergänzen die Farbcodierung; "
        "alle Detailwerte stehen ohne Hover in Tabelle und Positionsdetails bereit."
    )


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
) -> None:
    """Render the shell, refresh action, KPIs, filters, treemap, table and data status.

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
    """
    _ = rows
    render_accessibility_styles()
    header_cols = st.columns((4, 1))
    with header_cols[0]:
        st.title("Depot-Dashboard – Demo" if demo else "Depot-Dashboard")
    with header_cols[1]:
        refresh_clicked = st.button("Aktualisieren", key=REFRESH_BUTTON_KEY)

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
