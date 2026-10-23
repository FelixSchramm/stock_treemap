"""Erzeugt eine Aktien-Heatmap (Treemap) aus tickers.txt.

Kachelgroesse = Boersenwert, Farbe = Tagesveraenderung in Prozent.
Ausgabe: heatmap.html (interaktiv, im Browser oeffnen).
"""
import sys
import webbrowser
from pathlib import Path

import pandas as pd
import plotly.express as px
import yfinance as yf

TICKER_FILE = Path(__file__).with_name("tickers.txt")
OUTPUT_FILE = Path(__file__).with_name("heatmap.html")


def read_tickers(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [l.strip().upper() for l in lines if l.strip() and not l.startswith("#")]


def fetch(ticker: str) -> dict | None:
    """Holt Boersenwert und Tagesveraenderung. Gibt None zurueck, wenn Daten fehlen."""
    try:
        info = yf.Ticker(ticker).fast_info
        cap = info["market_cap"]
        last = info["last_price"]
        prev = info["previous_close"]
        if not all([cap, last, prev]):
            raise ValueError("unvollstaendige Daten")
        return {"Ticker": ticker, "Boersenwert": cap, "Aenderung": (last / prev - 1) * 100}
    except Exception as exc:
        print(f"Warnung: {ticker} uebersprungen ({exc})", file=sys.stderr)
        return None


def build_figure(df: pd.DataFrame):
    df = df.copy()
    df["Label"] = df.apply(lambda r: f"{r['Ticker']}<br>{r['Aenderung']:+.2f} %", axis=1)
    fig = px.treemap(
        df,
        path=["Label"],
        values="Boersenwert",
        color="Aenderung",
        color_continuous_scale=["#c0392b", "#2b2b2b", "#27ae60"],
        range_color=[-5, 5],
        color_continuous_midpoint=0,
    )
    fig.update_traces(textfont_size=22, textposition="middle center")
    fig.update_layout(margin=dict(t=30, l=5, r=5, b=5), coloraxis_showscale=False,
                      title="Heatmap: Tagesveraenderung")
    return fig


def main() -> None:
    tickers = read_tickers(TICKER_FILE)
    rows = [r for r in (fetch(t) for t in tickers) if r]
    if not rows:
        sys.exit("Keine Daten abgerufen. Internetverbindung und Ticker pruefen.")
    fig = build_figure(pd.DataFrame(rows))
    fig.write_html(OUTPUT_FILE)
    print(f"Gespeichert: {OUTPUT_FILE}")
    webbrowser.open(OUTPUT_FILE.as_uri())


if __name__ == "__main__":
    main()
