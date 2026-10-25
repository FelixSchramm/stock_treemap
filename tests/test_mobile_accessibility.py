"""Tests for mobile usability, non-hover access, contrast and keyboard support (M5-05)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from streamlit.testing.v1 import AppTest

from portfolio_dashboard.models import (
    CostBasis,
    CostBasisStatus,
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
from portfolio_dashboard.treemap import (
    MISSING_COLOR,
    SORT_BY_VALUE,
    TILE_TEXT_COLOR,
    Performance,
    build_position_details,
    build_table_rows,
    build_treemap,
    color_for,
    contrast_ratio,
    performance_direction_label,
    sort_table_rows,
)
from portfolio_dashboard.ui import (
    FILTER_CATEGORY_KEY,
    FILTER_QUERY_KEY,
    FILTER_TYPE_KEY,
    MOBILE_ACCESSIBILITY_CSS,
    REFRESH_BUTTON_KEY,
    SORT_DIRECTION_ASC,
    SORT_FIELD_KEY,
    SORT_ORDER_KEY,
)
from portfolio_dashboard.valuations import value_portfolio

APP_PATH = Path(__file__).parents[1] / "app.py"
STAMP = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
SHEET = Provenance(ValuationSource.SHEET, STAMP)
EUR = QuoteUnit("EUR", Decimal("1"))


def test_all_scale_and_missing_colors_meet_wcag_aa_contrast_with_tile_text():
    scale = Decimal("50")
    for step in range(-10, 11):
        pct = scale * Decimal(step) / Decimal(10)
        bg = color_for(pct, scale)
        ratio = contrast_ratio(TILE_TEXT_COLOR, bg)
        assert ratio >= 4.5, f"Contrast {ratio:.2f} < 4.5 for {pct}% ({bg})"

    missing_ratio = contrast_ratio(TILE_TEXT_COLOR, MISSING_COLOR)
    assert missing_ratio >= 4.5


def test_non_color_direction_labels_distinguish_gain_loss_zero_and_missing():
    assert performance_direction_label(Decimal("1.25")) == "Gewinn (+)"
    assert performance_direction_label(Decimal("-0.80")) == "Verlust (−)"
    assert performance_direction_label(Decimal("0.00")) == "Unverändert (0,00 %)"
    assert performance_direction_label(Decimal("-0.001")) == "Unverändert (0,00 %)"
    assert performance_direction_label(None) == "Keine Kennzahl (n. v.)"


def test_every_tooltip_field_is_accessible_without_hover_including_tiny_positions():
    large_pos = Position(
        name="Großposition",
        quantity=Decimal("100"),
        instrument=Instrument(InstrumentType.STOCK, display_ticker="BIG"),
        category="Kern",
        source_row=1,
        imported_at=STAMP,
        cost_basis=CostBasis(Decimal("9000"), CostBasisStatus.VERIFIED_REMAINING),
        valuation=Valuation(Decimal("100"), ValueStatus.AVAILABLE, SHEET, EUR),
    )
    tiny_pos = Position(
        name="Kleinstposition",
        quantity=Decimal("0.5"),
        instrument=Instrument(InstrumentType.ETF, display_ticker="TINY"),
        category="Nische",
        source_row=2,
        imported_at=STAMP,
        valuation=Valuation(Decimal("10"), ValueStatus.AVAILABLE, SHEET, EUR),
    )
    unvalued_pos = Position(
        name="Datenlücke",
        quantity=Decimal("3"),
        instrument=Instrument(InstrumentType.DERIVATIVE, display_ticker="GAP"),
        category="Derivate",
        source_row=3,
        imported_at=STAMP,
        valuation=None,
    )
    snapshot = PortfolioSnapshot(
        STAMP, SnapshotStatus.COMPLETE, (large_pos, tiny_pos, unvalued_pos)
    )
    valuation = value_portfolio(snapshot, ())
    perfs = (
        Performance(Decimal("11.11"), date(2026, 10, 22)),
        Performance(Decimal("-2.50"), date(2026, 10, 22)),
        Performance(None),
    )

    treemap = build_treemap(valuation, perfs)
    rows = build_table_rows(valuation, perfs)
    details = build_position_details(rows)

    # All 3 active positions (including tiny 0.05% and unvalued) are in table & details.
    assert len(rows) == 3
    assert len(details) == 3

    # Every line in each TreemapTile.tooltip is present in the non-hover detail lines!
    for tile, detail_block in zip(treemap.tiles, details[:2]):
        joined_details = "\n".join(detail_block)
        for tooltip_line in tile.tooltip:
            assert tooltip_line in joined_details

    # Sorting ascending by value brings the tiny 5-EUR position to index 0 for easy access.
    asc_rows = sort_table_rows(rows, SORT_BY_VALUE, descending=False)
    assert [r.name for r in asc_rows] == [
        "Kleinstposition",
        "Großposition",
        "Datenlücke",
    ]


def test_narrow_screen_workflow_view_switch_filters_refresh_and_non_hover_details():
    app = AppTest.from_file(str(APP_PATH)).run()
    assert not app.exception

    # Verify mobile touch-target & keyboard focus CSS is injected.
    markdown_blobs = "\n".join(m.value for m in app.markdown)
    assert "min-height: 2.75rem" in MOBILE_ACCESSIBILITY_CSS
    assert ":focus-visible" in MOBILE_ACCESSIBILITY_CSS
    assert "min-height: 2.75rem" in markdown_blobs

    # Non-hover details expander renders complete position details without hover.
    assert "Demo-Aktie A (DEMO-A)" in markdown_blobs
    assert "Anteil am bewerteten Depot: 60,00 %" in markdown_blobs
    assert "Demo-Fonds B (DEMO-B)" in markdown_blobs
    assert "Anteil am bewerteten Depot: 40,00 %" in markdown_blobs

    # Exercise full workflow: view switch, period switch, filter, sort, and refresh.
    app.radio[0].set_value("Seit Kauf").run()
    assert not app.exception
    assert app.subheader[0].value == "Meine Entwicklung · Seit Kauf"

    app.radio[0].set_value("Heute").run()
    app.selectbox(key=FILTER_TYPE_KEY).set_value("Aktie").run()
    app.selectbox(key=FILTER_CATEGORY_KEY).set_value("Demo").run()
    app.text_input(key=FILTER_QUERY_KEY).set_value("Demo-Aktie").run()
    app.selectbox(key=SORT_FIELD_KEY).set_value("Name").run()
    app.selectbox(key=SORT_ORDER_KEY).set_value(SORT_DIRECTION_ASC).run()
    app.button(key=REFRESH_BUTTON_KEY).click().run()

    assert not app.exception
    assert app.dataframe[0].value["Instrument"].tolist() == ["Demo-Aktie A"]
