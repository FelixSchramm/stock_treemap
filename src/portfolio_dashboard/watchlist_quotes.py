"""Result model of the Watchlist quote refresh (ADR 0011, #84); pure and offline.

Light on purpose: no provider, pandas, Sheets transport or UI imports, so the
demo can build the same objects. Periods are keyed by their display labels like
the depot's course periods. Every entry carries all four periods; a period either
has a percent with its origin or "n. v." with a stable reason code.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from portfolio_dashboard.models import (
    DataWarning,
    QuoteUnit,
    SnapshotStatus,
    ValueStatus,
    _aware,
    _decimal,
    _text,
)
from portfolio_dashboard.watchlist_sheet import (
    SHEET_VALUE_LABEL,
    WatchlistItem,
    WatchlistSnapshot,
)

PERIODS = ("Heute", "Woche", "Monat", "Jahr")
DATA_UNAVAILABLE_TEXT = "Daten nicht verfügbar"
UNVERIFIED_MAPPING = "unverified_mapping"  # Row has no usable Yahoo mapping.
SHEET_VALUE_MISSING = "sheet_value_missing"  # No substitute value in the Sheet.
DEMO_LABEL = "Demo-Wert, synthetisch"


class WatchlistOrigin(Enum):
    """Where one displayed number comes from."""

    PROVIDER = "provider"  # fresh, metadata-checked provider data
    CACHED_PROVIDER = "cached_provider"  # last valid provider state after a failure
    SHEET = "sheet"  # Sheet value, quote time unknown
    DEMO = "demo"  # invented demo value
    NONE = "none"  # n. v.


class WatchlistEntryStatus(Enum):
    LIVE = "live"
    CACHED = "cached"  # last valid provider state, age in ``age_text``
    SHEET_VALUE = "sheet_value"  # only the Sheet substitute is shown
    METADATA_MISMATCH = "metadata_mismatch"  # Yahoo metadata contradict the Sheet
    UNAVAILABLE = "unavailable"  # "Daten nicht verfügbar"
    DEMO = "demo"


@dataclass(frozen=True, slots=True)
class WatchlistPeriodValue:
    """One period. ``reason`` explains n. v., or why a Sheet value substitutes."""

    period: str
    percent: Decimal | None
    origin: WatchlistOrigin
    reason: str | None = None
    reference_date: date | None = None

    def __post_init__(self) -> None:
        if self.period not in PERIODS:
            raise ValueError("period must be Heute, Woche, Monat or Jahr")
        if not isinstance(self.origin, WatchlistOrigin):
            raise TypeError("origin must be WatchlistOrigin")
        if self.percent is None:
            if self.origin is not WatchlistOrigin.NONE or not self.reason:
                raise ValueError("n. v. requires origin NONE and a reason")
        else:
            _decimal(self.percent, "percent")
            if self.origin is WatchlistOrigin.NONE:
                raise ValueError("a value requires an origin")
        if self.reason is not None:
            _text(self.reason, "reason")

    @property
    def available(self) -> bool:
        return self.percent is not None


@dataclass(frozen=True, slots=True)
class WatchlistEntry:
    """One Watchlist row with price and all four periods in trading currency.

    ``price`` is in ``price_unit``: the trading currency for provider data, EUR for
    the Sheet substitute (never converted). ``retrieved_at`` is when the provider
    data were fetched (oldest part used); ``age_text`` is set for cached state.
    """

    item: WatchlistItem
    status: WatchlistEntryStatus
    status_text: str
    periods: tuple[WatchlistPeriodValue, ...]
    price: Decimal | None = None
    price_unit: QuoteUnit | None = None
    price_origin: WatchlistOrigin = WatchlistOrigin.NONE
    status_reason: str | None = None
    current_date: date | None = None  # Session behind the provider values.
    market_open: bool | None = None
    retrieved_at: datetime | None = None
    age_text: str | None = None
    warnings: tuple[DataWarning, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.item, WatchlistItem):
            raise TypeError("item must be WatchlistItem")
        if not isinstance(self.status, WatchlistEntryStatus):
            raise TypeError("status must be WatchlistEntryStatus")
        _text(self.status_text, "status_text")
        if tuple(value.period for value in self.periods) != PERIODS:
            raise ValueError("periods must be Heute, Woche, Monat, Jahr in order")
        if (self.price is None) != (self.price_unit is None):
            raise ValueError("price and price_unit belong together")
        if self.price is not None:
            _decimal(self.price, "price")
        if self.retrieved_at is not None:
            _aware(self.retrieved_at, "retrieved_at")

    def period(self, label: str) -> WatchlistPeriodValue:
        return self.periods[PERIODS.index(label)]


@dataclass(frozen=True, slots=True)
class WatchlistBundle:
    """Self-consistent state for the Watchlist view; swapped atomically.

    After a failed refresh the complete previous bundle is returned with
    ``using_cached_stand``; without one ``available`` is False.
    """

    available: bool
    snapshot: WatchlistSnapshot
    entries: tuple[WatchlistEntry, ...]
    last_attempt_at: datetime
    using_cached_stand: bool = False
    status_message: str | None = None

    def __post_init__(self) -> None:
        _aware(self.last_attempt_at, "last_attempt_at")
        if not isinstance(self.entries, tuple) or not all(
            isinstance(entry, WatchlistEntry) for entry in self.entries
        ):
            raise TypeError("entries must be a tuple of WatchlistEntry")


def sheet_entry(
    item: WatchlistItem,
    reason: str = UNVERIFIED_MAPPING,
    *,
    period_reasons: Iterable[str] | None = None,
    status: WatchlistEntryStatus | None = None,
) -> WatchlistEntry:
    """Entry from the Sheet columns only (no provider values).

    ``Heute`` shows ``Veränd. (%)`` as the labelled substitute when the Sheet has
    one, ``Woche``/``Monat``/``Jahr`` are n. v. ``reason`` is why no provider value
    is used (``unverified_mapping`` for rows without usable mapping, else a
    provider or metadata code); ``period_reasons`` optionally gives it per period.
    Duplicate-symbol rows arrive here through their parser status, with the parser
    warning ``duplicate_yahoo_symbol`` kept in ``warnings``.
    """
    reasons = list(period_reasons) if period_reasons is not None else [reason] * 4
    if len(reasons) != 4:
        raise ValueError("period_reasons needs one reason per period")
    change = item.sheet_change_percent_points
    has_price = item.sheet_price.status is ValueStatus.AVAILABLE
    if change is not None:
        today = WatchlistPeriodValue("Heute", change, WatchlistOrigin.SHEET, reasons[0])
    else:
        missing = SHEET_VALUE_MISSING if reasons[0] == UNVERIFIED_MAPPING else reasons[0]
        today = WatchlistPeriodValue("Heute", None, WatchlistOrigin.NONE, missing)
    periods = (
        today,
        *(
            WatchlistPeriodValue(label, None, WatchlistOrigin.NONE, why)
            for label, why in zip(PERIODS[1:], reasons[1:])
        ),
    )
    has_value = has_price or change is not None
    if status is None:
        status = (
            WatchlistEntryStatus.SHEET_VALUE
            if has_value
            else WatchlistEntryStatus.UNAVAILABLE
        )
    if status is WatchlistEntryStatus.METADATA_MISMATCH:
        text = (
            f"Yahoo-Metadaten widersprechen dem Blatt ({reason}); "
            "kein Anbieterkurs übernommen"
        )
        if has_value:
            text += f"; {SHEET_VALUE_LABEL}"
    elif status is WatchlistEntryStatus.SHEET_VALUE:
        text = SHEET_VALUE_LABEL
        if reason != UNVERIFIED_MAPPING:
            text += f"; Anbieterdaten nicht verfügbar ({reason})"
    else:
        text = DATA_UNAVAILABLE_TEXT
        if reason != UNVERIFIED_MAPPING:
            text += f" ({reason})"
    return WatchlistEntry(
        item,
        status,
        text,
        periods,
        item.sheet_price.price if has_price else None,
        item.sheet_price.unit if has_price else None,
        WatchlistOrigin.SHEET if has_price else WatchlistOrigin.NONE,
        None if reason == UNVERIFIED_MAPPING else reason,
        warnings=item.warnings,
    )


def demo_entry(
    item: WatchlistItem,
    percents: Iterable[Decimal],
    price: Decimal,
    unit: QuoteUnit,
    current_date: date,
    retrieved_at: datetime,
) -> WatchlistEntry:
    """Invented mapped-row values for the demo; never labelled as provider data."""
    periods = tuple(
        WatchlistPeriodValue(label, percent, WatchlistOrigin.DEMO)
        for label, percent in zip(PERIODS, percents, strict=True)
    )
    return WatchlistEntry(
        item,
        WatchlistEntryStatus.DEMO,
        DEMO_LABEL,
        periods,
        price,
        unit,
        WatchlistOrigin.DEMO,
        current_date=current_date,
        retrieved_at=retrieved_at,
        warnings=item.warnings,
    )


def empty_bundle(
    snapshot: WatchlistSnapshot, now: datetime, message: str
) -> WatchlistBundle:
    """Unavailable bundle without usable rows (FAILED snapshot, no earlier state)."""
    if snapshot.status is not SnapshotStatus.FAILED:
        raise ValueError("an empty bundle needs a FAILED snapshot")
    return WatchlistBundle(False, snapshot, (), now, False, message)
