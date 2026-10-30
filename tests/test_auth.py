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
import portfolio_dashboard.fundamentals_loader as fundamentals_loader
import portfolio_dashboard.local_source as local_source
import portfolio_dashboard.ui as ui
import portfolio_dashboard.watchlist_refresh as watchlist_refresh
from portfolio_dashboard.config import LoginConfig
from portfolio_dashboard.quote_cache import QuoteCache
from portfolio_dashboard.refresh import PortfolioRefreshCoordinator

APP_PATH = Path(__file__).parents[1] / "app.py"
real_production_coordinator = local_source.build_production_coordinator
real_production_watchlist = watchlist_refresh.build_production_watchlist_coordinator
real_production_fundamentals = fundamentals_loader.build_production_fundamentals_loader
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
    snapshot = demo.demo_snapshot

    class ForbiddenProvider:
        def latest_quote(self, *args, **kwargs):
            raise AssertionError("Synthetic positions without listings requested a quote")

        history = fx = latest_quote

    def own_coordinator(config, service_account, listing_entries):
        # Synthetic stand-in for the owner's Sheet; real builders stay unreached.
        calls.append("build_production_coordinator")
        return PortfolioRefreshCoordinator(
            lambda attempted_at: snapshot(), QuoteCache(ForbiddenProvider())
        )

    monkeypatch.setattr(local_source, "build_production_coordinator", own_coordinator)

    def own_watchlist(config, service_account, session=None):
        # The real builder returns None without a range and never opens a session then.
        calls.append("build_production_watchlist_coordinator")
        return real_production_watchlist(config, service_account, session)

    monkeypatch.setattr(
        watchlist_refresh, "build_production_watchlist_coordinator", own_watchlist
    )
    def own_fundamentals(*args, **kwargs):
        calls.append("build_production_fundamentals_loader")
        return real_production_fundamentals(*args, **kwargs)

    monkeypatch.setattr(
        fundamentals_loader, "build_production_fundamentals_loader", own_fundamentals
    )
    for module, name in (
        (demo, "demo_snapshot"),
        (demo, "demo_valuation"),
        (demo, "demo_rows"),
        (ui, "render_dashboard"),
    ):
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
        app.secrets["sheets_service_account"] = {"type": "service_account", "synthetic": True}
        app.secrets["listing"] = []
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
    assert calls == [
        "build_production_coordinator",
        "build_production_fundamentals_loader",
        "render_dashboard",
    ]
    assert app.dataframe
    assert any(caption.value.startswith("Eigenes Depot") for caption in app.caption)
    assert not any("synthetische" in info.value for info in app.info)
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
    assert calls == [
        "build_production_coordinator",
        "build_production_fundamentals_loader",
        "render_dashboard",
    ]


def test_owner_session_builds_own_data_once_from_the_secret_sections(protected_app, monkeypatch):
    make_app, calls = protected_app
    seen = []

    def record(config, service_account, listing_entries):
        seen.append((dict(service_account), list(listing_entries)))
        return own(config, service_account, listing_entries)

    own = local_source.build_production_coordinator
    monkeypatch.setattr(local_source, "build_production_coordinator", record)
    app = make_app(owner_claims())
    app.secrets["listing"] = [{"isin": "DE000SYN0001"}]
    app.run()
    app.run()
    assert not app.exception and app.dataframe
    assert calls == [
        "build_production_coordinator",
        "build_production_fundamentals_loader",
        "render_dashboard",
        "render_dashboard",
    ]
    assert seen == [({"type": "service_account", "synthetic": True}, [{"isin": "DE000SYN0001"}])]


@pytest.mark.parametrize("section", [None, {"type": "authorized_user", "secret": "synthetic-secret"}])
def test_missing_or_invalid_sheet_key_secret_shows_only_the_safe_message(protected_app, monkeypatch, section):
    make_app, calls = protected_app
    monkeypatch.setattr(
        local_source, "build_production_coordinator", real_production_coordinator
    )
    app = make_app(owner_claims())
    if section is None:
        del app.secrets["sheets_service_account"]
    else:
        app.secrets["sheets_service_account"] = section
    app.run()
    assert not app.exception
    # str(app) would include AppTest's own secret store; check the rendered output.
    assert [error.value for error in app.error] == [
        "Service-Account-Schlüssel fehlt oder ist ungültig."
    ]
    assert calls == [] and not app.dataframe and not app.title


