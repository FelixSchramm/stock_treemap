# Bewertungsübersicht «Innerer Wert»

Umsetzung von Issue #79 (M8-10, Epic #69) nach [ADR 0010](adr/0010-innerer-wert-fcff.md),
[ADR 0011](adr/0011-watchlist.md) und [ADR 0008](adr/0008-treemap-und-bedienung.md). Die
Ansicht macht die Modellwerte aus [DCF_CALCULATION.md](DCF_CALCULATION.md), den Kursvergleich aus
[PRICE_COMPARISON.md](PRICE_COMPARISON.md) und die Datenlücken für gehaltene und beobachtete
Aktien sichtbar. Sie ist eine Modellschätzung: kein Kauf- oder Verkaufssignal, kein
Konfidenzintervall, keine Änderung an Treemap, Kursperioden oder Seit-Kauf-Renditen.

## Bausteine

| Modul | Aufgabe |
| --- | --- |
| `valuation_overview` | rein fachlich (ohne Streamlit, Netzwerk, Uhr): Zeilen, Filter, Sortierung, Abdeckung, Statustexte |
| `valuation_view` | Streamlit-Darstellung, explizites Laden, Sitzungszustand, Einstieg für #80 |
| `ui.render_valuation_area` | Ansicht **Innerer Wert** im Ansichtsradio; lädt Depot-/Watchlist-Stand der eigenen Ansichten |
| `valuation_demo`, `fundamentals_demo` | ausdrücklich synthetische Demo-Daten über denselben Ladepfad |
| `app.py` | übergibt `overview=` (Produktiv: `valuation_view.private_source(coordinator.listings)`, Demo: `valuation_demo.demo_valuation_source()`) |

## Eine Zeile je Wertpapier

`collect_subjects(portfolio, entries, listings)` führt aktive Depotgruppen und Watchlist-Zeilen zusammen.
Eine **geprüfte Aktie** wird ausschließlich über die `instrument_id` ihres Registry-Listings erkannt (Depot:
`resolve_listing`, Watchlist: `watchlist.link_portfolio`). Dieselbe Aktie in Depot und Watchlist ist
**eine** Zeile mit den Kennzeichen `im Depot und auf Watchlist`; Modellwerte, Szenarien und
Abdeckung entstehen einmal je Aktie und sind daher in jedem Filter identisch. Mehrere Watchlist-Zeilen derselben
Aktie werden zusammengefasst (Hinweis in den Details). Die Watchlist braucht weder Einstand noch Stückzahl; die
Übersicht liest nie Mengen, Kosten oder Gewichte. Alle anderen Einträge (ungeprüfte Zuordnung, ETFs, Derivate)
bleiben eigene Zeilen; nichts wird ausgeblendet.

