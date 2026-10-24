# Innerer Wert: Feldvertrag und synthetische Referenzrechnung

M8-01 (#70), fachliche Spezifikation zu [ADR 0010](adr/0010-innerer-wert-fcff.md).
Dies beschreibt den Vertrag für spätere Implementierungen, keinen vorhandenen
DCF-Rechner, Live-Adapter oder nachgewiesenen kostenlosen Datenumfang.

## Getrennte Ebenen und Status

1. **Beobachtung:** unveränderte Geschäftszahl mit Felddefinition und Provenienz.
2. **Manuelle Ergänzung/Normalisierung:** eigener Datensatz, Originalwert bleibt
   erhalten; Feld, Quelle, Berichtsperiode, Begründung, Änderungszeitpunkt und
   ausdrückliche Bestätigung erforderlich. Kein Überschreiben der Beobachtung.
3. **Prognose:** vom Besitzer bestätigte, versionierte Annahmen für genau den
   geprüften Instrument-/Emittenten- und Berichtssatz. Neue Berichte erfordern
   erneute Prüfung der Verknüpfung; keine unbemerkte Wiederverwendung.
4. **Ergebnis:** unveränderlicher Verweis auf Eingabe-/Annahmenversion,
   Bewertungsstichtag, Modellversion, Rechenschritte und alle Qualitätsmerkmale.

Ein endlicher Zahlenwert 0 ist belegt und zulässig, wenn die Feldregel es erlaubt.
Fehlend (`None`) und ungültig sind eigene Zustände ohne nutzbaren Zahlenwert;
NaN/Infinity werden nie zu null. Unvollständig bezeichnet einen Satz mit fehlenden
Pflichtfeldern, veraltet einen fachlich zu alten Stand, manuell ergänzt dessen
Herkunft. Unbestätigte Annahmen und Modell nicht unterstützt sind eigene Gründe.
Diese Merkmale können gleichzeitig vorliegen. Ergebniswert fehlt, sobald eine
Pflichtbedingung scheitert; Quellen/Fehlergründe bleiben erhalten. Ein unpassender
Kurs sperrt den Vergleich, nicht automatisch den vorhandenen DCF in Berichtswährung.

## Identität, Zeitraum und Herkunft je Feld

Alle Geldfelder tragen eine explizite ISO-Berichtswährung und positive
`scale_to_currency` (z. B. 1000000 für Millionen) zur Währungshaupteinheit.
Stückzahlen tragen analog ihren expliziten Skalierungsfaktor, keine Geldwährung.
Raten sind dimensionslose Bruchteile; unbekannte Einheit ist ein Sperrgrund.
Vor der Rechnung werden die Skalen nachvollziehbar normalisiert. Handelswährung,
Pence-Einheit und gerichtetes FX sind separate Größen für M8-09, keine
Fundamental-Skalierung. Keine Währung wird als EUR angenommen.

| Metadatum | Definition / Einheit / Zeitraum | Herkunft und Pflichtregel |
| --- | --- | --- |
| Identitätskette | geprüfte Instrument-ID → Anteilsklasse → Emittent → Quelle; inkl. Konsolidierungskreis | Explizit geprüfte Registry; alle Glieder Pflicht. Gleiche Namen/Ticker reichen nicht. Unterschiedliche Listings dürfen nur für dieselbe bestätigte Aktie teilen; ADR/andere Klasse nie automatisch. |
| Berichtssatz-ID / Fassung | zusammengehörige GuV, Cashflow, Bilanz und vergleichbare Vorjahre | Bericht/Restatement, Pflicht; keine automatische Kombination alter und neuer Felder. |
| Feldherkunft | Providerfeld oder manuelle Belegreferenz, Rohdefinition, explizite Vorzeichen-/Skalennormalisierung | Je vorhandenem Feld Pflicht; private Belege/Identitäten nur in geschützter Laufzeit, nie in Code/Logs/GitHub. |
| Flussperiode | Beginn/Ende inklusive, Typ Geschäftsjahr; bekanntes Geschäftsjahreslabel | Je EBIT/Steuer/D&A/Capex/ΔNWC Pflicht; Beginn ≤ Ende; drei vollständige aufeinanderfolgende Jahre. Quartal, YTD, TTM getrennt kennzeichnen, nicht summierend mischen. |
| Bilanzstichtag | Eröffnungs-/Schlussdatum, kein summierbarer Fluss | Je NWC-Komponente/Brückenfeld Pflicht. ΔNWC nutzt genau zwei zum jeweiligen Geschäftsjahr passende Stichtage. |
| Aktienzahl-Stichtag | Datum und gemeinsame Splitbasis, Kennzeichnung aktuell/gewichtet | Bei aktueller Anzahl Pflicht; historische EPS-Anzahl bleibt separat und optional. |
| Berichts-/Publikationsdatum | Tatsächliches Dokument-/Veröffentlichungsdatum, soweit bekannt | Jeweils unbekannt zulässig und ausdrücklich sichtbar; Abruf ersetzt keines der beiden. Bekannte Publikation nach Bewertungsstichtag sperrt rückwirkende Verwendung. |
| retrieved_at | tatsächlicher Abrufzeitpunkt, zeitzonenbewusst | Für Providerbeobachtung Pflicht; bei manuellem Beleg darf unbekannt bleiben, Änderungszeitpunkt ist separat Pflicht. |
| Normalisierung/Bestätigung | Originalreferenz, neue Zahl, Grund, Quelle, Periode, Bestätigungszeitpunkt | Für jede manuelle Änderung Pflicht; ohne Bestätigung bleibt die Rechnung gesperrt. |
| Bewertungs-/Annahmenversion | t=0, Modellversion, ausgewählte Berichts-/Eingabeversion und bestätigter Szenarioplan | Je Resultat Pflicht; keine implizite Systemuhr oder automatische Übernahme neuer Daten. |

Bekannte Zeitpunkte sind zeitzonenbewusst, reine Berichtsperioden sind Datumswerte.
Zukunftsdatierte Beobachtungen sind ungültig; Prognosejahre liegen ausdrücklich
nach t=0 und werden nicht als beobachtete Historie etikettiert.

## Feldmatrix der historischen Basis und Kapitalbrücke

P = Pflicht; O = optional. P kann bei Datenlücken durch eine belegte, bestätigte
manuelle Ergänzung erfüllt werden; nie durch geratenes null. Jede Zeile übernimmt
die zugehörige Provenienz und Perioden-/Einheitenpflicht aus obiger Matrix.

| Feld | Definition | Einheit / Vorzeichen | Zeitraum | Herkunft / Pflicht und Sperrgrund |
| --- | --- | --- | --- | --- |
| EBIT | normalisierter operativer Gewinn vor Zinsen/Steuern; Aktienvergütung bleibt Aufwand | Geld; im MVP > 0 | je der ≥3 Jahresperioden | GuV plus dokumentierte Anpassung, P; fehlt/≤0 → keine geeignete Basis |
| operativer Steuersatz | erwartete Steuer auf operativen Gewinn, ohne Finanzierungs-Steuervorteil | Bruchteil, `0 ≤ T < 1` | historisch je Jahr, zukünftig je Prognosejahr | belegte Steuerabgrenzung bzw. bestätigte Annahme, P; nicht aus ungeprüfter Konzernsteuerquote raten |
| D&A | operative Abschreibungen/Amortisation; ohne Aktienvergütung und doppelt entfernte Einmaleffekte | Geld ≥ 0 | je Jahresperiode | Cashflow/Anhang, P; gemischte nicht trennbare Summe → unvollständig |
| Capex | operative Investitionsauszahlungen; Provider-Negativausgabe explizit in positiven Abzug umkehren | Geld ≥ 0 | je Jahresperiode | Cashflow/Anhang, P; negatives Normalisierungsresultat oder unklare Abgrenzung → ungültig |
| operatives NWC Anfang/Ende | nichtzahlungswirksame operative Umlaufaktiva minus nichtverzinsliche operative kurzfristige Verbindlichkeiten; ohne Cash/Finanzschulden | Geld, auch negativ möglich | passende zwei Bilanzstichtage je Jahr, insgesamt ≥4 | Bilanz-Komponenten/Beleg, P; fehlt ein Stichtag → ΔNWC unvollständig |
| Δoperatives NWC | Schluss minus Anfang desselben definierten Konsolidierungskreises; Zunahme bindet Geld | Geld, positives Vorzeichen wird abgezogen; negative Freisetzung zulässig | genau Jahresperiode | aus passenden NWC-Ständen oder belegter vollständig abgegrenzter Veränderung, P; Periodenmischung sperrt |
| historischer FCFF | EBIT×(1−T)+D&A−Capex−ΔNWC | Geld; im MVP jedes Mindesthistorienjahr > 0 | je Jahr | abgeleitet, P; nicht generisches Provider-FCF übernehmen |
| Provider-FCF / EPS-Aktienzahl | Kontrollgrößen mit eigener Definition | Geld / Stück | jeweilige Originalperiode | O; kein Ersatz für FCFF/aktuelle Aktienzahl |
| überschüssige liquide Mittel | frei verfügbare Liquidität abzüglich explizit erforderlicher operativer/gebundener Mittel | Geld ≥ 0, addieren | Brückenstichtag | Bilanz/Anhang plus bestätigte Abgrenzung, P auch bei belegter 0 |
| nichtoperative Vermögenswerte | nicht im operativen Wert enthaltene, der betrachteten Gruppe zurechenbare Anlagen | Geld ≥ 0, addieren | Brückenstichtag | belegte Bewertung, P auch bei 0; Doppelzählung mit Cash/operativem Ergebnis sperrt |
| Finanzschulden | verzinsliche Ansprüche inkl. konsistent behandelter Leases | Geld ≥ 0, abziehen | Brückenstichtag | Bilanz/Anhang, P auch bei 0; Nettoschuld nicht zusätzlich abziehen |
| Vorzugs-/Minderheitsansprüche | dem betrachteten Stammkapital vor-/nachgelagerte fremde Ansprüche am selben Unternehmenswert | Geld ≥ 0, je einmal abziehen | Brückenstichtag | Bilanz/Anhang und bestätigte Bewertungsmethode, beide P auch bei 0 |
| aktuelle ausstehende Aktien | aktuelle Stammaktien dieser Klasse ohne eigene Aktien, splitbereinigt | Stück > 0 | Aktienzahl-Stichtag | belegte aktuelle Anzahl, P; kein historischer gewichteter EPS-Ersatz |
| inkrementelle Verwässerung | konsistent bewertete zusätzliche Ansprüche aus bestehenden Instrumenten; keine doppelte Schuldlast | Stück ≥ 0 | derselbe aktuelle Kapitalstand | bestätigte Methode/Beleg, P auch bei 0; ungeklärte Rechte sperren |
| aktuelle verwässerte Stückzahl | ausstehende Aktien plus inkrementelle Verwässerung | Stück > 0 | gemeinsame Splitbasis | abgeleitet, P; nicht auf unzuordenbare Anteilsklassen verteilen |

Jüngster vollständiger Jahresabschluss/Brückenstichtag maximal 550 Tage alt,
Aktienzahl maximal 120 Tage alt, wie in ADR 0010 festgelegt. Bekannte spätere
Kapitalmaßnahmen sind trotz Altersgrenze einzubeziehen. Buchwertnäherungen und
unbekannte Publikationsdaten bleiben sichtbare Hinweise am Resultat. Jede als
wesentlich erkannte ungeklärte Abgrenzung blockiert; es gibt keinen automatischen
Prozentsatz für «unwesentlich», der fehlende Ansprüche auf null setzt.

## Prognose- und Ergebnisfelder

| Feld | Definition / Einheit / Vorzeichen / Zeitraum | Herkunft / Pflicht und Sperrgrund |
| --- | --- | --- |
| EBIT, T, D&A, Capex, ΔNWC je Jahr 1..5 | dieselben Komponenten/Einheiten/Vorzeichen wie oben, vollständiger Zukunftsplan | P je Szenario; Besitzer bestätigt direkt oder nachvollziehbare Wachstumstreiber samt Reinvestition. Fehlend/unbestätigt sperrt. |
| WACC | nominaler Kapitalkostensatz in Berichtswährung; Bruchteil > 0, konstant für Jahre 1..∞ | P je Szenario, Besitzerannahme, kein Demo-Default |
| g / nachhaltiger ROIC | langfristiges Wachstum / operative Kapitalrendite als Bruchteile; `0 ≤ g < WACC`, `ROIC > g` | beide P je Szenario; ungültige Differenz/Rate sperrt |
| NOPAT_6, D&A_6, Capex_6, ΔNWC_6 | expliziter nachhaltiger Folgejahresplan, wie ADR-Gleichungen; FCFF_6 > 0 | P, bestätigte Annahmen; Komponenten müssen Netto-Reinvestition NOPAT_6×g/ROIC ergeben |
| Sicherheitsabschlag s | persönlicher Abschlag als Bruchteil `0 ≤ s < 1` | O; fehlt → Zielpreis fehlt, DCF unverändert |
| PV_1..5 / TV_5 / PV(TV_5) | abgezinste Komponenten und Restwert in Geld | abgeleitet, mit vollständiger Eingabeversion; keine Zwischenrundung |
| operativer Wert / Stammaktionärswert | Geld in Berichtswährung, letzterer nach expliziter Kapitalbrücke | abgeleitet; nichtpositiver Stammaktionärswert → kein positiver Pro-Aktie-Modellwert |
| Wert je Aktie / optionaler Zielpreis | Berichtswährungs-Haupteinheiten je aktueller verwässerter Aktie | abgeleitet; keine Menge/Einstand/ADR-Konversion |
| Terminalwertanteil | PV(TV_5)/operativer Unternehmenswert als Bruchteil | abgeleitet, anzeigen; ab 0,75 zusätzlicher Dominanzhinweis |
| Status / Quellen / Alter | alle Eingabehinweise, Sperrgründe, Modell- und Annahmenversionen | P am Resultat und späteren Kursvergleich; ein neuer Abruf entfernt fachliche Alterswarnungen nicht |

Intern endliche Decimal-Werte mit mindestens 34 signifikanten Stellen, keine
Float-Konvertierung und keine Cent-Zwischenrundung. Über-/Unterlauf ist ein
Berechnungsfehler, kein null/Unendlich-Resultat. Nur die Darstellung rundet
`ROUND_HALF_EVEN`: Geld je Aktie auf 2 Nachkommastellen, Prozentanzeigen auf 2.
Ausgangswerte und ungerundete Rechenschritte bleiben für den Rechennachweis
verfügbar; Rundungsdifferenzen dürfen nicht in Folgegrößen zurückfließen.

## Vollständig erfundenes Zahlenbeispiel

Alle folgenden Zahlen, Identitäten und Annahmen sind synthetisch, keine
Vorgabewerte für echte Aktien. Eine erfundene geeignete einzige Stammaktienklasse
in EUR, keine ADR, keine unbekannten Ansprüche. Geldtabellen in **Millionen EUR**,
Aktienzahl in **Millionen Stück**, weshalb der Quotient EUR je Aktie ergibt.
Bewertungsstichtag 01.01.2026, aktuelle Aktienzahl/Brücke 31.12.2025, keine
zwischenzeitlichen Kapitalmaßnahmen. Drei konsistente, vollständig veröffentlichte
Jahresberichte 2022–2024 mit jüngstem Ende 31.12.2024 (Alter 366 Tage); Datum und
Herkunft seien ausdrücklich als synthetische Nachweise bestätigt. Dies belegt keine
reale Providerverfügbarkeit.

Historische normalisierte Basis (je 01.01.–31.12., T=20 %):

| Jahr | EBIT | D&A | Capex | NWC Anfang → Ende | ΔNWC | NOPAT | FCFF |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2022 | 130 | 20 | 44 | 100 → 116 | 16 | 104 | 64 |
| 2023 | 140 | 20 | 44 | 116 → 132 | 16 | 112 | 72 |
| 2024 | 150 | 20 | 44 | 132 → 148 | 16 | 120 | 80 |

Alle drei Szenarien verwenden in diesem Beispiel zur übersichtlichen Kontrolle
denselben explizit angenommenen Fünfjahresplan, nicht eine automatische Ableitung
aus der Historie. Keine Behauptung über den noch nicht veröffentlichten Jahreswert
2025. T=20 %, D&A=20, Capex=48 und ΔNWC=20 je Prognosejahr:

| t | EBIT | NOPAT | FCFF = NOPAT + 20 − 48 − 20 | PV bei WACC 10 % |
| --- | ---: | ---: | ---: | ---: |
| 1 | 160 | 128 | 80 | 72,727273 |
| 2 | 170 | 136 | 88 | 72,727273 |
| 3 | 180 | 144 | 96 | 72,126221 |
| 4 | 190 | 152 | 104 | 71,033399 |
| 5 | 200 | 160 | 112 | 69,543188 |

Summe der fünf PVs im Basisszenario = **358,157353881690**. Nachhaltiger ROIC
in jedem Szenario ausdrücklich 10 %. Im Jahr 6 sind D&A=20 und ΔNWC=0 angenommen;
Capex ist `20 + Netto-Reinvestition_6`. Die stilisierte Umstellung auf stabiles NWC
und nachhaltige Reinvestition ist eine ausdrückliche Beispielannahme, kein
allgemeines wirtschaftliches Versprechen. Basis: NOPAT_6=160×1,02=163,20;
Reinvestition_6=163,20×0,02/0,10=32,64; Capex_6=52,64;
FCFF_6=163,20+20−52,64−0=**130,56**.

Basis-Terminalwert: 130,56/(0,10−0,02)=**1632** am Ende von Jahr 5;
PV(TV)=1632/1,1^5=**1013,343599232541**. Operativer Wert =
**1371,500953114231**. Gemeinsame, vollständig belegte synthetische Brücke:
überschüssiges Cash 100 + nichtoperative Anlagen 20 − Schulden 200 − Vorzug 10 −
Minderheiten 5 = **−95**. Alle fünf Ansprüche sind explizit bekannt, nicht geraten.
Aktuelle 9,5 Mio. ausstehende Aktien + 0,5 Mio. inkrementelle Verwässerung =
10 Mio. aktuelle verwässerte Aktien. Keine Aktienvergütung in D&A, kein doppelt
abgezogener wandelbarer Anspruch. Stammaktionärswert = **1276,500953114231**;
Modellwert je Aktie = **127,650095311423 EUR**, angezeigt **127,65 EUR**.

| Synthetisches Szenario | WACC | g | NOPAT_6 | Reinvestition_6 | FCFF_6 | TV_5 | PV(TV_5) | operativer Wert | EUR/Aktie | TV-Anteil |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| vorsichtig | 12 % | 1 % | 161,60 | 16,16 | 145,44 | 1322,181818 | 750,241472 | 1089,799696 | 99,479970 | 68,842144 % |
| Basis | 10 % | 2 % | 163,20 | 32,64 | 130,56 | 1632,000000 | 1013,343599 | 1371,500953 | 127,650095 | 73,885738 % |
| optimistisch | 8 % | 3 % | 164,80 | 49,44 | 115,36 | 2307,200000 | 1570,241552 | 1948,637760 | 185,363776 | 80,581501 % |

Die höhere nachhaltige Wachstumsannahme erhöht hier auch die Reinvestition und
senkt FCFF_6; trotzdem steigt wegen anderer WACC/g der Modellwert. Die
optimistische Spalte trägt den Terminaldominanzhinweis. Bei ausdrücklich gewähltem
synthetischem s=20 % beträgt der Basis-Zielpreis 102,120076249138 EUR, angezeigt
102,12 EUR. Der DCF-Modellwert bleibt 127,650095311423 EUR.

### Reproduzierbare Referenzrechnung ohne App-/Provider-Abhängigkeit

Aus dem Repository per `uv run --locked python` ausführen; das ist eine
Standardbibliotheks-Referenz, kein Aufruf des späteren Rechners:

```python
from decimal import Decimal as D, localcontext

with localcontext() as ctx:
    ctx.prec = 40
    for w, g, expected in [
        (D('.12'), D('.01'), D('99.479969621694')),
        (D('.10'), D('.02'), D('127.650095311423')),
        (D('.08'), D('.03'), D('185.363776035345')),
    ]:
        forecast = [D(x) * D('.8') + D(20) - D(48) - D(20)
                    for x in (160, 170, 180, 190, 200)]
        pv = sum(f / (1+w)**t for t, f in enumerate(forecast, 1))
        nopat6 = D(160) * (1+g)
        reinvest6 = nopat6 * g / D('.10')
        fcff6 = nopat6 - reinvest6
        tv = fcff6 / (w-g)
        pv_tv = tv / (1+w)**5
        ev = pv + pv_tv
        equity = ev + D(100) + D(20) - D(200) - D(10) - D(5)
        per_share = equity / D(10)
        assert abs(per_share - expected) < D('.000000000001')
        assert D(0) < pv_tv / ev < D(1)
        print(w, g, fcff6, tv, pv_tv, ev, per_share, pv_tv/ev*100)
```

Prüfung: Zahlen unabhängig aus Komponenten und Diskontfaktoren nachrechnen, nicht
nur gerundete Tabellenwerte addieren. Der spätere Rechner muss diese Ergebnisse
mit synthetischen Tests sowie Fehlerfällen für Identität, Einheiten, Perioden,
Alter und fehlende Annahmen belegen. Keine Aussage über getestete Live-Daten,
Kaufentscheidungen oder den Abschluss von #69/#71.
