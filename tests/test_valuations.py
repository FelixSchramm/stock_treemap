"""Synthetic valuation examples: no actual holdings, provider data or network."""

import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import (
    MAX_EMAX,
    MIN_EMIN,
    Decimal,
    Inexact,
    Overflow,
    Underflow,
    localcontext,
)

import pytest

from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.models import (
    DataWarning,
    Instrument,
    InstrumentType,
    PortfolioSnapshot,
    Position,
    Provenance,
    ProviderMapping,
    QuoteUnit,
    SnapshotStatus,
    Valuation,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.valuations import (
    EurValuationStatus,
    FxRate,
    value_portfolio,
    value_position,
)

RETRIEVED = datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc)
OBSERVED = RETRIEVED - timedelta(minutes=15)
EUR = QuoteUnit("EUR", Decimal("1"))
USD = QuoteUnit("USD", Decimal("1"))
PENCE = QuoteUnit("GBP", Decimal("0.01"))
SHEET = Provenance(ValuationSource.SHEET, RETRIEVED)
PROVIDER = Provenance(
    ValuationSource.PROVIDER, RETRIEVED, OBSERVED, "synthetic-provider"
)
FX_SOURCE = Provenance(
    ValuationSource.CACHED_PROVIDER,
    RETRIEVED,
    OBSERVED - timedelta(minutes=5),
    "synthetic-fx",
)


def listing(unit=EUR, instrument_id="synthetic-stock", venue="SYNTHETIC-A"):
    return VerifiedListing(
        Instrument(
            InstrumentType.STOCK,
            instrument_id=instrument_id,
            trading_venue=venue,
            quote_unit=unit,
            provider_mapping=ProviderMapping("synthetic-provider", f"DEMO-{venue}"),
        ),
        equity_form=EquityForm.SHARE,
    )


def position(verified=None, quantity="2", price="12.3456789", row=3):
    verified = listing() if verified is None else verified
    return Position(
        "Synthetic stock",
        Decimal(quantity),
        verified.instrument,
        "Synthetic",
        row,
        RETRIEVED,
        valuation=Valuation(
            Decimal(price),
            ValueStatus.AVAILABLE,
            PROVIDER,
            verified.instrument.quote_unit,
        ),
    )


def fx(base="USD", quote="EUR", rate="0.8"):
    return FxRate(base, quote, Decimal(rate), FX_SOURCE)


def snapshot(*positions, status=SnapshotStatus.COMPLETE, warnings=()):
    return PortfolioSnapshot(RETRIEVED, status, positions, warnings)


def test_eur_value_has_no_fx_and_keeps_subcent_precision():
    verified = listing()
    original = position(verified, quantity="1.125")
    result = value_position(original, (verified,), fx("EUR", "USD", "3"))

    assert result.amount_eur == Decimal("13.8888887625")
    assert result.status is EurValuationStatus.AVAILABLE
    assert result.fx_rate is None
    assert result.position is original
    assert result.position.valuation.provenance is PROVIDER
    assert result.position.valuation.provenance.observed_at == OBSERVED
    assert result.weight_fraction is None


@pytest.mark.parametrize(
    ("unit", "price", "rate", "expected"),
    [
        (USD, "12.3456789", fx(), "19.75308624"),
        (PENCE, "123.456789", fx("GBP", rate="1.2"), "2.962962936"),
        (
            QuoteUnit("GBP", Decimal("1")),
            "1.23456789",
            fx("GBP", rate="1.2"),
            "2.962962936",
        ),
    ],
)
def test_direct_fx_and_major_minor_units(unit, price, rate, expected):
    verified = listing(unit)
    result = value_position(position(verified, price=price), (verified,), rate)

    assert result.amount_eur == Decimal(expected)
    assert result.fx_rate is rate
    assert result.fx_rate.provenance is FX_SOURCE
    assert (
        result.fx_rate.provenance.observed_at
        != result.position.valuation.provenance.observed_at
    )


