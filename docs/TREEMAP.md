# Treemap-Wertmodell, Kennzahlen, Filter, Tabelle und Aktualisierung (M5-02 / M5-03 / M5-04)

Umsetzung von [ADR 0008](adr/0008-treemap-und-bedienung.md). Das Modell steht in [`treemap.py`](../src/portfolio_dashboard/treemap.py), die atomare Aktualisierungs-Koordination in [`refresh.py`](../src/portfolio_dashboard/refresh.py) und das Zeichnen/Rendern in [`ui.py`](../src/portfolio_dashboard/ui.py).

- **Fläche:** EUR-Positionswert je verifizierter Instrumentgruppe (`PortfolioValuation.groups`). Die Fläche hängt nur von der Bewertung ab, nie von Ansicht oder Periode. Verschiedene Produkte (z. B. Derivate mit gleichem Basiswert) werden nicht zusammengeführt.
- **Keine Kachel:** Gruppen ohne positiven EUR-Wert (fehlender/ungültiger Kurs, fehlender FX, Bestand 0). Die Anzahl wird unter der Karte genannt.
- **Entwicklung:** `Performance(percent, reference_date)` je Gruppe, aus Kursperiode (`performance_from_period`) oder Seit-Kauf-Rendite (`personal_performances`). Fehlt der Prozentwert, bleibt die Fläche, die Kachel ist grau und zeigt „n. v.“.
- **Farbe:** rot – neutral – grün, symmetrisch um 0 %. Die Spannweite ist der größte Betrag der sichtbaren Werte (mindestens 1 %, höchstens 50 %). Größere Werte werden nur farblich begrenzt; Label und Tooltip zeigen die ungekürzte Zahl. Die Legende nennt die Spannweite und den grauen n.-v.-Zustand.
- **Label/Tooltip:** Name, Ticker und Prozent (mit „%“, n. v. ohne Einheit) in der Kachel; Tooltip mit Typ, Währung, Stückzahl, EUR-Wert, Anteil am bewerteten Depot, Datenstand (älteste belegte Kursbeobachtung; ohne belegten Zeitpunkt, z. B. bei Sheet-Werten, „Kursstand unbekannt“ – die Abrufzeit ist nie der Datenstand) und Referenzdatum.
- **Depotkennzahlen (`build_kpis`, `render_kpis`):**
  - Zeigen stets die Kennzahlen des ungefilterten Gesamtdepots auf Gruppenebene (`PortfolioValuation.groups`): bewerteter Depotwert in EUR, aktive Positionen inkl. Abdeckung (mit Hinweis auf Quellzeilen und teilweise bewertete Gruppen) sowie größter Gewinner (`> 0 %`) und größter Verlierer (`< 0 %`) mit expliziter Bezugsmenge.
  - Teilabdeckung (`Teilabdeckung (X von Y Positionen)`) und Ersatz-/Cache-Werte (`enthält Sheet-Wert, Kursstand unbekannt`, `enthält gecachte Kurse`) werden direkt an der Gesamtsumme (`total_status_note`) ausgewiesen.
  - In der Ansicht „Meine Entwicklung“ ergänzt `build_kpis` den Einstandsvergleich (`G/V seit Kauf (EUR)`, `Rendite seit Kauf (%)`, `Bewerteter Einstand`) über alle aktiven Positionen mit vollständigem verifiziertem Einstand.
- **Gemeinsame Filter (`FilterCriteria`, `filter_groups`, `render_filter_controls`):**
  - Freitextsuche (Name, Ticker, Instrument-ID, Symbol, ISIN-Hinweis, Kategorie), Instrumenttyp (`Alle`, `Aktie`, `ETF`, `Derivat`) und Kategorie (`Alle`, vorhandene Kategorien sowie `Ohne Kategorie` bei gemischter Kategorisierung).
  - Filter wirken gemeinsam auf die Treemap und die Positionsübersicht; die Kopfkennzahlen bleiben ungefilterte Depotkennzahlen.
- **Positionsübersicht (`build_table_rows`, `sort_table_rows`, `render_positions_table`):**
  - Enthält alle aktiven Instrumentgruppen einschließlich unbewertbarer Positionen (`Wert (EUR)` = leer / `n. v.`) mit deutschem Status (`Kurs fehlt`, `FX-Kurs fehlt`, `Instrument nicht zugeordnet`, `Sheet-Wert, Kursstand unbekannt`, `Einstand fehlt`, `Einstand ungeprüft (Teilverkauf)`, `Kurs/Historie veraltet`, `Historie unvollständig`, `Historie fehlt`, `Nur teilweise bewertet`, `OK`). Inaktive Nullbestände bleiben ausgeschlossen.
  - Die Spalte `Anteil am bewerteten Gesamtdepot (%)` bezieht sich auch bei aktiven Filtern auf `valuation.total_eur` und ist explizit so beschriftet.
  - Sortierbar nach `Wert (EUR)`, `Anteil am bewerteten Gesamtdepot (%)`, `Entwicklung (%)`, `Name` und `Typ`; fehlende Werte (`n. v.`) stehen unabhängig von der Sortierrichtung immer am Ende (Tie-Breaker: Name A–Z).
- **Manuelle Aktualisierung, Lade- und Fehlerzustände (`PortfolioRefreshCoordinator`, `DashboardBundle`, `build_status_notes`):**
  - Verbindet den „Aktualisieren“-Button mit dem lesenden Sheet-Import (`SheetsAdapter.read`, `SHEETS_READONLY_SCOPE` via `GET`) und dem `QuoteCache`.
  - Manuelle Sheet-Werte ohne beobachteten Kurszeitpunkt werden einheitlich als `Sheet-Wert, Kursstand unbekannt` benannt.
  - Tauscht das `DashboardBundle` (Bestand, Bewertung, persönliche Renditen, Periodenrenditen) atomar aus. Schlägt eine Aktualisierung fehl, bleibt ein vorhandener gültiger Stand samt Alter (`format_snapshot_age`) unverändert erhalten; ohne vorherigen Stand wird `Daten nicht verfügbar` angezeigt. Gemischte halbfertige Snapshots sind ausgeschlossen.
- **Demo:** feste, erfundene EUR-Preise und Kursprozente; die Seit-Kauf-Ansicht bleibt ohne verifizierten Einstand „n. v.“.

Nicht enthalten: Mobil/Zugänglichkeit (#29).
