# Exploratory Analysis

Freier Experimentierordner, um **Finanzdaten und APIs auszuprobieren** (aktuell `yfinance`), am Beispiel einzelner
Aktien oder der eigenen Aktien. Er gehört nicht zur Dashboard-App: Hier wird nichts aus `src/` importiert und
nichts an `app.py` oder `tests/` geändert.

## Regeln für diesen Ordner

Die Arbeitsregeln für Agenten aus der [`AGENTS.md`](../AGENTS.md) im Repository-Root (Worker-/Reviewer-Rollen,
ein Issue pro Branch, Pull Requests nach `agents/integration`) sind für diesen Ordner **nicht gedacht**. Hier wird
frei erkundet, und Ergebnisse dürfen direkt auf `main`.

Unverändert gilt der gesunde Menschenverstand:

- Keine Zugangsdaten, Schlüssel oder echte Depot-Exporte committen. Eigene Rohdaten gehören in einen
  Unterordner `private/` (steht in der `.gitignore`).
- Nur lesende Abrufe, mit Pausen zwischen vielen Aufrufen.
- yfinance ist ein inoffizieller Zugang zu Yahoo Finance. Umfang und Drosselung können sich ohne Ankündigung ändern.

Hinweis: Die Datei `AGENTS.md` im Root lässt sich nicht pro Ordner abschalten. Ein Agent, der im Repository
arbeitet, kann sie weiterhin laden. Dieser Absatz hält nur fest, dass sie hier nicht gemeint ist.

## Inhalt

| Datei | Zweck |
|---|---|
| `msft_basics.py` | Kurzes Skript: die Grundfunktionen von yfinance am Beispiel Microsoft (Kurs, Historie, Dividenden, Berichte, Analysten, Eigentümer, Wechselkurs). |
| `msft_basics_output.txt` | Ausgabe des Skripts (Abruf vom 31.10.2026). |
| `01_msft_api_rundgang.ipynb` | Ausgeführtes Notebook: alle Basisfunktionen für MSFT, mit Status (`ok` / `leer` / `fehler`), Größe und Antwortzeit je Aufruf (58 Aufrufe: 54 mit Daten, 4 leer, 0 Fehler). |

## Selbst ausführen

```bash
pip install yfinance==1.7.0 pandas matplotlib jupyter
python msft_basics.py
jupyter lab 01_msft_api_rundgang.ipynb
```

## Beobachtungen aus dem ersten Lauf

- `get_isin()` liefert für MSFT nur `-`, `news` und `sustainability` kommen leer zurück.
- Jahresberichte reichen nur 4 bis 5 Jahre zurück, Quartalsberichte 5 bis 7 Quartale.
- Intraday-Daten sind zeitlich begrenzt (1 Minute etwa 7 Tage, 5 und 15 Minuten etwa 60 Tage, Stundenwerte etwa 2 Jahre).
