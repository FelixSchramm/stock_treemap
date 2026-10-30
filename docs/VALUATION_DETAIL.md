# Detail- und Annahmenansicht «Innerer Wert»

Umsetzung von Issue #80 (M8-11, Epic #69) nach [ADR 0010](adr/0010-innerer-wert-fcff.md). Die Ansicht
erklärt den Modellwert einer geprüften Aktie und lässt den Besitzer seine Annahmen und fehlende
Geschäftszahlen kontrolliert pflegen. Sie ist eine Modellschätzung aus eigenen Annahmen: kein Kauf- oder
Verkaufssignal, kein Konfidenzintervall, kein Analystenziel. Es gibt keine Vorgabewerte für echte Aktien.

## Einstieg und Aufbau

Aus der [Bewertungsübersicht](VALUATION_OVERVIEW.md): Aktie wählen, **Detail- und Annahmenansicht öffnen**. Der
Knopf setzt `st.session_state[valuation_view.DETAIL_KEY]` auf die `instrument_id`; die Übersicht liest den
Schlüssel, **löscht ihn** und hält die geöffnete Aktie in `DETAIL_OPEN_KEY`. **Zurück zur Übersicht** schließt die
Ansicht und verwirft nicht gespeicherte Eingaben. Die Ansicht läuft hinter der Besitzerprüfung der App
(`app.py`); unautorisierte Sitzungen sehen weder die Ansicht noch einen vorbereiteten Zustand.

| Baustein | Aufgabe |
| --- | --- |
| `valuation_detail` | rein fachlich (kein Streamlit, keine Uhr): Parser und Validierung, Formular → `ScenarioAssumptions`, Ergänzungen → `ManualValue`, Dokumentoperationen, Anzeigetabellen, Rechenweg, Sensitivität |
| `valuation_detail_view` | Streamlit-Darstellung, Entwurfszustand, Speichern über `OverviewSource.scenario_saver` |
| `valuation_view` | öffnet/schließt die Ansicht, lädt nach dem Speichern neu (`reset_loaded_state`) |
| `valuation_demo`, `fundamentals_demo` | Demo: echtes `ScenarioRepository` auf einer temporären Demo-Datenbank der Sitzung |

Abschnitte von oben nach unten: Zusammenfassung (Status, Kursvergleich und Zielpreis des gespeicherten Stands, Datenstand),
**Offene Punkte der Datenbasis**, **Historische Geschäftszahlen**, **Manuelle Ergänzungen und Normalisierungen**,
**Zukunftsannahmen** (Editor, getrennt von den Geschäftszahlen), **Rechenweg**, **Sensitivität**, **Modellgrenzen**.

## Einheiten und Eingabe

Beträge stehen in **Millionen der Berichtswährung** (`Mio. EUR`), Aktienzahlen in **Mio. Stück**, Raten in **Prozent**,
der Wert je Aktie in der Berichtswährung. Die Einheit steht in jedem Feldlabel; **Felder und Einheiten erklärt** nennt
zusätzlich Erklärung und Vorzeichenregel jedes Feldes ohne Hover (der Tooltip am Feld wiederholt sie). Zahlen: Ziffern mit
Dezimalkomma oder -punkt, ohne Tausendertrennzeichen und ohne Einheit; Exponenten, `NaN`, `inf`, Texte und mehr als zwölf
Nachkommastellen werden mit einer festen Erklärung abgelehnt (die Meldung enthält nie den eingegebenen Text). Intern
rechnet nur `Decimal` (kein `float`). Ein leeres Pflichtfeld bleibt ein Fehler und wird nie zu 0.

## Historische Geschäftszahlen

Je Geschäftsjahr EBIT, Steuersatz, D&A, Capex, NWC am Jahresende sowie abgeleitet ΔNWC und FCFF, darunter Kapitalbrücke
und Aktienzahl mit Stichtag. Ein Stern kennzeichnet einen bestätigten manuellen Wert, **fehlt** eine Lücke; eine Lücke
macht abgeleitete Werte `n. v.`. Der abgeleitete historische FCFF ist nur Anzeige (dieselbe Gleichung wie in `dcf`; ein Test
vergleicht beide). **Quellen und Perioden aller Werte** nennt je Wert Periode, Anbieter und Zeilenname, Abruf, unbekanntes
Veröffentlichungsdatum bzw. bei manuellen Werten Quelle, Begründung, Änderungs- und Bestätigungsdatum und den
**erhaltenen Originalwert**.

## Manuelle Ergänzungen und Normalisierungen (#76)

