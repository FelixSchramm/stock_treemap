"""Immutable, offline data contracts; no parsing, mapping or valuation arithmetic.

Identifiers and provider mappings must be verified by the caller. Neither a name,
a display ticker nor a source row establishes instrument identity. Unknown units
and timestamps stay unknown; observations never acquire defaults from the clock.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class InstrumentType(Enum):
    STOCK = "stock"
    ETF = "etf"
    DERIVATIVE = "derivative"


class ValueStatus(Enum):
    AVAILABLE = "available"
    MISSING = "missing"
    INVALID = "invalid"


class CostBasisStatus(Enum):
    MISSING = "missing"
    INVALID = "invalid"
    UNVERIFIED = "unverified"
    VERIFIED_REMAINING = "verified_remaining"


class ValuationSource(Enum):
    PROVIDER = "provider"
    CACHED_PROVIDER = "cached_provider"
    SHEET = "sheet"


class PositionActivity(Enum):
    ACTIVE = "active"
    ZERO = "zero"
    NEGATIVE = "negative"


class SnapshotStatus(Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


def _decimal(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")


def _aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")


def _text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be nonblank text")


def _row(value: int) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("source_row must be a positive integer")


@dataclass(frozen=True, slots=True)
class DataWarning:
    """Sanitized explanation, optionally attached to a source row and field.

    Do not include raw cells, document identifiers or credentials in messages.
    """

    code: str
    message: str
    field: str | None = None
    source_row: int | None = None

    def __post_init__(self) -> None:
        _text(self.code, "code")
        _text(self.message, "message")
        if self.field is not None:
            _text(self.field, "field")
        if self.source_row is not None:
            _row(self.source_row)


def _warnings(value: tuple[DataWarning, ...]) -> None:
    if not isinstance(value, tuple) or not all(
        isinstance(warning, DataWarning) for warning in value
    ):
        raise TypeError("warnings must be a tuple of DataWarning")


@dataclass(frozen=True, slots=True)
class QuoteUnit:
    """Currency and scale for one quoted unit (GBP pence: GBP, Decimal('0.01')).

    A known currency with unknown major/minor unit uses scale_to_currency=None.
    Use None on the containing model when even the currency is unknown. This is
    unit metadata, not an FX rate; no conversion or rounding occurs here.
    """

    currency: str
    scale_to_currency: Decimal | None

    def __post_init__(self) -> None:
        if not (
            isinstance(self.currency, str)
            and len(self.currency) == 3
            and self.currency.isascii()
            and self.currency.isalpha()
            and self.currency.isupper()
        ):
            raise ValueError("currency must be a three-letter uppercase code")
        if self.scale_to_currency is not None:
            _decimal(self.scale_to_currency, "scale_to_currency")
            if self.scale_to_currency <= 0:
                raise ValueError("scale_to_currency must be positive")


@dataclass(frozen=True, slots=True)
class ProviderMapping:
    """Explicitly verified provider association, never inferred from a ticker."""

    provider: str
    symbol: str

    def __post_init__(self) -> None:
        _text(self.provider, "provider")
        _text(self.symbol, "symbol")


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_type: InstrumentType
    instrument_id: str | None = None
    display_ticker: str | None = None
    source_symbol: str | None = None
    trading_venue: str | None = None
    quote_unit: QuoteUnit | None = None
    provider_mapping: ProviderMapping | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_type, InstrumentType):
            raise TypeError("instrument_type must be InstrumentType")
        for name in ("instrument_id", "display_ticker", "source_symbol", "trading_venue"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name)
        if self.quote_unit is not None and not isinstance(self.quote_unit, QuoteUnit):
            raise TypeError("quote_unit must be QuoteUnit or None")
        if self.provider_mapping is not None:
            if not isinstance(self.provider_mapping, ProviderMapping):
                raise TypeError("provider_mapping must be ProviderMapping or None")
            if self.instrument_id is None:
                raise ValueError("provider_mapping requires verified instrument_id")


@dataclass(frozen=True, slots=True)
class Provenance:
    source: ValuationSource
    retrieved_at: datetime
    observed_at: datetime | None = None
    provider: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, ValuationSource):
            raise TypeError("source must be ValuationSource")
        _aware(self.retrieved_at, "retrieved_at")
        if self.observed_at is not None:
            _aware(self.observed_at, "observed_at")
        if self.source in (ValuationSource.PROVIDER, ValuationSource.CACHED_PROVIDER):
            _text(self.provider, "provider")
        elif self.provider is not None:
            raise ValueError("Sheet provenance cannot name a market provider")


@dataclass(frozen=True, slots=True)
class Valuation:
    """One per-unit price observation; no EUR value or return is inferred.

    AVAILABLE includes an explicitly observed zero. MISSING/INVALID have no
    usable price. Unknown currency or scale prevents conversion of numeric prices.
    A Sheet EUR fallback carries QuoteUnit('EUR', Decimal('1')); its actual
    observed_at may be unknown even when retrieved_at is known.
    """

    price: Decimal | None
    status: ValueStatus
    provenance: Provenance
    unit: QuoteUnit | None = None
    warnings: tuple[DataWarning, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, ValueStatus):
            raise TypeError("status must be ValueStatus")
        if not isinstance(self.provenance, Provenance):
            raise TypeError("provenance must be Provenance")
        if self.unit is not None and not isinstance(self.unit, QuoteUnit):
            raise TypeError("unit must be QuoteUnit or None")
        if self.status is ValueStatus.AVAILABLE:
            _decimal(self.price, "price")
            if self.price < 0:
                raise ValueError("price cannot be negative")
        elif self.price is not None:
            raise ValueError("missing or invalid price must be None")
        _warnings(self.warnings)


@dataclass(frozen=True, slots=True)
class CostBasis:
    """EUR cost of remaining units; an imported number is initially UNVERIFIED.

    VERIFIED_REMAINING must be supplied only after checking the source's meaning
    after partial sales. A verified zero remains known but has no return ratio.
    """

    amount_eur: Decimal | None = None
    status: CostBasisStatus = CostBasisStatus.MISSING

    def __post_init__(self) -> None:
        if not isinstance(self.status, CostBasisStatus):
            raise TypeError("status must be CostBasisStatus")
        if self.status in (CostBasisStatus.UNVERIFIED, CostBasisStatus.VERIFIED_REMAINING):
            _decimal(self.amount_eur, "amount_eur")
            if self.amount_eur < 0:
                raise ValueError("amount_eur cannot be negative")
        elif self.amount_eur is not None:
            raise ValueError("missing or invalid cost basis must be None")

    @property
    def usable_for_since_purchase(self) -> bool:
        """Cost-side prerequisite only; price/quantity/FX still need validation."""
        return (
            self.status is CostBasisStatus.VERIFIED_REMAINING
            and self.amount_eur > 0
        )


@dataclass(frozen=True, slots=True)
class Position:
    name: str
    quantity: Decimal
    instrument: Instrument
    category: str | None
    source_row: int
    imported_at: datetime
    cost_basis: CostBasis = field(default_factory=CostBasis)
    valuation: Valuation | None = None
    warnings: tuple[DataWarning, ...] = ()

    def __post_init__(self) -> None:
        _text(self.name, "name")
        _decimal(self.quantity, "quantity")
        if not isinstance(self.instrument, Instrument):
            raise TypeError("instrument must be Instrument")
        if self.category is not None:
            _text(self.category, "category")
        _row(self.source_row)
        _aware(self.imported_at, "imported_at")
        if not isinstance(self.cost_basis, CostBasis):
            raise TypeError("cost_basis must be CostBasis")
        if self.valuation is not None and not isinstance(self.valuation, Valuation):
            raise TypeError("valuation must be Valuation or None")
        _warnings(self.warnings)

    @property
    def activity(self) -> PositionActivity:
        if self.quantity > 0:
            return PositionActivity.ACTIVE
        if self.quantity == 0:
            return PositionActivity.ZERO
        return PositionActivity.NEGATIVE


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """One import attempt; COMPLETE with no positions is a successful empty source.

    PARTIAL carries only accepted rows and an explanation of missing coverage.
    FAILED never replaces a previous successful snapshot with an empty success.
    Keeping a last successful snapshot is an adapter/cache concern.
    """

    attempted_at: datetime
    status: SnapshotStatus
    positions: tuple[Position, ...] = ()
    warnings: tuple[DataWarning, ...] = ()

    def __post_init__(self) -> None:
        _aware(self.attempted_at, "attempted_at")
        if not isinstance(self.status, SnapshotStatus):
            raise TypeError("status must be SnapshotStatus")
        if not isinstance(self.positions, tuple) or not all(
            isinstance(position, Position) for position in self.positions
        ):
            raise TypeError("positions must be a tuple of Position")
        _warnings(self.warnings)
        if self.status is SnapshotStatus.FAILED and self.positions:
            raise ValueError("failed snapshot cannot contain imported positions")
        if self.status is not SnapshotStatus.COMPLETE and not self.warnings:
            raise ValueError("partial or failed snapshot requires an explanation")
