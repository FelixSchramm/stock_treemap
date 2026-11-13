# app-internals-lab

Persönliches Lernprojekt, um zu verstehen, wie mobile Apps intern funktionieren:

- **Code-Struktur**: Welches Framework steckt dahinter (React Native, Flutter, nativ Android/iOS)? Wie ist der Code organisiert?
- **Machine-Learning-Modelle**: Welche Modelle werden auf dem Gerät ausgeliefert (TFLite, Core ML, ONNX, XGBoost), wie sind sie aufgebaut und wofür werden sie vermutlich genutzt?
- **Datenflüsse**: Welche Endpunkte, Remote-Configs, SDKs und lokalen Speicher nutzt eine App?

## Rahmen und Grenzen

- Analysiert wird ausschließlich Code, der ohnehin an das eigene Gerät ausgeliefert wird (z. B. eine selbst installierte APK und deren Inhalt).
- Keine Nutzerdaten Dritter, kein Zugriff auf fremde Accounts.
- Kein Zugriff auf APIs oder Backends über die eigene, reguläre Nutzung der App hinaus. Netzwerk-Beobachtungen beschränken sich auf den Traffic des eigenen Geräts und Accounts.
- Keine Umgehung von Schutzmaßnahmen zu anderen Zwecken als dem Verständnis; keine Weitergabe oder Veröffentlichung von fremdem Code oder fremden Modellen.
- Committet werden nur eigene Notizen und Erklärungen. Rohdateien (APKs, entpackte Bundles, Modelldateien Dritter) bleiben lokal, siehe `.gitignore`. Code-Ausschnitte in `findings.md` werden kurz gehalten und dienen nur als Beleg für die eigene Erklärung.
- Zugangsdaten, Tokens oder persönliche Kennungen, die bei der Analyse auftauchen, werden nicht notiert, sondern geschwärzt (`<redacted>`).

## Struktur

```
app-internals-lab/
├── code-auslesen/<app-name>/        README.md (Technik, Extraktionsweg), findings.md (Fundstellen)
├── modell-analyse/<app-name>/       <modell-name>.md pro gefundenem Modell
├── weitere-analysen/
│   ├── netzwerk-traffic/<app-name>.md
│   ├── permissions-sdks/<app-name>.md
│   └── storage/<app-name>.md
├── tools/                           extract_apk.sh, beautify_bundle.sh, search_bundle.sh
├── notes/                           allgemeines Lernlog (z. B. formate.md)
└── workspace/                       lokale Rohdateien, wird nicht committet
```

Jeder Bereich enthält einen Ordner `_vorlage/` bzw. eine `_vorlage.md`, die für eine neue App kopiert wird.

## Typischer Ablauf

```bash
# 1. APK entpacken (landet in workspace/<app-name>/)
tools/extract_apk.sh pfad/zur/app.apk <app-name>

# 2. JS-Bundle lesbar machen (nur bei React Native / Web-basierten Apps)
tools/beautify_bundle.sh workspace/<app-name>/bundle/index.android.bundle

# 3. Gezielt suchen
tools/search_bundle.sh workspace/<app-name> ml
tools/search_bundle.sh workspace/<app-name> network

# 4. Erkenntnisse aus Vorlagen dokumentieren
cp -r code-auslesen/_vorlage code-auslesen/<app-name>
```

## Benötigte Werkzeuge (lokal)

- `unzip`, `grep`/`ripgrep`, `file`
- optional `apktool` (Ressourcen und Manifest dekodieren), `jadx` (Java/Kotlin dekompilieren)
- optional `npx prettier` oder `js-beautify` (Bundle formatieren)
- optional `hermes-dec` / `hbctool` (Hermes-Bytecode bei neueren React-Native-Apps)
- optional `aapt2` oder `apkanalyzer` (Manifest und Permissions)
