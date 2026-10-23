"""Synthetic verified associations only; no real identifiers or provider data."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import subprocess
import sys

import pytest

from portfolio_dashboard.instruments import (
    EquityForm,
    VerifiedListing,
    group_positions,
    lookup_provider_mapping,
    resolve_listing,
)
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    Position,
    Provenance,
    ProviderMapping,
    QuoteUnit,
    Valuation,
    ValuationSource,
    ValueStatus,
)


IMPORTED = datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc)
EUR = QuoteUnit("EUR", Decimal("1"))


def stock(identity="synthetic-local", venue="SYNTHETIC-A", symbol="DEMO.A"):
    return VerifiedListing(
        Instrument(
            InstrumentType.STOCK,
            instrument_id=identity,
            display_ticker="DEMO",
            source_symbol="SYNTHETIC:DEMO",
            trading_venue=venue,
            quote_unit=EUR,
            provider_mapping=ProviderMapping("synthetic-provider", symbol),
        ),
        equity_form=EquityForm.SHARE,
    )


def position(instrument, row=3):
    return Position(
        name="Same synthetic name",
        quantity=Decimal("1.125"),
        instrument=instrument,
        category="Synthetic category",
        source_row=row,
        imported_at=IMPORTED,
    )


def test_same_ticker_name_and_source_symbol_never_select_another_instrument():
    local = stock()
    other = stock("synthetic-other", "SYNTHETIC-B", "DEMO.B")
    listings = (local, other)
    assert local.instrument.display_ticker == other.instrument.display_ticker
    assert local.instrument.source_symbol == other.instrument.source_symbol
    assert lookup_provider_mapping(local.instrument, listings).symbol == "DEMO.A"
    assert lookup_provider_mapping(other.instrument, listings).symbol == "DEMO.B"
    unresolved = Instrument(
        InstrumentType.STOCK, display_ticker="DEMO", source_symbol="SYNTHETIC:DEMO"
    )
    assert resolve_listing(unresolved, listings) is None
    assert lookup_provider_mapping(unresolved, listings) is None
    members = (position(local.instrument), position(other.instrument, 4))
    assert group_positions(members, listings) == ((members[0],), (members[1],))


@pytest.mark.parametrize(
    "changes",
    [
        {"instrument_id": None, "provider_mapping": None},
        {"instrument_id": "synthetic-unknown"},
        {"trading_venue": None},
        {"trading_venue": "SYNTHETIC-UNKNOWN"},
        {"instrument_type": InstrumentType.DERIVATIVE},
        {"quote_unit": QuoteUnit("GBP", Decimal("0.01"))},
        {"provider_mapping": ProviderMapping("synthetic-provider", "WRONG")},
    ],
)
def test_missing_or_conflicting_metadata_does_not_guess_a_mapping(changes):
    verified = stock()
    candidate = replace(verified.instrument, **changes)
    assert lookup_provider_mapping(candidate, (verified,)) is None


def test_display_and_source_symbols_are_not_required_for_exact_identity_lookup():
    verified = stock()
    candidate = Instrument(
        InstrumentType.STOCK,
        instrument_id=verified.instrument.instrument_id,
        trading_venue=verified.instrument.trading_venue,
    )
    assert resolve_listing(candidate, (verified,)) is verified
    assert (
        lookup_provider_mapping(candidate, (verified,))
        == verified.instrument.provider_mapping
    )
    assert candidate.quote_unit is None  # The caller's original data is preserved.


def test_adr_and_local_share_remain_separate_even_with_the_same_ticker():
    local = stock()
    adr = replace(
        stock("synthetic-adr", "SYNTHETIC-B", "DEMO.ADR"),
        equity_form=EquityForm.ADR,
    )
    listings = (local, adr)
    assert lookup_provider_mapping(adr.instrument, listings).symbol == "DEMO.ADR"
    assert lookup_provider_mapping(adr.instrument, (local,)) is None
    members = (position(local.instrument), position(adr.instrument, 4))
    assert len(group_positions(members, listings)) == 2
    reused_id = replace(
        adr,
        instrument=replace(
            adr.instrument, instrument_id=local.instrument.instrument_id
        ),
    )
    with pytest.raises(ValueError, match="conflicting identity"):
        resolve_listing(local.instrument, (local, reused_id))


def test_etf_share_classes_and_listings_are_explicit():
    accumulating = VerifiedListing(
        Instrument(
            InstrumentType.ETF,
            instrument_id="synthetic-etf-acc",
            display_ticker="DEMO",
            trading_venue="SYNTHETIC-A",
            quote_unit=EUR,
            provider_mapping=ProviderMapping("synthetic-provider", "FUND.ACC.A"),
        ),
        isin="synthetic-isin-acc",
        share_class="synthetic-accumulating",
    )
    distributing = replace(
        accumulating,
        instrument=replace(
            accumulating.instrument,
            instrument_id="synthetic-etf-dist",
            provider_mapping=ProviderMapping("synthetic-provider", "FUND.DIST.A"),
        ),
        isin="synthetic-isin-dist",
        share_class="synthetic-distributing",
    )
    alternate_listing = replace(
        accumulating,
        instrument=replace(
            accumulating.instrument,
            trading_venue="SYNTHETIC-B",
            quote_unit=QuoteUnit("GBP", Decimal("0.01")),
            provider_mapping=ProviderMapping("synthetic-provider", "FUND.ACC.B"),
        ),
    )
    listings = (accumulating, distributing, alternate_listing)
    assert (
        lookup_provider_mapping(distributing.instrument, listings).symbol
        == "FUND.DIST.A"
    )
    assert (
        lookup_provider_mapping(alternate_listing.instrument, listings).symbol
        == "FUND.ACC.B"
    )
    resolved = resolve_listing(alternate_listing.instrument, listings)
    assert resolved.instrument.quote_unit.scale_to_currency == Decimal("0.01")
    wrong_class_id = replace(
        distributing,
        instrument=replace(
            distributing.instrument,
            instrument_id="synthetic-etf-acc",
            trading_venue="SYNTHETIC-C",
        ),
    )
    with pytest.raises(ValueError, match="conflicting identity"):
        resolve_listing(accumulating.instrument, (accumulating, wrong_class_id))


def test_derivative_uses_only_its_own_product_mapping_never_underlying_fallback():
    underlying = stock()
    derivative = VerifiedListing(
        Instrument(
            InstrumentType.DERIVATIVE,
            instrument_id="synthetic-derivative",
            display_ticker="DEMO",
            source_symbol="SYNTHETIC:DEMO",  # Deliberately names the underlying.
            trading_venue="SYNTHETIC-A",
        ),
        product_id="synthetic-product-001",
    )
    listings = (underlying, derivative)
    assert resolve_listing(derivative.instrument, listings) is derivative
    assert lookup_provider_mapping(derivative.instrument, listings) is None
    own_mapping = replace(
        derivative,
        instrument=replace(
            derivative.instrument,
            quote_unit=EUR,
            provider_mapping=ProviderMapping("synthetic-provider", "PRODUCT.001"),
        ),
    )
    assert (
        lookup_provider_mapping(derivative.instrument, (underlying, own_mapping)).symbol
        == "PRODUCT.001"
    )
    stolen_mapping = replace(
        own_mapping,
        instrument=replace(
            own_mapping.instrument,
            provider_mapping=underlying.instrument.provider_mapping,
        ),
    )
    with pytest.raises(ValueError, match="provider symbol"):
        resolve_listing(derivative.instrument, (underlying, stolen_mapping))


@pytest.mark.parametrize("unit", [None, QuoteUnit("GBP", None)])
def test_automatic_mapping_requires_verified_currency_and_unit_scale(unit):
    with pytest.raises(ValueError, match="currency and unit scale"):
        replace(stock(), instrument=replace(stock().instrument, quote_unit=unit))


def test_verified_identity_without_provider_coverage_remains_explicit():
    verified = stock()
    no_coverage = replace(
        verified,
        instrument=replace(
            verified.instrument, provider_mapping=None, quote_unit=None
        ),
    )
    assert resolve_listing(no_coverage.instrument, (no_coverage,)) is no_coverage
    assert lookup_provider_mapping(no_coverage.instrument, (no_coverage,)) is None


def test_registry_rejects_ambiguous_listing_and_provider_symbol_assignments():
    first = stock()
    with pytest.raises(ValueError, match="duplicate instrument/listing"):
        lookup_provider_mapping(first.instrument, (first, first))
    collision = stock("synthetic-other", "SYNTHETIC-B", "DEMO.A")
    with pytest.raises(ValueError, match="provider symbol"):
        lookup_provider_mapping(first.instrument, (first, collision))


@pytest.mark.parametrize(
    ("kind", "metadata", "error"),
    [
        (InstrumentType.STOCK, {}, "equity_form"),
        (InstrumentType.ETF, {"share_class": "synthetic-class"}, "ISIN or WKN"),
        (InstrumentType.ETF, {"wkn": "synthetic-wkn"}, "share_class"),
        (InstrumentType.DERIVATIVE, {}, "product_id"),
    ],
)
def test_checked_identity_requires_product_specific_metadata(kind, metadata, error):
    with pytest.raises(ValueError, match=error):
        VerifiedListing(
            Instrument(kind, instrument_id="synthetic-id", trading_venue="SYNTHETIC-A"),
            **metadata,
        )


def test_grouping_preserves_all_partial_positions_and_manual_timestamp():
    verified = stock()
    other_listing = replace(
        verified,
        instrument=replace(
            verified.instrument,
            trading_venue="SYNTHETIC-B",
            provider_mapping=ProviderMapping("synthetic-provider", "DEMO.B"),
        ),
    )
    first = position(verified.instrument)
    second = replace(
        position(other_listing.instrument, 8),
        quantity=Decimal("-0.25"),
        category="Other synthetic category",
        valuation=Valuation(
            Decimal("5"), ValueStatus.AVAILABLE,
            Provenance(ValuationSource.SHEET, IMPORTED), EUR,
        ),
    )
    unknown = position(Instrument(InstrumentType.STOCK, display_ticker="DEMO"), 10)
    unregistered = replace(
        first,
        source_row=12,
        instrument=replace(first.instrument, instrument_id="unregistered"),
    )
    groups = group_positions(
        (first, unknown, second, unknown, unregistered, unregistered),
        (verified, other_listing),
    )
    assert groups == (
        (first, second), (unknown,), (unknown,), (unregistered,), (unregistered,),
    )
    assert groups[0][0] is first and groups[0][1] is second
    assert [member.source_row for member in groups[0]] == [3, 8]
    assert groups[0][1].quantity == Decimal("-0.25")
    assert groups[0][1].valuation.provenance.observed_at is None
    assert groups[0][0].cost_basis.amount_eur is None


def test_instrument_lookup_and_grouping_are_offline_without_ui_or_provider_imports():
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
    raise AssertionError('Instrument lookup attempted a network connection')
socket.socket = no_network
socket.create_connection = no_network
from portfolio_dashboard.instruments import group_positions, lookup_provider_mapping
from portfolio_dashboard.models import Instrument, InstrumentType
assert lookup_provider_mapping(Instrument(InstrumentType.DERIVATIVE), ()) is None
assert group_positions((), ()) == ()
"""
    subprocess.run([sys.executable, "-c", code], check=True)
