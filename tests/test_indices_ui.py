"""Offline AppTests: automatic loading, controls, cache and area isolation."""

import os
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from test_indices import MEMBER, Market, Source
from test_watchlist_refresh import CLOSED

from portfolio_dashboard import indices
from portfolio_dashboard.demo import demo_watchlist_entries
from portfolio_dashboard.indices import IndexBundle, IndexRow, IndicesLoader
from portfolio_dashboard.indices_view import INDEX_KEY, LOADER_KEY, PERIOD_KEY
from portfolio_dashboard.ui import AREA_RADIO_KEY, REFRESH_BUTTON_KEY

APP = Path(__file__).parents[1] / "app.py"


@pytest.fixture
def dashboard(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("PORTFOLIO_"):
            monkeypatch.delenv(key)
    source, market = Source(), Market()
    loader = IndicesLoader(source, market, clock=lambda: CLOSED)
    monkeypatch.setattr(indices, "DemoIndices", lambda: loader)
    return AppTest.from_file(str(APP), default_timeout=30), source, market


def test_auto_load_periods_selection_refresh_and_other_areas_are_isolated(dashboard):
    app, source, market = dashboard
    app.run()
    assert not app.exception and not source.reads and not market.calls
    app.radio(key=AREA_RADIO_KEY).set_value("Indizes").run()
    assert not app.exception and source.reads == ["DAX"]
    assert app.selectbox(key="_indices.selected").value == "DAX"
    assert app.radio(key="_indices.period").options == ["Heute", "Woche", "Monat", "Jahr"]
    assert [metric.label for metric in app.metric] == [
        "Marktkapitalisierung", "Größter Gewinner", "Größter Verlierer", "Aktive Positionen",
    ]
    assert app.dataframe[0].value.columns[1] == "Kursentwicklung (%)"
    assert len(app.get("plotly_chart")) == 1
    for period in ("Woche", "Monat", "Jahr"):
        app.radio(key="_indices.period").set_value(period).run()
        assert not app.exception
        assert app.subheader[0].value == f"DAX · {period}"
    assert source.reads == ["DAX"] and len(market.calls) == 1
    app.selectbox(key="_indices.selected").set_value("MDAX").run()
    assert not app.exception and source.reads == ["DAX", "MDAX"]
    app.radio(key=AREA_RADIO_KEY).set_value("Watchlist").run()
    assert not app.exception
    app.radio(key=AREA_RADIO_KEY).set_value("Indizes").run()
    assert not app.exception
    assert app.session_state[INDEX_KEY] == "MDAX" and app.session_state[PERIOD_KEY] == "Jahr"
    assert app.selectbox(key="_indices.selected").value == "MDAX"
    assert app.radio(key="_indices.period").value == "Jahr"
    assert source.reads == ["DAX", "MDAX"]
    app.button(key=REFRESH_BUTTON_KEY).click().run()
    assert not app.exception and source.reads == ["DAX", "MDAX", "MDAX"]
    assert len(market.calls) == 3


def test_error_empty_and_missing_data_remain_usable(dashboard):
    app, source, _ = dashboard
    source.fail = True
    app.run().radio(key=AREA_RADIO_KEY).set_value("Indizes").run()
    assert not app.exception and app.warning and not app.dataframe
    source.fail = False
    source.read = lambda name: ()
    app.button(key=REFRESH_BUTTON_KEY).click().run()
    assert not app.exception and not app.dataframe
    assert any("keine Unternehmen" in msg.value for msg in app.info)
    source.read = lambda name: (MEMBER,)
    app.button(key=REFRESH_BUTTON_KEY).click().run()
    assert not app.exception and len(app.dataframe[0].value) == 1
    assert app.metric[1].value == "NA" and app.metric[2].value == "NA"


def test_winner_loser_and_table_change_with_the_selected_period(dashboard):
    app, _, _ = dashboard
    quote = demo_watchlist_entries()[0]
    periods = tuple(replace(value, percent=Decimal(number))
                    for value, number in zip(quote.periods, (5, -3, 0, 20)))
    quote = replace(quote, periods=periods)
    row = IndexRow(MEMBER, quote, Decimal(1000), "EUR")
    bundle = IndexBundle("DAX", (row,), CLOSED)
    app.run().radio(key=AREA_RADIO_KEY).set_value("Indizes").run()
    loader = app.session_state[LOADER_KEY]
    loader._bundles["DAX"] = bundle
    app.run()
    assert app.metric[1].value == "+5,00 %" and app.metric[2].value == "NA"
    assert app.dataframe[0].value["Kursentwicklung (%)"].iloc[0] == 5
    app.radio(key="_indices.period").set_value("Woche").run()
    assert app.metric[1].value == "NA" and app.metric[2].value == "-3,00 %"
    assert app.dataframe[0].value["Kursentwicklung (%)"].iloc[0] == -3
    app.radio(key="_indices.period").set_value("Monat").run()
    assert app.metric[1].value == "NA" and app.metric[2].value == "NA"
    assert app.dataframe[0].value["Kursentwicklung (%)"].iloc[0] == 0
