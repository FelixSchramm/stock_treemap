#!/usr/bin/env bash
# Formatiert ein minifiziertes JS-Bundle lesbar. Erkennt Hermes-Bytecode und
# verweist dann auf passende Disassembler.
#
# Nutzung: tools/beautify_bundle.sh <bundle-datei> [ausgabe-datei]
# Standard-Ausgabe: <bundle-datei>.pretty.js
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Nutzung: $0 <bundle-datei> [ausgabe-datei]" >&2
  exit 1
fi

IN="$1"
OUT="${2:-$IN.pretty.js}"
[[ -f "$IN" ]] || { echo "Datei nicht gefunden: $IN" >&2; exit 1; }

magic="$(head -c 8 "$IN" | od -An -tx1 | tr -d ' \n')"
if [[ "$magic" == "c61fbc03c103191f" ]]; then
  echo "Hermes-Bytecode erkannt, kein Klartext-JS." >&2
  if command -v hbc-decompiler >/dev/null; then
    echo "==> hermes-dec: dekompiliere nach $OUT"
    hbc-decompiler "$IN" "$OUT"
    exit 0
  fi
  echo "Optionen:" >&2
  echo "  pip install hermes-dec   ->  hbc-decompiler $IN $OUT" >&2
  echo "  hbctool disasm $IN <ordner>   (nur ältere Bytecode-Versionen)" >&2
  exit 2
fi

echo "==> Formatiere $IN -> $OUT"
if command -v prettier >/dev/null; then
  prettier --parser babel --print-width 100 <"$IN" >"$OUT"
elif command -v npx >/dev/null; then
  npx --yes prettier@3.3.3 --parser babel --print-width 100 <"$IN" >"$OUT"
elif command -v js-beautify >/dev/null; then
  js-beautify -s 2 "$IN" >"$OUT"
else
  echo "Weder prettier, npx noch js-beautify gefunden." >&2
  echo "Installieren: npm i -g prettier  oder  pip install jsbeautifier" >&2
  exit 1
fi

lines_in="$(wc -l <"$IN" | tr -d ' ')"
lines_out="$(wc -l <"$OUT" | tr -d ' ')"
echo "Fertig: $lines_in -> $lines_out Zeilen"

# React-Native-Module (Metro): __d(function..., <id>, [deps], "<pfad>") zählen
mods="$(grep -c '__d(' "$OUT" || true)"
[[ "$mods" -gt 0 ]] && echo "Metro-Module gefunden: $mods"
exit 0
