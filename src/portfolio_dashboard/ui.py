"""Small native Streamlit helpers for the German dashboard shell."""

from collections.abc import Callable, Sequence
from decimal import Decimal
from html import escape

import plotly.graph_objects as go
import streamlit as st

from portfolio_dashboard.models import PortfolioSnapshot
from portfolio_dashboard.treemap import (
    MISSING_COLOR,
    NOT_AVAILABLE,
    Performance,
    TreemapModel,
    build_treemap,
    color_for,
    format_percent,
)
from portfolio_dashboard.valuations import PortfolioValuation

COURSE_VIEW = "Kursentwicklung"
PERSONAL_VIEW = "Meine Entwicklung"
COURSE_PERIODS = ("Heute", "Woche", "Monat", "Jahr")

# Public keys are the retained session state; the hidden widget may be removed
# by Streamlit when switching views, so it has its own temporary key.
VIEW_KEY = "dashboard.view"
COURSE_PERIOD_KEY = "dashboard.course_period"
_PERIOD_WIDGET_KEY = "_dashboard.course_period"


def _remember_course_period() -> None:
    st.session_state[COURSE_PERIOD_KEY] = st.session_state[_PERIOD_WIDGET_KEY]


def render_view_controls() -> tuple[str, str]:
    """Return the view and period, preserving course periods across hidden runs."""
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


def treemap_figure(model: TreemapModel) -> go.Figure:
    """Draw the prepared tiles; all values and colors come from the model."""
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
    """Visible legend: symmetric around zero, plus the gray n. v. state."""
    scale = model.scale_percent
    stops = ", ".join(
        f"{color_for(scale * Decimal(step) / 10, scale)}"
        for step in range(-10, 11)
    )
    st.markdown(
        '<div style="display:flex;align-items:center;gap:8px;font-size:0.85rem">'
        f"<span>{escape(format_percent(-scale))} %</span>"
        f'<div style="flex:1;height:12px;border-radius:6px;'
        f'background:linear-gradient(to right, {stops})"></div>'
        f"<span>{escape(format_percent(scale))} %</span>"
        f'<span style="background:{MISSING_COLOR};padding:0 6px;border-radius:4px">'
        f"{NOT_AVAILABLE}</span></div>",
        unsafe_allow_html=True,
    )
    note = "Fläche: EUR-Positionswert. Farbskala symmetrisch um 0 %"
    if model.capped:
        note += "; größere Werte sind nur farblich begrenzt, Zahlen bleiben unverändert"
    st.caption(note + ".")


def render_treemap(model: TreemapModel) -> None:
    if not model.tiles:
        st.info("Keine bewertete Position für die Treemap.")
    else:
        st.plotly_chart(treemap_figure(model), width="stretch")
        render_color_legend(model)
    if model.omitted_count:
        st.caption(
            f"{model.omitted_count} Position(en) ohne bewerteten EUR-Wert: keine Kachel."
        )


def render_dashboard(
    snapshot: PortfolioSnapshot,
    rows: list[dict[str, str | float]],
    valuation: PortfolioValuation,
    performances: Callable[[str, str], Sequence[Performance]],
) -> None:
    """Render the shell and treemap; callers supply values and percentages."""
    st.title("Depot-Dashboard – Demo")
    st.caption(f"Demo-Depot · {len(snapshot.positions)} Beispielpositionen")
    timestamp = snapshot.attempted_at.strftime("%d.%m.%Y, %H:%M %Z")
    st.caption(f"Datenstand (synthetisch): {timestamp}")
    st.info("Nur synthetische Beispieldaten. Kein Zugriff auf echte Depot- oder Kursdaten.")

    st.divider()
    view, period = render_view_controls()
    st.subheader(f"{view} · {period}")
    if view == PERSONAL_VIEW:
        st.info("Rendite seit Kauf: n. v. – die Demo berechnet keine persönliche Rendite.")
    else:
        st.caption("Die Demo zeigt feste, erfundene Beispielwerte für die Kursentwicklung.")
    render_treemap(build_treemap(valuation, performances(view, period)))
    st.dataframe(rows, hide_index=True, width="stretch")
