## Begriffe und Datenstatus

**n. v. (NA)** heißt „nicht verfügbar“: Yahoo liefert keine ausreichenden Daten,
Metadaten widersprechen dem gewählten Listing oder eine Berechnung ist fachlich
nicht sinnvoll. Eine unbekannte Zahl wird niemals als null ersetzt.
**Noch nicht angebunden** bezeichnet eine fehlende Datenquelle oder eine noch
nicht geprüfte Zuordnung. Andere Analysen bleiben trotzdem nutzbar.

Die Unternehmensliste kommt aus Depot und Watchlist im Google Sheet. Nur die
vorhandene Yahoo-Zuordnung wird verwendet; ein Anzeigename oder ein freier Ticker
ist keine bestätigte Zuordnung. Fehlende Zuordnungen bleiben mit Erklärung sichtbar.
Doppelte identische Listings werden zusammengeführt; unterschiedliche Handelsplätze
bleiben unterscheidbar. „Unternehmen aus Sheet laden“ liest keine Marktkurse für
alle Positionen. Erst „Analysedaten laden“ ruft die gewählte Aktie und höchstens drei
weitere Vergleichsaktien ab. Das Sheet wird nicht verändert.

## Kennzahlen

| Begriff | Bedeutung und Grenze |
|---|---|
| Umsatz | Gemeldete Erlöse einer Berichtsperiode; kein Zahlungszufluss. |
| EBIT / Operating Income | Operatives Ergebnis vor Finanzierung und Steuern; Anbieterdefinition beachten. |
| Nettogewinn / Net Income | Gewinn nach Finanzierung, Steuern und sonstigen Effekten. |
| Brutto-, operative und Nettomarge | Entsprechendes Ergebnis geteilt durch Umsatz. |
| CFO / Operating Cash Flow | Zahlungsstrom aus der laufenden Geschäftstätigkeit. |
| Capex | Cash-Investitionen. In der Cashflow-Überleitung als Abfluss, sonst als positiver Betrag. |
| FCF / Free Cash Flow | CFO minus absoluter Cash-Capex; Anbieter-FCF wird auf diese Gleichung geprüft. Kein garantierter nachhaltiger Ertrag. |
| TTM | Trailing Twelve Months: Summe von vier aufeinanderfolgenden Quartalen. Fehlende Quartale unterbrechen die Reihe. |
| ROIC | Operativer Gewinn nach berichteten Steuern / durchschnittliches investiertes Kapital aus Anfang und Ende der Periode, inklusive Goodwill. |
| SBC / Stock Based Compensation | Aktienbasierte Vergütung; nicht zahlungswirksam im operativen CF, aber wirtschaftlich nicht kostenlos. |
| Working Capital | Bindung/Freisetzung operativen Umlaufkapitals; hier gemeldeter Cashflow-Effekt. |
| EPS | Gewinn je Aktie. Yahoo-Earnings-EPS und verwässertes Abschluss-EPS können unterschiedlich definiert sein. |
| KGV / P/E | Kurs geteilt durch Gewinn je Aktie. Nur bei passender Währung und Aktienbasis; historischer Median ist kein fairer Wert. |
| Forward P/E | Kurs geteilt durch geschätzten zukünftigen Gewinn je Aktie. |
| EV/EBITDA | Unternehmenswert / Ergebnis vor Zinsen, Steuern und Abschreibungen. Cash-Investitionen bleiben außen vor. |
| Reverse DCF | Gesuchtes FCF-Wachstum, das unter festgelegten Renditeannahmen den Kurs erklärt. Vereinfachter Eigenkapital-Cashflow-Proxy, **nicht** das FCFF-Modell unter „Innerer Wert“. |
| Kursrendite | Veränderung des unbereinigten Schlusskurses; keine Dividenden. |
| Gesamtrendite-Näherung | Veränderung von Yahoo `Adj Close`, inklusive Anbieterbereinigungen. Ohne persönliche Steuern und Gebühren. |
| Drawdown | Abstand zum bisher beobachteten Hoch. Linie nutzt die geladene Vorgeschichte; Zeitraum-Maximalverlust nur das gewählte Fenster. |
| Volatilität | Stichprobenstandardabweichung täglicher Log-Renditen × √252; kein Maß für Insolvenzrisiko. |
| OHLC / SMA | Eröffnung, Hoch, Tief, Schluss / gleitender Durchschnitt von 20 oder 50 Handelstagen. |
| EPS-Überraschung | Berichtetes EPS relativ zur Analystenschätzung; kein EPS-Wachstum. |
| Dividendenrendite TTM | Tatsächliche Ausschüttungen des vorangegangenen Kalenderjahres / Kurs. Nicht letzte Rate × 4. |
| Analysten-Spanne | Niedrigste bis höchste Schätzung; **kein** Konfidenzintervall. |
| Implizite Volatilität (IV) | In Optionspreisen enthaltene annualisierte Schwankungserwartung. Keine Richtungsprognose. |
| Abrufstand / Meldestichtag | Zeitpunkt unseres Abrufs / Zeitpunkt der berichteten Information. Beide können weit auseinanderliegen. |

## Diagramme und Yahoo-Schnittstellen

Die Nummerierung folgt V4. `t = yf.Ticker(gewähltes_symbol)`.
„Yahoo“ bedeutet: angebunden **sofern der Anbieter für das Unternehmen Daten liefert**.
Das ist keine Abdeckungs- oder Qualitätsgarantie.

