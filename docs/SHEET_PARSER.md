# Sheet-Zeilen offline parsen

`portfolio_dashboard.sheet_parser.parse_sheet_rows` übernimmt ausschließlich
bereits übergebene Überschriften und Zellen. Es liest weder Sheets noch Dateien,
Credentials, Provider oder die aktuelle Uhr. Der spätere Adapter muss Zugriff,
Abruf und Quellenkonfiguration separat behandeln.

## Eingabe und Zahlenformat

Alle dreizehn Überschriften aus [DATA_SOURCE.md](DATA_SOURCE.md) müssen innerhalb
von A–M genau einmal vorkommen. Ihre Reihenfolge darf wechseln; Großschreibung
und wiederholte Leerzeichen ändern das Mapping nicht. Fehlende oder doppelte
Überschriften ergeben einen `FAILED`-Snapshot mit Feld und Überschriftenzeile.
Zellen rechts von M werden ignoriert, auch wenn dort gleichnamige Aggregate
stehen. Ausnahme sind die optionalen Hinweisspalten *ISIN*, *Handelsplatz* und
*Kursdatum…* in N–T (siehe [DATA_SOURCE.md](DATA_SOURCE.md)): ISIN und Handelsplatz
landen als `source_isin`/`source_venue` ohne jede Identitäts- oder Providerwirkung,
eine ungültige ISIN wird mit `invalid_isin` verworfen. Das Kursdatum (`dd.mm.yyyy`,
ISO oder Sheets-Seriennummer, nicht nach `imported_at`) setzt den Kursstand der
Zeile; ungültig ergibt `invalid_price_date` und der Kursstand bleibt unbekannt. Zeilen wie
„Brokerage-Gesamtwert…“ oder „Differenz zu…“ ohne Typ zählen als Summenzeilen. Kurze Datenzeilen erhalten fehlende Zellen; `header_row` bestimmt den
tatsächlichen Zeilenbezug eines konfigurierten Ausschnitts.

`SheetMetadata.number_locale` ist erforderlich: `NumberLocale.GERMAN` liest
`1.234,125`, `NumberLocale.ENGLISH` liest `1,234.125`. Es gibt keine automatische
Erkennung: `1.234` bedeutet je nach explizitem Format 1234 oder 1,234. Falsch
gruppierte bzw. gemischte Formate werden abgelehnt. Geschützte oder normale
Leerzeichen können Dreiergruppen trennen. Numerische Zellen werden als bereits
unformatierte Zahlen behandelt und ohne Rundung über `Decimal` übernommen;
Boolesche und nicht endliche Werte sind ungültig. EUR-Spalten erlauben einen
einzelnen `EUR`-/`€`-Zusatz. Leere Zellen, `None` und `k. A.` bleiben unbekannt.

## Ergebnis und fachliche Grenzen

- `result.snapshot.positions` enthält akzeptierte **vorzeichenbehaftete**
  Nichtnullbestände, ohne Zusammenführung. `result.active_positions` enthält
  ausschließlich positive Bestände; `result.negative_positions` hält negative
  Bestände für die gesonderte Prüfung vor. Ein Anzeige-Ticker wird niemals zur
  Instrument-ID oder Provider-Zuordnung, auch nicht bei gleichen Tickern in
  Aktien-, ETF- und Derivatezeilen. `Typ` akzeptiert Aktie, ETF und Derivat.
- Leere Zeilen, beschriftete Summen-/Vergleichszeilen ohne Instrumenttyp und
  Nullbestände stehen mit Grund in `excluded_rows`. Eine benannte Position mit
  gültigem Typ bleibt erhalten, auch wenn ihr Name beispielsweise „Total“ lautet.
  Fehlende Namen, unbekannte Typen und fehlende/ungültige Mengen lehnen die Zeile
  ab und ergeben `PARTIAL`. Jede solche Warnung nennt Feld und Quellenzeile.
  Ein vollständig leerer Import bleibt ein erfolgreicher `COMPLETE`-Snapshot.
- Fehlende oder ungültige Kurse/Einstände entfernen den Bestand nicht. Sie
  erhalten die jeweiligen `MISSING`-/`INVALID`-Statuswerte und Warnungen. Ein
  ausdrücklich gelesener Nullkurs bleibt `AVAILABLE` mit Wert null; negative
  Preise und Einstände sind ungültig. Null-Einstand ermöglicht keinen Seit-Kauf-
  Prozentwert.
- Spalte G ist laut Schema ein EUR-Ersatzkurs pro Stück und trägt `SHEET`-Herkunft
  sowie EUR mit Faktor 1. Eine Währung aus F bleibt davon getrennt; ihr
  Haupt-/Untereinheitenfaktor ist unbekannt. Fehlende Währungen werden nicht als
  EUR interpretiert; uneindeutige Bezeichnungen wie `GBp` bleiben unbekannt.
  `imported_at` ist der übergebene Abruf-/Importzeitpunkt, niemals der Kursstand.
  Einen tatsächlichen Kursstand liefern nur ein gültiges Zeilen-Kursdatum (nicht
  nach `imported_at`) oder explizites `sheet_price_observed_at`: Das
  Zeilen-Kursdatum hat Vorrang vor `sheet_price_observed_at`; ist die Zelle leer,
  `None` oder `k. A.`, gilt `sheet_price_observed_at`; ist das Zeilen-Kursdatum
  ungültig (einschließlich Zukunftsdatum), bleibt `observed_at=None`, das
  Metadatum wird nicht übernommen und der Ersatzkurs trägt eine Warnung.
