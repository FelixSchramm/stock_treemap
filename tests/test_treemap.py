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
    COLOR_CAP_PERCENT,
    MISSING_COLOR,
    NEGATIVE_COLOR,
    NEUTRAL_COLOR,
    POSITIVE_COLOR,
    Performance,
    build_treemap,
    color_for,
    format_percent,
    personal_performances,
)
from portfolio_dashboard.ui import PERSONAL_VIEW, VIEW_KEY, treemap_figure
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
    app.radio(key=VIEW_KEY).set_value(PERSONAL_VIEW).run()
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
