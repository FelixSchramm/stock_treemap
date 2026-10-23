# ADR 0004: Google Sheets als lesende Bestandsquelle

- **Datum:** 2026-10-23
- **Status:** Angenommen; Chat-Lesezugriff geprüft, Laufzeitzugang offen
- **Herkunft:** Quelle vom Nutzer; Integrationsmodell technisch.

## Kontext

Das Sheet enthält Stückzahlen, Instrumenttypen, EUR-Einstand und EUR-Bewertung. Der Connector-Zugriff funktioniert. Ein Online-Python-Dienst kann nicht automatisch die Verbindung dieses Chats verwenden.

## Entscheidung

Das Sheet ist maßgeblich für Bestand, Kategorie und Einstand. Der Dienst liest es serverseitig über einen eigenen Service-Account mit Leserzugriff auf die benötigte Datei und `spreadsheets.readonly` als API-Scope. Dieser Scope begrenzt nicht auf ein einzelnes Tabellenblatt; die Datei-Freigabe begrenzt den tatsächlich möglichen Zugriff. Verwendet wird die bekannte Spreadsheet-ID.

Überschriften werden auf ein internes Modell abgebildet; das [Datenmapping](../DATA_SOURCE.md) beschreibt die geprüfte Struktur. Positionszeilen werden validiert. Summen, Vergleichswerte und Nullbestände zählen nicht als aktive Positionen. Zeilennummern dienen der Fehlerlokalisierung, nicht als dauerhafte Instrument-ID.

Negative Bestände werden als gesondert zu prüfende Positionen angezeigt; die erste Version macht daraus nicht durch einen Absolutwert eine Long-Position. Cash wird nur nach gesonderter fachlicher Festlegung aufgenommen.

Formeln und Formatierungen im Sheet bleiben unverändert. Die Anwendung berechnet ihre Kennzahlen aus validierten Eingaben neu; Ergebniszellen dienen als Plausibilisierung. Für Kurse gilt [0005](0005-instrumente-und-kursdaten.md). Ein späterer CSV-Import kann denselben Importvertrag bedienen.

## Alternativen

- Öffentlicher CSV-Link: unnötige öffentliche Verfügbarkeit der Bestände.
- Nutzer-OAuth mit Refresh-Token: möglich; für diesen Besitzer-Dienst weniger klar getrennt als ein dedizierter Lesezugang.
- Zweite Bestandsdatenbank: zusätzliche Synchronisierung ohne anfänglichen Nutzen.

## Konsequenzen

Service-Account und gezielte Lesefreigabe werden noch eingerichtet. Dieses ADR erzeugt keine Credentials. Reale Exporte und vollständige Rohdaten werden nicht als Testfixtures versioniert. Der Cache ersetzt nicht das Sheet.

## Referenzen

- [Geprüfte Struktur](../DATA_SOURCE.md)
- [gspread-Authentifizierung](https://docs.gspread.org/en/latest/oauth2.html)
- [Google-Sheets-Scopes](https://developers.google.com/workspace/sheets/api/scopes)
