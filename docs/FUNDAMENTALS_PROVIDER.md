# yfinance-Fundamentaldaten: Adapter und Normalisierung

M8-04 ([#73](https://github.com/FelixSchramm/stock_treemap/issues/73)). Dieser Teil
holt Jahresabschlüsse eines **geprüften** Emittenten über yfinance und überführt sie
in die vorhandenen Verträge aus [FUNDAMENTAL_MODELS.md](FUNDAMENTAL_MODELS.md). Die
Anforderungen stammen aus dem [Abdeckungsbericht](FUNDAMENTALS_COVERAGE.md) (#71).
Kein Cache, kein Ladepfad (#74), keine Oberfläche, kein zweiter Anbieter, kein
kostenpflichtiger Zugang. Der Kursprovider (`market_data`) bleibt unverändert.

## Aufbau

| Modul | Aufgabe | Abhängigkeiten |
| --- | --- | --- |
| `fundamentals_provider` | Adapter: ruft Jahres-GuV, -Bilanz, -Cashflow, Aktienzahl-Reihe und eine **Minimalauswahl** der Metadaten ab und wandelt sie in `ProviderStatements` | pandas; yfinance wird erst beim Aufruf der Standard-Ticker-Fabrik importiert |
| `fundamentals_normalization` | reine Normalisierung `normalize_statements(identity, statements, expectation)`, `build_report_set(...)`, `trailing_twelve_months(...)`, `FIELD_CATALOG` | nur Standardbibliothek und die Fachverträge; ein Test verbietet pandas, yfinance, Streamlit und Netzwerk |

`FundamentalsProvider` ist der kleine austauschbare Vertrag
(`fetch(identity) -> ProviderStatements`). `YFinanceFundamentalsProvider` nimmt
Ticker-Fabrik, Uhr und `sleep` entgegen; Tests laufen deshalb ohne Netzwerk und ohne
yfinance. Der Adapter wirft keine Providerfehler, sondern liefert bereinigte Status.

## Identität und Zuordnung

Abgerufen wird ausschließlich `identity.source.entity_id`, und nur wenn
`identity.source.provider == "yfinance"`. Es gibt keine Suche, Umbenennung oder
Ersatzquelle. Unterstützte Emittenten (`identity.issues` ohne Sperrgrund) sind
Voraussetzung; Banken, Versicherer, REITs, ADRs und unbekannte Emittentenarten lösen
**keinen** Abruf aus (`identity_not_supported`). Liefert die Metadatenantwort ein
anderes `symbol`, wird die gesamte Antwort verworfen (`identity_mismatch`).
`quoteType` muss `EQUITY` sein, die Handelswährung zur geprüften Kurseinheit des
Listings passen (`GBp`/`ZAc`/`ILA` nur mit Faktor 0,01) und `financialCurrency` genau
der vom Besitzer bestätigten Berichtswährung (`ProviderExpectation`) entsprechen.
Berichts- und Handelswährung sind getrennt; eine Abweichung ist nur ein Hinweis.

## Fehlerklassen (bereinigt)

Nur diese Codes erscheinen, nie Ausnahmetexte, Symbole, URLs oder Rohantworten; der
yfinance-Logger ist während des Abrufs stummgeschaltet. Im Normalisierungsergebnis
heißt der Grund `<Datensatz>_<Klasse>`, z. B. `annual_balance_timeout`.

| Klasse | Bedeutung |
| --- | --- |
| `timeout` | Zeitüberschreitung (yfinance wendet sein eigenes Netzwerk-Timeout an) |
| `access_restricted` | Drosselung/Zugriffsbeschränkung (`YFRateLimitError`, HTTP 401/403/429); wird nicht wiederholt |
| `provider_error` | sonstiger Providerfehler |
| `empty` | leere oder nur aus NaN bestehende Antwort |
| `unexpected_schema` | geänderte Struktur: keine Tabelle, Perioden nicht in den Spalten, Nicht-Datumsspalten, doppelte Spalten/Zeilen, Zeilenlabel keine Texte |
| `invalid_values` | nicht numerische oder nicht endliche Zellen (`inf`, Text, bool) wurden entfernt; blockiert als *ungültig* |
| `metadata_placeholder` | gedrosselter Metadaten-Platzhalter ohne Typ/Währung/Symbol; höchstens 3 Versuche mit wachsenden Pausen, danach Abbruch ohne weitere Abrufe |
| `identity_mismatch`, `identity_not_supported`, `unsupported_provider` | Zuordnung nicht verwertbar |

Fehlende Zeilen und NaN sind *fehlend*, nie 0; ein ausdrücklich gelieferter Wert 0
bleibt ein gelieferter Wert.

## Normalisierung

- **Zeilenwahl:** feste Präferenzlisten (identisch zu den geprüften Listen aus
  `fundamentals_coverage`; ein Test erzwingt Gleichheit). Je Feld wird **eine** Zeile für
  die ganze Reihe gewählt, nie je Jahr gemischt. Eine Zeile jenseits der ersten Präferenz
  ist eine **Ausweichzeile**: Hinweis `fallback_row_<erste Zeile>` und Vermerk in der
  Provenienz («Definition kann abweichen»). `EBIT` gilt nur als Kontrolle
  (`ebit_control_definition_differs`).
- **Capex:** Provider-Abfluss (negativ) wird ausdrücklich zum positiven Abzug umgekehrt;
  ein positiver Providerwert ist *ungültig* (`capex_sign_unexpected`), 0 wird mit Hinweis
  übernommen. Negative D&A und Finanzschulden sind *ungültig*, kein Absturz.
- **Provider-FCF:** nur als Kontrollbeobachtung `provider_fcf`; fehlt Capex und gleicht der
  FCF dem operativen Cashflow, steht `provider_fcf_equals_operating_cash_flow` im Ergebnis.
  Es entsteht nirgends ein FCFF.
- **NWC je Bilanzstichtag:** (Umlaufvermögen − Liquidität) − (kurzfristige Verbindlichkeiten
  − kurzfristige Finanzschulden); fehlt eine Komponente, ist genau dieser Stichtag
  *fehlend*. Hat der älteste Eröffnungsstichtag kein NWC, wird das älteste Jahr verworfen
  (`oldest_period_dropped_nwc_gap`), statt es halb zu füllen. ΔNWC leitet der Rechner ab.
- **Perioden:** Es werden nur Geschäftsjahre erzeugt. Spalten ohne Werte werden verworfen,
  Spalten nach dem Abrufdatum sind ungültig (`future_period`). Periodenenden müssen dem
  bestätigten Geschäftsjahresende ± 10 Tage entsprechen (`fiscal_year_end_mismatch`),
  aufeinanderfolgende Enden 364–371 Tage auseinanderliegen (sonst endet die Kette,
  `annual_history_gap`); Cashflow-/Bilanzspalten dürfen ± 10 Tage abweichen
  (`period_end_aligned`). Der Beginn des ältesten Jahres braucht einen belegten
  Vorgängerstichtag (`oldest_period_start_unknown`). Quartals- und YTD-Daten werden nicht
  abgerufen und nie in den Berichtssatz übernommen.
- **TTM:** `trailing_twelve_months` summiert nur die neuesten vier vollständigen,
  lückenlos aneinandergrenzenden, nicht überlappenden Quartale. Geschäftsjahres-, YTD- und
  TTM-Einträge tragen nie bei (keine Doppelzählung); ein fehlendes oder unvollständiges
  Quartal ergibt kein TTM (`ttm_incomplete`, `ttm_no_quarters`, `ttm_overlapping_periods`).
  Stichtagswerte lassen sich nicht übergeben. Der Adapter bildet daraus keine Beobachtung.
- **Aktienzahl:** nur die jüngste *datierte* Zahl der Zeitreihe (≤ Abrufdatum);
  undatierte Zähler bleiben ein Vorschlag (`shares_undated`). Weichen die Quellen
  (Zeitreihe, Metadaten, Bilanzzahl) um mehr als 10 % ab, ist die Beobachtung ungültig
  (`share_sources_conflict`). Die Altersgrenze (120 Tage) prüft `issues_at`. Die
  Anzahl der Stammaktienklassen kommt nie vom Provider. Die historische gewichtete
  EPS-Stückzahl ist nur Kontrolle.
- **Skala:** yfinance liefert absolute Einheiten (Faktor 1). EPS ≈ Ergebnis / Ø verwässerte
  Aktien (± 5 %) prüft das; ein Widerspruch sperrt alles (`scale_inconsistent`), eine
  nicht prüfbare Skala bleibt ein Hinweis (`scale_unverified`).
- **Sperren statt Fallback:** Währungs-, Typ-, Identitäts- oder Skalenkonflikte entfernen
  **alle** Zahlen (`numbers_withheld`, `currency=None`); es gibt keine EUR- und keine
  Nullannahme, `build_report_set` liefert dann `None`.
- **Berichtssatz-Identität:** Der Abruf liefert einen Satz aus einem Zeitpunkt
  (`report_set_id = yfinance-fetch-<UTC-Zeit>`, `revision = provider-revision-unknown`).
  Veröffentlichungs- und Berichtsdatum bleiben unbekannt (nie aus dem Abruf abgeleitet).

## Feldkatalog: automatisch, Kontrolle, manuell

`FIELD_CATALOG` listet jedes Vertragsfeld mit Herkunft. «Manuell» heißt: Das Ergebnis
enthält immer eine *fehlende* Beobachtung (`manual_required`) für den Pfad der
`ManualAdjustment`-Ergänzung; ein Providerwert erscheint höchstens als
gekennzeichneter `Suggestion`, nie als Beobachtung.

| Feld | Herkunft | Behandlung |
| --- | --- | --- |
| EBIT | automatisch | `Operating Income`, Ausweichzeile gekennzeichnet; Besitzer bestätigt die Abgrenzung |
| D&A, Capex | automatisch | Cashflow; Capex-Vorzeichen umgekehrt; Lücke → fehlend |
| operatives NWC (4+ Stichtage) | automatisch | aus vier Bilanzkomponenten je Stichtag |
| Finanzschulden | automatisch | `Total Debt` inkl. Leasing, wie berichtet |
| aktuelle Aktienzahl | automatisch | nur datiert, Quellen müssen übereinstimmen |
| Provider-FCF, gewichtete EPS-Aktienzahl | Kontrolle | nie Eingabe des Modells |
| ΔNWC | abgeleitet | berechnet der Rechner |
| **operativer Steuersatz** | **manuell** | Vorschlag: effektive Quote (Steuer / Vorsteuerergebnis, 0 ≤ Quote < 1) |
| **überschüssiges Cash** | **manuell** | Vorschlag: Bruttoliquidität als Obergrenze |
| **nichtoperative Vermögenswerte** | **manuell** | keine Providerquelle |
| **Vorzugs- und Minderheitsansprüche** | **manuell** | Vorschlag: Buchwert des Providers, falls die Zeile vorhanden ist; eine fehlende Zeile ist nie 0 |
| **inkrementelle Verwässerung** | **manuell** | keine Providerquelle |

## Berichtssatz und Besitzerbestätigungen

`build_report_set(normalized, capital_structure, input_version, OwnerConfirmations)`
erzeugt den `FundamentalReportSet` des Vertrags (oder `None`, wenn Zahlen gesperrt sind
oder Jahresperiode bzw. datierte Aktienzahl fehlen). Die Kapitalstruktur
(Klassenanzahl) liefert immer der Besitzer; ihr Datum muss dem Aktienzahl-Stichtag
entsprechen. Ohne ausdrückliche Bestätigung enthält der Satz drei blockierende Gründe
(`UNCONFIRMED_ASSUMPTIONS`), die erst mit einem neuen Satz entfallen:

- `ebit_definition`: Provider-EBIT entspricht dem gemeinten operativen Ergebnis (#71: 2–3 %
  Abweichung in Stichproben).
- `share_split_basis`: kein Aktiensplit verzerrt Aktienzahl, Bericht und Kurs; der
  Provider belegt die Splitbasis nicht (`provider-unverified`).
- `lease_treatment`: EBIT und `Total Debt` behandeln Leasing gleich (Betriebsleasing im EBIT
  und die Leasingschuld in der Brücke dürfen nicht doppelt belasten). Die Zeile
  `Capital Lease Obligations` wird nur als Hinweis ausgewertet
  (`financial_debt_includes_leases`, `lease_split_not_visible`).

Danach bleiben manuelle Felder, Altersgrenzen und die Rechnerprüfungen in
`issues_at`/`dcf` maßgeblich. Dass der Satz formal vollständig ist, belegt weder eine
Eignung noch einen positiven FCFF.

## Hinweise für #74 (Cache und Ladepfad)

- `ProviderStatements` enthält rohe Providerzahlen: nur im geschützten Speicher ablegen,
  nie in Logs. Die Normalisierung ist deterministisch aus (`identity`, `statements`,
  `expectation`) wiederholbar, ein Cache kann also die Statements statt der Ergebnisse
  halten. Ein neuer Abruf verjüngt keinen Geschäftsbericht (`issues_at` prüft das Alter).
- Alte und neue Abrufe nicht mischen; jede Normalisierung bindet an genau einen Abruf.
- Abrufe sparsam: höchstens ein Abruf je Instrument, Pausen (`pause_seconds`), keine
  Wiederholung bei `access_restricted`. `ProviderExpectation` (Berichtswährung,
  Geschäftsjahresende) gehört zur privaten Zuordnung des Besitzers.
- Nicht abgedeckt: Zwischenbilanzen (die Brücke nutzt den jüngsten Jahresabschluss),
  Restatement-/Berichtssatz-IDs, Veröffentlichungsdaten.

## Grenzen

Nur durch die Stichprobe aus #71 plausibilisiert; Untereinheiten, ADRs, Finanzunternehmen,
Mehrklassen und weitere Börsen sind ungeprüft. yfinance ist ein inoffizieller Zugang ohne
Zusage zu Umfang und Kostenfreiheit.

## Prüfung

```bash
uv run --locked pytest tests/test_fundamentals_normalization.py tests/test_fundamentals_provider.py
```

Alle Tests verwenden ausschließlich erfundene Strukturen und kein Netzwerk. Die
zusätzliche lokale Plausibilisierung gegen die privaten Snapshots aus #71 ist nicht
Teil der Tests.
