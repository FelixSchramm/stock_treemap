# Mobile Bedienung und Barrierefreiheit (M5-05)

Dieses Dokument beschreibt die geprüften Bildschirmgrößen, Browser, Bedienabläufe ohne Hover, Kontrast- und Tastaturanforderungen sowie verbleibende technische Einschränkungen gemäß [ADR 0008](adr/0008-treemap-und-bedienung.md) und Issue #29.

## 1. Geprüfte Bildschirmgrößen und Browser

| Gerätekategorie / Viewport | Auflösung (CSS-Pixel) | Geprüfte Browser / Engines | Geprüfte Abläufe |
| --- | --- | --- | --- |
| **iPhone SE / Mini (Hochformat)** | `375 × 667 px` | iOS Safari (WebKit), Chrome Mobile Emulation | Zeitraumwahl, Suche/Filter, Aktualisieren, Positionsdetails ohne Hover |
| **iPhone 15 / 16 (Hoch- & Querformat)** | `390 × 844 px` / `393 × 852 px` | iOS Safari (WebKit), Chrome Mobile Emulation | Treemap-Legende mit Umbruch, Kennzahlen-Umbruch, Sortierung, kleine Positionen (`< 0,1 %`) |
| **iPhone Plus / Pro Max** | `430 × 932 px` | iOS Safari (WebKit), Chrome Mobile Emulation | Vollständiger Bedienablauf inkl. Datenstatus-Expander und Positionsübersicht |
| **Tablet / schmales Fenster** | `768 × 1024 px` | macOS Safari, Google Chrome | Spaltenumbruch der Filterleiste und Kennzahlen |
| **Mac Desktop** | `1280 × 800 px`, `1440 × 900 px` | macOS Safari, Google Chrome | Mehrspaltige Darstellung, Tastaturnavigation (`Tab`, `Shift+Tab`, `Enter`, `Space`, Pfeiltasten) |

