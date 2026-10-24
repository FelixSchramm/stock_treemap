"""Treemap value model (ADR 0008): areas, colors, labels and tooltips.

Pure and offline; no Streamlit or Plotly. Tile areas come only from the EUR
valuation, so they cannot change with the selected view or period. A
performance (course period or personal return) only drives color and label.
Colors may be capped for display; every printed number stays unlimited.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

from portfolio_dashboard.models import InstrumentType, PositionActivity
from portfolio_dashboard.returns import PortfolioReturns
from portfolio_dashboard.valuations import PortfolioValuation, ValuationGroup

if TYPE_CHECKING:  # price_periods pulls in pandas; the demo path stays light.
    from portfolio_dashboard.price_periods import PeriodReturn

NOT_AVAILABLE = "n. v."
UNKNOWN_QUOTE_TIME = "Kursstand unbekannt"
# Display-only bound of the symmetric color scale, in percent.
COLOR_CAP_PERCENT = Decimal("50")
MIN_SCALE_PERCENT = Decimal("1")

NEGATIVE_COLOR = (179, 38, 30)
NEUTRAL_COLOR = (95, 99, 104)
POSITIVE_COLOR = (30, 142, 62)
MISSING_COLOR = "#3c4043"

_TYPE_LABELS = {
    InstrumentType.STOCK: "Aktie",
    InstrumentType.ETF: "ETF",
    InstrumentType.DERIVATIVE: "Derivat",
}


@dataclass(frozen=True, slots=True)
class Performance:
    """Percent of one instrument for the selected view; None means n. v."""

    percent: Decimal | None
    reference_date: date | None = None


@dataclass(frozen=True, slots=True)
class TreemapTile:
    name: str
    ticker: str | None
    amount_eur: Decimal  # Area; always positive.
    percent: Decimal | None
    color: str
    label: tuple[str, ...]  # Display lines inside the tile.
    tooltip: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TreemapModel:
    tiles: tuple[TreemapTile, ...]
    omitted_count: int  # Instruments without a positive EUR value: no tile.
    scale_percent: Decimal  # Legend runs from -scale to +scale.
    capped: bool  # Some percent lies beyond the color scale.


def performance_from_period(result: "PeriodReturn") -> Performance:
    return Performance(result.percent, result.reference_date)


def personal_performances(returns: PortfolioReturns) -> tuple[Performance, ...]:
    """One entry per valuation group, in group order."""
    return tuple(Performance(group.return_percent) for group in returns.groups)


def _german(value: Decimal, places: int) -> str:
    rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    if rounded == 0:
        rounded = abs(rounded)  # No "-0,00".
    text = f"{rounded:,.{places}f}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def format_percent(percent: Decimal | None) -> str:
    if percent is None:
        return NOT_AVAILABLE
    text = _german(percent, 2)
    return f"{text} %" if text.startswith("-") or text == "0,00" else f"+{text} %"


def color_scale(percents: Sequence[Decimal | None]) -> tuple[Decimal, bool]:
    """Symmetric limit from the visible values, and whether it caps any."""
    largest = max((abs(p) for p in percents if p is not None), default=Decimal(0))
    return (
        min(max(largest, MIN_SCALE_PERCENT), COLOR_CAP_PERCENT),
        largest > COLOR_CAP_PERCENT,
    )


def color_for(percent: Decimal | None, scale: Decimal) -> str:
    """Hex color; only the color position is clamped, never the value."""
    if percent is None:
        return MISSING_COLOR
    position = max(Decimal(-1), min(Decimal(1), percent / scale))
    target = POSITIVE_COLOR if position > 0 else NEGATIVE_COLOR
    mix = float(abs(position))
    channels = (
        round(base + (end - base) * mix) for base, end in zip(NEUTRAL_COLOR, target)
    )
    return "#{:02x}{:02x}{:02x}".format(*channels)


def _as_of(group: ValuationGroup) -> str:
    stamps = []
    for member in group.members:
        quote = member.position.valuation
        if member.amount_eur is None or quote is None:
            continue
        stamps.append(quote.provenance.observed_at)
    # The retrieval time is never a quote time (DOMAIN_MODELS, ADR 0005).
    if not stamps or None in stamps:
        return UNKNOWN_QUOTE_TIME
    return min(stamps).strftime("%d.%m.%Y, %H:%M %Z")


def _tooltip(
    group: ValuationGroup, performance: Performance, ticker: str | None
) -> tuple[str, ...]:
    first = group.members[0].position
    valued = [m for m in group.members if m.amount_eur is not None]
    active = [
        m for m in group.members if m.position.activity is PositionActivity.ACTIVE
    ]
    quantity = sum((m.position.quantity for m in valued), Decimal(0))
    currencies = {
        m.position.valuation.unit.currency
        for m in valued
        if m.position.valuation is not None and m.position.valuation.unit is not None
    }
    weight = group.weight_fraction
    lines = [
        first.name + (f" ({ticker})" if ticker else ""),
        f"Typ: {_TYPE_LABELS[first.instrument.instrument_type]}",
        f"Währung: {', '.join(sorted(currencies)) or NOT_AVAILABLE}",
        f"Stückzahl: {quantity.normalize():f}".replace(".", ","),
        f"Wert: {_german(group.amount_eur, 2)} EUR",
        "Anteil am bewerteten Depot: "
        + (NOT_AVAILABLE if weight is None else f"{_german(weight * 100, 2)} %"),
        f"Entwicklung: {format_percent(performance.percent)}",
        f"Datenstand: {_as_of(group)}",
        "Referenzdatum: "
        + (
            NOT_AVAILABLE
            if performance.reference_date is None
            else performance.reference_date.strftime("%d.%m.%Y")
        ),
    ]
    if any(m.amount_eur is None for m in active):
        lines.append("Hinweis: nur teilweise bewertet")
    return tuple(lines)


def build_treemap(
    valuation: PortfolioValuation, performances: Sequence[Performance]
) -> TreemapModel:
    """One tile per verified instrument group, aligned with valuation.groups.

    A group without a positive EUR value gets no tile (an unknown value is not
    drawn as an empty or tiny one). A missing percent keeps its area, is gray
    and reads "n. v.". Distinct products never merge by underlying or name:
    groups come from verified identity only.
    """
    if len(performances) != len(valuation.groups):
        raise ValueError("one performance per valuation group is required")
    shown = [
        (group, performance)
        for group, performance in zip(valuation.groups, performances)
        if group.amount_eur is not None and group.amount_eur > 0
    ]
    scale, capped = color_scale([performance.percent for _, performance in shown])
    tiles = []
    for group, performance in shown:
        first = group.members[0].position
        ticker = first.instrument.display_ticker
        percent = performance.percent
        tiles.append(
            TreemapTile(
                name=first.name,
                ticker=ticker,
                amount_eur=group.amount_eur,
                percent=percent,
                color=color_for(percent, scale),
                label=(first.name, *((ticker,) if ticker else ()), format_percent(percent)),
                tooltip=_tooltip(group, performance, ticker),
            )
        )
    return TreemapModel(tuple(tiles), len(valuation.groups) - len(shown), scale, capped)
