# Kurs-, Historien- und FX-Vertrag

`portfolio_dashboard.market_data` implementiert #18 über den kleinen
`MarketDataProvider`-Vertrag. `YFinanceProvider` verwendet die vorhandenen
`Instrument`, `VerifiedListing`, `QuoteUnit`, `Valuation` und `Provenance`.
Das Modul enthält weder Bestände noch reale Symbole oder eine Standard-Registry.
Der Demo-Einstieg ruft den Adapter nicht auf. Reale Zugriffe bleiben dem späteren,
autorisierten Lader vorbehalten.

## Identität, Einheiten und Status

Vor einem Kurs-/Historienabruf muss `resolve_listing` das genaue Instrument und
Listing auflösen. Das Mapping muss ausdrücklich `provider="yfinance"` nennen.
Für diesen Adapter bezeichnet `trading_venue` den extern geprüften, exakten
Yahoo-`exchangeName`-Code. Die kanonische Yahoo-Symbolschreibweise im Mapping
muss exakt mit dem Antwortfeld `symbol` übereinstimmen. Abweichende Symbole oder
Handelsplätze werden verworfen. Es gibt keine Suche, automatische Umbenennung,
Underlying-, ADR- oder andere Produkt-Ersatzversorgung. Ein ausdrücklich vom
Besitzer gepflegtes alternatives Listing desselben Produkts ist unten beschrieben.

Die Antwort muss eine gültige `exchangeTimezoneName` und die geprüfte Einheit
bestätigen. Ein übereinstimmender Währungscode gilt nur mit Faktor 1. Die vom
geprüften yfinance-Quellcode ausdrücklich benannten Untereinheiten `GBp`, `ZAc`
und `ILA` entsprechen GBP, ZAR und ILS mit Faktor 0,01. Andere oder fehlende
Tokens sowie widersprüchliche Faktoren bleiben unverfügbar. Der Adapter skaliert
keinen Preis und rechnet keine Währung um.

Erfolgreiche Beobachtungen tragen `ValueStatus.AVAILABLE`; ein explizit
beobachteter Kurs von null bleibt gültig. Ausfälle liefern `ValueStatus.MISSING`,
keinen nutzbaren Zahlenwert bzw. keine Historienpunkte und einen stabilen
`DataWarning.code`, etwa `unverified_mapping`, `no_provider_coverage`,
`provider_failure`, `missing_data`, `insufficient_history`, `invalid_history`,
`unknown_exchange_timezone` oder `unknown_or_conflicting_unit`.
Der Adapter übernimmt oder loggt keine Provider-Ausnahmetexte und Rohantworten.
Mehrdeutige Registries und ungültige Aufrufparameter bleiben Programmierfehler.

Die Oberfläche unterscheidet **Keine Kurszuordnung** von
**Kursabruf fehlgeschlagen (Grund)**. Eine fehlende Zuordnung wird auch dann
erkannt, wenn die Position schon eine Instrumentkennung besitzt. Der
Zuordnungs-/Abrufgrund hat Vorrang vor dem allgemeinen Sheet-Fallbackgrund.
Ein gültiger Kurs mit abgelehnter Historie zeigt getrennt
**Kurshistorie nicht verfügbar (Grund)**, beispielsweise **Kurs ungültig**
beim stabilen Historiencode `invalid_price`. Die Codes bleiben am
Bewertungs-/Periodenergebnis erhalten. Tabelle, Positionsdetails und Datenstatus
übersetzen ausschließlich eine feste Allowlist in kurze deutsche Ursachen.
Unbekannte Codes ergeben einen neutralen deutschen Text; Warnungsmeldungen,
Symbole, Rohantworten und Ausnahmetexte werden nie als Fehlerursache ausgegeben.

## Ausdrücklich gepflegte Sheet-Ersatzzuordnung (#101)

