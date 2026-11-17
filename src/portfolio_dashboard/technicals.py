"""Technical signals from daily closes: trend, support zones, RSI (issue #144).

Pure domain logic like ``price_periods``: no provider, Streamlit, cache or
clock. The input is the split-adjusted closing series of one listing, oldest
first, whose last value is the current price (the same price the "Heute"
period uses). All prices stay in the trading currency. Only methods that work
with closes alone are used; pivots and volume profile need high/low/volume.
Every value is either present or "n. v." with a stable reason code.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

# Swing lows: a close is the minimum of the trading days before and after it.
SWING_WINDOW = 5
SWING_MIN_DAYS = 60
MIN_SWING_TOUCHES = 2
# Candidates (and swing lows) at most this far apart form one zone.
ZONE_TOLERANCE = Decimal("0.015")
# Moving averages over closes.
SMA_SHORT = 20
SMA_MEDIUM = 50
SMA_LONG = 200
# Trend, Fibonacci and 52-week values need this many trading days.
LONG_MIN_DAYS = 200
YEAR_TRADING_DAYS = 252
FIBONACCI_LEVELS = (
    Decimal("0.236"),
    Decimal("0.382"),
    Decimal("0.5"),
    Decimal("0.618"),
    Decimal("0.786"),
)
# Trend rules.
TREND_SLOPE_LOOKBACK = 20  # SMA200 today versus this many trading days ago.
BOTTOM_NO_NEW_LOW_DAYS = 20
BOTTOM_MAX_ABOVE_LOW = Decimal("0.10")
CROSS_LOOKBACK = 20
# RSI after Wilder.
RSI_PERIOD = 14
RSI_OVERSOLD = Decimal(30)
RSI_OVERBOUGHT = Decimal(70)
# Next support prefers zones confirmed by this many different methods.
MIN_CONFLUENCE_STRENGTH = 2

INSUFFICIENT_HISTORY = "insufficient_history"
NO_SUPPORT_BELOW = "no_support_below"


class Trend(Enum):
    UP = "Aufwärtstrend"
    SIDEWAYS = "Seitwärts"
    BOTTOMING = "Bodenbildung"
    DOWN = "Abwärtstrend"


# Sort rank: higher is "better"; descending sorting starts with uptrends.
TREND_RANK = {Trend.UP: 4, Trend.SIDEWAYS: 3, Trend.BOTTOMING: 2, Trend.DOWN: 1}

TREND_RULES = {
    Trend.UP: "Kurs > SMA50 > SMA200 und SMA200 höher als vor 20 Handelstagen",
    Trend.BOTTOMING: (
        "Kurs < SMA200, kein neues 52-Wochen-Tief in 20 Handelstagen, "
        "höchstens 10 % über dem 52-Wochen-Tief und Kurs > SMA20"
    ),
    Trend.DOWN: "Kurs < SMA50 < SMA200",
    Trend.SIDEWAYS: "keine der Regeln für Aufwärtstrend, Bodenbildung oder Abwärtstrend",
}


class SupportMethod(Enum):
    SWING_LOW = "Swing-Tief"
    MOVING_AVERAGE = "Gleitender Durchschnitt"
    FIBONACCI = "Fibonacci"
    YEAR_LOW = "52-Wochen-Tief"


class CrossSignal(Enum):
    GOLDEN = "Golden Cross"
    DEATH = "Death Cross"


@dataclass(frozen=True, slots=True)
class SwingZone:
    """Bundled swing lows; ``price`` is the mean of the touching closes."""

    price: Decimal
    touches: int


@dataclass(frozen=True, slots=True)
class SupportCandidate:
    price: Decimal
    method: SupportMethod
    label: str  # e.g. "SMA200", "Fibonacci 61,8 %", "Swing-Tief (3 Berührungen)"


@dataclass(frozen=True, slots=True)
class SupportZone:
    """Candidates within the tolerance; ``price`` is their mean."""

    price: Decimal
    candidates: tuple[SupportCandidate, ...]

    @property
    def strength(self) -> int:
        """Number of different methods in the zone (1 to 4)."""
        return len({candidate.method for candidate in self.candidates})


@dataclass(frozen=True, slots=True)
class TechnicalSignals:
    """Technical values of one listing; ``None`` means "n. v." with a reason."""

    current_price: Decimal | None = None
    currency: str | None = None
    trend: Trend | None = None
    trend_reason: str | None = None
    sma20: Decimal | None = None
    sma50: Decimal | None = None
    sma200: Decimal | None = None
    year_low: Decimal | None = None
    year_high: Decimal | None = None
    zones: tuple[SupportZone, ...] = ()  # Below the price, highest first.
    next_support: SupportZone | None = None
    support_reason: str | None = None
    rsi: Decimal | None = None
    rsi_reason: str | None = None
    high_distance_percent: Decimal | None = None
    high_reason: str | None = None
    cross: CrossSignal | None = None

    @property
    def support_distance_percent(self) -> Decimal | None:
        """Distance from the price down to the next support (negative)."""
        if self.next_support is None or not self.current_price:
            return None
        return (self.next_support.price / self.current_price - 1) * 100

    @property
    def trend_rule(self) -> str | None:
        return TREND_RULES[self.trend] if self.trend is not None else None

    @property
    def rsi_label(self) -> str | None:
        if self.rsi is None:
            return None
        if self.rsi < RSI_OVERSOLD:
            return "überverkauft"
        if self.rsi > RSI_OVERBOUGHT:
            return "überkauft"
        return "neutral"


def unavailable_signals(reason: str, currency: str | None = None) -> TechnicalSignals:
    """All values "n. v." with the same reason (no mapping, no history, ...)."""
    return TechnicalSignals(
        currency=currency,
        trend_reason=reason,
        support_reason=reason,
        rsi_reason=reason,
        high_reason=reason,
    )


def sma(closes: Sequence[Decimal], window: int, *, end: int | None = None) -> Decimal | None:
    """Simple moving average of the ``window`` closes before index ``end``."""
    stop = len(closes) if end is None else end
    if window <= 0 or stop < window:
        return None
    return sum(closes[stop - window:stop], Decimal(0)) / window


def rsi(closes: Sequence[Decimal], period: int = RSI_PERIOD) -> Decimal | None:
    """Wilder RSI: simple mean of the first ``period`` changes, then smoothing."""
    if len(closes) < period + 1:
        return None
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum((max(c, Decimal(0)) for c in changes[:period]), Decimal(0)) / period
    loss = sum((max(-c, Decimal(0)) for c in changes[:period]), Decimal(0)) / period
    for change in changes[period:]:
        gain = (gain * (period - 1) + max(change, Decimal(0))) / period
        loss = (loss * (period - 1) + max(-change, Decimal(0))) / period
    if loss == 0:
        return Decimal(50) if gain == 0 else Decimal(100)
    return 100 - 100 / (1 + gain / loss)


def _cluster(prices: Sequence[Decimal]) -> list[list[int]]:
    """Group indices of ascending-sorted prices: within the tolerance of the zone's lowest."""
    order = sorted(range(len(prices)), key=lambda i: prices[i])
    groups: list[list[int]] = []
    for index in order:
        if groups and prices[index] <= prices[groups[-1][0]] * (1 + ZONE_TOLERANCE):
            groups[-1].append(index)
        else:
            groups.append([index])
    return groups


