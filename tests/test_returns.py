"""Synthetic financial examples; no actual holdings or provider observations."""

import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import MAX_EMAX, Decimal, Inexact, Overflow, Underflow, localcontext

import pytest

from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.models import (
    CostBasis,
    CostBasisStatus,
    DataWarning,
    Instrument,
    InstrumentType,
    PortfolioSnapshot,
    Position,
    Provenance,
    QuoteUnit,
    SnapshotStatus,
    Valuation,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.returns import (
    PersonalReturnStatus,
    personal_return,
    portfolio_returns,
)
from portfolio_dashboard.valuations import (
    EurValuationStatus,
    FxRate,
    value_portfolio,
    value_position,
)

RETRIEVED = datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc)
OBSERVED = RETRIEVED - timedelta(minutes=10)
EUR = QuoteUnit("EUR", Decimal("1"))
SHEET = Provenance(ValuationSource.SHEET, RETRIEVED)
LISTING = VerifiedListing(
    Instrument(
        InstrumentType.STOCK,
        instrument_id="synthetic-stock",
        trading_venue="SYNTHETIC-A",
        quote_unit=EUR,
    ),
    equity_form=EquityForm.SHARE,
)


def position(value="120", cost="100", *, quantity="1", row=3):
    return Position(
        "Synthetic stock",
        Decimal(quantity),
        LISTING.instrument,
        "Synthetic",
        row,
        RETRIEVED,
        CostBasis(Decimal(cost), CostBasisStatus.VERIFIED_REMAINING),
        Valuation(Decimal(value), ValueStatus.AVAILABLE, SHEET, EUR),
    )


def returns(*positions, status=SnapshotStatus.COMPLETE, warnings=(), listings=(LISTING,)):
    snapshot = PortfolioSnapshot(RETRIEVED, status, positions, warnings)
    return portfolio_returns(value_portfolio(snapshot, listings))


def test_individual_and_combined_returns_use_sums_of_the_same_cost_basis():
    result = returns(position(), position("330", "300", row=4))

    assert [member.return_percent for member in result.positions] == [
        Decimal("20"), Decimal("10")
    ]
    assert [member.gain_eur for member in result.positions] == [
        Decimal("20"), Decimal("30")
    ]
    assert result.total.eligible_market_value_eur == Decimal("450")
    assert result.total.eligible_cost_basis_eur == Decimal("400")
    assert result.total.gain_eur == Decimal("50")
    assert result.total.return_percent == Decimal("12.5")
    assert result.groups[0].return_percent == Decimal("12.5")
    assert result.total.active_count == result.total.eligible_count == 2
    assert result.total.coverage_fraction == Decimal("1")
    assert result.status is SnapshotStatus.COMPLETE


def test_missing_cost_keeps_portfolio_subset_but_blocks_instrument_group_return():
    unknown = replace(position("900", row=5), cost_basis=CostBasis())
    result = returns(position(), position("330", "300", row=4), unknown)

    assert result.valuation.total_eur == Decimal("1350")
    assert result.total.eligible_market_value_eur == Decimal("450")
    assert result.total.eligible_cost_basis_eur == Decimal("400")
    assert result.total.return_percent == Decimal("12.5")
    assert result.total.active_count == 3 and result.total.eligible_count == 2
    with localcontext() as context:
        context.prec = 28
        assert result.total.coverage_fraction == Decimal(2) / Decimal(3)
    assert result.status is SnapshotStatus.PARTIAL
    assert result.positions[-1].status is PersonalReturnStatus.MISSING_COST
    assert result.positions[-1].return_percent is None
    group = result.groups[0]
    assert group.return_percent is None
    assert group.eligible_market_value_eur == result.total.eligible_market_value_eur
    assert group.eligible_cost_basis_eur == result.total.eligible_cost_basis_eur
    assert group.coverage_fraction == result.total.coverage_fraction
    assert group.members[-1].valuation.position is unknown


