# ADR 0005: eindeutige Instrumente und gekennzeichnete Kursqualität

- **Datum:** 2026-10-23
- **Status:** Angenommen; Anbieterabdeckung einzelner Produkte offen
- **Herkunft:** Technische Entscheidung aus der Datenprüfung.

## Kontext

Das Sheet enthält Aktien, ETFs und Derivate. Manche Ticker sind Kurzbezeichnungen oder nennen nur ein Underlying. Einige Kurse sind manuell und ohne Kurszeitstempel eingetragen. Eine reine Tickerliste könnte falsche Instrumente laden.

## Entscheidung

Jedes Instrument erhält eine geprüfte Identität und explizite Provider-Zuordnung. Instrumenttyp, Handelsplatz, Währung und gegebenenfalls ISIN/WKN gehören zur Prüfung. Positionen werden nur bei bestätigter identischer Instrumentidentität zusammengeführt; gleiche Anzeige-Ticker reichen nicht.

yfinance ist die erste austauschbare Quelle für verfügbare Kurse, Historien und FX. Es gibt keine Echtzeitgarantie oder zugesagte vollständige Derivateabdeckung. Abrufe werden vor Veröffentlichung aus der Hosting-Umgebung geprüft.

Bewertungspriorität: geprüfter Anbieterpreis, zuletzt erfolgreich geladener Anbieterpreis mit Alter, danach ausdrücklich gekennzeichneter EUR-Sheet-Wert. Ohne belegten Zeitpunkt heißt Letzterer „Sheet-Wert, Kursstand unbekannt“. Quellenwechsel bleiben sichtbar. Historische Renditen verwenden konsistente Reihen desselben Instruments und derselben Adjustierung.

Ein manueller Wert kann die Fläche bestimmen, erzeugt aber keine erfundene Periodenrendite. Derivate werden niemals mit Kursen/Renditen ihres Underlyings befüllt. Unbekannter Positionswert bedeutet Tabellenzeile ohne Kachel; unbekannte Rendite bei bekanntem Wert bedeutet graue Kachel.

## Aktualisierung

Ziel bei geöffneter Sitzung: aktuelle Kurse/FX alle 60 Sekunden, Bestände alle 5 Minuten, abgeschlossene Historien alle 6 Stunden. Der aktuelle Handelstag wird bei Kursaktualisierung ergänzt. Intervalle sind konfigurierbar; gebündelte Abrufe und Backoff beachten Providergrenzen. Manuelles Aktualisieren erneuert Bestände und aktuelle Kurse unter denselben Grenzen.

Cacheeinträge tragen Quelle, Kurszeitpunkt und Abrufzeitpunkt. Ausfälle lassen den letzten Wert mit sichtbarem Alter bestehen. Es gibt zunächst keinen Hintergrund-Scheduler. Yahoo nennt etwa Nasdaq als Echtzeitquelle und Xetra/Frankfurt mit 15 Minuten Verzögerung; dies ist kein SLA für yfinance oder das Dashboard.

## Alternativen und Konsequenzen

Nur Sheet-Kurse liefern nicht alle historischen Perioden. Automatische Symbolsuche ohne Prüfung kann andere Listings treffen. Ein kostenpflichtiger Anbieter wird bei nachgewiesener Abdeckungslücke bewertet. Fehlende Kennungen blockieren die jeweilige automatische Versorgung, nicht die gekennzeichnete Anzeige vorhandener Bewertungen.

## Referenzen

- [Datenlücken und Symbolkonflikte](../DATA_SOURCE.md)
- [yfinance](https://ranaroussi.github.io/yfinance/)
- [Yahoo: Datenanbieter und Verzögerungen](https://help.yahoo.com/kb/finance/article-exchanges-data-delays-sln2310.html)
