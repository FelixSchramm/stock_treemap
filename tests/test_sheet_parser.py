"""Entirely synthetic schema/row cases; no Sheet or market access."""

import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from portfolio_dashboard.models import (
    CostBasisStatus,
    InstrumentType,
    PositionActivity,
    QuoteUnit,
    SnapshotStatus,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.sheet_parser import (
    SHEET_HEADERS,
    NumberLocale,
    SheetMetadata,
    parse_sheet_rows,
)

IMPORTED = datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc)
GERMAN = SheetMetadata(NumberLocale.GERMAN)
ENGLISH = SheetMetadata(NumberLocale.ENGLISH)


def synthetic_row(**changes):
    values = {
        "Position": "Synthetische Position",
        "Ticker": "DEMO",
        "Typ": "Aktie",
        "Anzahl": "1,125",
        "Symbol (Google Finance)": "DEMO-VENUE:DEMO",
        "Währung": "USD",
        "Kurs (EUR)": "12,50",
        "Aktueller Wert (EUR)": "14,0625",
        "Investiert (EUR)": "10,00",
        "G/V (%)": "40,625",
        "G/V (EUR)": "4,0625",
        "Kategorie": "Synthetisch",
        "Gewicht (%)": "25",
    }
    values.update(changes)
    return [values[header] for header in SHEET_HEADERS]


def parse(rows, metadata=GERMAN, **kwargs):
    return parse_sheet_rows(
        SHEET_HEADERS, rows, imported_at=IMPORTED, metadata=metadata, **kwargs
    )


def test_german_values_fractional_quantity_and_units_preserve_domain_boundaries():
    result = parse(
        [
            synthetic_row(
                **{
                    "Anzahl": "1.234,125",
                    "Kurs (EUR)": "1.234,56 €",
                    "Investiert (EUR)": "EUR 1.000,25",
                }
            )
        ]
    )
    (position,) = result.active_positions
    assert result.snapshot.status is SnapshotStatus.COMPLETE
    assert position.quantity == Decimal("1234.125")
    assert position.valuation.price == Decimal("1234.56")
    assert position.cost_basis.amount_eur == Decimal("1000.25")
    assert position.cost_basis.status is CostBasisStatus.UNVERIFIED
    assert not position.cost_basis.usable_for_since_purchase
    assert position.instrument.quote_unit == QuoteUnit("USD", None)
    assert position.valuation.unit == QuoteUnit("EUR", Decimal("1"))
    assert position.valuation.provenance.source is ValuationSource.SHEET
    assert position.valuation.provenance.retrieved_at == IMPORTED
    assert position.valuation.provenance.observed_at is None
    assert position.instrument.instrument_id is None
    assert position.instrument.provider_mapping is None
    assert position.instrument.trading_venue is None
    assert position.instrument.source_symbol == "DEMO-VENUE:DEMO"


def test_english_numbers_euro_markers_and_percent_references_use_explicit_locale():
    result = parse(
        [
            synthetic_row(
                **{
                    "Anzahl": "1,234.125",
                    "Kurs (EUR)": "1,234.50 EUR",
                    "Investiert (EUR)": "€ 1,000.25",
                    "G/V (%)": "20.5 %",
                    "Aktueller Wert (EUR)": "1,543.125",
                    "G/V (EUR)": "-10.25",
                    "Gewicht (%)": "0.5%",
                }
            )
        ],
        ENGLISH,
    )
    (position,) = result.active_positions
    assert position.quantity == Decimal("1234.125")
    assert position.valuation.price == Decimal("1234.50")
    assert position.cost_basis.amount_eur == Decimal("1000.25")
    assert result.references[0].gain_loss_percent_points == Decimal("20.5")
    assert result.references[0].weight_percent_points == Decimal("0.5")
    assert result.references[0].gain_loss_eur == Decimal("-10.25")


@pytest.mark.parametrize(
    ("metadata", "quantity", "expected"),
    [
        (GERMAN, "1.234", "1234"),
        (ENGLISH, "1.234", "1.234"),
        (GERMAN, "1,234", "1.234"),
        (ENGLISH, "1,234", "1234"),
        (ENGLISH, "1,234.125", "1234.125"),
        (ENGLISH, ".125", ".125"),
        (GERMAN, ",125", ".125"),
        (GERMAN, "1\u202f234,125", "1234.125"),
        (ENGLISH, "1\u00a0234.125", "1234.125"),
        (GERMAN, 1.125, "1.125"),
        (GERMAN, Decimal("1.123456789123456789"), "1.123456789123456789"),
    ],
)
def test_explicit_locale_controls_grouping_without_guessing(
    metadata, quantity, expected
):
    result = parse([synthetic_row(Anzahl=quantity)], metadata)
    assert result.active_positions[0].quantity == Decimal(expected)


