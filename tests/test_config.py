"""Configuration isolation, production refusal, and safe diagnostics."""

import os
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_dashboard.config import (
    ConfigurationError,
    Mode,
    Provider,
    SourceKind,
    load_config,
)
from portfolio_dashboard.fixtures import synthetic_import_cases


def production_settings() -> dict[str, str]:
    """Entirely fictitious values; configuration does not authenticate them."""
    return {
        "PORTFOLIO_MODE": "production",
        "PORTFOLIO_OWNER_ISSUER": "https://accounts.google.com",
        "PORTFOLIO_OWNER_SUBJECT": "synthetic-owner-subject",
        "PORTFOLIO_SHEET_ID": "synthetic-source-id",
        "PORTFOLIO_SHEET_RANGE": "synthetic-source-range",
    }


def production_auth() -> dict[str, str]:
    return {
        "client_id": "synthetic-client-id",
        "client_secret": "synthetic-client-secret",
        "cookie_secret": "synthetic-cookie-secret",
        "redirect_uri": "https://dashboard.example.invalid/oauth2callback",
        "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
    }


def set_app_environment(monkeypatch, settings: dict[str, str]) -> None:
    for name in tuple(os.environ):
        if name.startswith("PORTFOLIO_"):
            monkeypatch.delenv(name)
    for name, value in settings.items():
        monkeypatch.setenv(name, value)


def test_default_demo_discards_real_source_and_login_settings(monkeypatch):
    values = production_settings() | {
        "PORTFOLIO_MODE": "demo",
        "PORTFOLIO_SOURCE": "unsupported-source",
        "PORTFOLIO_PROVIDER": "unsupported-provider",
        "PORTFOLIO_LOGIN_PROVIDER": "unsupported-login",
    }
    set_app_environment(monkeypatch, values)
    config = load_config()
    assert config.mode is Mode.DEMO
    assert config.source.kind is SourceKind.SYNTHETIC
    assert config.provider is Provider.SYNTHETIC
    assert config.source.spreadsheet_id is None
    assert config.source.credentials_file is None
    assert not config.login.enabled
    assert config.login.owner_subject is None
    assert config.login.client_secret is None
    assert "synthetic-client-secret" not in repr(config)
    assert load_config({}) == config


def test_cache_defaults_and_overrides():
    defaults = load_config({}).ttls
    assert (defaults.quotes_seconds, defaults.holdings_seconds, defaults.history_seconds) == (60, 300, 21600)
    config = load_config({
        "PORTFOLIO_QUOTES_TTL_SECONDS": "15",
        "PORTFOLIO_HOLDINGS_TTL_SECONDS": "120",
        "PORTFOLIO_HISTORY_TTL_SECONDS": "7200",
    })
    assert (config.ttls.quotes_seconds, config.ttls.holdings_seconds, config.ttls.history_seconds) == (15, 120, 7200)


@pytest.mark.parametrize("invalid", ["0", "-1", "1.5", "NaN", "synthetic-secret-in-wrong-field"])
@pytest.mark.parametrize("field", ["PORTFOLIO_QUOTES_TTL_SECONDS", "PORTFOLIO_HOLDINGS_TTL_SECONDS", "PORTFOLIO_HISTORY_TTL_SECONDS"])
def test_invalid_ttls_are_rejected_without_values(field, invalid):
    with pytest.raises(ConfigurationError) as caught:
        load_config({field: invalid})
    assert field in str(caught.value)
    assert invalid not in str(caught.value)


@pytest.mark.parametrize("field", [key for key in production_settings() if key != "PORTFOLIO_MODE"])
def test_production_requires_explicit_nonplaceholder_settings(field):
    values = production_settings()
    values.pop(field)
    with pytest.raises(ConfigurationError) as caught:
        load_config(values, auth_settings=production_auth())
    assert field in str(caught.value)
    for value in values.values():
        assert value not in str(caught.value)
    values[field] = "<PLACEHOLDER>"
    with pytest.raises(ConfigurationError):
        load_config(values, auth_settings=production_auth())


def test_production_never_takes_a_key_file_path():
    # The Sheet key comes from the server-side [sheets_service_account] secret.
    values = production_settings() | {
        "PORTFOLIO_SHEETS_CREDENTIALS_FILE": "/nonexistent/synthetic-file.json",
    }
    config = load_config(values, auth_settings=production_auth())
    assert config.source.credentials_file is None
    assert config.source.listings_file is None


@pytest.mark.parametrize("field", ["PORTFOLIO_MODE", "PORTFOLIO_SOURCE", "PORTFOLIO_PROVIDER", "PORTFOLIO_LOGIN_PROVIDER", "PORTFOLIO_OWNER_ISSUER"])
def test_unsupported_settings_do_not_echo_values(field):
    values = production_settings() | {field: "synthetic-private-value"}
    with pytest.raises(ConfigurationError) as caught:
        load_config(values, auth_settings=production_auth())
    assert "synthetic-private-value" not in str(caught.value)
    assert field in str(caught.value)


