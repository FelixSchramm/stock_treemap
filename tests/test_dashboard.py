"""Exercise the actual dashboard's session lifecycle and offline boundary."""

import json
import os
import socket
from pathlib import Path

import pytest
from streamlit.proto.Metric_pb2 import Metric as MetricProto
from streamlit.testing.v1 import AppTest

from portfolio_dashboard.demo import DEMO_TIMESTAMP, demo_snapshot
from portfolio_dashboard.models import CostBasisStatus, ValuationSource
from portfolio_dashboard.treemap import WEIGHT_COLUMN_LABEL
from portfolio_dashboard.ui import (
    COURSE_PERIOD_KEY,
    COURSE_PERIODS,
    FILTER_CATEGORY_KEY,
    FILTER_QUERY_KEY,
    FILTER_TYPE_KEY,
    HIDE_MISSING_COURSE_KEY,
    PERIOD_RADIO_KEY,
    SORT_DIRECTION_ASC,
    SORT_FIELD_KEY,
    SORT_ORDER_KEY,
)

APP_PATH = Path(__file__).parents[1] / "app.py"


@pytest.fixture
def offline_demo(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("PORTFOLIO_"):
            monkeypatch.delenv(name)
    # Even inherited production source settings must not reach the demo.
    monkeypatch.setenv("PORTFOLIO_SHEETS_CREDENTIALS_FILE", "/nonexistent/demo-source.json")
    monkeypatch.setenv("PORTFOLIO_SHEET_ID", "synthetic-source-id")
    monkeypatch.setenv("PORTFOLIO_PROVIDER", "unsupported-provider")

    def no_network(*args, **kwargs):
        raise AssertionError("Dashboard demo attempted network access")

    original_open = Path.open

    def guard_source_open(path, *args, **kwargs):
        if str(path) == "/nonexistent/demo-source.json":
            raise AssertionError("Dashboard demo attempted a credential-file read")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(Path, "open", guard_source_open)


def test_new_dashboard_starts_with_course_today_and_fixed_demo_timestamp(offline_demo):
    app = AppTest.from_file(str(APP_PATH)).run()
    assert not app.exception
    assert app.radio(key=PERIOD_RADIO_KEY).label == "Zeitraum"
    assert app.radio(key=PERIOD_RADIO_KEY).options == [*COURSE_PERIODS, "Seit Kauf"]
    assert app.radio(key=PERIOD_RADIO_KEY).value == "Heute"
    assert app.subheader[0].value == "Kursentwicklung · Heute"
    # The percentage is the value; the unique ticker sits below as a gray,
    # arrowless note, and the full name is the metric's help text.
    winner, loser = app.metric[2], app.metric[3]
    assert (winner.value, winner.delta, winner.help) == ("+1,25 %", "DEMO-A", "Demo-Aktie A")
    assert (loser.value, loser.delta, loser.help) == ("-0,80 %", "DEMO-B", "Demo-Fonds B")
    for metric in (winner, loser):
        assert metric.proto.direction == MetricProto.MetricDirection.NONE
        assert metric.proto.color == MetricProto.MetricColor.GRAY
    assert "Demo-Depot · 2 Beispielpositionen" in [item.value for item in app.caption]
    assert "Datenstand (synthetisch): 23.10.2026, 12:00 UTC" in [
        item.value for item in app.caption
    ]
    assert app.dataframe[0].value["Wert (EUR)"].tolist() == [1200.0, 800.0]


@pytest.mark.parametrize("period", COURSE_PERIODS)
def test_since_purchase_period_switches_to_personal_view_and_back(offline_demo, period):
    app = AppTest.from_file(str(APP_PATH)).run()
    app.radio(key=PERIOD_RADIO_KEY).set_value(period).run()
    assert not app.exception
    assert app.session_state[COURSE_PERIOD_KEY] == period
    assert app.subheader[0].value == f"Kursentwicklung · {period}"
    course_rows = app.dataframe[0].value.copy()

    app.radio(key=PERIOD_RADIO_KEY).set_value("Seit Kauf").run()
    # A second hidden rerun exercises Streamlit's widget-state cleanup.
    app.run()
    assert not app.exception
    assert app.subheader[0].value == "Meine Entwicklung · Seit Kauf"
    assert "n. v." in app.info[1].value
    personal_df = app.dataframe[0].value
    invariant_cols = ["Instrument", "Wert (EUR)", WEIGHT_COLUMN_LABEL]
    assert personal_df[invariant_cols].equals(course_rows[invariant_cols])
    assert personal_df["Entwicklung (%)"].tolist() == ["n. v.", "n. v."]

    app.radio(key=PERIOD_RADIO_KEY).set_value(period).run()
    assert not app.exception
    assert app.radio(key=PERIOD_RADIO_KEY).value == period
    assert app.subheader[0].value == f"Kursentwicklung · {period}"
    assert app.dataframe[0].value.equals(course_rows)


def test_new_session_does_not_inherit_the_previous_view_or_period(offline_demo):
    previous = AppTest.from_file(str(APP_PATH)).run()
    previous.radio(key=PERIOD_RADIO_KEY).set_value("Jahr").run()
    previous.radio(key=PERIOD_RADIO_KEY).set_value("Seit Kauf").run()

    fresh = AppTest.from_file(str(APP_PATH)).run()
    assert not fresh.exception
    assert fresh.subheader[0].value == "Kursentwicklung · Heute"
    assert fresh.radio(key=PERIOD_RADIO_KEY).value == "Heute"


def test_demo_snapshot_cannot_claim_verified_cost_or_provider_quotes():
    snapshot = demo_snapshot()
    assert snapshot.attempted_at == DEMO_TIMESTAMP
    assert [position.name for position in snapshot.positions] == [
        "Demo-Aktie A", "Demo-Fonds B"
    ]
    assert all(position.imported_at == DEMO_TIMESTAMP for position in snapshot.positions)
    assert all(
        position.valuation.provenance.source is ValuationSource.SHEET
        and position.valuation.provenance.provider is None
        for position in snapshot.positions
    )
    assert all(
        position.cost_basis.status is CostBasisStatus.MISSING
        and not position.cost_basis.usable_for_since_purchase
        for position in snapshot.positions
    )


def test_shared_filters_apply_to_treemap_and_table_while_kpis_remain_portfolio_wide(
    offline_demo,
):
    app = AppTest.from_file(str(APP_PATH)).run()
    assert not app.exception
    assert app.metric[0].label == "Bewerteter Depotwert"
    assert app.metric[0].value == "2.000,00 EUR"
    assert app.metric[1].value == "2"

    # Filter by instrument type "ETF" -> only Demo-Fonds B (800 EUR, 40% of 2000 EUR).
    app.selectbox(key=FILTER_TYPE_KEY).set_value("ETF").run()
    assert not app.exception
    # Header KPIs remain portfolio-wide.
    assert app.metric[0].value == "2.000,00 EUR"
    assert app.metric[1].value == "2"

    df = app.dataframe[0].value
    assert df["Instrument"].tolist() == ["Demo-Fonds B"]
    assert df["Wert (EUR)"].tolist() == [800.0]
    # Weight remains 40% of the total valued portfolio (2000 EUR), not 100% of filtered!
    assert df[WEIGHT_COLUMN_LABEL].tolist() == [40.0]

    # Search filter narrows by query across name/ticker/category.
    app.selectbox(key=FILTER_TYPE_KEY).set_value("Alle").run()
    app.text_input(key=FILTER_QUERY_KEY).set_value("demo-a").run()
    assert not app.exception
    df_search = app.dataframe[0].value
    assert df_search["Instrument"].tolist() == ["Demo-Aktie A"]
    assert df_search[WEIGHT_COLUMN_LABEL].tolist() == [60.0]


def test_table_sorting_controls_reorder_rows_deterministically(offline_demo):
    app = AppTest.from_file(str(APP_PATH)).run()
    assert not app.exception
    assert app.dataframe[0].value["Instrument"].tolist() == [
        "Demo-Aktie A",
        "Demo-Fonds B",
    ]

    app.selectbox(key=SORT_FIELD_KEY).set_value("Wert (EUR)").run()
    app.selectbox(key=SORT_ORDER_KEY).set_value(SORT_DIRECTION_ASC).run()
    assert not app.exception
    assert app.dataframe[0].value["Instrument"].tolist() == [
        "Demo-Fonds B",
        "Demo-Aktie A",
    ]

    app.selectbox(key=FILTER_CATEGORY_KEY).set_value("Demo").run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 2


def _course_coverage_app():
    from decimal import Decimal

    from portfolio_dashboard.demo import demo_snapshot
    from portfolio_dashboard.treemap import Performance
    from portfolio_dashboard.ui import render_dashboard
    from portfolio_dashboard.valuations import value_portfolio

    snapshot = demo_snapshot()
    valuation = value_portfolio(snapshot, ())
    performances = {
        "Heute": (Performance(Decimal("1")), Performance(None)),
        "Woche": (Performance(None), Performance(Decimal("-1"))),
        "Monat": (Performance(None), Performance(None)),
        "Jahr": (Performance(Decimal("0")), Performance(None)),
        "Seit Kauf": (Performance(None), Performance(None)),
    }
    render_dashboard(snapshot, None, valuation, lambda view, period: performances[period])


def _chart_trace(app):
    return json.loads(app.get("plotly_chart")[0].proto.spec)["data"][0]


def test_course_toggle_hides_only_selected_period_tiles_and_keeps_complete_data():
    app = AppTest.from_function(_course_coverage_app).run()
    assert not app.exception
    assert app.toggle[0].label == "Positionen ohne Kursentwicklung ausblenden"
    assert app.toggle[0].value is True
    assert _chart_trace(app)["values"] == [1200.0]
    assert "1 Positionen (800,00 EUR) ohne Kursentwicklung ausgeblendet" in [
        item.value for item in app.caption
    ]
    initial_metrics = [(metric.label, metric.value, metric.delta) for metric in app.metric]
    initial_rows = app.dataframe[0].value.copy()
    initial_details = [item.value for item in app.markdown]
    assert len(initial_rows) == 2
    assert initial_rows[WEIGHT_COLUMN_LABEL].tolist() == [60.0, 40.0]

    app.toggle[0].set_value(False).run()
    assert not app.exception
    assert _chart_trace(app)["values"] == [1200.0, 800.0]
    assert [(m.label, m.value, m.delta) for m in app.metric] == initial_metrics
    assert app.dataframe[0].value.equals(initial_rows)
    assert [item.value for item in app.markdown] == initial_details
    assert not any("ohne Kursentwicklung ausgeblendet" in c.value for c in app.caption)

    app.toggle[0].set_value(True).run()
    app.radio(key=PERIOD_RADIO_KEY).set_value("Woche").run()
    assert not app.exception
    assert _chart_trace(app)["values"] == [800.0]
    assert "1 Positionen (1.200,00 EUR) ohne Kursentwicklung ausgeblendet" in [
        item.value for item in app.caption
    ]
    assert app.metric[0].value == "2.000,00 EUR"
    assert app.dataframe[0].value[WEIGHT_COLUMN_LABEL].tolist() == [60.0, 40.0]

    # All course tiles can disappear without losing positions, KPIs or status.
    app.radio(key=PERIOD_RADIO_KEY).set_value("Monat").run()
    assert not app.exception
    assert len(app.get("plotly_chart")) == 0
    assert "2 Positionen (2.000,00 EUR) ohne Kursentwicklung ausgeblendet" in [
        item.value for item in app.caption
    ]
    assert len(app.dataframe[0].value) == 2
    assert app.metric[0].value == "2.000,00 EUR"
    assert any("Datenstatus & Hinweise (2)" == e.label for e in app.expander)

    # A measured zero return is available and must remain visible.
    app.radio(key=PERIOD_RADIO_KEY).set_value("Jahr").run()
    assert not app.exception
    assert _chart_trace(app)["values"] == [1200.0]


def test_course_toggle_is_inactive_since_purchase_and_retains_its_session_preference():
    app = AppTest.from_function(_course_coverage_app).run()
    app.toggle[0].set_value(False).run()
    app.radio(key=PERIOD_RADIO_KEY).set_value("Seit Kauf").run()
    app.run()  # Exercise widget cleanup while the course toggle is absent.
    assert not app.exception
    assert len(app.toggle) == 0
    assert _chart_trace(app)["values"] == [1200.0, 800.0]
    assert not any("ohne Kursentwicklung ausgeblendet" in c.value for c in app.caption)
    app.radio(key=PERIOD_RADIO_KEY).set_value("Heute").run()
    assert not app.exception
    assert app.toggle[0].value is False
    assert app.session_state[HIDE_MISSING_COURSE_KEY] is False
    assert _chart_trace(app)["values"] == [1200.0, 800.0]

    app.toggle[0].set_value(True).run()
    app.radio(key=PERIOD_RADIO_KEY).set_value("Seit Kauf").run()
    assert _chart_trace(app)["values"] == [1200.0, 800.0]
    fresh = AppTest.from_function(_course_coverage_app).run()
    assert fresh.toggle[0].value is True