Zusätzlich sichern deterministische Offline-Tests in [`tests/test_mobile_accessibility.py`](../tests/test_mobile_accessibility.py) den kompletten Bedienablauf ohne Hover, die Vollständigkeit aller Tooltip-Informationen außerhalb der Treemap sowie die WCAG-AA-Kontraste automatisiert ab. Die Zeitraumauswahl mit fünf Optionen (Heute bis Seit Kauf, seit PR #98) ist bisher nur per Streamlit-AppTest geprüft; die Prüfung im Browser auf dem iPhone steht noch aus.

## 2. Touch-Ziele, Umbruch und schmale Ansicht

- **Touch-Ziele (`>= 44 px`):** Alle interaktiven Bedienelemente (`Aktualisieren`-Button, Radio-Optionen für `Zeitraum`, `Suche`, `Instrumenttyp`, `Kategorie`, `Sortierung`, `Reihenfolge` sowie die ausklappbaren Bereiche `Positionsdetails ohne Hover` und `Datenstatus & Hinweise`) erhalten über `MOBILE_ACCESSIBILITY_CSS` in [`ui.py`](../src/portfolio_dashboard/ui.py) eine Mindesthöhe von `2.75rem` (`44 px` gemäß Apple iOS Human Interface Guidelines).
- **Umbruch (`<= 640 px`):** Radio-Gruppen (`div[role="radiogroup"]`), die Farblegende und mehrspaltige Blöcke (`stHorizontalBlock`) umbrechen auf schmalen Bildschirmen automatisch (`flex-wrap: wrap`), sodass kein Steuerknopf abgeschnitten wird.

## 3. Bedienung ohne Hover und Erreichbarkeit kleiner Positionen

Auf Touch-Geräten (iPhone) und bei reiner Tastaturnavigation steht kein Maus-Hover über Plotly-Kacheln zur Verfügung. Zudem können sehr kleine Positionen (z. B. `< 0,1 %` Depotanteil) in einer flächenproportionalen Treemap naturgemäß nur eine sehr kleine Kachel erhalten, und unbewertbare Positionen (`Wert (EUR)` unbekannt) besitzen keine Kachel.

Daher sind **alle wesentlichen Tooltip-Informationen vollständig ohne Hover** auf zwei komplementären Wegen zugänglich:

1. **Sortier- und filterbare Positionsübersicht (`render_positions_table`):**
   - Zeigt alle aktiven Positionen (inklusive Kleinstpositionen und unbewertbarer Positionen) mit `Instrument`, `Ticker`, `Typ`, `Kategorie`, `Stückzahl`, `Währung`, `Wert (EUR)`, `Anteil am bewerteten Gesamtdepot (%)`, `Entwicklung (%)`, `Datenstand`, `Referenzdatum` und `Status`.
   - Durch Sortierung nach `Wert (EUR)` + `Aufsteigend` oder über die Freitextsuche lassen sich selbst kleinste Positionen sofort an die erste Stelle holen.
2. **Umbrechenbare Detailkarten (`Positionsdetails ohne Hover`, `render_position_details`):**
   - Unterhalb der Tabelle stellt ein per Touch und Tastatur (`Tab` + `Enter`/`Space`) bedienbarer Bereich für jede gefilterte Position sämtliche Zeilen des Treemap-Tooltips sowie Kategorie, explizite Richtungsangabe (`Gewinn (+)`, `Verlust (−)`, `Unverändert (0,00 %)`, `Keine Kennzahl (n. v.)`) und Datenstatus als vertikal umbrechenden Text dar – ganz ohne horizontales Tabellen-Scrollen.

## 4. Kontraste, Vorzeichen und Tastaturbedienung

- **WCAG-AA-Farbkontrast (`>= 4,5 : 1`):**
  - Weiße Kachel- und Legendenschrift (`#ffffff`) erreicht auf allen Hintergrundfarben der symmetrischen Treemap-Skala – von maximalem Verlust (`#b3261e`, Kontrast `6,68 : 1`) über Neutral (`#5f6368`, Kontrast `6,10 : 1`) bis zu maximalem Gewinn (`#188038`, Kontrast `5,10 : 1`) sowie dem grauen `n. v.`-Status (`#3c4043`, Kontrast `10,40 : 1`) – durchgängig ein Kontrastverhältnis von mindestens `4,5 : 1` (WCAG 2.1 Level AA).
- **Information nie allein über Farbe:**
  - Jede Prozentangabe trägt ein explizites Vorzeichen (`+` bei positiven Werten, `-` bei negativen Werten, `0,00 %` bei unveränderten Werten, `n. v.` bei fehlender Kennzahl).
  - Die Farblegende und die `Positionsdetails ohne Hover` benennen zusätzlich die Richtung als Text (`Verlust`, `Gewinn`, `keine Kennzahl`).
- **Tastaturfokus:**
  - Alle nativen Steuerelemente und Expander zeigen bei `:focus-visible` einen kontrastreichen Fokusring (`2px solid #8ab4f8`, `outline-offset: 2px`).

## 5. Verbleibende technische Einschränkungen

1. **Einzelkacheln im Plotly-SVG nicht einzeln per `Tab` fokussierbar:**
   - Plotly rendert Treemap-Kacheln als SVG-Pfade ohne eigene HTML-`tabindex`-Knoten. Tastaturnutzer navigieren daher über die nativen Filter-/Sortierfelder, die Positionsübersicht und den Bereich `Positionsdetails ohne Hover`, die identische Daten aus demselben Bewertungs-Snapshot bereitstellen.
2. **Textausblendung in sehr kleinen Treemap-Kacheln:**
   - Bei Positionen mit sehr geringem EUR-Anteil reicht die Kachelfläche in der Grafik nicht für mehrzeiligen Text aus. Diese Positionen bleiben über Suche, aufsteigende Wertsortierung, Tabelle und `Positionsdetails ohne Hover` uneingeschränkt erreichbar.
3. **Horizontales Scrollen der 12-spaltigen Tabelle auf schmalen Bildschirmen (`<= 390 px`):**
   - Das vollständige Tabellenraster scrollt auf dem iPhone im Hochformat innerhalb seines Containers horizontal. Für eine scrollfreie Einspalten-Lesbarkeit auf schmalen Displays dient der direkt darunterliegende Bereich `Positionsdetails ohne Hover`.
