"""Company analysis within the existing authorized Streamlit dashboard shell.

Layout follows the start page: company heading with pickers, a KPI row, one
fixed control bar and all topics of the chosen area as cards in a 2-column grid.
"""

from dataclasses import replace
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .charts import GROUPS, TITLES, AnalysisUnavailable, build
from .data import fetch
from .demo import demo_bundle
from .selection import DEFAULT_COMPANIES, Catalog, combine_companies, demo_catalog
from .summary import DATA_ERRORS, headline, key_figures

AREA = "Analyse"
STATE = "exploration.snapshot"
GROUP_KEY = "exploration.group"
CURRENCY_KEY = "exploration.currency"
RETURN_KEY = "exploration.return"
MONTHS_KEY = "exploration.months"

CATALOG_KEY = "exploration.catalog"
COMPANY_KEY = "exploration.company"
COMPARE_KEY = "exploration.comparisons"

CARD_HEIGHT = 280
RETURN_BASES = ("Gesamtrendite-Näherung", "Kursrendite")
MONTHS = (12, 36, 60)
GENERIC_FAILURE = (
    "n. v. – Yahoo liefert für diese Analyse keine ausreichenden oder zueinander "
    "passenden Daten. Fehlende Zahlen werden nicht ersetzt."
)
PLOTLY_CONFIG = {"displaylogo": False, "scrollZoom": False}


def render(*, demo=True, refresh_clicked=False, load_companies=None):
    """Render the analysis area below the dashboard header.

    :param demo: True uses synthetic data only, never Sheet or Yahoo
    :param refresh_clicked: reload the company list and the current analysis
    :param load_companies: callable reading the Sheet catalog, or None
    :return: None
    """
    _render_analysis(
        demo=demo, refresh_clicked=refresh_clicked, load_companies=load_companies
    )
    st.divider()
    glossary = Path(__file__).with_name("glossary.md").read_text(encoding="utf-8")
    for section in ("\n" + glossary).split("\n## ")[1:]:
        title, _, body = section.partition("\n")
        with st.expander(f"Glossar – {title}"):
            st.markdown(body.strip())


def _load_catalog(*, demo, refresh_clicked, load_companies, catalog_key):
    """Read the company list once per session or on explicit refresh.

    :param demo: use the synthetic catalog
    :param refresh_clicked: force a new read
    :param load_companies: Sheet loader or None
    :param catalog_key: session key including the mode suffix
    :return: Catalog
    """
    if catalog_key not in st.session_state or refresh_clicked:
        previous = st.session_state.get(catalog_key, Catalog())
        if demo:
            st.session_state[catalog_key] = demo_catalog()
        elif load_companies is None:
            st.session_state[catalog_key] = Catalog(
                notes=(
                    "Sheet-Anbindung ist nicht eingerichtet. Die Standardauswahl bleibt verfügbar.",
                )
            )
        else:
            with st.spinner("Unternehmensliste aus dem Sheet wird gelesen …"):
                try:
                    st.session_state[catalog_key] = load_companies(previous)
                except Exception:
                    st.session_state[catalog_key] = replace(
                        previous,
                        notes=(
                            "Sheet-Liste konnte nicht geladen werden. Standardauswahl und zuvor geladene Einträge bleiben verfügbar. ‚Aktualisieren‘ versucht es erneut.",
                        ),
                    )
    return st.session_state.get(catalog_key, Catalog())


def _pick_companies(companies):
    """Render company and comparison pickers.

    :param companies: selectable companies keyed by ``repr(key)``
    :return: (company, comparisons)
    """
    if st.session_state.get(COMPANY_KEY) not in companies:
        st.session_state.pop(COMPANY_KEY, None)
    selected = st.selectbox(
        "Unternehmen",
        list(companies),
        format_func=lambda key: companies[key].label,
        key=COMPANY_KEY,
    )
    company = companies[selected]
    if company.reason:
        return company, ()
    candidates = {
        key: c
        for key, c in companies.items()
        if c.symbol != company.symbol and not c.reason
    }
    current = st.session_state.get(COMPARE_KEY)
    if current is not None:
        st.session_state[COMPARE_KEY] = [key for key in current if key in candidates]
    compare_keys = st.multiselect(
        "Vergleichswerte (max. 3)",
        list(candidates),
        default=(
            [repr(c.key) for c in DEFAULT_COMPANIES if repr(c.key) in candidates][:3]
            if current is None
            else None
        ),
        format_func=lambda key: candidates[key].label,
        max_selections=3,
        key=COMPARE_KEY,
    )
    return company, tuple(candidates[key] for key in compare_keys)


