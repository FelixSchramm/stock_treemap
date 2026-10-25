# ADR 0008: Positionsflächen und zwei Farbansichten

- **Datum:** 2026-10-23
- **Status:** Angenommen
- **Herkunft:** Gewichtung/Umschalter vom Nutzer; weitere Darstellung technisch.

## Kontext

Die Treemap soll Positionsgröße und Entwicklung gleichzeitig zeigen. Kleine Positionen und Datenlücken müssen auch ohne große Kachel erreichbar bleiben.

## Entscheidung

Kachelflächen entsprechen dem bewerteten Positionswert in EUR und bleiben beim Wechsel der Ansicht oder Periode unverändert, bis sich die Bewertung aktualisiert. Ein Instrument entspricht einer Kachel; gleiche Underlying-Namen verbinden keine verschiedenen Produkte.

„Kursentwicklung“ startet bei Heute und bietet vier Perioden. „Meine Entwicklung“ färbt dieselben Kacheln nach Gewinn/Verlust seit Kauf. Zurückwechseln erhält die zuletzt gewählte Kursperiode; ein neuer Aufruf startet bei Heute.

Positive Werte sind grün, negative rot, unveränderte neutral, fehlende grau mit „n. v.“. Die sichtbare Farblegende ist symmetrisch um null. Ausreißer dürfen farblich, aber nicht numerisch begrenzt werden. Skalenregeln werden einheitlich aus den sichtbaren Daten berechnet; Ansicht und Spannweite bleiben beschriftet.

Name/Ticker und Prozentwert stehen in der Kachel. Tooltip und Tabelle ergänzen Typ, Währung, Stückzahl, EUR-Wert, Gewicht, Referenzdaten und Datenstatus. Die Tabelle ist such-/sortierbar und funktioniert ohne Hover. Filter betreffen Karte und Tabelle; Kopfkennzahlen bleiben Depotkennzahlen. Die Spalte „Depotanteil“ bezieht sich klar beschriftet auf den gesamten bewerteten Bestand, auch wenn die Flächenverteilung nur die gefilterte Auswahl darstellt.

Die Oberfläche ist deutsch, dunkel und für schmale Displays ausgelegt. Kontrast, Vorzeichen, Textlabels und Tastaturbedienbarkeit ergänzen Farben. Kategorien dienen als Filter; hierarchische Gruppierung bleibt eine Ausbauoption.

## Alternativen und Konsequenzen

Börsenwertgewichtung wurde durch die Nutzervorgabe ausgeschlossen. Gleiche Flächen würden Depotgewichte verbergen. Reine Hover-Bedienung wäre auf dem iPhone unpraktisch. Tabelle und mobile Prüfung gehören daher zur ersten Version. Konkretes Layout und Kontraste werden im Browser geprüft.

## Nachtrag 2026-10-25: Zeitraumauswahl statt Ansichts-Umschalter

Besitzerentscheidung vom 25.10.2026 (PR #98): Der Ansichts-Umschalter „Kursentwicklung“ / „Meine Entwicklung“ entfällt. „Seit Kauf“ ist die fünfte Option der Auswahl „Zeitraum“ (Heute, Woche, Monat, Jahr, Seit Kauf) und färbt dieselben Kacheln nach Gewinn/Verlust seit Kauf. Damit entfällt das gesonderte Merken der letzten Kursperiode beim Zurückwechseln; ein neuer Aufruf startet weiterhin bei Heute.
