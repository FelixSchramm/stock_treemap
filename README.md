# heatmap-builder

## Geplantes Depot-Dashboard

Das uv-Projekt enthält einen offline startbaren Streamlit-Einstieg mit ausschließlich synthetischen Beispieldaten. Fachmodule liegen unter `src/portfolio_dashboard` und importieren weder Streamlit noch externe Datenanbieter; die UI und die separate Zugriffssperre nutzen Streamlit. Der Produktiveinstieg verlangt eine native OIDC-Sitzung und eine explizite Besitzerfreigabe. Echte Datenquellen, Bewertung und die vollständige Dashboard-Oberfläche folgen in späteren Issues. Der frühere Heatmap-Prototyp bleibt separat erhalten.

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

Der deutsche Demo-Aufbau verwendet das native dunkle Streamlit-Theme, einen Depotkopf und einen ausdrücklich synthetischen, festen Datenstand. Jede neue Sitzung startet mit **Kursentwicklung / Heute**. Die Kursperioden **Heute, Woche, Monat, Jahr** bleiben beim Wechsel zu **Meine Entwicklung / Seit Kauf** und zurück erhalten; in der persönlichen Ansicht ist die Kursperiodenauswahl ausgeblendet. Die Demo zeigt in beiden Ansichten dieselben vorgegebenen Beispielwerte. Sie berechnet weder Kurs- noch persönliche Renditen und setzt keinen bestätigten tatsächlichen Einstand voraus. Die Treemap und fachlichen Berechnungen folgen separat. Der Produktiveinstieg zeigt auch nach Besitzerfreigabe ausschließlich diese synthetische Vorschau; ein realer Ladepfad ist noch nicht angeschlossen.

Die kleinen UI-Helfer stehen in [`ui.py`](src/portfolio_dashboard/ui.py). `dashboard.view` und `dashboard.course_period` speichern die Auswahl für die aktuelle Sitzung; der temporäre Perioden-Widgetzustand wird getrennt gehalten, damit Streamlit ihn beim Ausblenden entfernen kann. [`demo.py`](src/portfolio_dashboard/demo.py) liefert dafür einen ausschließlich synthetischen `PortfolioSnapshot` ohne Kurse und ohne bestätigten Einstand sowie separate, vorgegebene Tabellenwerte.

### Laufzeitkonfiguration und Testdaten

[`config.py`](src/portfolio_dashboard/config.py) lädt typisierte Einstellungen aus exportierten Umgebungsvariablen. [`.env.example`](.env.example) dokumentiert ausschließlich Standardwerte und Platzhalter; die Anwendung lädt `.env`-Dateien nicht automatisch. Secret-Werte gehören in die serverseitige Secret-Verwaltung des gewählten Hosts. Sie dürfen weder in Git noch in Kommandozeilen oder Logs erscheinen. Quelldatei, Tabellenbereich und Besitzerkennungen werden ebenfalls vertraulich behandelt.

