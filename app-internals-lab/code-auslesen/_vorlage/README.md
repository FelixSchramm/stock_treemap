# <app-name>

| Feld | Wert |
|---|---|
| Paketname | `com.example.app` |
| Version | x.y.z (Build nnn) |
| Plattform | Android / iOS |
| Untersucht am | JJJJ-MM-TT |

## Technologie

- Framework: React Native / Flutter / nativ (Kotlin, Java, Swift) / Xamarin / Web-View / ...
- Indizien: z. B. `assets/index.android.bundle` vorhanden, `libflutter.so`, `libhermes.so`, `libreactnativejni.so`
- JS-Engine (falls RN): JSC / Hermes (Bytecode-Version ...)
- Obfuskierung: keine / ProGuard/R8 / Bundle minifiziert

## Extraktionsweg

1. APK aus eigener Installation bezogen (Weg beschreiben, keine Download-Links von Drittseiten).
2. `tools/extract_apk.sh <apk> <app-name>`
3. Bundle gefunden unter: `...`
4. Formatiert mit: `tools/beautify_bundle.sh ...`

## Grobe Code-Struktur

- Einstiegspunkt:
- Wichtige Module / Ordner:
- Auffällige Bibliotheken:

## Offene Fragen
