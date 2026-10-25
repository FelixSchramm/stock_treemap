# Private Szenarien und manuelle Ergänzungen

M8-07 (#76) speichert Eingaben für die spätere Rechnung in #77 und den
Kursvergleich in #78. Es gibt keinen Providerabruf, Editor, DCF-Rechner oder
fertigen Bewertungswert. Reale Annahmen, Normalisierungen und Bestätigungen
kommen ausdrücklich vom Besitzer. Synthetische Vorlagen stehen ausschließlich
in Tests; neue Datensätze enthalten keine vorgegebenen Finanzannahmen.

## Ein gemeinsamer Stand pro geprüfter Aktie

`ScenarioRepository(PrivateDatabase())` verwendet für jede Operation denselben
[privaten Transaktions- und Zugriffspfad](PRIVATE_WATCHLIST.md) wie die Watchlist.
`get(identity, revision=None)` verlangt eine `VerifiedFundamentalIdentity` und
liefert den aktuellen `ScenarioDocument` oder `None`. Mit einer positiven
`revision` lässt sich ein früherer unveränderter Stand laden. Alternative
geprüfte Listings derselben Aktie teilen denselben Stand; der Lookup prüft
zusätzlich Klasse, Emittent, Quelle und Konsolidierungskreis. Andere
Instrument-IDs/Klassen bleiben getrennt. Ticker und Bezeichnungen sind keine
Schlüssel. Die externe Prüfung der Identität wird weiterhin vorausgesetzt,
nicht von dieser Ablage behauptet oder anhand von Namen abgeleitet.

Ein neuer `ScenarioDocument(originals, data_version, model_version, changed_at)`
hat Revision 0. `originals` ist ein `FundamentalReportSet` ohne manuelle
`adjustments`. `data_version`, `model_version` und der zeitzonenbewusste
Änderungszeitpunkt sind explizit. Die zwei Eingabeversionen haben getrennte Rollen:

- `originals.input_version` bezeichnet den unveränderten gelieferten Berichtssatz.
- `data_version` bezeichnet die ausgewählte Eingabebasis einschließlich manueller
  Änderungen oder Resets. `document.inputs` liefert ihren `InputReference`.

`save(document)` vergleicht dessen Revision mit dem aktuellen Datenbankstand und
schreibt eine vollständige neue Revision. Lesen, Vergleich, Prüfung bereits
verwendeter fachlicher Versionen und Einfügen laufen in **derselben**
`BEGIN IMMEDIATE`-Transaktion. Zwei konkurrierende Editoren können keine Version
unbemerkt überschreiben. Der Verlierer erhält `ScenarioConflict`, lädt erneut und
übernimmt seine Änderung ausdrücklich in den neuen Stand. Es gibt keinen
automatischen Merge. Erfolg wird erst nach Commit zurückgegeben. Alte Revisionen
bleiben erhalten; es gibt keinen automatischen Bereinigungs- oder Löschlauf.

Datenversionen dürfen nicht verschiedene Eingabebasen bezeichnen, originale
Datenversionen nicht verschiedene Originalberichte. Pro Szenarioname darf eine
Annahmenversion nicht verschiedene Pläne, Bindungen oder Modellversionen
bezeichnen. Diese Prüfung umfasst auch frühere Revisionen. Gleichbleibende
Versionen für unveränderte Inhalte sind zulässig.

## Historie, Normalisierung und Zukunft

Die drei Ebenen bleiben getrennt:

- `originals.observations` bewahrt die gelieferten Werte, Einheiten, Perioden und
  Quellen einschließlich fehlender Werte.
- `manual_values` enthält `ManualValue(inputs, adjustment, purpose, priority)`.
  `purpose` unterscheidet `ManualPurpose.SUPPLEMENT` und `NORMALIZATION`.
  `ManualAdjustment` bewahrt Original und Ersatz, Begründung, Quelle, Periode,
  Änderungszeit und ausdrückliche Bestätigung. Ersatzwerte benötigen bekannte
  passende Einheiten. `ManualPriority.MANUAL_WHEN_CONFIRMED` ist die sichtbare,
  gespeicherte Priorität: nur bestätigte, exakt gebundene Ergänzungen gelten vor
  der automatischen Quelle. Eine Prioritätswahl bestätigt keinen Wert.
- `scenarios` enthält höchstens einen `SavedScenario` je vorhandenem
  `Scenario.CAUTIOUS`, `BASE` und `OPTIMISTIC` (vorsichtig, Basis, optimistisch).
  Er hält `ScenarioAssumptions` und seine eigene `model_version`. Die vorhandenen
  `ForecastYear`- und `TerminalPlan`-Verträge speichern den expliziten
  Komponentenplan; es wird keine Fortschreibung aus historischen Zahlen geraten.

`effective_report_set()` liefert den getrennten normalisierten Ausgangsstand
als bestehenden `FundamentalReportSet`: Originalbeobachtungen plus passende
manuelle Anpassungen und ausgewählte `data_version`. Es überschreibt kein
Original. Unbestätigte Anpassungen bleiben fachlich gesperrt. Ein fehlender Wert
kann durch eine belegte Ergänzung ersetzt werden; auch dann bleibt der fehlende
Originalwert erhalten.

`with_manual(manual, data_version=..., changed_at=...)` verlangt die genaue
aktuelle **Original**-Bindung. Bei Änderungen und Reset muss eine neue ausgewählte
Datenversion angegeben werden. `reset_manual(manual, ...)` entfernt den gewählten
Ersatz aus dem aktuellen Stand; die automatische Quelle gilt wieder. Frühere
Revisionen behalten die Ergänzung und ihre Herkunft.

`with_scenario(assumptions, changed_at=...)` verlangt `assumptions.inputs ==
document.inputs` und dieselbe Berichtswährung. Die Methode ersetzt den Plan des
angegebenen Namens und bewahrt die übrigen Pläne. Sie setzt keine Bestätigung und
keine fehlende Zahl. Fehlende Pflichtkomponenten werden unverändert gespeichert
und liefern `INCOMPLETE`; fehlende Bestätigung liefert zusätzlich
`UNCONFIRMED_ASSUMPTIONS`. Nicht endliche Zahlen, Float-Eingaben, falsche Einheiten,
WACC ≤ g oder Sicherheitsabschläge außerhalb `[0, 1)` werden abgelehnt.

## Neue Berichte und Prüfung durch spätere Verbraucher

`refresh(originals, data_version=..., changed_at=...)` ersetzt die automatische
Basis ausschließlich innerhalb derselben geprüften Identität und Quelle. Die
neue Originalfassung benötigt bei geänderten Inhalten eine neue originale
Eingabeversion. Vorhandene manuelle Werte und Szenarien bleiben mit ihrer alten
Bindung erhalten. Weder Periode noch Bestätigung noch Prognosejahre werden
umgeschrieben. Ein unveränderter Originalstand überschreibt ebenfalls keine
manuelle Ergänzung.

`ManualValue.needs_review(originals)` zeigt geänderte Berichts-/Datenversionen
oder einen geänderten Originalwert. Solche Ergänzungen werden nicht angewendet;
der normalisierte Bericht enthält den blockierenden Grund
`manual_review_required`. Die erneute Prüfung benötigt einen neuen expliziten
`ManualValue` auf dem neuen Original und eine neue ausgewählte Datenversion.
Reset kann auch eine alte, prüfbedürftige Ergänzung entfernen.

Für #77/#78: `scenario(label)` liefert den gespeicherten Plan oder `None`.
**`issues_for(label)` am Dokument verwenden**, zusammen mit
`effective_report_set()` und `inputs`. Es kombiniert die bestehenden Eingabe-
und Annahmengründe mit der Modellbindung. Neue Daten liefern
`assumptions_input_mismatch`, ein neues Modell
`scenario_model_review_required`; ein fehlender Plan `scenario_missing`.
Alle Sperrgründe müssen bis zum Resultat erhalten bleiben. Ein gesperrter oder
unvollständiger Stand ist keine fertige Bewertung. Finanzielle Eignung,
FCFF-/Terminalgleichungen, Reinvestition und Rechenergebnisse prüft erst #77.

## Speicherformat, Betrieb und Grenzen

Schema 2 ergänzt `scenario_versions` mit Instrument-ID, Revision und
versioniertem JSON-Payload. Migration von Schema 1 prüft zunächst dessen genaue
Struktur, erhält sämtliche Watchlist-Daten und legt die neue Tabelle atomar an.
Fehlgeschlagene Migrationen rollen vollständig zurück. Unbekannte Fassungen und
Schemaabweichungen werden abgelehnt. Eine ältere App mit Schema 1 kann einen
Store mit Schema 2 nicht öffnen; kein automatischer Downgrade.

`scenario_codec.CODEC_VERSION = 1` kennzeichnet das Payloadformat. Endliche
Decimal-Zahlen werden als Strings ohne Float-Umweg und Zeitpunkte mit Zeitzone
gespeichert. Der Decoder akzeptiert nur feste Objektformen und bekannte Enums,
verwendet explizite Konstruktoren und validiert alle Fachverträge erneut.
Doppelte/unerwartete JSON-Schlüssel und ungültige Payloads liefern den festen
Fehler `PrivateStorageError(code="data")`; es gibt keine Pickles, dynamischen
Klassenimporte oder leeren Erfolgsfallback. Meldungen und Operationen loggen
keine privaten Werte; auch die neuen Aggregate geben sie nicht in `repr` aus.

Die bestehenden Besitzer-, Local-, Demo-, Pfad-, Dateirechte- und Wheel-Grenzen
bleiben gültig. Private Quellenhinweise und Annahmen verlassen diese geschützte
Ablage nicht. Demo-Daten liegen ausschließlich temporär pro Datenbankhandle;
Demo garantiert keine Neustarthaltbarkeit. Für private Neustarts sind weiterhin
ein geeignetes dauerhaftes Verzeichnis und geschützte Sicherungen erforderlich.
Die Datenbank ist nicht verschlüsselt. Die kleine Ablage liest zur
Versionsprüfung die Revisionen einer Aktie; sie ist für einen Besitzer gedacht,
nicht als unbegrenztes Mehrbenutzersystem.

Synthetische Prüfung:
`uv run --locked pytest tests/test_scenarios.py tests/test_private_store.py tests/test_watchlist.py tests/test_fundamentals.py tests/test_intrinsic_models.py`.