def _load_bundle(*, demo, refresh_clicked, company, comparisons, mode):
    """Load or reuse the session snapshot of the selection.

    :param demo: synthetic data only
    :param refresh_clicked: force a new provider request
    :param company: selected company
    :param comparisons: comparison companies
    :param mode: ".demo" or ".live" session suffix
    :return: (bundle or None, failure text or None)
    """
    signature = (company, comparisons)
    snapshots = st.session_state.setdefault(STATE + mode, {})
    failures = st.session_state.setdefault(STATE + ".failures" + mode, {})
    if refresh_clicked or (signature not in snapshots and signature not in failures):
        with st.spinner("Analysedaten der gewählten Aktie werden geladen …"):
            try:
                result = (
                    demo_bundle(company, comparisons)
                    if demo
                    else fetch(company, comparisons)
                )
                if (
                    result.get("symbol") == company.symbol
                    and not result["prices"].get(company.symbol, pd.DataFrame()).empty
                ):
                    snapshots[signature] = result
                    failures.pop(signature, None)
                    # Bound memory usage without sharing data across sessions.
                    while len(snapshots) > 8:
                        del snapshots[next(iter(snapshots))]
                else:
                    failures[signature] = (
                        "n. v. – Kurse fehlen oder passen nicht zur Zuordnung. Ein vorhandener Stand dieser Auswahl bleibt erhalten. ‚Aktualisieren‘ versucht es erneut."
                    )
            except Exception:
                failures[signature] = (
                    "n. v. – Abruf fehlgeschlagen. Ein vorhandener Stand dieser Auswahl bleibt erhalten. ‚Aktualisieren‘ versucht es erneut."
                )
            while len(failures) > 8:
                del failures[next(iter(failures))]
    return snapshots.get(signature), failures.get(signature)


def _render_analysis(*, demo, refresh_clicked, load_companies):
    mode = ".demo" if demo else ".live"
    catalog = _load_catalog(
        demo=demo,
        refresh_clicked=refresh_clicked,
        load_companies=load_companies,
        catalog_key=CATALOG_KEY + mode,
    )
    companies = {
        repr(c.key): c for c in combine_companies(DEFAULT_COMPANIES, catalog.companies)
    }
    heading, pickers = st.columns((3, 2), vertical_alignment="center")
    with pickers:
        company, comparisons = _pick_companies(companies)
    with heading:
        st.header(company.name, anchor=False)
        details = [company.symbol, company.currency, "Yahoo Finance"]
        st.caption("Analyse · " + " · ".join(str(item) for item in details if item))
    if demo:
        st.info(
            "Nur synthetische Beispieldaten – auch für Microsoft, Oracle, Alibaba und SAP. Kein Sheet- oder Yahoo-Zugriff."
        )
    for note in catalog.notes:
        st.warning(note)
    if company.reason:
        st.info(company.reason)
        return
    bundle, failure = _load_bundle(
        demo=demo,
        refresh_clicked=refresh_clicked,
        company=company,
        comparisons=comparisons,
        mode=mode,
    )
    if failure:
        st.warning(failure)
    if bundle is None:
        st.info(
            "Für diese Auswahl sind noch keine Analysedaten verfügbar. Über ‚Aktualisieren‘ kannst du den Abruf erneut starten."
        )
        return
    stamp = pd.Timestamp(bundle["retrieved_at"])
    stamps = [
        f"{'Synthetischer Stand' if demo else 'Abrufstand'}: {stamp:%d.%m.%Y, %H:%M UTC}"
    ]
    for label, listed in (
        ("Depot", catalog.depot_at),
        ("Watchlist", catalog.watchlist_at),
    ):
        if listed:
            stamps.append(f"Listenstand {label}: {listed:%d.%m.%Y, %H:%M UTC}")
    st.caption(
        " · ".join(stamps) + " · Originalanalyse: Financial Pipeline V4 · CI 1.3"
    )
    if not demo and pd.Timestamp.now(tz="UTC") - stamp > pd.Timedelta(hours=1):
        st.warning(
            "Dieser Snapshot ist älter als eine Stunde. ‚Aktualisieren‘ lädt einen neuen Stand."
        )
    _render_key_figures(bundle)
    st.divider()
    group, currency, total, months = _render_controls()
    topics = GROUPS[group]
    for start in range(0, len(topics), 2):
        columns = st.columns(2)
        for column, topic in zip(columns, topics[start : start + 2]):
            with column:
                _render_card(topic, bundle, currency, total, months, demo)
    with st.expander("Bedienung und Einordnung"):
        st.markdown(
            "Hover zeigt Einzelwerte. Ziehen zoomt in den Chart; Doppelklick setzt den Ausschnitt zurück. Ein Klick auf einen Legendeneintrag blendet die Reihe aus. Weitere Panels, alle Werte als Tabelle und der CSV-Download stehen in jeder Karte unter ‚Details‘."
        )
        st.markdown(
            "Geschäftszahlen verwenden die gemeldete Berichtswährung und die jeweiligen Perioden. Nicht angebundene Datenquellen bleiben als solche sichtbar. Währung, Renditebasis und Zeitraum gelten für Kurs- und Marktvergleiche. Snapshot-Daten wie Analysten und Optionen sind keine historische Zeitreihe. Die ursprünglichen V1–V4-Analysen bleiben unverändert in der Financial Pipeline."
        )
        st.markdown(
            "[Originalanalyse und Methodik](https://github.com/FelixSchramm/real-time-stock-market-pipeline/tree/52133fcaba5a734567880cab191a826d1ade6bab/data_exploration/v4)"
        )


