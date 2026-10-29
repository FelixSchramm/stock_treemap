# ADR 0011: Watchlist aus eigenem Sheet-Blatt mit reiner Kursentwicklung

- **Datum:** 2026-10-28
- **Status:** Angenommen; Kursabruf umgesetzt (#84, [Watchlist-Kursabruf](../WATCHLIST_QUOTES.md)), Ansicht umgesetzt (#85, [Watchlist-Ansicht](../WATCHLIST_VIEW.md)), Heatmap folgt (#86)
- **Herkunft:** Nutzerentscheidungen vom 25.10.2026 (Epic #82, Issue #83); Parser- und Prüfregeln technisch.

## Kontext

Neben dem Depot soll eine Watchlist mit 200 und mehr Einträgen verfolgt werden. Das Besitzer-Sheet enthält dafür bereits ein eigenes Blatt mit den Spalten `Position`, `Letzter Kurs (EUR)`, `Veränd. (%)` und `Nettoveränd. (EUR)`. Die Besitzerentscheidung vom 25.10.2026 macht dieses Blatt zur maßgeblichen Watchlist; die SQLite-Watchlist aus #75 wird in #95 ersetzt, nicht in diesem ADR.

## Entscheidung

**Umfang.** Die Watchlist zeigt ausschließlich reine Kursentwicklung (`Heute`, `Woche`, `Monat`, `Jahr` nach [0006](0006-kursentwicklung.md)) in Handelswährung. Sie kennt keine Stückzahlen, keinen Einstand, keinen Zielkurs, keine EUR-Positionsbewertung und keine automatische Verknüpfung mit dem Depot.

**Zweistufiges Spaltenschema** (Details: [Datenquelle](../DATA_SOURCE.md#watchlist-blatt)):

1. *Grundspalten A–D*, kompatibel mit dem bestehenden Blatt: `Position` (Pflicht), `Letzter Kurs (EUR)`, `Veränd. (%)` (Prozentpunkte), `Nettoveränd. (EUR)` (alle drei in der Zelle optional). Eine Zeile nur mit diesen Spalten ist gültig. Ihre Werte sind ein ausdrücklich gekennzeichneter Ersatzwert „Sheet-Wert, Kursstand unbekannt“ für `Heute`; `Woche`, `Monat` und `Jahr` zeigen ohne Providerzuordnung „n. v.“.
2. *Optionale Zusatzspalten ab E*, erkannt über die Überschrift: `Ticker`, `Typ` (`Aktie`, `ETF`), `Yahoo-Symbol`, `Handelsplatz`, `Währung`, `Kategorie`. Eine vollständige und widerspruchsfreie Zuordnung (`Yahoo-Symbol` + `Handelsplatz` + `Währung`) schaltet pro Zeile den Abruf aller vier Perioden über yfinance frei (folgende Aufgabe #84).

**Handelsplätze** sind der exakte Yahoo-`exchangeName`: Deutschland `GER`, `FRA`, `STU`, `MUN`, `DUS` (Zeitzone `Europe/Berlin`) sowie USA `NMS`, `NGM`, `NCM`, `NYQ`, `ASE`, `PCX` (`America/New_York`). Andere Werte sind eine Zeilenwarnung, kein stiller Abruf.

**Bewusste Ausnahme zu [0005](0005-instrumente-und-kursdaten.md).** `Yahoo-Symbol`, `Handelsplatz` und `Währung` werden für die Watchlist direkt im Blatt gepflegt statt in `listings.toml`, weil an der Watchlist keine Geldbeträge oder Depotgewichte hängen. Die Angaben sind ein **Kandidat**, keine geprüfte Instrumentidentität: Der Parser vergibt keine `instrument_id` und kein `ProviderMapping`. Der strikte Laufzeitabgleich gegen die Yahoo-Metadaten (`symbol`, `exchangeName`, `currency`) bleibt verpflichtend und ist Teil von #84; ohne Treffer gilt der Sheet-Wert. Für das Depot gilt 0005 unverändert. Ob eine Watchlist-Zeile *allein* als geprüfte Identität für die Bewertung nach [0010](0010-innerer-wert-fcff.md) gilt, ist nicht entschieden (offene Besitzerfrage zu #95). Bis dahin (umgesetzt in #95, [PRIVATE_WATCHLIST.md](../PRIVATE_WATCHLIST.md)) erhält eine Zeile nur dann einen Bewertungsschlüssel, wenn ihr vollständiger Kandidat genau einem bereits geprüften Registryeintrag entspricht; die SQLite-Watchlist aus #75 ist entfernt.

**Prüfregeln des Parsers** (jeweils Zeilenwarnung mit Rückfall auf den Sheet-Wert; Warnungen nennen nie Zellwerte):

- ungültige Zahlen; negative Kurse; Veränderung unter −100 Prozentpunkten;
- Derivate: keine Zuordnung, nur der Sheet-Wert (ADR 0005: Underlying-Kurse sind kein Ersatz);
- unvollständige Zuordnung, ungültiges Symbolformat, unbekannter Handelsplatz, ungültige Währung (dreibuchstabig, Großbuchstaben; `GBp` bleibt unbekannt);
- widersprüchliche Zuordnung: ein deutscher Platz verlangt die Yahoo-Endung des Platzes (`GER` `.DE`, `FRA` `.F`, `STU` `.SG`, `MUN` `.MU`, `DUS` `.DU`); US-Plätze erwarten ein Symbol ohne Punkt und die Währung `USD`;
- doppeltes Yahoo-Symbol: die erste gültige Zeile behält die Zuordnung, spätere Zeilen fallen mit Hinweis auf die erste Zeile auf ihren Sheet-Wert zurück, damit kein Symbol doppelt abgerufen wird.

Die Endungs- und Währungsregeln sind eine Plausibilisierung vor dem Abruf; maßgeblich bleibt die Yahoo-Antwort.

**Konfiguration.** Optional `PORTFOLIO_WATCHLIST_RANGE` (`SourceConfig.watchlist_range`), ein ausdrücklich benanntes Blatt mit Zeilen- und Spaltengrenze: `Blatt!A<n>:<D–T><m>`. Das schmale Format `Watchlist!A1:D300` und das erweiterte `Watchlist!A1:J300` bis `T` sind beide zulässig; die Zeilenzahl ist nicht auf 200 begrenzt. Ohne Wert gibt es keine Sheet-Watchlist. Demo verwirft die Einstellung.

## Alternativen

- Zuordnung weiter in `listings.toml`: Pflege außerhalb des Blatts für 200+ Einträge unpraktisch, ohne Nutzen, da keine Geldwerte betroffen sind.
- Provider-Symbolsuche anhand des Namens: kann andere Listings treffen (ADR 0005).
- Nur das 4-Spalten-Format: zeigt außer `Heute` keine Perioden.

## Konsequenzen

Ein falsch eingetragenes Symbol kann ein anderes Instrument laden; Laufzeitprüfung und Parser-Plausibilität verringern das Risiko, ersetzen aber nicht die Pflege durch den Besitzer. Sheet-Werte bleiben als solche gekennzeichnet, solange kein Kurszeitpunkt belegt ist. Zeilennummern dienen der Fehlerlokalisierung, nicht als dauerhafte ID. Echte Blattinhalte gehören weder in Tests noch in Dokumentation.

## Referenzen

- [Watchlist-Blatt in der Datenquelle](../DATA_SOURCE.md#watchlist-blatt), [Parser](../SHEET_PARSER.md#watchlist-parser)
- [0004](0004-google-sheets.md), [0005](0005-instrumente-und-kursdaten.md), [0006](0006-kursentwicklung.md)