- `PORTFOLIO_MODE`: `demo` oder `production`. Demo verwendet nur synthetische Daten und liest weder `st.user` noch Auth-Secrets. Produktion bleibt ohne vollständige Konfiguration und gültige Besitzerfreigabe geschlossen.
- `PORTFOLIO_SOURCE=google_sheets`, `PORTFOLIO_SHEET_ID`, `PORTFOLIO_SHEET_RANGE`, `PORTFOLIO_SHEETS_CREDENTIALS_FILE`: explizite Quellkonfiguration. Der Konfigurationslader liest keine Credential-Dateien und übernimmt keine möglicherweise geerbte Plattformidentität automatisch. Laufzeit-Lesezugang folgt in M2-03/M2-04.
- `PORTFOLIO_PROVIDER=yfinance`: vorgesehener Anbieter im Produktivmodus. Demo erzwingt stattdessen den synthetischen Anbieter; Providerabrufe folgen in M3-03.
- `PORTFOLIO_QUOTES_TTL_SECONDS=60`, `PORTFOLIO_HOLDINGS_TTL_SECONDS=300`, `PORTFOLIO_HISTORY_TTL_SECONDS=21600`: positive ganze Sekunden entsprechend ADR 0005.
- `PORTFOLIO_LOGIN_PROVIDER=google` und `PORTFOLIO_OWNER_ISSUER` / `PORTFOLIO_OWNER_SUBJECT`: explizit freigegebene, vertrauliche Besitzeridentität. Sie wird kontrolliert aus validierten Claims festgelegt (#31), niemals aus dem ersten Login oder einer E-Mail abgeleitet. Der Issuer muss exakt dem konfigurierten Wert entsprechen.
- OIDC-Client, Cookie-Secret, HTTPS-Callback mit Pfad `/oauth2callback` und der feste Google-Metadatenendpunkt stehen ausschließlich im serverseitigen Streamlit-Abschnitt `[auth]`, siehe [Platzhalter](.streamlit/secrets.example.toml). `load_config(auth_settings=...)` erhält denselben Abschnitt wie Streamlits Login-/Callback-Implementierung. Frühere `PORTFOLIO_OIDC_*` / `PORTFOLIO_COOKIE_SECRET`-Umgebungsvariablen ersetzen ihn nicht. Zusätzliche Provider-/Validierungs-Overrides und Token-Freigaben werden abgelehnt. Keine Auth-Credentials werden für Sheets verwendet.

Fehlende Platzhalter, ungültige Werte und fehlende Besitzerkonfiguration sperren den Produktiveinstieg. Fehlermeldungen nennen nur Feldnamen; Konfigurationsrepräsentationen geben keine vertraulichen Inhalte aus. `synthetic_import_cases()` in [`fixtures.py`](src/portfolio_dashboard/fixtures.py) stellt ausschließlich erfundene Aktien-/ETF-/Derivatefälle mit EUR/USD/GBP, fehlenden, Null- und negativen Werten bereit. Parser und Bewertung folgen separat. Die Bedeutung tatsächlicher Einstandswerte nach Teilverkäufen ist noch ungeklärt; daraus dürfen bis zur Bestätigung keine persönlichen Seit-Kauf-Renditen entstehen.

### Besitzerprüfung und Sitzungen

[`auth.py`](src/portfolio_dashboard/auth.py) verwendet die unterstützten APIs `st.login()`, `st.user` und `st.logout()` mit dem `streamlit[auth]`-Extra (Authlib). Streamlit/Authlib prüfen den OIDC-Callback einschließlich Signatur, Issuer, Audience, State und Nonce; die Anwendung implementiert keine eigene JWT-Prüfung und nimmt keine Claims aus Browserparametern entgegen. Vor jedem Datenladen und Rendern in `app.py` prüft der Server erneut `is_logged_in`, die exakte Besitzerkombination `iss` / `sub`, die Client-Audience und endliche, zeitlich gültige `iat` / `exp`. Entscheidungen werden nicht in Session State oder einem Cache gespeichert. Künftige Bestands-/Kursabrufe und Exporte müssen unter derselben Sperre bleiben; aktuell existieren keine realen Loader oder Exporte im Dashboard.

Streamlits Identitätscookie kann 30 Tage bestehen und verlängert die ID-Token-Gültigkeit nicht. Abgelaufene Tokens sperren den nächsten App-Lauf und erfordern Abmelden/erneute Anmeldung. **Von dieser App abmelden** ruft das SDK auf und beendet den Lauf vor jedem Loader. Ein neues anonymes Fenster bleibt gesperrt; beim Öffnen eines neuen Tabs kann ein noch gültiges Identitätscookie die Anmeldung wiederherstellen, wonach erneut die Besitzerfreigabe geprüft wird. Bereits laufende Tabs besitzen eigenständige Sitzungen und werden durch die App-Abmeldung eines anderen Tabs nicht automatisch abgemeldet. Eine bereits sichtbare Anzeige wird nicht fern-gelöscht; beim nächsten Lauf gelten wieder die Freigabe- und Ablaufprüfungen. Dies ist keine globale Abmeldung vom Google-Konto.

Die synthetischen Tests prüfen den tatsächlichen Einstieg mit anonymen, fremden, fehlenden, abgelaufenen und fehlerhaften Claims sowie der erlaubten Besitzeridentität. Sie überwachen beide Demo-Loader und den Renderer, blockieren Netzwerk-/Credential-Dateizugriffe und prüfen erneute Läufe, Login-/Logout-Aufrufe, erneutes Öffnen und unabhängige Tabs. AppTest ersetzt dabei `st.user` serverseitig und simuliert keine OIDC-Weiterleitung oder Browsercookies. Echter Login, Cookies/mehrere Browser-Tabs, HTTPS und Callback am Zielhost bleiben nach #31 / #34 separat zu prüfen. Synthetische Freigabe belegt keine echte Einrichtung.

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
