"""Company analysis within the existing authorized Streamlit dashboard shell."""

from dataclasses import replace
from pathlib import Path

import pandas as pd
import streamlit as st

from .charts import GROUPS, TITLES, AnalysisUnavailable, build
from .data import fetch
from .demo import demo_bundle
from .selection import DEFAULT_COMPANIES, Catalog, combine_companies, demo_catalog

AREA = "Analyse"
STATE = "exploration.snapshot"
GROUP_KEY = "exploration.group"
TOPIC_KEY = "exploration.topic"


CATALOG_KEY = "exploration.catalog"
COMPANY_KEY = "exploration.company"
COMPARE_KEY = "exploration.comparisons"


def render(*, demo=True, refresh_clicked=False, load_companies=None):
    st.subheader(AREA)
    st.caption(
        "Standardauswahl und Unternehmen aus Depot und Watchlist · Yahoo Finance"
    )
    _render_analysis(
        demo=demo, refresh_clicked=refresh_clicked, load_companies=load_companies
    )
    st.divider()
    glossary = Path(__file__).with_name("glossary.md").read_text(encoding="utf-8")
    for section in ("\n" + glossary).split("\n## ")[1:]:
        title, _, body = section.partition("\n")
        with st.expander(f"Glossar – {title}"):
            st.markdown(body.strip())


