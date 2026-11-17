"""Offline tests for the technical signals of issue #144 (synthetic closes only)."""

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_dashboard import technicals
from portfolio_dashboard.technicals import (
    INSUFFICIENT_HISTORY,
    NO_SUPPORT_BELOW,
    CrossSignal,
    SupportCandidate,
    SupportMethod,
    Trend,
    next_support,
    rsi,
    support_zones,
    swing_lows,
    swing_zones,
    technical_signals,
    unavailable_signals,
)

D = Decimal


def series(*values) -> tuple[Decimal, ...]:
    return tuple(D(str(v)) for v in values)


def linear(start, step, count) -> tuple[Decimal, ...]:
    return tuple(D(str(start)) + D(str(step)) * i for i in range(count))


def test_module_is_pure_and_thresholds_are_named_constants():
    tree = ast.parse(Path(technicals.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not {
        name for name in imported
        if name.split(".")[0] in {"streamlit", "yfinance", "pandas"}
        or name.endswith("quote_cache")
    }
    for name in (
        "SWING_WINDOW", "SWING_MIN_DAYS", "MIN_SWING_TOUCHES", "ZONE_TOLERANCE",
        "SMA_SHORT", "SMA_MEDIUM", "SMA_LONG", "LONG_MIN_DAYS", "YEAR_TRADING_DAYS",
        "FIBONACCI_LEVELS", "TREND_SLOPE_LOOKBACK", "BOTTOM_NO_NEW_LOW_DAYS",
        "BOTTOM_MAX_ABOVE_LOW", "CROSS_LOOKBACK", "RSI_PERIOD", "RSI_OVERSOLD",
        "RSI_OVERBOUGHT", "MIN_CONFLUENCE_STRENGTH",
    ):
        assert hasattr(technicals, name)


# --- Trend classes -----------------------------------------------------------


def test_rising_series_is_an_uptrend():
    signals = technical_signals(linear(100, "0.5", 260), "USD")
    assert signals.trend is Trend.UP
    assert signals.trend_reason is None
    assert signals.current_price == D("229.5")
    assert signals.currency == "USD"
    assert signals.sma20 > signals.sma50 > signals.sma200
    # The current price is the 52-week high.
    assert signals.high_distance_percent == 0
    assert signals.cross is None


def test_falling_series_is_a_downtrend():
    signals = technical_signals(linear(300, "-0.5", 260))
    assert signals.trend is Trend.DOWN
    assert signals.high_distance_percent < 0


def test_flat_series_is_sideways():
    signals = technical_signals((D(100),) * 260)
    assert signals.trend is Trend.SIDEWAYS
    assert signals.trend_rule.startswith("keine der Regeln")


def test_oscillating_series_without_direction_is_sideways():
    # A 10-day wave: SMA20, SMA50 and SMA200 all equal its mean, lows repeat.
    wave = (100, 102, 104, 105, 104, 102, 100, 98, 96, 95)
    closes = series(*(wave * 26))
    signals = technical_signals(closes)
    assert signals.trend is Trend.SIDEWAYS


def test_decline_with_holding_low_is_bottoming():
    decline = linear(200, "-0.4545", 220)  # 200 down to about 100
    dip = linear("99.5", "-0.45", 10)  # new 52-week low near 95.5
    recovery = linear("95.5", "0.25", 30)  # back up to about 102.75
    closes = decline + dip + recovery
    signals = technical_signals(closes)
    assert signals.trend is Trend.BOTTOMING
    assert "52-Wochen-Tief" in signals.trend_rule
    assert signals.current_price < signals.sma200
    assert signals.current_price > signals.sma20


def test_bottoming_needs_the_low_to_be_older_than_twenty_days():
    decline = linear(200, "-0.4545", 240)
    rebound = linear("90.9", "0.2", 10)  # low only ten days ago
    signals = technical_signals(decline + rebound)
    assert signals.trend is not Trend.BOTTOMING


def test_short_history_has_no_trend_but_short_averages_and_rsi():
    signals = technical_signals(linear(50, "0.1", 150))
    assert signals.trend is None
    assert signals.trend_reason == INSUFFICIENT_HISTORY
    assert signals.sma200 is None
    assert signals.year_low is None and signals.year_high is None
    assert signals.high_distance_percent is None
    assert signals.high_reason == INSUFFICIENT_HISTORY
    assert signals.sma20 is not None and signals.sma50 is not None
    assert signals.rsi is not None and signals.rsi_reason is None
    methods = {c.method for zone in signals.zones for c in zone.candidates}
    assert SupportMethod.FIBONACCI not in methods
    assert SupportMethod.YEAR_LOW not in methods


def test_very_short_history_is_not_available():
    signals = technical_signals(linear(50, "0.1", 10))
    assert signals.sma20 is None
    assert signals.rsi is None and signals.rsi_reason == INSUFFICIENT_HISTORY
    assert signals.next_support is None
    assert signals.support_reason == INSUFFICIENT_HISTORY


def test_missing_history_is_not_available_with_reason():
    signals = technical_signals(())
    assert signals.trend is None and signals.trend_reason == "history_unavailable"
    unmapped = unavailable_signals("unverified_instrument")
    assert {unmapped.trend_reason, unmapped.support_reason, unmapped.rsi_reason,
            unmapped.high_reason} == {"unverified_instrument"}


# --- Swing lows and confluence ----------------------------------------------


def _dips(base, *lows):
    closes = [base] * 10
    for low in lows:
        closes += [base - 5, low, base - 5] + [base] * 10
    return series(*closes)


def test_three_close_swing_lows_form_one_zone_with_three_touches():
    lows = swing_lows(_dips(110, 100, 101, "100.5"))
    assert lows == list(series(100, 101, "100.5"))
    zones = swing_zones(lows)
    assert len(zones) == 1
    assert zones[0].touches == 3
    assert zones[0].price == D("100.5")


def test_lows_ten_percent_apart_form_two_zones():
    zones = swing_zones(swing_lows(_dips(120, 100, 110)))
    assert [(zone.price, zone.touches) for zone in zones] == [(D(100), 1), (D(110), 1)]


def test_flat_runs_do_not_count_as_several_swing_lows():
    assert swing_lows((D(100),) * 30) == []


def test_confluence_beats_a_closer_single_method_zone():
    candidates = (
        SupportCandidate(D("100"), SupportMethod.SWING_LOW, "Swing-Tief (2 Berührungen)"),
        SupportCandidate(D("100.8"), SupportMethod.MOVING_AVERAGE, "SMA200"),
        SupportCandidate(D("99.5"), SupportMethod.FIBONACCI, "Fibonacci 61,8 %"),
        SupportCandidate(D("108"), SupportMethod.MOVING_AVERAGE, "SMA20"),
        SupportCandidate(D("115"), SupportMethod.MOVING_AVERAGE, "SMA50"),  # above
    )
    zones = support_zones(candidates, current=D("110"))
    assert [zone.strength for zone in zones] == [1, 3]
    assert zones[0].price == D("108")
    chosen = next_support(zones)
    assert chosen.strength == 3
    assert {c.label for c in chosen.candidates} == {
        "Swing-Tief (2 Berührungen)", "SMA200", "Fibonacci 61,8 %",
    }


def test_without_confluence_the_nearest_single_zone_is_used():
    candidates = (
        SupportCandidate(D("90"), SupportMethod.MOVING_AVERAGE, "SMA200"),
        SupportCandidate(D("105"), SupportMethod.MOVING_AVERAGE, "SMA20"),
    )
    chosen = next_support(support_zones(candidates, current=D("110")))
    assert chosen.price == D("105") and chosen.strength == 1


def test_signals_only_report_supports_below_the_price():
    signals = technical_signals(linear(100, "0.5", 260))
    assert signals.zones
    assert all(zone.price < signals.current_price for zone in signals.zones)
    assert [z.price for z in signals.zones] == sorted(
        (z.price for z in signals.zones), reverse=True
    )
    assert signals.next_support in signals.zones
    assert signals.support_distance_percent < 0


def test_new_all_time_low_has_no_support_below():
    signals = technical_signals(linear(300, "-0.5", 260))
    assert signals.next_support is None
    assert signals.zones == ()
    assert signals.support_reason == NO_SUPPORT_BELOW
    assert signals.support_distance_percent is None


# --- RSI and crossings -------------------------------------------------------

# Reference series of Wilder's RSI example (as published by StockCharts):
# 15 closes give the first RSI 70.46, 20 closes give 57.92.
RSI_REFERENCE = series(
    "44.34", "44.09", "44.15", "43.61", "44.33", "44.83", "45.10", "45.42", "45.84",
    "46.08", "45.89", "46.03", "45.61", "46.28", "46.28", "46.00", "46.03", "46.41",
    "46.22", "45.64",
)


def test_rsi_matches_the_documented_reference_values():
    assert round(rsi(RSI_REFERENCE[:15]), 2) == D("70.46")
    assert round(rsi(RSI_REFERENCE), 2) == D("57.92")
    assert rsi(RSI_REFERENCE[:14]) is None


@pytest.mark.parametrize(
    ("closes", "label"),
    [
        (linear(100, "-1", 30), "überverkauft"),
        (linear(100, "1", 30), "überkauft"),
        ((D(100),) * 30, "neutral"),
    ],
)
def test_rsi_classification(closes, label):
    assert technical_signals(closes).rsi_label == label


def test_golden_and_death_cross_within_twenty_days():
    golden = (D(100),) * 240 + linear(101, 1, 10)
    assert technical_signals(golden).cross is CrossSignal.GOLDEN
    death = (D(100),) * 240 + linear(99, -1, 10)
    assert technical_signals(death).cross is CrossSignal.DEATH
    old = (D(100),) * 200 + linear(101, 1, 10) + linear(111, "0.1", 40)
    assert technical_signals(old).cross is None
