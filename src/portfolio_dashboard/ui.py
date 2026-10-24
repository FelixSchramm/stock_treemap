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
    WEIGHT_COLUMN_LABEL,
    FilterCriteria,
    Performance,
    PortfolioKpis,
    PositionTableRow,
    TreemapModel,
    available_categories,
    build_kpis,
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
TYPE_FILTER_OPTIONS = (ALL_FILTER, "Aktie", "ETF", "Derivat")
SORT_DIRECTION_DESC = "Absteigend"
SORT_DIRECTION_ASC = "Aufsteigend"
SORT_DIRECTION_OPTIONS = (SORT_DIRECTION_DESC, SORT_DIRECTION_ASC)

# Public keys are the retained session state; the hidden widget may be removed
# by Streamlit when switching views, so it has its own temporary key.
VIEW_KEY = "dashboard.view"
COURSE_PERIOD_KEY = "dashboard.course_period"
_PERIOD_WIDGET_KEY = "_dashboard.course_period"
FILTER_QUERY_KEY = "dashboard.filter_query"
FILTER_TYPE_KEY = "dashboard.filter_type"
FILTER_CATEGORY_KEY = "dashboard.filter_category"
SORT_FIELD_KEY = "dashboard.sort_field"
SORT_ORDER_KEY = "dashboard.sort_order"
REFRESH_BUTTON_KEY = "dashboard.refresh"
BUNDLE_STATE_KEY = "dashboard.bundle"


def _remember_course_period() -> None:
    """Persist the selected course period across view switches."""
    st.session_state[COURSE_PERIOD_KEY] = st.session_state[_PERIOD_WIDGET_KEY]


def render_view_controls() -> tuple[str, str]:
    """Return the view and period, preserving course periods across hidden runs.

    :return: Tuple of (selected view, selected period or ``"Seit Kauf"``).
    """
    st.session_state.setdefault(VIEW_KEY, COURSE_VIEW)
    st.session_state.setdefault(COURSE_PERIOD_KEY, COURSE_PERIODS[0])
    view = st.radio(
        "Ansicht", (COURSE_VIEW, PERSONAL_VIEW), key=VIEW_KEY, horizontal=True
    )
    if view == PERSONAL_VIEW:
        st.caption("Zeitraum: Seit Kauf")
        return view, "Seit Kauf"

    st.session_state[_PERIOD_WIDGET_KEY] = st.session_state[COURSE_PERIOD_KEY]
    period = st.radio(
        "Kursperiode",
        COURSE_PERIODS,
        key=_PERIOD_WIDGET_KEY,
        on_change=_remember_course_period,
        horizontal=True,
    )
    return view, period


def render_kpis(kpis: PortfolioKpis, *, is_personal_view: bool = False) -> None:
    """Render portfolio-wide header KPIs from the unfiltered valuation snapshot.

    :param kpis: Calculated PortfolioKpis object.
    :param is_personal_view: Whether to include the personal cost-basis comparison.
    """
    cols = st.columns(4)
    with cols[0]:
        st.metric(
            "Bewerteter Depotwert",
            kpis.total_eur_label,
            delta=kpis.total_status_note,
            delta_color="off",
        )
    with cols[1]:
        st.metric(
            "Aktive Positionen",
            kpis.active_positions_label,
            delta=kpis.coverage_label,
            delta_color="off",
        )
    with cols[2]:
        st.metric("Größter Gewinner", kpis.winner_label)
    with cols[3]:
        st.metric("Größter Verlierer", kpis.loser_label)
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
        '<div style="display:flex;align-items:center;gap:8px;font-size:0.85rem">'
        f"<span>{escape(format_percent(-scale))}</span>"
        f'<div style="flex:1;height:12px;border-radius:6px;'
        f'background:linear-gradient(to right, {stops})"></div>'
        f"<span>{escape(format_percent(scale))}</span>"
        f'<span style="background:{MISSING_COLOR};padding:0 6px;border-radius:4px">'
        f"{NOT_AVAILABLE}</span></div>",
        unsafe_allow_html=True,
    )
    note = "Fläche: EUR-Positionswert. Farbskala symmetrisch um 0 %"
    if model.capped:
        note += "; größere Werte sind nur farblich begrenzt, Zahlen bleiben unverändert"
    st.caption(note + ".")


def render_treemap(model: TreemapModel) -> None:
    """Render the treemap chart, color legend and omitted-positions notice.

    :param model: Prepared TreemapModel.
    """
    if not model.tiles:
        st.info("Keine bewertete Position für die Treemap.")
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
    """
    _ = rows
    header_cols = st.columns((4, 1))
    with header_cols[0]:
        st.title("Depot-Dashboard – Demo")
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

    st.caption(f"Demo-Depot · {len(snapshot.positions)} Beispielpositionen")
    timestamp = snapshot.attempted_at.strftime("%d.%m.%Y, %H:%M %Z")
    if active_bundle is not None:
        st.caption(f"Datenstand: {active_bundle.age_label()}")
    else:
        st.caption(f"Datenstand (synthetisch): {timestamp}")

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
    if is_personal:
        st.info("Rendite seit Kauf: n. v. – die Demo berechnet keine persönliche Rendite.")
    else:
        st.caption("Die Demo zeigt feste, erfundene Beispielwerte für die Kursentwicklung.")

    kpis = build_kpis(
        valuation,
        current_performances,
        is_personal_view=is_personal,
        returns=resolved_returns,
    )
    render_kpis(kpis, is_personal_view=is_personal)

    criteria, sort_by, descending = render_filter_controls(valuation)
    filtered_groups, filtered_performances = filter_groups(
        valuation, current_performances, criteria
    )
    render_treemap(
        build_treemap(valuation, filtered_performances, groups=filtered_groups)
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