def _render_analysis(*, demo, refresh_clicked, load_companies):
    mode = ".demo" if demo else ".live"
    catalog_key = CATALOG_KEY + mode
    if demo:
        st.info(
            "Nur synthetische Beispieldaten – auch für Microsoft, Oracle, Alibaba und SAP. Kein Sheet- oder Yahoo-Zugriff."
        )
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
    catalog = st.session_state.get(catalog_key, Catalog())
    for note in catalog.notes:
        st.warning(note)
    for label, stamp in (
        ("Depot", catalog.depot_at),
        ("Watchlist", catalog.watchlist_at),
    ):
        if stamp:
            st.caption(f"Listenstand {label}: {stamp:%d.%m.%Y, %H:%M UTC}")
    companies = {
        repr(c.key): c for c in combine_companies(DEFAULT_COMPANIES, catalog.companies)
    }
    if st.session_state.get(COMPANY_KEY) not in companies:
        st.session_state.pop(COMPANY_KEY, None)
    selected = st.selectbox(
        "Unternehmen",
        list(companies),
        format_func=lambda key: companies[key].label,
        key=COMPANY_KEY,
    )
    company = companies[selected]
    st.markdown(f"**{company.name}**")
    if company.reason:
        st.info(company.reason)
        return
    candidates = {
        key: c
        for key, c in companies.items()
        if c.symbol != company.symbol and not c.reason
    }
    current = st.session_state.get(COMPARE_KEY)
    if current is not None:
        st.session_state[COMPARE_KEY] = [key for key in current if key in candidates]
    compare_keys = st.multiselect(
        "Vergleichsunternehmen (max. 3)",
        list(candidates),
        default=[repr(c.key) for c in DEFAULT_COMPANIES if repr(c.key) in candidates][
            :3
        ]
        if current is None
        else None,
        format_func=lambda key: candidates[key].label,
        max_selections=3,
        key=COMPARE_KEY,
    )
    comparisons = tuple(candidates[key] for key in compare_keys)
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
    if signature in failures:
        st.warning(failures[signature])
    bundle = snapshots.get(signature)
    if bundle is None:
        st.info(
            "Für diese Auswahl sind noch keine Analysedaten verfügbar. Über ‚Aktualisieren‘ kannst du den Abruf erneut starten."
        )
        return
    stamp = pd.Timestamp(bundle["retrieved_at"])
    prefix = "Synthetischer Stand" if demo else "Abrufstand"
    st.caption(
        f"{prefix}: {stamp:%d.%m.%Y, %H:%M UTC} · Originalanalyse: Financial Pipeline V4 · CI 1.3"
    )
    if not demo and pd.Timestamp.now(tz="UTC") - stamp > pd.Timedelta(hours=1):
        st.warning(
            "Dieser Snapshot ist älter als eine Stunde. ‚Aktualisieren‘ lädt einen neuen Stand."
        )
    group = st.radio("Analysebereich", list(GROUPS), horizontal=True, key=GROUP_KEY)
    topic = st.selectbox(
        "Fragestellung",
        GROUPS[group],
        format_func=TITLES.get,
        key=f"{TOPIC_KEY}.{group}",
    )
    currency, total, months = "EUR", True, 12
    if topic in {5, 6, 7, 8, 13}:
        controls = st.columns(3)
        currency = controls[0].selectbox(
            "Vergleichswährung", ["EUR", "USD"], key="exploration.currency"
        )
        total = (
            controls[1].selectbox(
                "Renditebasis",
                ["Gesamtrendite-Näherung", "Kursrendite"],
                key="exploration.return",
            )
            == "Gesamtrendite-Näherung"
        )
        months = controls[2].selectbox(
            "Zeitraum",
            [12, 36, 60],
            format_func=lambda n: f"{n // 12} Jahr" + ("e" if n > 12 else ""),
            key="exploration.months",
            disabled=topic == 6,
        )
    if topic in {1, 2}:
        months = st.selectbox(
            "Kurszeitraum",
            [6, 12, 36, 60],
            format_func=lambda n: f"{n} Monate",
            index=1,
        )
    st.divider()
    st.markdown(f"### {TITLES[topic]}")
    try:
        analysis = build(topic, bundle, currency, total, months)
    except AnalysisUnavailable as exc:
        st.warning(str(exc))
        return
    except (
        KeyError,
        ValueError,
        IndexError,
        TypeError,
        AttributeError,
        ZeroDivisionError,
    ):
        st.warning(
            "n. v. – Yahoo liefert für diese Analyse keine ausreichenden oder zueinander passenden Daten. Wähle ein anderes Thema oder aktualisiere den Stand. Fehlende Zahlen werden nicht ersetzt."
        )
        if bundle.get("errors"):
            with st.expander("Datenstatus"):
                for resource, reason in bundle["errors"].items():
                    st.write(f"{resource}: {reason}")
        return
    st.caption("Quelle: " + ("Synthetische Demonstration" if demo else analysis.source))
    st.caption(analysis.note)
    for index, panel in enumerate(analysis.panels):
        with st.container(border=True):
            st.markdown(f"**{panel.title}**")
            if panel.figure is not None:
                st.plotly_chart(
                    panel.figure,
                    theme=None,
                    width="stretch",
                    key=f"exploration.{topic}.{index}",
                    config={"displaylogo": False, "scrollZoom": False},
                )
                with st.expander("Daten ansehen und herunterladen"):
                    st.dataframe(panel.data, width="stretch")
                    st.download_button(
                        "CSV herunterladen",
                        panel.data.to_csv().encode("utf-8-sig"),
                        file_name=f"analyse_{topic:02d}_{index}.csv",
                        mime="text/csv",
                        key=f"exploration.csv.{topic}.{index}",
                    )
            else:
                st.dataframe(panel.data, width="stretch")
                st.download_button(
                    "CSV herunterladen",
                    panel.data.to_csv().encode("utf-8-sig"),
                    file_name=f"analyse_{topic:02d}_{index}.csv",
                    mime="text/csv",
                    key=f"exploration.csv.{topic}.{index}",
                )
    with st.expander("Bedienung und Einordnung"):
        st.markdown(
            "Hover zeigt Einzelwerte. Ziehen zoomt in den Chart; Doppelklick setzt den Ausschnitt zurück. Ein Klick auf einen Legendeneintrag blendet die Reihe aus. Alle Werte stehen zusätzlich als Tabelle bereit."
        )
        st.markdown(
            "Geschäftszahlen verwenden die gemeldete Berichtswährung und die jeweiligen Perioden. Nicht angebundene Datenquellen bleiben als solche sichtbar. Währungs- und Renditewahl gelten nur im Marktvergleich. Snapshot-Daten wie Analysten und Optionen sind keine historische Zeitreihe. Die ursprünglichen V1–V4-Analysen bleiben unverändert in der Financial Pipeline."
        )
        st.markdown(
            "[Originalanalyse und Methodik](https://github.com/FelixSchramm/real-time-stock-market-pipeline/tree/52133fcaba5a734567880cab191a826d1ade6bab/data_exploration/v4)"
        )
