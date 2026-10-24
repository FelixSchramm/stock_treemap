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
Handelsplätze werden verworfen. Es gibt keine Suche, Umbenennung, alternative
Börse, Underlying-, ADR- oder andere Produkt-Ersatzversorgung.

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
Das bisherige `prototype`-Extra bleibt als leeres Kompatibilitäts-Extra erhalten.

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
- `quotes(instruments)` bündelt einen Durchgang: nur fällige Schlüssel werden
  abgerufen. Nach `provider_failure_threshold` (3) aufeinanderfolgenden
  Providerfehlern gilt ein anbieterweites Backoff (gleiche Folge, 15 s bis 300 s)
  für Kurse, FX und Historie; übrige Abrufe entfallen (`provider_backoff`,
  `MISSING`), auch bei `refresh=True`. Bestände sind davon ausgenommen.
  Die Schwelle ist ein Standardwert, keine Besitzervorgabe.
- Fehler nach einem früheren Erfolg liefern den letzten Wert mit
  `source=CACHED_PROVIDER`, unverändertem Abruf-/Kurszeitpunkt (sichtbares Alter)
  und der Warnung `cached_provider_value`; das gilt auch innerhalb der TTL nach
  einem fehlgeschlagenen Abruf. Bestände behalten den letzten nicht
  fehlgeschlagenen Snapshot (`attempted_at` zeigt das Alter) mit der Warnung
  `cached_holdings_snapshot`.
- Die Historien-TTL (6 h) setzt abgeschlossene Zeiträume voraus; der Aufrufer
  übergibt keinen laufenden Handelstag (Aufgabe von #22).
- Der Cache ist nur im Speicher: nach einem Neustart ohne Daten bleibt das
  Ergebnis des Providers (`MISSING`, „Daten nicht verfügbar“); es gibt keinen
  fiktiven letzten Stand.
- `choose_valuation(markt, sheet)`: geprüfter Providerpreis, sonst letzter
  Providerpreis mit Alter, sonst ausdrücklich gekennzeichneter EUR-Sheet-Wert
  (`price_source_fallback`, ohne Kurszeit zusätzlich `sheet_value_quote_time_unknown`).
  Nicht-EUR- oder fehlende Sheet-Werte ersetzen nichts.
- `supports_period_return(valuation)` ist für nicht verfügbare Preise und für Sheet-/manuelle Preise `False`: sie
  bestimmen höchstens die Fläche, erzeugen aber keine Periodenrendite.
