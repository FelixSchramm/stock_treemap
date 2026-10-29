# Watchlist-Ansicht

Umsetzung von Issue #85 (Epic #82) nach [ADR 0011](adr/0011-watchlist.md) und
[ADR 0008](adr/0008-treemap-und-bedienung.md). Die Ansicht zeigt reine Kursentwicklung in
Handelswährung (Kursabruf: [WATCHLIST_QUOTES.md](WATCHLIST_QUOTES.md)); sie kennt keine
Stückzahlen, keinen Einstand und keine Verknüpfung mit dem Depot. Die Heatmap folgt in #86.

## Bausteine

| Modul | Inhalt |
| --- | --- |
| `watchlist_view` | rein fachlich (ohne Streamlit): Filter, Sortierung, Kennzahlen, Tabellenzeilen, Detailzeilen, Statushinweise |
| `watchlist_session.session_loader` | baut den Lader (Koordinator oder Demo-Lader) einmal je Sitzung und Kalendertag |
| `ui.render_watchlist` und `render_watchlist_*` | Streamlit-Darstellung; Ansichtssteuerung `render_area_control` |
| `demo.DemoWatchlist` | statischer Lader mit `demo.demo_watchlist_bundle()` |
| `app.py` | übergibt die Lader-Fabrik (`build_local_watchlist_coordinator`, `build_production_watchlist_coordinator`, `DemoWatchlist`) an `render_dashboard(watchlist=...)` |

