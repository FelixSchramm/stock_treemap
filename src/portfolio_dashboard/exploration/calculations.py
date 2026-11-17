"""Financial calculations with explicit periods, missingness and assumptions."""

import numpy as np
import pandas as pd
from scipy.optimize import brentq


def drawdown(series):
    """Return drawdown from the full supplied history, in percentage points.

    :param series: observed prices
    :return: drawdowns
    """
    return (series / series.cummax() - 1) * 100


def monthly_returns(prices, asof):
    """Use only completed calendar months and never forward-fill missing prices.

    :param prices: native-calendar prices
    :param asof: snapshot date
    :return: last twelve completed monthly returns in percentage points
    """
    cutoff = pd.Timestamp(asof).tz_localize(None).to_period("M").start_time
    monthly = prices.resample("ME").last()
    return (
        monthly.loc[monthly.index < cutoff].pct_change(fill_method=None).tail(12) * 100
    )


def trailing_four(frame):
    """Sum exactly four consecutive quarters, never bridge missing periods.

    :param frame: chronological quarterly statement
    :return: trailing-twelve-month amounts
    """
    frame = frame.sort_index()
    complete = (
        frame.index.to_series().diff().dt.days.between(60, 120).rolling(3).sum().eq(3)
    )
    return frame.rolling(4, min_periods=4).sum().where(complete, axis=0)


def dividend_yield(dividends, close):
    """Calculate actual ex-dividends over the prior calendar year / price.

    :param dividends: full ex-dividend history
    :param close: split-consistent USD closes
    :return: trailing yield in percent
    """
    cumulative = dividends.groupby(level=0).sum().sort_index().cumsum()
    end = cumulative.reindex(close.index, method="ffill").fillna(0)
    start = cumulative.reindex(
        close.index - pd.DateOffset(years=1), method="ffill"
    ).fillna(0)
    start.index = close.index
    return (end - start) / close * 100


def roic(income, invested):
    """Calculate NOPAT / mean opening and closing invested capital.

    Uses reported annual effective tax; unusual tax rates remain missing.
    Yahoo invested capital includes goodwill; no excess-cash/R&D restatement.

    :param income: operating income, tax provision and pretax income
    :param invested: invested-capital series with a prior-year observation
    :return: percent ROIC
    """
    tax = income["Tax Provision"] / income["Pretax Income"]
    tax = tax.where(tax.between(0, 1) & income["Pretax Income"].gt(0))
    invested = invested.sort_index()
    consecutive = invested.index.to_series().diff().dt.days.between(330, 400)
    capital = (invested + invested.shift()) / 2
    capital = capital.where(consecutive & capital.gt(0))
    return income["Operating Income"] * (1 - tax) / capital.reindex(income.index) * 100


def valid_calls(calls, spot, now):
    """Reject stale trades, invalid quotes, wide spreads and unusable IV.

    Last trade is only a freshness proxy, not an exchange quote timestamp.

    :param calls: Yahoo call chain
    :param spot: underlying USD close
    :param now: timezone-aware retrieval time
    :return: filtered chain, sorted by strike
    """
    midpoint = (calls["bid"] + calls["ask"]) / 2
    recent = pd.to_datetime(calls["lastTradeDate"], utc=True)
    valid = (
        calls["bid"].gt(0)
        & calls["ask"].ge(calls["bid"])
        & ((calls["ask"] - calls["bid"]) / midpoint).le(0.20)
        & calls["openInterest"].ge(10)
        & calls["strike"].between(spot * 0.8, spot * 1.2)
        & calls["impliedVolatility"].between(0.01, 3)
        & recent.between(now - pd.Timedelta(days=3), now)
    )
    return calls.loc[valid].sort_values("strike")


def equity_value(fcf, growth, discount, terminal=0.025, years=10):
    """Discount a constant-growth equity cash-flow proxy plus terminal value.

    :param fcf: latest annual CFO minus cash capex
    :param growth: annual cash-flow growth assumption
    :param discount: equity required return, greater than terminal growth
    :param terminal: perpetual growth after explicit horizon
    :param years: explicit forecast years
    :return: modeled total equity value in the input currency
    """
    if discount <= terminal or years < 1 or fcf <= 0 or growth <= -1:
        raise ValueError("Invalid DCF assumptions")
    t = np.arange(1, years + 1)
    future = fcf * (1 + growth) ** t
    return (future / (1 + discount) ** t).sum() + future[-1] * (1 + terminal) / (
        discount - terminal
    ) / (1 + discount) ** years


def implied_growth(fcf, market_cap, discount, terminal=0.025, years=10):
    """Solve growth required to match market capitalization; no extrapolation.

    :param fcf: positive base cash flow
    :param market_cap: current equity market value
    :param discount: equity required return
    :param terminal: perpetual growth
    :param years: explicit forecast years
    :return: annual growth, or NaN when outside the [-50%, 100%] search range
    """

    def residual(growth):
        """Return modeled equity value minus the observed market value."""
        return equity_value(fcf, growth, discount, terminal, years) - market_cap

    if residual(-0.5) * residual(1.0) > 0:
        return np.nan
    return brentq(residual, -0.5, 1.0)
