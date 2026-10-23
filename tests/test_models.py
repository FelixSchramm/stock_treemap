"""Synthetic domain contracts, without live sources or financial assumptions."""

import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from portfolio_dashboard.models import (
    CostBasis,
    CostBasisStatus,
    DataWarning,
    Instrument,
    InstrumentType,
    PortfolioSnapshot,
    Position,
    PositionActivity,
    Provenance,
    ProviderMapping,
    QuoteUnit,
    SnapshotStatus,
    Valuation,
    ValuationSource,
    ValueStatus,
)

RETRIEVED = datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc)
OBSERVED = RETRIEVED - timedelta(minutes=15)
EUR = QuoteUnit("EUR", Decimal("1"))
SHEET = Provenance(ValuationSource.SHEET, RETRIEVED)


@pytest.fixture
def position():
    return Position(
        name="Synthetic instrument",
        quantity=Decimal("1.125"),
        instrument=Instrument(
            InstrumentType.STOCK,
            instrument_id="synthetic-stock-a",
            display_ticker="DEMO",
            source_symbol="DEMO-VENUE:DEMO",
            trading_venue="SYNTHETIC-VENUE",
        ),
        category="Synthetic category",
        source_row=3,
        imported_at=RETRIEVED,
    )


def test_missing_price_is_not_an_observed_zero():
    missing = Valuation(None, ValueStatus.MISSING, SHEET, EUR)
    zero = Valuation(Decimal("0"), ValueStatus.AVAILABLE, SHEET, EUR)

    assert missing.price is None
    assert zero.price == Decimal("0")
    assert missing.status is not zero.status
    assert zero.provenance.observed_at is None
    assert zero.provenance.retrieved_at == RETRIEVED


@pytest.mark.parametrize("status", [ValueStatus.MISSING, ValueStatus.INVALID])
def test_unavailable_price_cannot_carry_a_usable_number(status):
    with pytest.raises(ValueError, match="must be None"):
        Valuation(Decimal("0"), status, SHEET, EUR)
    with pytest.raises(TypeError, match="Decimal"):
        Valuation(None, ValueStatus.AVAILABLE, SHEET, EUR)


def test_unknown_unit_is_not_assumed_to_be_eur():
    unknown = Valuation(Decimal("123.456789"), ValueStatus.AVAILABLE, SHEET)
    pence = QuoteUnit("GBP", Decimal("0.01"))
    pounds = QuoteUnit("GBP", Decimal("1"))
    unknown_scale = QuoteUnit("GBP", None)

    assert unknown.unit is None
    assert unknown.price == Decimal("123.456789")
    assert pence != pounds
    assert pence.scale_to_currency == Decimal("0.01")
    assert unknown_scale.currency == "GBP"
    assert unknown_scale.scale_to_currency is None
    assert unknown_scale not in (pence, pounds)


@pytest.mark.parametrize(
    "source", [ValuationSource.PROVIDER, ValuationSource.CACHED_PROVIDER]
)
def test_quote_provenance_retains_source_and_both_timestamps(source):
    provenance = Provenance(source, RETRIEVED, OBSERVED, "synthetic-provider")
    quote = Valuation(Decimal("120"), ValueStatus.AVAILABLE, provenance, EUR)

    assert quote.provenance.source is source
    assert quote.provenance.observed_at == OBSERVED
    assert quote.provenance.retrieved_at == RETRIEVED
    assert quote.provenance.provider == "synthetic-provider"
    with pytest.raises(ValueError, match="provider"):
        Provenance(source, RETRIEVED)


def test_sheet_eur_fallback_keeps_its_own_unit_and_unknown_market_time(position):
    instrument = replace(position.instrument, quote_unit=QuoteUnit("GBP", Decimal("0.01")))
    fallback = Valuation(Decimal("5.123456789"), ValueStatus.AVAILABLE, SHEET, EUR)
    imported = replace(position, instrument=instrument, valuation=fallback)

    assert imported.instrument.quote_unit.currency == "GBP"
    assert imported.valuation.unit == EUR
    assert imported.valuation.provenance.source is ValuationSource.SHEET
    assert imported.valuation.provenance.observed_at is None
    assert imported.valuation.price == Decimal("5.123456789")


