# Innerer Wert und Kurs vergleichen

M8-09 (#78) vergleicht den Modellwert aus dem [FCFF-DCF-Rechner](DCF_CALCULATION.md)
mit einem geprüften Börsenkurs derselben Aktie. `portfolio_dashboard.price_comparison`
ist rein und deterministisch: Es nutzt nur die vorhandenen Verträge
(`ScenarioCalculation`/`ScenarioValuationSet`, `Valuation` mit `QuoteUnit`, `FxRate`
aus [EUR_VALUATION.md](EUR_VALUATION.md), die Registry geprüfter Listings) und die
Standardbibliothek. Es gibt keine Abhängigkeit von Streamlit, Providern, Netzwerk,
Systemzeit, Bestandsmenge oder Einstand, und keine Oberfläche (#79/#80). Der
vergleichende Zeitpunkt ist ausdrücklich `ComparisonPolicy.as_of`.

## Öffentliche Namen (für #79/#80)

| Name | Aufgabe |
| --- | --- |
| `compare_to_price(calculation, instrument, price, listings, fx_rates, policy)` | ein Szenario gegen einen Kurs; liefert `PriceComparison` |
| `compare_scenarios_to_price(valuation_set, instrument, price, listings, fx_rates, policy)` | alle Szenarien eines `ScenarioValuationSet` in übergebener Reihenfolge mit demselben Kurs, FX und `as_of`; liefert `ScenarioPriceComparisons` (`comparisons`, `missing`, `limitations`, `get(Scenario)`) |
| `ComparisonPolicy(as_of, max_fx_age, max_price_age)` | Vergleichszeitpunkt (zeitzonenbewusst) und Altersgrenzen; keine Standardwerte |
| `PriceComparison` | Ergebnis, siehe unten |
| `ComparisonReason`, `ComparisonNote`, `PricePosition` | stabile Codes für Sperrgründe, sichtbare Hinweise und die Lage des Kurses |
| `FxCheck`, `EurAmounts`, `ComparisonSnapshot` | FX-Prüfung je Währung, EUR-Anzeige, Zeitpunkte |
| `valuations.unit_price_status(quote, listing)` | aus `value_position` herausgelöste Preisprüfung (unverändertes Verhalten), hier wiederverwendet |

`fx_rates` ist wie bei `value_portfolio` ein Mapping `Währung -> FxRate` (Richtung
Währung nach EUR, Haupteinheiten). Der Aufrufer ordnet den Kurs dem Instrument zu
und liefert den bereits gewählten Preis (z. B. aus `choose_valuation`); das Modul
sucht und lädt nichts.

## Zwei getrennte Kennzahlen

```text
Abschlag zum Modellwert           = (1 − Kurs / Modellwert) × 100     discount_to_model_percent
Abstand vom Kurs zum Modellwert   = (Modellwert / Kurs − 1) × 100     distance_to_model_percent
```

Kurs 80, Modellwert 100: 20 % Abschlag, 25 % Abstand (nicht verwechseln). Beide
werden nur bei positivem, gültigem Kurs und positivem Modellwert gebildet, sonst
sind sie `None` (keine Division durch null). Liegt der Kurs über dem Modellwert,
sind beide negativ (Kurs 125, Modellwert 100: −25 % und −20 %);
`position` (`BELOW_MODEL_VALUE`, `AT_MODEL_VALUE`, `ABOVE_MODEL_VALUE`) benennt das
eindeutig, damit die Oberfläche kein Vorzeichen deuten muss. Es sind weder Kauf-
noch Verkaufsurteile. Die Werte bleiben ungerundet (50 Stellen, `ROUND_HALF_EVEN`);
gerundet wird erst bei der Anzeige, z. B. mit `treemap.format_percent`.

## Währung, Einheit und FX

* Modellwert: Berichtswährung, Haupteinheit je Aktie (`model_value`, `reporting_currency`).
* Kurs: `price.price × scale_to_currency` in der Währung der Kurseinheit
  (`price_major`, `price_currency`). GBp (Pence, Faktor 0,01) wird dadurch zuerst zu GBP.
* **Gleiche Währung** (z. B. Berichtswährung GBP, Kurs in Pence): direkter Vergleich;
  FX ändert das Verhältnis nicht und wird nicht benötigt. Fehlendes oder veraltetes
  FX sperrt dann nur die EUR-Anzeige (`eur.reasons`), nicht den Vergleich.
* **Verschiedene Währungen**: beide Seiten werden mit je einem gerichteten `FxRate`
  nach EUR überführt und in EUR verglichen (`COMPARED_VIA_EUR`). Eine Seite in EUR
  braucht keinen Kurs.
* **Richtung**: nur `FxRate(X, "EUR")` ist für Währung X zulässig. Die Gegenrichtung
  (`FxRate("EUR", X)`) wird nicht invertiert, sondern als `FX_DIRECTION_MISMATCH`
  gemeldet; der abgelehnte Kandidat bleibt in `fx_checks` sichtbar.
* **EUR-Sheet-Ersatzpreis** (`QuoteUnit("EUR", 1)`, Quelle Sheet, z. B. aus
  `choose_valuation`) wird nicht erneut umgerechnet, auch wenn das Listing in
  einer Fremdwährung notiert; nur der Modellwert erhält dann FX. Der Hinweis
  `PRICE_SHEET_REPLACEMENT` und die Warnungen des Preises bleiben sichtbar.
* **FX-Alter**: Beobachtungszeit unbekannt (`FX_TIME_UNKNOWN`), älter als
  `max_fx_age` (`FX_STALE`, genau am Limit zulässig) oder nach `as_of`
  (`FX_TIME_AFTER_AS_OF`) sperrt jede Umrechnung. Es gibt keinen Ersatz durch den
  Abrufzeitpunkt.
* `EurAmounts` hält Modellwert, Zielpreis und Kurs je Aktie in EUR, jeweils `None`
  ohne nutzbaren Weg nach EUR. Ein fehlender Kurs oder fehlendes FX lässt den Modellwert
  in Berichtswährung (und alle EUR-Beträge mit nutzbarem Weg) sichtbar.

## Identität: nur dieselbe Aktie

Der Kurs gilt nur, wenn das übergebene `Instrument` in der Registry geprüfter
Listings aufgelöst wird, dieselbe `instrument_id` wie die Berechnung
(`result.inputs.instrument_id`) hat, eine Stammaktie (`InstrumentType.STOCK`,
`EquityForm.SHARE`) ist. Weitere Börsenplätze derselben bestätigten Aktie sind
zulässig. Sonst gibt es keinen Vergleich und keinen angezeigten Kurs:
`INSTRUMENT_UNVERIFIED`, `INSTRUMENT_MISMATCH`, `ADR_RATIO_UNSUPPORTED`,
`NOT_COMMON_SHARE`. **Grenze:** Ein ADR-Verhältnis (Aktien je ADR) ist in keinem
Vertrag vorhanden und wird nicht geraten; ADR- und andere Anteilsklassenkurse bleiben
gesperrt.

## Quellenstatus und gemeinsamer Snapshot

`PriceComparison` reicht die unveränderte `ScenarioCalculation` durch
(`calculation`, mit Rechenschritten, Quellenverweisen, `issues`, `model_issues` und den
Einschränkungscodes wie `terminal_value_dominant` und `negative_forecast_fcff`, die
nicht am `IntrinsicValueResult` stehen). `limitations` ergänzt diese Codes um

* `model_value_as_of_valuation_date_not_rolled_forward`: der Modellwert gilt zum
  `valuation_at` der Annahmen und wird nicht auf `as_of` fortgeschrieben;
* `per_share_only_no_quantity_or_cost_basis`: nur Pro-Aktie-Vergleich, keine Menge,
  kein Einstand, keine Portfolio-Summen.

`snapshot` zeigt `as_of`, `model_valuation_at`, Preisquelle, Beobachtungs- und
Abrufzeit des Kurses sowie sein Alter (unbekannt bleibt `None`). `notes`:
`PRICE_CACHED` (letzter Providerpreis nach Fehler), `PRICE_SHEET_REPLACEMENT`,
`PRICE_TIME_UNKNOWN`, `PRICE_OLDER_THAN_LIMIT` (älter als `max_price_age`, nur
Hinweis), `FX_CACHED`. `warnings` enthält die `DataWarning` von Kurs und genutztem FX.
Ein Kurs oder Modellstichtag nach `as_of` ist ein Sperrgrund
(`PRICE_TIME_AFTER_AS_OF`, `MODEL_VALUATION_AFTER_AS_OF`). Alle Szenarien einer
`ScenarioPriceComparisons` teilen denselben Kurs, dieselbe FX-Auswahl und dasselbe
`as_of`.

Weitere Sperrgründe (mehrere gleichzeitig möglich, nie ein Zahlenwert): kein
Modellwert (`MODEL_VALUE_UNAVAILABLE`, inkl. nichtpositivem Eigenkapitalwert),
fehlender/ungültiger Kurs, unbekannte Einheit, Provider-/Einheitenwiderspruch zum
geprüften Listing (`PRICE_*`), Kurs ≤ 0 (`PRICE_NOT_POSITIVE`) und
Rechenfehler (`ARITHMETIC_ERROR`).

## Die Plan-Periodendaten und der Stichtag

Die Plan-Periodendaten beeinflussen die Diskontierung nicht (Vereinfachung aus
[ADR 0010](adr/0010-innerer-wert-fcff.md)); das ändert dieser Vergleich nicht. Er macht
den Stichtag des Modellwerts (`model_valuation_at`) und seinen Abstand zu `as_of`
sichtbar.

## Prüfung

`uv run --locked pytest tests/test_price_comparison.py tests/test_valuations.py`
verwendet ausschließlich erfundene Daten und kein Netzwerk. Erwartungswerte sind von
Hand hergeleitet (80 gegen 100 → 20 %/25 %; 70 EUR gegen 100 USD × 0,8 → 12,5 %;
150 Pence gegen 2 GBP → 25 %) oder mit exakter `Fraction`-Arithmetik gebildet.

## Offene Besitzerfragen

1. Welche Altersgrenzen gelten für FX (`max_fx_age`) und Kurs (`max_price_age`)? Es gibt
   keine Standardwerte; Wochenenden und Feiertage sind nicht kalendarisch berücksichtigt.
   Soll ein zu alter Kurs sperren statt nur zu markieren?
2. Soll ein FX ohne Beobachtungszeit wie bisher sperren, oder genügt der Abrufzeitpunkt?
3. Soll der Modellwert (Stichtag `valuation_at`) bei großem Abstand zu `as_of` sperren,
   fortgeschrieben werden oder wie jetzt nur gekennzeichnet bleiben?
4. Soll der Vergleich bei verschiedenen Währungen weiterhin über EUR laufen (zwei
   Kurse, evtl. verschiedene Zeitpunkte) oder ein direktes Kreuz Berichts-/Handelswährung
   verwenden?
5. Soll es später ein bestätigtes ADR-Verhältnis (Aktien je ADR) im Vertrag geben?
   Bis dahin bleiben ADR-Kurse gesperrt.
6. Offene Frage A aus dem Review von PR #106 (Planjahr 1 und Stichtag) bleibt
   unverändert und wird hier nur sichtbar gemacht.
