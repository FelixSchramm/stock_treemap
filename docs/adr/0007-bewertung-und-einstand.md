# ADR 0007: EUR-Bewertung und Gewinn/Verlust seit Kauf

- **Datum:** 2026-10-23
- **Status:** Angenommen; Kostenbasis nach Teilverkäufen zu verifizieren
- **Herkunft:** Seit-Kauf-Prozentwert vom Nutzer; EUR aus dem Sheet abgeleitet.

## Kontext

Gewünscht ist „Gewinn/Verlust seit Kauf in Prozent“. Das Sheet liefert EUR-Einstand/EUR-Werte bei unterschiedlichen Handelswährungen. Nicht alle Einstandswerte sind bekannt.

## Entscheidung

```text
V_i = Stückzahl_i × Instrumentkurs_i × FX_i_nach_EUR
B_i = verbleibender Einstand der gehaltenen Stücke in EUR
G/V_i_EUR = V_i − B_i
G/V_i_Prozent = (V_i / B_i − 1) × 100
```

EUR-Sheet-Ersatzkurse werden nicht erneut umgerechnet. Fremdwährungskurse erfordern richtige FX-Richtung und Einheit, gegebenenfalls Pence statt Pfund. FX-Zeitpunkte gehören zum Bewertungsstatus.

Die persönliche Ansicht heißt „Seit Kauf“ und blendet die Periodenauswahl aus. Sie betrachtet unrealisierte Gewinne/Verluste aktueller Bestände. Dividenden, realisierte Verkäufe und Steuern sind nicht enthalten; Gebühren nur, soweit in der dokumentierten Sheet-Kostenbasis berücksichtigt.

Fehlender, ungültiger oder nicht positiver Einstand ergibt keinen Prozentwert. Nullbestände werden aus aktiver Bewertung und Kostenbasis ausgeschlossen. Positionen ohne bewertbaren Kurs bleiben in der Tabelle, fehlen aber in berechenbaren Summen. Manuelle/veraltete Bewertungen übertragen ihren Status auf die Kennzahl.

Ein ausdrücklich belegter Kurs von null ist von einem fehlenden Kurs zu unterscheiden. Bei zusammengeführten identischen Instrumenten ist die persönliche Kachelrendite nur berechenbar, wenn der Einstand aller zugehörigen aktiven Teilpositionen bekannt ist; anderenfalls bleibt sie „n. v.“.

Das Gesamtprozent lautet `(sum(V_i) / sum(B_i) − 1) × 100` über genau dieselbe gültige Teilmenge. Die Abdeckung wird genannt; Einzelprozente werden nicht einfach gemittelt. Der gesamte bewertete Depotwert kann eine andere Abdeckung haben und wird getrennt beschriftet.

## Alternativen

- Mittelwert der Einzelprozente: ignoriert verschiedene Kostenbasen.
- EUR-Einstand mit USD-Kurs vergleichen: vermischt Währungen.
- TWR/IRR aus Transaktionen: anderer Funktionsumfang.

## Konsequenzen und Prüfung

„Investiert (EUR)“ muss den Einstand der verbleibenden Stücke enthalten, insbesondere nach Teilverkäufen. Fehlende Einstände werden nicht aus aktuellen Kursen erfunden. Synthetischer Test: Wert/Einstand 120/100 und 330/300 ergibt 20 % und 10 %, zusammen 12,5 %. Eine dritte Position ohne Einstand wird aus dieser Gesamtkennzahl ausgeschlossen und als fehlend ausgewiesen.

## Beleg

[Datenquelle: Spalten G–K und geprüfte Formeln](../DATA_SOURCE.md). Die Anwendung berechnet Werte mit expliziter Abdeckung neu.