def test_same_names_and_display_tickers_do_not_merge_instruments(position):
    second = replace(
        position,
        source_row=4,
        instrument=replace(position.instrument, instrument_id="synthetic-stock-b"),
    )
    snapshot = PortfolioSnapshot(RETRIEVED, SnapshotStatus.COMPLETE, (position, second))

    assert position.name == second.name
    assert position.instrument.display_ticker == second.instrument.display_ticker
    assert position.instrument.instrument_id != second.instrument.instrument_id
    assert snapshot.positions == (position, second)


def test_unresolved_identity_is_preserved_without_invented_mapping(position):
    unresolved = replace(position, instrument=Instrument(InstrumentType.DERIVATIVE))
    another = replace(unresolved, source_row=4)
    snapshot = PortfolioSnapshot(RETRIEVED, SnapshotStatus.COMPLETE, (unresolved, another))

    assert len(snapshot.positions) == 2
    assert unresolved.instrument.instrument_id is None
    assert unresolved.instrument.provider_mapping is None


def test_provider_mapping_requires_an_explicit_instrument_identity(position):
    mapping = ProviderMapping("synthetic-provider", "SYNTHETIC-SYMBOL")
    with pytest.raises(ValueError, match="verified instrument_id"):
        Instrument(InstrumentType.ETF, provider_mapping=mapping)
    mapped = replace(position.instrument, provider_mapping=mapping)
    assert mapped.display_ticker == "DEMO"
    assert mapped.provider_mapping.symbol == "SYNTHETIC-SYMBOL"


@pytest.mark.parametrize(
    ("quantity", "activity"),
    [
        ("0.125", PositionActivity.ACTIVE),
        ("0", PositionActivity.ZERO),
        ("-0.125", PositionActivity.NEGATIVE),
    ],
)
def test_activity_uses_quantity_sign_and_keeps_fractional_negative_units(
    position, quantity, activity
):
    changed = replace(position, quantity=Decimal(quantity))
    assert changed.quantity == Decimal(quantity)
    assert changed.activity is activity


@pytest.mark.parametrize(
    ("amount", "status", "usable"),
    [
        (None, CostBasisStatus.MISSING, False),
        (None, CostBasisStatus.INVALID, False),
        (Decimal("100"), CostBasisStatus.UNVERIFIED, False),
        (Decimal("0"), CostBasisStatus.VERIFIED_REMAINING, False),
        (Decimal("100.123456789"), CostBasisStatus.VERIFIED_REMAINING, True),
    ],
)
def test_since_purchase_needs_verified_positive_remaining_cost(amount, status, usable):
    basis = CostBasis(amount, status)
    assert basis.amount_eur == amount
    assert basis.usable_for_since_purchase is usable


def test_cost_basis_is_not_inferred_from_price_or_quantity(position):
    valued = replace(
        position,
        valuation=Valuation(Decimal("100"), ValueStatus.AVAILABLE, SHEET, EUR),
    )
    assert valued.cost_basis.status is CostBasisStatus.MISSING
    assert valued.cost_basis.amount_eur is None
    assert not valued.cost_basis.usable_for_since_purchase
    with pytest.raises(ValueError, match="must be None"):
        CostBasis(Decimal("100"))


