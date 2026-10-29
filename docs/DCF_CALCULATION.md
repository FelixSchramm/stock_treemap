# FCFF-DCF-Rechner

M8-08 (#77) setzt die Rechnung aus [ADR 0010](adr/0010-innerer-wert-fcff.md) und dem
[Feldvertrag](INTRINSIC_VALUE.md) in `portfolio_dashboard.dcf` um. Das Modul ist
rein und deterministisch: Es verwendet nur `FundamentalReportSet`,
`ScenarioAssumptions` (siehe [Verträge](FUNDAMENTAL_MODELS.md)) und die
Standardbibliothek. Es gibt keine Abhängigkeit von Streamlit, Providern, Netzwerk,
Systemzeit, Bestandsmenge oder Einstand. Bewertungsstichtag ist
`ScenarioAssumptions.valuation_at`. Es gibt keine Standardannahmen; Kursvergleich,
EUR-Umrechnung (#78) und Oberfläche (#80) gehören nicht dazu.

## Öffentliche Funktionen

| Funktion | Ergebnis |
| --- | --- |
| `calculate_scenario(report_set, assumptions)` | `ScenarioCalculation(result, limitations)`; `result` ist der vorhandene `IntrinsicValueResult` |
| `calculate_scenarios(report_set, assumptions_tuple)` | `ScenarioValuationSet` mit 1–3 unabhängigen Szenarien in der **übergebenen Reihenfolge** |
| `calculate_sensitivity(report_set, assumptions, wacc_values, growth_values)` | `SensitivityGrid`; Zeilen folgen der WACC-, Spalten der Wachstumsachse |
| `centered_axis(center, step, count)` | `center ± k·step`; Schrittweite und Anzahl sind explizit, ohne Standardwert |

`ScenarioValuationSet.get(Scenario)` liefert ein Szenario, `missing` die nicht
übergebenen, `value_range` das (niedrigste, höchste) Pro-Aktie-Ergebnis nur dann,
wenn alle drei Szenarien einen Wert haben. Szenarionamen stammen ausschließlich aus
den Annahmen. Ergeben sich unerwartete Werte (z. B. «vorsichtig» über «optimistisch»),
bleibt die Zuordnung bestehen; `scenario_values_not_ordered` in `limitations` macht
das sichtbar. Ein gesperrtes Szenario sperrt keine anderen.

## Rechenweg und Konventionen

Alle Beträge werden zuerst mit `scale_to_currency` bzw. dem Stückfaktor der
Beobachtung auf Haupteinheiten normalisiert (Prognosen liegen bereits in
Haupteinheiten). Es wird mit `Decimal` (50 Stellen, `ROUND_HALF_EVEN`) ohne
Zwischenrundung gerechnet; Überlauf, Unterlauf oder ungültige Operation ergeben den
Sperrgrund `calculation_overflow`, nie 0 oder Unendlich.

```text
FCFF_t = EBIT_t·(1−T_t) + D&A_t − Capex_t − ΔNWC_t           t = 1..5
PV_t   = FCFF_t / (1+WACC)^t                                   Zahlung am Jahresende
NOPAT_6 = NOPAT_5·(1+g);   FCFF_6 = NOPAT_6 − NOPAT_6·g/ROIC
TV_5   = FCFF_6 / (WACC − g),  abgezinst mit (1+WACC)^5
Stammaktionärswert = operativer Wert + überschüssiges Cash + nichtoperative Vermögenswerte
                     − Finanzschulden − Vorzugsansprüche − Minderheitsansprüche
Wert je Aktie = Stammaktionärswert / (ausstehende Aktien + inkrementelle Verwässerung)
Zielpreis = Wert je Aktie · (1 − s)     nur bei gesetztem s und positivem Wert
```

Zeitpunktkonvention: t = 0 ist der Bewertungsstichtag; der Exponent ist die laufende
Nummer des Prognosejahrs (kein halbes erstes Jahr, kein Stichtagsversatz aus den
Periodendaten). Der Terminalwert steht am Ende von Jahr 5.

Der Terminalwert nutzt die ROIC-Formel des ADR. Der Jahr-6-Komponentenplan
(`TerminalPlan`) wird nur abgeglichen: `nopat` muss `NOPAT_5·(1+g)` und
`Capex + ΔNWC − D&A` die Netto-Reinvestition `NOPAT_6·g/ROIC` ergeben, jeweils mit
relativer Toleranz `PLAN_TOLERANCE = 0,01 %` von |NOPAT_6| (`terminal_nopat_mismatch`,
`terminal_reinvestment_mismatch`). Positive Wachstumsraten ohne Reinvestition
werden dadurch abgelehnt. Die Toleranz ist eine technische Rundungsreserve für
Benutzereingaben, keine finanzielle Annahme.

## Sperr- und Statusfälle

Das Ergebnis enthält alle unabhängigen Gründe gemeinsam (`IntrinsicValueResult.issues`):
alle Gründe aus `ScenarioAssumptions.issues_for(report_set)` (Bestätigung, Vollständigkeit,
Altersgrenzen, Identität, Klassenanzahl, nicht unterstützte Emittenten, Bindung an
Berichtssatz/Währung) und zusätzlich:

| Code | Status | Bedeutung |
| --- | --- | --- |
| `historical_ebit_not_positive`, `historical_fcff_not_positive` | Modell nicht unterstützt | EBIT oder aus Komponenten berechneter FCFF ≤ 0 in einem Historienjahr |
| `nwc_change_inconsistent` | ungültig | angegebenes ΔNWC passt nicht zu den NWC-Ständen |
| `terminal_nopat_mismatch`, `terminal_reinvestment_mismatch` | ungültig | Jahr-6-Plan widerspricht g/ROIC |
| `wacc_not_positive`, `growth_negative`, `growth_not_below_wacc`, `roic_not_positive`, `growth_not_below_roic` | ungültig | Satzbedingungen (`WACC > 0`, `0 ≤ g < WACC`, `ROIC > g`) |
| `shares_not_positive` | ungültig | verwässerte Stückzahl ≤ 0 |
| `nonpositive_sustainable_fcff` | ungültig | nachhaltiger FCFF_6 ≤ 0 |
| `no_positive_operating_value` | ungültig | operativer Wert ≤ 0 |
| `no_positive_common_equity_value` | ungültig | Stammaktionärswert ≤ 0; Rechenschritte der Brücke bleiben sichtbar, kein Wert je Aktie, kein Zielpreis |
| `calculation_input_unusable`, `calculation_overflow` | unvollständig / ungültig | unbrauchbare Eingabe bzw. numerischer Fehler |

Sobald ein Sperrgrund vorliegt, gibt es weder Wert je Aktie noch Zielpreis (keine
Kaufkennzahl). Provider-FCF und gewichtete EPS-Aktienzahlen sind reine Kontrollgrößen
und fließen nicht ein. Bestätigte manuelle Ergänzungen gelten über
`effective_observations` und bleiben als Hinweis sichtbar.

Negative FCFF einzelner expliziter Prognosejahre (z. B. Investitionsphase) werden
abgezinst und mit `negative_forecast_fcff` gekennzeichnet; nur der nachhaltige
FCFF_6 muss positiv sein.

## Zwischenwerte, Hinweise und Sensitivität

`result.steps` enthält die ungerundeten Schritte (`historical_fcff_<Jahr>`,
`fcff_year_t`, `pv_year_t`, `pv_explicit_sum`, `nopat_6`, `reinvestment_6`,
`sustainable_fcff_6`, `terminal_value_5`, `pv_terminal_value`, `operating_value`,
`terminal_value_share`, Brückenposten, `common_equity_value`, `diluted_shares`,
`value_per_share`, `target_price`). `result` nennt außerdem Eingabe-/Annahmenversion,
Modellversion (`fcff-dcf-v1`) und Quellenverweise. `limitations` enthält Codes, die
die Oberfläche beschriftet: `estimate_not_confidence_interval`,
`constant_wacc_and_sustainable_roic`, `year_end_cash_flows_no_mid_year_shift`,
`terminal_value_dominant` (Terminalwertanteil ≥ 75 %), `negative_forecast_fcff`.
Die Szenariospanne ist eine Modellschätzung und kein statistisches Konfidenzintervall.

Die Sensitivität rechnet jede Zelle mit expliziten WACC-/g-Werten neu, einschließlich
`FCFF_6` mit der Reinvestition `g/ROIC`; die Achsen werden nicht sortiert, Duplikate
abgelehnt. Ungültige Kombinationen (WACC ≤ 0, g < 0, g ≥ WACC, g ≥ ROIC) und Zellen
mit nichtpositivem Eigenkapitalwert sind nicht berechenbar und tragen ihren Grund. Ist
die Basis gesperrt, ist jede Zelle gesperrt.

## Prüfung

`uv run --locked pytest tests/test_dcf.py` verwendet ausschließlich erfundene Daten.
Erwartungswerte stammen aus dem von Hand hergeleiteten Beispiel in
[INTRINSIC_VALUE.md](INTRINSIC_VALUE.md), aus Perpetuitätsidentitäten ohne
5-Jahres-Aufteilung (konstanter FCFF 100 / 10 % = 1000; mit g = 5 %, ROIC = 25 %:
`FCFF_1/(WACC−g)`) sowie aus exakter `Fraction`-Arithmetik. Toleranz für Pro-Aktie-Werte
des Referenzbeispiels: 1e-12; Tabellenwerte in Millionen: 5e-7.
