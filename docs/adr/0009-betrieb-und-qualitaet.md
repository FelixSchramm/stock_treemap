# ADR 0009: Hosting, Versionierung und gezielte Prüfung

- **Datum:** 2026-10-23
- **Status:** Angenommen für Betriebsanforderungen; Hosting-Anbieter offen
- **Herkunft:** Online-Betrieb vom Nutzer; Betriebs-/Prüfkonzept technisch. Git wurde optional zugelassen.

## Kontext

Das Online-Dashboard muss unabhängig vom Mac laufen und serverseitig Kurse/Sheets lesen. Hosting-Ressourcen und Budget sind noch nicht ausgewählt.

## Entscheidung

Die erste Version wird für einen einzelnen Python-Container mit persistentem Prozess vorbereitet. Erforderlich sind HTTPS, WebSockets, ausgehende HTTPS-Aufrufe, Secret-Verwaltung und Healthcheck. Eine eigene Domain ist optional. Anbieter, Konto, URL und Budget werden vor Veröffentlichung festgelegt; dieses ADR bestellt keinen Dienst.

Zunächst gibt es keine separate Datenbank: Sheets bleibt Bestandsquelle, der Cache ist rekonstruierbar. Nach Neustart ohne Datenzugriff zeigt die App „Daten nicht verfügbar“, statt einen nicht vorhandenen letzten Stand vorzugeben. Aktualisierung bei Nutzung genügt; eine nächtliche Automatisierung ist kein Kernbestandteil.

Die Umsetzung nutzt lokales Git; ein GitHub-Remote bleibt optional und wäre privat. Quellcode, Lockfile und Dokumentation werden versioniert. Credentials, Tokens, Sheet-Exporte, personenbezogene Logs und Depot-Caches werden aus Git und Container-Build ausgeschlossen. Tests verwenden synthetische Depots.

## Prüfung und Auslieferung

- Import: Zahlen/Bruchstücke, leere Werte, Summenzeilen, Nullbestände, gleiche Ticker verschiedener Instrumente.
- Berechnung: Perioden, Splits, FX, Einstand und Summen mit Datenlücken.
- Zugriff: anonym, fremdes angemeldetes Konto, erlaubter Besitzer, Abmelden und erneutes Öffnen.
- Browser: Ansichtswechsel, Legende, Tabelle, schmaler Bildschirm, Fehlerzustände.
- Integration: echte Kurs-/Sheet-Abrufe aus der Hosting-Umgebung; synthetische Tests allein belegen keine Provider-Verfügbarkeit.
- Veröffentlichung: geschützte HTTPS-URL prüfen; vorheriges funktionierendes Image als Rollback-Ziel behalten.

## Alternativen und Konsequenzen

Eine lokale Vorschau erfüllt nicht die Online-Abnahme. Ein statischer Host führt Python nicht aus. Ein Wechsel zu Worker/zusätzlichem Backend benötigt eine neue Entscheidung. Hosting-Kosten und Credentials sind offene Konfiguration. Login darf nicht zur Behebung eines Deploymentproblems abgeschaltet werden.

## Referenzen

- [Streamlit: Docker-Betrieb](https://docs.streamlit.io/deploy/tutorials/docker)
- [Umsetzungsplan](../../PROJECT_PLAN.md)
