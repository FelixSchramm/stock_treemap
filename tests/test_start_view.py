"""Public start page with synthetic market data; offline AppTest, no secrets."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_dashboard import start_view
from portfolio_dashboard.market_overview import ASSETS, GROUPS, MarketQuote
from portfolio_dashboard.start_view import ASSETS_GROUP, build_market_figure

STAMP = datetime(2026, 10, 28, 22, 0, tzinfo=UTC)


def quotes_for(symbols, percent=Decimal("1.5")):
    closes = ((date(2026, 10, 27), Decimal(100)), (date(2026, 10, 28), Decimal(101)))
    return tuple(
        MarketQuote(item, {"Heute": percent if i else -percent, "Woche": None, "Monat": None,
                           "Jahr": Decimal(10)}, Decimal(101), date(2026, 10, 28), False, closes)
        for i, item in enumerate(symbols)
    )


def landing():
    import streamlit as st

    from portfolio_dashboard import start_view as view

    view.render(lambda: st.button("Login", key="auth.login", type="primary"))


@pytest.fixture
def requests(monkeypatch):
    seen = []

    def load(group):
        seen.append(group)
        return STAMP, quotes_for(ASSETS if group == ASSETS_GROUP else GROUPS[group])

    monkeypatch.setattr(start_view, "cached_quotes", load)
    return seen


def test_start_page_shows_login_clock_breadth_treemap_and_tiles(requests):
    app = AppTest.from_function(landing, default_timeout=30).run()
    assert not app.exception and not app.error
    assert app.header[0].value == "Märkte im Überblick"
    assert app.button(key="auth.login").label == "Login"
    labels = [metric.label for metric in app.metric]
    assert labels[:3] == ["Im Plus", "Stärkster Wert", "Schwächster Wert"]
    assert app.metric[0].value == "14 von 15"
    assert "DAX · 1 Jahr" in labels and "S&P 500 · 1 Jahr" in labels
    assert [metric.label for metric in app.metric[-4:]] == [item.name for item in ASSETS]
    assert len(app.get("plotly_chart")) == 1
    assert requests == ["Weltindizes", ASSETS_GROUP]


def test_sector_view_and_period_switch_reuse_the_shared_loader(requests):
    app = AppTest.from_function(landing, default_timeout=30).run()
    app.segmented_control(key=start_view.GROUP_KEY).set_value("Branchen Europa").run()
    assert not app.exception and app.metric[0].value == "18 von 19"
    app.segmented_control(key=start_view.PERIOD_KEY).set_value("Woche").run()
    assert not app.exception and app.metric[0].value == "0 von 19"
    assert app.metric[1].value == "NA"
    assert "Branchen Europa" in requests


def test_unavailable_market_data_keeps_login_and_shows_a_neutral_notice(monkeypatch):
    def broken(group):
        raise RuntimeError("synthetic-private-provider-error")

    monkeypatch.setattr(start_view, "cached_quotes", broken)
    app = AppTest.from_function(landing, default_timeout=30).run()
    assert not app.exception and app.button(key="auth.login")
    assert [info.value for info in app.info] == [start_view.NOT_AVAILABLE_TEXT]
    assert "synthetic-private-provider-error" not in str(app)


def test_world_treemap_groups_by_region_with_equal_tiles():
    quotes = quotes_for(GROUPS["Weltindizes"])
    trace = build_market_figure(quotes, "Heute").data[0]
    assert list(trace.ids[:3]) == ["Europa", "Amerika", "Asien"]
    assert list(trace.parents[:3]) == ["", "", ""] and set(trace.parents[3:]) == {"Europa", "Amerika", "Asien"}
    assert set(trace.values[3:]) == {1}
    assert trace.customdata[3][0] == "-1,50 %" and trace.customdata[4][0] == "+1,50 %"


def test_sector_treemap_is_flat():
    trace = build_market_figure(quotes_for(GROUPS["Branchen USA"]), "Jahr").data[0]
    assert set(trace.parents) == {""} and len(trace.ids) == 11