Auswählbar ist jede Beobachtung der Modelleingaben (nicht die Kontrollwerte Provider-FCF und gewichtete Aktienzahl, nicht
das abgeleitete ΔNWC). Fehlt der Anbieterwert (die manuellen Pflichtfelder aus `FIELD_CATALOG`/`MANUAL_FIELDS`:
operativer Steuersatz je Jahr, überschüssiges Cash, nichtoperative Vermögenswerte, Vorzugs- und Minderheitsansprüche,
inkrementelle Verwässerung), ist es eine **Ergänzung** (`ManualPurpose.SUPPLEMENT`), sonst eine **Normalisierung**.
Pflicht sind Wert, **Begründung** und **Quellenhinweis**; die Periode ist die der gewählten Beobachtung. Der Quellenhinweis
bleibt in der privaten Ablage und erscheint nur in dieser Besitzeransicht. Anbieter-Vorschläge (z. B. effektive
Steuerquote, Bruttoliquidität als Obergrenze) stehen als Hinweis; sie werden **nie vorbelegt oder übernommen**.

- Die Ergänzung wird nur wirksam, wenn sie **bestätigt** ist (Kontrollkästchen mit Bestätigungszeit); eine unbestätigte wird
  gespeichert, aber nicht angewendet und sperrt weiterhin. Die Priorität (`MANUAL_WHEN_CONFIRMED`) steht sichtbar am Feld.
  Optional: Markierung als Buchwertnäherung.
- **Zurücksetzen** entfernt den Ersatz aus dem aktuellen Stand; der Anbieterwert (oder die Lücke) gilt wieder. Frühere
  Revisionen behalten Ergänzung und Herkunft. Aktualisieren überschreibt nie eine Ergänzung.
- Jede Änderung erzeugt eine **neue Revision** mit neuer Datenversion (`<Eingabeversion>/d<n>`). Bereits gespeicherte Annahmen
  gehören dann zur früheren Datenbasis: Sie liefern keinen Wert, bis sie im Editor geprüft, **erneut bestätigt** und
  gespeichert werden; die Felder zeigen die früheren Zahlen als Ausgangspunkt, die Bestätigung wird nie übernommen.
- **Besitzerbestätigungen:** EBIT-Definition, Splitbasis und Leasing-Behandlung sowie die Zahl der Stammaktienklassen
  liegen in der **privaten Fundamentaldaten-Zuordnung** (Datei bzw. Secrets, [FUNDAMENTALS_CACHE.md](FUNDAMENTALS_CACHE.md)), nicht in der
  App. **Offene Punkte der Datenbasis** zeigt sie nur an (`bestätigt`/`ausstehend`, mit dem Schlüssel `confirm_…`); es gibt keine
  zweite, widersprüchliche Bestätigung in der App.

## Editor für die drei Szenarien

Radio **Szenario** (Vorsichtig, Basis, Optimistisch). Je Szenario: Ende des ersten Prognosejahrs (Datumsfeld, Standard aus
dem letzten Jahresabschluss: das erste Geschäftsjahr, das nach dem Bewertungsstichtag beginnt; vier weitere folgen im
Abstand von zwölf Monaten), je Prognosejahr EBIT, Steuersatz, D&A, Capex, ΔNWC, dann WACC, nachhaltiges Wachstum g, nachhaltiger
ROIC, optionaler persönlicher Sicherheitsabschlag sowie D&A, Capex und ΔNWC des **nachhaltigen Folgejahres**, zwei
Begründungen (Pflicht) und die Bestätigung. NOPAT des Folgejahres wird aus der Modellgleichung abgeleitet (`NOPAT_5 × (1 + g)`)
und angezeigt; die nötige Netto-Reinvestition `NOPAT × g ÷ ROIC` und der dazu passende Capex stehen als Hilfetext, ein Knopf
trägt ihn auf ausdrücklichen Klick ein. Bewertungsstichtag t = 0 und Bestätigungszeit sind die Uhr der Anwendung beim
Speichern.

**Vorschau:** Bei jeder Änderung (Fokuswechsel/Enter) rechnet die Ansicht neu: Wert je Aktie, Zielpreis, Terminalwertanteil,
Rechenweg und Sensitivität. Die Vorschau behandelt das Szenario rechnerisch als bestätigt, damit die Rechnung sichtbar ist; sie
ist als **Vorschau (nicht gespeichert)** beschriftet. Gespeichert wird **nur** über **Szenario speichern**.
Ist der Entwurf ungültig, gibt es keine Vorschau und keine Zahl, nur den Hinweis und den unveränderten gespeicherten Wert.

