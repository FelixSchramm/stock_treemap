"""Synthetic treemap value-model cases; offline and independent of Streamlit."""

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
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
from portfolio_dashboard.returns import portfolio_returns
from portfolio_dashboard.treemap import (
    ALL_FILTER,
    COLOR_CAP_PERCENT,
    MISSING_COLOR,
    NEGATIVE_COLOR,
    NEUTRAL_COLOR,
    NO_CATEGORY_LABEL,
    POSITIVE_COLOR,
    SHEET_UNKNOWN_QUOTE_STATUS,
    SORT_BY_NAME,
    SORT_BY_PERFORMANCE,
    SORT_BY_VALUE,
    WEIGHT_COLUMN_LABEL,
    FilterCriteria,
    Performance,
    available_categories,
    build_kpis,
    build_table_rows,
    build_treemap,
    color_for,
    filter_groups,
    format_eur,
    format_percent,
    personal_performances,
    sort_table_rows,
)
from portfolio_dashboard.ui import treemap_figure
from portfolio_dashboard.valuations import FxRate, value_portfolio

APP_PATH = Path(__file__).parents[1] / "app.py"
STAMP = datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc)
SHEET = Provenance(ValuationSource.SHEET, STAMP)
EUR = QuoteUnit("EUR", Decimal("1"))
USD = QuoteUnit("USD", Decimal("1"))


