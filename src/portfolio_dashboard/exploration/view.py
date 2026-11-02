"""Company analysis within the existing authorized Streamlit dashboard shell."""

import pandas as pd
import streamlit as st

from .charts import GROUPS, TITLES, AnalysisUnavailable, build
from .data import fetch
from .demo import demo_bundle

AREA = "Unternehmensanalyse"
STATE = "exploration.snapshot"
GROUP_KEY = "exploration.group"
TOPIC_KEY = "exploration.topic"


def render(*, demo=True, refresh_clicked=False):
    st.subheader("Microsoft · Unternehmensanalyse")
    st.caption("Geschäftsmodell · Kapitalqualität · Erwartungen · Kursrisiko")
    key = STATE + (".demo" if demo else ".live")
    if demo:
        st.info(
            "Nur synthetische Beispieldaten – auch bei bekannten Symbolen keine echten Kurse oder Geschäftszahlen."
        )
        if key not in st.session_state or refresh_clicked:
            st.session_state[key] = demo_bundle()
    elif refresh_clicked or st.button(
        "Analysedaten laden", disabled=key in st.session_state
    ):
        with st.spinner("Markt- und Unternehmensdaten werden abgerufen …"):
            try:
                result = fetch()
                if not result["prices"].get("MSFT", pd.DataFrame()).empty:
                    st.session_state[key] = result
                else:
                    st.warning(
                        "Microsoft-Kurse fehlen. Ein vorhandener Stand bleibt erhalten."
                    )
            except Exception:
                st.warning(
                    "Abruf fehlgeschlagen. Ein vorhandener Stand bleibt erhalten; Aktualisieren versucht es erneut."
                )
    if key not in st.session_state:
        st.info(
            "Lade die Unternehmensanalyse. Die Originalanalyse untersucht Microsoft; der Marktvergleich ergänzt Oracle, Alibaba HK und SAP."
        )
        return
    bundle = st.session_state[key]
    stamp = pd.Timestamp(bundle["retrieved_at"])
    prefix = "Synthetischer Stand" if demo else "Abrufstand"
    st.caption(
        f"{prefix}: {stamp:%d.%m.%Y, %H:%M UTC} · Originalanalyse: Financial Pipeline V4 · CI 1.3"
    )
    if not demo and pd.Timestamp.now(tz="UTC") - stamp > pd.Timedelta(hours=1):
        st.warning(
            "Dieser Snapshot ist älter als eine Stunde. Mit ‚Aktualisieren‘ lädst du einen neuen Stand."
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
            "Für diese Analyse fehlen gültige oder zueinander passende Daten. Wähle ein anderes Thema oder aktualisiere den Stand. Fehlende Zahlen werden nicht ersetzt."
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
            "Geschäftszahlen: Microsoft, USD, Geschäftsjahresende Juni. Währungs- und Renditewahl gelten nur im Marktvergleich. Snapshot-Daten wie Analysten und Optionen sind keine historische Zeitreihe. Die ursprünglichen V1–V4-Analysen bleiben unverändert in der Financial Pipeline."
        )
        st.markdown(
            "[Originalanalyse und Methodik](https://github.com/FelixSchramm/real-time-stock-market-pipeline/tree/52133fcaba5a734567880cab191a826d1ade6bab/data_exploration/v4)"
        )
