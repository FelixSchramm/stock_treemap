# Private Watchlist und SQLite-Ablage

M8-06 (#75) stellt Fachoperationen bereit. Die Oberfläche folgt in #79/#80;
Szenarien und manuelle Ergänzungen sind separat in
[PRIVATE_SCENARIOS.md](PRIVATE_SCENARIOS.md) (#76) beschrieben. Kein DCF, Provider-
oder Sheetabruf wird durch die Watchlist ausgelöst. Depotimport, Mengen, Einstand und
bestehende Marktpreisbewertungen bleiben unverändert.

## Fachschnittstelle

`WatchlistEntry(entry_id, label, listing=None, revision=1)` enthält weder
Stückzahl noch Einstand. `entry_id` ist eine vom aufrufenden Dienst vergebene
stabile Datensatz-ID, kein Ticker. Ohne `VerifiedListing` bleibt nur die
Bezeichnung sichtbar, `instrument` und `provider_mapping` sind `None`.
`mapping_status` und `mapping_explanation` erklären ungeprüfte Identität bzw.
fehlende Providerabdeckung. Bezeichnungen werden niemals als Providerinput
verwendet. Eine konstruierte `VerifiedListing` behauptet die vorherige externe
Prüfung gemäß dem vorhandenen Instrumentvertrag; die Ablage verifiziert keine
realen Zuordnungen und leitet keine fehlenden Identitäten her.

`WatchlistRepository(PrivateDatabase())` bietet `add(entry)`, `get(entry_id)`,
`list()`, `update(entry)` und `remove(entry_id, expected_revision=...)`.
Speichererfolg wird erst nach Commit zurückgegeben. `update` liefert eine neue
Revision; veraltetes Ändern oder Löschen wirft `WatchlistConflict`. Nach einem
Konflikt erneut laden. Auch zwei Sitzungen können dieselbe bestätigte Aktie
nicht doppelt anlegen: eine SQLite-Unique-Bedingung schützt deren verifizierte
Instrument-ID unabhängig vom Handelsplatz. Unterschiedliche Anteilsklassen
brauchen weiterhin unterschiedliche bestätigte Instrument-IDs; gleiche Namen,
Ticker oder Emittenten führen keine Klassen zusammen. Ungeprüfte Kandidaten
werden nicht anhand ihrer Bezeichnung dedupliziert.

`link_portfolio(entries, positions, listings)` liefert `WatchlistMembership`
mit `in_portfolio` und `valuation_key`. Die gemeinsame bestätigte
Instrument-ID kann vor einer Bewertung dedupliziert werden; andere Klassen
behalten eigene Schlüssel. Widersprüchliche Registry-Zuordnungen werden
abgelehnt. Die unveränderte `FundamentalRegistry.resolve(entry.instrument)`
liefert denselben Berichtssatz wie für genau dieselbe geprüfte Portfolioaktie.
Die Watchlist speichert keine Kopie der Unternehmensbewertung. Ungeprüfte
Einträge haben keinen Bewertungsschlüssel. Eine gespeicherte geprüfte Aktie
kann fachlich weiterhin etwa als ADR oder Mehrklassenemittent ausgeschlossen
sein; die Modellgrenzen aus ADR 0010 gelten unverändert.

## Datenpfad und Zugriff

Für Local/Produktion `PORTFOLIO_PRIVATE_DATA_DIR` ausdrücklich auf ein eigenes
absolutes Verzeichnis außerhalb von Checkout, Python-Installation und `/app`
setzen. Es gibt keinen privaten Standardpfad und keinen Speicherfallback.
Auch bei einer Wheel-Installation prüft der Dienst die serverseitigen Vorfahren
des Datenpfads auf Git-Checkouts (einschließlich `.git`-Dateien bei Worktrees)
und entpackte App-Wurzeln mit `app.py` und `pyproject.toml`. Die Prüfung hängt
nicht vom aktuellen Arbeitsverzeichnis oder Browserangaben ab.
Ohne Pfad funktionieren die übrigen App-Funktionen weiter; jede angeforderte
private Speicheroperation meldet einen Fehler statt Erfolg. Der Elternordner
muss bestehen. Der Dienst erstellt das Blattverzeichnis mit `0700` und
`private.sqlite3` mit `0600`; vorhandene zu offene Rechte, fremde Besitzer,
Symlinks und mehrfach verlinkte Datenbankdateien werden verweigert. POSIX-Rechte
werden soweit vom Betriebssystem unterstützt geprüft. Ein eigener Ordner
unter dem Home-Verzeichnis ist geeignet; Pfade und private Datensätze gehören
nicht in Kommandozeilenprotokolle oder GitHub.

Jede Transaktion liest die **aktuelle** Serverkonfiguration neu. Produktion
prüft `st.user` mit `owner_is_authorized` einschließlich Ablauf und Besitzer-
Allowlist **vor jedem** privaten Lesen/Schreiben. Local prüft jedes Mal dieselbe
Loopback-Liste wie `app.py`: `127.0.0.1`, `localhost`, `::1`. Ein gespeichertes
Repository-Objekt ist keine dauerhafte Freigabe. Konfigurations-, Identitäts-
und Adresscallbacks sind ausschließlich vertrauenswürdige Serververdrahtung,
niemals Eingaben aus Browserparametern. Kein Auth-Ergebnis wird gecacht.

Demo ignoriert den konfigurierten privaten Pfad vollständig und verwendet eine
leere temporäre Datenbank pro `PrivateDatabase`-Handle, ausschließlich für
synthetische Daten. `close()` entfernt nur diese temporären Demodaten; private
Datenbanken werden niemals automatisch gelöscht. Keine temporäre Demoänderung
verspricht Haltbarkeit über Neustarts.

## SQLite-Vertrag für #76

`PrivateDatabase.transaction()` ist die gemeinsame kleine Speicherschnittstelle
für weitere Repositories. Sie prüft Zugriff/Pfad/Schema und liefert genau für
die Dauer des Kontextblocks eine SQLite-Verbindung. Parametrisierte Statements
verwenden. Beim erfolgreichen Verlassen wird committet, bei Fehlern zurückgerollt
und die Verbindung geschlossen. Verbindungen oder private Ergebnisse gehören
nicht in globale Streamlit-Caches. Die Konfiguration wird je Aufruf neu gelesen.

`APPLICATION_ID`, `SCHEMA_VERSION = 2`, `MIGRATIONS` und `SCHEMAS` in
`private_store.py` kennzeichnen und prüfen das Format. Version 1 enthält
ausschließlich `watchlist` mit versioniertem JSON-Payload, bestätigter
Instrument-ID und Datensatzrevision.
Die erste Schemaanlage wird atomar vor der ersten Fachänderung committet;
ein abgelehnter Eintrag beschädigt keinen neuen Store. Jede weitere Migration
muss Version, SQL und die explizite erwartete Schemaprüfung gemeinsam ergänzen.
Version 2 ergänzt `scenario_versions` für #76 ohne Änderung der Watchlist-Tabelle
und erhält deren Daten. Die private Szenarioablage verwendet denselben
Transaktions- und Zugriffspfad und eigene fachliche Versionen und JSON-Codecs.
Die Watchlist selbst enthält weiterhin keine Szenarien oder Finanzannahmen.

`BEGIN IMMEDIATE` serialisiert kurze Operationen aus unabhängigen Sitzungen und
Prozessen. Schreibende Sitzungen warten höchstens zwei Sekunden auf SQLite;
beim ersten Dateianlegen wartet ein zweiter Initialisierer ebenfalls begrenzt.
Bei Belegung `PrivateStorageError(code="busy")` behandeln und nach erneutem
Laden wiederholen. Das ist für die kleine Einzelbenutzeranwendung bestimmt,
nicht für eine gemeinsam gemountete Netzwerkdatei oder mehrere Server.

## Fehler, Sicherung und Wiederherstellung

`PrivateStorageError.code` unterscheidet Zugriff, Local-Grenze, Pfad/Rechte,
Belegung, Beschädigung, unbekanntes Schema und ungültige gespeicherte Daten.
Meldungen enthalten keine Pfade, SQL-Fehler oder privaten Werte. Keine
Operation loggt Listen oder Datenbankinhalte. Die Datenbank ist **nicht
verschlüsselt**; Dateirechte ersetzen keine geschützte Host-/Backupsicherung.

`quick_check`, App-Kennung, Schemafassung und erwartete Tabellenstruktur werden
vor Zugriff geprüft. Beschädigte, leere/trunkierte vorhandene, fremde oder
zukünftige Datenbanken werden niemals still überschrieben oder als leere
Watchlist angezeigt. Ein unterbrochener erster Schemaaufbau kann eine leere
Datei hinterlassen; auch diese muss ausdrücklich geprüft werden.

Für eine Sicherung App/Schreibzugriffe stoppen und die vollständige Datenbank
auf geschützten privaten Speicher kopieren. Eventuelle SQLite-Journale/WAL-
Dateien nicht während eines laufenden Schreibvorgangs einzeln wegkopieren oder
löschen. Zur Wiederherstellung die App stoppen, den beschädigten Stand privat
aufbewahren und eine passende konsistente Sicherung mit denselben restriktiven
Rechten zurückspielen. Bei unbekannter neuer Schemafassung die passende
App-Version verwenden. Kein automatischer Downgrade oder Datenverlust-Fallback.

Im Container ist bei privater Nutzung ein vom Besitzer bereitgestelltes
persistentes Volume **außerhalb `/app`** erforderlich; UID 10001 muss den eigenen
Ordner lesen/schreiben können. Es wird kein Volume gekauft oder bereitgestellt.
Ohne über Neustarts erhaltenes Host-/Volume-Verzeichnis gibt es keine
Neustartgarantie. Git-/Container-Ignore-Regeln schließen Datenbanken, Sidecars,
Backups und gängige Watchlistdateien zusätzlich aus; sie ersetzen nicht das
Verbot privater Daten im Checkout oder Image.