Der **Kurs** einer zusammengeführten Aktie stammt aus der Depotposition, sonst aus der Watchlist-Zeile (Quelle
steht dabei: `Anbieter`, `Anbieter, zwischengespeichert`, `Sheet-Wert, Kursstand unbekannt`, im Demo
`Demo-Wert, synthetisch`). Das Instrument des Kurses muss zur selben `instrument_id` wie die Berechnung gehören
(sonst sperrt #78 den Vergleich).

## Spalten und die zwei Bezugsgrößen

`Name`, `Ticker`, `Bestand` (`Depot`, `Watchlist`, `im Depot und auf Watchlist`), `Kurs`, die drei Szenariowerte
`Vorsichtig`, `Basis`, `Optimistisch` (Wert je Aktie in Berichtswährung), zwei getrennte Kennzahlen zum **Basiswert**,
`Zielpreis (Basis)`, `Status`, `Datenstand`.

- **Kurs-Abschlag zum Basiswert (% des Basiswerts)** = `(1 − Kurs ÷ Modellwert) × 100`, bezogen auf den Modellwert.
- **Basiswert über Kurs (% des Kurses)** = `(Modellwert ÷ Kurs − 1) × 100`, bezogen auf den Kurs.

Kurs 80, Basiswert 100: 20 % Abschlag, 25 % über Kurs; liegt der Kurs über dem Basiswert, sind beide negativ. Beide
Werte und ein Satz wie `Kurs liegt unter dem Basiswert` stammen aus `price_comparison` (ungerundet; gerundet wird erst bei
der Anzeige mit Vorzeichen). Der **persönliche Zielpreis** ist `Basiswert × (1 − Sicherheitsabschlag)` des Basisszenarios
und erscheint nur, wenn der Besitzer einen Abschlag gesetzt hat. Die Details zeigen alle drei Szenarien mit beiden
Kennzahlen, den Kursvergleich in Worten und die Hinweise des Vergleichs (z. B. `Kurs: Sheet-Wert, kein Anbieterkurs`,
`Kurszeit unbekannt`, `Währungen verschieden: Vergleich über EUR`). Ohne Wechselkurs bei verschiedenen Währungen bleibt
der Modellwert sichtbar, der Vergleich zeigt den Grund (`Wechselkurs fehlt`); die Anwendung lädt noch keine
Wechselkurse.

## Status: nie null, nie nur Farbe

Fehlende Werte erscheinen als `n. v.` mit Grund, nie als `0`. Jede Zeile hat genau einen Status als Text:

| Status | Bedeutung |
| --- | --- |
| `Bewertet` / `Teilweise bewertet (n von 3 Szenarien)` | Szenariowerte vorhanden; übrige Szenarien mit Grund (z. B. `Annahmen fehlen`) |
| `Annahmen fehlen` | Fundamentaldaten vorhanden, aber keine gespeicherten Szenarioannahmen (der heutige Normalfall bei echten Aktien: der Besitzer trägt sie später ein) |
| `Daten/Annahmen unvollständig`, `Annahmen unbestätigt`, `Daten veraltet`, `Angaben ungültig` | Rechner sperrt; die Prüfgründe (Codes aus #77) stehen in den Details |
| `Modell nicht unterstützt` | Emittentenart/Instrument außerhalb des Modells (ADR 0010) |
| `Fundamentaldaten geändert, Annahmen prüfen` | der gespeicherte Szenariostand gehört zu einem anderen Berichtssatz (`input_version`); es wird nicht mit neuen Zahlen und alten Annahmen gerechnet |
| `Keine Fundamentaldaten` | kein vollständiger, verwertbarer Abruf gespeichert; letzter Fehlversuch mit bereinigten Codes |
| `Fundamentaldaten-Zuordnung fehlt` | Listing geprüft, aber ohne eigenen Eintrag der privaten `[[fundamental]]`-Zuordnung |
| `Fundamentaldaten nicht eingerichtet` / `noch nicht geladen` / `nicht verfügbar` | keine Zuordnung konfiguriert / noch nicht angefordert / Speicherfehler |
| `Zuordnung fehlt` | keine geprüfte Instrumentidentität; die Zeile erklärt, welche Angabe fehlt |
| `Kein Bewertungsmodell (ETF/Derivat)` | ausgeschlossene Produktart, siehe unten |

Quelle, Alter, manuelle Ergänzung, unvollständige Annahmen und nicht unterstütztes Modell stehen als Text in
`Status`, `Datenstand` und den `Details ohne Hover` (`manuell ergänzt: …`, `Buchwert als Näherung`,
`Veröffentlichungsdatum einzelner Angaben unbekannt`, `älter als die Altersgrenze: …`, `Terminalwertanteil ab 75 %`).

**Abrufalter und fachliches Alter bleiben getrennt** (Abschnitt „Ergebnis für Verbraucher“ in
[FUNDAMENTALS_CACHE.md](FUNDAMENTALS_CACHE.md)): `Abrufalter` stammt aus `last_checked_at`, das `fachliche Alter` aus
`latest_period_end` (Jahresabschluss), Bilanzstichtag und Aktienzahl-Stichtag; ein frischer Abruf alter Abschlüsse
bleibt fachlich alt. Der `Annahmenstand` nennt Version, Stichtag und Bestätigung des Basisszenarios.

## Abdeckung und ausgeschlossene Produktarten

`coverage(rows)` bezieht sich immer auf die Zeilen des **aktuellen Filters** (Bestand und Suche):
**Mit Modellwert** = Aktien mit mindestens einem Szenariowert ÷ im Filter **betrachtete Aktien**. Im Nenner stehen
alle Aktien, auch solche mit fehlender Zuordnung, fehlenden Annahmen oder nicht unterstütztem Modell, sowie Einträge ohne
angegebene Art (Watchlist-Zeilen ohne `Typ` und ohne Zuordnung); positiv bekannte Nicht-Aktien (ETF, Derivat, geprüftes
Nicht-Aktien-Listing) sind **ausgeschlossen**: Sie stehen nicht in der Tabelle, nicht im Nenner, sondern im Bereich
`Ausgeschlossene Produktarten (n)` mit Erklärung und Namen, zusätzlich als Zeile unter den Kennzahlen. Weitere Kennzahlen:
`Alle drei Szenarien` und `Ohne Modellwert`.

## Bedienung

Ansicht **Innerer Wert** im Radio `Ansicht` (neben Depot und Watchlist; `Zeitraum` gehört nicht dazu).

- **Bestand**: `Alle`, `Portfolio`, `Watchlist` (Überschneidung erscheint in beiden). **Suche** in Name, Ticker, Kategorie.
  **Anzeige**: `Alle Einträge`, `Nur mit Modellwert`, `Nur ohne Modellwert`. Filter ändern nie eine Bewertung.
- **Sortierung**: `Name`, `Ticker`, `Abschlag zum Modellwert (Basis)`, `Abstand zum Modellwert (Basis)` mit Reihenfolge;
  Zeilen ohne Zahl stehen immer zuletzt (nie als 0). Basiswerte werden nicht sortiert, weil Berichtswährungen
  verschieden sein können.
- **Darstellung**: `Tabelle` (mit `Bewertungsdetails ohne Hover (n)`) oder `Kompakt (Karten)` als umbrechender Text ohne
  Tabelle und ohne horizontales Scrollen, für schmale Viewports.
- `Datenstatus & Hinweise (n)` nennt je Aktie ohne volle Bewertung Status und Gründe.
- Filterwahl bleibt beim Ansichtswechsel erhalten (Schlüssel `valuation.*`, wie bei Depot und Watchlist).

## Laden und gemeinsamer Datenstand

- **Fundamentaldaten** kommen aus dem Ladepfad von #74: `st.session_state[fundamentals_loader.LOADER_STATE_KEY]`,
  `loader.load(instruments, refresh=...)` mit den Listing-Instrumenten aller geprüften Aktien (Depot und Watchlist gemeinsam,
  ein Abruf je Aktie). `load` kann bis zu fünf serielle Abrufe mit Pausen auslösen und blockiert dann den Skriptlauf; es
  läuft deshalb **nur** auf eine ausdrückliche Aktion (`Bewertungsdaten laden`, danach `Fundamentaldaten aktualisieren`,
  oder die Schaltfläche `Aktualisieren` in dieser Ansicht) hinter einem Spinner. Das Ergebnis (`LoadedFundamentals`)
  bleibt im Sitzungsschlüssel `valuation.loaded`; Reruns, Filter und Sortierung rufen nichts ab. Das Demo lädt seine erfundenen
  Daten (Speicher, keine Datei, kein Netzwerk) ohne Klick. Ohne private Zuordnung gibt es keinen Lader, keinen Knopf und
  den Status `Fundamentaldaten nicht eingerichtet`.
- **Szenarien** liest `OverviewSource.scenario_reader` (Produktiv `ScenarioRepository(PrivateDatabase()).get`) einmal
  beim Laden. **`PrivateStorageError`** (Lader oder Ablage) wird abgefangen und mit seiner festen Meldung gezeigt; die
  betroffenen Aktien erhalten `Fundamentaldaten nicht verfügbar`, es entsteht nie ein Wert und nie ein scheinbares
  `Annahmen fehlen` aus einem Lesefehler.
- **Kurse** sind die der Ansichten Depot und Watchlist (gleiche Bundles). Die Watchlist-Kurse werden nur auf eine
  ausdrückliche Aktion (oder im Demo) geladen, sonst wird das vorhandene Bundle der Sitzung verwendet; der Stand beider
  Kursquellen steht über der Tabelle.
- Alle Berechnungen laufen bei jedem Rerun aus dem gehaltenen Stand (reine Funktionen, keine Abrufe); die Uhr ist
  `OverviewSource.clock` (Produktiv UTC-Jetzt, Demo fester Zeitpunkt). Technische Hinweisgrenzen für Kurs und
  Wechselkurs: `MAX_PRICE_AGE`/`MAX_FX_AGE` je 4 Tage (offene Besitzerfrage 1 aus #78, siehe unten).

## Zuordnung der Watchlist: Abweichung vom Issue-Text und Grenzen

Der Issue-Text verlangte, „Watchlist hinzufügen/entfernen über M8-06 anzubinden“. Nach der Besitzerentscheidung vom
25.10.2026 (Epic #82, ADR 0011, #95) ist das Sheet-Blatt die maßgebliche Watchlist und **read-only**: Die Ansicht
schreibt nie ins Sheet und kennt weder Hinzufügen noch Entfernen; Änderungen erfolgen im Blatt. Die SQLite-Watchlist
(#75) ist entfernt. Einträge mit ungeprüfter Identität bleiben sichtbar und erklären die fehlende Zuordnung
(`Zuordnung fehlt`: kein vollständiger Yahoo-Kandidat im Blatt bzw. Kandidat entspricht keinem geprüften Listing).

Fundamentaldaten werden über `FundamentalSubjects.resolve(instrument)` nach exakter Instrument-ID, Typ und Handelsplatz
aufgelöst. Eine ISIN-only-Zuordnung gibt es bewusst nicht: Ein anderes Listing derselben ISIN braucht einen eigenen
`[[fundamental]]`-Eintrag mit gemeinsamem `source_symbol`, sonst steht die Zeile auf `Fundamentaldaten-Zuordnung fehlt`.
Technische Watchlist-Zeilen (`watchlist:<Platz>:<Symbol>`, ohne ISIN) haben ohne Registry-Eintrag keine Zuordnung.

**Offene Besitzerentscheidung:** Ob eine Watchlist-Zeile allein (ohne Eintrag in der geprüften Registry) als Identität gilt,
etwa über eine ISIN-Spalte im Watchlist-Blatt oder über ein bestätigtes Yahoo-Symbol samt Handelsplatz in der privaten
`[[fundamental]]`-Liste, ist weiterhin offen (ADR 0011, #95). Eine kleine saubere Lösung wäre eine explizite Zuordnung
bestätigter `symbol`+`venue`-Paare aus dieser Liste zu den Registry-Listings; sie wird ohne Besitzerfreigabe nicht
umgesetzt.

## Einstieg für die Detailansicht (#80)

Die Ansicht merkt nur die Wahl; es gibt keinen Editor. Auswahlfeld `Aktie für die Detailansicht` (nur Aktien mit
Fundamentaldaten-Identität) und Knopf `Detail- und Annahmenansicht vormerken` setzen
`st.session_state[valuation_view.DETAIL_KEY]` (`"valuation.detail_key"`) auf die `instrument_id` der geprüften Aktie
(`OverviewRow.detail_key`). #80 liest und löscht den Schlüssel, öffnet die Szenarioansicht über
`ScenarioRepository.get(identity)` und ruft nach jedem Speichern `valuation_view.reset_loaded_state(st.session_state)` auf,
damit die Übersicht beim nächsten Lauf die gespeicherten Szenarien liest. `valuation_overview.collect_subjects`,
`build_rows` und `entry_for` liefern dafür den Eintrag des Laders (`FundamentalsEntry.identity`, `report_set`).

## Demo

Das Demo zeigt ausschließlich Erfundenes: ein Wertpapier in Depot (Frankfurter Listing) und Watchlist (Xetra-Listing) mit
einem synthetischen Szenariodokument (neun manuelle Werte und drei bestätigte Pläne aus dem Beispiel in
[INTRINSIC_VALUE.md](INTRINSIC_VALUE.md): 106,98 / 135,15 / 192,86), je eine Watchlist-Aktie mit fehlenden Annahmen und
mit nicht unterstütztem Modell, zwei ETFs und zwei Zeilen ohne Zuordnung. Jede Zeile trägt den Hinweis
`Demo: Annahmen, manuelle Werte und Zahlen sind erfunden (synthetisch)`. Echte Annahmen gibt es noch nicht; sie
trägt der Besitzer später ein, bis dahin zeigt die Ansicht `Annahmen fehlen` statt erfundener Werte.

## Prüfung

```bash
uv run --locked pytest tests/test_valuation_overview.py tests/test_valuation_ui.py tests/test_auth.py tests/test_watchlist_ui.py
```

Synthetisch und offline: Depot allein, Watchlist allein, Überschneidung, leere Liste, unbewertbare Aktie (fehlende
Annahmen, nicht unterstütztes Modell, fehlende Zuordnung, Listing ohne eigene Fundamentaldaten-Zuordnung),
identische Modellwerte in jedem Filter, `n. v.` statt 0 in Anzeige und Sortierung, Abdeckung im Filter, getrenntes Abruf- und
fachliches Alter, teilweise/unbestätigte/unvollständige Annahmen, geänderter Berichtssatz, Ladefehler und
`PrivateStorageError`, kein Abruf ohne Aktion, Besitzerprüfung (unautorisierte Sitzungen sehen weder Watchlist noch
Szenarien), Tastatur-/Touch-Bausteine.

## Offene Punkte

1. Die Altersgrenzen für Kurs/Wechselkurs (4 Tage) sind technische Hinweise, keine Besitzerentscheidung
   ([PRICE_COMPARISON.md](PRICE_COMPARISON.md), offene Frage 1); die Anwendung lädt keine Wechselkurse, sodass
   Aktien mit abweichender Handels- und Berichtswährung nur Modellwerte, aber keinen Kursvergleich zeigen.
2. Identität der Watchlist-Zeile (siehe oben) und Fundamentaldaten für Watchlist-Aktien ohne Depotposition: das Listing
   muss in der Registry und in der privaten Zuordnung stehen.
3. Sichtprüfung in iOS Safari auf dem iPhone steht wie bei den anderen Ansichten aus.