| Nr. | Diagramm | Quelle / Funktion | Verfügbarkeit |
|---|---|---|---|
| 01 | Kurs in EUR | `t.history(auto_adjust=False)`, FX-Historie | Yahoo, FX erforderlich |
| 02 | Kurs und Ergebnisse | `t.get_earnings_dates()`, Historie | Yahoo, EPS-Schätzungen erforderlich |
| 03 | Cashflow je Quartal und TTM | `t.quarterly_cashflow` | Yahoo; TTM nur mit vier lückenlosen Quartalen |
| 04 | Kerzen und Volumen | `t.history()` | Yahoo, native Handelswährung |
| 05 | Indexierter Vergleich | Historien der gewählten Sheet-Aktien + FX | Yahoo; gemeinsame Start-/Endtage |
| 06 | Monatsrenditen | Historien + FX | Zwölf vollständige Monate |
| 07 | Drawdown | Historien + FX | Hoch aus bis zu zehn Jahren |
| 08 | Rendite und Risiko | Historien + FX | Berechnete Kennzahlen |
| 09 | Jährlicher Cashflow | `t.cashflow`, `t.income_stmt` | Yahoo |
| 10 | Wachstum und Margen | `t.income_stmt`, `t.quarterly_income_stmt` | Yahoo, tatsächliche Berichtsperioden |
| 11 | Earnings-Reaktion | Earnings + Aktien-/SPY-Historie | US-Handelssitzungen geprüft; andere Börsenplätze noch nicht angebunden |
| 12 | Rekonstruiertes KGV | Earnings + Historie | Nur geprüfte Aktienbasis, gleiche Währung, geprüfte US-Ereigniszuordnung |
| 13 | US-Marktvergleich | Aktie + SPY/QQQ bzw. S&P 500/Nasdaq-100 | US-Referenz, auch bei ausländischen Unternehmen ausdrücklich kein lokaler Marktindex |
| 14 | Dividende und Deckung | `t.dividends`, `t.cashflow`, `t.income_stmt` | Yahoo |
| 15 | Analystenkonsens | `t.analyst_price_targets`, `t.recommendations` | Yahoo-Snapshot |
| 16 | Kurszieländerungen | `t.upgrades_downgrades` | Einzelmeldungen, kein historischer Konsens |
| 17 | Insider | `t.insider_transactions` | US-Listings in USD; sonst Transaktionswährung noch nicht bestätigt |
| 18 | Institutionen | `t.institutional_holders` | Anteile und Meldedaten; Snapshot |
| 19 | Optionen | `t.options`, `t.option_chain(expiry)` | Nur Quotes mit ausreichender Liquidität/Aktualität |
| 20 | Bilanz und Zinsdeckung | `t.balance_sheet`, `t.income_stmt` | Yahoo |
| 21 | ROIC | Bilanz + GuV + Cashflow | Anfangs-/Endkapital und gültige Steuerquote erforderlich |
| 22 | Gewinnqualität | GuV + Cashflow | Yahoo |
| 23 | Kapitalverwendung | `t.cashflow` | Yahoo |
| 24 | Entwicklung je Aktie | GuV + Cashflow | Verwässerte durchschnittliche Aktien laut Abschluss, nicht automatisch ADR-Einheiten |
| 25 | Schätzungen und Revisionen | `t.earnings_estimate`, `t.revenue_estimate`, `t.eps_trend`, `t.eps_revisions` | Yahoo-Snapshot für identische Zielperioden |
| 26 | Portal-Multiplikatoren | `t.get_valuation_measures()` | Yahoo; andere Gewinnbasis als 12 möglich |
| 27 | Reverse DCF | Cashflow + `t.info` + Kurs | Gleiche Währung, geprüfte Aktienbasis und konsistenter Snapshot erforderlich |
| 28 | Geschäftssegmente | Geschäftsberichte / Segmentquelle | **Noch nicht angebunden. Keine festen Microsoft-Zahlen als Ersatz.** |
| 29 | Cashflow-Überleitung | `t.cashflow` | CFO − Cash-Capex = FCF, letztes vollständiges Geschäftsjahr |

## Währungen und Perioden

Kurse und Kerzen stehen in der bestätigten Handelswährung, Geschäftszahlen in
`financialCurrency`. Eine Einheit wie Pence wird ohne bestätigte Normalisierung
nicht stillschweigend als Pfund behandelt. Bei widersprüchlichen Metadaten wird
kein Diagramm mit einer geratenen Einheit erzeugt. EUR/USD gilt für die
Vergleichsgrafiken; eine EUR-Ansicht ändert nicht die Währung der Geschäftsberichte.
FX darf nur aus vorherigen Beobachtungen und höchstens sieben Kalendertagen stammen.
Juni ist **kein** universelles Geschäftsjahresende: relevant sind die gelieferten
Berichtsperioden des gewählten Unternehmens.

Das ursprüngliche API-Glossar der Financial Pipeline ist zusätzlich unverändert
unter `docs/reference/financial-pipeline-glossary.md` archiviert. Seine alten
„open“-Statusangaben und die MSFT-Nummerierung sind historisch. Die Tabelle oben
beschreibt die aktuelle Dashboard-Anbindung.
