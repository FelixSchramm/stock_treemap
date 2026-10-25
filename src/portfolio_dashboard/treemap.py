"""Treemap value model, portfolio KPIs, shared filters and positions table (ADR 0008).

Pure and offline; no Streamlit or Plotly. Tile areas come only from the EUR
valuation, so they cannot change with the selected view or period. A
performance (course period or personal return) only drives color and label.
Colors may be capped for display; every printed number stays unlimited.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

from portfolio_dashboard.models import (
    InstrumentType,
    PositionActivity,
    ValuationSource,
)
from portfolio_dashboard.returns import (
    PersonalReturnStatus,
    PortfolioReturns,
    ReturnAggregate,
    portfolio_returns,
)
from portfolio_dashboard.valuations import (
    EurValuationStatus,
    PortfolioValuation,
    ValuationGroup,
)

if TYPE_CHECKING:  # price_periods pulls in pandas; the demo path stays light.
    from portfolio_dashboard.price_periods import PeriodReturn

NOT_AVAILABLE = "n. v."
UNKNOWN_QUOTE_TIME = "Kursstand unbekannt"
SHEET_UNKNOWN_QUOTE_STATUS = "Sheet-Wert, Kursstand unbekannt"
# Display-only bound of the symmetric color scale, in percent.
COLOR_CAP_PERCENT = Decimal("50")
MIN_SCALE_PERCENT = Decimal("1")

NEGATIVE_COLOR = (179, 38, 30)
NEUTRAL_COLOR = (95, 99, 104)
POSITIVE_COLOR = (24, 128, 56)
MISSING_COLOR = "#3c4043"
TILE_TEXT_COLOR = "#ffffff"

ALL_FILTER = "Alle"
NO_CATEGORY_LABEL = "Ohne Kategorie"
WEIGHT_COLUMN_LABEL = "Anteil am bewerteten Gesamtdepot (%)"

SORT_BY_VALUE = "Wert (EUR)"
SORT_BY_WEIGHT = WEIGHT_COLUMN_LABEL
SORT_BY_PERFORMANCE = "Entwicklung (%)"
SORT_BY_NAME = "Name"
SORT_BY_TYPE = "Typ"
SORT_FIELDS = (
    SORT_BY_VALUE,
    SORT_BY_WEIGHT,
    SORT_BY_PERFORMANCE,
    SORT_BY_NAME,
    SORT_BY_TYPE,
)

_TYPE_LABELS = {
    InstrumentType.STOCK: "Aktie",
    InstrumentType.ETF: "ETF",
    InstrumentType.DERIVATIVE: "Derivat",
}
_LABEL_TO_TYPE = {label: kind for kind, label in _TYPE_LABELS.items()}

_VALUATION_STATUS_LABELS = {
    EurValuationStatus.MISSING_PRICE: "Kurs fehlt",
    EurValuationStatus.INVALID_PRICE: "Kurs ungültig",
    EurValuationStatus.UNKNOWN_UNIT: "Kurswährung/Einheit fehlt",
    EurValuationStatus.UNVERIFIED_INSTRUMENT: "Instrument nicht zugeordnet",
    EurValuationStatus.UNVERIFIED_PROVIDER: "Kursquelle nicht verifiziert",
    EurValuationStatus.UNIT_MISMATCH: "Kurseinheit weicht vom Listing ab",
    EurValuationStatus.MISSING_FX: "FX-Kurs fehlt",
    EurValuationStatus.FX_DIRECTION_MISMATCH: "FX-Richtung ungültig",
}

_RETURN_STATUS_LABELS = {
    PersonalReturnStatus.MISSING_COST: "Einstand fehlt",
    PersonalReturnStatus.INVALID_COST: "Einstand ungültig",
    PersonalReturnStatus.UNVERIFIED_COST: "Einstand ungeprüft (Teilverkauf)",
    PersonalReturnStatus.NONPOSITIVE_COST: "Einstand ≤ 0",
}

_PERIOD_REASON_LABELS = {
    "stale_quote": "Kurs/Historie veraltet",
    "insufficient_history": "Historie unvollständig",
    "missing_reference_close": "Historie unvollständig",
    "unexpected_gap": "Historie unvollständig",
    "missing_current_close": "Aktueller Schlusskurs fehlt",
    "calendar_incomplete": "Handelskalender unvollständig",
    "unverified_instrument": "Kennung/Zuordnung fehlt",
    "history_unavailable": "Historie fehlt",
}


@dataclass(frozen=True, slots=True)
class Performance:
    """Percent of one instrument for the selected view; None means n. v."""

    percent: Decimal | None
    reference_date: date | None = None


@dataclass(frozen=True, slots=True)
class TreemapTile:
    """Prepared tile data for one valued instrument group."""

    name: str
    ticker: str | None
    amount_eur: Decimal  # Area; always positive.
    percent: Decimal | None
    color: str
    label: tuple[str, ...]  # Display lines inside the tile.
    tooltip: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TreemapModel:
    """Prepared treemap model including symmetric color scale and omitted count."""

    tiles: tuple[TreemapTile, ...]
    omitted_count: int  # Instruments without a positive EUR value: no tile.
    scale_percent: Decimal  # Legend runs from -scale to +scale.
    capped: bool  # Some percent lies beyond the color scale.


@dataclass(frozen=True, slots=True)
class PerformerSummary:
    """Winner or loser summary with instrument name and percentage."""

    name: str
    ticker: str | None
    percent: Decimal
    formatted: str


@dataclass(frozen=True, slots=True)
class PortfolioKpis:
    """Portfolio-wide header KPIs derived from the unfiltered valuation snapshot."""

    total_eur: Decimal | None
    total_eur_label: str
    active_group_count: int
    active_row_count: int
    valued_group_count: int
    valued_row_count: int
    partially_valued_group_count: int
    active_positions_label: str
    coverage_label: str
    measured_group_count: int
    performance_reference_label: str
    winner: PerformerSummary | None
    loser: PerformerSummary | None
    winner_label: str
    loser_label: str
    personal_eligible_group_count: int | None = None
    personal_eligible_row_count: int | None = None
    personal_market_value_eur: Decimal | None = None
    personal_cost_basis_eur: Decimal | None = None
    personal_gain_eur: Decimal | None = None
    personal_return_percent: Decimal | None = None
    personal_gain_label: str | None = None
    personal_return_label: str | None = None
    personal_coverage_label: str | None = None
    is_partial_total: bool = False
    total_status_note: str = "Vollständig bewertet"


@dataclass(frozen=True, slots=True)
class FilterCriteria:
    """Shared filter state applied jointly to the treemap and the positions table."""

    query: str = ""
    instrument_type: str = ALL_FILTER
    category: str = ALL_FILTER


@dataclass(frozen=True, slots=True)
class PositionTableRow:
    """Inspectable table row for an active instrument group, including unvalued ones."""

    name: str
    ticker: str | None
    instrument_type: str
    category: str
    quantity: Decimal
    quantity_label: str
    currency: str
    amount_eur: Decimal | None
    amount_eur_label: str
    weight_percent: Decimal | None
    weight_label: str
    percent: Decimal | None
    percent_label: str
    as_of: str
    reference_date: str
    status: str
    source_row: int

    def to_display_dict(self) -> dict[str, str | float | None]:
        """Convert row into a dictionary suitable for Streamlit dataframe display.

        :return: Mapping of German column headers to display values.
        """
        return {
            "Instrument": self.name,
            "Ticker": self.ticker or "—",
            "Typ": self.instrument_type,
            "Kategorie": self.category,
            "Stückzahl": self.quantity_label,
            "Währung": self.currency,
            "Wert (EUR)": (
                float(self.amount_eur.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
                if self.amount_eur is not None
                else None
            ),
            WEIGHT_COLUMN_LABEL: (
                float(
                    self.weight_percent.quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_EVEN
                    )
                )
                if self.weight_percent is not None
                else None
            ),
            "Entwicklung (%)": self.percent_label,
            "Datenstand": self.as_of,
            "Referenzdatum": self.reference_date,
            "Status": self.status,
        }

    def detail_lines(self) -> tuple[str, ...]:
        """Build complete non-hover detail lines covering all tooltip and table fields.

        :return: Tuple of human-readable German lines for touch and keyboard access.
        """
        header = self.name + (f" ({self.ticker})" if self.ticker else "")
        return (
            header,
            f"Typ: {self.instrument_type}",
            f"Kategorie: {self.category}",
            f"Währung: {self.currency}",
            f"Stückzahl: {self.quantity_label}",
            f"Wert: {self.amount_eur_label}",
            f"Anteil am bewerteten Depot: {self.weight_label}",
            f"Entwicklung: {self.percent_label} ({performance_direction_label(self.percent)})",
            f"Datenstand: {self.as_of}",
            f"Referenzdatum: {self.reference_date}",
            f"Status: {self.status}",
        )


def performance_from_period(result: "PeriodReturn") -> Performance:
    """Convert a period return result into a display performance object.

    :param result: Calculated period return for an instrument.
    :return: Performance with percent and reference date.
    """
    return Performance(result.percent, result.reference_date)


def personal_performances(returns: PortfolioReturns) -> tuple[Performance, ...]:
    """Build one personal return performance entry per valuation group, in group order.

    :param returns: Portfolio returns calculated from a valuation.
    :return: Tuple of Performance objects aligned with valuation.groups.
    """
    return tuple(Performance(group.return_percent) for group in returns.groups)


def _german(value: Decimal, places: int) -> str:
    """Format a Decimal with German thousands and decimal separators.

    :param value: Decimal number to format.
    :param places: Number of decimal places.
    :return: Formatted German number string.
    """
    rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    if rounded == 0:
        rounded = abs(rounded)  # No "-0,00".
    text = f"{rounded:,.{places}f}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def format_eur(amount: Decimal | None, *, signed: bool = False) -> str:
    """Format a EUR amount in German notation or n. v. if unavailable.

    :param amount: EUR amount or None.
    :param signed: Whether positive amounts receive an explicit plus sign.
    :return: Formatted EUR string.
    """
    if amount is None:
        return NOT_AVAILABLE
    text = _german(amount, 2)
    if signed and not text.startswith("-") and text != "0,00":
        return f"+{text} EUR"
    return f"{text} EUR"


def format_percent(percent: Decimal | None) -> str:
    """Format a percentage in signed German notation or n. v. if unavailable.

    :param percent: Percentage value or None.
    :return: Signed German percentage string.
    """
    if percent is None:
        return NOT_AVAILABLE
    text = _german(percent, 2)
    return f"{text} %" if text.startswith("-") or text == "0,00" else f"+{text} %"


def performance_direction_label(percent: Decimal | None) -> str:
    """Return a non-color German direction label for accessibility alongside colors.

    :param percent: Percentage value or None.
    :return: German text label describing gain, loss, unchanged, or missing state.
    """
    if percent is None:
        return f"Keine Kennzahl ({NOT_AVAILABLE})"
    rounded = percent.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    if rounded > 0:
        return "Gewinn (+)"
    if rounded < 0:
        return "Verlust (−)"
    return "Unverändert (0,00 %)"


def _relative_luminance(hex_color: str) -> float:
    """Calculate WCAG 2.1 relative luminance for a 6-digit hex color.

    :param hex_color: Hex color string like ``"#188038"``.
    :return: Relative luminance in ``[0.0, 1.0]``.
    """
    cleaned = hex_color.lstrip("#")
    if len(cleaned) != 6:
        raise ValueError(f"expected 6-digit hex color, got {hex_color!r}")
    channels = [int(cleaned[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground_hex: str, background_hex: str) -> float:
    """Calculate WCAG 2.1 contrast ratio between two hex colors.

    :param foreground_hex: Foreground hex color string.
    :param background_hex: Background hex color string.
    :return: Contrast ratio ``>= 1.0``.
    """
    lum_a = _relative_luminance(foreground_hex)
    lum_b = _relative_luminance(background_hex)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def _format_quantity(quantity: Decimal) -> str:
    """Format a position quantity using German decimal notation without trailing zeros.

    :param quantity: Decimal quantity.
    :return: Formatted quantity string.
    """
    normalized = quantity.normalize()
    text = f"{normalized:f}"
    if "." in text:
        int_part, frac_part = text.split(".", 1)
        int_formatted = f"{int(int_part):,}".replace(",", ".")
        return f"{int_formatted},{frac_part}"
    return f"{int(text):,}".replace(",", ".")


def color_scale(percents: Sequence[Decimal | None]) -> tuple[Decimal, bool]:
    """Calculate the symmetric color scale limit from visible values.

    :param percents: Sequence of percentage values for visible tiles.
    :return: Tuple of (symmetric scale bound, whether any value exceeds the cap).
    """
    largest = max((abs(p) for p in percents if p is not None), default=Decimal(0))
    return (
        min(max(largest, MIN_SCALE_PERCENT), COLOR_CAP_PERCENT),
        largest > COLOR_CAP_PERCENT,
    )


def color_for(percent: Decimal | None, scale: Decimal) -> str:
    """Compute hex color for a percentage; only color position is clamped, never value.

    :param percent: Percentage value or None.
    :param scale: Positive symmetric bound of the color scale.
    :return: Hex RGB color string.
    """
    if percent is None:
        return MISSING_COLOR
    position = max(Decimal(-1), min(Decimal(1), percent / scale))
    target = POSITIVE_COLOR if position > 0 else NEGATIVE_COLOR
    mix = float(abs(position))
    channels = (
        round(base + (end - base) * mix) for base, end in zip(NEUTRAL_COLOR, target)
    )
    return "#{:02x}{:02x}{:02x}".format(*channels)


def _active_members(group: ValuationGroup):
    """Return active position valuations within a group.

    :param group: Valuation group to inspect.
    :return: List of active PositionValuation members.
    """
    return [
        member
        for member in group.members
        if member.position.activity is PositionActivity.ACTIVE
    ]


def _kpi_label(name: str, ticker: str | None, ticker_counts: Counter) -> str:
    """Return the ticker for compact KPI tiles only if no other group shares it.

    :param name: Full instrument name.
    :param ticker: Optional display ticker.
    :param ticker_counts: Number of valuation groups per display ticker in the snapshot.
    :return: The unique ticker, otherwise the full name.
    """
    return ticker if ticker and ticker_counts[ticker] == 1 else name


def _is_active_group(group: ValuationGroup) -> bool:
    """Check whether a valuation group has at least one active member.

    :param group: Valuation group to inspect.
    :return: True if at least one member is active.
    """
    return bool(_active_members(group))


def _as_of(group: ValuationGroup) -> str:
    """Determine the oldest proven quote timestamp for a valued group.

    :param group: Valuation group to inspect.
    :return: Formatted timestamp, Kursstand unbekannt, or n. v. if unvalued.
    """
    if group.amount_eur is None:
        return NOT_AVAILABLE
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


def _group_currencies(group: ValuationGroup) -> str:
    """Collect distinct quote currencies for a group's valued or active members.

    :param group: Valuation group to inspect.
    :return: Comma-separated currency codes or n. v.
    """
    valued = [m for m in group.members if m.amount_eur is not None]
    candidates = valued if valued else _active_members(group)
    currencies = {
        m.position.valuation.unit.currency
        for m in candidates
        if m.position.valuation is not None and m.position.valuation.unit is not None
    }
    return ", ".join(sorted(currencies)) or NOT_AVAILABLE


def _group_categories(group: ValuationGroup) -> tuple[str, ...]:
    """Return sorted unique non-empty categories across active members of a group.

    :param group: Valuation group to inspect.
    :return: Tuple of category strings.
    """
    active = _active_members(group) or list(group.members)
    categories = {
        member.position.category.strip()
        for member in active
        if member.position.category and member.position.category.strip()
    }
    return tuple(sorted(categories))


def group_status_label(
    group: ValuationGroup,
    performance: Performance,
    *,
    is_personal_view: bool = False,
    group_return: ReturnAggregate | None = None,
    period_reason: str | None = None,
) -> str:
    """Build a human-readable German status string for an active instrument group.

    :param group: Valuation group to describe.
    :param performance: Selected performance for the group.
    :param is_personal_view: Whether the personal since-purchase view is active.
    :param group_return: Optional personal return aggregate for the group.
    :param period_reason: Optional reason code from ``PeriodReturn`` when course return is missing.
    :return: German status description for table and status views.
    """
    active = _active_members(group)
    if not active:
        return "Inaktiv (Bestand 0)"

    notes: list[str] = []
    unvalued = [m for m in active if m.amount_eur is None]
    valued = [m for m in active if m.amount_eur is not None]

    if not valued:
        for member in unvalued:
            reason = _VALUATION_STATUS_LABELS.get(member.status, "Bewertung fehlt")
            if reason not in notes:
                notes.append(reason)
        return "; ".join(notes) if notes else "Bewertung fehlt"

    if unvalued:
        reasons = []
        for member in unvalued:
            reason = _VALUATION_STATUS_LABELS.get(member.status, "Bewertung fehlt")
            if reason not in reasons:
                reasons.append(reason)
        detail = f" ({', '.join(reasons)})" if reasons else ""
        notes.append(f"Nur teilweise bewertet{detail}")

    has_sheet_unknown = any(
        m.position.valuation is not None
        and m.position.valuation.provenance.source is ValuationSource.SHEET
        and m.position.valuation.provenance.observed_at is None
        for m in valued
    )
    has_sheet_timed = any(
        m.position.valuation is not None
        and m.position.valuation.provenance.source is ValuationSource.SHEET
        and m.position.valuation.provenance.observed_at is not None
        for m in valued
    )
    has_cache = any(
        m.position.valuation is not None
        and m.position.valuation.provenance.source is ValuationSource.CACHED_PROVIDER
        for m in valued
    )
    has_stale = any(
        "stale" in w.code.lower()
        for m in valued
        for w in m.warnings
    )
    has_unmapped_fallback = any(
        m.position.valuation is not None
        and m.position.valuation.provenance.source is ValuationSource.SHEET
        and m.position.instrument.instrument_id is None
        and any(w.code == "price_source_fallback" for w in m.warnings)
        for m in valued
    )

    if has_unmapped_fallback:
        notes.append("Kennung/Zuordnung fehlt")
    if has_sheet_unknown:
        notes.append(SHEET_UNKNOWN_QUOTE_STATUS)
    elif has_sheet_timed:
        notes.append("Sheet-Wert")
    if has_cache:
        notes.append("Gecachter Kurs")
    if has_stale:
        notes.append("Veralteter Kurs")

    if performance.percent is None:
        if is_personal_view and group_return is not None:
            cost_reasons: list[str] = []
            for member_ret in group_return.members:
                if member_ret.valuation.position.activity is not PositionActivity.ACTIVE:
                    continue
                reason = _RETURN_STATUS_LABELS.get(member_ret.status)
                if reason and reason not in cost_reasons:
                    cost_reasons.append(reason)
            if cost_reasons:
                notes.extend(cost_reasons)
            else:
                notes.append("Rendite seit Kauf n. v.")
        elif is_personal_view:
            notes.append("Einstand fehlt")
        elif period_reason == "sheet_fallback" and (has_sheet_unknown or has_sheet_timed):
            notes.append("Keine Kurshistorie bei Sheet-Bewertung")
        elif period_reason in _PERIOD_REASON_LABELS:
            mapped_reason = _PERIOD_REASON_LABELS[period_reason]
            if mapped_reason not in notes:
                notes.append(mapped_reason)
        else:
            notes.append("Historie fehlt")

    return "; ".join(notes) if notes else "OK"


def _tooltip(
    group: ValuationGroup, performance: Performance, ticker: str | None
) -> tuple[str, ...]:
    """Build tooltip lines for a treemap tile.

    :param group: Valuation group represented by the tile.
    :param performance: Performance for the selected view/period.
    :param ticker: Optional display ticker.
    :return: Tuple of tooltip lines.
    """
    first = group.members[0].position
    valued = [m for m in group.members if m.amount_eur is not None]
    active = _active_members(group)
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
    valuation: PortfolioValuation,
    performances: Sequence[Performance],
    *,
    groups: Sequence[ValuationGroup] | None = None,
) -> TreemapModel:
    """Build one tile per verified instrument group, aligned with groups and performances.

    A group without a positive EUR value gets no tile (an unknown value is not
    drawn as an empty or tiny one). A missing percent keeps its area, is gray
    and reads "n. v.". Distinct products never merge by underlying or name:
    groups come from verified identity only.

    :param valuation: Complete portfolio valuation snapshot.
    :param performances: One Performance per group in ``groups`` (or ``valuation.groups``).
    :param groups: Optional filtered subset of valuation groups.
    :return: Prepared TreemapModel.
    :raises ValueError: If ``len(performances)`` does not match the group count.
    """
    target_groups = valuation.groups if groups is None else tuple(groups)
    if len(performances) != len(target_groups):
        raise ValueError("one performance per valuation group is required")
    shown = [
        (group, performance)
        for group, performance in zip(target_groups, performances)
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
    return TreemapModel(tuple(tiles), len(target_groups) - len(shown), scale, capped)


def build_kpis(
    valuation: PortfolioValuation,
    performances: Sequence[Performance],
    *,
    is_personal_view: bool = False,
    returns: PortfolioReturns | None = None,
) -> PortfolioKpis:
    """Calculate portfolio-wide header KPIs from the unfiltered valuation snapshot.

    :param valuation: Complete portfolio valuation snapshot.
    :param performances: Sequence of performances aligned with ``valuation.groups``.
    :param is_personal_view: Whether the personal since-purchase view is selected.
    :param returns: Optional precomputed PortfolioReturns for the personal view.
    :return: PortfolioKpis with consistent group-level denominators and row notes.
    :raises ValueError: If ``len(performances)`` does not match ``len(valuation.groups)``.
    """
    if len(performances) != len(valuation.groups):
        raise ValueError("one performance per valuation group is required")

    ticker_counts = Counter(
        group.members[0].position.instrument.display_ticker for group in valuation.groups
    )
    active_pairs = [
        (group, perf)
        for group, perf in zip(valuation.groups, performances)
        if _is_active_group(group)
    ]
    active_group_count = len(active_pairs)
    active_row_count = valuation.active_count
    valued_group_count = sum(
        group.amount_eur is not None for group, _ in active_pairs
    )
    valued_row_count = valuation.valued_count
    partially_valued_group_count = sum(
        group.amount_eur is not None
        and any(m.amount_eur is None for m in _active_members(group))
        for group, _ in active_pairs
    )

    if active_row_count != active_group_count:
        active_positions_label = f"{active_group_count} (aus {active_row_count} Zeilen)"
    else:
        active_positions_label = str(active_group_count)

    coverage_label = f"{valued_group_count} von {active_group_count} bewertet"
    if partially_valued_group_count:
        coverage_label += f" (davon {partially_valued_group_count} teilweise)"

    measured = [
        (group, perf)
        for group, perf in active_pairs
        if perf.percent is not None
    ]
    measured_group_count = len(measured)
    performance_reference_label = (
        f"Bezugsmenge: {measured_group_count} von {active_group_count} "
        "aktiven Positionen mit Kennzahl"
    )

    positive = [
        (group, perf) for group, perf in measured if perf.percent > 0
    ]
    negative = [
        (group, perf) for group, perf in measured if perf.percent < 0
    ]

    winner: PerformerSummary | None = None
    if positive:
        win_group, win_perf = max(
            positive,
            key=lambda item: (
                item[1].percent,
                item[0].amount_eur if item[0].amount_eur is not None else Decimal(-1),
                -item[0].members[0].position.source_row,
            ),
        )
        win_first = win_group.members[0].position
        win_ticker = win_first.instrument.display_ticker
        win_pct = format_percent(win_perf.percent)
        winner = PerformerSummary(
            name=win_first.name,
            ticker=win_ticker,
            percent=win_perf.percent,
            formatted=f"{_kpi_label(win_first.name, win_ticker, ticker_counts)}: {win_pct}",
        )

    loser: PerformerSummary | None = None
    if negative:
        lose_group, lose_perf = min(
            negative,
            key=lambda item: (
                item[1].percent,
                -(item[0].amount_eur if item[0].amount_eur is not None else Decimal(-1)),
                item[0].members[0].position.source_row,
            ),
        )
        lose_first = lose_group.members[0].position
        lose_ticker = lose_first.instrument.display_ticker
        lose_pct = format_percent(lose_perf.percent)
        loser = PerformerSummary(
            name=lose_first.name,
            ticker=lose_ticker,
            percent=lose_perf.percent,
            formatted=f"{_kpi_label(lose_first.name, lose_ticker, ticker_counts)}: {lose_pct}",
        )

    if winner is not None:
        winner_label = winner.formatted
    elif measured_group_count > 0:
        winner_label = f"{NOT_AVAILABLE} (keine > 0 %)"
    else:
        winner_label = NOT_AVAILABLE

    if loser is not None:
        loser_label = loser.formatted
    elif measured_group_count > 0:
        loser_label = f"{NOT_AVAILABLE} (keine < 0 %)"
    else:
        loser_label = NOT_AVAILABLE

    valued_active_members = [
        member
        for group, _ in active_pairs
        for member in _active_members(group)
        if member.amount_eur is not None
    ]
    has_sheet_unknown = any(
        m.position.valuation is not None
        and m.position.valuation.provenance.source is ValuationSource.SHEET
        and m.position.valuation.provenance.observed_at is None
        for m in valued_active_members
    )
    has_sheet_timed = any(
        m.position.valuation is not None
        and m.position.valuation.provenance.source is ValuationSource.SHEET
        and m.position.valuation.provenance.observed_at is not None
        for m in valued_active_members
    )
    has_cached = any(
        m.position.valuation is not None
        and m.position.valuation.provenance.source is ValuationSource.CACHED_PROVIDER
        for m in valued_active_members
    )
    is_partial_total = bool(
        valued_group_count < active_group_count or partially_valued_group_count > 0
    )

    qualifiers: list[str] = []
    if valuation.total_eur is None:
        total_status_note = "Daten nicht verfügbar"
    else:
        if is_partial_total:
            qualifiers.append(
                f"Teilabdeckung ({valued_group_count} von {active_group_count} Positionen)"
            )
        if has_sheet_unknown:
            qualifiers.append(f"enthält {SHEET_UNKNOWN_QUOTE_STATUS}")
        elif has_sheet_timed:
            qualifiers.append("enthält Sheet-Werte")
        if has_cached:
            qualifiers.append("enthält gecachte Kurse")
        total_status_note = (
            " · ".join(qualifiers)
            if qualifiers
            else "Vollständig aus verifizierten Kursen bewertet"
        )

    if not is_personal_view:
        return PortfolioKpis(
            total_eur=valuation.total_eur,
            total_eur_label=format_eur(valuation.total_eur),
            active_group_count=active_group_count,
            active_row_count=active_row_count,
            valued_group_count=valued_group_count,
            valued_row_count=valued_row_count,
            partially_valued_group_count=partially_valued_group_count,
            active_positions_label=active_positions_label,
            coverage_label=coverage_label,
            measured_group_count=measured_group_count,
            performance_reference_label=performance_reference_label,
            winner=winner,
            loser=loser,
            winner_label=winner_label,
            loser_label=loser_label,
            is_partial_total=is_partial_total,
            total_status_note=total_status_note,
        )

    resolved_returns = returns if returns is not None else portfolio_returns(valuation)
    eligible_groups = [
        group_ret
        for group, group_ret in zip(valuation.groups, resolved_returns.groups)
        if _is_active_group(group) and group_ret.return_percent is not None
    ]
    personal_eligible_group_count = len(eligible_groups)
    personal_eligible_row_count = resolved_returns.total.eligible_count

    if eligible_groups:
        market_eur = sum(
            (g.eligible_market_value_eur for g in eligible_groups), Decimal(0)
        )
        cost_eur = sum(
            (g.eligible_cost_basis_eur for g in eligible_groups), Decimal(0)
        )
        gain_eur = market_eur - cost_eur
        return_pct = (gain_eur / cost_eur) * Decimal(100)
        gain_label = format_eur(gain_eur, signed=True)
        return_label = format_percent(return_pct)
    else:
        market_eur = None
        cost_eur = None
        gain_eur = None
        return_pct = None
        gain_label = NOT_AVAILABLE
        return_label = NOT_AVAILABLE

    personal_cov = (
        f"{personal_eligible_group_count} von {active_group_count} "
        "Positionen mit vollständigem Einstand"
    )
    if personal_eligible_row_count != personal_eligible_group_count:
        personal_cov += f" ({personal_eligible_row_count} von {active_row_count} Zeilen)"

    return PortfolioKpis(
        total_eur=valuation.total_eur,
        total_eur_label=format_eur(valuation.total_eur),
        active_group_count=active_group_count,
        active_row_count=active_row_count,
        valued_group_count=valued_group_count,
        valued_row_count=valued_row_count,
        partially_valued_group_count=partially_valued_group_count,
        active_positions_label=active_positions_label,
        coverage_label=coverage_label,
        measured_group_count=measured_group_count,
        performance_reference_label=performance_reference_label,
        winner=winner,
        loser=loser,
        winner_label=winner_label,
        loser_label=loser_label,
        personal_eligible_group_count=personal_eligible_group_count,
        personal_eligible_row_count=personal_eligible_row_count,
        personal_market_value_eur=market_eur,
        personal_cost_basis_eur=cost_eur,
        personal_gain_eur=gain_eur,
        personal_return_percent=return_pct,
        personal_gain_label=gain_label,
        personal_return_label=return_label,
        personal_coverage_label=personal_cov,
        is_partial_total=is_partial_total,
        total_status_note=total_status_note,
    )


def available_categories(valuation: PortfolioValuation) -> tuple[str, ...]:
    """Return filter options for categories across all active positions.

    :param valuation: Portfolio valuation snapshot.
    :return: Tuple starting with ``"Alle"`` followed by sorted category names.
    """
    categories: set[str] = set()
    has_uncategorized = False
    for group in valuation.groups:
        active = _active_members(group)
        if not active:
            continue
        group_cats = _group_categories(group)
        if group_cats:
            categories.update(group_cats)
        else:
            has_uncategorized = True
    options = [ALL_FILTER, *sorted(categories)]
    if has_uncategorized and categories:
        options.append(NO_CATEGORY_LABEL)
    return tuple(options)


def _matches_filter(group: ValuationGroup, criteria: FilterCriteria) -> bool:
    """Check whether an active valuation group matches the filter criteria.

    :param group: Valuation group to test.
    :param criteria: Active search, type, and category filter criteria.
    :return: True if the group is active and matches all criteria.
    """
    active = _active_members(group)
    if not active:
        return False

    if criteria.instrument_type and criteria.instrument_type != ALL_FILTER:
        expected_type = _LABEL_TO_TYPE.get(criteria.instrument_type)
        if expected_type is None:
            return False
        if not any(
            m.position.instrument.instrument_type is expected_type for m in active
        ):
            return False

    if criteria.category and criteria.category != ALL_FILTER:
        group_cats = _group_categories(group)
        if criteria.category == NO_CATEGORY_LABEL:
            if group_cats:
                return False
        elif criteria.category not in group_cats:
            return False

    query = criteria.query.strip().lower()
    if query:
        searchable: list[str] = []
        for member in active:
            pos = member.position
            inst = pos.instrument
            searchable.append(pos.name)
            for attr in (
                inst.display_ticker,
                inst.instrument_id,
                inst.source_symbol,
                inst.source_isin,
                pos.category,
            ):
                if attr:
                    searchable.append(attr)
            searchable.append(_TYPE_LABELS[inst.instrument_type])
        if not any(query in token.lower() for token in searchable):
            return False

    return True


def filter_groups(
    valuation: PortfolioValuation,
    performances: Sequence[Performance],
    criteria: FilterCriteria | None = None,
) -> tuple[tuple[ValuationGroup, ...], tuple[Performance, ...]]:
    """Filter active instrument groups and their aligned performances.

    :param valuation: Complete portfolio valuation snapshot.
    :param performances: Performances aligned with ``valuation.groups``.
    :param criteria: Optional filter criteria (defaults to unfiltered active groups).
    :return: Tuple of (filtered ValuationGroups, aligned Performances).
    :raises ValueError: If ``len(performances)`` does not match ``len(valuation.groups)``.
    """
    if len(performances) != len(valuation.groups):
        raise ValueError("one performance per valuation group is required")
    active_criteria = criteria if criteria is not None else FilterCriteria()
    matched = [
        (group, perf)
        for group, perf in zip(valuation.groups, performances)
        if _matches_filter(group, active_criteria)
    ]
    return (
        tuple(group for group, _ in matched),
        tuple(perf for _, perf in matched),
    )


def build_table_rows(
    valuation: PortfolioValuation,
    performances: Sequence[Performance],
    *,
    criteria: FilterCriteria | None = None,
    is_personal_view: bool = False,
    returns: PortfolioReturns | None = None,
    period_reasons: Sequence[str | None] | None = None,
) -> tuple[PositionTableRow, ...]:
    """Build table rows for all matching active instrument groups, including unvalued ones.

    Weight fractions always refer to the full valued portfolio total
    (``valuation.total_eur``), even when a filter narrows the visible rows.

    :param valuation: Complete portfolio valuation snapshot.
    :param performances: Performances aligned with ``valuation.groups``.
    :param criteria: Optional filter criteria.
    :param is_personal_view: Whether the personal since-purchase view is active.
    :param returns: Optional PortfolioReturns for detailed cost-basis status labels.
    :param period_reasons: Optional period reason codes aligned with ``valuation.groups``.
    :return: Tuple of PositionTableRow objects in snapshot order.
    :raises ValueError: If ``len(performances)`` does not match ``len(valuation.groups)``.
    """
    if len(performances) != len(valuation.groups):
        raise ValueError("one performance per valuation group is required")
    if period_reasons is not None and len(period_reasons) != len(valuation.groups):
        raise ValueError("one period_reason per valuation group is required")
    active_criteria = criteria if criteria is not None else FilterCriteria()
    resolved_returns = (
        returns
        if returns is not None
        else (portfolio_returns(valuation) if is_personal_view else None)
    )
    return_groups = (
        resolved_returns.groups
        if resolved_returns is not None
        else (None,) * len(valuation.groups)
    )
    reasons_seq = (
        tuple(period_reasons)
        if period_reasons is not None
        else (None,) * len(valuation.groups)
    )

    rows: list[PositionTableRow] = []
    for group, perf, group_ret, reason in zip(
        valuation.groups, performances, return_groups, reasons_seq
    ):
        if not _matches_filter(group, active_criteria):
            continue
        active = _active_members(group)
        first = active[0].position
        ticker = first.instrument.display_ticker
        kind_label = _TYPE_LABELS[first.instrument.instrument_type]
        cats = _group_categories(group)
        category_label = ", ".join(cats) if cats else "—"
        quantity = sum((m.position.quantity for m in active), Decimal(0))
        weight_pct = (
            group.weight_fraction * Decimal(100)
            if group.weight_fraction is not None
            else None
        )
        weight_label = (
            f"{_german(weight_pct, 2)} %" if weight_pct is not None else NOT_AVAILABLE
        )
        reference_str = (
            perf.reference_date.strftime("%d.%m.%Y")
            if perf.reference_date is not None
            else NOT_AVAILABLE
        )
        status_str = group_status_label(
            group,
            perf,
            is_personal_view=is_personal_view,
            group_return=group_ret,
            period_reason=reason,
        )
        rows.append(
            PositionTableRow(
                name=first.name,
                ticker=ticker,
                instrument_type=kind_label,
                category=category_label,
                quantity=quantity,
                quantity_label=_format_quantity(quantity),
                currency=_group_currencies(group),
                amount_eur=group.amount_eur,
                amount_eur_label=format_eur(group.amount_eur),
                weight_percent=weight_pct,
                weight_label=weight_label,
                percent=perf.percent,
                percent_label=format_percent(perf.percent),
                as_of=_as_of(group),
                reference_date=reference_str,
                status=status_str,
                source_row=first.source_row,
            )
        )
    return tuple(rows)


def sort_table_rows(
    rows: Sequence[PositionTableRow],
    sort_by: str = SORT_BY_VALUE,
    *,
    descending: bool = True,
) -> tuple[PositionTableRow, ...]:
    """Sort position table rows while always placing missing values (n. v.) last.

    Tie-breaking is always deterministic by lowercase name A-Z, then source row.

    :param rows: Sequence of PositionTableRow items.
    :param sort_by: Column key from ``SORT_FIELDS``.
    :param descending: Sort direction for present values.
    :return: Sorted tuple of PositionTableRow items.
    :raises ValueError: If ``sort_by`` is not a supported sort field.
    """
    if sort_by not in SORT_FIELDS:
        raise ValueError(f"unsupported sort_by field: {sort_by}")

    if sort_by in (SORT_BY_VALUE, SORT_BY_WEIGHT, SORT_BY_PERFORMANCE):
        def numeric_value(row: PositionTableRow) -> Decimal | None:
            if sort_by == SORT_BY_VALUE:
                return row.amount_eur
            if sort_by == SORT_BY_WEIGHT:
                return row.weight_percent
            return row.percent

        present = [r for r in rows if numeric_value(r) is not None]
        missing = [r for r in rows if numeric_value(r) is None]
        present.sort(
            key=lambda r: (
                -numeric_value(r) if descending else numeric_value(r),
                r.name.lower(),
                r.source_row,
            )
        )
        missing.sort(key=lambda r: (r.name.lower(), r.source_row))
        return tuple(present + missing)

    def text_key(row: PositionTableRow) -> str:
        if sort_by == SORT_BY_TYPE:
            return row.instrument_type.lower()
        return row.name.lower()

    ordered = sorted(rows, key=lambda r: (r.name.lower(), r.source_row))
    ordered.sort(key=text_key, reverse=descending)
    return tuple(ordered)


def build_status_notes(
    valuation: PortfolioValuation,
    performances: Sequence[Performance],
    *,
    is_personal_view: bool = False,
    returns: PortfolioReturns | None = None,
    period_reasons: Sequence[str | None] | None = None,
) -> tuple[str, ...]:
    """Build position-referenced German status notices for data gaps and fallbacks.

    :param valuation: Complete portfolio valuation snapshot.
    :param performances: Performances aligned with ``valuation.groups``.
    :param is_personal_view: Whether the personal since-purchase view is active.
    :param returns: Optional precomputed PortfolioReturns.
    :param period_reasons: Optional period reason codes aligned with ``valuation.groups``.
    :return: Tuple of human-readable German status messages.
    """
    notes: list[str] = []
    for warning in valuation.snapshot.warnings:
        row_prefix = (
            f"Zeile {warning.source_row}: "
            if warning.source_row is not None
            else ""
        )
        entry = f"Import-Hinweis ({warning.code}): {row_prefix}{warning.message}"
        if entry not in notes:
            notes.append(entry)

    rows = build_table_rows(
        valuation,
        performances,
        is_personal_view=is_personal_view,
        returns=returns,
        period_reasons=period_reasons,
    )
    for row in rows:
        if row.status != "OK":
            ticker_part = f" ({row.ticker})" if row.ticker else ""
            notes.append(f"{row.name}{ticker_part}: {row.status}")
    return tuple(notes)


def build_position_details(
    rows: Sequence[PositionTableRow],
) -> tuple[tuple[str, ...], ...]:
    """Build non-hover detail blocks for all visible rows (including tiny/unvalued).

    :param rows: Sequence of PositionTableRow objects.
    :return: Tuple of detail line tuples, one per row.
    """
    return tuple(row.detail_lines() for row in rows)


