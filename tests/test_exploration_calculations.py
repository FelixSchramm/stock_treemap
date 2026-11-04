"""Independent financial invariants retained from the V4 analysis."""

import unittest

import numpy as np
import pandas as pd

from portfolio_dashboard.exploration import calculations as calc


class V4Finance(unittest.TestCase):
    """Verify investor-relevant invariants with independently checkable examples."""

    def test_dcf_constant_perpetuity_and_inverse(self):
        """Zero growth on both horizons must reduce to FCF / discount rate."""
        self.assertAlmostEqual(calc.equity_value(100, 0, 0.1, terminal=0), 1000)
        value = calc.equity_value(67, 0.12, 0.1)
        self.assertAlmostEqual(calc.implied_growth(67, value, 0.1), 0.12)
        self.assertGreater(calc.equity_value(67, 0.12, 0.08), value)
        with self.assertRaises(ValueError):
            calc.equity_value(100, 0.1, 0.02, terminal=0.025)

    def test_roic_uses_average_capital_and_requires_prior_year(self):
        """Opening 100, closing 200 and NOPAT 30 produce a 20% return."""
        dates = pd.to_datetime(["2024-06-30", "2025-06-30"])
        income = pd.DataFrame(
            {
                "Operating Income": [40, 40],
                "Tax Provision": [10, 10],
                "Pretax Income": [40, 40],
            },
            index=dates,
        )
        capital = pd.Series([100, 200], index=dates)
        actual = calc.roic(income, capital)
        self.assertTrue(np.isnan(actual.iloc[0]))
        self.assertAlmostEqual(actual.iloc[1], 20)
        capital.index = pd.to_datetime(["2023-06-30", "2025-06-30"])
        self.assertTrue(calc.roic(income, capital).isna().all())

    def test_ttm_does_not_sum_four_nonconsecutive_quarters(self):
        """A skipped quarter must invalidate rolling-four results."""
        frame = pd.DataFrame(
            {"CF": [1, 2, 3, 4]},
            index=pd.to_datetime(
                ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
            ),
        )
        self.assertEqual(calc.trailing_four(frame).iloc[-1, 0], 10)
        frame.index = pd.to_datetime(
            ["2025-03-31", "2025-06-30", "2025-12-31", "2026-03-31"]
        )
        self.assertTrue(calc.trailing_four(frame).isna().all().all())

    def test_prior_high_is_not_reset_at_display_start(self):
        """A past high of 200 remains the reference for a displayed 100 price."""
        frame = pd.Series([200, 100, 150], index=pd.date_range("2024-01-01", periods=3))
        np.testing.assert_allclose(calc.drawdown(frame).iloc[1:], [-50, -25])

    def test_option_spread_crossed_market_and_staleness(self):
        """A valid IV is insufficient when the option cannot be sensibly quoted."""
        now = pd.Timestamp("2026-11-01", tz="UTC")
        calls = pd.DataFrame(
            {
                "strike": [100] * 5,
                "bid": [10, 0, 12, 5, 10],
                "ask": [11, 11, 10, 15, 11],
                "openInterest": [20] * 5,
                "impliedVolatility": [0.3] * 5,
                "lastTradeDate": [now - pd.Timedelta(days=d) for d in [1, 1, 1, 1, 5]],
            }
        )
        self.assertEqual(list(calc.valid_calls(calls, 100, now).index), [0])

    def test_complete_months_preserve_missing_month(self):
        """Current month and unknown returns cannot appear as completed returns."""
        frame = pd.DataFrame(
            {"x": [100, 110, np.nan, 121, 150]},
            index=pd.to_datetime(
                ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-01"]
            ),
        )
        result = calc.monthly_returns(frame, "2026-05-15")
        self.assertEqual(result.index[-1], pd.Timestamp("2026-04-30"))
        self.assertAlmostEqual(result.iloc[1, 0], 10)
        self.assertTrue(result.iloc[-2:].isna().all().all())
