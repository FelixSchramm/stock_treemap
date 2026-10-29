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

Referenzen: [ADR 0004](adr/0004-google-sheets.md), [ADR 0005](adr/0005-instrumente-und-kursdaten.md), [ADR 0007](adr/0007-bewertung-und-einstand.md).
