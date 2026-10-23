# heatmap-builder

## Geplantes Depot-Dashboard

Der vorhandene Code ist ein früher Heatmap-Prototyp. Das geplante Dashboard wird direkt online mit Login bereitgestellt, gewichtet Kacheln nach eigenen Positionswerten und bietet Kursentwicklung sowie Gewinn/Verlust seit Kauf in Prozent. Die Anwendung ist noch nicht umgesetzt.

- [Umsetzungsplan](PROJECT_PLAN.md)
- [Architekturentscheidungen / ADRs](docs/adr/README.md)
- [Geprüfte Google-Sheet-Struktur und Datenmapping](docs/DATA_SOURCE.md)

## Vorhandener Prototyp

Eigene Aktien-Heatmap (Treemap) wie bei TradingView: Kachelgroesse = Boersenwert, Farbe = Tagesveraenderung.

## Nutzung

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python heatmap.py
```

Das Skript erzeugt `heatmap.html` und oeffnet sie im Browser.

## Eigene Aktien

Ticker in `tickers.txt` eintragen, ein Ticker pro Zeile.

## Hinweise

- Datenquelle ist `yfinance` (inoffiziell, Kurse koennen verzoegert sein).
- Keine Anlageberatung.


Sheet 1aI5XmeKpOsgdFskItToPVNR-X8AWy9XNYh1Ggt49ZJw

gerne auch den Skill ascuhhean 
