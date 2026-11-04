"""Synthetic end-to-end coverage of every chart and the app navigation."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from portfolio_dashboard.exploration.charts import (
    GROUPS,
    AnalysisUnavailable,
    build,
    cash,
    pe_series,
)
from portfolio_dashboard.exploration.data import prices
from portfolio_dashboard.exploration.demo import demo_bundle
from portfolio_dashboard.exploration.view import AREA, GROUP_KEY, TOPIC_KEY
from portfolio_dashboard.ui import AREA_RADIO_KEY


@pytest.fixture
def bundle():
    return demo_bundle()


@pytest.mark.parametrize("number", range(1, 30))
def test_all_topics_render_real_plotly_traces_from_synthetic_data(bundle, number):
    if number == 28:
        with pytest.raises(AnalysisUnavailable, match="Noch nicht angebunden"):
            build(number, bundle)
        return
    analysis = build(number, bundle)
    assert analysis.panels and analysis.note
    for panel in analysis.panels:
        assert not panel.data.empty
        if panel.figure is not None:
            assert len(panel.figure.data) > 0
            panel.figure.to_json()


@pytest.mark.parametrize("currency", ["EUR", "USD"])
@pytest.mark.parametrize("total", [False, True])
def test_comparison_choices_have_identical_rebased_start(bundle, currency, total):
    frame = build(5, bundle, currency, total, 36).panels[0].data
    np.testing.assert_allclose(frame.iloc[0], 100)
    assert len(frame) > 500


def test_fx_is_directional_and_never_uses_future_or_stale_rates():
    days = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-10"])
    raw = {
        "MSFT": pd.DataFrame({"Close": [100, 100, 100]}, index=days),
        "EURUSD=X": pd.DataFrame({"Close": [2]}, index=days[1:2]),
    }
    actual = prices(
        {"prices": raw, "currencies": {"MSFT": "USD"}}, ("MSFT",), "EUR", False
    ).MSFT
    assert np.isnan(actual.iloc[0])
    assert actual.iloc[1] == 50
    assert np.isnan(actual.iloc[2])
    # A native EUR series does not depend on FX being available.
    raw = {"SAP.DE": pd.DataFrame({"Close": [100, 110, 120]}, index=days)}
    assert (
        prices(
            {"prices": raw, "currencies": {"SAP.DE": "EUR"}}, ("SAP.DE",), "EUR", False
        ).iloc[-1, 0]
        == 120
    )


def test_fcf_mismatch_is_rejected(bundle):
    bundle["cash"].loc["Free Cash Flow"] *= 2
    with pytest.raises(ValueError, match="Anbieter-FCF"):
        cash(bundle)


def test_financial_currency_and_reverse_dcf_snapshot_are_checked(bundle):
    bundle["info"]["financialCurrency"] = None
    with pytest.raises(ValueError, match="Berichtswährung"):
        build(9, bundle)
    bundle["info"]["financialCurrency"] = "USD"
    bundle["info"]["marketCap"] *= 2
    with pytest.raises(ValueError, match="5 %"):
        build(27, bundle)


def test_pe_only_uses_known_consecutive_earnings_and_invalidates_gaps():
    eps = pd.to_datetime(
        ["2025-01-15", "2025-04-15", "2025-07-15", "2025-10-15", "2026-04-15"]
    )
    dates = pd.date_range("2025-10-15", "2026-04-17")
    bundle = {
        "symbol": "MSFT",
        "earnings": pd.DataFrame({"Reported EPS": [1] * 5}, index=eps),
        "prices": {"MSFT": pd.DataFrame({"Close": 100}, index=dates)},
    }
    result = pe_series(bundle)
    assert np.isnan(result.iloc[0])
    assert result.iloc[1] == 25
    assert np.isnan(result.loc["2026-04-16"])


def test_missing_optional_resource_does_not_affect_other_topics(bundle):
    del bundle["options"]
    assert build(9, bundle).panels
    with pytest.raises(KeyError):
        build(19, bundle)


def test_ui_all_topics_offline_and_return_to_depot(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_MODE", "demo")

    def forbidden():
        raise AssertionError("Demo attempted a provider fetch")

    monkeypatch.setattr("portfolio_dashboard.exploration.view.fetch", forbidden)
    app = AppTest.from_file(
        str(Path(__file__).parents[1] / "app.py"), default_timeout=15
    ).run()
    app.radio(key=AREA_RADIO_KEY).set_value(AREA).run()
    assert not app.exception
    assert app.radio(key=GROUP_KEY).value == "Überblick"
    assert app.selectbox(key=f"{TOPIC_KEY}.Überblick").value == 1
    assert app.get("plotly_chart")
    for group, topics in GROUPS.items():
        app.radio(key=GROUP_KEY).set_value(group).run()
        for topic in topics:
            app.selectbox(key=f"{TOPIC_KEY}.{group}").set_value(topic).run()
            assert not app.exception, (group, topic)
            assert [e.label for e in app.expander][-3:] == [
                "Glossar – Kennzahlen", "Glossar – Diagramme", "Glossar – Datenquellen"
            ]
            if topic in {11, 12, 17, 28}:
                assert app.warning, (group, topic)
                assert any(
                    "nicht angebunden" in w.value or "nicht bestätigt" in w.value
                    for w in app.warning
                )
            else:
                assert not app.warning, (group, topic)
    app.radio(key=AREA_RADIO_KEY).set_value("Depot").run()
    assert not app.exception
    assert app.dataframe[0].value["Instrument"].tolist() == [
        "Demo-Aktie A",
        "Demo-Fonds B",
    ]


def test_failed_live_refresh_preserves_old_snapshot(monkeypatch):
    from portfolio_dashboard.exploration import view

    monkeypatch.setattr(view, "fetch", lambda *args: {"prices": {}, "errors": {}})

    def wrapper():
        import streamlit as st

        from portfolio_dashboard.exploration import view
        from portfolio_dashboard.exploration.demo import demo_bundle
        from portfolio_dashboard.exploration.selection import Catalog, demo_catalog

        company = demo_catalog().companies[0]
        st.session_state[view.CATALOG_KEY + ".live"] = Catalog(depot=(company,))
        st.session_state[view.STATE + ".live"] = {(company, ()): demo_bundle(company)}
        view.render(
            demo=False, refresh_clicked=True, load_companies=lambda previous: previous
        )

    app = AppTest.from_function(wrapper).run()
    assert not app.exception
    assert any("vorhandener Stand" in w.value for w in app.warning)
    held = app.session_state[view.STATE + ".live"]
    assert len(held) == 1
    assert next(iter(held.values()))["retrieved_at"] == demo_bundle()["retrieved_at"]
