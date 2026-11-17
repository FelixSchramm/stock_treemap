# Technische Kennzahlen je Position (#144)

Die Positionsübersicht zeigt neben der Kursentwicklung, **wo eine Aktie charttechnisch steht**: Trend, nächste Unterstützung mit Stärke, RSI (14) und Abstand zum 52-Wochen-Hoch. Die Kennzahlen sind eine Einordnung, **keine Anlageempfehlung**.

## Datenbasis

- Grundlage ist dieselbe Tageshistorie, die die App beim Aktualisieren für die Kursperioden lädt (`refresh._listing_period_results` → `QuoteCache.history`, Kalenderspanne etwa 400 Kalendertage). Es gibt **keine zusätzlichen Yahoo-Abfragen**.
- `PriceHistory.points` enthält je Handelstag nur den splitbereinigten Schlusskurs. Alle Methoden kommen deshalb mit Schlusskursen aus; Hoch, Tief und Volumen werden nicht verwendet.
- Die Reihe besteht aus den Schlusskursen der Kalendersitzungen vor dem aktuellen Kursdatum, gefolgt vom **aktuellen Kurs**. Das ist derselbe Kurs, den `price_period_returns` für „Heute“ verwendet: während der Sitzung der Live-Kurs, sonst der Schlusskurs der letzten abgeschlossenen Sitzung.
- Alle Preise stehen in der **Handelswährung** der Position, wie bei den Kursperioden.
- Positionen ohne verifizierte Kurszuordnung, ohne aktuellen Kurs oder ohne Historie erhalten „n. v.“ mit Grund. Es wird kein Ersatzwert erfunden.

## Fachmodul `technicals.py`

Reine Fachlogik nach dem Muster von `price_periods.py`: kein Zugriff auf Provider, Streamlit, Cache oder Systemzeit. Eingabe ist die Schlusskursreihe (älteste zuerst, letzter Wert = aktueller Kurs), Ausgabe das unveränderliche Objekt `TechnicalSignals`. Fehlende Werte sind `None` mit stabilem Grund:

| Grund | Bedeutung |
| --- | --- |
| `insufficient_history` | Zu wenige Handelstage für die Kennzahl |
| `no_support_below` | Kein Kandidat liegt unter dem Kurs (z. B. neues Allzeittief) |
| `history_unavailable` | Keine Kurshistorie |
| Grund der Kursperiode | z. B. `unverified_instrument`, `quote_unavailable`, `sheet_fallback`, `inactive` |

Alle Schwellenwerte sind benannte Konstanten im Modul.

## Unterstützungen

Es zählen nur Werte **unterhalb** des aktuellen Kurses.

| Methode | Regel | Mindestdaten |
| --- | --- | --- |
| Swing-Tiefs | Ein Schlusskurs liegt unter den 5 Handelstagen davor und nicht über den 5 Handelstagen danach (`SWING_WINDOW`). Tiefs, die höchstens 1,5 % auseinanderliegen (`ZONE_TOLERANCE`), bilden eine Zone; ihr Preis ist der Mittelwert. Eine Zone zählt ab 2 Berührungen (`MIN_SWING_TOUCHES`). | 60 Handelstage (`SWING_MIN_DAYS`) |
| Gleitende Durchschnitte | SMA20, SMA50, SMA200 der Schlusskurse | 20 / 50 / 200 Handelstage |
| Fibonacci-Retracements | Spanne zwischen 52-Wochen-Hoch und -Tief der Schlusskurse (letzte 252 Handelstage); Marken bei 23,6 / 38,2 / 50 / 61,8 / 78,6 % unter dem Hoch | 200 Handelstage (`LONG_MIN_DAYS`) |
| 52-Wochen-Tief | Niedrigster Schlusskurs der letzten 252 Handelstage (`YEAR_TRADING_DAYS`) | 200 Handelstage |

Die strenge Bedingung auf der linken Seite des Swing-Fensters verhindert, dass eine flache Strecke mehrfach als Tief zählt.

**Konfluenz:** Alle Kandidaten unter dem Kurs werden aufsteigend sortiert und mit 1,5 % Toleranz zum niedrigsten Wert der Zone gebündelt. Der Zonenpreis ist der Mittelwert der Kandidaten. Die **Stärke** ist die Anzahl verschiedener Methoden in der Zone (1 bis 4); SMA20, SMA50 und SMA200 zählen zusammen als eine Methode. Die **nächste Unterstützung** ist die höchste Zone mit Stärke von mindestens 2 (`MIN_CONFLUENCE_STRENGTH`). Gibt es keine, gilt die nächstgelegene Zone mit Stärke 1. Der **Abstand** ist `(Zonenpreis / Kurs − 1) × 100` und damit negativ.

## Trend

Feste Reihenfolge, die erste zutreffende Regel gilt:

