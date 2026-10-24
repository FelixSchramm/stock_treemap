"""Pure EUR Long valuations using explicit quotes, listings and directed FX.

No provider lookup, clock, rounding to cents or cost-basis calculation occurs.
Result members retain the original observations and their source status/times.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import (
    MAX_EMAX,
    MIN_EMIN,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Underflow,
    localcontext,
)
from enum import Enum

from portfolio_dashboard.instruments import (
    VerifiedListing,
    group_positions,
    resolve_listing,
)
from portfolio_dashboard.models import (
    DataWarning,
    PortfolioSnapshot,
    Position,
    PositionActivity,
    Provenance,
    QuoteUnit,
    SnapshotStatus,
    ValuationSource,
    ValueStatus,
)


@dataclass(frozen=True, slots=True)
class FxRate:
    """Explicit major-unit rate: one base currency buys rate quote currency.

    The caller verifies the observation and supplies its actual provenance. An
    inverse rate is never silently inverted; missing FX is represented by None.
    """

    base_currency: str
    quote_currency: str
    rate: Decimal
    provenance: Provenance
    warnings: tuple[DataWarning, ...] = ()

    def __post_init__(self) -> None:
        QuoteUnit(self.base_currency, Decimal("1"))
        QuoteUnit(self.quote_currency, Decimal("1"))
        if not isinstance(self.rate, Decimal):
            raise TypeError("rate must be Decimal")
        if not self.rate.is_finite() or self.rate <= 0:
            raise ValueError("rate must be finite and positive")
        if not isinstance(self.provenance, Provenance):
            raise TypeError("provenance must be Provenance")
        if not isinstance(self.warnings, tuple) or not all(
            isinstance(warning, DataWarning) for warning in self.warnings
        ):
            raise TypeError("warnings must be a tuple of DataWarning")


class EurValuationStatus(Enum):
    AVAILABLE = "available"
    INACTIVE = "inactive"
    MISSING_PRICE = "missing_price"
    INVALID_PRICE = "invalid_price"
    UNKNOWN_UNIT = "unknown_unit"
    UNVERIFIED_INSTRUMENT = "unverified_instrument"
    UNVERIFIED_PROVIDER = "unverified_provider"
    UNIT_MISMATCH = "unit_mismatch"
    MISSING_FX = "missing_fx"
    FX_DIRECTION_MISMATCH = "fx_direction_mismatch"


@dataclass(frozen=True, slots=True)
class PositionValuation:
    """One attempt, retaining the relevant FX candidate even if direction fails."""

    position: Position
    amount_eur: Decimal | None
    status: EurValuationStatus
    fx_rate: FxRate | None = None
    weight_fraction: Decimal | None = None

    @property
    def warnings(self) -> tuple[DataWarning, ...]:
        quote = self.position.valuation
        return (
            self.position.warnings
            + (() if quote is None else quote.warnings)
            + (() if self.fx_rate is None else self.fx_rate.warnings)
        )


def _total(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    nonzero = tuple(value for value in values if value)
    if not nonzero:
        return Decimal("0")
    # Preserve all finite input digits when adding differently scaled values.
    with localcontext(Emax=MAX_EMAX, Emin=MIN_EMIN, clamp=0) as context:
        context.prec = max(
            context.prec,
            max(value.adjusted() for value in nonzero)
            - min(value.as_tuple().exponent for value in nonzero)
            + len(str(len(nonzero)))
            + 1,
        )
        for signal in (Inexact, InvalidOperation, Overflow):
            context.traps[signal] = True
        return sum(nonzero, Decimal("0"))


def _product(factors: tuple[Decimal, ...]) -> Decimal:
    if not all(factors):
        return Decimal("0")
    parts = tuple(factor.as_tuple() for factor in factors)
    with localcontext(Emax=MAX_EMAX, Emin=MIN_EMIN, clamp=0) as context:
        context.prec = max(context.prec, sum(len(part.digits) for part in parts))
        for signal in (Inexact, InvalidOperation, Overflow):
            context.traps[signal] = True
        # Combine exponents only after multiplying integer coefficients. A large
        # intermediate price product may become representable after small FX.
        coefficient = Decimal("1")
        for part in parts:
            coefficient *= Decimal((part.sign, part.digits, 0))
        product = coefficient.as_tuple()
        return context.create_decimal(
            (product.sign, product.digits, sum(part.exponent for part in parts))
        )


def _fraction(numerator: Decimal, denominator: Decimal) -> Decimal:
    # Ratios may recur, so retain caller precision, with the full exponent range.
    with localcontext(Emax=MAX_EMAX, Emin=MIN_EMIN, clamp=0) as context:
        for signal in (DivisionByZero, InvalidOperation, Overflow, Underflow):
            context.traps[signal] = True
        return numerator / denominator


def value_position(
    position: Position,
    listings: tuple[VerifiedListing, ...],
    fx_rate: FxRate | None = None,
) -> PositionValuation:
    """Value one active Long position; unavailable inputs remain unavailable.

    Provider prices require the verified listing's provider and quote unit.
    Explicit Sheet units are authoritative for Sheet prices, including EUR
    fallbacks for foreign listings or instruments without provider coverage.
    Exact arithmetic exceeding Decimal's representable range raises rather than
    publishing a rounded, infinite or silently underflowed valuation.
    """
    listing = resolve_listing(position.instrument, listings)
    if fx_rate is not None and not isinstance(fx_rate, FxRate):
        raise TypeError("fx_rate must be FxRate or None")

    def unavailable(
        status: EurValuationStatus, rate: FxRate | None = None
    ) -> PositionValuation:
        return PositionValuation(position, None, status, rate)

    if position.activity is not PositionActivity.ACTIVE:
        return unavailable(EurValuationStatus.INACTIVE)
    quote = position.valuation
    if quote is None or quote.status is ValueStatus.MISSING:
        return unavailable(EurValuationStatus.MISSING_PRICE)
    if quote.status is ValueStatus.INVALID:
        return unavailable(EurValuationStatus.INVALID_PRICE)
    unit = quote.unit
    if unit is None or unit.scale_to_currency is None:
        return unavailable(EurValuationStatus.UNKNOWN_UNIT)
    if quote.provenance.source is not ValuationSource.SHEET:
        if listing is None:
            return unavailable(EurValuationStatus.UNVERIFIED_INSTRUMENT)
        mapping = listing.instrument.provider_mapping
        if mapping is None or mapping.provider != quote.provenance.provider:
            return unavailable(EurValuationStatus.UNVERIFIED_PROVIDER)
        if unit != listing.instrument.quote_unit:
            return unavailable(EurValuationStatus.UNIT_MISMATCH)
    applied_fx = None
    factors = (position.quantity, quote.price, unit.scale_to_currency)
    if unit.currency != "EUR":
        if fx_rate is None:
            return unavailable(EurValuationStatus.MISSING_FX)
        if (fx_rate.base_currency, fx_rate.quote_currency) != (unit.currency, "EUR"):
            return unavailable(EurValuationStatus.FX_DIRECTION_MISMATCH, fx_rate)
        applied_fx = fx_rate
        factors += (fx_rate.rate,)
    amount = _product(factors)
    return PositionValuation(position, amount, EurValuationStatus.AVAILABLE, applied_fx)


@dataclass(frozen=True, slots=True)
class ValuationGroup:
    """Verified identical instruments; each partial position stays inspectable."""

    members: tuple[PositionValuation, ...]
    amount_eur: Decimal | None
    weight_fraction: Decimal | None


@dataclass(frozen=True, slots=True)
class PortfolioValuation:
    snapshot: PortfolioSnapshot
    positions: tuple[PositionValuation, ...]
    groups: tuple[ValuationGroup, ...]
    total_eur: Decimal | None

    @property
    def active_count(self) -> int:
        return sum(
            member.position.activity is PositionActivity.ACTIVE
            for member in self.positions
        )

    @property
    def valued_count(self) -> int:
        return sum(member.amount_eur is not None for member in self.positions)

    @property
    def coverage_fraction(self) -> Decimal | None:
        """Count coverage of accepted active rows, never a value-coverage claim."""
        if not self.active_count:
            return None
        return _fraction(Decimal(self.valued_count), Decimal(self.active_count))

    @property
    def status(self) -> SnapshotStatus:
        if self.snapshot.status is SnapshotStatus.FAILED:
            return SnapshotStatus.FAILED
        if (
            self.snapshot.status is SnapshotStatus.PARTIAL
            or self.valued_count < self.active_count
        ):
            return SnapshotStatus.PARTIAL
        return SnapshotStatus.COMPLETE


def value_portfolio(
    snapshot: PortfolioSnapshot,
    listings: tuple[VerifiedListing, ...],
    fx_rates: Mapping[str, FxRate] | None = None,
) -> PortfolioValuation:
    """Aggregate and weight exactly the same available, positive-quantity rows.

    FX is explicitly selected by the quote's currency key; every selected rate
    still has to point from that currency to EUR. There is no inverse fallback.
    Weights use the caller's Decimal precision and rounding, with the full
    exponent range and no display rounding. A zero or unavailable total has no
    defined weights. Unrepresentable arithmetic raises, as in value_position.
    """
    groups = group_positions(snapshot.positions, listings)
    rates = {} if fx_rates is None else fx_rates
    values = tuple(
        value_position(
            position,
            listings,
            rates.get(position.valuation.unit.currency)
            if position.valuation is not None and position.valuation.unit is not None
            else None,
        )
        for position in snapshot.positions
    )
    total = _total(
        tuple(value.amount_eur for value in values if value.amount_eur is not None)
    )
    if (
        total is None
        and snapshot.status is SnapshotStatus.COMPLETE
        and not any(
            value.position.activity is PositionActivity.ACTIVE for value in values
        )
    ):
        total = Decimal("0")

    def weight(amount: Decimal | None) -> Decimal | None:
        return (
            _fraction(amount, total)
            if amount is not None and total is not None and total > 0
            else None
        )

    values = tuple(
        replace(value, weight_fraction=weight(value.amount_eur)) for value in values
    )
    by_position = {id(value.position): value for value in values}
    valued_groups = []
    for group in groups:
        members = tuple(by_position[id(position)] for position in group)
        amount = _total(
            tuple(
                member.amount_eur for member in members if member.amount_eur is not None
            )
        )
        valued_groups.append(ValuationGroup(members, amount, weight(amount)))
    return PortfolioValuation(snapshot, values, tuple(valued_groups), total)
