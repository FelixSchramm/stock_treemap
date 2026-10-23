# Geprüfte Datenquelle und Importmapping

Stand: 23. Oktober 2026. Lesender Connector-Zugriff war erfolgreich. Dieses Dokument enthält Struktur und Befunde, keinen vollständigen Depotexport. Werte wurden nicht verändert.

## Quelle und geprüfte Bereiche

[Verifiziertes Google Sheet](https://docs.google.com/spreadsheets/d/1aI5XmeKpOsgdFskItToPVNR-X8AWy9XNYh1Ggt49ZJw/edit)

| Eigenschaft | Beobachtung |
| --- | --- |
| Hauptblatt | `Untitled`, sheetId `1205104475`, 1001 Zeilen × 30 Spalten |
| Weiteres Blatt | `Verkäufe`, sheetId `2001`, 500 Zeilen × 13 Spalten |
| Locale | `de_DE` |
| Sheet-Zeitzone | `America/Los_Angeles`; nicht als Zeitzone aller Wertpapiere verwenden |
| Hauptblatt gelesen | `A1:AD10`, `A11:M45` |
| Formeln geprüft | `G2:M4`, `G18:K36` |
| Verkäufe gelesen | `A1:M4`; Überschriften und eine Summenzelle, keine Transaktionszeilen in diesem geprüften Bereich |

Die aktuelle Positionstabelle steht in `A1:M33`: eine Kopfzeile und 32 Positionszeilen, davon 30 mit positivem Bestand. Die aktiven Zeilen umfassen 24 Aktien, 2 ETFs und 4 Derivate. Nullbestände stehen aktuell in Zeile 22 und 28. Ab Zeile 35 folgen Zusammenfassungs-/Vergleichszeilen. Rechts der Positionstabelle stehen Kategorieaggregate, keine zusätzlichen Positionen.

Dies sind geprüfte Grenzen des aktuellen Inhalts, keine dauerhaft fest kodierten Importgrenzen. Der Import liest begrenzte Bereiche anhand der konfigurierten Tabelle, validiert Zeilentypen und erkennt Schemaänderungen. Ungeprüfte restliche Zeilen werden nicht als nachweislich leer beschrieben.

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

Die geprüften J-Formeln erzeugen Prozentpunkte (`× 100`), keine Bruchteile: 20 bedeutet 20 %, nicht 2000 %. Eingelesene Referenzwerte dürfen nicht nochmals mit 100 multipliziert werden. Die Anwendung berechnet ihre Kennzahlen aus Eingangsgrößen.

Das Modell ergänzt eine geprüfte `instrument_id`, Provider-Zuordnung, Währungseinheit, Quellenzeile, Importzeitpunkt und je Bewertung Quelle/Kurszeitpunkt/Status. Geld und Mengen bleiben numerisch. Leere Felder und „k. A.“ bedeuten fehlende Daten.

## Entscheidungsrelevante Befunde

1. **Listing-Konflikte:** Alibaba hat `BABA` als Anzeige-Ticker, aber `HKG:9988` als Handelssymbol. US-ADR und Hongkong-Aktie sind nicht austauschbar. Deutsche Telekom führt `DTE` und `ETR:DTE`; ein US-Ticker gleichen Namens ist kein Ersatz. Maßgeblich ist die geprüfte Identität mit Listing.
2. **Fehlende ETF-Identität:** Aktive ETF-Zeilen 18 und 21 haben nur `ETF` als Ticker, kein Google-Finance-Symbol und keine explizite Handelswährung. Der Name allein identifiziert keine eindeutige Anteilsklasse. ISIN/WKN und Listing fehlen.
3. **Derivate:** Zeilen 30–33 enthalten keine Produktkennung im geprüften Schema. Ticker wie `SAP` oder `CRWD` nennen dort den Basiswert. Benötigt werden Kennungen und Kurse der konkreten Produkte.
4. **Feste Kurswerte:** G18, G21 und G29:G33 enthalten feste Zahlen statt Kursformeln. Ob sie manuell oder durch einen anderen Prozess aktualisiert wurden, ist nicht belegt. Ein Sheet-Lesezeitpunkt beweist nicht ihren Marktzeitpunkt; sie bleiben Ersatzwerte mit unbekanntem Kursstand.
5. **Unbekannter Einstand:** I29 enthält „k. A.“, I32 ist leer. Für diese aktiven Positionen ist ohne Ergänzung kein Seit-Kauf-Prozentwert berechenbar.
6. **Nullbestand mit Einstand:** Zeile 22 enthält trotz Nullbestand einen Einstand. Dieser gehört nicht zur Kostenbasis aktiver Positionen.
7. **Handelsstatus:** Der Name in Zeile 29 enthält „derzeit nicht handelbar“. Das ist eine Aussage des Sheets, keine unabhängig geprüfte aktuelle Handelsstatusbestätigung.
8. **Zusammenfassung:** Geprüfte Summenformeln verwenden unterschiedliche Bedingungen für Werte und Einstand. Eine persönliche Gesamtrendite der App benötigt dieselbe gültige Positionsmenge für Zähler und Nenner.
9. **Datenstand:** Dateititel, feste Vergleichszeile, Sheet-Abruf und tatsächliche Kurse können verschiedene Zeitpunkte haben. Keiner ersetzt einen instrumentbezogenen Kurszeitstempel.

## Noch zu verifizieren

- Eindeutige ETF-/Derivatekennungen und tatsächliche Anbieterabdeckung.
- Ob „Investiert (EUR)“ nach Teilverkäufen den verbleibenden Einstand einschließlich der im Sheet berücksichtigten Gebühren enthält.
- Fehlende Einstandswerte und belastbare Zeitpunkte fester Bewertungen.
- Nach Auswahl des Hosts: lesender Zugriff mit dem eigenen Laufzeit-Service-Account.

Die Befunde sind in [ADR 0004](adr/0004-google-sheets.md), [0005](adr/0005-instrumente-und-kursdaten.md) und [0007](adr/0007-bewertung-und-einstand.md) berücksichtigt. Sie begründen keine automatische Änderung des Original-Sheets.
