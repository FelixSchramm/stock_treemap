# ADR 0002: Python mit uv, Streamlit und Plotly

- **Datum:** 2026-10-23
- **Status:** Angenommen
- **Herkunft:** Python/uv vom Nutzer; Framework und Struktur technisch.

## Kontext

Das Projekt verwendet Python, pandas, Plotly und yfinance. Benötigt werden Treemap, Bedienelemente, Login und Online-Nutzung. uv ist als Projekt- und Paketmanager vorgegeben.

## Entscheidung

uv verwaltet Python-Version, Umgebung und Abhängigkeiten über `.python-version`, `pyproject.toml` und `uv.lock`. Konkrete Versionen werden nach einem Kompatibilitätscheck bei der Umsetzung gesperrt.

Streamlit stellt die Oberfläche bereit, Plotly die Treemap. Import, Instrumentzuordnung, Kurse, Cache und Berechnungen liegen in separaten Python-Modulen. pandas verarbeitet Tabellen; fachliche Funktionen bleiben ohne UI und Netzwerk testbar. Finanzwerte werden nicht vorzeitig gerundet.

Der Produktivdienst wird als Python-Container vorbereitet. Builds verwenden `uv sync --locked`. Geplanter Entwicklungsstart: `uv run streamlit run app.py`.

## Alternativen

- FastAPI mit eigenem JavaScript-Frontend: mehr Freiheit für umfangreiche mobile Funktionen, derzeit zusätzlicher Aufwand.
- Statische Plotly-HTML-Datei: allein kein geschützter serverseitiger Datenabruf.
- JavaScript-Worker als einzige Laufzeit: führt den vorhandenen Python-Datenpfad nicht direkt aus; ein solcher Wechsel benötigt eine neue Entscheidung.

## Konsequenzen

Hosting muss Python-Prozesse und WebSockets unterstützen. Eine installierbare Offline-App wird nicht aus der Frameworkwahl abgeleitet. Bei später verbindlicher nativer App oder umfangreicher PWA kann das Frontend ersetzt und die Fachlogik weiterverwendet werden.

## Referenzen

- [uv-Projekte](https://docs.astral.sh/uv/guides/projects/)
- [Plotly in Streamlit](https://docs.streamlit.io/develop/api-reference/charts/st.plotly_chart)
- [Streamlit im Docker-Container](https://docs.streamlit.io/deploy/tutorials/docker)
