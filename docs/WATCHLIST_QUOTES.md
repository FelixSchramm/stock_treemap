# Watchlist: Kursabruf, Kalender und Herkunft

Umsetzung von Issue #84 (Epic #82) nach [ADR 0011](adr/0011-watchlist.md): Die
Watchlist-Zeilen aus dem Sheet-Blatt ([Schema](DATA_SOURCE.md#watchlist-blatt),
[Parser](SHEET_PARSER.md#watchlist-parser)) erhalten über yfinance die vier
Kursperioden nach [ADR 0006](adr/0006-kursentwicklung.md). Die Ansicht ist in #85
umgesetzt ([WATCHLIST_VIEW.md](WATCHLIST_VIEW.md)), die Heatmap folgt in #86; dieses Modul
enthält keine UI.

## Bausteine

| Modul | Inhalt |
| --- | --- |
| `local_source.venue_calendars(today)` | `SessionCalendar` je Handelsplatz: `GER`, `FRA`, `STU`, `MUN`, `DUS` (Europe/Berlin) und neu `NMS`, `NGM`, `NCM`, `NYQ`, `ASE`, `PCX` (America/New_York) |
| `watchlist_refresh.WatchlistSheetsReader` | liest das Watchlist-Blatt (`PORTFOLIO_WATCHLIST_RANGE`) mit demselben lesenden Service-Account |
| `watchlist_refresh.WatchlistRefreshCoordinator` | `load(refresh=False)` liefert ein `WatchlistBundle`; Cache, Abgleich, Perioden, Fallbacks |
| `watchlist_refresh.build_local_watchlist_coordinator` / `build_production_watchlist_coordinator` | Verdrahtung wie beim Depot; `None` ohne `PORTFOLIO_WATCHLIST_RANGE` |
| `watchlist_quotes` | leichtgewichtiges Ergebnismodell (`WatchlistBundle`, `WatchlistEntry`, `WatchlistPeriodValue`, `sheet_entry`), ohne pandas/Provider |
| `demo.demo_watchlist_bundle()` | synthetische Demo-Watchlist (DE, US, nur Sheet-Wert, ohne Wert) |

## US-Kalender

Reguläre Zeiten 09:30–16:00 (America/New_York), eine gemeinsame Feiertagsmenge für alle
sechs US-Plätze (`us_holidays(year)`): Neujahr, Martin Luther King Jr. Day (3. Montag
Januar), Presidents' Day (3. Montag Februar), Karfreitag, Memorial Day (letzter Montag
Mai), Juneteenth (ab 2022), Independence Day, Labor Day (1. Montag September),
Thanksgiving (4. Donnerstag November), Weihnachten. Fällt ein fester Feiertag auf einen
Samstag, bleibt der Freitag davor geschlossen; fällt er auf einen Sonntag, der Montag
danach.

**Abweichung vom Issue-Wortlaut, bewusst:** Die Samstag-Regel gilt nicht für Neujahr.
Fällt der 1. Januar auf einen Samstag, bleibt die NYSE am 31. Dezember geöffnet (z. B.
31.12.2021). Würde man den Tag als Feiertag führen, fehlte eine echte Sitzung im
Kalender. Zusätzlich sind einzelne außerplanmäßige Ganztagsschließungen geführt (u. a.
Trauertage 2018-12-05 und 2025-01-09, Sandy 2012-10-29/30, 9/11 2001-09-11..14).
Vorzeitige Schlüsse (Tag nach Thanksgiving, Heiligabend) bleiben wie bei den deutschen
Plätzen unmodelliert und zählen als reguläre Sitzung.

Die Kalender gelten in **Börsen-Ortszeit**: Sitzungstage sind Daten am Handelsplatz,
Öffnungszeiten Ortszeit. `watchlist_refresh.session_running(calendar, zeitzone, now)`
rechnet den aware `now` in die Zeitzone des Handelsplatzes um. Dadurch stimmen die
Phasen mit unterschiedlichen Sommerzeiten (Europa wechselt am letzten Oktobersonntag,
die USA am ersten Novembersonntag: dazwischen liegen nur fünf statt sechs Stunden
zwischen Berlin und New York). Die deutschen Kalender und `VENUE_HOURS` bleiben
unverändert; US-Plätze stehen absichtlich nicht in `VENUE_HOURS`, damit
`parse_listings` für das Depot weiterhin nur deutsche Plätze und EUR akzeptiert.

## Ablauf eines Refresh

1. Das Blatt wird einmal gelesen (`WatchlistSheetsReader.read`) und über
   `QuoteCache.holdings` für `PORTFOLIO_HOLDINGS_TTL_SECONDS` gehalten. Der Koordinator
   besitzt einen eigenen `QuoteCache` (eigene Backoff-Zähler, kein Schlüsselkonflikt mit
   dem Depot).
2. Jede `MAPPED`-Zeile wird ein technisches `VerifiedListing`
   (`instrument_id = "watchlist:<Handelsplatz>:<Symbol>"`, Handelsplatz,
   `QuoteUnit(Währung, 1)`, `ProviderMapping("yfinance", Symbol)`). Es trägt weder ISIN
   noch Produktidentität; „verifiziert“ wird es erst durch den Laufzeitabgleich.
3. Kurse (`QuoteCache.quotes`) werden nur für Plätze mit **laufender** Sitzung geholt;
   sonst zählt der letzte Schlusskurs der Historie. Historien (`QuoteCache.history`)
   nutzen den Kalenderzeitraum wie beim Depot und werden nach einer verpassten
   Sitzungsschließung neu abgerufen: Eine während der Sitzung geholte Historie zeigt
   sonst nach Börsenschluss ihren Zwischenstand als Schlusskurs (#110, Regel in
   [MARKET_DATA.md](MARKET_DATA.md#cache-backoff-und-ersatzwerte-19)).
4. `price_period_returns` liefert `Heute`, `Woche`, `Monat`, `Jahr` als reine
   Kursrendite in **Handelswährung** (Split-bereinigt, ohne Dividenden; keine
   EUR-Umrechnung, keine FX-Kurse, keine Verknüpfung mit dem Depot).

## Abgleich mit den Yahoo-Metadaten

`YFinanceProvider` verwirft jede Antwort, deren `symbol`, `exchangeName` oder `currency`
nicht zum Blatt passt; zusätzlich prüft der Koordinator, dass die
Yahoo-`exchangeTimezoneName` zum Handelsplatz gehört. Es wird dann **kein** Anbieterwert
übernommen; `WatchlistEntry.status` ist `METADATA_MISMATCH`, `status_reason` nennt den
Grund:

| Code | Bedeutung |
| --- | --- |
| `instrument_metadata_mismatch` | `symbol` oder `exchangeName` weicht ab |
| `unknown_or_conflicting_unit` | `currency` weicht ab (auch Unterwährungen wie `GBp`) |
| `exchange_timezone_mismatch` | Börsenzeitzone passt nicht zum Handelsplatz |

## Ergebnis je Zeile (`WatchlistEntry`)

Jede Zeile hat immer vier `WatchlistPeriodValue` (`Heute`, `Woche`, `Monat`, `Jahr`) mit
`percent` und `origin` (`PROVIDER`, `CACHED_PROVIDER`, `SHEET`, `DEMO`) oder „n. v.“
(`percent is None`, `origin NONE`) mit stabilem `reason`. Dazu `price`/`price_unit`
(Handelswährung; EUR nur beim Sheet-Wert), `current_date`, `market_open`,
`retrieved_at`, `age_text`, `status`, `status_text`, `status_reason` und die
Parser-`warnings`.

| Status | Bedeutung |
| --- | --- |
| `LIVE` | frische, abgeglichene Anbieterdaten |
| `CACHED` | Aktualisierung fehlgeschlagen; letzter gültiger Anbieterstand mit Alter (`age_text`, `retrieved_at`) |
| `SHEET_VALUE` | nur der Sheet-Ersatzwert: „Sheet-Wert, Kursstand unbekannt“ |
| `METADATA_MISMATCH` | Yahoo-Metadaten widersprechen dem Blatt (siehe oben); nur Sheet-Wert, falls vorhanden |
| `UNAVAILABLE` | „Daten nicht verfügbar“ (kein Anbieter- und kein Sheet-Wert) |
| `DEMO` | erfundene Demo-Werte |

- **Zeilen ohne Yahoo-Zuordnung** (`SHEET_ONLY`, `FALLBACK`): `Heute` zeigt `Veränd. (%)`
  (Prozentpunkte unverändert) mit Herkunft `SHEET`, `price` ist `Letzter Kurs (EUR)`;
  `Woche`, `Monat`, `Jahr` sind „n. v.“ (`unverified_mapping`). Fehlt der Sheet-Wert,
  ist `Heute` „n. v.“ (`sheet_value_missing`).
- **Doppelte Yahoo-Symbole (`duplicate_yahoo_symbol`)**: Der Parser behält die
  Zuordnung der ersten gültigen Zeile; spätere Zeilen sind `FALLBACK` und verhalten sich
  wie Zeilen ohne Zuordnung (nur Sheet-Wert, Warnung mit Hinweis auf die erste Zeile in
  `warnings`). Pro Symbol wird nur ein Abruf ausgeführt.
- **Anbieterausfall**: Nach einem früheren Erfolg liefert der `QuoteCache` den letzten
  gültigen Stand (`CACHED`, Altersangabe). Ein Kurs einer früheren Sitzung gilt bei
  laufender Sitzung nicht als aktuell (`stale_quote`): dann sind alle Perioden „n. v.“.
  Ohne früheren Stand zeigt eine Zeile mit Sheet-Wert diesen mit dem Hinweis
  „Anbieterdaten nicht verfügbar (<Code>)“, sonst „Daten nicht verfügbar (<Code>)“.
  Fehlertexte, Symbole und Rohantworten gelangen in keine Meldung.
- **Sheet-Ausfall**: Nach einem früheren Erfolg bleibt das gesamte frühere
  `WatchlistBundle` unverändert erhalten (`using_cached_stand`, Meldung mit Alter);
  sonst `available=False` mit „Daten nicht verfügbar“.

## Sheets-Abruf

- Identische Abfrage wie `SheetsAdapter` (`valueRenderOption=UNFORMATTED_VALUE`,
  `dateTimeRenderOption=SERIAL_NUMBER`), ein GET ohne Redirect und mit TLS-Prüfung,
  neutrale Fehlercodes, keine Wiederholung. Die Konstante `SHEET_REQUEST_PARAMS` wird in
  einem Test gegen die Anfrage des Depot-Adapters geprüft. Prozentspalten haben damit
  dieselbe Semantik wie beim Depot (Prozentpunkte, nie skaliert).
- `PORTFOLIO_WATCHLIST_RANGE` erzwingt keine Zeilengrenze. Der Abruf begrenzt die Anfrage
  auf `MAX_WATCHLIST_ROWS` (2000 Zeilen inklusive Überschrift) ab der ersten Bereichszeile;
  größere Bereiche ergeben einen `PARTIAL`-Snapshot mit Warnung `watchlist_row_limit`.
  Eine Antwort mit mehr Zeilen oder Spalten als angefragt ist ungültig.

## Grenzen

- Pro Zeile sind im Kaltstart zwei Providerabrufe nötig (Historie, bei laufender Sitzung
  zusätzlich Quote), sequenziell mit 10 s Timeout. 200+ Zeilen brauchen im Kaltstart
  entsprechend Zeit; danach tragen die TTLs (`PORTFOLIO_QUOTES_TTL_SECONDS`,
  `PORTFOLIO_HISTORY_TTL_SECONDS`). Abschätzung: bei 0,3-1 s je Yahoo-Anfrage grob
  1,5-8 Minuten für 250 Zeilen mit laufender Sitzung (nicht gemessen); bei völligem
  Ausfall begrenzt der Backoff nach drei Fehlern die Dauer (ca. 30 s bei 10-s-Timeouts).
  `load()` blockiert und darf nicht ungeschützt im Renderpfad stehen; die Ansicht (#85)
  ruft es nur hinter einem Spinner mit Statushinweis auf, einmal je Sitzung bzw. bei
  `Aktualisieren` (ein Zeitbudget je Durchgang gibt es nicht). Parallele Sitzungen warten
  auf die Sperre des jeweiligen Koordinators.
- Ein Anbieter-Backoff (drei Fehler in Folge) pausiert weitere Abrufe providerweit. Auch
  vom Anbieter abgelehnte Symbole (z. B. Tippfehler, delistet) zählen als Fehler, und die
  Erholungsprobe nach dem Backoff nimmt den ersten fälligen Schlüssel. Damit abgelehnte
  Symbole die gültigen Zeilen dahinter nicht sperren, ruft der Koordinator Historie und
  (bei laufender Sitzung) Quote in **einer gemeinsamen Reihenfolge** ab: zuerst Schlüssel
  ohne gescheiterte Abrufe (Historien vor Quotes, dann Blattreihenfolge), zuletzt bekannte
  defekte. Der Rang gilt **je Abrufart und Instrument**: Ein Historien-Abruf wird nach dem
  Fehlerzähler seines eigenen Schlüssels (`("history", Instrument)`) im `QuoteCache`
  gereiht, ein Quote-Abruf nach dem Zähler seines Quote-Schlüssels plus dem Historienzähler
  derselben Zeile (ein abgelehntes Symbol scheitert an beidem, der Historienzähler ist
  immer aktuell). Der Quotezähler beeinflusst nie die Historie: Er wird nur durch einen
  erfolgreichen Quote bei laufender Sitzung zurückgesetzt und bleibt nach Börsenschluss
  eingefroren; eine geheilte gültige Zeile mit hohem, eingefrorenem Quotezähler würde sonst
  bis zur nächsten Historien-TTL (Standard 6 h) hinter neu abgelehnten Symbolen warten.
  Bei Gleichstand gilt: Historien vor Quotes, dann Blattreihenfolge (stabile Sortierung).
  Gezählt werden nur tatsächlich versuchte und gescheiterte Abrufe; ein wegen Backoff
  übersprungener Abruf (Anzeige weiter `cached_provider_value` bzw. `provider_backoff`)
  erhöht ihn nicht, ein Erfolg setzt ihn zurück. Der Koordinator führt keinen eigenen
  Fehlerzustand. Die `entries` bleiben in Blattreihenfolge.
  - **Erholungszeit wächst mit der Zahl defekter Zeilen.** Defekte Zeilen ohne bisher
    gescheiterten Abruf haben Rang 0 wie gültige Zeilen (Kaltstart: noch kein Erfolg;
    Warmzustand: Zeilen waren früher gültig, Yahoo lehnt sie jetzt erstmals ab) und nehmen je
    Backoff-Fenster genau eine Erholungsprobe; erst danach haben sie einen Fehlerzähler und
    rücken ans Ende. Weil sich das Backoff-Fenster dabei verdoppelt (bis 300 s), dauert es
    bis zur Erholung der gültigen Zeilen (60 Zeilen, defekte Zeilen am Blattanfang, Laden
    alle 20 s, synthetisch gemessen; Börse offen oder geschlossen, Kaltstart und Warmzustand
    ergeben dieselben Werte):

    | defekte Zeilen vor den gültigen | 3 | 4 | 5 | 6 | 7 | 8 | 10 | 12 | 15 | 20 |
    | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
    | Erholung bis alle gültigen Zeilen `LIVE` | 20 s | 60 s | 120 s | 240 s | 480 s | 780 s | 1380 s | 1980 s | 2880 s | 4380 s |

    Ab der sechsten defekten Zeile kommen je Zeile rund fünf Minuten (Backoff-Obergrenze
    300 s) dazu. Das ist eine bekannte Grenze: Eine nie versuchte defekte Zeile ist ohne Änderung
    an `quote_cache.py` nicht von einer gültigen zu unterscheiden und braucht deshalb eine
    eigene Erholungsprobe. Defekte Zeilen am Ende des Blatts oder zwischen vielen gültigen
    kosten nichts. Bis dahin zeigen im Kaltstart die gültigen Zeilen `provider_backoff`, im
    Warmzustand ihren letzten gültigen Stand (`CACHED`, mit Alter); danach sind sie `LIVE`,
    die defekten Zeilen bleiben `provider_failure` (Kaltstart: `SHEET_VALUE`) bzw. `CACHED`.
  - **Warmzustand**: Nach der Erholung rufen die gültigen Zeilen ihre Daten wieder normal
    ab. Ein eingefrorener Quotezähler (Quote-Fehler in der Sitzung, Heilung nach
    Börsenschluss) verzögert die Historie einer geheilten Zeile nicht mehr: Gemessen mit den
    Produktions-TTLs (60 s / 300 s / 21600 s) sind die gültigen Zeilen auch dann sofort
    wieder `LIVE`, wenn danach ein Block Symbole abgelehnt wird (vorher bis zu einer
    Historien-TTL, 21840 s); ebenso, wenn Defekte an einer offenen und gültige Zeilen nur an
    einer geschlossenen Börse liegen. Nicht abgedeckt (unverändert, auch vor dieser
    Änderung): Der Quotezähler gilt weiter für den Quote-Abruf selbst. Hat eine Zeile am
    Vortag in der Sitzung viele Quote-Fehler gesammelt und wird am nächsten Handelstag in
    der laufenden Sitzung ein Block Symbole abgelehnt, bleibt der Quote dieser Zeile
    (Anzeige `SHEET_VALUE`, `stale_quote`) hinter dem Block, solange dessen Zähler nicht
    höher sind; die Historie ist davon nicht betroffen. Ebenso warten geheilte Zeilen mit
    einem echten, älteren Fehlerzähler hinter neu abgelehnten Symbolen, wenn ein
    Totalausfall ihren erneuten Abruf verhindert hat, bis eine Historien-TTL abläuft (in
    der Zufallssimulation mit 3600-s-TTL einzelne Verläufe mit 3660-4980 s; endlich). Mit
    den Produktions-TTLs dauert das Muster am Folgetag („alle gültigen Zeilen mit
    eingefrorenem Quotezähler, mindestens drei neu abgelehnte davor“) bis zu einer
    Historien-TTL (Standard 6 h); die Zeilen zeigen währenddessen `SHEET_VALUE` mit
    `stale_quote`. Eine Aktualitätsinformation je Schlüssel (Issue #111) würde das beheben.
  - Bei einem echten Totalausfall gilt der Backoff unverändert (gemessen: 30 Anbieter-
    aufrufe in zwei Stunden bei 60 Zeilen); nach dem Ende des Ausfalls sind alle Zeilen
    nach etwa vier Minuten wieder `LIVE`.
- Der Kalender (`venue_calendars(date.today())`) entsteht einmal beim Bau des
  Koordinators. In einem langlebigen Prozess rückt das Fenster nicht nach: nach etwa
  35 Tagen liegt der Zieltag für `Jahr` vor dem ersten Kalendertag (`calendar_incomplete`),
  nach gut einem Jahr endet der Kalender. Die Ansicht (#85) baut den Koordinator deshalb
  bei Datumswechsel neu (`watchlist_session.session_loader`; der Cache geht dabei
  verloren, der nächste Lauf lädt hinter dem Spinner neu).
- Der Koordinator ist threadsicher serialisiert (`load` hält eine Sperre); der Cache lebt
  nur im Prozess.

## Prüfung

`uv run --locked pytest tests/test_us_calendars.py tests/test_watchlist_refresh.py`
(synthetisch, offline, IP-Sockets gesperrt).
