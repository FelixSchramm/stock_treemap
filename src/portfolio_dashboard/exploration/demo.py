"""Invented data for UI tests and offline exploration; never a market snapshot."""

import numpy as np
import pandas as pd

from .data import SYMBOLS


def demo_bundle():
    index = pd.bdate_range("2016-11-01", "2026-10-30")
    t = np.arange(len(index))
    bundle = {
        "retrieved_at": "2026-11-02T12:00:00+00:00",
        "version": "synthetic",
        "synthetic": True,
        "prices": {},
        "errors": {},
    }
    for i, symbol in enumerate(SYMBOLS):
        value = (70 + i * 10) * np.exp(t * 0.0003 + 0.08 * np.sin(t / 60 + i))
        if symbol in ("EURUSD=X", "USDHKD=X"):
            value = (1.1 if symbol == "EURUSD=X" else 7.8) + 0.02 * np.sin(t / 60)
        bundle["prices"][symbol] = pd.DataFrame(
            {
                "Open": value * 0.997,
                "High": value * 1.012,
                "Low": value * 0.985,
                "Close": value,
                "Adj Close": value * np.exp(t * 0.00001),
                "Volume": 1e7 + 1e6 * np.sin(t / 20),
            },
            index=index,
        )
    annual = pd.date_range("2022-06-30", periods=5, freq="YE-JUN")
    quarterly = pd.date_range("2023-09-30", periods=12, freq="QE")

    def statements(periods, quarter=False):
        factor = np.linspace(0.75, 1.1, len(periods)) / (4 if quarter else 1)
        amounts = {
            "Total Revenue": 200e9,
            "Net Income": 55e9,
            "Operating Income": 70e9,
            "Gross Profit": 130e9,
            "Tax Provision": 12e9,
            "Pretax Income": 67e9,
            "Interest Expense": 3e9,
            "Research And Development": 24e9,
            "Diluted Average Shares": 7e9 * (4 if quarter else 1),
            "Diluted EPS": 8,
            "Operating Cash Flow": 90e9,
            "Capital Expenditure": -30e9,
            "Free Cash Flow": 60e9,
            "Cash Dividends Paid": -15e9,
            "Stock Based Compensation": 8e9,
            "Change In Working Capital": -2e9,
            "Repurchase Of Capital Stock": -20e9,
            "Purchase Of Business": -10e9,
            "Issuance Of Debt": 5e9,
            "Repayment Of Debt": -4e9,
            "Invested Capital": 180e9,
            "Total Debt": 75e9,
            "Cash Cash Equivalents And Short Term Investments": 65e9,
            "Capital Lease Obligations": 18e9,
        }
        return pd.DataFrame(
            {
                p: {key: value * factor[i] for key, value in amounts.items()}
                for i, p in enumerate(periods)
            }
        )

    for key in ("income", "cash", "balance"):
        bundle[key] = statements(annual)
    for key in ("quarter_income", "quarter_cash"):
        bundle[key] = statements(quarterly, True)
    eps_dates = pd.date_range(
        "2022-01-25 16:30", periods=19, freq="3MS", tz="America/New_York"
    ) + pd.Timedelta(days=24)
    bundle["earnings"] = pd.DataFrame(
        {
            "Reported EPS": np.linspace(2, 4, 19),
            "EPS Estimate": np.linspace(2.1, 3.8, 19),
        },
        index=eps_dates,
    )
    bundle["dividends"] = pd.Series(
        np.linspace(0.4, 0.9, 39),
        index=pd.date_range("2017-02-15", periods=39, freq="3MS"),
        name="Dividends",
    )
    spot = bundle["prices"]["MSFT"].Close.iloc[-1]
    bundle["info"] = {
        "financialCurrency": "USD",
        "currency": "USD",
        "marketCap": spot * 7e9,
        "sharesOutstanding": 7e9,
    }
    bundle["targets"] = {
        "low": spot * 0.8,
        "mean": spot * 1.1,
        "median": spot * 1.08,
        "high": spot * 1.3,
        "current": spot,
    }
    bundle["recommendations"] = pd.DataFrame(
        {
            "period": ["-2m", "-1m", "0m"],
            "strongBuy": [8, 9, 10],
            "buy": [15, 14, 16],
            "hold": [6, 5, 4],
            "sell": [1, 2, 1],
            "strongSell": [1, 1, 1],
        }
    )
    event_dates = pd.date_range("2026-02-01", periods=6, freq="MS")
    bundle["actions"] = pd.DataFrame(
        {
            "Firm": [f"Demo Research {i}" for i in range(6)],
            "priorPriceTarget": spot * np.linspace(0.9, 1.1, 6),
            "currentPriceTarget": spot * np.linspace(1, 1.2, 6),
        },
        index=event_dates,
    )
    bundle["insiders"] = pd.DataFrame(
        {
            "Start Date": event_dates,
            "Text": ["Sale", "Purchase", "Award", "Sale", "Purchase", "Sale"],
            "Value": [2e6, 3e6, np.nan, 1e6, 2e6, 0],
        }
    )
    bundle["holders"] = pd.DataFrame(
        {
            "Holder": [
                "Demo Institution A",
                "Demo Institution B",
                "Demo Institution C",
            ],
            "pctHeld": [0.06, 0.04, 0.03],
            "pctChange": [0.05, -0.03, 0.01],
            "Date Reported": [pd.Timestamp("2026-06-30")] * 3,
        }
    )
    for key, scale in (("estimates", 1), ("revenue_estimates", 1e10)):
        bundle[key] = pd.DataFrame(
            {
                "low": np.array([10, 11]) * scale,
                "avg": np.array([12, 13]) * scale,
                "high": np.array([14, 15]) * scale,
                "numberOfAnalysts": [30, 28],
            },
            index=["0y", "+1y"],
        )
    bundle["eps_trend"] = pd.DataFrame(
        {
            "90daysAgo": [11, 12],
            "60daysAgo": [11.5, 12.5],
            "30daysAgo": [11.8, 12.8],
            "7daysAgo": [11.9, 12.9],
            "current": [12, 13],
        },
        index=["0y", "+1y"],
    )
    bundle["eps_revisions"] = pd.DataFrame(
        {"upLast30days": [5, 6], "downLast30days": [2, 3]}, index=["0y", "+1y"]
    )
    bundle["valuation"] = pd.DataFrame(
        {
            "2024-06-30": [28, 25, 20],
            "2025-06-30": [30, 26, 22],
            "2026-06-30": [29, 24, 21],
            "Current": [31, 27, 23],
        },
        index=["Trailing P/E", "Forward P/E", "Enterprise Value/EBITDA"],
    )
    bundle["options"] = {}
    for days in (7, 30, 90):
        expiry = (
            pd.Timestamp(bundle["retrieved_at"]) + pd.Timedelta(days=days)
        ).strftime("%Y-%m-%d")
        bundle["options"][expiry] = pd.DataFrame(
            {
                "strike": spot * np.linspace(0.85, 1.15, 7),
                "bid": [10] * 7,
                "ask": [11] * 7,
                "openInterest": [100] * 7,
                "impliedVolatility": np.array(
                    [0.34, 0.31, 0.29, 0.28, 0.29, 0.31, 0.34]
                )
                + days / 10000,
                "lastTradeDate": [
                    pd.Timestamp(bundle["retrieved_at"]) - pd.Timedelta(days=1)
                ]
                * 7,
            }
        )
    bundle["segments"] = pd.DataFrame(
        [
            {
                "year": year,
                "segment": segment,
                "revenue_million_usd": amount * (1 + (year - 2024) * 0.1),
                "operating_income_million_usd": amount
                * 0.4
                * (1 + (year - 2024) * 0.1),
            }
            for year in (2024, 2025, 2026)
            for segment, amount in [
                ("Demo · Produktivität", 80000),
                ("Demo · Cloud", 70000),
                ("Demo · Geräte", 30000),
            ]
        ]
    )
    return bundle
