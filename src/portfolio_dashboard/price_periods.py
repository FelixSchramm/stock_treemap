"""Pure price-only returns for today, week, month and year (ADR 0006).

No provider access, clock or UI. The caller injects the aware ``now`` and the
expected regular sessions of the exchange (explicit dates, no calendar library).
History and quote must share the split-adjusted, dividend-unadjusted basis of
market_data; prices are compared as given and are never adjusted again here.
Every period returns either a percent or "n. v." with a stable reason code.
"""

import calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from zoneinfo import ZoneInfo

from portfolio_dashboard.market_data import (
    AdjustmentBasis,
    PriceHistory,
    PriceObservation,
)
from portfolio_dashboard.models import QuoteUnit, ValueStatus

# Expected sessions that may lack a bar before a reference date is rejected.
MAX_MISSING_SESSIONS = 2


class PricePeriod(Enum):
    TODAY = "today"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


@dataclass(frozen=True, slots=True)
class SessionCalendar:
    """Expected regular sessions in exchange-local dates plus regular hours.

    Early closes are not modelled; supply the actual close_time if needed.
    The set must cover the dates the periods reach back to.
    """

    sessions: frozenset[date]
    open_time: time
    close_time: time

    def __post_init__(self) -> None:
        if not all(type(day) is date for day in self.sessions):
            raise TypeError("sessions must be dates")
        if not self.open_time < self.close_time:
            raise ValueError("open_time must be before close_time")


def session_calendar(
    sessions: Iterable[date], open_time: time, close_time: time
) -> SessionCalendar:
    return SessionCalendar(frozenset(sessions), open_time, close_time)


@dataclass(frozen=True, slots=True)
class PeriodReturn:
    period: PricePeriod
    percent: Decimal | None = None
    reason: str | None = None  # Stable code when percent is None ("n. v.").
    market_open: bool | None = None
    current_date: date | None = None
    reference_date: date | None = None
    target_date: date | None = None
    current_price: Decimal | None = None
    reference_price: Decimal | None = None
    unit: QuoteUnit | None = None

    @property
    def available(self) -> bool:
        return self.percent is not None


def _shift_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _target(period: PricePeriod, current: date) -> date:
    if period is PricePeriod.WEEK:
        return current - timedelta(days=7)
    if period is PricePeriod.MONTH:
        return _shift_months(current, 1)
    return _shift_months(current, 12)  # 29 Feb falls back to 28 Feb.


def price_period_returns(
    history: PriceHistory,
    quote: PriceObservation | None,
    sessions: SessionCalendar,
    now: datetime,
) -> dict[PricePeriod, PeriodReturn]:
    """Return all four periods; ``now`` must be timezone-aware."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    problem = _history_problem(history)
    if problem is None:
        local = now.astimezone(ZoneInfo(history.exchange_timezone))
        today, clock = local.date(), local.time()
        running = (
            today in sessions.sessions
            and sessions.open_time <= clock < sessions.close_time
        )
        closes = {
            point.session_date: point.close
            for point in history.points
            if point.session_date in sessions.sessions
        }
        if running:
            current_date, current, problem = _live_quote(
                history, quote, sessions, local
            )
        else:
            # Latest session that has finished; a day before open is not one.
            finished = [
                day
                for day in sessions.sessions
                if day < today or (day == today and clock >= sessions.close_time)
            ]
            current_date = max(finished, default=None)
            current = closes.get(current_date)
            if current_date is None:
                problem = "calendar_incomplete"
            elif current is None:
                problem = "missing_current_close"
    if problem is not None:
        return {
            period: PeriodReturn(period, reason=problem, unit=history.unit)
            for period in PricePeriod
        }
    return {
        period: _period(
            period, history.unit, running, current_date, current, closes, sessions
        )
        for period in PricePeriod
    }


def _history_problem(history: PriceHistory) -> str | None:
    if history.status is not ValueStatus.AVAILABLE or not history.points:
        return "history_unavailable"
    if history.adjustment_basis is not AdjustmentBasis.YAHOO_SPLIT_ADJUSTED_PRICE:
        return "unknown_adjustment_basis"
    if history.unit is None or history.exchange_timezone is None:
        return "history_unavailable"
    return None


def _live_quote(history, quote, sessions, local):
    if (
        quote is None
        or quote.valuation.status is not ValueStatus.AVAILABLE
        or quote.valuation.price is None
    ):
        return None, None, "missing_quote"
    if quote.adjustment_basis is not history.adjustment_basis:
        return None, None, "unknown_adjustment_basis"
    if quote.valuation.unit != history.unit:
        return None, None, "unit_mismatch"
    observed = quote.valuation.provenance.observed_at
    if observed is None:
        return None, None, "missing_quote_time"
    observed = observed.astimezone(local.tzinfo)
    # A quote left from a previous session is not today's running price.
    if (
        observed.date() != local.date()
        or not sessions.open_time <= observed.time() < sessions.close_time
        or observed > local
    ):
        return None, None, "stale_quote"
    return observed.date(), quote.valuation.price, None


def _period(period, unit, running, current_date, current, closes, sessions):
    def result(reason=None, *, reference_date=None, reference=None, target=None):
        percent = None
        if reason is None:
            percent = (current - reference) / reference * 100
        return PeriodReturn(
            period,
            percent,
            reason,
            running,
            current_date,
            reference_date,
            target,
            current,
            reference,
            unit,
        )

    if period is PricePeriod.TODAY:
        target = max((d for d in sessions.sessions if d < current_date), default=None)
        candidates = [] if target is None else [target]
    else:
        target = _target(period, current_date)
        candidates = sorted(
            (d for d in sessions.sessions if d <= target), reverse=True
        )[: MAX_MISSING_SESSIONS + 1]
    if not candidates:
        return result("calendar_incomplete", target=target)
    if period is not PricePeriod.TODAY and not any(d <= target for d in closes):
        return result("insufficient_history", target=target)
    for day in candidates:
        if day in closes:
            if closes[day] == 0:
                return result("zero_reference", reference_date=day, target=target)
            return result(reference_date=day, reference=closes[day], target=target)
    reason = (
        "missing_reference_close" if period is PricePeriod.TODAY else "unexpected_gap"
    )
    return result(reason, reference_date=candidates[0], target=target)
