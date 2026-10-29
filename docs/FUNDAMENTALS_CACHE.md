# Fundamentaldaten-Cache und geschützter Ladepfad

M8-05 ([#74](https://github.com/FelixSchramm/stock_treemap/issues/74)). Dieser Teil
holt die Jahresabschlüsse aus [FUNDAMENTALS_PROVIDER.md](FUNDAMENTALS_PROVIDER.md)
(#73) nur nach Besitzerprüfung, hält sie in der geschützten privaten Ablage und
stellt Portfolio und Watchlist denselben Datenstand zur Verfügung. Es gibt keinen
Hintergrunddienst und keine Oberfläche (Ansicht: #79/#80), keinen zweiten
Anbieter und keinen kostenpflichtigen Zugang. Reale Zuordnungen stehen nicht im
Repository.

## Aufbau

| Modul | Aufgabe |
| --- | --- |
| `fundamentals_config` | strenge Prüfung der privaten Zuordnung (`[[fundamental]]`) zu `FundamentalSubject`s: Identität, Berichtswährung, Geschäftsjahresende, Klassenanzahl, Besitzerbestätigungen |
| `fundamentals_cache` | versioniertes JSON-Format und Ablage der **rohen** `ProviderStatements` (`PrivateFundamentalsStore`, SQLite-Schema 3, Tabelle `fundamentals_cache`); `MemoryFundamentalsStore` für Demo |
| `fundamentals_loader` | `FundamentalsLoader.load(instruments, refresh=...)`, `FetchPolicy`, `FetchGate`, Ergebnistypen; Konstruktion aus der Laufzeitkonfiguration |
| `fundamentals_demo` | synthetische Demo-Daten über denselben Ladepfad |

Der Cache hält die rohen Providerzahlen, nicht die Normalisierung. Diese ist aus
(`identity`, `statements`, `expectation`) deterministisch und wird bei jedem Laden
wiederholt; eine geänderte Regel kann also keine veraltete Deutung hinterlassen.
Pro geprüfter Aktie gibt es eine Zeile (Schlüssel: Hash über Instrument-ID,
Anteilsklasse, Emittent, Quelle und Konsolidierungskreis, kein Klartext). Sie enthält
den **letzten vollständigen** Abruf mit Abrufzeit und Paketversion und getrennt davon
den letzten **fehlgeschlagenen** Versuch (nur bereinigte Codes, Zähler, früheste
Wiederholung). Beides wird nie vermischt.

## Zuordnung aus privater Laufzeitkonfiguration

Wie bei den Kurs-Listings: lokal eine TOML-Datei außerhalb des Repositorys
(`PORTFOLIO_FUNDAMENTALS_FILE`, nur im lokalen Modus gelesen), produktiv die Liste
`[[fundamental]]` in den Host-Secrets (`st.secrets`). Ohne Zuordnung entsteht kein
Lader; Demo verwirft die Einstellung. Ein ungültiger Eintrag stoppt den Einstieg mit
einer Meldung, die nur die Eintragsnummer nennt (nie Werte, Pfade oder Dateiinhalt).

```toml
[[fundamental]]
isin = "XX0000000001"           # Instrument-ID des geprüften Listings
venue = "SYN"                   # Yahoo exchangeName des Listings
symbol = "SYN.A"                # Yahoo-Symbol des Listings
# source_symbol = "SYN.A"       # optional: Quelle der Abschlüsse, sonst symbol
trading_currency = "EUR"        # Handelswährung des Listings
# quote_unit_scale = 1          # optional, Standard 1 (Pence/Cent: 0.01)
equity_form = "share"           # share oder adr
issuer_id = "<Emittent>"
share_class_id = "<Anteilsklasse>"
consolidation_scope = "<Konsolidierungskreis>"
issuer_kind = "nonfinancial"    # nonfinancial, bank, insurer, reit, unknown
evidence_reference = "<Beleg>"
verified_at = 2026-10-01T09:00:00Z      # mit Zeitzone
reporting_currency = "EUR"      # laut Besitzer; Abweichung sperrt alle Zahlen
fiscal_year_end = "12-31"       # MM-TT
ordinary_share_classes = 1      # optional; fehlt = unbekannt, sperrt Pro-Aktie-Wert
class_count_evidence = "<Beleg>"        # Pflicht, wenn die Anzahl angegeben ist
class_count_verified_at = 2026-10-01T09:00:00Z
confirm_ebit_definition = false         # optional, Standard false
confirm_share_split_basis = false
confirm_lease_treatment = false
```

Regeln: Pflichtschlüssel fehlen oder unbekannte Schlüssel (Tippfehler) lassen den
Eintrag scheitern, nichts wird ergänzt oder geraten. Naive Zeitangaben, Währungen
in Kleinschreibung und doppelte oder widersprüchliche Einträge (gleiche Listing-ID,
ein Symbol für zwei Listings, eine Anteilsklasse für zwei ISINs, abweichende
Besitzerangaben bei gleicher Quelle) werden abgelehnt. Mehrere Listings derselben
Aktie teilen genau einen Abruf, wenn sie dieselbe Quelle (`source_symbol`) nennen
und in Währung, Klassenanzahl und Bestätigungen übereinstimmen. Die Handelswährung
der Quelle muss zur Kurseinheit des Listings passen, sonst sperrt #73 die Zahlen.

Das Datum der Klassenanzahl-Prüfung (`CapitalStructure.as_of`) wird aus dem
Stichtag der datierten Aktienzahl gesetzt; der Beleg des Besitzers gilt damit für den
jeweils aktuellen Kapitalstand und bleibt mit `class_count_verified_at` sichtbar.
Ändern sich Klassenanzahl oder Bestätigungen, ändert sich die `input_version` des
Berichtssatzes (`<report_set_id>/c<Anzahl>f<Flags>`), nicht der Abruf.

## Zugriffsgrenze

`load` ruft zuerst `PrivateDatabase.require_real_access()`: Produktion verlangt die
validierte Besitzersitzung (`owner_is_authorized`, inkl. Ablauf), lokal die
Loopback-Adresse; **Demo und jeder andere Modus werden abgelehnt**. Danach liest
jeder Cache-Zugriff die Konfiguration und Sitzung erneut (je Transaktion), und vor
**jedem** Providerabruf wird die Prüfung wiederholt. Es wird nichts zwischengespeichert.
Fehlt `PORTFOLIO_PRIVATE_DATA_DIR`, schlägt bereits das Lesen des Caches fehl
(`PrivateStorageError("path")`): ohne geschützte Ablage gibt es keinen Abruf, weil sich
das Ergebnis nicht sichern ließe. Anonyme, fremde oder abgelaufene Sitzungen lesen
weder Cache noch Szenarien noch Zuordnung und lösen keinen Abruf aus. Der Anbieter
wird erst in den Builder-Funktionen erzeugt; Konstruktion und Import rufen nichts ab.

Der Abruf selbst läuft **außerhalb** einer SQLite-Transaktion (Lesen, Abruf, Schreiben
sind getrennte kurze Transaktionen), damit andere Sitzungen nicht auf das Netzwerk warten.
Alle Abrufe eines Prozesses laufen nacheinander über ein gemeinsames `FetchGate`; eine
zweite Sitzung findet danach den frischen Cache. Bei mehreren Prozessen auf derselben
Datei gibt es keine solche Sperre (Einzelbenutzer-Anwendung, siehe Grenzen).

## Abruffrist, Backoff, Grenzen

`FetchPolicy` (Standardwerte; die Frist stammt aus `PORTFOLIO_FUNDAMENTALS_TTL_SECONDS`,
initial 86 400 s = 24 h):

| Wert | Standard | Bedeutung |
| --- | ---: | --- |
| `ttl_seconds` | 86 400 | Abruffrist ab dem letzten erfolgreichen Abruf (nur bei Nutzung, kein Hintergrunddienst) |
| `min_refetch_seconds` | 3 600 | Mindestalter für einen **manuellen** Abruf (`refresh=True`) |
| `backoff_base/max_seconds` | 300 / 21 600 | nach Fehlern: verdoppelt je Folgefehler bis zur Obergrenze |
| `restricted_base/max_seconds` | 3 600 / 86 400 | nach Zugriffsbeschränkung (`access_restricted`) |
| `pause_seconds` | 3,0 | Mindestabstand zwischen Abrufen verschiedener Aktien (der Adapter pausiert zusätzlich innerhalb eines Abrufs) |
| `max_fetches_per_load` | 5 | höchstens so viele Aktien je `load`; Rest `deferred_batch_limit`, der nächste Aufruf setzt fort |

- **Cachetreffer:** innerhalb der Frist kein Abruf. Ablauf (`now - checked_at >= ttl`)
  löst genau einen Abruf je Aktie aus. Nie geladene Aktien kommen vor veralteten.
- **Manuelles Aktualisieren** überspringt nur die Frist, nie Backoff, Mindestalter,
  Stapelgrenze oder die Sperre nach Zugriffsbeschränkung (`manual_refresh_too_soon`,
  `backoff_active`).
- **Keine Wiederholung bei `access_restricted`:** der Fehler öffnet eine längere
  Sperre für die Aktie **und** stoppt alle weiteren Abrufe im Prozess
  (`provider_backoff`). Es gibt keinen Wiederholungsversuch im selben Lauf und keinen
  Ausweichanbieter; ein Abruf umgeht keine providerseitige Grenze.
- **Beschränkte Wiederholung:** zwischen zwei Versuchen liegt mindestens der Backoff;
  Zähler und Zeitpunkt liegen in der Ablage und überstehen einen Neustart.
- Eine geänderte Paketversion (`yfinance`) macht den gespeicherten Abruf fällig
  (`package_version_changed`); er bleibt als gekennzeichneter Stand erhalten, bis ein
  neuer Abruf gelingt.

## Was als Erfolg gilt

Ein Abruf ersetzt den gespeicherten Stand nur, wenn er vollständig (kein gemeldeter
Fehler, alle fünf Datensätze vorhanden), nicht gesperrt (`numbers_withheld`) und
verwertbar ist (Bilanzstichtag und datierte Aktienzahl vorhanden, keine
`*_unexpected_schema`). Andernfalls werden **keine** Teilzahlen gespeichert; der letzte
vollständige Stand bleibt unverändert und wird gekennzeichnet gezeigt, der Versuch
steht als `failures` (bereinigte Codes wie `annual_balance_timeout`, `no_usable_base`,
`numbers_withheld`) mit `last_attempt_at`. So entsteht nie ein Snapshot aus neuen und
alten Feldern. Ein Abruf mit **identischem Inhalt** behält seine ursprüngliche
Abrufzeit (`retrieved_at`, damit `report_set_id` und `input_version` stabil bleiben,
Hinweis `content_unchanged`); nur `last_checked_at` rückt vor. Ändert sich der Inhalt,
entsteht ein neuer Berichtssatz, und manuelle Ergänzungen prüft der Besitzer erneut
(`needs_review`).

## Ergebnis für Verbraucher (#79)

```python
loader = st.session_state[LOADER_STATE_KEY]      # "own.fundamentals_loader"; None ohne Zuordnung
bundle = loader.load(instruments, refresh=False)  # Iterable[Instrument]; Depot und Watchlist gemeinsam
entry = bundle.entry(instrument)                  # FundamentalsEntry
```

`bundle.provider_calls` zählt die Abrufe des Aufrufs. `load` wirft
`PrivateStorageError` (feste Meldung, `code`) bei fehlendem Zugriff oder fehlender
Ablage; dann wurde nichts abgerufen. Je `FundamentalsEntry`:

| Feld | Bedeutung |
| --- | --- |
| `status` | `current` (Abruf innerhalb der Frist), `cached` (letzter vollständiger Stand, gekennzeichnet), `unavailable` (kein vollständiger Abruf gespeichert, nichts erfunden), `unsupported` (Banken, ADRs, unbekannt: nie abgerufen), `unmapped` (keine Zuordnung) |
| `notes` | Gründe: `retrieval_window_expired`, `refresh_failed`, `package_version_changed`, `backoff_active`, `manual_refresh_too_soon`, `deferred_batch_limit`, `provider_backoff`, `content_unchanged`, `report_set_rejected`; bei `unsupported` die Modellgründe |
| `failures`, `last_attempt_at`, `retry_after` | bereinigter letzter Fehlversuch |
| `last_checked_at`, `retrieval_age(now)` | **Alter des letzten Abrufs** |
| `retrieved_at` | Lieferzeit dieses Inhalts (Berichtssatzname) |
| `latest_period_end`, `bridge_date`, `issues_at(valuation_at)` | **fachliches Alter** der Abschlüsse; `issues_at` meldet `annual_report_age`, `bridge_age`, `share_count_age` unabhängig vom Abruf |
| `normalized`, `report_set` | Normalisierung und `FundamentalReportSet` (oder `None`, z. B. gesperrt) mit den Besitzerbestätigungen; manuelle Felder bleiben fehlend |

Alter des Abrufs und fachliches Alter sind getrennt anzuzeigen: ein frischer Abruf alter
Abschlüsse bleibt alt, ein unbekanntes Veröffentlichungsdatum bleibt unbekannt
(`unknown_publication`). `stale` ist wahr bei Ablauf, fehlgeschlagener Aktualisierung
oder Paketwechsel. Der Ergebnistyp gibt in `repr` keine Zahlen oder Kennungen aus.

Ein Berichtssatz ist formal vollständig, aber noch kein Bewertungsergebnis: die
manuellen Felder (Steuersatz, überschüssiges Cash, ...) und die Rechnerprüfung (#77) folgen.

## Demo

`demo_fundamentals_loader()` nutzt denselben Ladepfad mit festem Zeitstempel, einem
Speicher ohne Dateizugriff und einem Offline-Anbieter mit erfundenen Zahlen
(`DEMO_FUNDAMENTALS_INSTRUMENT`). Demo erreicht weder die geschützte Ablage noch
den echten Anbieter noch eine echte Zuordnung; ein echter Lader lehnt den Demo-Modus ab.

## Betrieb und Grenzen

- Schema 3 ergänzt `fundamentals_cache` (siehe [PRIVATE_WATCHLIST.md](PRIVATE_WATCHLIST.md));
  eine ältere App kann eine Datenbank mit Schema 3 nicht öffnen, kein Downgrade.
  Die Ablage ist nicht verschlüsselt und kein Mehrbenutzersystem. Sicherung wie dort beschrieben.
- Speicherung der Rohzahlen nur in der geschützten Ablage; nie in Logs, Fehlermeldungen
  oder `repr`. Die Datenbank ist nicht Teil des Repositorys oder Images.
- Mehrere Prozesse auf derselben Datei teilen kein `FetchGate` und können beim ersten
  Start je einmal abrufen; die Datei selbst bleibt konsistent (eine Zeile pro Aktie,
  atomare Transaktionen).
- Offene Punkte aus #73 bleiben: Splitbasis, EBIT- und Leasing-Abgrenzung brauchen die
  Bestätigung des Besitzers; Veröffentlichungsdaten und Restatement-IDs sind unbekannt;
  Zwischenbilanzen fehlen; ADRs, Finanzunternehmen, Mehrklassen und weitere Börsen
  sind ungeprüft. yfinance ist ein inoffizieller Zugang ohne Zusage zu Umfang und
  Kostenfreiheit.

## Prüfung

```bash
uv run --locked pytest tests/test_fundamentals_loader.py tests/test_fundamentals_cache.py tests/test_fundamentals_config.py tests/test_private_store.py tests/test_scenarios.py
```

Alle Tests verwenden ausschließlich erfundene Daten, eine gesteuerte Uhr und einen
Fake-Anbieter; es gibt kein Netzwerk. Die zusätzliche lokale Gegenprobe gegen die
privaten Snapshots aus #71 ist nicht Teil der Tests.
