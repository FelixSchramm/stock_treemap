"""Synthetic sessions and prices only; injected clock, no network."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from portfolio_dashboard.market_data import (
    AdjustmentBasis,
    HistoryPoint,
    PriceHistory,
    PriceObservation,
)
from portfolio_dashboard.models import (
    Provenance,
    QuoteUnit,
    Valuation,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.price_periods import (
    PricePeriod,
    _target,
    price_period_returns,
    session_calendar,
)

USD = QuoteUnit("USD", Decimal(1))
EUR = QuoteUnit("EUR", Decimal(1))
BASIS = AdjustmentBasis.YAHOO_SPLIT_ADJUSTED_PRICE
NY = "America/New_York"
OPEN, CLOSE = time(9, 30), time(16)


def sessions(holidays=(), start=date(2024, 1, 1), end=date(2029, 12, 31)):
    days = (start + timedelta(days=n) for n in range((end - start).days + 1))
    return session_calendar(
        (d for d in days if d.weekday() < 5 and d not in holidays), OPEN, CLOSE
    )


def history(
    cal,
    *,
    start=date(2024, 1, 1),
    skip=(),
    overrides=None,
    basis=BASIS,
    split_on=None,
    tz=NY,
    unit=USD,
):
    overrides = overrides or {}
    points = tuple(
        HistoryPoint(
            d, Decimal(overrides.get(d, 100)), Decimal(2 if d == split_on else 0)
        )
        for d in sorted(cal.sessions)
        if start <= d and d not in skip
    )
    retrieved = datetime(2029, 1, 1, tzinfo=UTC)
    return PriceHistory(
        ValueStatus.AVAILABLE,
        Provenance(ValuationSource.PROVIDER, retrieved, None, "synthetic"),
        unit,
        tz,
        basis,
        points,
    )


def quote(price, observed, unit=USD, basis=BASIS):
    prov = Provenance(ValuationSource.PROVIDER, observed, observed, "synthetic")
    return PriceObservation(
        Valuation(Decimal(price), ValueStatus.AVAILABLE, prov, unit), basis
    )


def ny(*args):
    return datetime(*args, tzinfo=ZoneInfo(NY))


def test_closed_market_uses_last_session_close_against_previous_close():
    cal = sessions()
    h = history(cal, overrides={date(2026, 10, 22): 100, date(2026, 10, 23): 103})
    r = price_period_returns(h, None, cal, ny(2026, 10, 23, 17))[PricePeriod.TODAY]
    assert (r.market_open, r.current_date, r.reference_date) == (
        False,
        date(2026, 10, 23),
        date(2026, 10, 22),
    )
    assert r.percent == Decimal(3) and r.unit == USD


def test_running_market_uses_quote_against_previous_close():
    cal = sessions()
    h = history(cal, overrides={date(2026, 10, 22): 200, date(2026, 10, 23): 1})
    q = quote(190, ny(2026, 10, 23, 10, 58))
    r = price_period_returns(h, q, cal, ny(2026, 10, 23, 11))[PricePeriod.TODAY]
    assert r.market_open is True and r.percent == Decimal(-5)
    assert r.reference_date == date(2026, 10, 22)  # incomplete today bar ignored


@pytest.mark.parametrize("kind", ["none", "old", "unit", "basis"])
def test_running_market_without_usable_quote_is_not_available(kind):
    cal = sessions()
    quotes = {
        "none": None,
        "old": quote(100, ny(2026, 10, 22, 15)),
        "unit": quote(100, ny(2026, 10, 23, 10), unit=EUR),
        "basis": quote(100, ny(2026, 10, 23, 10), basis=None),
    }
    out = price_period_returns(history(cal), quotes[kind], cal, ny(2026, 10, 23, 11))
    assert all(not r.available and r.reason for r in out.values())


@pytest.mark.parametrize(
    "now",
    [
        ny(2026, 10, 24, 12),  # Saturday
        ny(2026, 10, 25, 23),  # Sunday
        ny(2026, 10, 26, 8),  # Monday before open
    ],
)
def test_weekend_and_before_open_show_last_finished_session(now):
    cal = sessions()
    r = price_period_returns(history(cal), None, cal, now)[PricePeriod.TODAY]
    assert r.current_date == date(2026, 10, 23) and r.market_open is False


def test_holiday_session_is_skipped_for_today_and_week():
    cal = sessions(holidays={date(2026, 10, 22), date(2026, 10, 16)})
    h = history(
        cal,
        overrides={
            date(2026, 10, 21): 50,
            date(2026, 10, 15): 80,
            date(2026, 10, 23): 100,
        },
    )
    out = price_period_returns(h, None, cal, ny(2026, 10, 23, 17))
    assert out[PricePeriod.TODAY].reference_date == date(2026, 10, 21)
    week = out[PricePeriod.WEEK]
    assert week.target_date == date(2026, 10, 16)
    assert week.reference_date == date(2026, 10, 15)
    assert week.percent == Decimal(25)


def test_exchange_timezone_decides_the_local_session_date():
    cal = session_calendar(sessions().sessions, time(9), time(15))
    h = history(cal, tz="Asia/Tokyo")
    # 23:00 UTC on the 22nd is 08:00 JST on the 23rd: before open.
    before = price_period_returns(h, None, cal, datetime(2026, 10, 22, 23, tzinfo=UTC))
    assert before[PricePeriod.TODAY].current_date == date(2026, 10, 22)
    # 07:00 UTC on the 23rd is 16:00 JST: the 23rd has closed.
    after = price_period_returns(h, None, cal, datetime(2026, 10, 23, 7, tzinfo=UTC))
    assert after[PricePeriod.TODAY].current_date == date(2026, 10, 23)


def test_naive_now_is_rejected():
    cal = sessions()
    with pytest.raises(ValueError):
        price_period_returns(history(cal), None, cal, datetime(2026, 10, 23, 17))


@pytest.mark.parametrize(
    ("period", "current", "target"),
    [
        (PricePeriod.WEEK, date(2026, 3, 3), date(2026, 2, 24)),
        (PricePeriod.MONTH, date(2026, 3, 31), date(2026, 2, 28)),
        (PricePeriod.MONTH, date(2028, 3, 31), date(2028, 2, 29)),
        (PricePeriod.MONTH, date(2026, 1, 31), date(2025, 12, 31)),
        (PricePeriod.MONTH, date(2026, 5, 31), date(2026, 4, 30)),
        (PricePeriod.YEAR, date(2028, 2, 29), date(2027, 2, 28)),
        (PricePeriod.YEAR, date(2026, 10, 23), date(2025, 10, 23)),
    ],
)
def test_calendar_targets_clamp_month_end_and_leap_day(period, current, target):
    assert _target(period, current) == target


def test_month_end_reference_falls_back_to_last_close_at_or_before_target():
    # 2026-02-28 is a Saturday: the reference is Friday the 27th.
    cal = sessions()
    h = history(cal, overrides={date(2026, 2, 27): 50, date(2026, 3, 31): 55})
    r = price_period_returns(h, None, cal, ny(2026, 3, 31, 17))[PricePeriod.MONTH]
    assert (r.target_date, r.reference_date) == (date(2026, 2, 28), date(2026, 2, 27))
    assert r.percent == Decimal(10)


def test_leap_day_year_reference():
    # 2027-02-28 is a Sunday: the reference is Friday the 26th.
    cal = sessions()
    h = history(cal, overrides={date(2027, 2, 26): 80, date(2028, 2, 29): 100})
    r = price_period_returns(h, None, cal, ny(2028, 2, 29, 17))[PricePeriod.YEAR]
    assert (r.target_date, r.reference_date) == (date(2027, 2, 28), date(2027, 2, 26))
    assert r.percent == Decimal(25)


def test_split_event_does_not_create_a_jump_or_get_adjusted_twice():
    cal = sessions()
    h = history(cal, split_on=date(2026, 10, 20))  # closes already comparable
    out = price_period_returns(h, None, cal, ny(2026, 10, 23, 17))
    assert out[PricePeriod.WEEK].percent == 0
    assert out[PricePeriod.TODAY].percent == 0


def test_unknown_adjustment_basis_is_not_available():
    cal = sessions()
    h = history(cal, basis=None)
    out = price_period_returns(h, None, cal, ny(2026, 10, 23, 17))
    assert {r.reason for r in out.values()} == {"unknown_adjustment_basis"}


def test_return_is_price_only_in_trading_currency_units():
    pence = QuoteUnit("GBP", Decimal("0.01"))
    cal = sessions()
    h = history(
        cal,
        unit=pence,
        overrides={date(2026, 10, 22): 100, date(2026, 10, 23): 101},
    )
    r = price_period_returns(h, None, cal, ny(2026, 10, 23, 17))[PricePeriod.TODAY]
    assert r.percent == Decimal(1) and r.unit == pence


def test_insufficient_history_for_new_listing():
    cal = sessions()
    h = history(cal, start=date(2026, 10, 1))
    out = price_period_returns(h, None, cal, ny(2026, 10, 23, 17))
    assert out[PricePeriod.TODAY].available and out[PricePeriod.WEEK].available
    for period in (PricePeriod.MONTH, PricePeriod.YEAR):
        assert out[period].reason == "insufficient_history"
        assert out[period].target_date is not None and out[period].percent is None


def test_unexpected_gap_is_bounded_and_not_treated_as_holiday():
    cal = sessions()
    now = ny(2026, 10, 23, 17)
    gap = {date(2026, 10, 16), date(2026, 10, 15), date(2026, 10, 14)}
    week = price_period_returns(history(cal, skip=gap), None, cal, now)[
        PricePeriod.WEEK
    ]
    assert week.reason == "unexpected_gap" and week.reference_date == date(2026, 10, 16)
    # Two missing expected sessions are still bridged.
    ok = history(cal, skip={date(2026, 10, 16), date(2026, 10, 15)})
    bridged = price_period_returns(ok, None, cal, now)[PricePeriod.WEEK]
    assert bridged.reference_date == date(2026, 10, 14)


def test_missing_current_and_previous_close_give_reasons():
    cal = sessions()
    now = ny(2026, 10, 23, 17)
    no_current = history(cal, skip={date(2026, 10, 23)})
    out = price_period_returns(no_current, None, cal, now)
    assert {r.reason for r in out.values()} == {"missing_current_close"}
    no_prev = history(cal, skip={date(2026, 10, 22)})
    today = price_period_returns(no_prev, None, cal, now)[PricePeriod.TODAY]
    assert today.reason == "missing_reference_close"


def test_unavailable_history_and_zero_reference():
    cal = sessions()
    now = ny(2026, 10, 23, 17)
    bad = PriceHistory(ValueStatus.MISSING, history(cal).provenance)
    week = price_period_returns(bad, None, cal, now)[PricePeriod.WEEK]
    assert week.reason == "history_unavailable"
    zero = history(cal, overrides={date(2026, 10, 22): 0})
    today = price_period_returns(zero, None, cal, now)[PricePeriod.TODAY]
    assert today.reason == "zero_reference"
