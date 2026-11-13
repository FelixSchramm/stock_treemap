# tools

| Skript | Zweck |
|---|---|
| `extract_apk.sh <apk> <app-name>` | Entpackt APK/XAPK/APKM/APKS nach `workspace/<app-name>/`, sammelt Bundles und Modelle, listet Framework-Indizien |
| `beautify_bundle.sh <bundle> [out]` | Formatiert minifiziertes JS (prettier/js-beautify), erkennt Hermes-Bytecode |
| `search_bundle.sh <pfad> <kategorie\|regex>` | Sucht nach Mustern: `ml`, `network`, `config`, `storage`, `sdk`, `permissions`, `all` oder eigener Regex |

Alle Ausgaben landen unter `workspace/` und werden nicht committet.
