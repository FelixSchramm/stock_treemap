# Sheets ausschließlich lesend abrufen

`portfolio_dashboard.sheets_adapter.JournalSheetsReader` liest pro `read`-Aufruf
die drei Tabellen `Instrumente`, `Transaktionen` und `Kurse_manuell`
([ADR 0012](adr/0012-sheet-datenmodell.md)) in **einem** Aufruf des offiziellen
Sheets-REST-Endpunkts `spreadsheets.values.batchGet`. Es gibt keine Suche über
Drive, keine Schreibmethode, keine Änderung von Formeln oder Formaten, keine
automatischen Wiederholungen und keine Datenhaltung zwischen Versuchen. Werte
werden als `UNFORMATTED_VALUE` abgerufen, Daten als Seriennummer; Formeln liefern
ihre berechneten Werte. JSON-Dezimalzahlen gehen als `Decimal` an die
[Tabellen-Parser](SHEET_PARSER.md).

## Expliziter Aufrufvertrag

Der Host übergibt `SourceConfig` mit `kind=SourceKind.GOOGLE_SHEETS` und privater
Spreadsheet-ID sowie die unterstützten Yahoo-Handelsplätze des Depots mit ihrer
Währung (`local_source.DEPOT_VENUES`). Die Bereiche sind fest und begrenzt
(`'Instrumente'!A1:L2000`, `'Transaktionen'!A1:L10000`, `'Kurse_manuell'!A1:C2000`);
mehr Zeilen oder Spalten als angefordert gelten als ungültige Antwort.

`read(attempted_at=...)` benötigt einen expliziten Zeitzonen-Zeitstempel für den
Abruf/Import. Er ersetzt niemals den tatsächlichen Kurszeitpunkt. Das Ergebnis
`JournalReadResult` enthält den Snapshot und die im selben Abruf gelesenen
geprüften Listings; ein Fehler liefert einen `FAILED`-Snapshot ohne Listings.

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
Echte Session-Erzeugung und jeder echte Abruf liegen hinter der serverseitigen
Besitzerfreigabe aus #32.
Konfiguration allein stellt keine Freigabe dar.

## Ergebnisse, Fehler und Aktualisierung

Fehlende oder doppelte Spaltennamen ergeben `FAILED` mit dem Code
`sheet_schema_error` (Meldung nennt nur Tabelle und Spalte). Fehler in einzelnen
Zeilen machen nur die betroffene ISIN „n. v.“ und ergeben `PARTIAL`; die übrigen
Positionen bleiben erhalten ([Regeln](DATA_SOURCE.md#regeln)).

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

Prüfung mit synthetischen Sessions:
`uv run --locked pytest tests/test_sheet_tables.py tests/test_ledger.py`.
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