@pytest.mark.parametrize(
    ("metadata", "quantity"),
    [
        (GERMAN, "12.34"),
        (GERMAN, "1,234.56"),
        (ENGLISH, "12,34"),
        (ENGLISH, "1.234,56"),
        (GERMAN, "1 2"),
        (GERMAN, "1.234 567"),
        (GERMAN, "1e3"),
        (GERMAN, "NaN"),
        (GERMAN, "Infinity"),
        (GERMAN, float("nan")),
        (GERMAN, float("inf")),
        (GERMAN, Decimal("sNaN")),
        (GERMAN, True),
        (GERMAN, "2 €"),
        (GERMAN, "2%"),
        (GERMAN, None),
        (GERMAN, ""),
        (GERMAN, "k. A."),
    ],
)
def test_invalid_or_missing_quantities_reject_only_the_row(metadata, quantity):
    result = parse(
        [synthetic_row(Anzahl=quantity), synthetic_row(Anzahl=2)],
        metadata,
        header_row=5,
    )
    assert result.snapshot.status is SnapshotStatus.PARTIAL
    assert [p.source_row for p in result.active_positions] == [7]
    assert result.excluded_rows[0].source_row == 6
    assert result.excluded_rows[0].reason == "invalid_position"
    (warning,) = [w for w in result.snapshot.warnings if w.code == "invalid_quantity"]
    assert (warning.field, warning.source_row) == ("quantity", 6)


def test_empty_summary_zero_and_right_aggregates_are_excluded_negative_is_separate():
    total = ["Summe Aktien", "", "", "", "", "", "", "999", "999", "", "", "", ""]
    result = parse(
        [
            [],
            ["k. A."] * 13,
            total,
            synthetic_row(Anzahl="0"),
            synthetic_row(Anzahl="-0,125"),
            synthetic_row(Anzahl="0,25"),
            [""] * 13 + ["999999", "Rechtes Aggregat"],
        ]
    )
    assert result.snapshot.status is SnapshotStatus.COMPLETE
    assert [(r.source_row, r.reason) for r in result.excluded_rows] == [
        (2, "empty"),
        (3, "empty"),
        (4, "summary"),
        (5, "zero_quantity"),
        (8, "empty"),
    ]
    assert len(result.snapshot.positions) == 2
    (negative,) = result.negative_positions
    assert negative.quantity == Decimal("-0.125")
    assert negative.activity is PositionActivity.NEGATIVE
    assert sum(p.quantity for p in result.active_positions) == Decimal("0.25")
    (warning,) = [w for w in result.snapshot.warnings if w.code == "negative_quantity"]
    assert (warning.source_row, warning.field) == (6, "quantity")


def test_identical_raw_tickers_across_instrument_types_are_never_merged():
    result = parse([synthetic_row(Typ=kind) for kind in ("Aktie", "ETF", "Derivat")])
    assert len(result.active_positions) == 3
    assert {p.instrument.instrument_type for p in result.active_positions} == set(
        InstrumentType
    )
    assert {p.instrument.display_ticker for p in result.active_positions} == {"DEMO"}
    assert all(p.instrument.instrument_id is None for p in result.active_positions)


def test_summary_name_with_valid_instrument_type_is_not_discarded():
    assert len(parse([synthetic_row(Position="Total")]).active_positions) == 1


@pytest.mark.parametrize("field", ["Position", "Typ"])
def test_required_text_fields_reject_incomplete_positions_with_safe_diagnostics(field):
    result = parse([synthetic_row(**{field: None})])
    assert result.snapshot.status is SnapshotStatus.PARTIAL
    assert not result.active_positions
    assert result.snapshot.warnings[0].source_row == 2
    assert result.snapshot.warnings[0].field in {"name", "instrument_type"}


