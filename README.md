# heatmap-builder

## Geplantes Depot-Dashboard

Das uv-Projekt enthält einen offline startbaren Streamlit-Einstieg mit ausschließlich synthetischen Beispieldaten. Fachmodule liegen unter `src/portfolio_dashboard` und importieren weder Streamlit noch externe Datenanbieter. Login, echte Datenquellen, Bewertung und die vollständige Dashboard-Oberfläche folgen in späteren Issues. Der frühere Heatmap-Prototyp bleibt separat erhalten.

- [Umsetzungsplan](PROJECT_PLAN.md)
- [Epics, Issues und Reihenfolge](docs/backlog/README.md)
- [GitHub-Issues](https://github.com/FelixSchramm/stock_treemap_open_ai/issues)
- [Codex-Cloud-Worker/Reviewer-Vorlagen](agent_architecture/README.md)
- [Architekturentscheidungen / ADRs](docs/adr/README.md)
- [Allgemeines Importschema](docs/DATA_SOURCE.md)

## Dashboard entwickeln und prüfen

Voraussetzungen: [uv](https://docs.astral.sh/uv/) und Python 3.12 (in `.python-version` festgehalten). `pyproject.toml` beschreibt das Projekt; `uv.lock` sperrt die tatsächlich geprüften Versionen einschließlich der Entwicklungswerkzeuge.

```bash
uv sync --locked
uv run --locked streamlit run app.py
```

Der Demo-Einstieg benötigt weder Netzwerkzugriff noch Secrets, Login oder eine persönliche Tickerliste. Er liest keine echte Depotkonfiguration. Der Start des lokalen Servers ist keine Veröffentlichung und richtet keinen geschützten Produktivbetrieb ein.

```bash
uv run --locked pytest
uv run --locked python -c "from portfolio_dashboard.demo import demo_rows; print(demo_rows())"
```

Die Tests führen den Streamlit-Einstieg aus und prüfen die sichtbaren synthetischen Zeilen. Zusätzlich importiert ein frischer Python-Prozess die Fachmodule mit gesperrten UI-/Provider-Imports und Netzwerkverbindungen. In der Cloud können schreibgeschützte Home-Verzeichnisse einen abweichenden Cache benötigen: `export UV_CACHE_DIR=/workspace/.cache/uv XDG_CACHE_HOME=/workspace/.cache`.

## Vorhandener Prototyp (Legacy)

Das bestehende `heatmap.py` erzeugt eine Aktien-Heatmap (Treemap): Kachelgroesse = Boersenwert, Farbe = Tagesveraenderung. `requirements.txt` gilt ausschließlich für diesen Legacy-Prototyp; es ist kein zweites Dependency-Set für das Dashboard. Alternativ lässt sich der Prototyp mit den gesperrten Projektversionen über `uv sync --locked --extra prototype` und `uv run --locked --extra prototype python heatmap.py` betreiben (persönliche `tickers.txt` und Yahoo-Netzwerkzugriff erforderlich).

### Legacy-Nutzung

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp tickers.example.txt tickers.txt   # anschließend eigene Ticker eintragen
python heatmap.py
```

Das Skript erzeugt `heatmap.html` und oeffnet sie im Browser.

### Eigene Aktien im Legacy-Prototyp

`tickers.example.txt` nach `tickers.txt` kopieren und eigene Ticker eintragen, ein Ticker pro Zeile. Die persönliche Liste bleibt lokal und wird nicht versioniert.

### Hinweise zum Legacy-Prototyp

- Datenquelle ist `yfinance` (inoffiziell, Kurse koennen verzoegert sein).
- Keine Anlageberatung.


Sheet-ID und Dokumentlinks gehören in lokale Konfiguration oder `docs/private/`, nicht in das Repository. `docs/DATA_SOURCE.md` beschreibt ausschließlich das allgemeine Importschema.
