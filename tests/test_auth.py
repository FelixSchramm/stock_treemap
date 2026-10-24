"""Synthetic OIDC claims and the real entry point's protected side effects.

AppTest does not implement a browser/OIDC callback or shared identity cookies.
Its st.user substitute is server-side test input, never an application API.
"""

import builtins
import os
import socket
from dataclasses import replace
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from streamlit.user_info import UserInfoProxy

import portfolio_dashboard.auth as auth
import portfolio_dashboard.demo as demo
import portfolio_dashboard.ui as ui
from portfolio_dashboard.config import LoginConfig

APP_PATH = Path(__file__).parents[1] / "app.py"
NOW = 2_000_000_000
LOGIN = LoginConfig(
    enabled=True, owner_issuer="https://accounts.google.com",
    owner_subject="synthetic-owner-subject", client_id="synthetic-client-id",
)


def owner_claims():
    return {
        "is_logged_in": True, "iss": LOGIN.owner_issuer, "sub": LOGIN.owner_subject,
        "aud": LOGIN.client_id, "iat": NOW - 60, "exp": NOW + 60,
    }


BAD_CLAIMS = [
    {}, {"is_logged_in": False}, {"is_logged_in": "true"},
    {"iss": "accounts.google.com"}, {"iss": "https://other.example.invalid"},
    {"sub": "synthetic-other-subject", "email": "owner@example.invalid", "email_verified": True},
    {"sub": None}, {"exp": NOW}, {"exp": NOW - 1}, {"exp": None},
    {"exp": "2000000060"}, {"exp": True}, {"exp": float("nan")},
    {"exp": float("inf")}, {"exp": 10**400}, {"iat": None},
    {"iat": "1999999940"}, {"iat": False}, {"iat": float("nan")},
    {"iat": -1}, {"iat": NOW + 1}, {"iat": 10**400},
    {"aud": None}, {"aud": "synthetic-other-client"}, {"aud": []},
    {"aud": [LOGIN.client_id, 1]}, {"aud": [LOGIN.client_id, "other"]},
    {"azp": "synthetic-other-client"},
    {"aud": [LOGIN.client_id, "other"], "azp": "synthetic-other-client"},
]


@pytest.mark.parametrize("changes", BAD_CLAIMS)
def test_owner_check_rejects_missing_foreign_expired_or_malformed_claims(changes):
    claims = {} if not changes else owner_claims() | changes
    assert not auth.owner_is_authorized(claims, LOGIN, now=NOW)


@pytest.mark.parametrize("missing", ["is_logged_in", "iss", "sub", "iat", "exp", "aud"])
def test_every_required_claim_is_mandatory(missing):
    claims = owner_claims()
    claims.pop(missing)
    assert not auth.owner_is_authorized(claims, LOGIN, now=NOW)


@pytest.mark.parametrize("login", [LoginConfig(), replace(LOGIN, enabled=False),
                                   replace(LOGIN, owner_issuer=None),
                                   replace(LOGIN, owner_subject=None), replace(LOGIN, client_id=None)])
def test_valid_identity_without_explicit_configuration_is_denied(login):
    assert not auth.owner_is_authorized(owner_claims(), login, now=NOW)


@pytest.mark.parametrize("audience", [LOGIN.client_id, [LOGIN.client_id], [LOGIN.client_id, "other"]])
def test_valid_owner_and_oidc_audience_are_accepted(audience):
    claims = owner_claims() | {"aud": audience, "azp": LOGIN.client_id}
    assert auth.owner_is_authorized(claims, LOGIN, now=NOW)


