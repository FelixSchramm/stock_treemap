# Datenquelle und Importmapping

Dieses Dokument enthält nur das allgemeine Importschema. Dokumentlink, Spreadsheet-ID, Blatt-IDs und konkrete Depotbefunde werden ausschließlich in lokaler Konfiguration bzw. im ignorierten Ordner `docs/private/` geführt. Keine echten Depotdaten in Issues, Testfixtures oder Logs übernehmen.

## Zugriff und Bereichserkennung

Die Anwendung liest das konfigurierte Google Sheet ausschließlich lesend. Der Laufzeitzugang benötigt einen eigenen Service-Account; der Chat-Connector ersetzt diesen nicht. Blatt und Bereich sind konfigurierbar. Positionszeilen werden anhand der Überschriften und Zeilentypen erkannt; Zusammenfassungen, Vergleichszeilen und rechte Aggregate sind keine zusätzlichen Positionen. Zeilennummern dienen der Fehlerlokalisierung, nicht als dauerhafte Instrument-ID.

Die konkrete Quelle und die Ergebnisse des ersten Leseabgleichs liegen lokal unter `docs/private/DATA_SOURCE.md`. Diese Datei gehört weder ins Repository noch in den Container.

## Spaltenmapping

| Spalte | Originalüberschrift | Internes Feld / Verwendung |
| --- | --- | --- |
| A | Position | `name` |
| B | Ticker | `display_ticker`, keine eindeutige ID |
| C | Typ | `instrument_type`: Aktie, ETF, Derivat |
| D | Anzahl | `quantity`; numerisch, Bruchstücke erlaubt |
| E | Symbol (Google Finance) | `source_symbol`; Handelsplatzhinweis, explizit auf Provider abbilden |
| F | Währung | `quote_currency`, falls belegt |
| G | Kurs (EUR) | `sheet_price_eur`, ggf. gekennzeichneter Ersatzwert |
| H | Aktueller Wert (EUR) | Plausibilisierung; aus Bestand und Preis neu berechnen |
| I | Investiert (EUR) | `cost_basis_eur`; Bedeutung nach Teilverkäufen prüfen |
| J | G/V (%) | Referenz zur Plausibilisierung; neu berechnen |
| K | G/V (EUR) | Referenz zur Plausibilisierung; neu berechnen |
| L | Kategorie | `category` |
| M | Gewicht (%) | Referenz; aus derselben Bewertungsmenge neu berechnen |

Optionale Hinweisspalten rechts von M (gesucht in N–X, per Überschrift, nur bei genau einem Treffer):

| Überschrift | Internes Feld / Verwendung |
| --- | --- |
| ISIN | `source_isin`: **ungeprüfter** Hinweis, keine Instrument-ID; die geprüfte Zuordnung bleibt in `instruments.py` |
| Handelsplatz | `source_venue`: ungeprüfter Hinweis, nicht `trading_venue` |
| Kursdatum… | Kursstand des Sheet-Kurses dieser Zeile; Tagesgenauigkeit (Beginn des Berliner Tages) |

Zusätzlich sind die ausdrücklich gepflegten Ersatzspalten `Yahoo-Symbol`,
`Yahoo-Handelsplatz`, `Yahoo-Aktienform` und `Yahoo-Anteilsklasse` innerhalb N–X
möglich (typischerweise U–X). Sie sind getrennte Besitzerangaben zur exakten
ISIN-/Typ-Produktidentität; der ursprüngliche Google-Finance-Symbolhinweis wird
dadurch nicht zur Yahoo-Suche. Vollständiger Vertrag, Prüfung und Priorität:
[MARKET_DATA.md](MARKET_DATA.md). Der begrenzte Importbereich muss bis zur letzten
verwendeten Zusatzspalte reichen.

Die erwarteten J-Formeln erzeugen Prozentpunkte (`× 100`), keine Bruchteile: 20 bedeutet 20 %, nicht 2000 %. Eingelesene Referenzwerte dürfen nicht nochmals mit 100 multipliziert werden. Die Anwendung berechnet ihre Kennzahlen aus Eingangsgrößen.

Das Modell ergänzt eine geprüfte `instrument_id`, Provider-Zuordnung, Währungseinheit, Quellenzeile, Importzeitpunkt und je Bewertung Quelle/Kurszeitpunkt/Status. Geld und Mengen bleiben numerisch. Leere Felder und „k. A.“ bedeuten fehlende Daten.

## Regeln und offene Prüfungen

- Anzeige-Ticker, Handelsplatzsymbol und Instrumentidentität unterscheiden; ADRs und lokale Aktien sowie unterschiedliche Listings nicht verwechseln.
- ETFs über Anteilsklasse, ISIN/WKN und Listing identifizieren; Derivate benötigen eine eigene Produktkennung. Underlying-Kurse sind kein Ersatz.
- Feste Sheet-Kurse ohne belegten Kurszeitpunkt als Ersatzwerte mit unbekanntem Kursstand kennzeichnen.
- Fehlende oder ungültige Einstände bleiben unbekannt. Nullbestände gehören nicht zur aktiven Kostenbasis; negative Bestände werden gesondert ausgewiesen.
- Prüfen, ob der Einstand nach Teilverkäufen den verbleibenden Stücken entspricht. Zusammengefasste Kennzahlen benötigen dieselbe gültige Positionsmenge für Wert und Einstand.
- Sheet-Zeitzone und Abrufzeit sind weder Börsenzeitzone noch tatsächlicher Kurszeitpunkt.
- Vor Veröffentlichung Instrumentabdeckung, Einstandssemantik und eigenen Laufzeitzugang verifizieren. Konkrete Ergebnisse bleiben in der geschützten Laufzeitkonfiguration oder lokalen Dokumentation.

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

Referenzen: [ADR 0004](adr/0004-google-sheets.md), [ADR 0005](adr/0005-instrumente-und-kursdaten.md), [ADR 0007](adr/0007-bewertung-und-einstand.md), [ADR 0011](adr/0011-watchlist.md).