def _render_key_figures(bundle):
    """Render the KPI row; any failure leaves the tiles at "n. v.".

    :param bundle: analysis bundle
    :return: None
    """
    try:
        figures = key_figures(bundle)
    except DATA_ERRORS:
        st.caption("Kennzahlen: n. v. – Kursdaten reichen nicht für die Kopfzeile.")
        return
    for column, figure in zip(st.columns(len(figures)), figures):
        column.metric(
            figure.label,
            figure.value,
            figure.delta,
            chart_data=figure.chart,
            chart_type=figure.chart_type,
            help=figure.help,
        )


def _render_controls():
    """Render the fixed control bar valid for every chart of the page.

    :return: (group, currency, total, months)
    """
    group = (
        st.segmented_control(
            "Bereich", tuple(GROUPS), default="Überblick", key=GROUP_KEY
        )
        or "Überblick"
    )
    controls = st.columns((1, 2, 2))
    with controls[0]:
        currency = (
            st.segmented_control(
                "Währung", ("EUR", "USD"), default="EUR", key=CURRENCY_KEY
            )
            or "EUR"
        )
    with controls[1]:
        basis = (
            st.segmented_control(
                "Renditebasis", RETURN_BASES, default=RETURN_BASES[0], key=RETURN_KEY
            )
            or RETURN_BASES[0]
        )
    with controls[2]:
        months = (
            st.segmented_control(
                "Zeitraum",
                MONTHS,
                default=12,
                format_func=lambda n: f"{n // 12} Jahr" + ("e" if n > 12 else ""),
                key=MONTHS_KEY,
            )
            or 12
        )
    st.caption(
        "Währung, Renditebasis und Zeitraum gelten für Kurs- und Marktvergleiche. "
        "Geschäftszahlen bleiben in Berichtswährung; Monatsrenditen zeigen stets "
        "die letzten zwölf vollständigen Monate."
    )
    return group, currency, basis == RETURN_BASES[0], months


def _render_card(topic, bundle, currency, total, months, demo):
    """Render one topic: main chart, takeaway and collapsed details.

    :param topic: topic number in ``TITLES``
    :param bundle: analysis bundle
    :param currency: comparison currency
    :param total: True for the total-return approximation
    :param months: comparison window in months
    :param demo: label the source as synthetic
    :return: None
    """
    with st.container(border=True):
        st.markdown(f"**{TITLES[topic]}**")
        try:
            analysis = build(topic, bundle, currency, total, months)
        except AnalysisUnavailable as exc:
            st.warning(str(exc))
            return
        except DATA_ERRORS:
            st.warning(GENERIC_FAILURE)
            if bundle.get("errors"):
                with st.expander("Datenstatus"):
                    for resource, reason in bundle["errors"].items():
                        st.write(f"{resource}: {reason}")
            return
        main = next(
            (i for i, p in enumerate(analysis.panels) if p.figure is not None), None
        )
        if main is not None:
            _chart(analysis.panels[main].figure, f"exploration.{topic}.{main}")
        else:
            st.dataframe(analysis.panels[0].data, width="stretch", height=CARD_HEIGHT)
        symbol = bundle["symbol"]
        st.caption(headline(analysis, (symbol, bundle.get("names", {}).get(symbol))))
        with st.expander("Details"):
            st.caption(
                "Quelle: "
                + ("Synthetische Demonstration" if demo else analysis.source)
                + " · "
                + analysis.note
            )
            for index, panel in enumerate(analysis.panels):
                st.markdown(f"**{panel.title}**")
                if panel.figure is not None and index != main:
                    _chart(panel.figure, f"exploration.{topic}.{index}")
                st.dataframe(panel.data, width="stretch")
                st.download_button(
                    "CSV herunterladen",
                    panel.data.to_csv().encode("utf-8-sig"),
                    file_name=f"analyse_{topic:02d}_{index}.csv",
                    mime="text/csv",
                    key=f"exploration.csv.{topic}.{index}",
                )


def _chart(figure, key):
    """Show a copy of a topic figure at card height; charts.py stays untouched.

    :param figure: Plotly figure from ``charts.build``
    :param key: unique widget key
    :return: None
    """
    card = go.Figure(figure)
    card.update_layout(height=CARD_HEIGHT, margin=dict(l=8, r=12, t=40, b=24))
    st.plotly_chart(card, theme=None, width="stretch", key=key, config=PLOTLY_CONFIG)