def swing_lows(closes: Sequence[Decimal]) -> list[Decimal]:
    """Closes lower than the ``SWING_WINDOW`` days before and not above the days after.

    The strict comparison on one side keeps a flat run from counting several times.
    """
    lows = []
    for i in range(SWING_WINDOW, len(closes) - SWING_WINDOW):
        value = closes[i]
        if value < min(closes[i - SWING_WINDOW:i]) and value <= min(
            closes[i + 1:i + 1 + SWING_WINDOW]
        ):
            lows.append(value)
    return lows


def swing_zones(lows: Sequence[Decimal]) -> tuple[SwingZone, ...]:
    """Bundle swing lows at most ``ZONE_TOLERANCE`` apart, ascending by price."""
    return tuple(
        SwingZone(sum((lows[i] for i in group), Decimal(0)) / len(group), len(group))
        for group in _cluster(lows)
    )


def support_zones(
    candidates: Sequence[SupportCandidate], current: Decimal
) -> tuple[SupportZone, ...]:
    """Bundle the candidates below ``current`` into zones, highest zone first."""
    below = [c for c in candidates if c.price < current]
    zones = [
        SupportZone(
            sum((below[i].price for i in group), Decimal(0)) / len(group),
            tuple(below[i] for i in group),
        )
        for group in _cluster([c.price for c in below])
    ]
    return tuple(sorted(zones, key=lambda zone: zone.price, reverse=True))


def next_support(zones: Sequence[SupportZone]) -> SupportZone | None:
    """Highest zone with confluence, otherwise the nearest single-method zone."""
    ordered = sorted(zones, key=lambda zone: zone.price, reverse=True)
    confirmed = [z for z in ordered if z.strength >= MIN_CONFLUENCE_STRENGTH]
    if confirmed:
        return confirmed[0]
    return ordered[0] if ordered else None