@pytest.mark.parametrize(
    "rate", [fx("EUR", "USD", "1.25"), fx("GBP", "EUR"), fx("USD", "GBP")]
)
def test_wrong_fx_direction_is_unavailable_without_inversion(rate):
    verified = listing(USD)
    result = value_position(position(verified), (verified,), rate)

    assert result.amount_eur is None
    assert result.status is EurValuationStatus.FX_DIRECTION_MISMATCH
    assert result.fx_rate is rate
    assert result.fx_rate.provenance is FX_SOURCE


def test_missing_fx_does_not_fall_back_to_identity_rate():
    verified = listing(USD)
    result = value_position(position(verified), (verified,))
    assert result.amount_eur is None
    assert result.status is EurValuationStatus.MISSING_FX


def test_sheet_eur_price_uses_own_unit_even_for_unmapped_foreign_instrument():
    original = replace(
        position(listing(PENCE), quantity="1.125"),
        instrument=Instrument(InstrumentType.DERIVATIVE, quote_unit=PENCE),
        valuation=Valuation(Decimal("5.123456789"), ValueStatus.AVAILABLE, SHEET, EUR),
    )
    result = value_position(original, (), fx("GBP", rate="999"))

    assert result.amount_eur == Decimal("5.763888887625")
    assert result.fx_rate is None
    assert result.position.valuation.provenance.observed_at is None
    assert result.position.valuation.provenance.source is ValuationSource.SHEET


@pytest.mark.parametrize("unit", [None, QuoteUnit("USD", None)])
def test_unknown_price_unit_is_not_filled_from_verified_listing(unit):
    verified = listing(USD)
    original = position(verified)
    original = replace(original, valuation=replace(original.valuation, unit=unit))
    result = value_position(original, (verified,), fx())
    assert result.amount_eur is None
    assert result.status is EurValuationStatus.UNKNOWN_UNIT


def test_provider_quote_requires_verified_instrument_provider_and_unit():
    verified = listing(PENCE)
    original = position(verified)
    assert (
        value_position(original, ()).status is EurValuationStatus.UNVERIFIED_INSTRUMENT
    )
    wrong_unit = replace(original, valuation=replace(original.valuation, unit=EUR))
    assert (
        value_position(wrong_unit, (verified,)).status
        is EurValuationStatus.UNIT_MISMATCH
    )
    wrong_provider = replace(
        original,
        valuation=replace(
            original.valuation,
            provenance=replace(PROVIDER, provider="other-synthetic-provider"),
        ),
    )
    assert (
        value_position(wrong_provider, (verified,)).status
        is EurValuationStatus.UNVERIFIED_PROVIDER
    )
    no_mapping = replace(
        verified, instrument=replace(verified.instrument, provider_mapping=None)
    )
    unmapped = replace(original, instrument=no_mapping.instrument)
    assert (
        value_position(unmapped, (no_mapping,)).status
        is EurValuationStatus.UNVERIFIED_PROVIDER
    )


@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        (None, EurValuationStatus.MISSING_PRICE),
        (
            Valuation(None, ValueStatus.MISSING, PROVIDER, EUR),
            EurValuationStatus.MISSING_PRICE,
        ),
        (
            Valuation(None, ValueStatus.INVALID, PROVIDER, EUR),
            EurValuationStatus.INVALID_PRICE,
        ),
    ],
)
def test_missing_or_invalid_price_never_becomes_zero(quote, expected):
    verified = listing()
    result = value_position(replace(position(verified), valuation=quote), (verified,))
    assert result.amount_eur is None
    assert result.status is expected


def test_evidenced_zero_remains_eligible_and_has_zero_weight_in_positive_total():
    verified = listing()
    zero = position(verified, price="0")
    positive = position(verified, price="10", row=4)
    result = value_portfolio(snapshot(zero, positive), (verified,))
    assert result.total_eur == Decimal("20")
    assert result.valued_count == result.active_count == 2
    assert result.positions[0].amount_eur == Decimal("0")
    assert result.positions[0].weight_fraction == Decimal("0")
    assert result.positions[0].status is EurValuationStatus.AVAILABLE
    assert result.positions[1].weight_fraction == Decimal("1")


