#!/usr/bin/env bash
# Entpackt eine APK (oder XAPK/APKM/APKS mit Split-APKs) nach workspace/<app-name>/
# und sammelt JS-Bundles, ML-Modelle und Framework-Indizien.
#
# Nutzung: tools/extract_apk.sh <pfad/zur/app.apk|.xapk|.apkm|.apks> <app-name>
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Nutzung: $0 <apk|xapk|apkm|apks> <app-name>" >&2
  exit 1
fi

INPUT="$1"
APP="$2"
[[ -f "$INPUT" ]] || { echo "Datei nicht gefunden: $INPUT" >&2; exit 1; }
[[ "$APP" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "app-name nur mit [A-Za-z0-9._-]" >&2; exit 1; }
command -v unzip >/dev/null || { echo "unzip fehlt" >&2; exit 1; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WS="$ROOT/workspace/$APP"
RAW="$WS/raw"
mkdir -p "$RAW" "$WS/bundle" "$WS/models"

echo "==> Entpacke $INPUT nach $RAW"
unzip -qo "$INPUT" -d "$RAW/base"

# Container mit Split-APKs: alle enthaltenen APKs zusätzlich entpacken
shopt -s nullglob
splits=("$RAW"/base/*.apk)
if (( ${#splits[@]} > 0 )); then
  echo "==> ${#splits[@]} Split-APK(s) gefunden"
  for s in "${splits[@]}"; do
    name="$(basename "$s" .apk)"
    unzip -qo "$s" -d "$RAW/$name"
  done
fi
shopt -u nullglob

# Optional: Manifest und Ressourcen dekodieren
if command -v apktool >/dev/null; then
  main_apk="$INPUT"
  if [[ -f "$RAW/base/base.apk" ]]; then main_apk="$RAW/base/base.apk"; fi
  if [[ "$main_apk" == *.apk ]]; then
    echo "==> apktool decode"
    apktool d -f -s -o "$WS/apktool" "$main_apk" >/dev/null || echo "   apktool fehlgeschlagen (wird übersprungen)"
  fi
else
  echo "   Hinweis: apktool nicht installiert, Manifest bleibt binär"
fi

echo "==> Suche JS-Bundles"
find "$RAW" -type f \( -name '*.bundle' -o -name '*.jsbundle' -o -path '*/assets/www/*.js' -o -path '*/assets/public/*.js' \) \
  -print0 | while IFS= read -r -d '' f; do
    cp "$f" "$WS/bundle/"
    if head -c 8 "$f" | od -An -tx1 | tr -d ' \n' | grep -q '^c61fbc03c103191f'; then
      echo "   $f  [Hermes-Bytecode]"
    else
      echo "   $f  [Klartext-JS]"
    fi
  done

echo "==> Suche ML-Modelle"
find "$RAW" -type f \( -iname '*.tflite' -o -iname '*.lite' -o -iname '*.onnx' -o -iname '*.ort' \
  -o -iname '*.mlmodel' -o -iname '*.ptl' -o -iname '*.pt' -o -iname '*.task' -o -iname '*.binarypb' \
  -o -iname '*.ubj' -o -iname '*xgb*' -o -iname '*lgbm*' -o -iname '*model*.json' -o -iname '*model*.bin' \) \
  -print0 | while IFS= read -r -d '' f; do
    cp "$f" "$WS/models/"
    printf '   %s  (%s Bytes)\n' "$f" "$(wc -c <"$f" | tr -d ' ')"
  done
# TFLite ohne passende Endung anhand des FlatBuffer-Identifiers finden
find "$RAW" -type f -size +10k ! -name '*.tflite' -print0 | while IFS= read -r -d '' f; do
  if [[ "$(dd if="$f" bs=1 skip=4 count=4 2>/dev/null)" == "TFL3" ]]; then
    cp "$f" "$WS/models/"
    echo "   $f  [TFLite ohne Endung]"
  fi
done

echo "==> Framework-Indizien"
libs="$(find "$RAW" -path '*/lib/*' -name '*.so' -exec basename {} \; | sort -u)"
has() { grep -qx "$1" <<<"$libs"; }
has libreactnativejni.so && echo "   React Native"
has libhermes.so        && echo "   Hermes-Engine"
has libflutter.so       && echo "   Flutter (Dart-Code in libapp.so)"
has libmonodroid.so     && echo "   Xamarin / .NET"
has libunity.so         && echo "   Unity"
has libtensorflowlite_jni.so && echo "   TensorFlow Lite Runtime"
has libonnxruntime.so   && echo "   ONNX Runtime"
[[ -d "$RAW/base/assets/www" || -d "$RAW/base/assets/public" ]] && echo "   Cordova / Capacitor"
dex_count="$(find "$RAW" -name 'classes*.dex' | wc -l | tr -d ' ')"
echo "   $dex_count Dex-Datei(en)"

echo
echo "Fertig. Arbeitsverzeichnis: $WS (wird nicht committet)"
echo "Nächster Schritt: tools/beautify_bundle.sh $WS/bundle/<datei>  bzw.  tools/search_bundle.sh $WS <kategorie>"
