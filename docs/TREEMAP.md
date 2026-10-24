# Treemap-Wertmodell (M5-02)

Umsetzung von [ADR 0008](adr/0008-treemap-und-bedienung.md). Das Modell steht in [`treemap.py`](../src/portfolio_dashboard/treemap.py) und ist ohne Streamlit/Plotly testbar; das Zeichnen übernimmt `ui.treemap_figure`.

- **Fläche:** EUR-Positionswert je verifizierter Instrumentgruppe (`PortfolioValuation.groups`). Die Fläche hängt nur von der Bewertung ab, nie von Ansicht oder Periode. Verschiedene Produkte (z. B. Derivate mit gleichem Basiswert) werden nicht zusammengeführt.
- **Keine Kachel:** Gruppen ohne positiven EUR-Wert (fehlender/ungültiger Kurs, fehlender FX, Bestand 0). Die Anzahl wird unter der Karte genannt.
- **Entwicklung:** `Performance(percent, reference_date)` je Gruppe, aus Kursperiode (`performance_from_period`) oder Seit-Kauf-Rendite (`personal_performances`). Fehlt der Prozentwert, bleibt die Fläche, die Kachel ist grau und zeigt „n. v.“.
- **Farbe:** rot – neutral – grün, symmetrisch um 0 %. Die Spannweite ist der größte Betrag der sichtbaren Werte (mindestens 1 %, höchstens 50 %). Größere Werte werden nur farblich begrenzt; Label und Tooltip zeigen die ungekürzte Zahl. Die Legende nennt die Spannweite und den grauen n.-v.-Zustand.
- **Label/Tooltip:** Name, Ticker und Prozent (mit „%“, n. v. ohne Einheit) in der Kachel; Tooltip mit Typ, Währung, Stückzahl, EUR-Wert, Anteil am bewerteten Depot, Datenstand (älteste belegte Kursbeobachtung; ohne belegten Zeitpunkt, z. B. bei Sheet-Werten, „Kursstand unbekannt“ – die Abrufzeit ist nie der Datenstand) und Referenzdatum.
- **Demo:** feste, erfundene EUR-Preise und Kursprozente; die Seit-Kauf-Ansicht bleibt ohne verifizierten Einstand „n. v.“.

Nicht enthalten: Kennzahlen/Tabelle/Filter (#27), Aktualisieren und Fehlerzustände (#28), Mobil/Zugänglichkeit (#29).
