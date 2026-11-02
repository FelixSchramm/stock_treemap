"""V4 snapshot adapter. No import-time I/O or persistent provider-response cache."""

import pandas as pd

TICKERS = ("ORCL", "MSFT", "9988.HK", "SAP.DE")
NAMES = {
    "ORCL": "Oracle",
    "MSFT": "Microsoft",
    "9988.HK": "Alibaba HK",
    "SAP.DE": "SAP",
}
SYMBOLS = (*TICKERS, "EURUSD=X", "USDHKD=X", "^GSPC", "^NDX", "SPY", "QQQ")


def dates(frame):
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
    return frame.sort_index()


def fetch():
    """Called only by the authorized UI; resources can fail independently."""
    import yfinance as yf

    now = pd.Timestamp.now(tz="UTC")
    bundle = {
        "retrieved_at": now.isoformat(),
        "version": yf.__version__,
        "prices": {},
        "errors": {},
        "synthetic": False,
    }
    for symbol in SYMBOLS:
        try:
            frame = dates(
                yf.Ticker(symbol).history(period="10y", auto_adjust=False, timeout=15)
            )
            if frame.empty:
                raise ValueError("empty")
            bundle["prices"][symbol] = frame.dropna(subset=["Close"])
        except Exception:
            bundle["errors"][symbol] = "Kursreihe nicht verfügbar."
    stock = yf.Ticker("MSFT")
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
        "info": lambda: stock.info,
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
            if value is None or len(value) == 0:
                raise ValueError("Yahoo returned no data")
            bundle[name] = value
        except Exception:  # noqa: BLE001
            bundle["errors"][name] = "Anbieterressource nicht verfügbar."
    try:
        expiries = list(stock.options)
        available = [
            d
            for d in expiries
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
    except Exception:  # noqa: BLE001
        bundle["errors"]["options"] = "Anbieterressource nicht verfügbar."
    return bundle


def statement(bundle, name, columns):
    """Select statement rows, keeping unavailable items as missing.

    :param bundle: fetched snapshot
    :param name: statement resource name
    :param columns: requested Yahoo line items
    :return: chronological amounts in original USD units
    """
    return bundle[name].reindex(columns).T.sort_index().dropna(how="all")


def prices(bundle, symbols=TICKERS, currency="EUR", total=True):
    """Convert price or adjusted-close series into one display currency.

    FX gaps are filled only from prior observations, for at most seven days.
    Listing calendars stay native. Adjusted closes approximate gross total return.

    :param bundle: fetched snapshot
    :param symbols: requested listings/benchmarks
    :param currency: EUR or USD
    :param total: use adjusted closes if true
    :return: outer-aligned daily prices
    """
    if currency not in ("EUR", "USD"):
        raise ValueError("Display currency must be EUR or USD")
    raw = bundle["prices"]
    result = {}
    for symbol in symbols:
        series = raw[symbol]["Adj Close" if total else "Close"].copy()
        series = series.where(series.gt(0) & series.lt(float("inf")))
        native = (
            "EUR" if symbol == "SAP.DE" else "HKD" if symbol == "9988.HK" else "USD"
        )

        def fx(name):
            rate = raw[name]["Close"].reindex(
                series.index, method="ffill", tolerance=pd.Timedelta(days=7)
            )
            return rate.where(rate.gt(0) & rate.lt(float("inf")))

        if native == "HKD":
            series = series / fx("USDHKD=X")
            native = "USD"
        if native != currency:
            rate = fx("EURUSD=X")
            series = series / rate if currency == "EUR" else series * rate
        result[symbol] = series
    return pd.concat(result, axis=1)
