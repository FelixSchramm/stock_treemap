"""Local mode: loopback-only entry point and Sheet-only coordinator, synthetic only."""

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import portfolio_dashboard.local_source as local_source
from portfolio_dashboard.config import (
    ConfigurationError,
    LoginConfig,
    Mode,
    load_config,
)
from portfolio_dashboard.demo import demo_snapshot
from portfolio_dashboard.quote_cache import QuoteCache
from portfolio_dashboard.refresh import PortfolioRefreshCoordinator
from portfolio_dashboard.sheet_parser import SHEET_HEADERS
from portfolio_dashboard.sheets_session import SheetsCredentialError

APP_PATH = Path(__file__).parents[1] / "app.py"
LOCAL = {
    "PORTFOLIO_MODE": "local",
    "PORTFOLIO_SHEET_ID": "synthetic-source-id",
    "PORTFOLIO_SHEET_RANGE": "'Synthetic tab'!A1:M10",
    "PORTFOLIO_SHEETS_CREDENTIALS_FILE": "/nonexistent/synthetic-credential.json",
}
# Two shares at 50 EUR, 80 EUR remaining cost basis: 100 EUR value, +25 %.
ROW = [
    "Synthetische Aktie",
    "SYN",
    "Aktie",
    "2",
    "SYNX:SYN",
    "EUR",
    "50,00",
    "100,00",
    "80,00",
    "25",
    "20,00",
    "Synthetisch",
    "100",
]


class ForbiddenProvider:
    """Sheet-only mode must not request any quote, history or FX."""

    def __init__(self, listings):
        assert listings == ()

    def quote(self, *args, **kwargs):
        raise AssertionError("Sheet-only mode requested a provider quote")

    history = fx = quote


class SyntheticResponse:
    status_code = 200

    def __init__(self, rows):
        self.body = json.dumps({"majorDimension": "ROWS", "values": rows})

    def json(self, **kwargs):
        return json.loads(self.body, **kwargs)

    def close(self):
        pass


class SyntheticSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return SyntheticResponse([list(SHEET_HEADERS), ROW])


def test_local_config_reads_source_without_login_or_auth_secrets():
    config = load_config(LOCAL)
    assert config.mode is Mode.LOCAL
    assert config.source.spreadsheet_id == "synthetic-source-id"
    assert config.source.credentials_file == LOCAL["PORTFOLIO_SHEETS_CREDENTIALS_FILE"]
    assert config.login == LoginConfig()


@pytest.mark.parametrize(
    "field",
    [
        "PORTFOLIO_SHEET_ID",
        "PORTFOLIO_SHEET_RANGE",
        "PORTFOLIO_SHEETS_CREDENTIALS_FILE",
    ],
)
def test_local_config_requires_every_source_setting(field):
    with pytest.raises(ConfigurationError, match=field) as error:
        load_config(LOCAL | {field: "<PLACEHOLDER>"})
    assert "PLACEHOLDER" not in str(error.value)


def test_local_coordinator_values_the_sheet_with_verified_remaining_cost(monkeypatch):
    session = SyntheticSession()

    def readonly_session(path):
        assert path == LOCAL["PORTFOLIO_SHEETS_CREDENTIALS_FILE"]
        return session

    monkeypatch.setattr(local_source, "build_readonly_session", readonly_session)
    monkeypatch.setattr(local_source, "YFinanceProvider", ForbiddenProvider)
    bundle = local_source.build_local_coordinator(load_config(LOCAL)).load()

    assert bundle.available
    assert len(session.calls) == 1
    assert bundle.valuation.total_eur == Decimal("100")
    assert bundle.returns.total.eligible_cost_basis_eur == Decimal("80")
    assert bundle.returns.total.return_percent == Decimal("25")


@pytest.fixture
def local_app(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("PORTFOLIO_"):
            monkeypatch.delenv(name)
    for key, value in LOCAL.items():
        monkeypatch.setenv(key, value)
    calls = []

    def fake_coordinator(config):
        calls.append(config.mode)
        return PortfolioRefreshCoordinator(
            lambda attempted_at: demo_snapshot(), QuoteCache(ForbiddenProvider(()))
        )

    monkeypatch.setattr(local_source, "build_local_coordinator", fake_coordinator)
    original_get_option = st.get_option

    def make_app(address):
        monkeypatch.setattr(
            st,
            "get_option",
            lambda key: (
                address if key == "server.address" else original_get_option(key)
            ),
        )
        return AppTest.from_file(str(APP_PATH))

    return make_app, calls


@pytest.mark.parametrize("address", [None, "", "0.0.0.0", "192.0.2.10"])
def test_local_mode_refuses_non_loopback_address_before_reading_the_sheet(
    local_app, address
):
    make_app, calls = local_app
    app = make_app(address).run()
    assert not app.exception
    assert "127.0.0.1" in app.error[0].value
    assert calls == []
    assert not app.title and not app.dataframe


@pytest.mark.parametrize("address", ["127.0.0.1", "localhost", "::1"])
def test_local_mode_renders_own_data_without_demo_notices(local_app, address):
    make_app, calls = local_app
    app = make_app(address).run()
    assert not app.exception
    assert calls == [Mode.LOCAL]
    assert app.title[0].value == "Depot-Dashboard"
    assert not any("synthetische" in info.value for info in app.info)
    assert any(caption.value.startswith("Eigenes Depot") for caption in app.caption)
    assert app.dataframe


def test_local_mode_never_reads_hosted_secrets(local_app, monkeypatch):
    make_app, calls = local_app

    class Forbidden:
        def __getattr__(self, name):
            raise AssertionError("Local mode consulted st.secrets")

        def __getitem__(self, key):
            raise AssertionError("Local mode consulted st.secrets")

    monkeypatch.setattr(st, "secrets", Forbidden())
    app = make_app("127.0.0.1").run()
    assert not app.exception
    assert calls == [Mode.LOCAL]
    assert app.dataframe


def test_local_mode_builds_the_coordinator_once_per_session(local_app):
    make_app, calls = local_app
    app = make_app("127.0.0.1").run()
    app.run()
    assert not app.exception
    assert calls == [Mode.LOCAL]


def test_local_mode_shows_only_the_safe_credential_message(local_app, monkeypatch):
    make_app, _ = local_app

    def failing(config):
        raise SheetsCredentialError(
            "Service-Account-Schlüssel fehlt oder ist ungültig."
        )

    monkeypatch.setattr(local_source, "build_local_coordinator", failing)
    app = make_app("127.0.0.1").run()
    assert not app.exception
    assert app.error[0].value == "Service-Account-Schlüssel fehlt oder ist ungültig."
    assert not app.title
