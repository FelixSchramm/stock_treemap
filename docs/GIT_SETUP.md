# Git, GitHub und private Dateien

Git ist lokal auf `main` eingerichtet; `origin` verweist auf `FelixSchramm/stock_treemap_open_ai`. Die [GitHub-Issues](https://github.com/FelixSchramm/stock_treemap_open_ai/issues) enthalten sieben Epics und 31 Aufgaben. Der [Backlog](backlog/README.md) dokumentiert deren Zuordnung und Reihenfolge.

## Versionierungsgrenze

Quellcode, Architektur, allgemeines Importschema und synthetische Beispieldaten gehören ins Repository. Persönliche Dokumentlinks, Sheet-IDs, konkrete Depotbefunde, Tickerlisten, Exporte, Caches und Credentials bleiben in ignorierten lokalen Dateien bzw. der Secret-Verwaltung.

- `docs/private/`: persönliche Quellen und konkrete Datenprüfungen.
- `tickers.txt`: lokale Liste; `tickers.example.txt` ist die öffentliche Vorlage.
- `.env`, `.env.*`, `.streamlit/secrets.toml`, Credentials-Dateien und Schlüssel: lokale Secrets.
- `data/`, `exports/`, `cache/`, `.cache/`, `logs/`: Laufzeitdaten.
- `*.local.*`: zusätzliche lokale Konfigurationsdateien.

`.gitignore` verhindert nur das erstmalige Hinzufügen passender Dateien. Bereits verfolgte Dateien müssen zusätzlich aus dem Git-Index entfernt werden. Eine Bereinigung des aktuellen Dateibaums entfernt keine alten Commits. Der ursprüngliche Commit enthält frühere private Dokumentbezüge; die Historie wurde auf Nutzerwunsch nicht ersetzt. Die Sichtbarkeit des Repositorys wird dadurch nicht automatisch geändert.

## Arbeitsablauf

Vor Änderungen den lokalen Stand mit dem Remote abgleichen. Für Aufgaben einen Branch erstellen, die jeweilige Issue-ID im Commit/PR verwenden und die festgelegten Abnahmekriterien prüfen. Secrets und echte Depotdaten dürfen weder in Diffs noch in Testausgaben oder Issue-Anhängen stehen. Ein Epic wird erst nach seinen Aufgaben und seinem Abschlusskriterium geschlossen.

Wenn Änderungen über den GitHub-Connector erfolgen, ist der lokale Git-Index nicht automatisch aktualisiert. In einer Sitzung mit eingeschränktem `.git`-Schreibzugriff muss der lokale Abgleich anschließend im normalen Terminal erfolgen; lokale Änderungen vorher sichern und nicht blind überschreiben. Die persönliche Tickerliste ist vorsorglich auch unter `docs/private/tickers.txt` gesichert.