def test_headers_map_by_name_within_a_to_m_and_ignore_extra_aggregate_columns():
    order = list(range(13))[::-1]
    row = synthetic_row()
    headers = ["  " + SHEET_HEADERS[i].upper() + "  " for i in order] + ["Anzahl"]
    result = parse_sheet_rows(
        headers,
        [[row[i] for i in order] + ["999999"]],
        imported_at=IMPORTED,
        metadata=GERMAN,
    )
    assert result.active_positions[0].quantity == Decimal("1.125")
    assert result.active_positions[0].name == "Synthetische Position"


@pytest.mark.parametrize(
    "headers",
    [
        SHEET_HEADERS[:-1],
        SHEET_HEADERS[:3] + ("synthetic-sensitive-header",) + SHEET_HEADERS[4:],
        SHEET_HEADERS[:3] + ("Ticker",) + SHEET_HEADERS[4:],
        SHEET_HEADERS[:3] + ("unknown",) + SHEET_HEADERS[4:] + ("Anzahl",),
    ],
)
def test_missing_duplicate_or_moved_outside_a_to_m_headers_fail_safely(headers):
    result = parse_sheet_rows(
        headers, [synthetic_row()], imported_at=IMPORTED, metadata=GERMAN, header_row=4
    )
    assert result.snapshot.status is SnapshotStatus.FAILED
    assert not result.snapshot.positions
    assert all(w.source_row == 4 and w.field for w in result.snapshot.warnings)
    assert "synthetic-sensitive-header" not in repr(result.snapshot.warnings)
    assert {w.code for w in result.snapshot.warnings} <= {
        "missing_column",
        "duplicate_column",
    }


def test_short_rows_keep_holdings_with_missing_optional_inputs_unknown():
    result = parse([["Synthetisch", "", "ETF", "0,125"]])
    (position,) = result.active_positions
    assert result.snapshot.status is SnapshotStatus.COMPLETE
    assert position.instrument.display_ticker is None
    assert position.instrument.source_symbol is None
    assert position.instrument.quote_unit is None
    assert position.category is None
    assert position.valuation.price is None
    assert position.valuation.status is ValueStatus.MISSING
    assert position.cost_basis.status is CostBasisStatus.MISSING


@pytest.mark.parametrize(
    ("price", "status", "expected"),
    [
        (None, ValueStatus.MISSING, None),
        ("k. A.", ValueStatus.MISSING, None),
        ("0", ValueStatus.AVAILABLE, Decimal("0")),
        ("-1", ValueStatus.INVALID, None),
        ("12,3,4", ValueStatus.INVALID, None),
    ],
)
def test_missing_zero_negative_and_invalid_price_are_distinct(price, status, expected):
    (position,) = parse([synthetic_row(**{"Kurs (EUR)": price})]).active_positions
    assert position.valuation.status is status
    assert position.valuation.price == expected


@pytest.mark.parametrize(
    ("cost", "status", "expected"),
    [
        (None, CostBasisStatus.MISSING, None),
        ("", CostBasisStatus.MISSING, None),
        ("k. A.", CostBasisStatus.MISSING, None),
        ("0", CostBasisStatus.UNVERIFIED, Decimal("0")),
        ("-1", CostBasisStatus.INVALID, None),
        ("bad", CostBasisStatus.INVALID, None),
    ],
)
def test_invalid_or_missing_cost_never_becomes_zero_or_verified(cost, status, expected):
    (position,) = parse([synthetic_row(**{"Investiert (EUR)": cost})]).active_positions
    assert position.cost_basis.status is status
    assert position.cost_basis.amount_eur == expected
    assert not position.cost_basis.usable_for_since_purchase


def test_only_explicit_metadata_establishes_cost_semantics_and_market_timestamp():
    observed = IMPORTED - timedelta(hours=1)
    metadata = replace(
        GERMAN, cost_basis_verified_remaining=True, sheet_price_observed_at=observed
    )
    (position,) = parse([synthetic_row()], metadata).active_positions
    assert position.cost_basis.status is CostBasisStatus.VERIFIED_REMAINING
    assert position.cost_basis.usable_for_since_purchase
    assert position.valuation.provenance.observed_at == observed
    assert not any(w.code == "unknown_price_time" for w in position.warnings)
    (zero,) = parse(
        [synthetic_row(**{"Investiert (EUR)": "0"})], metadata
    ).active_positions
    assert not zero.cost_basis.usable_for_since_purchase


