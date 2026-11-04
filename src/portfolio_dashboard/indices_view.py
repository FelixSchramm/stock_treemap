"""Index selection, four KPIs, hoverable treemap and complete constituent table."""

from decimal import Decimal
from html import escape

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from portfolio_dashboard.design import style_figure
from portfolio_dashboard.indices import IndexBundle, IndexSourceError
from portfolio_dashboard.sheets_adapter import SheetsConfigurationError
from portfolio_dashboard.sheets_session import SheetsCredentialError
from portfolio_dashboard.treemap import color_for, color_scale, format_percent
from portfolio_dashboard.watchlist_quotes import PERIODS

AREA = "Indizes"
LOADER_KEY = "own.indices_loader"
INDEX_KEY = "indices.selected"
PERIOD_KEY = "indices.period"


def _percent(value):
    return "NA" if value is None else format_percent(value)


def _money(value, currency):
    if value is None:
        return "NA"
    for scale, suffix in ((Decimal("1e12"), "Bio."), (Decimal("1e9"), "Mrd."), (Decimal("1e6"), "Mio.")):
        if value >= scale:
            return f"{value / scale:.2f}".replace(".", ",") + f" {suffix} {currency}"
    return f"{value:.2f}".replace(".", ",") + f" {currency}"


def build_figure(bundle: IndexBundle, period: str) -> go.Figure:
    weighted = bundle.total_cap is not None
    rows = [row for row in bundle.rows if not weighted or row.market_cap is not None]
    scale, _ = color_scale([row.percent(period) for row in rows])
    figure = go.Figure(go.Treemap(
        ids=[str(i) for i in range(len(rows))],
        labels=[escape(row.constituent.name) for row in rows],
        parents=[""] * len(rows),
        values=[float(row.market_cap) if weighted else 1 for row in rows],
        customdata=[[
            escape(row.constituent.symbol or "NA"),
            _percent(row.percent(period)),
            _money(row.market_cap, row.currency),
            _percent(row.market_cap / bundle.total_cap * 100).lstrip("+") if weighted else "Gleiche Kachelgröße",
        ] for row in rows],
        marker=dict(colors=[color_for(row.percent(period), scale) for row in rows], line=dict(width=2)),
        texttemplate="<b>%{label}</b><br>%{customdata[1]}",
        hovertemplate=(
            "<b>%{label}</b> · %{customdata[0]}<br>"
            f"Kursentwicklung ({period}): %{{customdata[1]}}<br>"
            "Marktkapitalisierung: %{customdata[2]}<br>"
            "Flächenanteil: %{customdata[3]}<extra></extra>"
        ),
        textfont=dict(color="white"),
        pathbar=dict(visible=False),
        tiling=dict(pad=3),
    ))
    style_figure(figure)
    figure.update_layout(height=530, margin=dict(l=0, r=0, t=10, b=0))
    return figure


def table_rows(bundle: IndexBundle, period: str) -> list[dict]:
    total = bundle.total_cap
    rows = []
    for row in bundle.rows:
        entry = row.quote
        percent = row.percent(period)
        price = entry.price if entry else None
        status = row.note or "Yahoo Finance"
        if percent is None:
            status += " · NA für diesen Zeitraum"
        rows.append({
            "Unternehmen": row.constituent.name,
            "Kursentwicklung (%)": float(percent) if percent is not None else None,
            "Kurs": float(price) if price is not None else None,
            "Kurswährung": entry.price_unit.currency if entry and entry.price_unit else "NA",
            "Marktkapitalisierung": float(row.market_cap) if row.market_cap is not None else None,
            "Kapitalisierungswährung": row.currency or "NA",
            "Anteil (%)": float(row.market_cap / total * 100) if total and row.market_cap else None,
            "Yahoo-Ticker": row.constituent.symbol or "NA",
            "Kursstand": entry.current_date.strftime("%d.%m.%Y") if entry and entry.current_date else "NA",
            "Status": status,
        })
    return rows


