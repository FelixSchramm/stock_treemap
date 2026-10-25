# Sheets ausschließlich lesend abrufen

`portfolio_dashboard.sheets_adapter.SheetsAdapter` liest pro `read`-Aufruf genau
einen konfigurierten Bereich über den offiziellen Sheets-REST-Endpunkt
`spreadsheets.values.get`. Es gibt keine Suche über Drive, keine Schreibmethode,
keine Änderung von Formeln oder Formaten, keine automatischen Wiederholungen und
keine Datenhaltung zwischen Versuchen. Werte werden als `UNFORMATTED_VALUE`
abgerufen; Formeln liefern ihre berechneten Werte. JSON-Dezimalzahlen gehen als
`Decimal` an den bestehenden [Zeilenparser](SHEET_PARSER.md).

## Expliziter Aufrufvertrag

Der Host übergibt `SourceConfig` mit `kind=SourceKind.GOOGLE_SHEETS`, privater
Spreadsheet-ID und endlichem A1-Bereich, beispielsweise dem **synthetischen**
`'Synthetic tab'!A7:M1000`. Der Bereich muss ein ausdrücklich benanntes Blatt,
Spalten A bis zu einer Endspalte zwischen M und T und beide Zeilengrenzen enthalten (Endspalte T liest auch die optionalen Hinweisspalten). Die erste Bereichszeile enthält die
dreizehn Pflichtüberschriften; die tatsächlichen Quellenzeilen bleiben erhalten. Die
Anzahl zurückgegebener Zeilen und Spalten darf den Bereich nicht überschreiten.
Nicht unterstützte Bereiche werden vor einem Abruf mit neutraler Meldung
abgelehnt. `SheetMetadata` legt Zahlenformat und gegebenenfalls unabhängig
verifizierte Quellenfakten fest; es gibt keine automatische Locale-Erkennung.

`read(attempted_at=...)` benötigt einen expliziten Zeitzonen-Zeitstempel für den
Abruf/Import. Er ersetzt niemals den tatsächlichen Kurszeitpunkt. Dieser bleibt
ohne explizites `sheet_price_observed_at` unbekannt. Auch die Bedeutung des
Einstands verbleibender Stücke wird durch einen erfolgreichen Abruf nicht
bestätigt.

Die injizierte Session stellt für den Adapter ausschließlich `get` bereit und
folgt dem Vertrag einer `requests.Session`. Der **autorisierte Host** muss sie
explizit mit einer verifizierten Service-Account-Identität und genau dem Scope
`https://www.googleapis.com/auth/spreadsheets.readonly` bereitstellen
(`SHEETS_READONLY_SCOPE`). Die gezielte Dateifreigabe begrenzt zusätzlich die
tatsächlich erreichbaren Dateien. Der Adapter erzeugt keine Session, behandelt
keine Tokens und prüft keine übergebenen Credential-Scopes. Eine synthetische
Session oder ein Token-Platzhalter beweist keine echte Berechtigung.

Der Host verantwortet Session-Lebensdauer und Token-Erneuerung, deren Timeouts
und neutrale Diagnostik, und muss automatische Wiederholungen, Hintergrundabrufe,
Drive-Suche und Sheet-Schreibpfade im verwendeten Transport ausschließen. Der
Adapter selbst sendet genau ein GET mit endlichem positivem Timeout und
`allow_redirects=False`; auch ein 401 löst hier keinen zweiten Abruf aus.
`verify=True` erhält die TLS-Prüfung einschließlich konfiguriertem CA-Vertrauen;
der Host muss die verwalteten Proxy-Einstellungen erhalten.

Der Adapter liest weder ADC noch Umgebungsvariablen oder Credential-Dateien,
auch nicht `SourceConfig.credentials_file`. Credential-Erzeugung, sichere Ablage
und Erneuerung gehören zu #14 und zur späteren autorisierten Host-Integration.
Ein verwalteter Proxy-Platzhalter ist kein lokal verwendbarer Signaturschlüssel.
Dieses Modul hat keinen Aufruf aus der App. Jede spätere echte Session-Erzeugung
und jeder echte Abruf müssen hinter die serverseitige Besitzerfreigabe aus #32.
Konfiguration allein stellt keine Freigabe dar.

## Ergebnisse, Fehler und Aktualisierung

`SheetsReadResult.parsed` enthält den unveränderten Parser-Vertrag einschließlich
Referenzwerten und ausgeschlossenen Zeilen. `result.snapshot` ist der zugehörige
Snapshot. Ein leerer API-Wertebereich oder reine Überschriften ergibt
`COMPLETE` ohne Positionen; ein nichtleerer Bereich mit ungültigen Überschriften
ergibt `FAILED`. Ungültige Positionszeilen ergeben weiterhin `PARTIAL` mit nur
den akzeptierten Positionen, ohne daraus einen vollständigen Bestand zu machen.

Timeout, Identitätsfehler, fehlende Berechtigung, nicht erreichbare Quelle,
Abruflimit, Dienstfehler, sonstiger Transportfehler und ungültige Antwort erhalten
feste Warncodes und einen `FAILED`-Snapshot ohne Positionen. Fehlerantworten
werden nicht als Rohdaten ausgewertet oder geloggt. Adapter und äußeres Ergebnis
haben neutrale Repräsentationen; die explizit entnommenen Domain-/Parserdaten
enthalten naturgemäß Bestandsdaten und dürfen nicht ungeschützt geloggt werden.
Der verwendete urllib3-Transportlogger wird während des Abrufs auch bei DEBUG
gefiltert, weil er private URLs und Providerdetails enthalten kann.
Andere Ausführungen behalten ihre Diagnostik; eigene injizierte Transports dürfen
ebenfalls keine privaten Wire-Logs oder Rohantworten ausgeben.

Jeder Refresh liefert einen eigenen Stand mit dem neuen Zeitstempel. Ein Fehler
übernimmt keine Positionen aus einem früheren Erfolg. Ein neu erzeugter Adapter
kennt keinen letzten guten Stand. Cache und gegebenenfalls separat angezeigte
ältere Daten gehören zu #19; nach Neustart ohne Zugriff bleiben Daten unbekannt.

Prüfung mit synthetischen Sessions und gesperrten IP-Sockets:
`uv run --locked pytest tests/test_sheets_adapter.py`.
Diese Tests belegen **keinen echten Laufzeitzugang**. Service-Account, gezielte
Freigabe und echter Abruf bleiben für #14 beziehungsweise den späteren
geschützten Hosting-Abgleich ungeprüft.

## Session aus Service-Account-Schlüssel

`portfolio_dashboard.sheets_session.build_readonly_session(path)` baut aus der
Schlüsseldatei (`PORTFOLIO_SHEETS_CREDENTIALS_FILE`, außerhalb des Repos) eine
`AuthorizedSession` mit ausschließlich dem Scope `SHEETS_READONLY_SCOPE`. Nur
Schlüssel vom Typ `service_account` werden akzeptiert. Fehler nennen weder Pfad
noch Inhalt. Das Sheet muss dem Dienstkonto als Betrachter freigegeben sein.
Im Produktivmodus baut `build_readonly_session_from_info(section)` dieselbe
Session aus dem Secret-Abschnitt `[sheets_service_account]`, erst nach der
Besitzerprüfung.
