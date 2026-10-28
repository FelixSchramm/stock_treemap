# Offline-Verträge für Fundamentaldaten und inneren Wert

M8-03 (#72) setzt die typisierten Verträge aus
[INTRINSIC_VALUE.md](INTRINSIC_VALUE.md) und [ADR 0010](adr/0010-innerer-wert-fcff.md)
um. `fundamentals` und `intrinsic_models` verwenden ausschließlich die
Standardbibliothek und bestehende reine Instrumentmodelle. Es gibt hier keinen
Providerabruf, Cache, Speicherpfad, Editor oder DCF-Rechner. Die getrennte
[private Szenarioablage](PRIVATE_SCENARIOS.md) (#76) persistiert diese Verträge
über den gemeinsamen geschützten SQLite-Zugriff. `models.Valuation`
bleibt die vorhandene Marktpreisbeobachtung.

## Identität und gemeinsamer Datenstand

- `VerifiedFundamentalIdentity` verbindet ein vorhandenes `VerifiedListing`
  ausdrücklich mit Anteilsklasse, Emittent, `FundamentalSource`,
  Konsolidierungskreis, Emittententyp und datiertem Beleg. Konstruktion behauptet
  eine vorherige externe Prüfung; der Code prüft keine echten Belege. Namen und
  Ticker sind keine Zuordnungsschlüssel.
- `CapitalStructure` enthält die unabhängig belegte vollständige Anzahl der
  Stammaktienklassen einschließlich nicht gelisteter Klassen, Quelle,
  Kapitalstichtag und Prüfbeleg. Genau eine Klasse ist erforderlich; mehrere
  sind nicht unterstützt, eine unbekannte Anzahl bleibt unvollständig. Ein
  geprüftes Listing allein beweist die Anzahl nicht.
- `FundamentalRegistry(identities, report_sets).resolve(instrument)` liefert
  denselben ausgewählten unveränderlichen `FundamentalReportSet` für alle
  geprüften Listings exakt derselben Aktie. Andere Instrument-IDs, Klassen,
  ADRs und ungeprüfte Listings übernehmen diesen Satz nicht. Widersprüchliche
  Zuordnungen und mehrere ausgewählte Versionen derselben Aktie werden abgelehnt.
  Portfolio und spätere Watchlist können diesen Lookup gleichermaßen verwenden;
  weder Registry noch Berichtssatz enthalten Bestandsmenge oder Einstand.

## Beobachtungen, Perioden und Änderungen

`FundamentalObservation` hält Feld, `Decimal | None`, `ValueStatus`, Einheit,
Periode, `ReportIdentity` und `FieldProvenance` getrennt. Fehlend und ungültig
tragen keinen Zahlenwert. Belegte Nullwerte bleiben erhalten, soweit die
Feldregel sie erlaubt. NaN, Infinity und Float-Werte werden abgelehnt.

`FundamentalUnit` trennt Geld mit Berichtswährung und Faktor zur Haupteinheit,
skalierte Stückzahlen und unskalierte Raten. Unbekannte Währung/Skala bleiben
unbekannt und sperren die Eingabebasis. Explizite verschiedene Skalierungen
bleiben an den Originalwerten erhalten; ein späterer Rechner muss sie vor der
Arithmetik nachvollziehbar normalisieren. Hier erfolgt keine Umrechnung.
Währungscodes verwenden dieselbe Syntaxprüfung wie `QuoteUnit`; die echte
Währungszuordnung muss der Datenlieferant verifizieren.

`FlowPeriod` unterscheidet Geschäftsjahr, Quartal, YTD und TTM samt Beginn,
Ende, Label und Vollständigkeit. `InstantPeriod` bezeichnet Bilanzstichtage,
`CurrentSharesPeriod` aktuelle Aktienzahl und Splitbasis,
`WeightedSharesPeriod` historische gewichtete EPS-Stückzahlen. Falsche
Feld-/Perioden- und Feld-/Einheitenkombinationen werden abgelehnt.

Die Herkunft hält Provider-/Belegreferenz, Rohdefinition und ausdrücklich
beschriebene Normalisierung fest. Providerbeobachtungen benötigen einen
zeitzonenbewussten Abrufzeitpunkt. Dokument- und Publikationszeitpunkt dürfen
unbekannt bleiben; keiner wird aus dem Abruf abgeleitet. Private Referenzen
gehören ausschließlich in geschützte Laufzeitdaten, niemals in Logs oder GitHub.

`ManualAdjustment` bewahrt Original und Ersatz als getrennte Beobachtungen;
Feld, Periode und Bericht müssen gleich bleiben. Grund, Änderungszeit und
Beleg sind Pflicht. Erst eine datierte explizite Bestätigung aktiviert den
Ersatz in `effective_observations`; unbestätigte Änderungen sperren weiterhin.
Buchwertnäherungen bleiben als Hinweis sichtbar.

## Berichtssatz und Qualitätsgründe

`FundamentalReportSet` hält eine ausgewählte Eingabeversion mit konsistenten
Berichts-/Restatement-IDs, Emittent, Konsolidierungskreis und Berichtswährung.
Die Kapitalbrücke verwendet einen gemeinsamen Stichtag; aktuelle Aktienzahlen
und Klassenbeleg denselben Kapitalstand und dieselbe Splitbasis.

`issues_at(valuation_at)` verlangt einen expliziten zeitzonenbewussten
Bewertungszeitpunkt. Es prüft strukturell mindestens drei vollständige
aufeinanderfolgende Geschäftsjahre, ihre Pflichtkomponenten und NWC-Stichtage,
Kapitalbrücke, aktuelle Aktienzahl sowie die Altersgrenzen aus ADR 0010.
Ein neuer Abruf verjüngt keinen Geschäftsbericht. Bekannte zukünftige Daten
werden gesperrt. Unbekannte Publikation bleibt ein sichtbarer Hinweis.
`qualifications` transportiert zusätzliche belegte Sperrgründe, beispielsweise
ungeklärte Ansprüche oder Kapitalmaßnahmen, ohne deren finanzielle Behandlung
zu erraten.

Alle unabhängigen Gründe bleiben als `QualityIssue(status, code)` sichtbar.
`QualityStatus` trennt fehlend, ungültig, unvollständig, veraltet, manuell
ergänzt, unbestätigte Annahmen und nicht unterstützt. `blocking` kennzeichnet
Sperrgründe; manuelle Herkunft, Buchwertnäherung und unbekannte Publikation
sind Hinweise. Keine strukturellen Sperrgründe bedeutet noch keinen bewiesenen
positiven FCFF: finanzielle Eignung, Reinvestition, Claims und alle Gleichungen
prüft der Rechner aus #77 (siehe [DCF_CALCULATION.md](DCF_CALCULATION.md)).

## Prognose und Resultat

`InputReference.from_report_set(report_set)` bindet Annahmen an Instrument,
Klasse, Emittent, Berichtsfassung und Eingabeversion. `ScenarioAssumptions`
enthält einen Plan aus fünf `ForecastYear`-Komponentensätzen, `TerminalPlan`,
explizite WACC-/g-/ROIC-Annahmen und optionale Sicherheitsmarge, jeweils in
Berichtswährungs-Haupteinheiten. Fehlende Eingaben bekommen keine Defaults.
`issues_for(report_set)` prüft die genaue Bindung und übernimmt Eingabegründe;
neue Berichte oder manuelle Eingabeversionen erfordern eine neue Bestätigung.
Die drei `Scenario`-Labels ersetzen weder Bestätigung noch Rechnung.

`IntrinsicValueResult` hält Eingabe-, Annahmen- und Modellversion,
Bewertungszeitpunkt, Quellen, ungerundete `CalculationStep`-Werte und sämtliche
Qualitätsgründe. Ein Sperrgrund verbietet einen Pro-Aktie-Modellwert und
Zielpreis. Ein verfügbares Resultat benötigt einen positiven endlichen Wert,
Quellen und Rechenschritte. Der spätere Rechner liefert und validiert diese
Werte; die Verträge berechnen, runden und sortieren keine Ergebnisse.

Prüfung ausschließlich mit erfundenen Daten:
`uv run --locked pytest tests/test_fundamentals.py tests/test_intrinsic_models.py tests/test_instruments.py tests/test_models.py`.
