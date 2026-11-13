# Formate: Nachschlagewerk

## App-Pakete

| Format | Plattform | Inhalt | Erkennen |
|---|---|---|---|
| APK | Android | ZIP mit `AndroidManifest.xml` (binär), `classes*.dex`, `res/`, `assets/`, `lib/<abi>/*.so` | `unzip -l` |
| Split-APKs / XAPK / APKM | Android | mehrere APKs (Base + Config-Splits für ABI, Sprache, Dichte) | ZIP mit mehreren `.apk` |
| AAB | Android | Upload-Format für den Play Store, wird nicht direkt installiert | `BundleConfig.pb` |
| IPA | iOS | ZIP mit `Payload/<App>.app/`, Binary meist FairPlay-verschlüsselt | `unzip -l` |

## Framework-Indizien

| Framework | Typische Spuren |
|---|---|
| React Native | `assets/index.android.bundle`, `libreactnativejni.so`, bei Hermes zusätzlich `libhermes.so` |
| Hermes | Bundle beginnt mit Magic `c6 1f bc 03 c1 03 19 1f`; kein Klartext-JS, braucht `hermes-dec`/`hbctool` |
| Flutter | `libflutter.so`, `libapp.so` (Dart AOT-Snapshot), `assets/flutter_assets/` |
| Xamarin / .NET MAUI | `assemblies/*.dll`, `libmonodroid.so` |
| Cordova / Capacitor / Ionic | `assets/www/` bzw. `assets/public/` mit HTML/JS |
| Unity | `libunity.so`, `libil2cpp.so`, `assets/bin/Data/` |
| Nativ Kotlin/Java | nur `classes*.dex`, dekompilierbar mit `jadx` |

## ML-Modellformate

### TensorFlow Lite (`.tflite`)

- FlatBuffer-Format, Identifier `TFL3` an Byte 4-7.
- Enthält Graph (Operatoren), Gewichte, oft quantisiert (int8/uint8, float16).
- Häufig für Bild-, Audio- und Textklassifikation on-device.
- Ansehen: Netron, `tf.lite.Interpreter(model_path=...)` -> `get_input_details()`, `get_output_details()`.
- Metadaten (Labels, Normalisierung) teils eingebettet (TFLite Metadata, ZIP am Dateiende).

### ONNX (`.onnx`, `.ort`)

- Protobuf-basiertes, framework-neutrales Austauschformat (PyTorch, scikit-learn, ...).
- `.ort` ist das optimierte Format von ONNX Runtime Mobile.
- Ansehen: Netron, `onnx.load(...)`, `model.graph.input`, `model.graph.node`.
- Kann neuronale Netze, aber auch klassische Modelle (TreeEnsemble-Operator) enthalten.

### Core ML (`.mlmodel`, `.mlmodelc/`, `.mlpackage/`)

- Apple-Format; `.mlmodel` ist Protobuf, `.mlmodelc` die kompilierte Fassung im App-Bundle.
- Typen: NeuralNetwork, MLProgram, TreeEnsemble, Pipeline u. a.
- Ansehen: `coremltools` (`ct.models.MLModel(...)`), Netron für `.mlmodel`.

### XGBoost (`.json`, `.ubj`, `.model`/`.bin`)

- Gradient-Boosted Decision Trees.
- JSON-Format: `learner.gradient_booster.model.trees[]`, je Baum Arrays wie `split_indices`, `split_conditions`, `left_children`, `right_children`.
- `learner.objective.name` verrät die Aufgabe (`binary:logistic`, `reg:squarederror`, `multi:softprob`, `rank:pairwise`).
- `learner.feature_names` ist oft gesetzt und damit sehr aufschlussreich.
- `.ubj` = Universal Binary JSON, gleiche Struktur.
- Ansehen: `xgboost.Booster(model_file=...)`, `booster.trees_to_dataframe()`.

### Weitere

| Format | Herkunft | Hinweis |
|---|---|---|
| `.ptl` / `.pt` | PyTorch Mobile / TorchScript | ZIP mit `code/` und `data.pkl` |
| LightGBM (`.txt`) | Microsoft | Textformat mit `Tree=0`, `split_feature=` |
| MediaPipe (`.task`, `.binarypb`) | Google | Bündel aus TFLite-Modellen und Graph-Konfiguration |
| Firebase ML Custom Models | Google | werden zur Laufzeit geladen, nicht im APK |

## Werkzeuge

| Werkzeug | Zweck |
|---|---|
| Netron | Visualisierung fast aller Modellformate |
| apktool | Manifest/Ressourcen dekodieren, smali |
| jadx | Dex -> Java/Kotlin-ähnlicher Code |
| hermes-dec / hbctool | Hermes-Bytecode disassemblieren/dekompilieren |
| prettier / js-beautify | Minifiziertes JS formatieren |
| mitmproxy | Eigenen HTTPS-Traffic beobachten |