@pytest.mark.parametrize(
    "value", [1.25, Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")]
)
def test_financial_fields_reject_float_and_nonfinite_values(position, value):
    error = TypeError if isinstance(value, float) else ValueError
    with pytest.raises(error):
        replace(position, quantity=value)
    with pytest.raises(error):
        Valuation(value, ValueStatus.AVAILABLE, SHEET, EUR)
    with pytest.raises(error):
        CostBasis(value, CostBasisStatus.UNVERIFIED)
    with pytest.raises(error):
        QuoteUnit("EUR", value)


def test_negative_prices_costs_and_nonpositive_quote_scales_are_invalid():
    with pytest.raises(ValueError, match="negative"):
        Valuation(Decimal("-1"), ValueStatus.AVAILABLE, SHEET, EUR)
    with pytest.raises(ValueError, match="negative"):
        CostBasis(Decimal("-1"), CostBasisStatus.UNVERIFIED)
    for scale in (Decimal("0"), Decimal("-1")):
        with pytest.raises(ValueError, match="positive"):
            QuoteUnit("GBP", scale)


def test_empty_success_partial_import_and_failed_attempt_are_distinct(position):
    warning = DataWarning("row_invalid", "Invalid quantity", "quantity", 4)
    empty = PortfolioSnapshot(RETRIEVED, SnapshotStatus.COMPLETE)
    partial = PortfolioSnapshot(RETRIEVED, SnapshotStatus.PARTIAL, (position,), (warning,))
    failed = PortfolioSnapshot(RETRIEVED, SnapshotStatus.FAILED, warnings=(warning,))

    assert empty.positions == failed.positions == ()
    assert empty.status is not failed.status
    assert partial.positions == (position,)
    assert partial.warnings[0].source_row == 4
    assert partial.warnings[0].field == "quantity"
    with pytest.raises(ValueError, match="requires an explanation"):
        PortfolioSnapshot(RETRIEVED, SnapshotStatus.PARTIAL)
    with pytest.raises(ValueError, match="requires an explanation"):
        PortfolioSnapshot(RETRIEVED, SnapshotStatus.FAILED)
    with pytest.raises(ValueError, match="cannot contain"):
        PortfolioSnapshot(RETRIEVED, SnapshotStatus.FAILED, (position,), (warning,))


def test_warnings_travel_with_position_and_valuation(position):
    warning = DataWarning("unknown_quote_time", "Market timestamp unavailable", "price", 3)
    price = Valuation(Decimal("0"), ValueStatus.AVAILABLE, SHEET, EUR, (warning,))
    warned = replace(position, valuation=price, warnings=(warning,))

    assert warned.warnings == warned.valuation.warnings == (warning,)
    assert warned.valuation.status is ValueStatus.AVAILABLE


def test_all_timestamps_require_timezones_and_never_use_the_current_clock(position):
    naive = datetime(2026, 1, 5, 15, 30)
    for build in (
        lambda: Provenance(ValuationSource.SHEET, naive),
        lambda: Provenance(ValuationSource.SHEET, RETRIEVED, naive),
        lambda: replace(position, imported_at=naive),
        lambda: PortfolioSnapshot(naive, SnapshotStatus.COMPLETE),
    ):
        with pytest.raises(ValueError, match="timezone"):
            build()
    local = OBSERVED.astimezone(timezone(timedelta(hours=2)))
    assert Provenance(ValuationSource.SHEET, RETRIEVED, local).observed_at == OBSERVED


@pytest.mark.parametrize("row", [0, -1, 1.5, True])
def test_source_row_is_a_positive_integer_not_an_identity(position, row):
    with pytest.raises(ValueError, match="source_row"):
        replace(position, source_row=row)
    with pytest.raises(ValueError, match="source_row"):
        DataWarning("invalid", "Invalid field", source_row=row)


def test_snapshot_and_nested_models_cannot_be_mutated(position):
    snapshot = PortfolioSnapshot(RETRIEVED, SnapshotStatus.COMPLETE, (position,))
    with pytest.raises(FrozenInstanceError):
        snapshot.positions[0].quantity = Decimal("10")
    with pytest.raises(FrozenInstanceError):
        snapshot.positions[0].instrument.instrument_id = "changed"
    with pytest.raises(TypeError, match="tuple"):
        PortfolioSnapshot(RETRIEVED, SnapshotStatus.COMPLETE, [position])
    with pytest.raises(TypeError, match="tuple"):
        replace(position, warnings=[])


def test_models_import_without_ui_providers_or_network():
    code = """
import importlib.abc
import socket
import sys
class OfflineBoundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        blocked = {'streamlit', 'pandas', 'plotly', 'yfinance', 'requests', 'gspread'}
        if fullname.split('.')[0] in blocked:
            raise AssertionError(f'Unexpected dependency: {fullname}')
sys.meta_path.insert(0, OfflineBoundary())
def no_network(*args, **kwargs):
    raise AssertionError('Domain import attempted a network connection')
socket.socket = no_network
socket.create_connection = no_network
from portfolio_dashboard.models import CostBasis, Instrument, InstrumentType
assert CostBasis().amount_eur is None
assert Instrument(InstrumentType.ETF).instrument_id is None
"""
    subprocess.run([sys.executable, "-c", code], check=True)