| Trend | Regel |
| --- | --- |
| Aufwärtstrend | Kurs > SMA50 > SMA200 und SMA200 heute höher als vor 20 Handelstagen (`TREND_SLOPE_LOOKBACK`) |
| Bodenbildung | Kurs < SMA200, kein neues 52-Wochen-Tief in den letzten 20 Handelstagen (`BOTTOM_NO_NEW_LOW_DAYS`), Kurs höchstens 10 % über dem 52-Wochen-Tief (`BOTTOM_MAX_ABOVE_LOW`) und Kurs > SMA20 |
| Abwärtstrend | Kurs < SMA50 < SMA200 |
| Seitwärts | alle übrigen Fälle |
| n. v. | weniger als 200 Handelstage oder Historie nicht verfügbar |

Für die Steigung der SMA200 braucht es 220 Handelstage. Bei 200 bis 219 Handelstagen greift die Aufwärtstrend-Regel deshalb nicht; die übrigen Regeln gelten. Mit der geladenen Spanne von etwa 400 Kalendertagen liegen normalerweise rund 270 Handelstage vor.

## Weitere Kennzahlen

- **RSI (14)** nach Wilder: Der erste Durchschnitt von Gewinnen und Verlusten ist das einfache Mittel der ersten 14 Veränderungen, danach geglättet mit `(alt × 13 + neu) / 14`. Einordnung „überverkauft“ unter 30, „überkauft“ über 70, sonst „neutral“. Mindestens 15 Schlusskurse. Referenz im Test: die Beispielreihe von Wilder (wie bei StockCharts veröffentlicht) ergibt nach 15 Kursen 70,46 und nach 20 Kursen 57,92.
- **Abstand 52W-Hoch (%)** = `(Kurs / 52-Wochen-Hoch − 1) × 100`; ab 200 Handelstagen.
- **Kreuzungssignal:** „Golden Cross“ oder „Death Cross“, wenn die SMA50 die SMA200 in den letzten 20 Handelstagen (`CROSS_LOOKBACK`) gekreuzt hat; bei mehreren Kreuzungen zählt die jüngste. Sonst leer. Benötigt 220 Handelstage.

Bei weniger als 200 Handelstagen sind Trend, Fibonacci, SMA200 und 52-Wochen-Werte „n. v.“ mit `insufficient_history`. SMA20, SMA50 und RSI werden trotzdem berechnet, wenn ihre Mindestdaten reichen.

## Anzeige in der Positionsübersicht

Die Spalten stehen direkt hinter `Heute (%)`:

| Spalte | Anzeige | Sortierwert |
| --- | --- | --- |
| Trend | Aufwärtstrend / Seitwärts / Bodenbildung / Abwärtstrend / n. v. | Rang 4 bis 1 in dieser Reihenfolge |
| Nächste Unterstützung | Preis mit Währung, z. B. „147,80 USD“ | Preis |
| Abstand Unterstützung (%) | z. B. „-5,25 %“ | Prozentwert |
| Stärke | 1–4 | Zahl |
| RSI (14) | z. B. „28,40 (überverkauft)“ | RSI |
| Abstand 52W-Hoch (%) | z. B. „-12,30 %“ | Prozentwert |

- Die Werte bleiben numerisch; deutsche Formatierung, Währung und Trendname betreffen nur die Anzeige. So sortieren die Spaltenköpfe korrekt.
- Das Auswahlfeld `Sortierung` enthält alle neuen Spalten (absteigend). `n. v.` steht immer am Ende, Tie-Breaker ist der Name A–Z.
- `Positionsdetails ohne Hover` zeigt zusätzlich die angewendete Trendregel, alle Unterstützungszonen mit beteiligten Methoden, die nächste Unterstützung, RSI, Abstand zum 52-Wochen-Hoch und das Kreuzungssignal, jeweils mit Grund bei „n. v.“.
- Unter der Tabelle steht: „Technische Kennzahlen aus Tagesschlusskursen in Handelswährung. Keine Anlageempfehlung.“
- Zeitraumwahl und Kursentwicklungs-Schalter der Treemap verändern die neuen Spalten nicht.
- Die Werte liegen im `DashboardBundle` (`technical_signals`, in derselben Reihenfolge wie `valuation.groups`) und werden mit dem Bundle atomar ausgetauscht.

## Demo

Die Demo zeigt feste, erfundene Werte (`demo.demo_technical_signals`): Demo-Aktie A im Aufwärtstrend, Demo-Fonds B in Bodenbildung mit Death Cross. Das Demo-Depot hat nur zwei Positionen; ein „n. v.“-Beispiel mit Grund ist dort nicht enthalten. Die n. v.-Fälle sind in den Offline-Tests abgedeckt.

## Nicht im Umfang

Pivot-Punkte und Volumenprofil (brauchen Hoch, Tief und Volumen in `HistoryPoint`), Widerstände oberhalb des Kurses, technische Spalten in Watchlist und Indizes-Seite, Marken im Kurschart, Alarme sowie Kauf- oder Verkaufsempfehlungen.