Für die Heatmap (#86) stehen dieselben Bausteine bereit: `watchlist_view.build_rows(entries,
period, criteria)` liefert die gefilterten `WatchlistRow` (u. a. `percent`, `percent_label`,
`source`, `status`, `detail_lines()`), `sort_rows` die Reihenfolge, `build_kpis` die
Kopfkennzahlen; das Bundle liegt in `st.session_state[ui.WATCHLIST_BUNDLE_KEY]`.

## Bedienung

- **Ansicht** (`Depot` | `Watchlist`, Schlüssel `dashboard.area`) steht über dem Inhalt. Im
  Depot bleiben `Kursentwicklung` (Zeitraum `Heute` bis `Jahr`) und `Meine Entwicklung`
  (Zeitraum `Seit Kauf`) wie seit #102 aus dem Zeitraum abgeleitet; ein eigener
  Ansichts-Umschalter dafür kehrt nicht zurück. Jede neue Sitzung startet mit
  `Kursentwicklung / Heute` im Depot.
- **Zeitraum** (`dashboard.course_period`) gilt für beide Ansichten; die Watchlist bietet nur
  `Heute`, `Woche`, `Monat`, `Jahr`. Ist im Depot `Seit Kauf` gewählt, zeigt die Watchlist
  `Heute` mit Hinweis, **ohne** die gemerkte Auswahl zu ändern: zurück im Depot ist
  `Seit Kauf` wieder aktiv. Wählt man in der Watchlist selbst einen Zeitraum, ersetzt er die
  gemerkte Auswahl auch für das Depot.
- Suche, Typ-, Kategorie- und Sortierauswahl haben je Ansicht eigene Sitzungsschlüssel
  (`watchlist.*` bzw. `dashboard.*`) und bleiben beim Wechsel erhalten (Streamlit verwirft
  sonst den Zustand nicht gezeichneter Widgets; `_keep_state` schreibt ihn zurück).
- **Aktualisieren** wirkt nur auf die angezeigte Ansicht; die andere behält ihren geladenen
  Stand.

## Laden

- Die Lader-Fabrik wird **erst beim ersten Öffnen der Watchlist** aufgerufen, im Produktivmodus
  also nach bestandener Besitzerprüfung (wie das Depot) und nie in der Depot-Ansicht. Der
  Lader lebt in `st.session_state` und wird bei **Datumswechsel neu gebaut**, weil der
  Handelskalender beim Bau mit `date.today()` entsteht (Cache und gemerktes Bundle gehen
  dabei verloren; der nächste Lauf lädt hinter dem Spinner neu).
- `load()` läuft nie ungeschützt: Kaltstart und `Aktualisieren` zeigen einen Spinner mit dem
  Hinweis, dass der erste Abruf bei vielen Einträgen mehrere Minuten dauern kann. Das Bundle
  wird in der Sitzung gehalten; Periodenwechsel, Filter und Sortierung laden nicht neu. Ein
  Zeitbudget je Durchgang gibt es nicht. Parallele Sitzungen warten auf die Sperre des
  jeweiligen Koordinators (jede Sitzung baut ihren eigenen).
- Ohne `PORTFOLIO_WATCHLIST_RANGE` (lokal und produktiv) zeigt die Ansicht einen
  Konfigurationshinweis statt eines Fehlers und baut keinen Lader; das Depot ist unberührt.
  Die Demo zeigt die synthetische Watchlist und ignoriert die Einstellung.
- `available=False` ergibt eine Fehlermeldung (nichts weiter), `using_cached_stand` eine
  Warnung mit der Meldung des Koordinators (Alter des früheren Stands).

## Inhalt

Kopfkennzahlen (ungefilterter Gesamtbestand, gewählte Periode): beobachtete Werte,
Datenabdeckung (`n von m mit Kennzahl`, mit Hinweis auf Sheet-Ersatzwerte, zwischengespeicherte
oder Demo-Werte), größter Gewinner und größter Verlierer (Prozentwert groß, Ticker oder Name
darunter, `· Sheet-Wert` bei Ersatzwerten). Sheet-Ersatzwerte zählen als Werte, sind aber
überall benannt.

Filter: Suche (Name, Ticker, Yahoo-Symbol, Kategorie), Typ (`Alle`, `Aktie`, `ETF`, `Derivat`,
`Nicht angegeben`; `Derivat` ergänzt die vier Optionen des Issues, damit Derivat-Zeilen
filterbar bleiben), Kategorie (inkl. `Ohne Kategorie`). Sortierung: `Kursentwicklung (%)`
(Standard, absteigend), `Name`, `Ticker`, mit Reihenfolge; fehlende Werte (`n. v.`, kein
Ticker) stehen immer zuletzt, Gleichstände nach Name und Blattzeile.

Tabelle: `Name`, `Ticker`, `Typ`, `Kategorie`, `Handelsplatz`, `Kurs` (Handelswährung; EUR mit
Zusatz `(Sheet)` beim Sheet-Wert), `Kursentwicklung (%)`, `Quelle`, `Referenzdatum`,
`Kursstand`, `Status`. **Details ohne Hover** (`Watchlist-Details ohne Hover (n)`) zeigt je
Eintrag alle Felder, alle vier Perioden mit Herkunft und Referenzdatum, den Status, Hinweise
und die Blattzeile als umbrechenden Text in einem einzigen Markdown-Element (200+ Einträge
bleiben schnell). `Datenstatus & Hinweise (n)` listet Import-Hinweise und alle Einträge, die
nicht frische Anbieterdaten zeigen.

## Kennzeichnung

| Fall | Anzeige |
| --- | --- |
| `Heute` aus dem Blatt (`SHEET`) | Quelle und Status `Sheet-Wert, Kursstand unbekannt`; Kurs mit `(Sheet)`; Kursstand `Kursstand unbekannt` |
| Fehlende Periode | `n. v.` in der Spalte, Grund in `Quelle` (z. B. `keine Yahoo-Zuordnung im Blatt`), **nie** `0,00 %`; `0,00 %` erscheint nur bei echter Veränderung 0 |
| `CACHED` | Quelle `Anbieter, zwischengespeichert (vor 2 Std.)`, Status mit `Alter`, Kursstand mit Alter |
| Gemischte Zeile (`Heute` Sheet, Rest Anbieter) | Herkunft je Periode in `Quelle` (gewählte Periode) und in den Details; Status ergänzt `; Heute: Sheet-Wert, Kursstand unbekannt` |
| Anbieterausfall / Metadaten-Konflikt | Status mit festem Grundtext (z. B. `Kursanbieter nicht erreichbar`); unbekannte Codes werden nie ausgegeben |

Texte aus dem Blatt (Namen, Kategorien) werden für Markdown und HTML maskiert.

## Mobil und Barrierefreiheit

Die Ansicht nutzt dieselben Bedienelemente wie das Depot (Radio, Auswahlfelder,
Textfeld, Expander) und damit die Touch-Ziele (`>= 44 px`), den Umbruch unter `640 px` und den
Fokusring aus `MOBILE_ACCESSIBILITY_CSS`. Alle Werte stehen ohne Hover im Detailbereich;
Richtung steht als Vorzeichen und Text (`Gewinn (+)`, `Verlust (−)`), nie nur als Farbe. Die
11-spaltige Tabelle scrollt auf schmalen Viewports horizontal, die Details ersetzen sie dort.

## Prüfung

`uv run --locked pytest tests/test_watchlist_view.py tests/test_watchlist_ui.py tests/test_auth.py`
(synthetisch, offline): Navigation, Periodenerhalt und `Seit Kauf`-Rückfall, Filter,
Sortierung, 250 Zeilen mit und ohne Zusatzspalten, Konfigurationshinweis, Lader pro Sitzung
und Tag, Besitzerprüfung vor dem Lader, Kennzeichnung von Sheet-Wert, Cache und `n. v.`.
