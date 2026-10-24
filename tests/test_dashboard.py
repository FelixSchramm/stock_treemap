"""Exercise the actual dashboard's session lifecycle and offline boundary."""

import os
import socket
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_dashboard.demo import DEMO_TIMESTAMP, demo_snapshot
from portfolio_dashboard.models import CostBasisStatus, ValuationSource
from portfolio_dashboard.ui import (
    COURSE_PERIOD_KEY,
    COURSE_PERIODS,
    COURSE_VIEW,
    PERSONAL_VIEW,
    VIEW_KEY,
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
    assert app.radio(key=VIEW_KEY).value == COURSE_VIEW
    assert app.radio[1].options == list(COURSE_PERIODS)
    assert app.radio[1].value == "Heute"
    assert app.subheader[0].value == "Kursentwicklung · Heute"
    assert "Demo-Depot · 2 Beispielpositionen" in [item.value for item in app.caption]
    assert "Datenstand (synthetisch): 23.10.2026, 12:00 UTC" in [
        item.value for item in app.caption
    ]
    assert app.dataframe[0].value["Wert (EUR)"].tolist() == [1200.0, 800.0]


@pytest.mark.parametrize("period", COURSE_PERIODS)
def test_personal_view_hides_periods_and_retains_last_course_choice(offline_demo, period):
    app = AppTest.from_file(str(APP_PATH)).run()
    app.radio[1].set_value(period).run()
    assert not app.exception
    assert app.session_state[COURSE_PERIOD_KEY] == period
    assert app.subheader[0].value == f"Kursentwicklung · {period}"
    course_rows = app.dataframe[0].value.copy()

    app.radio(key=VIEW_KEY).set_value(PERSONAL_VIEW).run()
    # A second hidden rerun exercises Streamlit's widget-state cleanup.
    app.run()
    assert not app.exception
    assert len(app.radio) == 1
    assert app.subheader[0].value == "Meine Entwicklung · Seit Kauf"
    assert "Zeitraum: Seit Kauf" in [item.value for item in app.caption]
    assert "n. v." in app.info[1].value
    assert app.dataframe[0].value.equals(course_rows)

    app.radio(key=VIEW_KEY).set_value(COURSE_VIEW).run()
    assert not app.exception
    assert app.radio[1].value == period
    assert app.subheader[0].value == f"Kursentwicklung · {period}"
    assert app.dataframe[0].value.equals(course_rows)


def test_new_session_does_not_inherit_the_previous_view_or_period(offline_demo):
    previous = AppTest.from_file(str(APP_PATH)).run()
    previous.radio[1].set_value("Jahr").run()
    previous.radio(key=VIEW_KEY).set_value(PERSONAL_VIEW).run()

    fresh = AppTest.from_file(str(APP_PATH)).run()
    assert not fresh.exception
    assert fresh.radio(key=VIEW_KEY).value == COURSE_VIEW
    assert fresh.radio[1].value == "Heute"


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
