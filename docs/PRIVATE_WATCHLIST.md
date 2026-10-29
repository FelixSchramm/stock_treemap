# Watchlist-Zuordnung und private SQLite-Ablage

Es gibt genau eine Watchlist: das Watchlist-Blatt im Google Sheet
([ADR 0011](adr/0011-watchlist.md), [Blattschema](DATA_SOURCE.md#watchlist-blatt),
[Parser](SHEET_PARSER.md#watchlist-parser)). Hinzufügen, Ändern und Entfernen
erfolgen ausschließlich dort; die App pflegt keine zweite Liste (#95, Besitzer-
entscheid vom 25.10.2026). Die private SQLite-Ablage dient nur noch Szenarien und
manuellen Ergänzungen ([PRIVATE_SCENARIOS.md](PRIVATE_SCENARIOS.md), #76). Weder
Watchlist noch Zuordnung lösen DCF, Provider- oder Sheetabruf aus; Depotimport,
Mengen, Einstand und Marktpreisbewertungen bleiben unverändert.

## Zuordnung zu Portfolio und Bewertungsschlüssel

`portfolio_dashboard.watchlist` ist rein, offline und ohne Speicherzugriff.
`link_portfolio(items, positions, listings)` erhält die geparsten Zeilen
(`WatchlistSnapshot.items`, ein Tuple von `WatchlistItem`), die Depotpositionen
und die Registry geprüfter `VerifiedListing`-Einträge ([INSTRUMENT_MAPPING.md](INSTRUMENT_MAPPING.md))
und liefert je Zeile genau eine `WatchlistMembership` in Blattreihenfolge; keine
Zeile entfällt. Sie enthält `item`, `in_portfolio`, `valuation_key`, `status`
(`IdentityStatus`), `explanation`, bei geprüften Zeilen `listing`/`instrument`
der Registry. Weder Name, Ticker, Zeilennummer noch eine Zelle des Blatts wird je
zur `instrument_id`. Mengen, Einstand und Positionen werden nicht verändert.

**Regel für eine geprüfte Zeile.** Das Blatt liefert nur einen Kandidaten
(`Yahoo-Symbol`, `Handelsplatz`, `Währung`; Parserstatus `MAPPED`). Er gilt erst als
geprüfte Identität, wenn er **genau** mit einem Eintrag der bereits geprüften
Registry übereinstimmt:

- `provider_mapping` ist `yfinance` mit demselben Symbol,
- `trading_venue` ist derselbe Yahoo-`exchangeName`,
- `quote_unit` hat dieselbe Währung und den Faktor 1,
- eine angegebene `Typ`-Spalte widerspricht dem geprüften Typ nicht (leer ist erlaubt).

Der Bewertungsschlüssel ist die `instrument_id` des **Registryeintrags**, nicht
ein Wert aus dem Blatt. Dieselbe geprüfte Aktie hat daher in Portfolio und Watchlist
denselben Schlüssel, auch auf einem anderen geprüften Handelsplatz; die unveränderte
`FundamentalRegistry.resolve(membership.instrument)` liefert denselben Berichtssatz
wie für die Portfolioaktie. Andere Anteilsklassen, ADRs und andere Papiere behalten
eigene Schlüssel. `in_portfolio` ist wahr, wenn eine Position zur selben geprüften
`instrument_id` auflöst.

Alle anderen Zeilen bleiben sichtbar, erhalten aber **keinen** Bewertungsschlüssel:

| `IdentityStatus` | Bedeutung | Schlüssel |
| --- | --- | --- |
| `VERIFIED` | Kandidat entspricht einer geprüften Aktie der Registry | ja |
| `VERIFIED_NOT_STOCK` | entspricht einem geprüften Nicht-Aktien-Listing (ETF) | nein (kein Bewertungsmodell) |
| `UNMATCHED` | Kandidat fehlt in der Registry oder widerspricht ihr (Symbol, Platz, Währung, Einheit, Typ) | nein |
| `NO_CANDIDATE` | Zeile nur A–D, unvollständige/widersprüchliche Angabe, Derivat (Parser `SHEET_ONLY`/`FALLBACK`) | nein |

Ein Schlüssel macht aus einer Zeile weder eine Bewertung noch einen Kurs. Eine
geprüfte Aktie kann fachlich weiterhin ausgeschlossen sein (ADR 0010).

**Offene Besitzerfrage (#95).** ADR 0011 hat nicht entschieden, ob eine Blattzeile
*allein* (ohne Eintrag in der geprüften Registry) für die Bewertung nach ADR 0010
als Identität gilt, etwa über zusätzliche Spalten wie ISIN oder Anteilsklasse.
Bis zur Entscheidung gilt die enge Regel oben: Blatt-Kandidaten werden nur gegen
bereits geprüfte Registryeinträge bestätigt und nehmen keine Identitätsentscheidung
vorweg. Um eine Watchlist-Aktie ohne Depotposition zu bewerten, ist ihr Listing in
die geprüfte Registry (`listings.toml`/`[[listing]]`) aufzunehmen.

## Entfernte SQLite-Watchlist und Altbestand

`WatchlistRepository`, `WatchlistEntry`, `WatchlistConflict` und `MappingStatus`
(#75) sind entfernt. Die App liest oder schreibt die SQLite-Tabelle `watchlist`
nicht mehr und migriert, verschiebt oder löscht ihre Zeilen nicht.

Behandlung bestehender privater Datenbanken (Entscheidung zu #95):

- Die Tabelle `watchlist` bleibt unverändert (Schema inzwischen `SCHEMA_VERSION = 3`); die Tabelle `watchlist` ist
  weiterhin Teil der erwarteten Struktur. Eine alte Datenbank (Schema 1 oder 2) mit
  Zeilen startet daher wie bisher, wird bei Schema 1 nur um `scenario_versions`
  ergänzt und bleibt für Szenarien voll funktionsfähig.
- Die Zeilen werden nie gelesen oder geparst. Auch ungültige alte Nutzdaten können
  den Start oder Szenarien nicht mehr stören; sie werden aber auch nicht angezeigt.
- Keine stille Datenvernichtung: weder `DROP`, `DELETE` noch Umbenennung. Neue
  Datenbanken legen die Tabelle leer an (ein einheitliches Schema; keine Weiche
  zwischen alten und neuen Stores) und nichts schreibt hinein.
- Wer die alten Einträge noch braucht, überträgt sie selbst in das Watchlist-Blatt
  und sichert die Datenbank wie unten beschrieben. Das Entfernen der leeren oder
  veralteten Tabelle wäre eine eigene Migration (neue Schemaversion mit
  ausdrücklicher Besitzerfreigabe) und ist nicht Teil von #95.

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

`APPLICATION_ID`, `SCHEMA_VERSION = 3`, `MIGRATIONS` und `SCHEMAS` in
`private_store.py` kennzeichnen und prüfen das Format. Version 1 enthält
ausschließlich die seit #95 ungenutzte Tabelle `watchlist` (siehe oben).
Die erste Schemaanlage wird atomar vor der ersten Fachänderung committet;
ein abgelehnter Eintrag beschädigt keinen neuen Store. Jede weitere Migration
muss Version, SQL und die explizite erwartete Schemaprüfung gemeinsam ergänzen.
Version 2 ergänzt `scenario_versions` für #76 ohne Änderung der Watchlist-Tabelle
und erhält deren Daten. Version 3 ergänzt `fundamentals_cache` für #74 (rohe Fundamentalabrufe, [FUNDAMENTALS_CACHE.md](FUNDAMENTALS_CACHE.md)).
`PrivateDatabase.require_real_access()` prüft Besitzer bzw. Loopback ohne Dateizugriff und lehnt Demo ab.
Die private Szenarioablage verwendet denselben
Transaktions- und Zugriffspfad und eigene fachliche Versionen und JSON-Codecs.

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
Liste angezeigt. Ein unterbrochener erster Schemaaufbau kann eine leere
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