- Eingelesene Einstände sind standardmäßig `UNVERIFIED`. Nur die explizite
  Quellenfeststellung `cost_basis_verified_remaining=True` bestätigt die Bedeutung
  als Einstand verbleibender Stücke. Diese Feststellung ist für die tatsächliche
  Besitzerquelle weiterhin offen; reale Seit-Kauf-Renditen bleiben unverfügbar.
- `references` bewahrt H/J/K/M nur zur späteren Plausibilisierung. Prozentangaben
  bleiben in ihrer gelesenen Größenordnung: J `20` bzw. `20 %` bedeutet 20
  Prozentpunkte und wird nicht mit 100 multipliziert. Auch M wird nicht anhand
  von Zellformatierungen umskaliert. Nichtnumerische Ergebniszellen, negative
  H-Werte bei positiven Beständen und Gewichte außerhalb 0–100 erzeugen Warnungen
  und einen unbekannten Referenzwert. Referenzen überschreiben keine Eingänge;
  Marktwerte, Renditen, Gewichte und Summen werden hier nicht berechnet.

Warnungstexte enthalten nur feste Schemaerklärungen, keine Rohzellen oder
Dokumentkennungen. Die zurückgegebenen Positionen enthalten naturgemäß die
übergebenen Bestandsfelder und gehören nicht in ungeschützte Logs.

Synthetisches Beispiel:

```python
from datetime import datetime, timezone
from portfolio_dashboard.sheet_parser import (
    NumberLocale, SHEET_HEADERS, SheetMetadata, parse_sheet_rows,
)

result = parse_sheet_rows(
    SHEET_HEADERS,
    [["Synthetischer ETF", "DEMO", "ETF", "0,125"]],
    imported_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
    metadata=SheetMetadata(NumberLocale.GERMAN),
)
assert len(result.active_positions) == 1
assert result.active_positions[0].instrument.instrument_id is None
assert not result.active_positions[0].cost_basis.usable_for_since_purchase
```

Prüfung: `uv run --locked pytest tests/test_sheet_parser.py`. Die Tests verwenden
ausschließlich synthetische Zellen und prüfen auch Import und Parsing im frischen
Prozess mit gesperrten UI-/Provider-Paketen und gesperrtem Netzwerk.

## Watchlist-Parser

`portfolio_dashboard.watchlist_sheet.parse_watchlist_rows(headers, rows, imported_at=..., number_locale=NumberLocale.GERMAN, header_row=1)`
liest das Watchlist-Blatt nach [ADR 0011](adr/0011-watchlist.md) mit denselben
Eigenschaften wie der Depot-Parser: keine Quelle, kein Netzwerk, keine Uhr, feste
Warnungstexte ohne Zellwerte, Zahlenformat nur über `NumberLocale`. Schema und
Handelsplätze: [DATA_SOURCE.md](DATA_SOURCE.md#watchlist-blatt).

- Ergebnis: `WatchlistParseResult.snapshot` (`WatchlistSnapshot` mit `attempted_at`, `status`, `items`, `warnings`) und `excluded_rows`. `COMPLETE` darf Zeilenwarnungen tragen, `PARTIAL` bedeutet abgelehnte Zeilen (fehlender `Position`-Name), `FAILED` ein fehlendes oder doppeltes Grundspalten-Etikett in A–D (ohne Items).
- `WatchlistItem`: `name`, `source_row`, `sheet_price` (`Valuation`: EUR, `SHEET`, Kursstand unbekannt; `MISSING` bei leerer Zelle ohne Warnung), `sheet_change_percent_points`, `sheet_net_change_eur`, `status`, `mapping`, `ticker`, `instrument_type`, `category`, `warnings`. Die Anzeigebezeichnung des Ersatzwertes ist `SHEET_VALUE_LABEL`.
- `WatchlistRowStatus`: `SHEET_ONLY` (keine Yahoo-Angabe), `MAPPED` (`WatchlistMapping(symbol, exchange, currency, instrument_type)` mit `timezone`; nur ein Kandidat ohne `instrument_id`), `FALLBACK` (Hinweis, nur Sheet-Wert).
- Warncodes der Zuordnung: `incomplete_mapping`, `invalid_yahoo_symbol`, `unknown_exchange`, `invalid_currency`, `symbol_exchange_conflict`, `currency_exchange_conflict`, `duplicate_yahoo_symbol`, `unsupported_derivative`, `invalid_instrument_type`, `invalid_text`; Zahlen: `invalid_price`, `invalid_change`, `invalid_net_change`; Schema: `missing_column`, `duplicate_column`; Zeile: `invalid_name`.
- Leere Zeilen stehen in `excluded_rows`. Das Bereichsformat prüft `config.parse_watchlist_range` (`WatchlistRange` mit `first_row`, `last_row`, `column_count`); ein Abruf des Blatts ist nicht Teil dieses Moduls.

Zuordnung zu Portfolio und Bewertungsschlüssel (#95): [PRIVATE_WATCHLIST.md](PRIVATE_WATCHLIST.md).

Prüfung: `uv run --locked pytest tests/test_watchlist_sheet.py` (synthetisch, auch 280 Zeilen).