@pytest.mark.parametrize("redirect", [
    "http://dashboard.example.invalid/oauth2callback", "https://[synthetic-private-value",
    "https://dashboard.example.invalid/", "https://user@dashboard.example.invalid/oauth2callback",
    "https://dashboard.example.invalid/oauth2callback?private=synthetic-private-value",
])
def test_production_requires_valid_https_callback_without_leaking_parse_errors(redirect):
    with pytest.raises(ConfigurationError) as caught:
        load_config(production_settings(), auth_settings=production_auth() | {"redirect_uri": redirect})
    assert "auth.redirect_uri" in str(caught.value)
    assert redirect not in str(caught.value)


def test_config_representations_hide_private_values():
    values = production_settings()
    config = load_config(values, auth_settings=production_auth())
    assert config.mode is Mode.PRODUCTION
    assert config.source.kind is SourceKind.GOOGLE_SHEETS
    assert config.provider is Provider.YFINANCE
    assert config.login.enabled
    for item in (config, config.source, config.login):
        for name, value in (values | production_auth()).items():
            if name != "PORTFOLIO_MODE":
                assert value not in repr(item)


@pytest.mark.parametrize("complete", [False, True])
def test_production_app_stays_closed_before_loading_any_rows(monkeypatch, complete):
    import socket

    import portfolio_dashboard.demo

    values = production_settings() if complete else {"PORTFOLIO_MODE": "production"}
    set_app_environment(monkeypatch, values)

    def unexpected(*args, **kwargs):
        raise AssertionError("Production must not load data or contact a provider")

    monkeypatch.setattr(portfolio_dashboard.demo, "demo_rows", unexpected)
    monkeypatch.setattr(portfolio_dashboard.demo, "demo_snapshot", unexpected)
    monkeypatch.setattr(socket, "create_connection", unexpected)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"))
    # Provide an in-memory secret store; never consult host secret files.
    app.secrets["auth"] = production_auth() if complete else {}
    app.run()
    assert not app.exception
    assert len(app.error) == 1
    assert not app.dataframe
    assert not app.title
    for name, value in values.items():
        if name != "PORTFOLIO_MODE":
            assert value not in app.error[0].value


@pytest.mark.parametrize("field", list(production_auth()))
@pytest.mark.parametrize("invalid", [None, "", "<PLACEHOLDER>", "CHANGE_ME", 42])
def test_server_auth_requires_all_fields_without_echoing_values(field, invalid):
    auth = production_auth() | {field: invalid}
    with pytest.raises(ConfigurationError) as caught:
        load_config(production_settings(), auth_settings=auth)
    assert field in str(caught.value)
    assert "synthetic-client-secret" not in str(caught.value)


@pytest.mark.parametrize("auth", [None, {}, {"client_kwargs": {"claims_options": {}}},
                                       production_auth() | {"expose_tokens": ["id"]},
                                       production_auth() | {"server_metadata_url": "https://other.example.invalid"}])
def test_missing_auth_or_sdk_overrides_fail_closed(auth):
    with pytest.raises(ConfigurationError):
        load_config(production_settings(), auth_settings=auth)


def test_legacy_environment_oidc_fields_cannot_replace_server_auth():
    values = production_settings() | {
        "PORTFOLIO_OIDC_CLIENT_ID": "synthetic-client-id",
        "PORTFOLIO_OIDC_CLIENT_SECRET": "synthetic-client-secret",
        "PORTFOLIO_COOKIE_SECRET": "synthetic-cookie-secret",
        "PORTFOLIO_OIDC_REDIRECT_URI": "https://dashboard.example.invalid/oauth2callback",
    }
    with pytest.raises(ConfigurationError, match="auth"):
        load_config(values)


def test_synthetic_fixture_cases_cover_import_boundaries_without_shared_mutation():
    rows = synthetic_import_cases()
    assert {row["currency"] for row in rows} >= {"EUR", "USD", "GBP"}
    assert {row["type"] for row in rows} >= {"stock", "etf", "derivative"}
    assert any(row["cost_eur"] is None for row in rows)
    assert any(row["cost_eur"] == "" for row in rows)
    assert any(Decimal(row["quantity"]) == 0 for row in rows)
    assert any(Decimal(row["quantity"]) < 0 for row in rows)
    assert any(row["value_eur"] is None for row in rows)
    assert all(row["symbol"].startswith("DEMO-") for row in rows)
    rows[0]["symbol"] = "changed"
    assert synthetic_import_cases()[0]["symbol"] == "DEMO-A"