def test_long_totals_weights_and_groups_use_the_same_eligible_members():
    first_listing = listing()
    second_listing = listing(USD, venue="SYNTHETIC-B")
    other_listing = listing(instrument_id="synthetic-other", venue="SYNTHETIC-C")
    first = position(first_listing, quantity="2", price="10")
    second = position(second_listing, quantity="5", price="20", row=4)
    other = position(other_listing, quantity="10", price="10", row=5)
    missing = replace(position(first_listing, row=6), valuation=None)
    zero_quantity = position(first_listing, quantity="0", price="999", row=7)
    negative = position(first_listing, quantity="-100", price="999", row=8)
    original = snapshot(first, second, other, missing, zero_quantity, negative)
    result = value_portfolio(
        original, (first_listing, second_listing, other_listing), {"USD": fx()}
    )

    assert result.snapshot is original
    assert result.total_eur == Decimal("200")
    assert result.active_count == 4 and result.valued_count == 3
    assert result.coverage_fraction == Decimal("0.75")
    assert result.status is SnapshotStatus.PARTIAL
    assert [member.weight_fraction for member in result.positions] == [
        Decimal("0.1"),
        Decimal("0.4"),
        Decimal("0.5"),
        None,
        None,
        None,
    ]
    assert sum(
        member.weight_fraction
        for member in result.positions
        if member.weight_fraction is not None
    ) == Decimal("1")
    assert [group.amount_eur for group in result.groups] == [
        Decimal("100"),
        Decimal("100"),
    ]
    assert [group.weight_fraction for group in result.groups] == [
        Decimal("0.5"),
        Decimal("0.5"),
    ]
    assert [member.position.source_row for member in result.groups[0].members] == [
        3,
        4,
        6,
        7,
        8,
    ]
    assert result.positions[-1].position.quantity == Decimal("-100")
    assert result.positions[-1].status is EurValuationStatus.INACTIVE
    assert result.positions[-2].status is EurValuationStatus.INACTIVE


def test_unresolved_sheet_rows_do_not_merge_by_claimed_id_or_name():
    manual = replace(
        position(),
        valuation=Valuation(Decimal("10"), ValueStatus.AVAILABLE, SHEET, EUR),
    )
    result = value_portfolio(snapshot(manual, replace(manual, source_row=4)), ())
    assert result.total_eur == Decimal("40")
    assert len(result.groups) == 2
    assert [group.weight_fraction for group in result.groups] == [
        Decimal("0.5"),
        Decimal("0.5"),
    ]


def test_wrongly_keyed_fx_rate_cannot_enter_total_or_weight_denominator():
    eur_listing = listing()
    usd_listing = listing(USD, instrument_id="synthetic-other", venue="SYNTHETIC-B")
    eur = position(eur_listing, price="10")
    usd = position(usd_listing, price="100", row=4)
    warning = DataWarning("fx_direction", "Synthetic direction mismatch")
    rate = replace(fx("EUR", "USD", "1.25"), warnings=(warning,))
    result = value_portfolio(
        snapshot(eur, usd), (eur_listing, usd_listing), {"USD": rate}
    )

    assert result.total_eur == Decimal("20")
    assert result.positions[0].weight_fraction == Decimal("1")
    assert result.positions[1].weight_fraction is None
    assert result.positions[1].warnings == (warning,)
    assert result.positions[1].status is EurValuationStatus.FX_DIRECTION_MISMATCH
    assert result.groups[1].amount_eur is None
    assert result.groups[1].weight_fraction is None
    assert result.coverage_fraction == Decimal("0.5")
    assert result.status is SnapshotStatus.PARTIAL