Der lokale und der produktive Lader lesen vier optionale, eindeutig benannte
Überschriften innerhalb **N–X**. Hinter den bisherigen Spalten können sie in
**U–X** stehen. Der konfigurierte Sheet-Bereich muss diese Spalten einschließen
und bleibt explizit begrenzt, beispielsweise `'<Tab>'!A1:X500`; unbeschränkte
Spaltenbereiche bleiben gesperrt. Bestehende A–M..A–T-Bereiche funktionieren
weiter, lesen aber keine außerhalb ihres Bereichs liegenden Ersatzangaben.

| Überschrift | Inhalt |
| --- | --- |
| `Yahoo-Symbol` | Exakter, vom Besitzer geprüfter Yahoo-Symbolstring |
| `Yahoo-Handelsplatz` | `GER`, `FRA`, `STU`, `MUN` oder `DUS`; fest EUR, Faktor 1 |
| `Yahoo-Aktienform` | Für Aktien zwingend `share` oder `adr`; für ETFs leer |
| `Yahoo-Anteilsklasse` | Für ETFs zwingend geprüfte Anteilsklasse; für Aktien leer |

Die bestehende ISIN-Überschrift und der Instrumenttyp bestimmen das Produkt.
Alle Angaben müssen vollständig sein; Form/Klasse werden nicht geraten oder
automatisch geerbt. Die Pflege ist dieselbe ausdrückliche Besitzerbestätigung
der Produktidentität wie ein privater TOML-Eintrag. Form und Klasse müssen mit
einem vorhandenen privaten Listing derselben ISIN/Typ-Identität übereinstimmen.
Ein anderes Symbol am **gleichen Handelsplatz** ist zulässig, etwa als explizite
Korrektur eines nicht mehr nutzbaren Symbols. Symbole werden nicht anhand von
Name, Ticker, Suffix, Stammaktie, Index oder Basiswert hergeleitet. Ein technisch
gültiges falsches Produktsymbol ist ohne externe Identitätsprüfung nicht
erkennbar; diese Prüfung bleibt vor der privaten Eintragung beim Besitzer.

Die Auswahl erfolgt einmal für das ganze Produkt, mit einer gemeinsamen Quote
und Historie für alle vier Perioden:

1. Ein vollständig nutzbares primäres App-Listing gewinnt; kein Ersatzabruf.
2. Fehlt es oder ist seine Quote abgelehnt, wird die gepflegte Alternative versucht.
3. Bei nutzbarer primärer Quote mit lückenhaften Perioden gewinnt die Alternative
   nur, wenn ihre verfügbaren Perioden eine **echte Obermenge** bilden. Vorhandene
   primäre Perioden dürfen nicht zugunsten anderer Perioden verloren gehen.
4. Bei Gleichstand oder ungeeignetem Ersatz bleibt die primäre Zuordnung.
   Sind beide Abrufe unbrauchbar, bleiben gekennzeichneter Sheet-Wert/n. v.
   und die sichere Fehlerursache sichtbar. Eine gültige alternative Quote ohne
   nutzbare Historie kann bei fehlender Primärzuordnung weiterhin bewerten;
   Kursperioden bleiben dann n. v. mit dem Historiengrund.

Ein fehlgeschlagenes Listing ist noch kein Anbieterausfall: Der Cache wertet
Fehler je **Position** (primäres Listing plus gepflegter Ersatz, der direkt
danach abgerufen wird). Eine Position erhöht die anbieterweite Fehlerserie
höchstens einmal und nur, wenn alle ihre Kandidaten fehlschlagen; jeder
erfolgreiche Abruf setzt die Serie zurück. Gesunde Ersatzlistings bleiben so
unabhängig von davor liegenden fehlerhaften Listings erreichbar, solange der
Anbieter antwortet. Ein echter Ausfall (alle Kandidaten fehlerhaft) öffnet das
anbieterweite Backoff unverändert. Die Wiederaufnahme prüft zuerst Positionen
ohne Fehlerserie, danach die Positionen mit dem am längsten zurückliegenden
Versuch ihres besten Kandidaten (siehe unten). Reine Ersatzpositionen ohne
primäres App-Listing sowie Positionen ohne Ersatz zählen einzeln und können in
einem ersten Durchgang nachfolgende gesunde Schlüssel sperren; das heilt sich
ab dem nächsten Durchgang nach Ablauf des Backoffs, weil die gesunden
Schlüssel ohne Fehlerserie vor den fehlerhaften geprüft werden.