**Validierung feldnah:** Jeder Fehler steht als `Fehler: …` unter dem Feld (Text, nicht nur Farbe): kein Zahlformat, Steuersatz
außerhalb 0 bis unter 100 %, negative D&A/Capex, WACC ≤ 0, g < 0, g ≥ WACC, g ≥ ROIC, Sicherheitsabschlag außerhalb 0 bis unter 100 %,
Folgejahr-Reinvestition passt nicht zu g und ROIC (mit dem passenden Capex), erstes Prognosejahr beginnt vor dem Stichtag, fehlende
Begründung. Fehler in einem leeren Feld erscheinen erst nach einem Speicherversuch. Bei jedem Fehler wird **nichts** gespeichert
(keine Teilwerte); der Speicherversuch meldet `Nicht gespeichert`.

**Speichern und Versionen:** über die private Szenarioablage ([PRIVATE_SCENARIOS.md](PRIVATE_SCENARIOS.md)): eine neue Revision, Annahmenversion
`a<Revision>`, die beiden anderen Szenarien und alle früheren Revisionen bleiben unverändert. Zugriff nur nach Besitzerprüfung
(Produktion) bzw. auf Loopback (lokal); die Demo nutzt eine temporäre Datenbank je Sitzung. Nach dem Speichern ruft die Ansicht
`valuation_view.reset_loaded_state(st.session_state)` auf; der nächste Lauf liest Fundamentaldaten und Szenarien einmalig neu aus
dem gemeinsamen Lader (Cache zuerst, derselbe Abrufrahmen wie das manuelle Laden). Speicherfehler zeigen die feste Meldung
(`PrivateStorageError`), die Eingaben bleiben stehen. Erfolg wird erst nach dem Commit gemeldet.

**Konflikt:** Hat ein anderer Editor zwischenzeitlich gespeichert, liefert die Ablage `ScenarioConflict`; nichts wird überschrieben,
die Eingaben bleiben sichtbar (zum Sichern kopieren), **Gespeicherten Stand neu laden** verwirft sie und lädt den neuen Stand. Es gibt keinen
automatischen Merge.

