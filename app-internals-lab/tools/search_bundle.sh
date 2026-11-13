#!/usr/bin/env bash
# Durchsucht ein extrahiertes App-Verzeichnis oder ein einzelnes Bundle nach
# typischen Mustern, gruppiert nach Kategorie.
#
# Nutzung: tools/search_bundle.sh <datei|verzeichnis> <kategorie|regex>
# Kategorien: ml, network, config, storage, sdk, permissions, all
# Alles andere wird als eigener regulärer Ausdruck interpretiert.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

TARGET="$1"
CAT="$2"
[[ -e "$TARGET" ]] || { echo "Nicht gefunden: $TARGET" >&2; exit 1; }

declare -A P
P[ml]='\.tflite|\.onnx|\.mlmodel|\.ptl|xgboost|lightgbm|Interpreter\(|loadModel|TensorFlowLite|tflite|onnxruntime|InferenceSession|MLModel|predict\(|runInference|feature_names|featureVector|getInputTensor|ModelManager|CustomModel'
P[network]='https?://[A-Za-z0-9._-]+|baseURL|baseUrl|fetch\(|axios|XMLHttpRequest|okhttp|Retrofit|GraphQL|graphql|websocket|wss://|/api/v[0-9]+'
P[config]='remoteConfig|RemoteConfig|getRemoteConfig|featureFlag|FeatureFlag|launchdarkly|LaunchDarkly|optimizely|statsig|abTest|experiment|getBoolean\(|getValue\('
P[storage]='AsyncStorage|SharedPreferences|getSharedPreferences|MMKV|SQLite|openDatabase|Realm|Room|UserDefaults|localStorage|EncryptedSharedPreferences|Keychain|Keystore'
P[sdk]='firebase|crashlytics|amplitude|mixpanel|segment|appsflyer|adjust|branch\.io|braze|onesignal|sentry|bugsnag|datadog|facebook\.sdk|FBSDK|admob|google-mobile-ads|applovin|unityads|ironsource|hotjar|clevertap|newrelic'
P[permissions]='android\.permission\.[A-Z_]+|requestPermissions|PermissionsAndroid|NSCameraUsageDescription|NSLocation[A-Za-z]*UsageDescription|ACCESS_FINE_LOCATION|READ_CONTACTS|RECORD_AUDIO'

if command -v rg >/dev/null; then
  search() { rg -n -o -i --no-heading --no-ignore --hidden -a -M 300 -e "$1" "$TARGET" 2>/dev/null || true; }
else
  search() { grep -rnoiaE "$1" "$TARGET" 2>/dev/null || true; }
fi

run() {
  local name="$1" pattern="$2"
  echo "===== $name ====="
  local result
  result="$(search "$pattern")"
  if [[ -z "$result" ]]; then
    echo "(keine Treffer)"
  else
    # Treffer pro Wert verdichten, damit die Übersicht lesbar bleibt
    echo "$result" | awk -F: '{v=$NF; c[v]++; if(!(v in f)) f[v]=$1":"$2} END{for(v in c) printf "%5d  %-60s  erste Stelle: %s\n", c[v], v, f[v]}' | sort -rn | head -n 80
  fi
  echo
}

case "$CAT" in
  all) for k in ml network config storage sdk permissions; do run "$k" "${P[$k]}"; done ;;
  ml|network|config|storage|sdk|permissions) run "$CAT" "${P[$CAT]}" ;;
  *) run "regex: $CAT" "$CAT" ;;
esac

echo "Hinweis: Treffer sind Hinweise, keine Belege. Kontext prüfen mit: grep -n -C 5 '<treffer>' <datei>"
echo "Tokens, Keys und persönliche Kennungen nicht in findings.md übernehmen."