@pytest.mark.parametrize(
    ("cost", "expected"),
    [
        (CostBasis(), PersonalReturnStatus.MISSING_COST),
        (CostBasis(None, CostBasisStatus.INVALID), PersonalReturnStatus.INVALID_COST),
        (
            CostBasis(Decimal("100"), CostBasisStatus.UNVERIFIED),
            PersonalReturnStatus.UNVERIFIED_COST,
        ),
        (
            CostBasis(Decimal("0"), CostBasisStatus.VERIFIED_REMAINING),
            PersonalReturnStatus.NONPOSITIVE_COST,
        ),
    ],
)
def test_unusable_costs_never_enter_either_side_of_the_return(cost, expected):
    original = replace(position(), cost_basis=cost)
    result = returns(original)
    member = result.positions[0]
    assert member.status is expected
    assert member.return_percent is member.gain_eur is None
    assert member.valuation.position.cost_basis is cost
    assert result.valuation.total_eur == Decimal("120")
    assert result.total.eligible_market_value_eur is None
    assert result.total.eligible_cost_basis_eur is None
    assert result.total.return_percent is result.total.gain_eur is None
    assert result.total.coverage_fraction == Decimal("0")
    assert result.groups[0].return_percent is None
    assert result.status is SnapshotStatus.PARTIAL


@pytest.mark.parametrize("status", [CostBasisStatus.UNVERIFIED, CostBasisStatus.VERIFIED_REMAINING])
def test_negative_cost_is_rejected_at_the_domain_boundary(status):
    with pytest.raises(ValueError, match="negative"):
        CostBasis(Decimal("-100"), status)


def test_observed_zero_value_is_a_valid_minus_one_hundred_percent_return():
    result = returns(position("0"))
    assert result.positions[0].return_percent == Decimal("-100")
    assert result.positions[0].gain_eur == Decimal("-100")
    assert result.total.eligible_market_value_eur == Decimal("0")
    assert result.total.eligible_cost_basis_eur == Decimal("100")
    assert result.total.return_percent == result.groups[0].return_percent == Decimal("-100")
    assert result.total.coverage_fraction == Decimal("1")
    assert result.status is SnapshotStatus.COMPLETE


@pytest.mark.parametrize(
    "quote",
    [None, Valuation(None, ValueStatus.MISSING, SHEET, EUR), Valuation(None, ValueStatus.INVALID, SHEET, EUR)],
)
def test_missing_values_exclude_their_verified_cost_too(quote):
    unknown = replace(position("900", "500", row=4), valuation=quote)
    result = returns(position(), unknown)
    assert result.total.eligible_market_value_eur == Decimal("120")
    assert result.total.eligible_cost_basis_eur == Decimal("100")
    assert result.total.return_percent == Decimal("20")
    assert result.total.coverage_fraction == Decimal("0.5")
    assert result.positions[-1].status is PersonalReturnStatus.UNAVAILABLE_VALUE
    assert result.groups[0].return_percent is None


@pytest.mark.parametrize("quantity", ["0", "-10"])
def test_inactive_rows_do_not_require_cost_or_enter_coverage_or_group_return(quantity):
    inactive = replace(position(quantity=quantity), cost_basis=CostBasis())
    result = returns(position(), inactive)
    assert result.positions[-1].status is PersonalReturnStatus.INACTIVE
    assert result.total.active_count == result.total.eligible_count == 1
    assert result.total.eligible_cost_basis_eur == Decimal("100")
    assert result.total.return_percent == result.groups[0].return_percent == Decimal("20")
    assert result.groups[0].coverage_fraction == Decimal("1")
    assert result.status is SnapshotStatus.COMPLETE