def hexcolor(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def position(name, kind, quantity, price, unit=EUR, ticker=None, cost=None, row=1):
    quote = None
    if price is not None:
        quote = Valuation(Decimal(price), ValueStatus.AVAILABLE, SHEET, unit)
    return Position(
        name=name,
        quantity=Decimal(quantity),
        instrument=Instrument(kind, display_ticker=ticker),
        category=None,
        source_row=row,
        imported_at=STAMP,
        cost_basis=CostBasis(Decimal(cost), CostBasisStatus.VERIFIED_REMAINING)
        if cost
        else CostBasis(),
        valuation=quote,
    )


def valued(*positions, fx=None):
    snapshot = PortfolioSnapshot(STAMP, SnapshotStatus.COMPLETE, tuple(positions))
    return value_portfolio(snapshot, (), fx)


def usd_rate():
    return {"USD": FxRate("USD", "EUR", Decimal("0.5"), SHEET)}


def test_areas_are_eur_values_and_independent_of_the_selected_performance():
    valuation = valued(
        position("Aktie", InstrumentType.STOCK, "10", "12", ticker="DEMO-A", cost="100", row=1),
        position("Fonds", InstrumentType.ETF, "2", "40", unit=USD, row=2),
        fx=usd_rate(),
    )
    course = [Performance(Decimal("1.5"), date(2026, 1, 2)), Performance(Decimal("-3"))]
    week = [Performance(Decimal("-9")), Performance(Decimal("40"))]
    personal = personal_performances(portfolio_returns(valuation))

    models = [build_treemap(valuation, perf) for perf in (course, week, personal)]
    areas = [[t.amount_eur for t in model.tiles] for model in models]
    assert areas[0] == areas[1] == areas[2] == [Decimal("120"), Decimal("40.0")]
    assert [t.color for t in models[0].tiles] != [t.color for t in models[1].tiles]


def test_missing_return_is_gray_n_v_and_keeps_its_area():
    valuation = valued(position("Aktie", InstrumentType.STOCK, "1", "50", ticker="DEMO-A"))
    tile = build_treemap(valuation, [Performance(None)]).tiles[0]
    assert tile.amount_eur == Decimal("50")
    assert tile.color == MISSING_COLOR
    assert tile.label == ("Aktie", "DEMO-A", "n. v.")
    assert "Entwicklung: n. v." in tile.tooltip
    assert "Referenzdatum: n. v." in tile.tooltip


def test_personal_view_without_verified_cost_is_n_v_but_with_cost_is_signed():
    valuation = valued(
        position("Mit Einstand", InstrumentType.STOCK, "1", "120", cost="100", row=1),
        position("Ohne Einstand", InstrumentType.STOCK, "1", "80", row=2),
    )
    tiles = build_treemap(valuation, personal_performances(portfolio_returns(valuation))).tiles
    assert [t.label[-1] for t in tiles] == ["+20,00 %", "n. v."]
    assert [t.color == MISSING_COLOR for t in tiles] == [False, True]


def test_unknown_or_nonpositive_value_creates_no_tile():
    valuation = valued(
        position("Bekannt", InstrumentType.STOCK, "1", "10", row=1),
        position("Kein Kurs", InstrumentType.STOCK, "1", None, row=2),
        position("Null", InstrumentType.STOCK, "1", "0", row=3),
        position("Ohne Bestand", InstrumentType.STOCK, "0", "10", row=4),
        position("Fremdwährung ohne FX", InstrumentType.STOCK, "1", "10", unit=USD, row=5),
    )
    model = build_treemap(valuation, [Performance(Decimal(1))] * 5)
    assert [t.name for t in model.tiles] == ["Bekannt"]
    assert model.omitted_count == 4


def test_color_cap_changes_only_the_color_never_the_numbers():
    valuation = valued(
        position("Ausreißer", InstrumentType.STOCK, "1", "10", row=1),
        position("Normal", InstrumentType.STOCK, "1", "10", row=2),
    )
    model = build_treemap(
        valuation, [Performance(Decimal("120.5")), Performance(Decimal("-25"))]
    )
    assert model.scale_percent == COLOR_CAP_PERCENT and model.capped
    assert model.tiles[0].percent == Decimal("120.5")
    assert model.tiles[0].label[-1] == "+120,50 %"
    assert "Entwicklung: +120,50 %" in model.tiles[0].tooltip
    assert model.tiles[0].color == hexcolor(POSITIVE_COLOR)
    assert model.tiles[1].color == color_for(Decimal("-25"), COLOR_CAP_PERCENT)


def test_scale_is_symmetric_and_derived_from_visible_values():
    valuation = valued(
        position("Hoch", InstrumentType.STOCK, "1", "10", row=1),
        position("Tief", InstrumentType.STOCK, "1", "10", row=2),
        position("Unsichtbar", InstrumentType.STOCK, "1", None, row=3),
    )
    model = build_treemap(
        valuation,
        [Performance(Decimal("4")), Performance(Decimal("-8")), Performance(Decimal("90"))],
    )
    assert model.scale_percent == Decimal("8") and not model.capped
    assert model.tiles[1].color == hexcolor(NEGATIVE_COLOR)
    assert color_for(Decimal("8"), model.scale_percent) == hexcolor(POSITIVE_COLOR)
    assert color_for(Decimal("0"), model.scale_percent) == hexcolor(NEUTRAL_COLOR)
    assert color_for(Decimal("-4"), Decimal("8")) != color_for(Decimal("4"), Decimal("8"))


def test_format_percent_is_german_signed_and_never_negative_zero():
    assert format_percent(Decimal("1234.5")) == "+1.234,50 %"
    assert format_percent(Decimal("-0.004")) == "0,00 %"
    assert format_percent(Decimal("0")) == "0,00 %"
    assert format_percent(Decimal("-2.345")) == "-2,34 %"
    assert format_percent(None) == "n. v."


def test_distinct_derivatives_with_same_name_or_ticker_are_never_merged():
    valuation = valued(
        position("Call Basiswert", InstrumentType.DERIVATIVE, "3", "5", ticker="DEMO-X", row=1),
        position("Call Basiswert", InstrumentType.DERIVATIVE, "2", "7", ticker="DEMO-X", row=2),
        position("Basiswert", InstrumentType.STOCK, "1", "100", ticker="DEMO-X", row=3),
    )
    model = build_treemap(valuation, [Performance(Decimal(1))] * 3)
    assert [t.amount_eur for t in model.tiles] == [Decimal("15"), Decimal("14"), Decimal("100")]


def test_tooltip_contains_quantity_type_currency_value_weight_stand_and_reference():
    valuation = valued(
        position("Fonds", InstrumentType.ETF, "2.5", "40", unit=USD, ticker="DEMO-B", row=1),
        position("Aktie", InstrumentType.STOCK, "10", "15", row=2),
        fx=usd_rate(),
    )
    tile = build_treemap(
        valuation, [Performance(Decimal("2"), date(2026, 1, 2)), Performance(None)]
    ).tiles[0]
    assert tile.tooltip == (
        "Fonds (DEMO-B)",
        "Typ: ETF",
        "Währung: USD",
        "Stückzahl: 2,5",
        "Wert: 50,00 EUR",
        "Anteil am bewerteten Depot: 25,00 %",
        "Entwicklung: +2,00 %",
        "Datenstand: Kursstand unbekannt",
        "Referenzdatum: 02.01.2026",
    )


def test_one_performance_per_group_is_required():
    valuation = valued(position("Aktie", InstrumentType.STOCK, "1", "10"))
    with pytest.raises(ValueError):
        build_treemap(valuation, [])


def test_figure_uses_model_values_and_escapes_text():
    valuation = valued(position("<b>Evil</b>", InstrumentType.STOCK, "1", "10"))
    figure = treemap_figure(build_treemap(valuation, [Performance(Decimal(1))]))
    trace = figure.data[0]
    assert list(trace.values) == [10.0]
    assert "<b>" not in trace.text[0] and "<b>" not in trace.hovertext[0]
    assert "<br>" in trace.hovertext[0]


def test_dashboard_renders_same_tile_areas_in_both_views():
    app = AppTest.from_file(str(APP_PATH)).run()
    assert not app.exception
    course = json.loads(app.get("plotly_chart")[0].proto.spec)["data"][0]
    app.radio[0].set_value("Seit Kauf").run()
    assert not app.exception
    personal = json.loads(app.get("plotly_chart")[0].proto.spec)["data"][0]
    assert course["values"] == personal["values"] == [1200.0, 800.0]
    assert course["marker"]["colors"] != personal["marker"]["colors"]
    assert set(personal["marker"]["colors"]) == {MISSING_COLOR}


def test_tooltip_shows_only_a_proven_quote_time_never_the_retrieval_time():
    observed = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    provider = Provenance(
        ValuationSource.PROVIDER, STAMP, observed_at=observed, provider="demo-provider"
    )
    valuation = valued(
        position("Beobachtet", InstrumentType.STOCK, "1", "10", row=1),
        position("Sheet", InstrumentType.STOCK, "1", "10", row=2),
    )
    quote = valuation.groups[0].members[0].position.valuation
    object.__setattr__(quote, "provenance", provider)
    observed_tile, sheet_tile = build_treemap(valuation, [Performance(None)] * 2).tiles
    assert "Datenstand: 05.01.2026, 09:00 UTC" in observed_tile.tooltip
    assert "Datenstand: Kursstand unbekannt" in sheet_tile.tooltip
    assert not any("15:30" in line for line in sheet_tile.tooltip)


def test_partial_hint_ignores_inactive_members_of_a_group():
    valuation = valued(
        position("Aktie", InstrumentType.STOCK, "1", "10", row=1),
        position("Aktie", InstrumentType.STOCK, "0", "10", row=2),
    )
    tiles = build_treemap(valuation, [Performance(None)] * len(valuation.groups)).tiles
    assert all("Hinweis: nur teilweise bewertet" not in t.tooltip for t in tiles)


def test_legend_and_note_texts_are_visible():
    app = AppTest.from_file(str(APP_PATH)).run()
    assert not app.exception
    legend = " ".join(m.value for m in app.markdown)
    assert "<span>-" in legend and " %</span>" in legend and "n. v." in legend
    assert "Fläche: EUR-Positionswert. Farbskala symmetrisch um 0 %." in [
        c.value for c in app.caption
    ]


def test_unvaluable_positions_remain_visible_in_table_with_status_and_inactive_excluded():
    valuation = valued(
        position("Bewertet", InstrumentType.STOCK, "10", "20", ticker="VAL-1", row=1),
        position("Ohne Kurs", InstrumentType.ETF, "5", None, ticker="MISS-1", row=2),
        position(
            "Fremdwährung ohne FX",
            InstrumentType.DERIVATIVE,
            "3",
            "15",
            unit=USD,
            ticker="FX-1",
            row=3,
        ),
        position("Verkauft", InstrumentType.STOCK, "0", "50", ticker="ZERO-1", row=4),
    )
    perfs = [
        Performance(Decimal("3.5"), date(2026, 1, 2)),
        Performance(None),
        Performance(None),
        Performance(None),
    ]
    rows = build_table_rows(valuation, perfs)
    assert [r.name for r in rows] == [
        "Bewertet",
        "Ohne Kurs",
        "Fremdwährung ohne FX",
    ]
    assert rows[0].amount_eur == Decimal("200")
    assert rows[0].weight_percent == Decimal("100")
    assert rows[0].status == SHEET_UNKNOWN_QUOTE_STATUS
    assert rows[1].amount_eur is None
    assert rows[1].amount_eur_label == "n. v."
    assert rows[1].weight_label == "n. v."
    assert rows[1].status == "Kurs fehlt"
    assert rows[2].amount_eur is None
    assert rows[2].status == "FX-Kurs fehlt"


def test_weight_column_refers_to_full_portfolio_even_when_filtered():
    p1 = Position(
        name="Alpha Aktie",
        quantity=Decimal("10"),
        instrument=Instrument(InstrumentType.STOCK, display_ticker="ALP"),
        category="Tech",
        source_row=1,
        imported_at=STAMP,
        valuation=Valuation(Decimal("30"), ValueStatus.AVAILABLE, SHEET, EUR),
    )
    p2 = Position(
        name="Beta ETF",
        quantity=Decimal("10"),
        instrument=Instrument(InstrumentType.ETF, display_ticker="BET"),
        category="Welt",
        source_row=2,
        imported_at=STAMP,
        valuation=Valuation(Decimal("70"), ValueStatus.AVAILABLE, SHEET, EUR),
    )
    valuation = valued(p1, p2)
    perfs = [Performance(Decimal("1")), Performance(Decimal("2"))]

    criteria = FilterCriteria(instrument_type="Aktie", category="Tech", query="alp")
    filtered_groups, filtered_perfs = filter_groups(valuation, perfs, criteria)
    treemap = build_treemap(valuation, filtered_perfs, groups=filtered_groups)
    table_rows = build_table_rows(valuation, perfs, criteria=criteria)

    assert len(treemap.tiles) == 1
    assert "Anteil am bewerteten Depot: 30,00 %" in treemap.tiles[0].tooltip
    assert len(table_rows) == 1
    assert table_rows[0].weight_percent == Decimal("30")
    assert table_rows[0].to_display_dict()[WEIGHT_COLUMN_LABEL] == 30.0


def test_kpis_report_coverage_winner_loser_and_personal_cost_comparison():
    valuation = valued(
        position("Gewinner", InstrumentType.STOCK, "1", "150", cost="100", row=1),
        position("Verlierer", InstrumentType.ETF, "1", "80", cost="100", row=2),
        position("Ohne Einstand", InstrumentType.STOCK, "1", "70", row=3),
        position("Ohne Kurs", InstrumentType.DERIVATIVE, "2", None, row=4),
    )
    returns = portfolio_returns(valuation)
    perfs = personal_performances(returns)
    kpis = build_kpis(
        valuation, perfs, is_personal_view=True, returns=returns
    )

    assert kpis.total_eur == Decimal("300")
    assert kpis.total_eur_label == "300,00 EUR"
    assert kpis.active_group_count == 4
    assert kpis.valued_group_count == 3
    assert kpis.coverage_label == "3 von 4 bewertet"
    assert kpis.measured_group_count == 2
    assert "2 von 4 aktiven Positionen mit Kennzahl" in kpis.performance_reference_label
    assert (kpis.winner_label, kpis.winner_note) == ("+50,00 %", "Gewinner")
    assert (kpis.loser_label, kpis.loser_note) == ("-20,00 %", "Verlierer")
    assert kpis.personal_eligible_group_count == 2
    assert kpis.personal_cost_basis_eur == Decimal("200")
    assert kpis.personal_gain_eur == Decimal("30")
    assert kpis.personal_gain_label == "+30,00 EUR"
    assert kpis.personal_return_label == "+15,00 %"
    assert (
        kpis.personal_coverage_label
        == "2 von 4 Positionen mit vollständigem Einstand"
    )


def test_kpi_labels_use_full_names_when_stock_and_derivative_share_a_ticker():
    valuation = valued(
        position("Alpha Synth AG", InstrumentType.STOCK, "10", "11", ticker="SYN-A", row=1),
        position(
            "Turbo Long Alpha Synth",
            InstrumentType.DERIVATIVE,
            "5",
            "10",
            ticker="SYN-A",
            row=2,
        ),
    )
    kpis = build_kpis(valuation, [Performance(Decimal("-3")), Performance(Decimal("8"))])
    assert len(valuation.groups) == 2
    assert (kpis.winner_label, kpis.winner_note) == ("+8,00 %", "Turbo Long Alpha Synth")
    assert (kpis.loser_label, kpis.loser_note) == ("-3,00 %", "Alpha Synth AG")


def test_kpi_labels_keep_full_names_without_ticker_despite_a_shared_first_word():
    valuation = valued(
        position("Synthfonds Core Welt", InstrumentType.ETF, "4", "90", row=1),
        position("Synthfonds Schwellenländer", InstrumentType.ETF, "2", "50", row=2),
    )
    kpis = build_kpis(valuation, [Performance(Decimal("5")), Performance(Decimal("-2"))])
    assert (kpis.winner_label, kpis.winner_note) == ("+5,00 %", "Synthfonds Core Welt")
    assert (kpis.loser_label, kpis.loser_note) == ("-2,00 %", "Synthfonds Schwellenländer")


def test_kpi_labels_use_a_unique_ticker_and_the_full_name_without_ticker():
    valuation = valued(
        position("Beta Synth AG", InstrumentType.STOCK, "1", "70", ticker="SYN-B", row=1),
        position("Gamma Synth AG", InstrumentType.STOCK, "1", "30", row=2),
    )
    kpis = build_kpis(valuation, [Performance(Decimal("7")), Performance(Decimal("-1.5"))])
    assert (kpis.winner_label, kpis.winner_note) == ("+7,00 %", "SYN-B")
    assert kpis.winner.name == "Beta Synth AG"
    assert (kpis.loser_label, kpis.loser_note) == ("-1,50 %", "Gamma Synth AG")


def test_kpi_without_winner_or_loser_shows_n_v_with_a_short_reason():
    valuation = valued(
        position("Delta Synth AG", InstrumentType.STOCK, "1", "10", ticker="SYN-D", row=1),
        position("Epsilon Synth AG", InstrumentType.STOCK, "1", "10", row=2),
    )
    only_losses = build_kpis(valuation, [Performance(Decimal("-4")), Performance(None)])
    assert only_losses.winner is None
    assert (only_losses.winner_label, only_losses.winner_note) == ("n. v.", "keine > 0 %")
    assert (only_losses.loser_label, only_losses.loser_note) == ("-4,00 %", "SYN-D")

    unmeasured = build_kpis(valuation, [Performance(None), Performance(None)])
    assert (unmeasured.winner_label, unmeasured.winner_note) == ("n. v.", None)
    assert (unmeasured.loser_label, unmeasured.loser_note) == ("n. v.", None)


def test_sort_table_rows_always_places_missing_values_last_and_breaks_ties_by_name():
    valuation = valued(
        position("Zeta", InstrumentType.STOCK, "1", "100", row=1),
        position("Alpha", InstrumentType.STOCK, "1", "100", row=2),
        position("Lücke", InstrumentType.ETF, "1", None, row=3),
    )
    perfs = [
        Performance(Decimal("-5")),
        Performance(Decimal("10")),
        Performance(None),
    ]
    rows = build_table_rows(valuation, perfs)

    desc_val = sort_table_rows(rows, SORT_BY_VALUE, descending=True)
    assert [r.name for r in desc_val] == ["Alpha", "Zeta", "Lücke"]

    asc_val = sort_table_rows(rows, SORT_BY_VALUE, descending=False)
    assert [r.name for r in asc_val] == ["Alpha", "Zeta", "Lücke"]

    asc_perf = sort_table_rows(rows, SORT_BY_PERFORMANCE, descending=False)
    assert [r.name for r in asc_perf] == ["Zeta", "Alpha", "Lücke"]

    by_name_desc = sort_table_rows(rows, SORT_BY_NAME, descending=True)
    assert [r.name for r in by_name_desc] == ["Zeta", "Lücke", "Alpha"]


def test_available_categories_includes_uncategorized_option_when_mixed():
    p1 = Position(
        name="Kat A",
        quantity=Decimal("1"),
        instrument=Instrument(InstrumentType.STOCK),
        category="Dividende",
        source_row=1,
        imported_at=STAMP,
    )
    p2 = Position(
        name="Ohne Kat",
        quantity=Decimal("1"),
        instrument=Instrument(InstrumentType.ETF),
        category=None,
        source_row=2,
        imported_at=STAMP,
    )
    valuation = valued(p1, p2)
    cats = available_categories(valuation)
    assert cats == (ALL_FILTER, "Dividende", NO_CATEGORY_LABEL)
    filtered, _ = filter_groups(
        valuation,
        [Performance(None), Performance(None)],
        FilterCriteria(category=NO_CATEGORY_LABEL),
    )
    assert len(filtered) == 1
    assert filtered[0].members[0].position.name == "Ohne Kat"
    assert format_eur(None) == "n. v."
    assert format_eur(Decimal("12.5"), signed=True) == "+12,50 EUR"

