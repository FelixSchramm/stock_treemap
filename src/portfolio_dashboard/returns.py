"""Pure unrealized EUR returns for explicitly verified remaining cost bases.

Inputs are the EUR valuation results, not prices or transaction histories. The
caller must verify the cost of the units still held; no cost is inferred here.
"""

from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from portfolio_dashboard.models import (
    CostBasisStatus,
    PositionActivity,
    SnapshotStatus,
)
from portfolio_dashboard.valuations import (
    EurValuationStatus,
    PortfolioValuation,
    PositionValuation,
    _fraction,
    _product,
    _total,
)


class PersonalReturnStatus(Enum):
    AVAILABLE = "available"
    INACTIVE = "inactive"
    UNAVAILABLE_VALUE = "unavailable_value"
    MISSING_COST = "missing_cost"
    INVALID_COST = "invalid_cost"
    UNVERIFIED_COST = "unverified_cost"
    NONPOSITIVE_COST = "nonpositive_cost"


def _gain(market_value: Decimal, cost_basis: Decimal) -> Decimal:
    # Share the valuation layer's exact sums/products and full-range ratios.
    return _total((market_value, cost_basis.copy_negate()))


def _percent(gain: Decimal, cost_basis: Decimal) -> Decimal:
    # Subtract before dividing to preserve small gains. Scale after division so
    # a large but representable cost/gain does not overflow an intermediate ×100.
    return _product((_fraction(gain, cost_basis), Decimal("100")))


@dataclass(frozen=True, slots=True)
class PositionReturn:
    valuation: PositionValuation
    status: PersonalReturnStatus
    gain_eur: Decimal | None = None
    return_percent: Decimal | None = None

    @property
    def eligible(self) -> bool:
        return self.status is PersonalReturnStatus.AVAILABLE


def personal_return(valuation: PositionValuation) -> PositionReturn:
    """Calculate (EUR value / verified positive remaining EUR cost − 1) × 100.

    An observed zero value is eligible and returns −100%. Missing values and
    missing, invalid, unverified or nonpositive costs have no return. The input
    retains all price/FX provenance, warnings and the original cost status.
    """
    position = valuation.position
    if position.activity is not PositionActivity.ACTIVE:
        return PositionReturn(valuation, PersonalReturnStatus.INACTIVE)
    if (
        valuation.status is not EurValuationStatus.AVAILABLE
        or valuation.amount_eur is None
    ):
        return PositionReturn(valuation, PersonalReturnStatus.UNAVAILABLE_VALUE)
    cost = position.cost_basis
    unavailable = {
        CostBasisStatus.MISSING: PersonalReturnStatus.MISSING_COST,
        CostBasisStatus.INVALID: PersonalReturnStatus.INVALID_COST,
        CostBasisStatus.UNVERIFIED: PersonalReturnStatus.UNVERIFIED_COST,
    }
    if cost.status in unavailable:
        return PositionReturn(valuation, unavailable[cost.status])
    if not cost.usable_for_since_purchase:
        return PositionReturn(valuation, PersonalReturnStatus.NONPOSITIVE_COST)
    gain = _gain(valuation.amount_eur, cost.amount_eur)
    return PositionReturn(
        valuation, PersonalReturnStatus.AVAILABLE, gain, _percent(gain, cost.amount_eur)
    )


@dataclass(frozen=True, slots=True)
class ReturnAggregate:
    """Both EUR subtotals always refer to exactly the eligible members.

    A group requires complete active-member coverage for its percentage. A
    portfolio percentage describes its eligible subset, with coverage alongside.
    """

    members: tuple[PositionReturn, ...]
    eligible_market_value_eur: Decimal | None
    eligible_cost_basis_eur: Decimal | None
    gain_eur: Decimal | None
    return_percent: Decimal | None

    @property
    def active_count(self) -> int:
        return sum(
            member.valuation.position.activity is PositionActivity.ACTIVE
            for member in self.members
        )

    @property
    def eligible_count(self) -> int:
        return sum(member.eligible for member in self.members)

    @property
    def coverage_fraction(self) -> Decimal | None:
        """Count coverage of accepted active source rows, not value coverage."""
        if not self.active_count:
            return None
        return _fraction(Decimal(self.eligible_count), Decimal(self.active_count))


def _aggregate(
    members: tuple[PositionReturn, ...], *, require_complete: bool
) -> ReturnAggregate:
    eligible = tuple(member for member in members if member.eligible)
    market = _total(tuple(member.valuation.amount_eur for member in eligible))
    cost = _total(
        tuple(member.valuation.position.cost_basis.amount_eur for member in eligible)
    )
    gain = None if not eligible else _gain(market, cost)
    complete = all(
        member.eligible
        for member in members
        if member.valuation.position.activity is PositionActivity.ACTIVE
    )
    percent = (
        _percent(gain, cost)
        if eligible and (complete or not require_complete)
        else None
    )
    return ReturnAggregate(members, market, cost, gain, percent)


@dataclass(frozen=True, slots=True)
class PortfolioReturns:
    valuation: PortfolioValuation
    positions: tuple[PositionReturn, ...]
    groups: tuple[ReturnAggregate, ...]
    total: ReturnAggregate

    @property
    def status(self) -> SnapshotStatus:
        if self.valuation.status is SnapshotStatus.FAILED:
            return SnapshotStatus.FAILED
        if (
            self.valuation.status is SnapshotStatus.PARTIAL
            or self.total.eligible_count < self.total.active_count
        ):
            return SnapshotStatus.PARTIAL
        return SnapshotStatus.COMPLETE


def portfolio_returns(valuation: PortfolioValuation) -> PortfolioReturns:
    """Aggregate verified costs and EUR values from the same eligible rows.

    Pass an unmodified value_portfolio result. Its verified instrument groups
    are retained; neither source-row numbers nor claimed IDs establish identity.
    Inactive rows stay inspectable and never enter costs, values or coverage.
    No dividends, realized sales, taxes or additional fees are added.
    """
    positions = tuple(personal_return(member) for member in valuation.positions)
    # Match original objects by occurrence, never by source-row or instrument ID.
    # Even the same Position object appearing twice remains two source members.
    by_position = defaultdict(deque)
    for member in positions:
        by_position[id(member.valuation.position)].append(member)
    groups = tuple(
        _aggregate(
            tuple(by_position[id(member.position)].popleft() for member in group.members),
            require_complete=True,
        )
        for group in valuation.groups
    )
    return PortfolioReturns(
        valuation, positions, groups, _aggregate(positions, require_complete=False)
    )