Referenzkurs und aktueller Kurs werden nie zwischen Listings gemischt. Alle
Provider-Metadaten-, Einheiten-, Kalender-, Split- und Historienprüfungen bleiben
streng. Fehlerhafte/unvollständige optionale Zeilen oder widersprüchliche
Produkt-/Symbolzuordnungen werden einzeln mit neutralem Hinweis ignoriert.
Andere Bestände bleiben verfügbar. Die bisherige Gesamtfehlerstrategie für
ungültige private TOML-Einträge bleibt bestehen. Derivate und USD-Listings
werden in diesen Ladewegen weiterhin nicht unterstützt; USD erfordert unter
anderem den Kalender aus #84 und eine getrennte Besitzerentscheidung.

**Kurszuordnung** in Tabelle und Positionsdetails nennt App/Sheet-Ersatz,
Provider, aktives Symbol und Handelsplatz. Der Status und Datenstatus nennen
die Herkunft ebenfalls, auch in **Seit Kauf**. Ein versuchter, nicht gewählter
Ersatz wird kenntlich gemacht. Ein erfolgreicher Bestandsrefresh ersetzt die
Kandidaten vollständig; Entfernen oder Wechseln einer Sheet-Angabe verhindert
die weitere Nutzung der vorherigen Zuordnung. Der Cache trennt nach dem
vollständigen Listinginstrument einschließlich Provider-Symbol und Handelsplatz.
Ein fehlgeschlagener Bestandsrefresh behält das bisherige atomare Bundle samt
Zuordnung und Perioden; der vorhandene Backoff bleibt wirksam.

## Zeitpunkt und reguläre Sitzung

`latest_quote` lädt reguläre Tagesdaten mit `prepost=False` und verwendet nur
`regularMarketPrice` zusammen mit `regularMarketTime`, niemals Vor-/Nachbörsen-
oder ersatzweise den letzten Tagespreis. Das ist die letzte vom Provider
gemeldete reguläre Beobachtung, keine Echtzeitgarantie. yfinance formatiert
Yahoo-Unixsekunden in einen zeitzonenbewussten `pandas.Timestamp`; beide
ausdrücklichen Formen werden unterstützt. Naive/fehlende Zeitpunkte,
Millisekundenverwechslungen und Zeitpunkte nach dem Abruf werden verworfen.
`Provenance` hält `source=PROVIDER`, `provider="yfinance"`, den tatsächlichen
Kurszeitpunkt und den unabhängig erfassten Abrufzeitpunkt.

`history(start, end, minimum_points=2)` verwendet einen inklusiven Start und ein
exklusives Ende. Jeder `HistoryPoint` enthält das lokale `session_date`, `close`
und das Provider-Splitereignis `split_ratio` (neue/alte Stücke; 0 ohne Ereignis).
Ein Tagesindex um Mitternacht ist kein belegter Schlusskurszeitpunkt:
`PriceHistory.provenance.observed_at` bleibt daher `None`. Zeitzone, Einheit,
Quelle, Abrufzeit und Adjustierungsbasis stehen am Historienergebnis.
Fehlende/ungültige Preise werden nicht übersprungen. Unsortierte, doppelte,
zeitzonenlose oder außerhalb des Fensters liegende Tagesdaten werden verworfen.
`minimum_points` prüft ausschließlich die Beobachtungszahl. Erwartete Handelstage,
Referenzdatum, unvollständiger aktueller Handelstag und Periodenrenditen folgen
in #22; dieses Ergebnis behauptet keine Kalender- oder Periodenvollständigkeit.

## Geprüfte Adjustierungsbasis und Bibliotheksversion