def test_invalid_listing_secret_shows_only_the_safe_message(protected_app, monkeypatch):
    make_app, calls = protected_app
    monkeypatch.setattr(
        local_source, "build_production_coordinator", real_production_coordinator
    )
    app = make_app(owner_claims())
    app.secrets["listing"] = [{"isin": "DE000SYN0001", "symbol": "SYN.DE"}]
    app.run()
    assert not app.exception
    assert "Eintrag 1" in app.error[0].value
    assert "SYN" not in app.error[0].value
    assert calls == [] and not app.dataframe


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


def test_watchlist_loader_is_built_only_for_the_owner_and_only_when_opened(protected_app, monkeypatch):
    make_app, calls = protected_app
    app = make_app(owner_claims()).run()
    assert not app.exception
    assert "build_production_watchlist_coordinator" not in calls  # Depot view only
    app.radio(key=ui.AREA_RADIO_KEY).set_value("Watchlist").run()
    app.run()
    assert not app.exception and not app.error
    # No PORTFOLIO_WATCHLIST_RANGE: a configuration notice, no credential access.
    assert [info.value for info in app.info] == [ui.WATCHLIST_NOT_CONFIGURED_TEXT]
    assert calls.count("build_production_watchlist_coordinator") == 1
    calls.clear()
    monkeypatch.setenv("PORTFOLIO_OWNER_SUBJECT", "synthetic-revoked-owner")
    app.run()
    assert_closed(app, calls)  # revoked owner: no builder, no renderer, no Watchlist


def test_valuation_area_is_owner_only_and_opens_without_any_loading(protected_app, monkeypatch):
    make_app, calls = protected_app
    app = make_app(owner_claims()).run()
    assert app.radio(key=ui.AREA_RADIO_KEY).options == ["Depot", "Watchlist", "Innerer Wert"]
    calls.clear()
    app.radio(key=ui.AREA_RADIO_KEY).set_value("Innerer Wert").run()
    assert not app.exception and not app.error
    # No Watchlist range and no fundamentals mapping: nothing is built or fetched.
    assert "build_production_watchlist_coordinator" not in calls
    assert [s.value for s in app.subheader][:1] == ["Innerer Wert"]
    monkeypatch.setenv("PORTFOLIO_OWNER_SUBJECT", "synthetic-revoked-owner")
    calls.clear()
    app.run()
    assert_closed(app, calls)  # revoked owner: no area, no table, no private figure


def test_detail_view_state_opens_nothing_for_a_revoked_owner(protected_app, monkeypatch):
    make_app, calls = protected_app
    app = make_app(owner_claims()).run()
    app.radio(key=ui.AREA_RADIO_KEY).set_value("Innerer Wert").run()
    # A pre-set request and an open detail view must not outlive the owner check.
    app.session_state["valuation.detail_key"] = "synthetic-instrument"
    app.session_state["valuation.detail_current"] = "synthetic-instrument"
    monkeypatch.setenv("PORTFOLIO_OWNER_SUBJECT", "synthetic-revoked-owner")
    calls.clear()
    app.run()
    assert_closed(app, calls)
    assert not app.subheader and not app.text_input and not app.table


def test_production_watchlist_uses_the_service_account_secret_after_the_gate(protected_app, monkeypatch):
    from datetime import UTC, datetime

    from portfolio_dashboard.sheet_parser import NumberLocale
    from portfolio_dashboard.watchlist_quotes import WatchlistBundle, sheet_entry
    from portfolio_dashboard.watchlist_sheet import BASIC_HEADERS, parse_watchlist_rows

    make_app, calls = protected_app
    monkeypatch.setenv("PORTFOLIO_WATCHLIST_RANGE", "Synthetic!A1:J400")
    now = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
    snapshot = parse_watchlist_rows(
        BASIC_HEADERS, [["Synthetik AG", "12,50", "1,5", "0,18"]],
        imported_at=now, number_locale=NumberLocale.GERMAN,
    ).snapshot
    bundle = WatchlistBundle(True, snapshot, tuple(sheet_entry(i) for i in snapshot.items), now)
    seen = []

    class Loader:
        def load(self, *, refresh=False):
            return bundle

    def build(config, service_account, session=None):
        seen.append((config.source.watchlist_range, dict(service_account)))
        return Loader()

    monkeypatch.setattr(watchlist_refresh, "build_production_watchlist_coordinator", build)
    app = make_app(owner_claims()).run()
    app.radio(key=ui.AREA_RADIO_KEY).set_value("Watchlist").run()
    assert not app.exception and not app.error
    assert app.dataframe[0].value["Name"].tolist() == ["Synthetik AG"]
    assert len(seen) == 1 and seen[0][1] == {"type": "service_account", "synthetic": True}
    assert seen[0][0] is not None
    app.run()
    assert len(seen) == 1  # once per session
