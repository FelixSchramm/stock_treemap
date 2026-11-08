# Datenquelle und Importmapping

Dieses Dokument enthält nur das allgemeine Importschema. Dokumentlink, Spreadsheet-ID, Blatt-IDs und konkrete Depotbefunde werden ausschließlich in lokaler Konfiguration bzw. im ignorierten Ordner `docs/private/` geführt. Keine echten Depotdaten in Issues, Testfixtures oder Logs übernehmen.

## Zugriff und Tabellen (ADR 0012)

Die Anwendung liest das konfigurierte Google Sheet ausschließlich lesend, und zwar die drei Tabellen
`Instrumente`, `Transaktionen` und `Kurse_manuell` in einem einzigen `values:batchGet` mit festen,
begrenzten Bereichen (`A1:L2000`, `A1:L10000`, `A1:C2000`). Zeile 1 enthält die Spaltennamen; ihre
Reihenfolge ist frei, jeder Name muss genau einmal vorkommen. Ein fehlender oder doppelter Name
ergibt einen `FAILED`-Snapshot mit Tabelle und Spalte. Leere Zeilen werden übersprungen.
Entscheidung und Datenmodell: [ADR 0012](adr/0012-sheet-datenmodell.md). Code:
`sheet_tables.py` (Tabellen, Bestand, Listings), `ledger.py` (FIFO-Journal), `sheets_adapter.JournalSheetsReader` (Abruf).

| Tabelle | Spalten | Verwendung |
| --- | --- | --- |
| `Instrumente` | `isin`, `name`, `typ`, `aktienform`, `anteilsklasse`, `basiswert_isin`, `kategorie`, `google_symbol`, `yahoo_symbol`, `yahoo_handelsplatz`, `waehrung`, `geprueft` | Eine Zeile je Wertpapier. Die ISIN ist die `instrument_id`. Ein Listing gilt nur mit Datum in `geprueft` als geprüft. |
| `Transaktionen` | `txn_id`, `datum`, `isin`, `art`, `stueck`, `kurs_eur`, `gebuehr_eur`, `steuern_eur`, `betrag_eur`, `bezug_txn_id`, `broker_ref`, `notiz` | Journal; Bestand und Einstand der verbleibenden Stücke nach FIFO. |
| `Kurse_manuell` | `isin`, `datum`, `kurs_eur` | Gekennzeichneter EUR-Ersatzkurs; Formeln (z. B. GOOGLEFINANCE) erlaubt. |

## Regeln

- **Identität:** Name, Anzeigekürzel und Zeilennummer sind nie eine ID. Doppelte ISINs in `Instrumente` werden ab der zweiten Zeile ignoriert.
- **Kurszuordnung des Depots:** Nur geprüfte Listings auf `GER`, `FRA`, `STU`, `MUN` oder `DUS` in EUR. Aktien brauchen `aktienform` (`share` oder `adr`), ETFs `anteilsklasse`; Derivate erhalten nie eine Kurszuordnung. Ein Yahoo-Symbol, das mehreren ISINs zugeordnet ist, wird für alle verworfen.
- **Journal:** Reihenfolge nach `datum`, bei gleichem Tag nach `txn_id`. `Kauf`, `Sparplan` und `Einbuchung` sind Zugänge, `Verkauf` und `Ausbuchung` Abgänge. `betrag_eur` ist bei Zugängen die Belastung inklusive Gebühren, bei Verkäufen der Erlös vor Steuern. Eine `Einbuchung` mit `bezug_txn_id` übernimmt den FIFO-Einstand der referenzierten `Ausbuchung`; eine `Ausbuchung` ohne Übernahme ist ein realisierter Verlust.
- **Fehler bleiben lokal:** Ungültige Zeilen, doppelte `txn_id`/`broker_ref`, Abgänge über den Bestand oder falsche Verweise machen nur die betroffene ISIN „n. v.“; der Snapshot ist dann `PARTIAL`. Eine Transaktion ohne Zeile in `Instrumente` wird gemeldet.
- **Einstand:** Ein fehlerfreies Journal ergibt `CostBasisStatus.VERIFIED_REMAINING`.
- **Sheet-Kurs:** Je ISIN gewinnt der jüngste datierte Eintrag, ohne datierten Eintrag der letzte undatierte. Ohne Datum gilt „Sheet-Wert, Kursstand unbekannt“. Fehlt jeder Kurs, bleibt die Bewertung `MISSING`.
- **Zahlen und Daten:** Zahlen als Zahl oder deutsch formatierter Text; Daten als Sheets-Seriennummer, `TT.MM.JJJJ` oder ISO.
- Warnungen nennen Tabelle, Feld und Zeile, nie Zellwerte. Echte Daten gehören weder in Tests noch in Logs.

