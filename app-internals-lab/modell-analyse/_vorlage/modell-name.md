# Modell: <modell-name> (<app-name>)

| Feld | Wert |
|---|---|
| Datei (lokal, nicht committet) | `workspace/<app-name>/.../<datei>` |
| Größe | ... KB |
| Modelltyp | XGBoost / LightGBM / TFLite / Core ML / ONNX / PyTorch Mobile / ... |
| Ausgeliefert | im APK / per Remote-Download (Firebase ML, eigenes CDN, ...) |
| Untersucht mit | z. B. Netron, `tflite`-Interpreter, `onnx`-Python-Paket, `xgboost` |

## Architektur / Struktur

- Bäume: Anzahl, max. Tiefe, Objective (z. B. `binary:logistic`)
- bzw. Layer: Anzahl, Typen (Conv2D, Dense, LSTM, ...), Quantisierung (int8, float16)
- Input-Tensor(en): Shape, Dtype
- Output-Tensor(en): Shape, Dtype, vermutete Bedeutung

## Input-Features (soweit erkennbar)

| Index | Feature-Name / Vermutung | Quelle im Code |
|---|---|---|
| 0 | | |

## Laden und Feature-Aufbereitung im Code

- Laden: siehe `code-auslesen/<app-name>/findings.md#f…`
- Feature-Zusammenstellung: siehe `code-auslesen/<app-name>/findings.md#f…`
- Verwendung des Outputs (Schwellwert, Ranking, UI-Entscheidung):

## Abgeleiteter Zweck

Was lässt sich aus Struktur, Features und Verwendung über den Zweck ableiten? Klar trennen zwischen belegt und vermutet.

## Offene Fragen