Am 24.10.2026 wurden der aktuelle offizielle Release **yfinance 1.7.0** und sein
Quellcode geprüft (Tag-Commit `3d9d2f0cacb662bff689874cd6113bae3a30a885`).
Die Laufzeitabhängigkeit ist exakt auf diese schon zuvor im Lock vorhandene
Version festgelegt. Es wurden keine sonstigen Paketversionen aktualisiert.

- [`PriceHistory.history`](https://github.com/ranaroussi/yfinance/blob/1.7.0/yfinance/scrapers/history.py)
  hat standardmäßig `auto_adjust=True`, `back_adjust=False`, `repair=False`,
  `prepost=False`, `keepna=False`. Der Adapter setzt alle explizit; insbesondere
  `auto_adjust=False`, `back_adjust=False`, `repair=False`, `actions=True`,
  `keepna=True`, `rounding=False`, `timeout=10` und `raise_errors=True`.
  Der unterstützte, inzwischen als veraltet markierte `raise_errors`-Parameter
  verhindert den normalen Fehler-Logging-Pfad dieser festgelegten Version.
- [`parse_quotes` und `auto_adjust`](https://github.com/ranaroussi/yfinance/blob/1.7.0/yfinance/utils.py)
  unterscheiden Yahoo-`Close` und `Adj Close`. `auto_adjust` ersetzt `Close`
  durch `Adj Close` und verwendet das Verhältnis für OHLC. Die synthetischen
  Vertragstests prüfen diese tatsächlichen Bibliotheksfunktionen und Defaults,
  einschließlich der `regularMarketTime`-Formatierung in Unixsekunden.
- Die offizielle [Reparaturdokumentation](https://github.com/ranaroussi/yfinance/blob/1.7.0/doc/source/advanced/price_repair.rst)
  unterscheidet fehlende Dividendenadjustierung von `Adj Close` und fehlende
  Splitadjustierung vorheriger Preise. `_fix_bad_stock_splits` im offiziellen
  Quellcode erklärt ausdrücklich, dass Yahoo frühere Preise normalerweise bereits
  für neue Splits adjustiert und manchmal fehlerhaft gar nicht oder doppelt.

Die Basis heißt daher `YAHOO_SPLIT_ADJUSTED_PRICE`:
**Yahoo Close, splitbereinigt, ohne Dividendenadjustierung**. `auto_adjust=False`
bedeutet hier nicht splitunbereinigt. Splitfaktoren werden ausschließlich als
Ereignismetadaten weitergegeben und niemals erneut auf `Close` angewandt.
`Adj Close`, Dividenden und Kapitalausschüttungen gehen nicht in Preise ein.
Es gibt keine automatische Datenreparatur oder pauschale Neuadjustierung.
Die Basis beschreibt die belegte Provider-Semantik; sie garantiert nicht die
Fehlerfreiheit einzelner Yahoo-Werte. Synthetische Tests und Quellenprüfung
belegen keine reale Produktabdeckung oder Erreichbarkeit.

## FX ohne Richtungsinferenz

`VerifiedFxPair(base_unit, quote_unit, mapping, trading_venue)` erklärt die extern
geprüfte Richtung und beide vollständigen Währungseinheiten: Der Zahlenwert ist
**Quote-Einheiten pro einer Base-Einheit**. Die Zeichenfolge des Symbols hat
keine fachliche Bedeutung für die Richtung. Das verlangte Mapping und Paar
bleiben im `FxObservation` erhalten. Das Antwort-Währungstoken muss zur
Quote-Einheit passen; das Base-Instrument und die Richtung gehören zum expliziten
Prüfnachweis des Aufrufers. Es gibt keine Invertierung, Ersatzpaar-Suche oder
automatischen Identitätskurs. FX verlangt einen positiven endlichen Wert und
denselben belegten regulären Kurszeitpunkt. Ein späterer Lader kann dieses
Ergebnis nach Einheitenprüfung in den separaten Bewertungsvertrag überführen;
der Adapter importiert keine Bewertungsrechnung.

## Prüfung und Betriebsgrenze

`uv run --locked pytest tests/test_market_data.py` verwendet ausschließlich
erfundene `Ticker`-Fixtures bzw. synthetische Eingaben für die festgelegte
Bibliothek. Die vollständige Testsuite sperrt IP-Sockets über `pytest-socket`.
Es werden keine echten Kurse, Sheets, Secrets, ADC-Dateien oder Identitäten
gelesen. Die Standard-Ticker-Fabrik wird erst bei einem expliziten Methodenaufruf
importiert; Factory und Uhr sind für Tests injizierbar. Geerbte Proxy- und
TLS-Konfigurationen werden nicht verändert oder deaktiviert.

Yahoo-Konnektivität aus der Zielumgebung ist weiterhin **ungeprüft**. Reale
Zuordnungen und Abdeckung bleiben in #17 offen. Cache, Bündelung und Backoff
folgen in #19; UI, Authentifizierung, Lader und Bewertung sind nicht Teil von #18.

## Cache, Backoff und Ersatzwerte (#19)

`portfolio_dashboard.quote_cache.QuoteCache` umschließt einen
`MarketDataProvider` mit einem Speicher-Cache ohne Hintergrund-Scheduler. Uhr und
`CacheTTLs` (Standard: Kurse/FX 60 s, Bestände 5 min, Historien 6 h) werden
injiziert; `quote`, `fx`, `history` und `holdings` nutzen dieselben Regeln.

- Innerhalb der TTL wird der gespeicherte Wert unverändert ohne Abruf geliefert.
- `refresh=True` (manuelles Aktualisieren) überspringt nur die TTL, nicht das Backoff.
- Fehler erzeugen ein Backoff je Schlüssel (15 s, verdoppelt, höchstens 300 s;
  Konstruktorparameter). Solange es läuft, erfolgt kein Providerabruf. Ein Erfolg setzt es zurück.
- `quotes(instruments, replacements=...)` bündelt einen Durchgang: nur fällige
  Schlüssel werden abgerufen, Positionen ohne Fehlerserie zuerst (in
  Eingabereihenfolge), danach die mit dem am längsten zurückliegenden Abrufversuch
  (bester Kandidat aus Primär- und Ersatzlisting; `probe_order_key`). Eine
  Position zählt höchstens einen
  Providerfehler und nur, wenn auch ihre Ersatzlistings fehlschlagen. Die Rückgabe bleibt in Eingabereihenfolge. Dadurch
  belegen dauerhaft abgelehnte Symbole nicht immer die erste Erholungsprobe.
  Nach `provider_failure_threshold` (3) aufeinanderfolgenden Providerfehlern
  (Warncode `provider_failure`, auch bei vom Provider abgelehnten Symbolen;
  fehlende Zuordnungen und Validierungsfehler zählen nicht) gilt ein
  anbieterweites Backoff (gleiche Folge, 15 s bis 300 s)
  für Kurse, FX und Historie; übrige Abrufe entfallen (`provider_backoff`,
  `MISSING`), auch bei `refresh=True`. Bestände sind davon ausgenommen.
  Die Schwelle ist ein Standardwert, keine Besitzervorgabe.
  **Probeabruf bei offenem Backoff (#111):** Jedes Backoff-Fenster (höchstens
  300 s) lässt genau einen Probeabruf zu, den ersten fälligen Schlüssel der
  Reihenfolge. Die Reihenfolge richtet sich nicht nach der Fehlerzahl: Sonst
  wartete eine wieder gesunde Position mit langer Fehlerhistorie, bis neuere,
  dauerhaft fehlerhafte Positionen mehr Fehler gesammelt hätten. Stattdessen
  zählt der Zeitpunkt des letzten tatsächlichen Abrufversuchs
  (`QuoteCache.attempts()`, nur Abrufe beim Anbieter, keine Cache-Treffer und
  keine Backoff-Auslassungen); jede Probe rückt die Position ans Ende der Runde.
  **Höchstzahl:** Eine Position wird nach höchstens so vielen Backoff-Fenstern
  erneut geprüft, wie Positionen gerade fehlschlagen (höchstens ihre Anzahl,
  also bei Refreshes im Fensterabstand höchstens so viele Refreshes). Beispiel
  mit Refresh alle 10 Minuten, zwei dauerhaft fehlerhaften Nachbarn und Reparatur
  bei +40 Minuten: erneut geprüft spätestens bei +60 Minuten (vorher nach
  Fehlerzahl je nach Vorgeschichte 130 bis 490 Minuten). Der Backoff wird nicht
  aufgeweicht: Bei echtem Ausfall bleibt es bei einem Abruf je Fenster (30
  Anbieteraufrufe in zwei Stunden bei 60 und 250 Positionen, vor und nach der
  Änderung gleich); die Probe geht reihum statt immer an dieselbe Position. Ein
  Erfolg setzt das Backoff weiter für alle zurück.
  Ein Schlüssel, der schon in einer eigenen Fehlerserie steckt (etwa ein
  dauerhaft abgelehntes Symbol), wird nur direkt nach dem Erfolg eines zuvor
  fehlerfreien Schlüssels ohne neuen Zwischenfehler ignoriert. Abgelehnte
  Symbole sperren daher nach einem gesunden Durchgang weder FX- und
  Historienabrufe noch andere Schlüssel, während nach einer bloßen
  Teil-Erholung eines Ausfalls das anbieterweite Backoff bei erneuten Fehlern
  wieder greift. Beim ersten Laden nach einem Neustart lassen sich abgelehnte
  Symbole von einem Ausfall nicht unterscheiden.
- Fehler nach einem früheren Erfolg liefern den letzten Wert mit
  `source=CACHED_PROVIDER`, unverändertem Abruf-/Kurszeitpunkt (sichtbares Alter)
  und der Warnung `cached_provider_value`; das gilt auch innerhalb der TTL nach
  einem fehlgeschlagenen Abruf. Bestände behalten den letzten nicht
  fehlgeschlagenen Snapshot (`attempted_at` zeigt das Alter) mit der Warnung
  `cached_holdings_snapshot`.
- Die Historien-TTL (6 h) gilt nur bis zum nächsten Sitzungsende. Eine Tageshistorie,
  die während einer laufenden Sitzung abgerufen wurde, enthält als letzten Balken nur
  den Zwischenstand des Tages (Abruf vor der Eröffnung: noch keinen Balken). Übergibt
  der Aufrufer den Kalender (`history(..., sessions=calendar)`; Depot und Watchlist
  tun das), gilt der Eintrag nicht mehr als frisch, sobald seit dem Abruf eine
  reguläre Sitzung geschlossen hat (`price_periods.session_closed_between`: Schlusszeit
  in der Zeitzone der Historie, daher mit Sommerzeitwechseln; Wochenenden und
  Börsenfeiertage zählen nicht). Dann wird erneut abgerufen (Backoff wie sonst); schlägt das
  fehl, bleibt der alte Eintrag als `CACHED_PROVIDER` mit Alter sichtbar. Ein Abruf
  ab der Schlusszeit gilt als endgültig und wieder für die TTL; eine nach der
  Schlusszeit noch verzögert gelieferte Zwischenstand-Historie bleibt daher
  möglich (Feed-Verzögerung, kein Abgleich mit der Quote). Ohne `sessions` gilt nur die TTL.
- Der Cache ist nur im Speicher: nach einem Neustart ohne Daten bleibt das
  Ergebnis des Providers (`MISSING`, „Daten nicht verfügbar“); es gibt keinen
  fiktiven letzten Stand.
- `choose_valuation(markt, sheet)`: geprüfter Providerpreis, sonst letzter
  Providerpreis mit Alter, sonst ausdrücklich gekennzeichneter EUR-Sheet-Wert
  (`price_source_fallback`, ohne Kurszeit zusätzlich `sheet_value_quote_time_unknown`).
  Nicht-EUR- oder fehlende Sheet-Werte ersetzen nichts.
- `supports_period_return(valuation)` ist für nicht verfügbare Preise und für Sheet-/manuelle Preise `False`: sie
  bestimmen höchstens die Fläche, erzeugen aber keine Periodenrendite.
