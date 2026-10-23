"""Small native Streamlit helpers for the German dashboard shell."""

import streamlit as st

from portfolio_dashboard.models import PortfolioSnapshot


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


def render_dashboard(
    snapshot: PortfolioSnapshot, rows: list[dict[str, str | float]]
) -> None:
    """Render only the synthetic shell; no source access or return calculation."""
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
        st.caption("Die Demo zeigt feste Beispielwerte; Kursrenditen sind noch nicht verfügbar.")
    st.dataframe(rows, hide_index=True, width="stretch")