Das frühere Depotblatt (Spalten A–M) bleibt im Sheet als Formelansicht und wird von der App nicht mehr gelesen.

## Watchlist-Blatt

Die Watchlist liegt in einem eigenen Blatt derselben Datei und wird über `PORTFOLIO_WATCHLIST_RANGE` (`Blatt!A1:D300` bis `Blatt!A1:T…`, erste Zeile = Überschriften) ausgewählt; Entscheidung: [ADR 0011](adr/0011-watchlist.md). Sie enthält reine Kursentwicklung, keine Stückzahlen, keinen Einstand und keinen Zielkurs.

| Spalte | Überschrift | Verwendung |
| --- | --- | --- |
| A–D (Grundspalten, je genau einmal) | `Position` | Name, **Pflicht** |
| | `Letzter Kurs (EUR)` | optionaler Sheet-Kurs, gekennzeichneter Ersatzwert „Sheet-Wert, Kursstand unbekannt“ |
| | `Veränd. (%)` | optionale Sheet-Tagesänderung in Prozentpunkten (20 = 20 %, nicht skaliert) |
| | `Nettoveränd. (EUR)` | optionale Sheet-Tagesänderung in EUR |
| ab E, über Überschrift (je höchstens einmal, bis Spalte T) | `Ticker` | Anzeige, keine ID |
| | `Typ` | `Aktie`, `ETF`; `Derivat` erhält keine Zuordnung |
| | `Yahoo-Symbol` | Kandidat für den yfinance-Abruf (Großschreibung wird vereinheitlicht) |
| | `Handelsplatz` | Yahoo-`exchangeName`: `GER`, `FRA`, `STU`, `MUN`, `DUS` (Berlin) oder `NMS`, `NGM`, `NCM`, `NYQ`, `ASE`, `PCX` (New York) |
| | `Währung` | dreibuchstabiger Code in Großbuchstaben, z. B. `EUR`, `USD` |
| | `Kategorie` | Anzeige |

Eine Zeile mit nur A–D ist gültig: `Heute` zeigt den Sheet-Wert als Ersatzwert, `Woche`, `Monat` und `Jahr` zeigen „n. v.“. Eine Zeile mit vollständiger und widerspruchsfreier Zuordnung (`Yahoo-Symbol`, `Handelsplatz`, `Währung`) erhält alle vier Perioden (Abruf: [WATCHLIST_QUOTES.md](WATCHLIST_QUOTES.md); Ansicht: [WATCHLIST_VIEW.md](WATCHLIST_VIEW.md)); jede unvollständige, widersprüchliche, doppelte oder derivatbezogene Zuordnung ergibt eine Zeilenwarnung und den Sheet-Wert. Dies ist die bewusste Ausnahme zu ADR 0005 (Pflege im Blatt statt in `listings.toml`); der Laufzeitabgleich gegen die Yahoo-Metadaten bleibt Pflicht. Parser: [SHEET_PARSER.md](SHEET_PARSER.md#watchlist-parser).

Im Besitzer-Sheet sind `Yahoo-Symbol`, `Handelsplatz` und `Währung` Formeln, die aus `Instrumente` lesen, und nur bei gesetztem `geprueft` gefüllt ([ADR 0012](adr/0012-sheet-datenmodell.md)).

Referenzen: [ADR 0004](adr/0004-google-sheets.md), [ADR 0005](adr/0005-instrumente-und-kursdaten.md), [ADR 0007](adr/0007-bewertung-und-einstand.md), [ADR 0011](adr/0011-watchlist.md), [ADR 0012](adr/0012-sheet-datenmodell.md).