def render(factory, *, refresh_clicked=False, demo=False):
    if factory is None:
        st.info("Index-Daten sind noch nicht angebunden.")
        return
    try:
        if LOADER_KEY not in st.session_state:
            st.session_state[LOADER_KEY] = factory()
        loader = st.session_state[LOADER_KEY]
        names = loader.list_indices(refresh=refresh_clicked)
    except (IndexSourceError, SheetsConfigurationError, SheetsCredentialError):
        st.error("Index-Blätter konnten nicht geladen werden. Über „Aktualisieren“ erneut versuchen.")
        return
    if not names:
        st.info("Noch keine Index-Blätter gefunden. Unterstützt werden DAX, MDAX, Euro Stoxx 50, "
                "STOXX Europe 600, S&P 500, Nasdaq 100 und Dow Jones.")
        return

    left, right = st.columns((1, 2))
    selected = st.session_state.get(INDEX_KEY)
    if selected not in names:
        selected = "DAX" if "DAX" in names else names[0]
    with left:
        name = st.selectbox("Index", names, index=names.index(selected), key="_indices.selected")
    st.session_state[INDEX_KEY] = name
    with right:
        current_period = st.session_state.get(PERIOD_KEY, "Heute")
        period = st.radio("Zeitraum", PERIODS, index=PERIODS.index(current_period),
                          horizontal=True, key="_indices.period")
    st.session_state[PERIOD_KEY] = period
    with st.spinner(f"{name}: Unternehmen, Kurse und Marktkapitalisierungen werden geladen …"):
        bundle = loader.load(name, refresh=refresh_clicked)
    st.subheader(f"{name} · {period}")
    if demo:
        st.info("Nur synthetische Beispieldaten. Kein Zugriff auf echte Index- oder Kursdaten.")
    if bundle.warning:
        st.warning(bundle.warning)
    if not bundle.available:
        return
    st.caption(f"Geladener Stand: {bundle.fetched_at:%d.%m.%Y, %H:%M %Z} · "
               "Zusammensetzung aus dem Sheet · Kurse und Marktkapitalisierung: Yahoo Finance"
               if not demo else "Synthetischer Demo-Index")
    if not bundle.rows:
        st.info("Dieses Index-Blatt enthält noch keine Unternehmen.")
        return

    gains = [row for row in bundle.rows if row.percent(period) is not None and row.percent(period) > 0]
    losses = [row for row in bundle.rows if row.percent(period) is not None and row.percent(period) < 0]
    winner = max(gains, key=lambda row: row.percent(period), default=None)
    loser = min(losses, key=lambda row: row.percent(period), default=None)
    covered = sum(row.market_cap is not None for row in bundle.rows)
    quoted = sum(row.percent(period) is not None for row in bundle.rows)
    columns = st.columns(4)
    with columns[0]:
        st.metric("Marktkapitalisierung", _money(bundle.total_cap, bundle.cap_currency),
                  help="Summe verfügbarer Unternehmenswerte. Keine offizielle Indexgewichtung.")
        st.caption(f"{covered}/{len(bundle.rows)} Werte verfügbar" if bundle.cap_currency else "Keine gemeinsame Kapitalisierungswährung verfügbar")
    with columns[1]:
        st.metric("Größter Gewinner", _percent(winner.percent(period)) if winner else "NA")
        st.caption(winner.constituent.name if winner else "Keine positive Kursentwicklung verfügbar")
    with columns[2]:
        st.metric("Größter Verlierer", _percent(loser.percent(period)) if loser else "NA")
        st.caption(loser.constituent.name if loser else "Keine negative Kursentwicklung verfügbar")
    with columns[3]:
        st.metric("Aktive Positionen", str(len(bundle.rows)), help="Unternehmen im ausgewählten Index-Blatt; keine Depotpositionen.")
        st.caption(f"{quoted}/{len(bundle.rows)} mit Kursentwicklung ({period})")

    st.plotly_chart(build_figure(bundle, period), width="stretch", key="indices.treemap")
    st.caption("Grün: Gewinn · Rot: Verlust · Grau: NA · Kursentwicklung ohne Dividenden, in Handelswährung.")
    if bundle.total_cap is not None:
        st.caption("Kachelgröße: verfügbare Marktkapitalisierung · Unternehmensgrößen, keine offiziellen Indexgewichte. "
                   "Unternehmen ohne Marktkapitalisierung bleiben in der Liste sichtbar.")
    else:
        st.caption("Gleiche Kachelgrößen: keine vergleichbaren Marktkapitalisierungen verfügbar. "
                   "Verschiedene Währungen werden nicht addiert.")
    st.subheader("Unternehmen im Index")
    frame = pd.DataFrame(table_rows(bundle, period))
    numbers = ["Kursentwicklung (%)", "Kurs", "Marktkapitalisierung", "Anteil (%)"]
    styled = frame.style.format({column: "{:,.2f}" for column in numbers},
                                na_rep="NA", decimal=",", thousands=".")
    st.dataframe(styled, hide_index=True, width="stretch")
