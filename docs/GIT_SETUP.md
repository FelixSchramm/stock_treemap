# Git- und GitHub-Einrichtung

Stand: 23. Oktober 2026. **Noch nicht initialisiert oder veröffentlicht.**

Die aktuelle Sitzung darf `.git` im Projektordner nicht schreiben (`Operation not permitted`). Die lokale GitHub-CLI erreicht `api.github.com` nicht. Die verbundene GitHub-Integration meldet das Konto `FelixSchramm`, bietet aber keine Repository-Erstellung. Ein vorhandenes Repository mit dem Projektnamen konnte darüber nicht gefunden werden. Browser-Automation ist derzeit nicht verfügbar.

## Vorbereitet

- `.gitignore` und `.dockerignore` schließen Secrets, Exporte, Caches und lokale Artefakte aus.
- [Backlog](backlog/README.md): sieben Epics und 31 zugehörige Aufgaben mit Abhängigkeiten und Abnahmekriterien.
- `scripts/github_backlog.py` prüft das Backlog lokal und legt später Milestones, Labels, Epic-Issues und Aufgaben-Issues in einem privaten GitHub-Repository an. Ein erneuter Lauf erkennt vorhandene Einträge anhand stabiler IDs.

## Einrichtung außerhalb der eingeschränkten Sitzung

Im Terminal im Projektordner ausführen. Das Ziel ist ein **privates** Repository `FelixSchramm/stock_treemap_open_ai`. Keine Zugangsdaten in Projektdateien oder Issues eintragen.

```bash
gh auth status
# Nur falls nötig:
gh auth login --hostname github.com

git init -b main
git add .gitignore .dockerignore README.md PROJECT_PLAN.md heatmap.py requirements.txt tickers.txt docs scripts
git diff --cached --stat
git commit -m "chore: preserve prototype and plan dashboard epics"

gh repo create FelixSchramm/stock_treemap_open_ai --private --source=. --remote=origin --push
python3 scripts/github_backlog.py --repo FelixSchramm/stock_treemap_open_ai --apply
```

Falls das Repository inzwischen existiert, nicht erneut erstellen: Sichtbarkeit und Eigentümer prüfen und das bestehende Repository regulär anbinden. Keine fremde Historie überschreiben und keinen Force-Push verwenden. Die Anleitung oben ist für den aktuell noch nicht initialisierten Ordner und ein neues Repository gedacht.

## Lokale Prüfung ohne GitHub-Zugriff

```bash
python3 scripts/github_backlog.py
```

Der Standardlauf validiert IDs, Zuordnung, Referenzen und den Abhängigkeitsgraphen und schreibt nichts nach GitHub. `--apply` benötigt eine funktionierende, angemeldete `gh`-CLI. Die Ziel-Repository-Metadaten werden vor Schreibzugriff geprüft; öffentliche Repositories werden abgelehnt.

## Abschlussnachweis

Die Einrichtung ist erst abgeschlossen, wenn `git status` funktioniert, `origin` auf das gewünschte private Repository zeigt, der erste Commit auf GitHub vorhanden ist und der Import sieben Epic-Issues sowie 31 Aufgaben-Issues ausweist. Der Import schreibt die tatsächlichen GitHub-Links nach `docs/backlog/GITHUB_LINKS.md`; diese Datei anschließend regulär committen und pushen. M1-01 danach mit diesen Nachweisen schließen. Eine vorbereitete Datei ist noch kein veröffentlichtes GitHub-Issue.