def test_j_percentage_points_are_not_scaled_and_results_do_not_override_inputs():
    result = parse(
        [
            synthetic_row(
                **{
                    "G/V (%)": "20 %",
                    "Gewicht (%)": "0,5",
                    "G/V (EUR)": "-1.000,25 €",
                    "Aktueller Wert (EUR)": "999.999,99",
                }
            )
        ]
    )
    (reference,) = result.references
    assert reference.gain_loss_percent_points == Decimal("20")
    assert reference.weight_percent_points == Decimal("0.5")
    assert reference.gain_loss_eur == Decimal("-1000.25")
    assert reference.current_value_eur == Decimal("999999.99")
    (position,) = result.active_positions
    assert position.valuation.price == Decimal("12.50")
    assert position.cost_basis.amount_eur == Decimal("10.00")
    assert not position.cost_basis.usable_for_since_purchase


def test_invalid_result_cells_warn_without_removing_an_otherwise_valid_holding():
    result = parse(
        [
            synthetic_row(
                **{
                    "G/V (%)": "synthetic-private-cell",
                    "Gewicht (%)": "101",
                    "Aktueller Wert (EUR)": "-1",
                }
            )
        ]
    )
    assert len(result.active_positions) == 1
    assert result.snapshot.status is SnapshotStatus.COMPLETE
    assert result.references[0].gain_loss_percent_points is None
    assert result.references[0].weight_percent_points is None
    assert result.references[0].current_value_eur is None
    warnings = [w for w in result.snapshot.warnings if w.code == "invalid_reference"]
    assert {w.field for w in warnings} == {
        "gain_loss_percent_points",
        "weight_percent_points",
        "current_value_eur",
    }
    assert all(w.source_row == 2 for w in warnings)
    assert "synthetic-private-cell" not in repr(result.snapshot.warnings)


def test_invalid_optional_text_and_currency_remain_unknown_without_inference():
    (position,) = parse(
        [synthetic_row(Ticker=123, **{"Währung": "GBp", "Kategorie": "k. A."})]
    ).active_positions
    assert position.instrument.display_ticker is None
    assert position.instrument.quote_unit is None
    assert position.category is None
    assert {w.field for w in position.warnings} >= {"display_ticker", "quote_currency"}
    assert position.valuation.unit.currency == "EUR"


def test_empty_source_is_success_and_invalid_only_source_is_partial_not_empty_success():
    assert parse([]).snapshot.status is SnapshotStatus.COMPLETE
    assert (
        parse([synthetic_row(Anzahl="bad")]).snapshot.status is SnapshotStatus.PARTIAL
    )


def test_metadata_and_offsets_must_be_explicit_and_valid_even_for_empty_sources():
    with pytest.raises(TypeError, match="NumberLocale"):
        SheetMetadata("de_DE")
    with pytest.raises(TypeError, match="bool"):
        replace(GERMAN, cost_basis_verified_remaining="false")
    with pytest.raises(ValueError, match="timezone"):
        replace(GERMAN, sheet_price_observed_at=IMPORTED.replace(tzinfo=None))
    with pytest.raises(ValueError, match="timezone"):
        parse_sheet_rows(
            SHEET_HEADERS,
            [],
            imported_at=IMPORTED.replace(tzinfo=None),
            metadata=GERMAN,
        )
    for row in (0, -1, True):
        with pytest.raises(ValueError, match="header_row"):
            parse([], header_row=row)


def test_parser_runs_in_a_fresh_process_without_ui_providers_or_network():
    code = """
import importlib.abc
import socket
import sys
class OfflineBoundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {'streamlit', 'pandas', 'plotly', 'yfinance', 'requests', 'gspread'}:
            raise AssertionError('Parser loaded an external dependency')
sys.meta_path.insert(0, OfflineBoundary())
def no_network(*args, **kwargs):
    raise AssertionError('Parser attempted a network connection')
socket.socket = no_network
socket.create_connection = no_network
from datetime import datetime, timezone
from portfolio_dashboard.sheet_parser import NumberLocale, SHEET_HEADERS, SheetMetadata, parse_sheet_rows
result = parse_sheet_rows(SHEET_HEADERS, [['Synthetic', '', 'ETF', '0,125']],
    imported_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    metadata=SheetMetadata(NumberLocale.GERMAN))
assert len(result.active_positions) == 1
assert not result.active_positions[0].cost_basis.usable_for_since_purchase
"""
    subprocess.run([sys.executable, "-c", code], check=True)
