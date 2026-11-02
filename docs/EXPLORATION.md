# Unternehmensanalyse: Financial Pipeline in Streamlit

Die Ansicht **Unternehmensanalyse** ergänzt Depot, Watchlist und Innerer Wert.
Sie portiert alle 29 Themen der Financial-Pipeline-V4 in native Plotly-Charts,
interaktive Tabellen und CSV-Downloads. Die Originalskripte, PNGs und die eingefrorene
Baseline im Pipeline-Repository bleiben unverändert.

![Unternehmensanalyse mit ausschließlich synthetischen Daten](images/exploration-demo.png)

## Start

```sh
uv sync --locked
uv run --locked streamlit run app.py
```

In der Navigation **Unternehmensanalyse** auswählen. Standardmäßig läuft das
Dashboard vollständig offline mit deutlich markierten synthetischen Beispielen.
Bekannte Symbole im Demo-Modus stehen **nicht** für echte Marktdaten.

Im bereits eingerichteten lokalen bzw. produktiven Modus bleibt der bestehende
Loopback-/Besitzerschutz des App-Einstiegs aktiv. Erst **Analysedaten laden** oder
**Aktualisieren** lädt den Yahoo-Snapshot. Filterwechsel rufen keine Daten ab.
Der Snapshot liegt ausschließlich in der jeweiligen Streamlit-Sitzung, nicht in
einem globalen Cache oder einer Pickle-Datei. Nach einer Stunde wird sein Alter
markiert; Aktualisierung erfolgt ausdrücklich. Beim vollständigen Kursabrufausfall
bleibt ein vorhandener Stand mit ursprünglichem Zeitstempel erhalten.
Einzelne fehlende Ressourcen sperren nur die betroffenen Analysen.

Der Unternehmensfokus ist wie im Original Microsoft. Oracle, Alibaba HK und SAP
sind die feste Vergleichsauswahl der Ausgangsanalyse, keine dynamisch aus dem
Depot geladenen Positionen. Es werden keine Bestände, Gewichte oder Einstandswerte
übertragen. Eine beliebige Unternehmensauswahl würde zusätzliche Prüfung der
Berichtswährungen, Geschäftsjahre, Segmente und Veröffentlichungszeiten benötigen.

## Gestaltung und Navigation

- **Geschäftsmodell:** Segmente, Wachstum/Margen, Jahres- und Quartalscashflow,
  Cashflow-Überleitung, Gewinnqualität.
- **Kapital & Bilanz:** ROIC, Kapitalverwendung, Entwicklung je Aktie, Bilanz,
  Zinsdeckung, Dividende und Ausschüttungsquote.
- **Bewertung:** Schätzungen/Revisionen, rekonstruiertes KGV, Portal-Multiplikatoren,
  Reverse DCF.
- **Kurs & Risiko:** EUR-Kurs, EPS-Ereignisse, Kerzen/Volumen, Aktienvergleich,
  Monatsrenditen, Drawdown, Risikoübersicht, Benchmark, Earnings-Reaktion.
- **Marktumfeld:** Analystenkonsens, Zieländerungen, Insider, Institutionen, Optionen.

Eine Fragestellung zur Zeit hält die Seite schnell und übersichtlich. Mehrere
Kennzahlen eines Themas stehen in getrennten Panels, niemals auf einer Doppelachse.
Hover, Zoom und Legendenauswahl funktionieren direkt in Plotly. Tabelle und Download
machen die Zahlen auch ohne Hover erreichbar. Monatsrenditen zeigen stets zwölf
vollständige Monate; der deaktivierte Zeitraumfilter erklärt die Ausnahme.

Die CI 1.3 liegt unter `src/portfolio_dashboard/ci.json`. `design.py` verwendet die
Tokens für Plotly und die Dashboard-Oberfläche. Die Treemap behält ihre fachliche
Farbzuordnung (Rot/Grau/Grün); Typografie, Flächen, Kennzahlenkarten und Kopfzeile
folgen nun derselben CI. Questrial wird lokal inklusive OFL-Lizenz unter `static/`
geliefert; keine Google-Fonts-Anfrage beim App-Aufruf. Die Streamlit-Theme-Datei
enthält die entsprechenden Grundfarben. Bei einer CI-Aktualisierung diese Datei
zusammen mit den Tokens abgleichen.