def _percent_label(level: Decimal) -> str:
    text = f"{level * 100:.1f}".replace(".", ",")
    return f"{text.removesuffix(',0')} %"


def _candidates(closes, sma20, sma50, sma200, year_low, year_high):
    candidates = []
    if len(closes) >= SWING_MIN_DAYS:
        for zone in swing_zones(swing_lows(closes)):
            if zone.touches >= MIN_SWING_TOUCHES:
                candidates.append(SupportCandidate(
                    zone.price, SupportMethod.SWING_LOW,
                    f"Swing-Tief ({zone.touches} Berührungen)",
                ))
    for label, value in (("SMA20", sma20), ("SMA50", sma50), ("SMA200", sma200)):
        if value is not None:
            candidates.append(SupportCandidate(value, SupportMethod.MOVING_AVERAGE, label))
    if year_low is not None and year_high is not None and year_high > year_low:
        for level in FIBONACCI_LEVELS:
            candidates.append(SupportCandidate(
                year_high - (year_high - year_low) * level,
                SupportMethod.FIBONACCI,
                f"Fibonacci {_percent_label(level)}",
            ))
    if year_low is not None:
        candidates.append(SupportCandidate(year_low, SupportMethod.YEAR_LOW, "52-Wochen-Tief"))
    return candidates


def _trend(closes, current, sma20, sma50, sma200, year_low):
    previous_sma200 = sma(closes, SMA_LONG, end=len(closes) - TREND_SLOPE_LOOKBACK)
    if (
        current > sma50 > sma200
        and previous_sma200 is not None
        and sma200 > previous_sma200
    ):
        return Trend.UP
    recent_low = min(closes[-BOTTOM_NO_NEW_LOW_DAYS:])
    if (
        current < sma200
        and recent_low > year_low
        and current <= year_low * (1 + BOTTOM_MAX_ABOVE_LOW)
        and current > sma20
    ):
        return Trend.BOTTOMING
    if current < sma50 < sma200:
        return Trend.DOWN
    return Trend.SIDEWAYS


def _cross(closes) -> CrossSignal | None:
    """Most recent SMA50/SMA200 crossing within the last ``CROSS_LOOKBACK`` days."""
    n = len(closes)
    signal = None
    for end in range(n - CROSS_LOOKBACK + 1, n + 1):
        before = (sma(closes, SMA_MEDIUM, end=end - 1), sma(closes, SMA_LONG, end=end - 1))
        after = (sma(closes, SMA_MEDIUM, end=end), sma(closes, SMA_LONG, end=end))
        if None in before or None in after:
            continue
        if before[0] <= before[1] and after[0] > after[1]:
            signal = CrossSignal.GOLDEN
        elif before[0] >= before[1] and after[0] < after[1]:
            signal = CrossSignal.DEATH
    return signal


def technical_signals(
    closes: Sequence[Decimal], currency: str | None = None
) -> TechnicalSignals:
    """Evaluate the closing series (oldest first, last value = current price)."""
    closes = tuple(closes)
    if not closes or closes[-1] <= 0:
        return unavailable_signals("history_unavailable", currency)
    current = closes[-1]
    sma20 = sma(closes, SMA_SHORT)
    sma50 = sma(closes, SMA_MEDIUM)
    long_enough = len(closes) >= LONG_MIN_DAYS
    sma200 = sma(closes, SMA_LONG) if long_enough else None
    year = closes[-YEAR_TRADING_DAYS:]
    year_low = min(year) if long_enough else None
    year_high = max(year) if long_enough else None

    trend = cross = high_distance = None
    long_reason = None if long_enough else INSUFFICIENT_HISTORY
    if long_enough:
        trend = _trend(closes, current, sma20, sma50, sma200, year_low)
        cross = _cross(closes)
        high_distance = (current / year_high - 1) * 100

    zones = support_zones(_candidates(closes, sma20, sma50, sma200, year_low, year_high), current)
    support = next_support(zones)
    support_reason = None
    if support is None:
        support_reason = INSUFFICIENT_HISTORY if sma20 is None else NO_SUPPORT_BELOW
    rsi_value = rsi(closes)
    return TechnicalSignals(
        current_price=current,
        currency=currency,
        trend=trend,
        trend_reason=long_reason,
        sma20=sma20,
        sma50=sma50,
        sma200=sma200,
        year_low=year_low,
        year_high=year_high,
        zones=zones,
        next_support=support,
        support_reason=support_reason,
        rsi=rsi_value,
        rsi_reason=None if rsi_value is not None else INSUFFICIENT_HISTORY,
        high_distance_percent=high_distance,
        high_reason=long_reason,
        cross=cross,
    )
