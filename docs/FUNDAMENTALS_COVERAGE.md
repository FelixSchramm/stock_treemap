# Abdeckungsprüfung kostenloser Fundamentaldaten (yfinance)

M8-02 ([#71](https://github.com/FelixSchramm/stock_treemap/issues/71)), Stand
**29.10.2026**. Der Bericht prüft, welche Felder des
[Feldvertrags](INTRINSIC_VALUE.md) yfinance für ein vom Besitzer privat
bestätigtes Testuniversum liefert. Er enthält ausschließlich aggregierte
Fallzahlen, Datenarten und Problemklassen: keine Namen, Symbole, ISINs,
Unternehmenszahlen oder Rohantworten. Der Detailbericht je Instrument liegt nur
in ignorierten privaten Dateien.

Das Ergebnis gilt nur für diese Stichprobe, diesen Tag und die geprüfte
Paketversion. Yahoo-Zugriff über yfinance ist inoffiziell; Umfang, Bedingungen,
Drosselung und Kostenfreiheit können sich ohne Ankündigung ändern. Es gibt keine
Aussage zu 100 % Abdeckung und keine Zusage dauerhafter Kostenfreiheit.

## Verfahren

- **Paket:** `yfinance==1.7.0` (gesperrt in `pyproject.toml` und `uv.lock`).
- **Freigabe:** Der Besitzer gab am 29.10.2026 einen begrenzten, nur lesenden
  Live-Abruf für ein privates Testuniversum frei und bestätigte die
  Instrumentzuordnungen. Kauf, Abo und Bezahlzugang waren nicht beteiligt.
- **Werkzeug:** `scripts/fundamentals_coverage_probe.py` (manuell, nie in CI, nicht
  von `app.py` importiert) ruft je Instrument Jahres- und Quartals-GuV, -Bilanz und
  -Cashflow, Metadaten (`info`), die Aktienzahl-Zeitreihe und Ergebnistermine mit
  Pausen ab. Die reine Auswertelogik (`fundamentals_coverage`) wird offline mit
  ausschließlich erfundenen Frames getestet; `--confirm-live` und ein
  Ausgabepfad unterhalb eines `private`-Ordners sind Pflicht, die Standardausgabe
  zeigt nur Zählwerte.
- **Regeln der Auswertung:** Fehlende Zeile und NaN sind *fehlend*, nie 0; ein
  ausdrücklich gelieferter Wert 0 zählt als geliefert. **OK** heißt: Wert für die drei
  jüngsten Geschäftsjahre bzw. alle vier Bilanzstichtage vorhanden. Es heißt nicht,
  dass der Wert fachlich normalisiert oder für den Vertrag richtig abgegrenzt ist.
- **Fälle:** 10 Aktien (Kapitalgesellschaften, Nichtfinanzunternehmen, jeweils nur
  eine Stammaktienklasse, laut Besitzerbestätigung). Läufe: ein Erstlauf und ein
  Wiederholungslauf nach Erweiterung des Werkzeugs (alle Prüfungen, die beide Läufe
  enthielten, ergaben identische Zählwerte), dazu wenige Diagnoseabrufe; bei 6 Fällen
  mussten gedrosselte Metadaten nach Wartezeit erneut gelesen werden.
- **Plausibilisierung:** 3 der 10 Fälle, je Umsatz, EBIT und Capex des letzten
  Geschäftsjahrs (8 Werte; ein Capex-Wert ohne Vergleichsquelle) gegen Originalberichte bzw.
  veröffentlichte Ergebnismitteilungen. Quellen und Einzelwerte bleiben privat.

## Abgedeckte und nicht abgedeckte Varianten

| Variante | Fälle | Status |
| --- | ---: | --- |
| US-Listing, USD, Berichtswährung USD | 3 | geprüft |
| Xetra, EUR, Berichtswährung EUR | 6 | geprüft |
| Hongkong, HKD, **abweichende** Berichtswährung | 1 | geprüft (einziger Fall dieser Klasse) |
| davon Geschäftsjahr nicht Kalenderjahr | 4 | geprüft |

**Ungeprüft** (nicht vorhanden im Testuniversum, daher nicht behauptet):
Subeinheiten wie Pence/Cent (`GBp`, `ZAc`, `ILA`), ADRs, weitere Börsen und
Währungen, Banken/Versicherer/REITs, Unternehmen mit mehreren Stammaktienklassen,
Verlustunternehmen als Regelfall, Small Caps, Restatements, 52/53-Wochen-Kalender
mit stärkerer Abweichung, sowie jedes Instrument außerhalb dieser 10 Fälle.
Für die Hongkong-Klasse ist die Berichtswährung ein Einzelbeleg, kein Muster.

## Pflichtfeldabdeckung (Fälle von 10)

| Vertragsfeld | OK | teilweise | fehlt | Anmerkung |
| --- | ---: | ---: | ---: | --- |
| EBIT / operatives Ergebnis (`Operating Income`) | 10 | 0 | 0 | 1 Fall mit nichtpositivem EBIT; Abgrenzung weicht vom berichteten Betriebsergebnis ab (s. u.) |
| Steuern und Vorsteuerergebnis | 10 | 0 | 0 | liefert nur die *effektive* Quote, keinen operativen Steuersatz |
| D&A | 10 | 0 | 0 | |
| Capex | 9 | 0 | 1 | im fehlenden Fall drei Jahre leer, Provider-FCF dort gleich operativem Cashflow |
| NWC-Komponenten, 4 Bilanzstichtage | 10 | 0 | 0 | Komponenten einzeln vorhanden; Ableitung im Adapter nötig |
| Liquide Mittel (brutto) | 10 | 0 | 0 | kein *überschüssiges* Cash |
| Finanzschulden inkl. Leasing (`Total Debt`) | 10 | 0 | 0 | Summenidentität mit kurz- und langfristigen Posten inkl. Leasing in 10 von 10 |
| Minderheitsansprüche | 8 | 0 | 2 | Zeile fehlt (nicht als 0 gewertet) |
| Vorzugsansprüche | 2 | 0 | 8 | Zeile fehlt (nicht als 0 gewertet) |
| Aktuelle Aktienzahl mit Stichtag ≤ 120 Tage | 8 | 2 | 0 | 2 Fälle: datierte Reihe veraltet; Zähler im Metadatensatz undatiert |

Weitere Prüfungen: Handelswährung und Instrumenttyp laut Metadaten wie bestätigt
10/10; Berichtswährung wie bestätigt 10/10 (davon 1 abweichend von der
Handelswährung); Jahreshistorie 10/10 lückenlos mit 4 bis 5 Jahren (Ziel 5–10 Jahre
daher nicht durchgängig erreicht); Quartalshistorie mit mindestens vier
lückenlosen Quartalen in allen drei Abschlüssen 6/10 (4 teilweise: Lücken oder leere
Quartalsabschlüsse); Periodenenden passen zum bestätigten Geschäftsjahresende
10/10; jüngster Jahresabschluss höchstens 550 Tage alt 10/10 (8 mit neuerer
Zwischenbilanz); Capex-Vorzeichen einheitlich negativ (Abfluss) 9/9 vorhandene, mit
bestandener Kontrolle gegen Provider-FCF; Skalen-/Einheitenkontrolle über
EPS = Ergebnis / Aktienzahl 10/10; **Veröffentlichungsdatum je Bericht 0/10**
(nur Ergebnistermine als Näherung in 9 Fällen, kein Berichtsfeld).
Eine informelle Näherung des historischen FCFF aus diesen Feldern ergab in 6 von 10
Fällen drei positive Jahre, in 2 mindestens ein Jahr ≤ 0, in 2 war sie nicht
ableitbar; das ist keine Modellprüfung.

## Problemklassen

1. **Fehlende Zeile statt Wert 0** (Minderheit, Vorzug): nicht als 0 interpretieren.
2. **Stillschweigend fehlendes Capex** mit Provider-FCF gleich operativem Cashflow:
   Provider-FCF ist als Kontrollgröße unzuverlässig.
3. **EBIT-Abgrenzung:** In den plausibilisierten Fällen stimmten 6 von 8 Werten
   innerhalb 1 % mit Originalquellen überein; 2 EBIT-Werte wichen um 2–3 % vom
   berichteten Betriebsergebnis ab. Provider-EBIT ist nicht automatisch das
   normalisierte EBIT des Vertrags.
4. **Steuer:** nur effektive Quote aus Steueraufwand und Vorsteuerergebnis.
5. **Aktienzahl:** Zähler ohne Stichtag oder Zeitreihe älter als 120 Tage; keine
   Aussage zu Aktienklassen und keine inkrementelle Verwässerung.
6. **Nicht geliefert:** Veröffentlichungsdatum je Bericht, Berichtssatz-/Restatement-ID,
   überschüssiges Cash, nichtoperative Vermögenswerte, operativer Steuersatz.
7. **Periodenlücken:** Quartalsabschlüsse leer, halbjährlich oder lückenhaft; Jahresreihen
   nur 4–5 Jahre; Spalten ohne Werte kommen vor.
8. **Drosselung:** Der Metadatenabruf lieferte bei 6 Fällen zeitweise einen
   Platzhalter ohne Typ und Währung, ohne Fehler zu melden. Er muss als fehlend
   erkannt werden und darf keine Zahlen ersetzen.
9. **Währung:** Berichts- und Handelswährung können verschieden sein (1 Fall);
   Umrechnung bleibt M8-09.

## Entscheidung je Feldklasse

- **Automatische Versorgung (mit Plausibilitäts- und Besitzerprüfung):** EBIT-Basis
  (Abgrenzung sichtbar und bestätigt), D&A, Capex (Vorzeichen umkehren), NWC-Komponenten,
  Bruttoliquidität, Finanzschulden inkl. Leasing, Berichtswährung, Perioden und
  aktuelle Aktienzahl mit belegtem Stichtag.
- **Manuelle Ergänzung nötig:** Minderheit und Vorzug (ausdrückliche Bestätigung
  auch für 0), überschüssiges Cash und nichtoperative Vermögenswerte, operativer
  Steuersatz, inkrementelle Verwässerung, Anzahl der Stammaktienklassen,
  Veröffentlichungsdatum, fehlendes Capex, undatierte Aktienzahl.
- **Derzeit keine Bewertung:** Instrumente mit fehlender Pflichtgröße ohne bestätigte
  Ergänzung, nichtpositivem EBIT oder FCFF, nicht belegter Währung/Skala sowie die
  bereits ausgeschlossenen Modellklassen. In dieser Stichprobe wären das mindestens
  die Fälle mit fehlendem Capex und nichtpositivem EBIT, ohne Ergänzung also 2 von 10.

Die erste Version bleibt damit auch bei Teilabdeckung nutzbar: Lücken führen zu
«Bewertung nicht verfügbar» oder zu belegter manueller Pflege, nicht zu 0.

## Anforderungen an M8-03/M8-04 (#72/#73)

1. Providerzeilen als feste, geprüfte Präferenzliste lesen (z. B. `Operating Income`,
   `Depreciation And Amortization`, `Capital Expenditure`, `Total Debt`); anderes EBIT
   (`EBIT`) nur als Kontrollgröße mit Hinweis «Definition weicht ab».
2. Capex-Vorzeichen ausdrücklich normalisieren (Provider: negativ = Abfluss) und
   fehlende Werte nie als 0 behandeln; Provider-FCF nie als FCFF übernehmen und bei
   fehlendem Capex und FCF gleich operativem Cashflow als Qualitätsmerkmal melden.
3. Operatives NWC je Bilanzstichtag aus Umlaufvermögen, Liquidität, kurzfristigen
   Verbindlichkeiten und kurzfristigen Schulden bilden; vier Stichtage Pflicht, eine
   fehlende Komponente macht ΔNWC unvollständig.
4. Minderheit, Vorzug, überschüssiges Cash, nichtoperative Vermögenswerte und
   inkrementelle Verwässerung als manuell zu bestätigende Pflichtfelder modellieren;
   eine fehlende Providerzeile ergibt «fehlend», nie 0.
5. Aktienzahl nur mit Stichtag übernehmen; Zähler ohne Datum oder älter als 120 Tage
   ist «unvollständig» bzw. manuell; widersprüchliche Quellen (> 10 %) sperren; die
   Anzahl der Stammaktienklassen kommt nie aus dem Provider.
6. Steuer: effektive Quote als Vorschlag kennzeichnen, operativen Satz bestätigen lassen.
7. `financialCurrency` lesen, gegen Zuordnung prüfen und von der Handelswährung
   trennen; keine EUR-Annahme.
8. Spalten als Periodenenden lesen, Spalten ohne Werte verwerfen, gegen das
   erwartete Geschäftsjahresende (± 10 Tage) prüfen; Quartalsdaten nicht voraussetzen.
9. Veröffentlichungs- und Berichtssatzdaten als unbekannt führen; ein Abruf liefert
   einen Satz aus einem Abruf, kein Mischen alter und neuer Abrufe.
10. Metadaten-Platzhalter und Teilantworten als Fehlerstatus erkennen, begrenzt
    wiederholen und keine Zahlen daraus ableiten; Abrufe sparsam und mit Pausen.
11. Vor Nutzung der Provider-EBIT-Werte die Abgrenzung je Emittent bestätigen lassen
    (Abweichungen von 2–3 % im Plausibilisierungstest).

## Prüfung und Wiederholung

Die Auswertelogik wird mit `uv run --locked pytest tests/test_fundamentals_coverage.py`
offline geprüft. Eine Wiederholung braucht eine neue ausdrückliche Besitzerfreigabe
und läuft nur manuell (Aufruf im Kopf von `scripts/fundamentals_coverage_probe.py`).
SEC- oder andere Gratisanbieter sind nur eine dokumentierte spätere Option; es gibt
keinen zweiten Adapter und keinen kostenpflichtigen Fallback.
