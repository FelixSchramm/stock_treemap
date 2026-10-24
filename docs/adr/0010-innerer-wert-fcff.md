# ADR 0010: Innerer Wert als FCFF-DCF-Modellschätzung

- **Datum:** 2026-10-25
- **Status:** Fachvertrag für M8-01 (#70); Implementierung und Live-Abdeckung offen
- **Herkunft:** Epic #69; Modell- und Prüfregeln sind technische Festlegungen,
  keine vom Besitzer gewählten realen Finanzannahmen.

## Kontext und vorhandene Grundlage

`models.Valuation` (#12) bezeichnet einen beobachteten Stückpreis.
`instruments.VerifiedListing` (#16) verbindet ein geprüftes Wertpapier mit einem
Listing; `valuations` (#21) berechnet daraus EUR-Positionswerte mit expliziten
Einheiten und gerichtetem FX. Diese Module und ihre Verträge sind in
`agents/integration` bei `6b8154060468a6ba8f05743343213e59a1a22912` vorhanden.
Sie bleiben unverändert. Ein innerer Wert ist ein eigenständiges Modellresultat,
kein Kurs, Einstand oder persönlicher Ertrag. Fundamentaldaten enthalten keine
Bestandsmenge; dieselbe geprüfte Aktie verwendet in Portfolio und Watchlist
denselben Unternehmensdatenstand und dasselbe gewählte Szenario.

## Entscheidung

Wir spezifizieren einen nominalen FCFF-DCF in der Berichtswährung: fünf explizite
Jahre, Zahlung jeweils am Jahresende, danach einen nachhaltigen Restwert am Ende
von Jahr 5. Bewertungsstichtag ist t=0, ohne unterstelltes halbes erstes Jahr.
Cashflows zum Unternehmen werden mit nominalen, zur Währung passenden WACC
abgezinst. Zinssätze sind Bruchteile, nicht Prozentzahlen. Reale Eingaben benötigen
explizite Besitzerbestätigung; es gibt keine Standardsätze aus der Demo.

Die Pro-Aktie-Rechnung ist ausschließlich für Emittenten mit genau einer belegten
Stammaktienklasse definiert. Der gesamte Stammaktionärswert gehört damit derselben
einzigen Klasse wie die aktuelle verwässerte Stückzahl. Mehrere Stammaktienklassen
sind unabhängig von bestätigtem Mapping oder gleichen wirtschaftlichen Rechten
«Modell nicht unterstützt»; das MVP verteilt keinen Wert zwischen Klassen.

```text
NOPAT_t = EBIT_t × (1 − operativer_Steuersatz_t)
FCFF_t = NOPAT_t + D&A_t − Capex_t − Δoperatives_NWC_t
PV_t = FCFF_t / (1 + WACC)^t                         für t = 1..5
TV_5 = nachhaltiger_FCFF_6 / (WACC − g)
operativer_Unternehmenswert = Summe(PV_1..PV_5) + TV_5/(1+WACC)^5
Stammaktionärswert = operativer_Unternehmenswert
                    + überschüssige_Liquidität + nichtoperative_Vermögenswerte
                    − Finanzschulden − Vorzugsansprüche − Minderheitsansprüche
Wert_je_Aktie = Stammaktionärswert / aktuelle_verwässerte_Stückzahl
Terminalwertanteil = [TV_5/(1+WACC)^5] / operativer_Unternehmenswert
```

Generisches Provider-FCF oder operativer Cashflow minus Capex ist nur ein separat
beschrifteter Kontrollwert. Zinsen, Finanzierung und Steuerwirkungen darin können
FCFE statt FCFF abbilden. Kein solcher Wert wird ungeprüft mit WACC diskontiert
und anschließend erneut um Schulden korrigiert.

### Prognose, Reinvestition und Restwert

Jedes der fünf Jahre hat einen vollständigen Komponentenplan. Alternativ darf der
Besitzer ausdrücklich eine Fortschreibung aus normalisiertem EBIT/NOPAT und
Wachstumsannahmen wählen; auch dann werden D&A, Capex und ΔNWC je Jahr angegeben
oder aus ausdrücklich bestätigten Treibern hergeleitet. Ein bloßer FCFF-Basiswert
mal Wachstumsrate ohne Reinvestitionsplan ist kein zulässiger Berechnungspfad.

Für das nachhaltige Folgejahr sind NOPAT_6, ein nachhaltiger positiver ROIC und g
explizite Annahmen. Das MVP unterstützt nur `0 ≤ g < WACC`, `WACC > 0`,
`ROIC > g`, `0 < operativer Steuersatz < 1` oder explizit belegten Steuersatz 0.
`g=0` ist eine mögliche bestätigte Annahme, kein Ersatz für fehlendes Wachstum.
Es gilt:

```text
NOPAT_6 = NOPAT_5 × (1 + g)
Netto-Reinvestition_6 = NOPAT_6 × g / nachhaltiger_ROIC
nachhaltiger_FCFF_6 = NOPAT_6 − Netto-Reinvestition_6
                  = NOPAT_6 + D&A_6 − Capex_6 − ΔNWC_6
```

Der Komponentenplan für Jahr 6 muss dieselbe Netto-Reinvestition ergeben; bei
`g=0` muss er zumindest Ersatzinvestitionen (`Capex + ΔNWC = D&A`) abbilden.
Positive Wachstumsraten bei pauschal null Reinvestition werden abgelehnt.
Die nachhaltige Marge/Steuerquote und der Übergang aus Jahr 5 sind zu begründen;
FCFF_6 ist deshalb nicht pauschal `FCFF_5 × (1+g)`. Nachhaltiger ROIC ist eine
Zukunftsannahme und wird nicht unbemerkt aus einem ungeprüften Buchwert abgeleitet.

Drei einzeln bestätigte Pläne heißen vorsichtig, Basis und optimistisch. Labels
ersetzen keine Rechnung; Resultate werden nicht nachträglich umsortiert, um eine
gewünschte Reihenfolge zu erzeugen. Sensitivität variiert explizite WACC-/g-Werte,
rechnet FCFF_6 inklusive Reinvestition neu und kennzeichnet ungültige Zellen.
Ein optionaler persönlicher Sicherheitsabschlag `0 ≤ s < 1` erzeugt den getrennten
Zielpreis `Wert_je_Aktie × (1−s)`, keinen veränderten DCF-Wert. Fehlendes s bedeutet
kein Zielpreis. Szenariospannen sind keine statistischen Konfidenzintervalle und
keine Kauf-/Verkaufsurteile.

### Kapitalbrücke, Aktienzahl und Aktienvergütung

Brücke und Cashflows müssen denselben Konsolidierungskreis verwenden. Der
Unternehmenswert aus voll konsolidiertem EBIT enthält beispielsweise das Geschäft
von Minderheiten; deren Anspruch wird genau einmal abgezogen. Nichtoperative
Vermögenswerte und liquide Mittel dürfen weder im operativen Cashflow noch in
mehreren Brückenfeldern doppelt vorkommen. Restriktive/operative Liquidität ist
kein automatisch überschüssiges Cash. Finanzschulden umfassen auch vergleichbare
Leasingverbindlichkeiten, sofern EBIT/Cashflows konsistent mit dieser Behandlung
normalisiert sind. Inkonsistente Leasing-/Zinsabgrenzung sperrt die Bewertung.

Jeder Anspruch benötigt Herkunft, Stichtag, Bewertung und Abgrenzung. Belegte
Nullansprüche sind erlaubt; fehlend ist niemals null. Buchwerte als Näherung für
Marktwerte werden je Feld ausdrücklich bestätigt und am Ergebnis sichtbar
markiert. Unaufklärbare wesentliche Ansprüche sperren die Rechnung. Keine pauschale
Nettoschuld wird zusätzlich zu bereits enthaltenem Cash/Schulden abgezogen.

Vor der Division muss die vollständige Kapitalstruktur des Emittenten am aktuellen
Kapitalstand genau eine Stammaktienklasse belegen, einschließlich nicht gelisteter
Klassen. Bestätigte Instrument-/Klassen-Zuordnung allein belegt das nicht; die
Anzahl der Listings ist kein Ersatz. Mehr als eine Stammaktienklasse sperrt die
gesamte Bewertung als «Modell nicht unterstützt», auch bei identischen
wirtschaftlichen Rechten. Eine unbekannte Klassenanzahl sperrt als «Daten/Annahmen
unvollständig». Weder Summieren von Klassenstückzahlen noch eine geratene
Kapitalrechteallokation ist ein zulässiger Ausweg im MVP.

Der Nenner ist die aktuelle Stückzahl der zum Stichtag ausstehenden Stammaktien
dieser einzigen Klasse zuzüglich belegter inkrementeller Verwässerung in dieselbe
Klasse. Eigene Aktien sind aus ausstehenden Aktien ausgeschlossen.
Options-/Wandlungsrechte werden nur mit nachvollziehbarer, konsistenter Methode
berücksichtigt; andernfalls «Verwässerung ungeklärt». Ein konvertierter Anspruch
wird nicht zugleich als Schuld abgezogen. Historische gewichtete EPS-Aktienzahlen
sind Kontrollgrößen und kein stiller Ersatz. Splits zwischen Berichts-, Aktienzahl-
und Kursstichtag verlangen eine belegte gemeinsame Splitbasis.

Aktienvergütung bleibt als operativer Aufwand im EBIT; sie wird nicht als D&A
zurückaddiert. Enthält eine Provider-D&A-Summe Aktienvergütung, muss sie zuvor
getrennt werden. Bereits ausgegebene Ansprüche sind in der aktuellen Verwässerung
zu berücksichtigen. Künftige Vergütung darf nicht gleichzeitig als voller Aufwand
und zusätzliche pauschale zukünftige Verwässerung doppelt belastet werden;
ein anderer Ansatz benötigt eine neue dokumentierte Modellentscheidung.

### Eignung, Historie und Altersgrenzen

Das MVP unterstützt nur etablierte profitable Nichtfinanzunternehmen mit positivem
normalisiertem EBIT und FCFF in jedem der mindestens drei vollständigen,
aufeinanderfolgenden Geschäftsjahre. Einschließlich der Eröffnungsbilanz des ersten
Jahres müssen für ΔNWC vier passende Stichtage vorliegen. Ziel sind 5–10 Jahre
Historie; deren kostenlose Verfügbarkeit ist ausdrücklich nicht nachgewiesen.
TTM, verkürzte Geschäftsjahre und lückenhafte Quartalsketten ersetzen diesen
Mindestnachweis nicht. Restatements werden nur als konsistenter Berichtssatz
verwendet, keine Mischung verschiedener Fassungen.

Einmaleffekte werden nicht automatisch entfernt. Jede Normalisierung bewahrt den
Originalwert und braucht Quelle, Periode, Begründung und Besitzerbestätigung.
Zyklische Unternehmen benötigen zusätzlich einen belegten vollständigen Zyklus
und eine bestätigte Normalisierung; fehlt dies, lautet der Status «Daten/Annahmen
unvollständig». Der Code rät weder Zykluslänge noch normalisierten Gewinn.

Technische, konservative Sperrregeln dieses MVP (keine Aussage zur gesetzlichen
Publikationsfrist): jüngster Jahresabschluss und Brücken-Bilanzstichtag höchstens
550 Tage vor Bewertungsstichtag; aktuelle Aktienzahl höchstens 120 Tage alt.
Zusätzlich sind bekannte spätere Kapitalmaßnahmen/Claims einzuarbeiten. Alter wird
in verstrichenen UTC-Tagen gemessen; exakt an der Grenze ist zulässig. Ein jüngerer
Abruf ändert das fachliche Alter nicht. Fehlende Stichtage oder Zukunftsdatierungen
sperren. Unbekanntes Veröffentlichungsdatum bleibt unbekannt und als Hinweis
sichtbar; eine historische Point-in-time-Behauptung ist dann unzulässig. Bekannte
Veröffentlichung nach Bewertungsstichtag sperrt die Verwendung für diesen Stichtag.
Preis-/FX-Alter wird separat in M8-09 behandelt und kann den Kursvergleich sperren,
ohne einen gültigen DCF-Wert in Berichtswährung zu löschen.

Banken, Versicherer, REITs, ETFs, Derivate, ADRs, Emittenten mit mehreren
Stammaktienklassen und nicht eindeutig zuordenbare Anteilsklassen erhalten
«Modell nicht unterstützt». Ein bestätigtes Mapping hebt diese Modellgrenzen nicht
auf. Ungeprüfte Instrument-/Emittenten-
Zuordnung, unbekannte Währung/Skala, unvereinbare Perioden, unbestätigte reale
Annahmen, NaN/Infinity, nichtpositive nachhaltige Cashflows oder Stückzahl sowie
nichtpositiver Stammaktionärswert liefern keine scheinbar attraktive Pro-Aktie-Zahl.
Der letzte Fall wird als «kein positiver Stammaktionärswert» mit der rechnerischen
Brücke erklärt. Mehrere Sperrgründe bleiben gemeinsam sichtbar.

## Alternativen und Konsequenzen

- FCFE mit Eigenkapitalkosten wäre ein anderes Modell und wird nicht vermischt.
- Multiples/Analystenziele sind kein Ersatz für einen fehlenden DCF-Vertrag.
- Automatisch geratene Normalisierung oder ein kostenpflichtiger Fallback werden
  ausgeschlossen. Manuelle belegte Ergänzung bleibt möglich.

Konstante WACC, stabile Terminalmarge/ROIC und eine vereinfachte operative
Steuerquote sind Modellnäherungen. Verlustvorträge, komplexe Steuerregime,
komplexe Kapitalrechte und wesentliche Umstrukturierungen benötigen fachliche
Klärung statt stiller Standards. Hoher Terminalwertanteil bleibt sichtbar; ab
75 % zeigt die spätere UI zusätzlich einen Dominanzhinweis. Diese technische
Hinweisschwelle ändert keine Rechnung und behauptet keine statistische Sicherheit.

Der [Feldvertrag und das synthetische Rechenbeispiel](../INTRINSIC_VALUE.md)
konkretisieren diesen ADR. M8-02 (#71) muss tatsächliche kostenlose Datenabdeckung
separat nachweisen; M8-03 bis M8-09 implementieren Verträge/Rechnung. Keine reale
Finanzannahme, Provider-Abdeckung oder komplette Epic-Abnahme folgt aus diesem ADR.

## Fachliche Referenzen

- [Damodaran: Value introduction](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/background/valintro.htm)
- [Damodaran: Valuation lectures](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/lectures/val.html)

Die Links sind methodische Referenzen aus #70, kein in dieser Aufgabe erneut
geprüfter Nachweis einer Anbieterabdeckung.