def test_zero_total_missing_total_empty_success_and_failure_stay_distinct():
    verified = listing()
    all_zero = value_portfolio(snapshot(position(verified, price="0")), (verified,))
    all_missing = value_portfolio(
        snapshot(replace(position(verified), valuation=None)), (verified,)
    )
    empty = value_portfolio(snapshot(), ())
    inactive = value_portfolio(snapshot(position(verified, quantity="-2")), (verified,))
    warning = DataWarning("import_failed", "Synthetic unavailable source")
    failed = value_portfolio(
        snapshot(status=SnapshotStatus.FAILED, warnings=(warning,)), ()
    )
    partial_empty = value_portfolio(
        snapshot(status=SnapshotStatus.PARTIAL, warnings=(warning,)), ()
    )

    assert all_zero.total_eur == empty.total_eur == inactive.total_eur == Decimal("0")
    assert all_zero.positions[0].weight_fraction is None
    assert all_zero.coverage_fraction == Decimal("1")
    assert all_missing.total_eur is failed.total_eur is None
    assert all_missing.status is SnapshotStatus.PARTIAL
    assert all_missing.coverage_fraction == Decimal("0")
    assert empty.status is inactive.status is SnapshotStatus.COMPLETE
    assert empty.coverage_fraction is None
    assert failed.status is SnapshotStatus.FAILED
    assert failed.snapshot.warnings == (warning,)
    assert partial_empty.total_eur is None
    assert partial_empty.coverage_fraction is None
    assert partial_empty.status is SnapshotStatus.PARTIAL


def test_partial_import_and_all_source_warnings_and_times_remain_visible():
    verified = listing(USD)
    warning = DataWarning("synthetic_partial", "Synthetic row rejected", source_row=9)
    quote_warning = DataWarning("cached_price", "Synthetic cached quote")
    fx_warning = DataWarning("cached_fx", "Synthetic cached FX")
    original = position(verified)
    original = replace(
        original,
        warnings=(warning,),
        valuation=replace(
            original.valuation,
            provenance=replace(PROVIDER, source=ValuationSource.CACHED_PROVIDER),
            warnings=(quote_warning,),
        ),
    )
    rate = replace(fx(), warnings=(fx_warning,))
    imported = snapshot(original, status=SnapshotStatus.PARTIAL, warnings=(warning,))
    result = value_portfolio(imported, (verified,), {"USD": rate})

    assert result.status is SnapshotStatus.PARTIAL
    assert result.coverage_fraction == Decimal("1")  # Accepted active rows only.
    assert result.positions[0].warnings == (warning, quote_warning, fx_warning)
    assert (
        result.positions[0].position.valuation.provenance.source
        is ValuationSource.CACHED_PROVIDER
    )
    assert result.positions[0].fx_rate.provenance is FX_SOURCE
    assert result.snapshot.warnings == (warning,)
    assert result.positions[0].position.cost_basis.amount_eur is None


def test_exact_products_and_sums_are_not_rounded_to_decimal_context_or_cents():
    verified = listing(USD)
    original = position(
        verified,
        quantity="1.12345678901234567890123456789",
        price="2.12345678901234567890123456789",
    )
    rate = fx(rate="0.812345678901234567890123456789")
    with localcontext() as context:
        context.prec = 120
        expected = original.quantity * original.valuation.price * rate.rate
        expected_total = expected + Decimal(
            "0.00000000000000000000000000000000000000001"
        )
    manual = replace(
        position(verified, row=4, quantity="1"),
        valuation=Valuation(
            Decimal("0.00000000000000000000000000000000000000001"),
            ValueStatus.AVAILABLE,
            SHEET,
            EUR,
        ),
    )
    with localcontext() as context:
        context.prec = 6
        result = value_portfolio(snapshot(original, manual), (verified,), {"USD": rate})
    assert result.positions[0].amount_eur == expected
    assert result.total_eur == expected_total
    assert result.groups[0].amount_eur == expected_total


