# heatmap-builder

## Geplantes Depot-Dashboard

Das uv-Projekt enthält einen offline startbaren Streamlit-Einstieg mit ausschließlich synthetischen Beispieldaten. Fachmodule liegen unter `src/portfolio_dashboard` und importieren weder Streamlit noch externe Datenanbieter. Login, echte Datenquellen, Bewertung und die vollständige Dashboard-Oberfläche folgen in späteren Issues. Der frühere Heatmap-Prototyp bleibt separat erhalten.

- [Umsetzungsplan](PROJECT_PLAN.md)
- [Epics, Issues und Reihenfolge](docs/backlog/README.md)
- [GitHub-Issues](https://github.com/FelixSchramm/stock_treemap_open_ai/issues)
- [Codex-Cloud-Worker/Reviewer-Vorlagen](agent_architecture/README.md)
- [Architekturentscheidungen / ADRs](docs/adr/README.md)
- [Allgemeines Importschema](docs/DATA_SOURCE.md)
- [Fachlicher Datenvertrag](docs/DOMAIN_MODELS.md)

## Dashboard entwickeln und prüfen

Voraussetzungen: [uv](https://docs.astral.sh/uv/) und Python 3.12 (in `.python-version` festgehalten). `pyproject.toml` beschreibt das Projekt; `uv.lock` sperrt die tatsächlich geprüften Versionen einschließlich der Entwicklungswerkzeuge.

```bash
uv sync --locked
uv run --locked streamlit run app.py
```

Der Demo-Einstieg benötigt weder Netzwerkzugriff noch Secrets, Login oder eine persönliche Tickerliste. `PORTFOLIO_MODE=demo` ist der Standard; reale Quellen- und Login-Einstellungen werden in diesem Modus verworfen. Der Start des lokalen Servers ist keine Veröffentlichung und richtet keinen geschützten Produktivbetrieb ein.

```bash
uv run --locked ruff check .
uv run --locked pytest
uv run --locked python -c "from portfolio_dashboard.demo import demo_rows; print(demo_rows())"
```

Die Tests führen den Streamlit-Einstieg aus und prüfen die sichtbaren synthetischen Zeilen und Ansichtswechsel. Zusätzlich importiert ein frischer Python-Prozess die Fachmodule mit gesperrten UI-/Provider-Imports und Netzwerkverbindungen. In der Cloud können schreibgeschützte Home-Verzeichnisse einen abweichenden Cache benötigen: `export UV_CACHE_DIR=/workspace/.cache/uv XDG_CACHE_HOME=/workspace/.cache`.

Der deutsche Demo-Aufbau verwendet das native dunkle Streamlit-Theme, einen Depotkopf und einen ausdrücklich synthetischen, festen Datenstand. Jede neue Sitzung startet mit **Kursentwicklung / Heute**. Die Kursperioden **Heute, Woche, Monat, Jahr** bleiben beim Wechsel zu **Meine Entwicklung / Seit Kauf** und zurück erhalten; in der persönlichen Ansicht ist die Kursperiodenauswahl ausgeblendet. Die Demo zeigt in beiden Ansichten dieselben vorgegebenen Beispielwerte. Sie berechnet weder Kurs- noch persönliche Renditen und setzt keinen bestätigten tatsächlichen Einstand voraus. Die Treemap und fachlichen Berechnungen folgen separat; der reale Datenpfad bleibt bis M6-03 (#32) geschlossen.

Die kleinen UI-Helfer stehen in [`ui.py`](src/portfolio_dashboard/ui.py). `dashboard.view` und `dashboard.course_period` speichern die Auswahl für die aktuelle Sitzung; der temporäre Perioden-Widgetzustand wird getrennt gehalten, damit Streamlit ihn beim Ausblenden entfernen kann. [`demo.py`](src/portfolio_dashboard/demo.py) liefert dafür einen ausschließlich synthetischen `PortfolioSnapshot` ohne Kurse und ohne bestätigten Einstand sowie separate, vorgegebene Tabellenwerte.

### Laufzeitkonfiguration und Testdaten

[`config.py`](src/portfolio_dashboard/config.py) lädt typisierte Einstellungen aus exportierten Umgebungsvariablen. [`.env.example`](.env.example) dokumentiert ausschließlich Standardwerte und Platzhalter; die Anwendung lädt `.env`-Dateien nicht automatisch. Secret-Werte gehören in die serverseitige Secret-Verwaltung des gewählten Hosts. Sie dürfen weder in Git noch in Kommandozeilen oder Logs erscheinen. Quelldatei, Tabellenbereich und Besitzerkennungen werden ebenfalls vertraulich behandelt.

- `PORTFOLIO_MODE`: `demo` oder `production`. Demo verwendet nur synthetische Daten; im aktuellen Stand bleibt der Produktiveinstieg auch bei vollständiger Konfiguration geschlossen, bis die Besitzerprüfung implementiert ist.
- `PORTFOLIO_SOURCE=google_sheets`, `PORTFOLIO_SHEET_ID`, `PORTFOLIO_SHEET_RANGE`, `PORTFOLIO_SHEETS_CREDENTIALS_FILE`: explizite Quellkonfiguration. Der Konfigurationslader liest keine Credential-Dateien und übernimmt keine möglicherweise geerbte Plattformidentität automatisch. Laufzeit-Lesezugang folgt in M2-03/M2-04.
- `PORTFOLIO_PROVIDER=yfinance`: vorgesehener Anbieter im Produktivmodus. Demo erzwingt stattdessen den synthetischen Anbieter; Providerabrufe folgen in M3-03.
- `PORTFOLIO_QUOTES_TTL_SECONDS=60`, `PORTFOLIO_HOLDINGS_TTL_SECONDS=300`, `PORTFOLIO_HISTORY_TTL_SECONDS=21600`: positive ganze Sekunden entsprechend ADR 0005.
- `PORTFOLIO_LOGIN_PROVIDER=google` sowie die `PORTFOLIO_OWNER_*`, `PORTFOLIO_OIDC_*` und `PORTFOLIO_COOKIE_SECRET`-Felder: getrennte OIDC-/Besitzerkonfiguration. Vollständige Konfiguration ist keine Anmeldung und keine Besitzerfreigabe. Login und serverseitige Autorisierung folgen in M6-03.

Fehlende Platzhalter, ungültige Werte und fehlende Besitzerkonfiguration sperren den Produktiveinstieg. Fehlermeldungen nennen nur Feldnamen; Konfigurationsrepräsentationen geben keine vertraulichen Inhalte aus. `synthetic_import_cases()` in [`fixtures.py`](src/portfolio_dashboard/fixtures.py) stellt ausschließlich erfundene Aktien-/ETF-/Derivatefälle mit EUR/USD/GBP, fehlenden, Null- und negativen Werten bereit. Parser und Bewertung folgen separat. Die Bedeutung tatsächlicher Einstandswerte nach Teilverkäufen ist noch ungeklärt; daraus dürfen bis zur Bestätigung keine persönlichen Seit-Kauf-Renditen entstehen.

### Automatische Prüfung und PR-Ablauf

Der Workflow [CI](.github/workflows/ci.yml) prüft Pull Requests nach `agents/integration` und `main` sowie Pushes auf diese beiden Branches. Er verwendet Python 3.12, uv 0.12.23 und die gesperrten Entwicklungsabhängigkeiten. Lokal dieselben drei Schritte ausführen:

```bash
uv sync --locked
uv run --locked ruff check .
uv run --locked pytest
```

Ruff prüft Python-Fehler, ungenutzte/undefinierte Namen und Importreihenfolge im gesamten Quellbaum einschließlich Legacy-Prototyp. Pytest führt die gezielten Tests unter `tests/` aus; ein fehlgeschlagener Test beendet den CI-Job mit Fehler. `pytest-socket` sperrt IP-Sockets standardmäßig über die Pytest-Konfiguration. Lokale Unix-Sockets bleiben für Streamlits asyncio-Ereignisschleife erlaubt; der separate Fachmodul-Importtest sperrt auch diese. Fixtures bleiben synthetisch. CI erhält keine Produktions-Secrets und benötigt weder echtes Sheet noch Kursanbieterzugang. Nur Checkout, Werkzeug- und Dependency-Installation benötigen Netzwerkzugriff.

Für jede Aufgabe von `agents/integration` einen kurzen Issue-Branch anlegen und einen PR zurück nach `agents/integration` öffnen. Abhängige, noch offene PRs und deren Basis-Commit ausdrücklich im PR nennen. Vor dem Zusammenführen müssen der Check **Locked install, lint, tests (Python 3.12)** und die unabhängige fachliche Prüfung erfolgreich sein. Exakte Befehle, Commit-SHA und den tatsächlichen Actions-Lauf im PR dokumentieren; ein lokaler Erfolg ist kein CI-Nachweis. Diese Vorgabe konfiguriert keinen Branch-Schutz und erteilt keine Merge-Freigabe.

Live-Prüfungen gehören in einen getrennten, ausdrücklich autorisierten Ablauf nach Auswahl der Hosting-Umgebung und ihrer Secret-Verwaltung: echte Sheet-/Kursabrufe, Login/Besitzerzugriff und geschützte HTTPS-URL gemäß [ADR 0009](docs/adr/0009-betrieb-und-qualitaet.md). Sie laufen nicht in diesem CI-Workflow; synthetische Tests belegen keine Provider-Verfügbarkeit. Private Antworten und Credentials dürfen weder Test-Fixtures noch Logs oder PR-Anhänge werden.

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
