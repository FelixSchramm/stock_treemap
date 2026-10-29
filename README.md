# heatmap-builder

## Geplantes Depot-Dashboard

Das uv-Projekt enthält einen offline startbaren Streamlit-Einstieg mit ausschließlich synthetischen Beispieldaten. Fachmodule liegen unter `src/portfolio_dashboard` und importieren weder Streamlit noch externe Datenanbieter; die UI und die separate Zugriffssperre nutzen Streamlit. Der Produktiveinstieg verlangt eine native OIDC-Sitzung und eine explizite Besitzerfreigabe. Echte Datenquellen, Bewertung und die vollständige Dashboard-Oberfläche folgen in späteren Issues.

- [Umsetzungsplan](PROJECT_PLAN.md)
- [Epics, Issues und Reihenfolge](docs/backlog/README.md)
- [GitHub-Issues](https://github.com/FelixSchramm/stock_treemap_open_ai/issues)
- [Codex-Cloud-Worker/Reviewer-Vorlagen](agent_architecture/README.md)
- [Architekturentscheidungen / ADRs](docs/adr/README.md)
- [Allgemeines Importschema](docs/DATA_SOURCE.md)
- [Fachlicher Datenvertrag](docs/DOMAIN_MODELS.md)
- [Sheet-Parser und Importgrenzen](docs/SHEET_PARSER.md)
- [Geprüfte Instrument- und Provider-Zuordnung](docs/INSTRUMENT_MAPPING.md)
- [Lesender Sheets-Adapter und Fehlerstatus](docs/SHEETS_ADAPTER.md)
- [Kurs-, Historien- und FX-Providervertrag](docs/MARKET_DATA.md)
- [EUR-Bewertung und Gewichtung](docs/EUR_VALUATION.md)
- [Persönliche Rendite seit Kauf](docs/PERSONAL_RETURNS.md)
- [Kursperioden (Heute, Woche, Monat, Jahr)](docs/PRICE_PERIODS.md)
- [Fachliche Regressionen, Abnahmewerte und Toleranzen](docs/DOMAIN_REGRESSIONS.md)
- [Container und Betriebsparameter](docs/CONTAINER.md)
- [Betrieb auf Streamlit Community Cloud](docs/COMMUNITY_CLOUD.md)
- [Treemap-Wertmodell](docs/TREEMAP.md)
- [Mobile Bedienung und Barrierefreiheit](docs/MOBILE_ACCESSIBILITY.md)

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

Die Tests führen den Streamlit-Einstieg aus und prüfen die sichtbaren synthetischen Zeilen und Zeitraumwechsel. Zusätzlich importiert ein frischer Python-Prozess die Fachmodule mit gesperrten UI-/Provider-Imports und Netzwerkverbindungen. In der Cloud können schreibgeschützte Home-Verzeichnisse einen abweichenden Cache benötigen: `export UV_CACHE_DIR=/workspace/.cache/uv XDG_CACHE_HOME=/workspace/.cache`.

Der deutsche Demo-Aufbau verwendet das native dunkle Streamlit-Theme, einen Depotkopf und einen ausdrücklich synthetischen, festen Datenstand. Jede neue Sitzung startet mit **Kursentwicklung / Heute**. Die Auswahl **Zeitraum** bietet die Kursperioden **Heute, Woche, Monat, Jahr** und als fünfte Option **Seit Kauf** (Meine Entwicklung); die gewählte Option bleibt für die laufende Sitzung erhalten. Die Demo zeigt eine Treemap mit festen, erfundenen EUR-Preisen und Kursprozenten ([`docs/TREEMAP.md`](docs/TREEMAP.md)); die Seit-Kauf-Ansicht bleibt ohne verifizierten Einstand „n. v.“. Sie berechnet keine echten Renditen und setzt keinen bestätigten tatsächlichen Einstand voraus. Der Produktiveinstieg zeigt nach Besitzerfreigabe das eigene Depot (siehe [Produktivmodus](#produktivmodus-mit-login)).

Die kleinen UI-Helfer stehen in [`ui.py`](src/portfolio_dashboard/ui.py). `dashboard.course_period` speichert den gewählten Zeitraum für die aktuelle Sitzung; das Auswahlfeld hat einen eigenen Widget-Schlüssel und wird bei jedem Lauf daraus vorbelegt. [`demo.py`](src/portfolio_dashboard/demo.py) liefert dafür einen ausschließlich synthetischen `PortfolioSnapshot` mit festen Sheet-Preisen und ohne bestätigten Einstand sowie separate, vorgegebene Tabellenwerte.

Direkt unter **Bewerteter Depotwert** blendet der standardmäßig aktive Schalter **Positionen ohne Kursentwicklung ausblenden** die Treemap-Kacheln ohne Prozentwert der gewählten Kursperiode aus. Der Hinweis nennt die Anzahl dieser bewerteten Positionen und ihren EUR-Wert im Gesamtbestand. Depotwert, Gewichte und Kennzahlen bleiben vollständig; Tabelle, Positionsdetails und Datenstatus zeigen die Positionen weiterhin. Bei **Seit Kauf** gibt es diesen Filter nicht.

### Laufzeitkonfiguration und Testdaten

[`config.py`](src/portfolio_dashboard/config.py) lädt typisierte Einstellungen aus exportierten Umgebungsvariablen. [`.env.example`](.env.example) dokumentiert ausschließlich Standardwerte und Platzhalter; die Anwendung lädt `.env`-Dateien nicht automatisch. Secret-Werte gehören in die serverseitige Secret-Verwaltung des gewählten Hosts. Sie dürfen weder in Git noch in Kommandozeilen oder Logs erscheinen. Quelldatei, Tabellenbereich und Besitzerkennungen werden ebenfalls vertraulich behandelt.

- `PORTFOLIO_MODE`: `demo` oder `production`. Demo verwendet nur synthetische Daten und liest weder `st.user` noch Auth-Secrets. Produktion bleibt ohne vollständige Konfiguration und gültige Besitzerfreigabe geschlossen.
- `PORTFOLIO_SOURCE=google_sheets`, `PORTFOLIO_SHEET_ID`, `PORTFOLIO_SHEET_RANGE`: explizite Quellkonfiguration. Der Service-Account-Schlüssel kommt im lokalen Modus aus der Datei `PORTFOLIO_SHEETS_CREDENTIALS_FILE`, im Produktivmodus ausschließlich aus dem serverseitigen Secret-Abschnitt `[sheets_service_account]`. Der Konfigurationslader liest keine Credential-Dateien und übernimmt keine möglicherweise geerbte Plattformidentität automatisch. Der [Sheets-Adapter](docs/SHEETS_ADAPTER.md) benötigt eine explizite Session und einen begrenzten Bereich; die Session entsteht über `sheets_session.build_readonly_session` bzw. `build_readonly_session_from_info` (M2-03).
- `PORTFOLIO_WATCHLIST_RANGE` (optional): Bereich des Watchlist-Blatts im selben Sheet, ausdrücklich mit Blattname und Zeilengrenzen und Endspalte `D` bis `T`, z. B. `Watchlist!A1:D300` (nur Grundspalten) oder `Watchlist!A1:J300` (mit Yahoo-Zusatzspalten). Schema und Regeln: [ADR 0011](docs/adr/0011-watchlist.md) und [DATA_SOURCE.md](docs/DATA_SOURCE.md#watchlist-blatt). Demo verwirft den Wert. Modell, Parser und Bereichsprüfung existieren; Abruf und Ansicht folgen (M9).
- `PORTFOLIO_PROVIDER=yfinance`: vorgesehener Anbieter im Produktivmodus. Demo erzwingt stattdessen den synthetischen Anbieter. Der austauschbare [Adapter](docs/MARKET_DATA.md) für verifizierte Kurs-/Historien-/FX-Mappings ist unabhängig testbar; der reale Lader ist noch nicht angebunden.
- `PORTFOLIO_QUOTES_TTL_SECONDS=60`, `PORTFOLIO_HOLDINGS_TTL_SECONDS=300`, `PORTFOLIO_HISTORY_TTL_SECONDS=21600`: positive ganze Sekunden entsprechend ADR 0005.
- `PORTFOLIO_LOGIN_PROVIDER=google` und `PORTFOLIO_OWNER_ISSUER` / `PORTFOLIO_OWNER_SUBJECT`: explizit freigegebene, vertrauliche Besitzeridentität. Sie wird kontrolliert aus validierten Claims festgelegt (#31), niemals aus dem ersten Login oder einer E-Mail abgeleitet. Der Issuer muss exakt dem konfigurierten Wert entsprechen.
- OIDC-Client, Cookie-Secret, HTTPS-Callback mit Pfad `/oauth2callback` und der feste Google-Metadatenendpunkt stehen ausschließlich im serverseitigen Streamlit-Abschnitt `[auth]`, siehe [Platzhalter](.streamlit/secrets.example.toml). `load_config(auth_settings=...)` erhält denselben Abschnitt wie Streamlits Login-/Callback-Implementierung. Frühere `PORTFOLIO_OIDC_*` / `PORTFOLIO_COOKIE_SECRET`-Umgebungsvariablen ersetzen ihn nicht. Zusätzliche Provider-/Validierungs-Overrides und Token-Freigaben werden abgelehnt. Keine Auth-Credentials werden für Sheets verwendet.

Fehlende Platzhalter, ungültige Werte und fehlende Besitzerkonfiguration sperren den Produktiveinstieg. Fehlermeldungen nennen nur Feldnamen; Konfigurationsrepräsentationen geben keine vertraulichen Inhalte aus. `synthetic_import_cases()` in [`fixtures.py`](src/portfolio_dashboard/fixtures.py) stellt ausschließlich erfundene Aktien-/ETF-/Derivatefälle mit EUR/USD/GBP, fehlenden, Null- und negativen Werten bereit. Der [Sheet-Parser](docs/SHEET_PARSER.md) validiert übergebene Zeilen offline; der getrennte [Sheets-Adapter](docs/SHEETS_ADAPTER.md) nutzt denselben Importvertrag. Echte App-Abrufe bleiben bis zur Besitzerfreigabe geschlossen; Bewertung folgt separat. „Investiert (EUR)“ enthält laut Besitzerentscheidung (#20) den Einstand der verbleibenden Stücke ohne Gebühren.

### Lokal mit eigenen Daten

`PORTFOLIO_MODE=local` liest das eigene Sheet über den lesenden Service-Account, ohne Login. Deshalb startet der Modus nur, wenn Streamlit ausschließlich auf einer Loopback-Adresse lauscht; jede andere Adresse sperrt vor dem ersten Sheet-Abruf (Nachtrag in [ADR 0003](docs/adr/0003-login-und-zugriff.md)). Werte und Einstand stammen aus dem Sheet (deutsches Zahlenformat, Einstand als Restbestand gemäß #20). Ohne geprüfte Provider-Zuordnungen werden keine Kurse abgerufen; die Kursperioden zeigen „n. v.“, der Zeitraum **Seit Kauf** (Meine Entwicklung) rechnet mit den Sheet-Werten.

Optional verweist `PORTFOLIO_LISTINGS_FILE` auf eine private TOML-Datei außerhalb des Repositorys. Jeder Eintrag ordnet einer Sheet-ISIN (Spalte R) ein vom Besitzer geprüftes primäres Yahoo-Listing in EUR für Live-Kurse und Kursperioden zu. Die Datei legt Handelsplatz und Währung fest; Spalte F beschreibt das Listing des Sheets (Spalte E) und wird dafür nicht herangezogen. Unterstützt sind die Yahoo-Börsencodes `GER` (Xetra, 9:00–17:30), `FRA`, `STU`, `MUN` und `DUS` (8:00–22:00) mit den gemeinsamen Börsenfeiertagen. Derivate bleiben beim Sheet-Wert.

Als optionale Ersatzzuordnung kann das private Sheet vier eindeutig benannte Zusatzspalten führen: **Yahoo-Symbol**, **Yahoo-Handelsplatz**, **Yahoo-Aktienform** (`share` oder `adr` für Aktien) und **Yahoo-Anteilsklasse** (nur für ETFs). Sie passen hinter die bisherigen Spalten in **U–X**; die Zuordnung erfolgt anhand der Überschriften innerhalb N–X. Dafür den ausdrücklich begrenzten `PORTFOLIO_SHEET_RANGE` bis **X** erweitern, etwa `'<Tab>'!A1:X500`. Eine vollständige Angabe ist die ausdrückliche Besitzerbestätigung, dass Symbol, EUR-Handelsplatz und Form/Anteilsklasse genau zur ISIN und zum Typ dieser Zeile gehören. Alle Daten müssen vor der privaten Befüllung geprüft werden. Es gibt keine automatische Symbolsuche oder Produktableitung.

Das private App-Listing bleibt vorrangig. Der Sheet-Ersatz wird bei fehlender Zuordnung oder unbrauchbarer primärer Quote versucht; bei vorhandenen Kursperioden gewinnt er nur, wenn er **alle** verfügbaren primären Perioden und mindestens eine weitere liefert. Bei Gleichstand bleibt das App-Listing. Quote und sämtliche Perioden stammen immer gemeinsam aus einer gewählten Zuordnung. Die Spalte **Kurszuordnung** nennt Herkunft, Yahoo-Symbol und Handelsplatz; der Status kennzeichnet den verwendeten Sheet-Ersatz auch bei **Seit Kauf**. Ein technisch gültiges, aber vom Besitzer falsch zugeordnetes Produktsymbol lässt sich durch die Metadatenprüfung allein nicht erkennen. Details und Fehlergrenzen: [Marktdaten](docs/MARKET_DATA.md).

```toml
[[listing]]
isin = "DE000SYN0001"   # ISIN aus Spalte R
type = "stock"          # stock oder etf
symbol = "SYN.DE"       # Yahoo-Symbol
venue = "GER"           # Yahoo exchangeName
equity_form = "share"   # nur Aktien: share oder adr; ETFs stattdessen share_class = "Acc"
```

```bash
export PORTFOLIO_MODE=local
export PORTFOLIO_SHEET_ID='<private Sheet-ID>'
export PORTFOLIO_SHEET_RANGE='<Tabellenblatt>!A1:T200'   # einfache Anführungszeichen, sonst deutet zsh "!" als Verlaufsbefehl; Kopfzeile in der ersten Zeile, Endspalte M–T
export PORTFOLIO_SHEETS_CREDENTIALS_FILE="$HOME/<Pfad außerhalb des Repos>/service-account.json"
export PORTFOLIO_LISTINGS_FILE="$HOME/<Pfad außerhalb des Repos>/listings.toml"   # optional
uv run --locked streamlit run app.py --server.address 127.0.0.1
```

Die Werte nur in der eigenen Shell setzen, nicht in Dateien im Repository. Der Modus ist nicht für Container oder Hosting gedacht; dort gilt der Produktivmodus mit Login. Der lokale Modus liest Schlüssel und Zuordnungen nur aus Dateien und nie aus `st.secrets`; ein gehosteter Betrieb, der versehentlich auf `local` steht, findet daher keinen Schlüssel.

### Produktivmodus mit Login

`PORTFOLIO_MODE=production` zeigt nach Login und Besitzerprüfung dasselbe eigene Depot wie der lokale Modus: Werte und Einstand aus dem Sheet, Live-Kurse und Kursperioden für die Positionen mit geprüftem Listing. Statt Dateien verwendet er die serverseitigen Secrets: `[sheets_service_account]` (Felder der Service-Account-JSON) und optional `[[listing]]`-Einträge im Format der lokalen Zuordnungsdatei. Ladeablauf und Secrets werden erst nach bestandener Besitzerprüfung angefasst, einmal pro Sitzung aufgebaut und bei jedem Lauf erneut hinter die Prüfung gestellt. Vorlage: [`secrets.example.toml`](.streamlit/secrets.example.toml), Einrichtung: [Community Cloud](docs/COMMUNITY_CLOUD.md).

Der Produktivmodus liest dieselben optionalen Yahoo-Ersatzspalten wie der lokale Modus. Ohne private App-Listings und ohne vollständige Sheet-Ersatzzuordnung bleibt es beim Sheet-Wert. **Keine Kurszuordnung**, **Kursabruf fehlgeschlagen (deutscher Grund)** und ein abgelehnter Historienabruf sind in Tabelle, Positionsdetails und Datenstatus getrennt erkennbar. Fehlertexte enthalten ausschließlich feste deutsche Ursachen, keine Provider-Rohantworten oder Ausnahmetexte.

### Besitzerprüfung und Sitzungen

[`auth.py`](src/portfolio_dashboard/auth.py) verwendet die unterstützten APIs `st.login()`, `st.user` und `st.logout()` mit dem `streamlit[auth]`-Extra (Authlib). Streamlit/Authlib prüfen den OIDC-Callback einschließlich Signatur, Issuer, Audience, State und Nonce; die Anwendung implementiert keine eigene JWT-Prüfung und nimmt keine Claims aus Browserparametern entgegen. Vor jedem Datenladen und Rendern in `app.py` prüft der Server erneut `is_logged_in`, die exakte Besitzerkombination `iss` / `sub`, die Client-Audience und endliche, zeitlich gültige `iat` / `exp`. Entscheidungen werden nicht in Session State oder einem Cache gespeichert. Bestands-/Kursabrufe und künftige Exporte bleiben unter derselben Sperre; Exporte existieren derzeit nicht.

Streamlits Identitätscookie kann 30 Tage bestehen und verlängert die ID-Token-Gültigkeit nicht. Abgelaufene Tokens sperren den nächsten App-Lauf und erfordern Abmelden/erneute Anmeldung. **Von dieser App abmelden** ruft das SDK auf und beendet den Lauf vor jedem Loader. Ein neues anonymes Fenster bleibt gesperrt; beim Öffnen eines neuen Tabs kann ein noch gültiges Identitätscookie die Anmeldung wiederherstellen, wonach erneut die Besitzerfreigabe geprüft wird. Bereits laufende Tabs besitzen eigenständige Sitzungen und werden durch die App-Abmeldung eines anderen Tabs nicht automatisch abgemeldet. Eine bereits sichtbare Anzeige wird nicht fern-gelöscht; beim nächsten Lauf gelten wieder die Freigabe- und Ablaufprüfungen. Dies ist keine globale Abmeldung vom Google-Konto.

Die synthetischen Tests prüfen den tatsächlichen Einstieg mit anonymen, fremden, fehlenden, abgelaufenen und fehlerhaften Claims sowie der erlaubten Besitzeridentität. Sie überwachen beide Demo-Loader und den Renderer, blockieren Netzwerk-/Credential-Dateizugriffe und prüfen erneute Läufe, Login-/Logout-Aufrufe, erneutes Öffnen und unabhängige Tabs. AppTest ersetzt dabei `st.user` serverseitig und simuliert keine OIDC-Weiterleitung oder Browsercookies. Echter Login, Cookies/mehrere Browser-Tabs, HTTPS und Callback am Zielhost bleiben nach #31 / #34 separat zu prüfen. Synthetische Freigabe belegt keine echte Einrichtung.

### Automatische Prüfung und PR-Ablauf

Der Workflow [CI](.github/workflows/ci.yml) prüft Pull Requests nach `agents/integration` und `main` sowie Pushes auf diese beiden Branches. Er verwendet Python 3.12, uv 0.12.23 und die gesperrten Entwicklungsabhängigkeiten. Lokal dieselben drei Schritte ausführen:

```bash
uv sync --locked
uv run --locked ruff check .
uv run --locked pytest
```

Ruff prüft Python-Fehler, ungenutzte/undefinierte Namen und Importreihenfolge im gesamten Quellbaum. Pytest führt die gezielten Tests unter `tests/` aus; ein fehlgeschlagener Test beendet den CI-Job mit Fehler. `pytest-socket` sperrt IP-Sockets standardmäßig über die Pytest-Konfiguration. Lokale Unix-Sockets bleiben für Streamlits asyncio-Ereignisschleife erlaubt; der separate Fachmodul-Importtest sperrt auch diese. Fixtures bleiben synthetisch. CI erhält keine Produktions-Secrets und benötigt weder echtes Sheet noch Kursanbieterzugang. Nur Checkout, Werkzeug- und Dependency-Installation benötigen Netzwerkzugriff.

Für jede Aufgabe von `agents/integration` einen kurzen Issue-Branch anlegen und einen PR zurück nach `agents/integration` öffnen. Abhängige, noch offene PRs und deren Basis-Commit ausdrücklich im PR nennen. Vor dem Zusammenführen müssen der Check **Locked install, lint, tests (Python 3.12)** und die unabhängige fachliche Prüfung erfolgreich sein. Exakte Befehle, Commit-SHA und den tatsächlichen Actions-Lauf im PR dokumentieren; ein lokaler Erfolg ist kein CI-Nachweis. Diese Vorgabe konfiguriert keinen Branch-Schutz und erteilt keine Merge-Freigabe.

Live-Prüfungen gehören in einen getrennten, ausdrücklich autorisierten Ablauf nach Auswahl der Hosting-Umgebung und ihrer Secret-Verwaltung: echte Sheet-/Kursabrufe, Login/Besitzerzugriff und geschützte HTTPS-URL gemäß [ADR 0009](docs/adr/0009-betrieb-und-qualitaet.md). Sie laufen nicht in diesem CI-Workflow; synthetische Tests belegen keine Provider-Verfügbarkeit. Private Antworten und Credentials dürfen weder Test-Fixtures noch Logs oder PR-Anhänge werden.

Kursdaten stammen aus `yfinance` (inoffiziell, Kurse können verzögert sein). Keine Anlageberatung.

Sheet-ID und Dokumentlinks gehören in lokale Konfiguration oder `docs/private/`, nicht in das Repository. `docs/DATA_SOURCE.md` beschreibt ausschließlich das allgemeine Importschema.

Die Offline-Fachoperationen für eine private Watchlist und die explizit konfigurierte
SQLite-Ablage sind in [PRIVATE_WATCHLIST.md](docs/PRIVATE_WATCHLIST.md) dokumentiert.
Der Watchlist-Editor folgt separat; Demo verwendet ausschließlich temporäre synthetische Daten.
