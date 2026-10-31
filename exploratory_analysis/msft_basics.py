"""Grundfunktionen der yfinance-API am Beispiel Microsoft (MSFT).

Eigenständig, nicht Teil der App. Nur lesende Abrufe, keine Zugangsdaten.

    pip install yfinance==1.7.0 pandas
    python msft_basics.py
"""

import warnings

import yfinance as yf

warnings.filterwarnings("ignore")

t = yf.Ticker("MSFT")


def show(title, data, rows=3):
    """Titel, Größe und die ersten Zeilen ausgeben."""
    size = "leer" if data is None or len(data) == 0 else f"{len(data)} Einträge"
    print(f"\n=== {title} ({size}) ===")
    if size != "leer":
        print(data.head(rows) if hasattr(data, "head") else data)


# 1. Aktueller Kurs und Stammdaten
fast = t.fast_info
print("Letzter Kurs:", round(fast["lastPrice"], 2), fast["currency"], "| Börse:", fast["exchange"])
info = t.info
for key in ("longName", "sector", "industry", "marketCap", "trailingPE", "dividendYield"):
    print(f"{key}: {info.get(key)}")

# 2. Kurshistorie (Open/High/Low/Close/Volume, Dividenden, Splits)
show("history 1 Monat, täglich", t.history(period="1mo", interval="1d"))
show("history 5 Tage, stündlich", t.history(period="5d", interval="1h"))
show("history max, täglich", t.history(period="max", interval="1d"), rows=2)

# 3. Dividenden und Splits
show("dividends", t.dividends.tail(3))
show("splits", t.splits.tail(3))

# 4. Finanzberichte (Spalten = Berichtsperioden)
show("income_stmt (Jahr)", t.income_stmt.loc[["Total Revenue", "Net Income"]])
show("quarterly_income_stmt", t.quarterly_income_stmt.loc[["Total Revenue", "Net Income"]])
show("balance_sheet", t.balance_sheet.loc[["Total Assets", "Stockholders Equity"]])
show("cashflow", t.cashflow.loc[["Operating Cash Flow", "Free Cash Flow"]])

# 5. Analysten
show("analyst_price_targets", t.analyst_price_targets)
show("recommendations", t.recommendations)

# 6. Ergebnistermine und Eigentümer
show("earnings_dates", t.earnings_dates)
show("major_holders", t.major_holders)

# 7. Mehrere Symbole auf einmal und Wechselkurs
show("download MSFT + SIE.DE", yf.download(["MSFT", "SIE.DE"], period="5d", progress=False))
show("Wechselkurs EURUSD=X", yf.Ticker("EURUSD=X").history(period="5d"))