**Warum Plotly?** Bereits im Dashboard vorhanden, native Kerzen, Wasserfälle,
Heatmaps, Hover und Bildexport. Altair wäre für verknüpfte statistische Selektionen
interessant; ECharts für spezielle Interaktionen bei sehr großen Datenmengen.
Für diese Finanzanalysen entsteht durch eine zweite Chart-Bibliothek kein klarer
Mehrwert. Matplotlib bleibt im Pipeline-Projekt für statische Exportgrafiken.

## Datenregeln und Herkunft

Ausgangsstand: [Financial Pipeline, Commit 52133fc](https://github.com/FelixSchramm/real-time-stock-market-pipeline/tree/52133fcaba5a734567880cab191a826d1ade6bab/data_exploration/v4).
`calculations.py` übernimmt die V4-Rechenfunktionen; `data.py` übernimmt die
Statement-/FX-Konventionen mit einem neuen sessionbasierten Abruf. `charts.py`
portiert `business.py` und `market.py` ohne Matplotlib-Abhängigkeit.

- FX nur aus vorherigen Beobachtungen, maximal sieben Tage. Keine Zukunftskurse,
  keine aufgefüllten Aktienkurse. Native EUR/USD-Reihen brauchen keinen unnötigen FX-Abruf.
- EUR/USD und Kurs-/Gesamtrenditewahl gelten für die Vergleichsanalysen;
  Geschäftszahlen bleiben USD, Geschäftsjahresende Juni.
- Gesamtrendite ist eine Näherung aus `Adj Close`, ohne persönliche Steuern/Gebühren.
- TTM erfordert vier aufeinanderfolgende Quartale. FCF wird gegen CFO minus
  absoluten Cash-Capex geprüft. Fehlende Felder werden nicht als null interpretiert.
- Drawdown-Linie nutzt die geladene Vorgeschichte, die Risikoübersicht unterscheidet
  Zeitraum-Drawdown und Abstand zum Zehnjahreshoch.
- KGV verwendet bekannte Quartals-EPS erst ab dem Folgetag; fehlende Quartale
  unterbrechen die TTM-Basis. Median ist ausdrücklich kein fairer Wert.
- Reverse DCF bleibt ein vereinfachter Eigenkapital-Cashflow-Proxy aus V4.
  Er ersetzt **nicht** den bestehenden FCFF-Rechenkern unter **Innerer Wert**.
- Segmentdaten sind die kuratierte öffentliche Berichtstabelle aus V4, Quelle
  Microsoft Annual Report 2026, Note 18. Sie sind als feste Berichtsversion markiert
  und werden nicht durch Yahoo aktualisiert. Demo nutzt erfundene Segmente.
- Analysten-, Eigentümer- und Optionsdaten sind Snapshots. Meldedatum und
  Abrufdatum sind unterschiedliche Größen; Optionsquotes werden auf Qualität gefiltert.

## Prüfung und Grenzen

Tests verwenden ausschließlich synthetische Daten und gesperrte IP-Netzwerkzugriffe:

```sh
uv run --locked pytest
uv run --locked ruff check src app.py tests
```

Neue Tests prüfen alle 29 Renderer, Navigation durch alle Themen, Rückkehr zum Depot,
FX-Richtung/Alter, TTM-Lücken, ROIC, Ausschüttungsperioden, KGV-Verfügbarkeit,
DCF-Inversion, Quote-Filter und Erhalt des alten Stands bei fehlgeschlagenem Refresh.
Zusätzlich wurde die Oberfläche mit Chromium auf Desktop und 390 px Breite geprüft.
Der abschließende vollständige Testlauf bestand mit **1.779 Tests** (zwei bestehende
yfinance/Pandas-Deprecation-Warnungen). Ein zusätzlicher Live-Abruf am 02.11.2026
lieferte alle zehn Kursressourcen; alle 29 Themen konnten daraus gerendert werden.
Diese Anbieterantworten wurden weder als Dateien gespeichert noch in Fixtures übernommen.
Der lokale Font wurde mit HTTP 200 geladen und im Browser als verfügbar bestätigt.

`ruff check .` findet im unveränderten Bestandsnotebook
`exploratory_analysis/01_msft_api_rundgang.ipynb` bereits vorhandene E401/I001/E701/E702-Verstöße.
Das Notebook wird auf Wunsch des Auftrags unverändert erhalten.
Der neue Code und die App-/Testmodule bestehen den gezielten Lint-Lauf.
Es wurde keine Produktivbereitstellung oder Änderung an Zugangsdaten vorgenommen.
