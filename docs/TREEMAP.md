# Treemap-Wertmodell, Kennzahlen, Filter und Tabelle (M5-02 / M5-03)

Umsetzung von [ADR 0008](adr/0008-treemap-und-bedienung.md). Das Modell steht in [`treemap.py`](../src/portfolio_dashboard/treemap.py) und ist ohne Streamlit/Plotly testbar; das Zeichnen und Rendern übernimmt [`ui.py`](../src/portfolio_dashboard/ui.py).

- **Fläche:** EUR-Positionswert je verifizierter Instrumentgruppe (`PortfolioValuation.groups`). Die Fläche hängt nur von der Bewertung ab, nie von Ansicht oder Periode. Verschiedene Produkte (z. B. Derivate mit gleichem Basiswert) werden nicht zusammengeführt.
- **Keine Kachel:** Gruppen ohne positiven EUR-Wert (fehlender/ungültiger Kurs, fehlender FX, Bestand 0). Die Anzahl wird unter der Karte genannt.
- **Entwicklung:** `Performance(percent, reference_date)` je Gruppe, aus Kursperiode (`performance_from_period`) oder Seit-Kauf-Rendite (`personal_performances`). Fehlt der Prozentwert, bleibt die Fläche, die Kachel ist grau und zeigt „n. v.“.
- **Farbe:** rot – neutral – grün, symmetrisch um 0 %. Die Spannweite ist der größte Betrag der sichtbaren Werte (mindestens 1 %, höchstens 50 %). Größere Werte werden nur farblich begrenzt; Label und Tooltip zeigen die ungekürzte Zahl. Die Legende nennt die Spannweite und den grauen n.-v.-Zustand.
- **Label/Tooltip:** Name, Ticker und Prozent (mit „%“, n. v. ohne Einheit) in der Kachel; Tooltip mit Typ, Währung, Stückzahl, EUR-Wert, Anteil am bewerteten Depot, Datenstand (älteste belegte Kursbeobachtung; ohne belegten Zeitpunkt, z. B. bei Sheet-Werten, „Kursstand unbekannt“ – die Abrufzeit ist nie der Datenstand) und Referenzdatum.
- **Depotkennzahlen (`build_kpis`, `render_kpis`):**
  - Zeigen stets die Kennzahlen des ungefilterten Gesamtdepots auf Gruppenebene (`PortfolioValuation.groups`): bewerteter Depotwert in EUR, aktive Positionen inkl. Abdeckung (mit Hinweis auf Quellzeilen und teilweise bewertete Gruppen) sowie größter Gewinner (`> 0 %`) und größter Verlierer (`< 0 %`) mit expliziter Bezugsmenge.
  - In der Ansicht „Meine Entwicklung“ ergänzt `build_kpis` den Einstandsvergleich (`G/V seit Kauf (EUR)`, `Rendite seit Kauf (%)`, `Bewerteter Einstand`) über alle aktiven Positionen mit vollständigem verifiziertem Einstand.
- **Gemeinsame Filter (`FilterCriteria`, `filter_groups`, `render_filter_controls`):**
  - Freitextsuche (Name, Ticker, Instrument-ID, Symbol, ISIN-Hinweis, Kategorie), Instrumenttyp (`Alle`, `Aktie`, `ETF`, `Derivat`) und Kategorie (`Alle`, vorhandene Kategorien sowie `Ohne Kategorie` bei gemischter Kategorisierung).
  - Filter wirken gemeinsam auf die Treemap und die Positionsübersicht; die Kopfkennzahlen bleiben ungefilterte Depotkennzahlen.
- **Positionsübersicht (`build_table_rows`, `sort_table_rows`, `render_positions_table`):**
  - Enthält alle aktiven Instrumentgruppen einschließlich unbewertbarer Positionen (`Wert (EUR)` = leer / `n. v.`) mit deutschem Status (`Kurs fehlt`, `FX-Kurs fehlt`, `Instrument nicht zugeordnet`, `Sheet-Wert, Kursstand unbekannt`, `Einstand fehlt`, `Historie fehlt`, `Nur teilweise bewertet`, `OK`). Inaktive Nullbestände bleiben ausgeschlossen.
  - Die Spalte `Anteil am bewerteten Gesamtdepot (%)` bezieht sich auch bei aktiven Filtern auf `valuation.total_eur` und ist explizit so beschriftet.
  - Sortierbar nach `Wert (EUR)`, `Anteil am bewerteten Gesamtdepot (%)`, `Entwicklung (%)`, `Name` und `Typ`; fehlende Werte (`n. v.`) stehen unabhängig von der Sortierrichtung immer am Ende (Tie-Breaker: Name A–Z).
- **Demo:** feste, erfundene EUR-Preise und Kursprozente; die Seit-Kauf-Ansicht bleibt ohne verifizierten Einstand „n. v.“.

Nicht enthalten: Aktualisieren und Fehlerzustände (#28), Mobil/Zugänglichkeit (#29).