@pytest.fixture
def protected_app(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("PORTFOLIO_"):
            monkeypatch.delenv(name)
    values = {
        "PORTFOLIO_MODE": "production", "PORTFOLIO_OWNER_ISSUER": LOGIN.owner_issuer,
        "PORTFOLIO_OWNER_SUBJECT": LOGIN.owner_subject,
        "PORTFOLIO_SHEET_ID": "synthetic-source-id",
        "PORTFOLIO_SHEET_RANGE": "synthetic-source-range",
        "PORTFOLIO_SHEETS_CREDENTIALS_FILE": "/nonexistent/synthetic-credential.json",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(auth.time, "time", lambda: NOW)

    def unexpected(*args, **kwargs):
        raise AssertionError("Protected run attempted external access")

    # The fixture blocks any configured source or inherited platform credential
    # path without inspecting those files. pytest-socket also blocks IP sockets.
    blocked = {values["PORTFOLIO_SHEETS_CREDENTIALS_FILE"]}
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        blocked.add(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
    original_open, original_path_open = builtins.open, Path.open

    def guard_open(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and str(file) in blocked:
            unexpected()
        return original_open(file, *args, **kwargs)

    def guard_path_open(path, *args, **kwargs):
        if str(path) in blocked:
            unexpected()
        return original_path_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guard_open)
    monkeypatch.setattr(Path, "open", guard_path_open)
    monkeypatch.setattr(socket, "create_connection", unexpected)
    calls = []
    for module, name in ((demo, "demo_snapshot"), (demo, "demo_rows"), (ui, "render_dashboard")):
        original = getattr(module, name)

        def track(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, track)

    def make_app(claims):
        monkeypatch.setattr(st, "user", claims)
        app = AppTest.from_file(str(APP_PATH))
        app.secrets["auth"] = {
            "client_id": LOGIN.client_id, "client_secret": "synthetic-client-secret",
            "cookie_secret": "synthetic-cookie-secret",
            "redirect_uri": "https://dashboard.example.invalid/oauth2callback",
            "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
        }
        return app

    return make_app, calls


def assert_closed(app, calls):
    assert not app.exception
    assert app.error
    assert calls == []
    assert not app.title and not app.dataframe and not app.radio
    assert not app.get("download_button")


@pytest.mark.parametrize("changes", BAD_CLAIMS)
def test_actual_entry_point_denies_before_every_loader_and_renderer(protected_app, changes):
    make_app, calls = protected_app
    claims = {} if not changes else owner_claims() | changes
    app = make_app(claims).run()
    assert_closed(app, calls)


def test_owner_access_and_revocation_are_checked_on_each_run(protected_app, monkeypatch):
    make_app, calls = protected_app
    app = make_app(owner_claims()).run()
    assert not app.exception
    assert calls == ["demo_snapshot", "demo_rows", "render_dashboard"]
    assert app.dataframe[0].value["Instrument"].tolist() == ["Demo-Aktie A", "Demo-Fonds B"]
    calls.clear()
    monkeypatch.setenv("PORTFOLIO_OWNER_SUBJECT", "synthetic-revoked-owner")
    app.run()
    assert_closed(app, calls)


def test_native_streamlit_user_mapping_reaches_the_same_owner_gate(protected_app, monkeypatch):
    import streamlit.user_info

    make_app, calls = protected_app
    app = make_app(owner_claims())
    monkeypatch.setattr(streamlit.user_info, "_get_user_info", owner_claims)
    monkeypatch.setattr(st, "user", UserInfoProxy())
    app.run()
    assert not app.exception and app.dataframe
    assert calls == ["demo_snapshot", "demo_rows", "render_dashboard"]


@pytest.mark.parametrize("missing", ["PORTFOLIO_OWNER_SUBJECT", "PORTFOLIO_SHEET_ID", "auth"])
def test_valid_owner_cannot_bypass_incomplete_server_configuration(protected_app, monkeypatch, missing):
    make_app, calls = protected_app
    app = make_app(owner_claims())
    if missing == "auth":
        app.secrets["auth"] = {}
    else:
        monkeypatch.delenv(missing)
    assert_closed(app.run(), calls)


@pytest.mark.parametrize("fails", [False, True])
def test_logout_stops_before_loading_then_reopened_anonymous_app_is_closed(protected_app, monkeypatch, fails):
    make_app, calls = protected_app
    claims = owner_claims()
    app = make_app(claims).run()
    calls.clear()
    logouts = []

    def logout():
        logouts.append(True)
        if fails:
            raise RuntimeError("synthetic-private-provider-error")
        claims.clear()  # Model SDK session clearing, not a real browser cookie.

    monkeypatch.setattr(st, "logout", logout)
    app.button(key="auth.logout").click().run()
    assert not app.exception and logouts == [True] and calls == []
    assert not app.dataframe and not app.title
    assert "synthetic-private-provider-error" not in str(app)
    reopened = make_app({}).run()
    assert_closed(reopened, calls)


def test_independent_tabs_recheck_expiry_without_claiming_global_logout(protected_app, monkeypatch):
    make_app, calls = protected_app
    first_claims, second_claims = owner_claims(), owner_claims()
    first = make_app(first_claims).run()
    second = make_app(second_claims).run()
    assert first.dataframe and second.dataframe
    calls.clear()
    monkeypatch.setattr(st, "user", first_claims)
    monkeypatch.setattr(st, "logout", first_claims.clear)
    first.button(key="auth.logout").click().run()
    assert not calls and not first.dataframe
    monkeypatch.setattr(st, "user", second_claims)
    second.run()
    assert second.dataframe  # Existing SDK sessions are independent.
    assert "nicht automatisch abgemeldet" in second.caption[0].value
    calls.clear()
    monkeypatch.setattr(auth.time, "time", lambda: NOW + 60)
    second.run()
    assert_closed(second, calls)


@pytest.mark.parametrize("fails", [False, True])
def test_login_uses_supported_sdk_and_never_falls_through(protected_app, monkeypatch, fails):
    make_app, calls = protected_app
    logins = []

    def login():
        logins.append(True)
        if fails:
            raise RuntimeError("synthetic-private-provider-error")

    monkeypatch.setattr(st, "login", login)
    app = make_app({}).run()
    app.button(key="auth.login").click().run()
    assert logins == [True]
    assert_closed(app, calls)
    assert "synthetic-private-provider-error" not in str(app)


def test_demo_does_not_read_auth_secrets_or_user(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_MODE", "demo")

    class Forbidden:
        def get(self, *args, **kwargs):
            raise AssertionError("Demo consulted identity or secrets")

    monkeypatch.setattr(st, "secrets", Forbidden())
    monkeypatch.setattr(st, "user", Forbidden())
    app = AppTest.from_file(str(APP_PATH)).run()
    assert not app.exception and app.dataframe