def test_distinct_groups_repeated_row_ids_and_same_object_occurrences_are_preserved():
    first = position()
    second = position("330", "300")  # Same source row and instrument ID, new position.
    other_listing = replace(
        LISTING, instrument=replace(LISTING.instrument, instrument_id="synthetic-other")
    )
    other = replace(position("50", "100"), instrument=other_listing.instrument)
    result = returns(first, second, first, other, listings=(LISTING, other_listing))
    assert len(result.positions) == 4
    assert [member.return_percent for member in result.positions] == [20, 10, 20, -50]
    assert result.groups[0].members == result.positions[:3]
    assert result.groups[0].members[0] is result.positions[0]
    assert result.groups[0].members[2] is result.positions[2]
    assert result.groups[0].eligible_market_value_eur == Decimal("570")
    assert result.groups[0].eligible_cost_basis_eur == Decimal("500")
    assert result.groups[0].return_percent == Decimal("14")
    assert result.groups[1].return_percent == Decimal("-50")
    assert result.total.eligible_market_value_eur == Decimal("620")
    assert result.total.eligible_cost_basis_eur == Decimal("600")
    assert result.total.active_count == result.total.eligible_count == 4


def test_unresolved_instrument_ids_do_not_invent_group_identity():
    result = returns(position(), position("330", "300"), listings=())
    assert len(result.groups) == 2
    assert [group.return_percent for group in result.groups] == [20, 10]
    assert result.total.return_percent == Decimal("12.5")


def test_empty_inactive_partial_and_failed_sources_never_invent_a_return():
    warning = DataWarning("synthetic_import", "Synthetic incomplete import")
    empty = returns()
    inactive = returns(position(quantity="0"))
    partial_empty = returns(status=SnapshotStatus.PARTIAL, warnings=(warning,))
    failed = returns(status=SnapshotStatus.FAILED, warnings=(warning,))
    partial = returns(position(), status=SnapshotStatus.PARTIAL, warnings=(warning,))
    for result in (empty, inactive, partial_empty, failed):
        assert result.total.return_percent is None
        assert result.total.eligible_market_value_eur is None
        assert result.total.eligible_cost_basis_eur is None
        assert result.total.coverage_fraction is None
    assert empty.status is inactive.status is SnapshotStatus.COMPLETE
    assert partial_empty.status is partial.status is SnapshotStatus.PARTIAL
    assert failed.status is SnapshotStatus.FAILED
    assert partial.total.return_percent == Decimal("20")
    assert partial.total.coverage_fraction == Decimal("1")  # Accepted rows only.
    assert partial.valuation.snapshot.warnings == (warning,)


def test_manual_or_cached_value_status_warnings_and_fx_times_remain_available():
    price_warning = DataWarning("synthetic_cached", "Synthetic cached price")
    fx_warning = DataWarning("synthetic_fx", "Synthetic stale FX observation")
    usd = QuoteUnit("USD", Decimal("1"))
    original = replace(
        position("150"),
        valuation=Valuation(
            Decimal("150"), ValueStatus.AVAILABLE,
            Provenance(ValuationSource.SHEET, RETRIEVED, OBSERVED), usd,
            (price_warning,),
        ),
    )
    fx_source = Provenance(
        ValuationSource.CACHED_PROVIDER, RETRIEVED, OBSERVED - timedelta(hours=1),
        "synthetic-fx",
    )
    rate = FxRate("USD", "EUR", Decimal("0.8"), fx_source, (fx_warning,))
    valuation = value_portfolio(
        PortfolioSnapshot(RETRIEVED, SnapshotStatus.COMPLETE, (original,)),
        (LISTING,), {"USD": rate},
    )
    result = portfolio_returns(valuation)
    assert result.valuation is valuation
    assert result.positions[0].valuation is valuation.positions[0]
    assert result.total.return_percent == Decimal("20")
    assert result.positions[0].valuation.warnings == (price_warning, fx_warning)
    assert result.positions[0].valuation.fx_rate.provenance is fx_source
    assert result.positions[0].valuation.position.valuation.provenance.observed_at == OBSERVED
    wrong = personal_return(value_position(original, (LISTING,)))
    assert wrong.status is PersonalReturnStatus.UNAVAILABLE_VALUE
    assert wrong.valuation.status is EurValuationStatus.MISSING_FX