**Neuer Bericht (Prüfhinweis aus #76):** Liefert der Lader einen anderen Berichtssatz als der gespeicherte, sperrt die Ansicht den Editor
und die Ergänzungen und fordert **Neuen Berichtssatz übernehmen** an. Danach sind alle manuellen Werte
(`manual_review_required`) und Annahmen (`assumptions_input_mismatch`) als prüfbedürftig markiert und werden nicht auf die
neuen Zahlen angewendet; sie müssen ausdrücklich neu gespeichert werden. Es wird nie still zwischen Berichten übertragen.

## Rechenweg

Aus dem Ergebnis von `dcf.calculate_scenario` (ungerundet gerechnet, nur die Anzeige rundet): je Prognosejahr EBIT, Steuersatz, NOPAT, D&A,
Capex, ΔNWC, **FCFF**, **Diskontfaktor** und **Barwert**; Summe der Barwerte, Folgejahr (NOPAT, Reinvestition, nachhaltiger FCFF), **Restwert**
und dessen Barwert, **operativer Wert**, **Terminalwertanteil**; **Eigenkapitalbrücke** (zzgl. Cash und nichtoperative Vermögenswerte, abzgl.
Finanzschulden, Vorzugs- und Minderheitsansprüche), **verwässerte Aktienzahl**, **Wert je Aktie**, optional **Zielpreis**. Ab 75 %
Terminalwertanteil steht eine Warnung (Terminalwertdominanz). Gesperrte Ergebnisse nennen ihre Gründe verständlich (Codes in Klammern),
nie eine Zahl. Der Zielpreis `Wert je Aktie × (1 − Abschlag)` verändert den DCF-Wert nicht.

## Sensitivität

5 × 5 Wert je Aktie um WACC (Zeilen) und g (Spalten) des gewählten Szenarios, das Szenario selbst mit ●. Jede Zelle rechnet FCFF_6 mit
der Reinvestition `g/ROIC` neu (`dcf.calculate_sensitivity`). Schrittweiten (WACC 0,5/1/2 Prozentpunkte, g 0,25/0,5/1) sind
Anzeigeparameter, keine Annahmen. **Ungültige Kombinationen** (g ≥ WACC, g < 0, WACC ≤ 0, g ≥ ROIC, Eigenkapitalwert ≤ 0) stehen als
`n. v. (n)` mit nummerierter Erklärung darunter. Ist das Szenario gesperrt, gibt es keine Zahlen, nur die Gründe. Wenn der WACC über dem
nachhaltigen ROIC liegt, senkt höheres Wachstum den Wert; das erklärt ein Satz unter der Tabelle.

## Modellgrenzen

Immer sichtbar: Modellschätzung ohne Konfidenzintervall, konstante Raten, Jahresend-Cashflows, ggf. Terminalwertdominanz und negative
Prognose-FCFF; die Szenariospanne ist keine sichere Prognose; Analystenziele und Marktpreise fließen nicht ein; kein Kauf-/Verkaufsurteil.

## Demo

Alle Annahmen und Zahlen sind erfunden und so beschriftet. Die Demo-Aktie A trägt das synthetische Dokument aus
[INTRINSIC_VALUE.md](INTRINSIC_VALUE.md) (Basis 135,15 EUR mit Brücke der Demo); **Demo-Aktie US** hat keine Annahmen und zeigt den
leeren Zustand mit den offenen Datenpunkten. Gespeichert wird in einer temporären Datenbank der Sitzung (`DEMO_REPOSITORY_KEY`), nie in der
geschützten Ablage; ein neuer Browser-Tab beginnt wieder mit dem Ausgangsdokument.

## Bedienung auf schmalen Bildschirmen und per Tastatur

Nur native Streamlit-Widgets (Textfelder, Datumsfeld, Radio, Auswahl, Kontrollkästchen, Expander, Schaltflächen): Mindesthöhe 44 px,
Fokusring, Tab-Reihenfolge in Leserichtung (geprüft: Eingabe, Tab bis **Szenario speichern**, Enter speichert). Je Prognosejahr stehen
die Felder zeilenweise (EBIT/Steuersatz, D&A/Capex, ΔNWC) und stapeln sich auf schmalen Bildschirmen. Tabellen sind echte HTML-Tabellen
(`st.table`) mit kurzen Zellen (Einheit in Beschriftung und Erklärtext); sie passen bei 390 px in die Breite oder scrollen innerhalb ihres
Kastens, nie die Seite. Siehe [MOBILE_ACCESSIBILITY.md](MOBILE_ACCESSIBILITY.md), Abschnitt 8.

## Datenschutz

Echte Annahmen, Begründungen und Quellenhinweise gibt es nur in der geschützten Ablage und der Besitzeransicht. Sie erscheinen weder in
Logs noch in Fehlermeldungen (feste Texte; die Parserfehler enthalten nie die Eingabe) noch in `repr` (ein Test prüft Logs und Ausgabe).
Entwicklung, Tests und Demo verwenden ausschließlich erfundene Zahlen.

## Grenzen und offene Punkte

1. **Betrieb:** Die Live-App läuft auf Streamlit Community Cloud ohne dauerhaften Speicher. Gespeicherte Szenarien in
   `PORTFOLIO_PRIVATE_DATA_DIR` überstehen dort keinen Neustart ([COMMUNITY_CLOUD.md](COMMUNITY_CLOUD.md)). Das ist eine offene Besitzerentscheidung
   (dauerhafter Speicher oder lokaler Betrieb); sie wird hier nicht gelöst.
2. Prognosejahre sind zwölfmonatige Jahre ab dem gewählten Jahresende (keine 52/53-Wochen-Jahre); das erste Prognosejahr beginnt nach dem
   Bewertungsstichtag, das laufende Geschäftsjahr entfällt damit im Modell (wie im Beispiel in INTRINSIC_VALUE.md). Prognosejahr n wird mit
   n vollen Jahren abgezinst (1 ÷ (1 + WACC)^n), unabhängig vom gewählten Datum des Jahresendes (Modellvertrag aus #77, ADR 0010); die
   Oberfläche nennt das im Rechenweg.
3. Nicht gespeicherte Eingaben gehen beim Verlassen der Detailansicht oder beim Wechsel der Ansicht verloren.
4. Beim Neuladen nach dem Speichern gelten Abruffrist, Backoff und Stapelgrenze des Laders; eine fällige Aktualisierung anderer Aktien kann
   dabei mit abgerufen werden (wie beim manuellen Laden).
5. Reale Zukunftsannahmen und Normalisierungen trägt der Besitzer erst bei Nutzung ein; ohne sie zeigen reale Aktien `Annahmen fehlen`.
6. iOS Safari auf dem iPhone ist nicht geprüft.

## Prüfung

```bash
uv run --locked pytest tests/test_valuation_detail.py tests/test_valuation_detail_ui.py tests/test_valuation_ui.py tests/test_valuation_overview.py
```

Synthetisch und offline: Parser und Einheiten, Referenzwert 127,650095311423 aus dem Formular, jeder ungültige Feldfall, Terminalplan-Abgleich,
Bestätigung nicht übertragbar, Ergänzung/Normalisierung/Zurücksetzen, eindeutige Versionen über mehrere Speicherungen, konkurrierende Editoren,
neuer Bericht, Rechenweg- und Sensitivitätstabellen, Terminalwertdominanz, gesperrte Ergebnisse ohne Zahl, Datenschutz in Logs, Zugriffsregeln
der geschützten Ablage (Loopback) und Speicherfehler.
