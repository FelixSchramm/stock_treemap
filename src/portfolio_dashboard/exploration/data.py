"""Yahoo snapshots for explicitly mapped Sheet companies; no import-time I/O."""

import re

import pandas as pd

from .selection import Company

# Explicit FX conventions: series contains quote currency per USD, except EUR/GBP.
FX = {
    "EUR": ("EURUSD=X", False),
    "GBP": ("GBPUSD=X", False),
    "HKD": ("USDHKD=X", True),
    "JPY": ("USDJPY=X", True),
    "CHF": ("USDCHF=X", True),
    "CAD": ("USDCAD=X", True),
    "AUD": ("AUDUSD=X", False),
    "CNY": ("USDCNY=X", True),
    "SEK": ("USDSEK=X", True),
    "DKK": ("USDDKK=X", True),
    "NOK": ("USDNOK=X", True),
}


# Resources where an empty result is a valid answer rather than a failure.
EMPTY_IS_ANSWER = {"dividends"}


def dates(frame):
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
    return frame.sort_index()


def currency_code(value):
    return (
        value if isinstance(value, str) and re.fullmatch(r"[A-Z]{3}", value) else None
    )


def fetch(company: Company, comparisons=()):
    """Fetch only selected, metadata-checked listings and explicit US benchmarks.

    Public presets and Sheet mappings choose symbol/venue/currency. Metadata must agree;
    errors never fall back to Microsoft or to another company's cached snapshot.
    """
    import yfinance as yf

    if company.reason or not company.symbol:
        raise ValueError("Yahoo-Zuordnung fehlt.")
    now = pd.Timestamp.now(tz="UTC")
    selected = tuple({c.key: c for c in (company, *comparisons)}.values())
    if len(selected) > 4 or any(c.reason or not c.symbol for c in selected):
        raise ValueError("Ungültige Vergleichsauswahl.")
    if len({c.symbol for c in selected}) != len(selected):
        raise ValueError("Widersprüchliche Zuordnungen desselben Yahoo-Symbols.")
    bundle = {
        "retrieved_at": now.isoformat(),
        "version": yf.__version__,
        "symbol": company.symbol,
        "name": company.name,
        "exchange": company.exchange,
        "equity_form": company.equity_form.value if company.equity_form else None,
        "comparison_symbols": tuple(c.symbol for c in selected),
        "names": {c.symbol: f"{c.name} ({c.symbol})" for c in selected},
        "currencies": {},
        "prices": {},
        "errors": {},
        "synthetic": False,
    }
    stocks = {}
    for item in selected:
        try:
            stock = yf.Ticker(item.symbol)
            info = stock.info
            if (
                info.get("symbol") != item.symbol
                or info.get("quoteType") != "EQUITY"
                or info.get("exchange") != item.exchange
                or currency_code(info.get("currency")) != item.currency
                or item.scale is None
                or item.scale != 1
            ):
                raise ValueError("metadata mismatch")
            frame = dates(
                stock.history(period="10y", auto_adjust=False, timeout=15)
            ).dropna(subset=["Close"])
            if frame.empty:
                raise ValueError("empty")
            bundle["prices"][item.symbol] = frame
            bundle["currencies"][item.symbol] = item.currency
            stocks[item.symbol] = stock
            if item.symbol == company.symbol:
                bundle["info"] = info
        except Exception:
            bundle["errors"][item.symbol] = (
                "n. v. – Kurse fehlen oder Yahoo-Metadaten passen nicht zur Sheet-Zuordnung."
            )
    if company.symbol not in stocks:
        return bundle
    currencies = set(bundle["currencies"].values()) | {"EUR"}
    extra = {"SPY", "QQQ", "^GSPC", "^NDX"} | {FX[c][0] for c in currencies if c in FX}
    for symbol in sorted(extra):
        try:
            frame = dates(
                yf.Ticker(symbol).history(period="10y", auto_adjust=False, timeout=15)
            ).dropna(subset=["Close"])
            if frame.empty:
                raise ValueError("empty")
            bundle["prices"][symbol] = frame
            if symbol in {"SPY", "QQQ", "^GSPC", "^NDX"}:
                bundle["currencies"][symbol] = "USD"
        except Exception:
            bundle["errors"][symbol] = (
                "n. v. – Benchmark- oder FX-Reihe nicht verfügbar."
            )
    stock = stocks[company.symbol]
    calls = {
        "income": lambda: stock.income_stmt,
        "quarter_income": lambda: stock.quarterly_income_stmt,
        "balance": lambda: stock.balance_sheet,
        "cash": lambda: stock.cashflow,
        "quarter_cash": lambda: stock.quarterly_cashflow,
        "earnings": lambda: stock.get_earnings_dates(limit=48),
        "dividends": lambda: stock.dividends,
        "targets": lambda: stock.analyst_price_targets,
        "recommendations": lambda: stock.recommendations,
        "actions": lambda: stock.upgrades_downgrades,
        "insiders": lambda: stock.insider_transactions,
        "holders": lambda: stock.institutional_holders,
        "major": lambda: stock.major_holders,
        "estimates": lambda: stock.earnings_estimate,
        "revenue_estimates": lambda: stock.revenue_estimate,
        "eps_trend": lambda: stock.eps_trend,
        "eps_revisions": lambda: stock.eps_revisions,
        "valuation": lambda: stock.get_valuation_measures(periods=None),
        "filings": lambda: stock.sec_filings,
    }
    for name, function in calls.items():
        try:
            value = function()
            # An empty dividend history is an answer: the company pays none.
            if value is None or (len(value) == 0 and name not in EMPTY_IS_ANSWER):
                raise ValueError("Yahoo returned no data")
            bundle[name] = value
        except Exception:  # noqa: BLE001
            bundle["errors"][name] = "Anbieterressource nicht verfügbar."

    try:
        available = [
            d
            for d in stock.options
            if (pd.Timestamp(d) - now.tz_localize(None).normalize()).days >= 7
        ]
        chains = {}
        for horizon in (7, 30, 90):
            if not available:
                break
            expiry = min(
                available,
                key=lambda d: abs(
                    (pd.Timestamp(d) - now.tz_localize(None)).days - horizon
                ),
            )
            available.remove(expiry)
            chains[expiry] = stock.option_chain(expiry).calls
        bundle["options"] = chains
    except Exception:
        bundle["errors"]["options"] = "n. v. – Optionsdaten nicht verfügbar."
    return bundle


def statement(bundle, name, columns):
    """Chronological reported amounts in financialCurrency; missing stays missing."""
    return bundle[name].reindex(columns).T.sort_index().dropna(how="all")


def prices(bundle, symbols=None, currency="EUR", total=True):
    """Explicit native currencies; past-only FX, maximum seven calendar days."""
    if currency not in ("EUR", "USD"):
        raise ValueError("Display currency must be EUR or USD")
    symbols = symbols if symbols is not None else bundle["comparison_symbols"]
    raw = bundle["prices"]
    result = {}
    for symbol in symbols:
        series = raw[symbol]["Adj Close" if total else "Close"].copy()
        series = series.where(series.gt(0) & series.lt(float("inf")))
        native = bundle["currencies"][symbol]

        def usd_per_unit(code):
            if code == "USD":
                return 1.0
            pair, inverse = FX[code]
            rate = raw[pair]["Close"].reindex(
                series.index, method="ffill", tolerance=pd.Timedelta(days=7)
            )
            rate = rate.where(rate.gt(0) & rate.lt(float("inf")))
            return 1 / rate if inverse else rate

        if native != currency:
            series = series * usd_per_unit(native) / usd_per_unit(currency)
        result[symbol] = series
    return pd.concat(result, axis=1)