def test_fees_only_contribute_when_in_the_explicitly_verified_remaining_basis():
    warning = DataWarning(
        "verified_cost_includes_fees", "Synthetic remaining EUR cost includes fees",
        field="cost_basis",
    )
    included = replace(position("120", "105"), warnings=(warning,))
    unverified = replace(
        included, cost_basis=CostBasis(Decimal("105"), CostBasisStatus.UNVERIFIED)
    )
    with localcontext() as context:
        context.prec = 12
        result = returns(position(), included, unverified)
        expected = Decimal("15") / Decimal("105") * Decimal("100")
    assert result.positions[0].return_percent == Decimal("20")
    assert result.positions[1].return_percent == expected
    assert result.positions[1].valuation.warnings == (warning,)
    assert result.positions[2].return_percent is None
    assert result.total.eligible_cost_basis_eur == Decimal("205")
    assert result.total.eligible_market_value_eur == Decimal("240")


def test_exact_subtotals_and_small_gains_survive_low_precision():
    original = position(
        "1000000000000000000000000000000.0000000001",
        "1000000000000000000000000000000",
    )
    with localcontext() as context:
        context.prec = 6
        result = returns(original, position("0.0000000002", "0.0000000001"))
        assert context.prec == 6
    assert result.positions[0].gain_eur == Decimal("0.0000000001")
    assert result.positions[0].return_percent == Decimal("1e-38")
    assert result.total.eligible_market_value_eur == Decimal(
        "1000000000000000000000000000000.0000000003"
    )
    assert result.total.eligible_cost_basis_eur == Decimal(
        "1000000000000000000000000000000.0000000001"
    )
    assert result.total.gain_eur == Decimal("0.0000000002")
    assert result.total.return_percent == Decimal("2e-38")


@pytest.mark.parametrize("exponent", [1000000, -1000000])
def test_returns_use_full_exponent_range_without_changing_caller_context(exponent):
    with localcontext() as context:
        context.prec = 6
        context.Emax = 9
        context.Emin = -9
        context.traps[Overflow] = context.traps[Underflow] = False
        result = returns(position(f"1.2e{exponent}", f"1e{exponent}"))
        assert (context.prec, context.Emax, context.Emin) == (6, 9, -9)
    assert result.total.return_percent == Decimal("20")
    assert result.total.gain_eur == Decimal(f"2e{exponent - 1}")


def test_recurring_returns_use_caller_precision_and_rounding_once():
    with localcontext() as context:
        context.prec = 6
        context.traps[Inexact] = False
        result = returns(position("4", "3"))
    assert result.total.return_percent == Decimal("33.3333")
    assert result.positions[0].return_percent == result.groups[0].return_percent


def test_large_representable_gain_does_not_overflow_before_dividing_by_large_cost():
    result = returns(position(f"2e{MAX_EMAX}", f"1e{MAX_EMAX}"))
    assert result.positions[0].gain_eur == Decimal(f"1e{MAX_EMAX}")
    assert result.positions[0].return_percent == Decimal("100")
    assert result.total.return_percent == Decimal("100")


def test_returns_are_offline_without_ui_or_provider_imports():
    code = """
import importlib.abc
import socket
import sys
class OfflineBoundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {'streamlit', 'pandas', 'plotly', 'yfinance', 'requests', 'gspread'}:
            raise AssertionError(f'Unexpected dependency: {fullname}')
sys.meta_path.insert(0, OfflineBoundary())
def no_network(*args, **kwargs):
    raise AssertionError('Returns attempted a network connection')
socket.socket = no_network
socket.create_connection = no_network
from datetime import datetime, timezone
from portfolio_dashboard.models import PortfolioSnapshot, SnapshotStatus
from portfolio_dashboard.valuations import value_portfolio
from portfolio_dashboard.returns import portfolio_returns
snapshot = PortfolioSnapshot(datetime(2026, 1, 5, tzinfo=timezone.utc), SnapshotStatus.COMPLETE)
assert portfolio_returns(value_portfolio(snapshot, ())).total.return_percent is None
"""
    subprocess.run([sys.executable, "-c", code], check=True)
