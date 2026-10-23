# ADR 0006: reine Kursentwicklung mit definierten Perioden

- **Datum:** 2026-10-23
- **Status:** Angenommen
- **Herkunft:** Zeiträume und reine Kursentwicklung vom Nutzer; Referenzregeln technisch.

## Kontext

Perioden benötigen eindeutige Vergleichspunkte. Börsen haben verschiedene Kalender und Zeitzonen. Splits dürfen keine künstlichen Renditesprünge erzeugen; Dividenden sind keine reine Kursentwicklung.

## Entscheidung

Die Farbe zeigt `(P_aktuell / P_referenz − 1) × 100` in Handelswährung. Verwendet wird die reguläre Sitzung des Instruments; Vor-/Nachbörse bleiben zunächst außen vor.

| Auswahl | Referenz |
| --- | --- |
| Heute, regulärer Handel läuft | Letzter verfügbarer regulärer Kurs gegen Schlusskurs der vorherigen Sitzung |
| Heute, geschlossen/vor Öffnung | Letzte abgeschlossene Sitzung gegen deren vorherigen Schlusskurs; Sitzungsdatum sichtbar |
| 1 Woche | Sieben Kalendertage vor dem lokalen Börsendatum des aktuellen Vergleichskurses |
| 1 Monat | Ein Kalendermonat davor; fehlender Tag wird auf Monatsende begrenzt |
| 1 Jahr | Ein Kalenderjahr davor; 29. Februar fällt ggf. auf 28. Februar |

Für Woche/Monat/Jahr gilt der letzte gültige Schlusskurs am oder vor dem Zieltag. Börsenkalender und erwartete Handelstage werden geprüft: unerwartete Lücken werden nicht wie Feiertage behandelt oder unbegrenzt rückwärts überbrückt. Bei unzureichender Historie erscheint „n. v.“ mit Grund. Kurs- und Referenzdatum bleiben sichtbar.

Aktuelle/historische Preise werden auf derselben splitbereinigten Basis verglichen, ohne Dividendenadjustierung. Provider-Defaults werden ausdrücklich geprüft; bereits bereinigte Daten dürfen nicht doppelt adjustiert werden. FX beeinflusst diese Ansicht nicht, aber EUR-Bewertung und persönliche Entwicklung nach [0007](0007-bewertung-und-einstand.md).

## Alternativen

- Fünf Handelstage als Woche oder YTD als Jahr: andere Semantik.
- Dividendenbereinigte Total-Return-Reihe: nicht die bestellte Kursentwicklung.
- Kürzere Historie als volle Jahresrendite: irreführender Zeitraum.

## Konsequenzen und Prüfung

Tests decken Wochenenden/Feiertage, Zeitzonen, Monatsende/Schaltjahr, Splits, fehlende Handelstage und neu gelistete Instrumente ab. Die heutige Stückzahl beeinflusst die Kursrendite nicht. Eine echte historische Depotperformance wird daraus nicht konstruiert.