@pytest.mark.parametrize("exponent", [1000000, -1000000])
def test_finite_values_outside_caller_exponent_limits_stay_finite(exponent):
    verified = listing(USD)
    first = position(verified, quantity=f"1e{exponent}", price="2")
    second = position(verified, quantity=f"1e{exponent}", price="3", row=4)
    with localcontext() as context:
        context.prec = 6
        context.Emax = 9
        context.Emin = -9
        context.traps[Overflow] = context.traps[Underflow] = False
        result = value_portfolio(snapshot(first, second), (verified,), {"USD": fx()})
        assert (context.prec, context.Emax, context.Emin) == (6, 9, -9)

    assert result.total_eur == Decimal(f"4e{exponent}")
    assert result.total_eur.is_finite()
    assert result.groups[0].amount_eur == result.total_eur
    assert [member.amount_eur for member in result.positions] == [
        Decimal(f"1.6e{exponent}"),
        Decimal(f"2.4e{exponent}"),
    ]
    assert [member.weight_fraction for member in result.positions] == [
        Decimal("0.4"),
        Decimal("0.6"),
    ]


def test_total_keeps_large_integer_and_small_fraction_and_ignores_zero_scale():
    verified = listing()
    large = position(
        verified,
        quantity="1",
        price="12345678901234567890123456789012345678901234567890",
    )
    small = position(
        verified,
        quantity="1",
        price="0.0000000000000000000000000000000000000001",
        row=4,
    )
    zero = position(verified, quantity="1", price=f"0e{MAX_EMAX}", row=5)
    with localcontext() as context:
        context.Emax = 9
        context.Emin = -9
        result = value_portfolio(snapshot(large, small, zero), (verified,))
    assert result.total_eur == Decimal(
        "12345678901234567890123456789012345678901234567890."
        "0000000000000000000000000000000000000001"
    )
    assert result.groups[0].amount_eur == result.total_eur
    assert result.positions[-1].amount_eur == result.positions[-1].weight_fraction == 0
    assert result.positions[1].weight_fraction > 0


@pytest.mark.parametrize(
    ("quantity", "error"),
    [(f"1e{MAX_EMAX}", Overflow), (f"1e{MIN_EMIN}", Inexact)],
)
def test_unrepresentable_exact_product_raises_even_if_caller_disables_traps(
    quantity, error
):
    verified = listing()
    original = position(verified, quantity=quantity, price=quantity)
    with localcontext() as context:
        context.traps[Overflow] = context.traps[Inexact] = False
        with pytest.raises(error):
            value_position(original, (verified,))


@pytest.mark.parametrize("exponent", [MAX_EMAX, MIN_EMIN])
def test_exact_product_uses_final_exponent_after_fx_not_intermediate_range(exponent):
    verified = listing(USD)
    original = position(verified, quantity=f"1e{exponent}", price=f"1e{exponent}")
    result = value_position(original, (verified,), fx(rate=f"1e{-exponent}"))
    assert result.amount_eur == Decimal(f"1e{exponent}")
    assert result.status is EurValuationStatus.AVAILABLE


def test_unrepresentable_total_raises_instead_of_publishing_infinite_sum():
    verified = listing()
    first = position(verified, quantity=f"9e{MAX_EMAX}", price="1")
    second = replace(first, source_row=4)
    with localcontext() as context:
        context.traps[Overflow] = False
        with pytest.raises(Overflow):
            value_portfolio(snapshot(first, second), (verified,))


@pytest.mark.parametrize(
    "rate", [0.8, Decimal("NaN"), Decimal("Infinity"), Decimal("0"), Decimal("-1")]
)
def test_fx_rejects_float_nonfinite_and_nonpositive_rates(rate):
    with pytest.raises((TypeError, ValueError)):
        FxRate("USD", "EUR", rate, FX_SOURCE)


def test_valuation_functions_are_offline_without_ui_or_provider_imports():
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
    raise AssertionError('Valuation attempted a network connection')
socket.socket = no_network
socket.create_connection = no_network
from datetime import datetime, timezone
from decimal import Decimal
from portfolio_dashboard.models import PortfolioSnapshot, SnapshotStatus
from portfolio_dashboard.valuations import value_portfolio
snapshot = PortfolioSnapshot(datetime(2026, 1, 5, tzinfo=timezone.utc), SnapshotStatus.COMPLETE)
assert value_portfolio(snapshot, ()).total_eur == Decimal('0')
"""
    subprocess.run([sys.executable, "-c", code], check=True)
