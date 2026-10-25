# Fachliche Regressionen, Abnahmewerte und numerische Toleranzen (M4-05)

Dieses Dokument beschreibt den integrierten fachlichen Abnahmepfad aus
[Issue #24](https://github.com/FelixSchramm/stock_treemap_open_ai/issues/24)
über Import (`sheet_parser.py`), Instrumentzuordnung (`instruments.py`),
Marktdaten-Cache und Ersatzwertwahl (`quote_cache.py`), EUR-Bewertung
(`valuations.py`), persönliche Rendite (`returns.py`), die vier Kursperioden
(`price_periods.py`) und das Treemap-Wertmodell (`treemap.py`). Alle Prüfungen
arbeiten mit ausschließlich synthetischen Sollfällen, injizierter Zeit und
ohne Provider- oder Netzwerkzugriff.

Grundlagen: [ADR 0005](adr/0005-instrumente-und-kursdaten.md),
[ADR 0006](adr/0006-kursentwicklung.md),
[ADR 0007](adr/0007-bewertung-und-einstand.md) und
[ADR 0008](adr/0008-treemap-und-bedienung.md).

## 1. Integrierter Datenfluss

1. **Import (`parse_sheet_rows`):** Liest Zeilen A–M (und optionale Hinweise
   in N–T) deterministisch nach expliziter `NumberLocale` und `imported_at`.
   Summen-/Vergleichszeilen ohne Typ, Leerzeilen und Nullbestände landen in
   `excluded_rows` und erreichen keine aktive Bewertung. Negative Bestände
   bleiben als `PositionActivity.NEGATIVE` im Snapshot sichtbar, gehen aber in
   keine Long-Summe ein. Spalte G liefert den EUR-Sheet-Ersatzkurs mit
   `ValuationSource.SHEET`; Spalte I liefert den EUR-Einstand (nur mit
   `cost_basis_verified_remaining=True` als `VERIFIED_REMAINING` freigegeben).
2. **Instrumentzuordnung (`resolve_listing`, `group_positions`):** Verbindet
   importierte Positionen ausschließlich über explizit geprüfte
   `VerifiedListing`-Einträge (`instrument_id`, `instrument_type`,
   `trading_venue`). Gleiche Anzeige-Ticker oder `source_symbol`-Hinweise
   (z. B. Underlying bei Derivaten oder verschiedene Anteilsklassen/ADRs)
   führen niemals zu einer Zusammenführung. Bestätigte identische Instrumente
   bilden eine gemeinsame `ValuationGroup` unter Erhalt aller Teilpositionen.
3. **Cache und Ersatzwertwahl (`QuoteCache`, `choose_valuation`,
   `supports_period_return`):** Vorrang hat der aktuelle Providerpreis
   (`PROVIDER`), danach der letzte erfolgreiche Providerpreis mit Alter
   (`CACHED_PROVIDER`, Warnung `cached_provider_value`), danach ein verfügbarer
   EUR-Sheet-Ersatzkurs (`SHEET`, Warnung `price_source_fallback` und ohne
   Kursdatum `sheet_value_quote_time_unknown`). Sheet-/manuelle Preise liefern
   `supports_period_return(valuation) is False` und erzeugen keine
   Kursperiodenrendite.
4. **EUR-Bewertung (`value_portfolio`):** Berechnet je aktiver Long-Position
   `Menge × Preis × scale_to_currency × FX_nach_EUR`. Ein EUR-Sheet-Ersatzkurs
   (`QuoteUnit("EUR", 1)`) wird nicht erneut mit FX umgerechnet, auch wenn das
   Instrument sonst in Fremdwährung notiert. `total_eur`, Gruppenwerte und
   `weight_fraction` verwenden exakt dieselbe Teilmenge aktiver Positionen mit
   `EurValuationStatus.AVAILABLE`.
5. **Persönliche Rendite (`portfolio_returns`):** Berechnet `gain_eur` und
   `return_percent` ausschließlich für aktive Positionen mit verfügbarer
   EUR-Bewertung und positivem `CostBasisStatus.VERIFIED_REMAINING`. Das
   Gesamtprozent lautet `(sum(V_i) / sum(B_i) − 1) × 100` über genau diese
   gültige Teilmenge (`eligible_market_value_eur`, `eligible_cost_basis_eur`).
   Eine Instrumentgruppe erhält nur dann ein `return_percent`, wenn **alle**
   aktiven Teilpositionen der Gruppe gültig bewertet sind und einen geprüften
   positiven Einstand besitzen.
6. **Kursperioden (`price_period_returns`):** Berechnet Heute, Woche, Monat und
   Jahr in Handelswährung auf Basis `YAHOO_SPLIT_ADJUSTED_PRICE` ohne FX- und
   ohne Dividendenadjustierung. Bereits splitbereinigte Schlusskurse werden
   nicht doppelt mit `split_ratio` multipliziert.
7. **Treemap-Wertmodell (`build_treemap`):** Erzeugt eine Kachel je
   `ValuationGroup` mit positivem EUR-Wert (`amount_eur > 0`). Gruppen ohne
   positiven EUR-Wert werden in `omitted_count` gezählt. Kachelflächen bleiben
   über alle fünf Ansichten (Heute, Woche, Monat, Jahr, Seit Kauf) identisch.

## 2. Synthetische Sollfälle und Abnahmewerte

In `tests/test_domain_regressions.py` wird ein gemeinsames synthetisches Depot
mit `imported_at = 2026-10-23 14:30 UTC` und `now = 2026-10-23 18:00 UTC`
(Handelsschluss in Frankfurt/London, laufende Sitzung in New York um 14:00 EDT)
geprüft. Wechselkurse: `USD -> EUR = 0.80`, `GBP -> EUR = 1.20`.

### 2.1 Positionen und Bewertung (Snapshot-Konsistenz)

| Zeile | Position / Fall | Typ | Menge | Kursquelle / Einheit | EUR-Wert (`V_i`) | Geprüfter Einstand (`B_i`) | Seit Kauf (`%`) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | Leerzeile | – | – | ausgeschlossen (`empty`) | – | – | – |
| 3 | `Summe Aktien` | – | – | ausgeschlossen (`summary`) | – | – | – |
| 4 | Nullbestand | Aktie | `0` | ausgeschlossen (`zero_quantity`) | – | – | – |
| 5 | EUR-Aktie Alpha (`SYNTH-EUR`) | Aktie | `10` | Provider `12,00 EUR` | `120,00` | `100,00` | `+20,00 %` |
| 6 | USD-Aktie Beta (`SYNTH-USD`, Split 2:1 am 20.10.2026) | Aktie | `8,25` | Provider `50,00 USD` × `0,80` | `330,00` | `300,00` | `+10,00 %` |
| 7 | Britischer ETF (`SYNTH-GBX`, in Pence `GBp`) | ETF | `50` | Cache `250 GBp` × `0,01` × `1,20` | `150,00` | `120,00` | `+25,00 %` |
| 8 | Teilposition 1 (`SYNTH-MULTI`, XETRA) | Aktie | `4` | Provider `25,00 EUR` | `100,00` | `80,00` | `+25,00 %` |
| 9 | Teilposition 2 (`SYNTH-MULTI`, Tradegate, gleicher Bestand nach Teilverkauf) | Aktie | `6` | Provider `25,00 EUR` | `150,00` | `120,00` | `+25,00 %` |
| 10 | Teilposition A (`SYNTH-PARTIAL-COST`, mit Einstand) | Aktie | `2` | Provider `40,00 EUR` | `80,00` | `50,00` | `+60,00 %` |
| 11 | Teilposition B (`SYNTH-PARTIAL-COST`, fehlender Einstand `k. A.`) | Aktie | `3` | Provider `40,00 EUR` | `120,00` | fehlt (`MISSING_COST`) | `n. v.` (`None`) |
| 12 | Derivat mit Sheet-Ersatzkurs (`SYNTH-DERIV`, Symbol nennt Underlying) | Derivat | `5` | Sheet-Fallback `9,00 EUR` (kein Kursdatum) | `45,00` | fehlt (`MISSING_COST`) | `n. v.` (`None`) |
| 13 | Unbewertbare Aktie ohne Kurs (`SYNTH-MISSING-PRICE`) | Aktie | `7` | Provider + Sheet fehlen (`MISSING_PRICE`) | `None` | `70,00` (ausgeschlossen) | `n. v.` (`None`) |
| 14 | Fremdwährungs-ETF ohne FX (`SYNTH-CHF`) | ETF | `2` | Provider `100,00 CHF`, kein FX (`MISSING_FX`) | `None` | `150,00` (ausgeschlossen) | `n. v.` (`None`) |
| 15 | Ausgebuchter Optionsschein mit belegtem Kurs `0` (`SYNTH-ZERO-PRICE`) | Derivat | `10` | Provider `0,00 EUR` | `0,00` | `40,00` | `-100,00 %` |
| 16 | Korrektur-/Negativbuchung (`SYNTH-NEG`) | Aktie | `-2` | `INACTIVE` (`PositionActivity.NEGATIVE`) | `None` | `20,00` (`INACTIVE`) | `n. v.` (`None`) |

### 2.2 Aggregierte Kennzahlen für denselben Snapshot

- **Akzeptierte Positionen im Snapshot:** 12 Zeilen (11 aktiv mit `quantity > 0`,
  1 negativ mit `quantity < 0`; 3 Zeilen beim Import als `empty`, `summary`,
  `zero_quantity` ausgeschlossen).
- **Bewertete aktive Positionen (`valued_count / active_count`):** `9 / 11`
  (`coverage_fraction = 9/11`). Zeilen 13 (`MISSING_PRICE`) und 14
  (`MISSING_FX`) bleiben in `valuation.positions` und `valuation.groups` für die
  Tabellenansicht erhalten, tragen aber keinen EUR-Wert bei.
- **Gesamter bewerteter Depotwert (`valuation.total_eur`):**
  `120 + 330 + 150 + (100 + 150) + (80 + 120) + 45 + 0 = 1.095,00 EUR`.
- **Summe der Einzelgewichte (`weight_fraction`):** Über alle 9 bewerteten
  aktiven Positionen exakt `1` (`1095 / 1095`); über alle 7 bewerteten
  Instrumentgruppen ebenfalls exakt `1`. Der belegte Nullkurs (Zeile 15) hat
  `amount_eur = 0` und `weight_fraction = 0`.
- **Treemap-Konsistenz:**
  - `valuation.groups` umfasst 10 Gruppen (Zeilen 8/9 bilden Gruppe
    `SYNTH-MULTI` mit `250,00 EUR`; Zeilen 10/11 bilden Gruppe
    `SYNTH-PARTIAL-COST` mit `200,00 EUR`; die übrigen 8 Zeilen bilden je eine
    eigene Gruppe, darunter Zeile 16 als unaufgelöste inaktive Gruppe ohne
    positiven EUR-Wert).
  - `build_treemap` erzeugt genau 6 Kacheln mit positivem EUR-Wert
    (`120`, `330`, `150`, `250`, `200`, `45` EUR; Summe `1.095,00 EUR` =
    `valuation.total_eur`).
  - `omitted_count = 4` (Zeile 13 ohne Kurs, Zeile 14 ohne FX, Zeile 15 mit
    EUR-Wert `0,00` sowie Zeile 16 negativ: genau die 4 Gruppen mit
    `amount_eur is None` oder `amount_eur == 0` bleiben ohne Kachel).
- **Persönliche Rendite (`portfolio_returns`):**
  - Berechenbare aktive Positionen (`eligible_count / active_count`): `7 / 11`
    (Zeilen 5, 6, 7, 8, 9, 10, 15).
  - Zeilen 11 und 12 fehlen wegen fehlendem Einstand (`MISSING_COST`), obwohl ihr
    Marktwert (`120` bzw. `45` EUR) in `valuation.total_eur` enthalten ist.
  - Zeilen 13 und 14 fehlen wegen fehlendem Marktwert (`UNAVAILABLE_VALUE`),
    obwohl ein geprüfter Einstand (`70` bzw. `150` EUR) vorliegt; ihr Einstand
    darf **nicht** in `eligible_cost_basis_eur` einfließen.
  - Gemeinsame gültige Teilmenge:
    - `eligible_market_value_eur = 120 + 330 + 150 + 100 + 150 + 80 + 0 = 930,00 EUR`
    - `eligible_cost_basis_eur = 100 + 300 + 120 + 80 + 120 + 50 + 40 = 810,00 EUR`
    - `gain_eur = 930 − 810 = +120,00 EUR`
    - `return_percent = (120 / 810) × 100 = 14,814814... %` (`400 / 27 %`)
  - Gruppenverhalten bei Teilpositionen:
    - Gruppe `SYNTH-MULTI` (Zeilen 8 + 9, beide mit geprüftem Einstand):
      `eligible_market_value_eur = 250`, `eligible_cost_basis_eur = 200`,
      `gain_eur = 50`, `return_percent = +25 %` (Kachel grün, `+25,00 %`).
    - Gruppe `SYNTH-PARTIAL-COST` (Zeilen 10 + 11, Einstand nur für Zeile 10):
      `return_percent = None` (Kachel grau mit `n. v.`, Fläche bleibt `200,00 EUR`),
      während Zeile 10 in die Depot-Teilmenge (`total`) eingeht.
- **Kursperioden (`price_period_returns`):**
  - `SYNTH-EUR` (geschlossener Markt XETRA am 23.10.2026, Schlusskurs `12,00 EUR`):
    - Heute vs. 22.10.2026 (`12,00` vs. `11,428571...` / Sollfall `10,00` -> `+20 %`)
    - Woche vs. 16.10.2026, Monat vs. 23.09.2026, Jahr vs. 23.10.2025.
  - `SYNTH-USD` (laufende Sitzung NYSE am 23.10.2026 14:00 EDT, Live-Quote
    `50,00 USD`, 2:1-Split am 20.10.2026 bereits in `Close` bereinigt):
    - Unvollständiger heutiger Historienbalken wird ignoriert; kein künstlicher
      50-%-/100-%-Splitsprung zwischen vor und nach dem 20.10.2026.
  - `SYNTH-GBX` (Handelswährung `GBp`, neu gelistet am 01.10.2026):
    - Heute und Woche in Pence verfügbar (ohne EUR-FX-Einfluss); Monat und Jahr
      liefern `percent = None` mit `reason = "insufficient_history"`, niemals `0 %`.
  - `SYNTH-PARTIAL-COST` (unerwartete Datenlücke von 3 fehlenden Handelstagen um
    den Wochentag 16.10.2026):
    - Woche liefert `percent = None` mit `reason = "unexpected_gap"`.
  - `SYNTH-DERIV` (nur Sheet-Ersatzkurs):
    - `supports_period_return` ist `False`; alle Kursperioden bleiben in der
      Treemap grau mit `n. v.` und Tooltip `Datenstand: Kursstand unbekannt`.

## 3. Zulässige numerische Toleranzen und Rundungsregeln

Alle Fachmodule arbeiten durchgängig mit `decimal.Decimal` (keine `float`-Zwischenwerte):

1. **Exakte Produkte und Summen (Toleranz `0`):**
   - `PositionValuation.amount_eur`, `PortfolioValuation.total_eur`,
     `ValuationGroup.amount_eur`, `ReturnAggregate.eligible_market_value_eur`,
     `ReturnAggregate.eligible_cost_basis_eur` und `gain_eur` werden mit
     dynamisch erweiterter Dezimalpräzision über alle endlichen Eingabestellen
     exakt berechnet (`Inexact`-Trap aktiv). Es findet **keine** vorzeitige
     Cent-Rundung statt; die zulässige Abweichung im Test ist `Decimal("0")`.
2. **Brüche, Gewichte und Renditeprozente (Toleranz `<= 1 ULP` der Aufruferpräzision):**
   - `weight_fraction`, `coverage_fraction`, `personal_return.return_percent`
     und `price_period_returns.percent` teilen im `Decimal`-Kontext des
     Aufrufers (Standard: `prec = 28`, `ROUND_HALF_EVEN`).
   - Endliche Dezimalbrüche (z. B. `120 / 100 -> 20 %`, `450 / 400 -> 12,5 %`,
     `250 / 1000 -> 0,25`) sind exakt (`Toleranz = 0`).
   - Periodische Brüche (z. B. `1/3`, `400/27 %`, `9/11`) sind bis auf die
     letzte Stelle der aktiven `Decimal`-Präzision (`1e-26` bei Standardpräzision
     28) deterministisch; die Summe aller Einzelgewichte weicht bei 28 Stellen
     um höchstens `n × 10^(-28)` von `1` ab.
3. **Anzeigeformatierung in der Treemap (` ROUND_HALF_EVEN` auf 2 Nachkommastellen):**
   - `treemap.format_percent` und EUR-Wertanzeigen im Tooltip runden erst bei der
     Textausgabe mit `ROUND_HALF_EVEN` (Banker's Rounding) auf `0,01` Prozentpunkte
     bzw. `0,01 EUR`. `-0,00` wird zu `0,00 %` normalisiert.
   - Die Farbskala klemmt Ausreißer nur für den Farbwert auf `±50 %`
     (`COLOR_CAP_PERCENT`); `tile.percent`, `tile.label` und `tile.tooltip`
     behalten den ungekürzten Wert.
   - `ui.treemap_figure` übergibt `float(tile.amount_eur)` ausschließlich am
     Plotly-Rand für das Layout; das Fachmodell `TreemapTile.amount_eur` bleibt
     exakt `Decimal`.

## 4. Ausführung der Regressionstests

```bash
uv run --locked ruff check .
uv run --locked pytest tests/test_domain_regressions.py
uv run --locked pytest
```
