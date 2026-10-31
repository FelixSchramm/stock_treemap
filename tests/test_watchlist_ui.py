"""Watchlist area of the dashboard (#85): navigation, filters, labels; offline AppTests.

Entirely synthetic. The demo and local entry points run through ``app.py``; local
mode gets a fake depot coordinator and, where stated, the real Watchlist
coordinator on a fake Sheet reader. IP sockets are blocked by pytest-socket.
"""

import os
import socket
from datetime import date
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from test_watchlist_view import STAMP, entries

import portfolio_dashboard.local_source as local_source
import portfolio_dashboard.ui as ui
import portfolio_dashboard.watchlist_refresh as watchlist_refresh
from portfolio_dashboard.demo import demo_snapshot
from portfolio_dashboard.local_source import venue_calendars
from portfolio_dashboard.models import DataWarning, SnapshotStatus
from portfolio_dashboard.quote_cache import QuoteCache
from portfolio_dashboard.refresh import PortfolioRefreshCoordinator
from portfolio_dashboard.sheet_parser import NumberLocale
from portfolio_dashboard.ui import (
    AREA_RADIO_KEY,
    COURSE_PERIOD_KEY,
    FILTER_TYPE_KEY,
    PERIOD_RADIO_KEY,
    WATCHLIST_BUNDLE_KEY,
    WATCHLIST_FILTER_CATEGORY_KEY,
    WATCHLIST_FILTER_QUERY_KEY,
    WATCHLIST_FILTER_TYPE_KEY,
    WATCHLIST_NOT_CONFIGURED_TEXT,
    WATCHLIST_SINCE_PURCHASE_TEXT,
    WATCHLIST_SORT_FIELD_KEY,
    WATCHLIST_SORT_ORDER_KEY,
    _markdown_text,
)
from portfolio_dashboard.watchlist_quotes import WatchlistBundle, empty_bundle
from portfolio_dashboard.watchlist_refresh import WatchlistRefreshCoordinator
from portfolio_dashboard.watchlist_sheet import (
    BASIC_HEADERS,
    OPTIONAL_HEADERS,
    SHEET_VALUE_LABEL,
    WatchlistSnapshot,
    parse_watchlist_rows,
)

APP_PATH = Path(__file__).parents[1] / "app.py"
LOCAL = {
    "PORTFOLIO_MODE": "local",
    "PORTFOLIO_SHEET_ID": "synthetic-source-id",
    "PORTFOLIO_SHEET_RANGE": "'Synthetic tab'!A1:M10",
    "PORTFOLIO_SHEETS_CREDENTIALS_FILE": "/nonexistent/synthetic-credential.json",
}
RANGE = {"PORTFOLIO_WATCHLIST_RANGE": "Synthetic!A1:J400"}
WIDE = (*BASIC_HEADERS, *OPTIONAL_HEADERS)


def new_app():
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def area(app, value):
    app.radio(key=AREA_RADIO_KEY).set_value(value)
    return app.run()


def table(app):
    return app.dataframe[0].value


def column(app, name):
    return table(app)[name].tolist()


def expander_labels(app):
    return [expander.label for expander in app.expander]


def details_text(app):
    return next(m.value for m in app.markdown if m.value.startswith("**"))


@pytest.fixture
def offline_demo(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("PORTFOLIO_"):
            monkeypatch.delenv(name)
    # The demo drops inherited Watchlist settings like every other source setting.
    monkeypatch.setenv("PORTFOLIO_WATCHLIST_RANGE", "Synthetic!A1:J400")

    def no_network(*args, **kwargs):
        raise AssertionError("Dashboard demo attempted network access")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)


class FakeLoader:
    """Same ``load`` interface as the coordinator; records every call."""

    def __init__(self, bundle):
        self.bundle = bundle
        self.calls = []

    def load(self, *, refresh=False):
        self.calls.append(refresh)
        return self.bundle


class ForbiddenProvider:
    def __getattr__(self, name):
        raise AssertionError("Depot stand-in requested market data")


@pytest.fixture
def local(monkeypatch):
    """Local entry point with a fake depot and a recording Watchlist builder."""
    for name in tuple(os.environ):
        if name.startswith("PORTFOLIO_"):
            monkeypatch.delenv(name)
    for key, value in LOCAL.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        local_source,
        "build_local_coordinator",
        lambda config: PortfolioRefreshCoordinator(
            lambda attempted_at: demo_snapshot(), QuoteCache(ForbiddenProvider())
        ),
    )
    original = st.get_option
    monkeypatch.setattr(
        st,
        "get_option",
        lambda key: "127.0.0.1" if key == "server.address" else original(key),
    )
    state = {"built": [], "loader": None}

    def use(loader, configured=True):
        if configured:
            monkeypatch.setenv("PORTFOLIO_WATCHLIST_RANGE", RANGE["PORTFOLIO_WATCHLIST_RANGE"])

        def build(config):
            state["built"].append(config.mode)
            return loader

        monkeypatch.setattr(watchlist_refresh, "build_local_watchlist_coordinator", build)
        state["loader"] = loader
        return state

    state["use"] = use
    return state


def sheet_rows(count, *, wide=False):
    """250 synthetic rows; every third has the optional columns, the rest only A-D."""
    rows = []
    for index in range(count):
        change = f"{(index % 11) - 5},5"
        base = [f"Wert {index:03d}", f"{10 + index},00", change, "0,10"]
        if wide:
            kind = ("Aktie", "ETF")[index % 2]
            category = ("Tech", "Welt", None)[index % 3]
            base += [f"T{index:03d}", kind, None, None, None, category]
        rows.append(base)
    return rows


def real_coordinator(rows, headers=BASIC_HEADERS, reads=None):
    """The real coordinator on a fake Sheet reader (no row has a Yahoo mapping)."""

    def read(attempted_at):
        if reads is not None:
            reads.append(attempted_at)
        return parse_watchlist_rows(
            headers, rows, imported_at=attempted_at, number_locale=NumberLocale.GERMAN
        ).snapshot

    return WatchlistRefreshCoordinator(read, calendars=venue_calendars(date.today()))


def snapshot_of(items_rows, headers=WIDE):
    return parse_watchlist_rows(
        headers, items_rows, imported_at=STAMP, number_locale=NumberLocale.GERMAN
    ).snapshot


# --- navigation -----------------------------------------------------------------


def test_demo_starts_on_the_depot_course_view_and_offers_the_watchlist(offline_demo):
    app = new_app().run()
    assert not app.exception
    assert app.radio(key=AREA_RADIO_KEY).label == "Ansicht"
    assert app.radio(key=AREA_RADIO_KEY).options == ["Depot", "Watchlist", "Innerer Wert"]
    assert app.radio(key=AREA_RADIO_KEY).value == "Depot"
    assert app.subheader[0].value == "Kursentwicklung · Heute"
    assert app.radio(key=PERIOD_RADIO_KEY).value == "Heute"
    assert "Watchlist" not in " ".join(c.value for c in app.caption)


def test_demo_watchlist_shows_synthetic_values_without_loading_anything(offline_demo):
    app = area(new_app().run(), "Watchlist")
    assert not app.exception
    assert app.subheader[0].value == "Watchlist · Heute"
    assert app.radio(key=PERIOD_RADIO_KEY).options == ["Heute", "Woche", "Monat", "Jahr"]
    assert any("Nur synthetische Beispieldaten" in info.value for info in app.info)
    assert [m.label for m in app.metric] == [
        "Beobachtete Werte",
        "Datenabdeckung (Heute)",
        "Größter Gewinner",
        "Größter Verlierer",
    ]
    assert len(table(app)) == 6 and not app.error
    assert expander_labels(app) == ["Watchlist-Details ohne Hover (6)", "Datenstatus & Hinweise (2)"]
    assert any(c.value.startswith("Datenstand (synthetisch)") for c in app.caption)


def test_demo_watchlist_marks_sheet_values_and_missing_periods(offline_demo):
    app = area(new_app().run(), "Watchlist")
    rows = table(app).set_index("Name")
    assert rows.loc["Demo-Krypto", "Quelle"] == SHEET_VALUE_LABEL
    assert rows.loc["Demo-Krypto", "Kursstand"] == "Kursstand unbekannt"
    assert rows.loc["Demo-Krypto", "Kurs"].endswith("(Sheet)")
    assert rows.loc["Demo-Aktie DE", "Quelle"] == "Demo-Wert, synthetisch"
    assert rows.loc["Demo-ohne Kurs", "Kursentwicklung (%)"] == "n. v."
    assert any(
        c.value.startswith("Datenabdeckung „Heute“: 5 von 6") and "1 Sheet-Ersatzwert(e)" in c.value
        for c in app.caption
    )

    app.radio(key=PERIOD_RADIO_KEY).set_value("Woche").run()
    rows = table(app).set_index("Name")
    assert rows.loc["Demo-Krypto", "Kursentwicklung (%)"] == "n. v."
    assert "keine Yahoo-Zuordnung im Blatt" in rows.loc["Demo-Krypto", "Quelle"]
    assert rows.loc["Demo-ohne Kurs", "Kursentwicklung (%)"] == "n. v."
    assert rows.loc["Demo-Aktie US", "Kursentwicklung (%)"] == "+4,05 %"
    assert "0,00 %" not in rows["Kursentwicklung (%)"].tolist()
    text = details_text(app)
    # Without hover: every period of the Sheet-only entry with its origin.
    assert "Heute: -2,30 % · Sheet-Wert, Kursstand unbekannt" in text
    assert "Woche: n. v. · n. v. (keine Yahoo-Zuordnung im Blatt)" in text


@pytest.mark.parametrize("period", ["Woche", "Monat", "Jahr"])
def test_period_is_kept_when_switching_between_depot_and_watchlist(offline_demo, period):
    app = new_app().run()
    app.radio(key=PERIOD_RADIO_KEY).set_value(period).run()
    app = area(app, "Watchlist")
    assert app.radio(key=PERIOD_RADIO_KEY).value == period
    assert app.subheader[0].value == f"Watchlist · {period}"
    app.radio(key=PERIOD_RADIO_KEY).set_value("Heute").run()
    app = area(app, "Depot")
    assert app.radio(key=PERIOD_RADIO_KEY).value == "Heute"
    assert app.subheader[0].value == "Kursentwicklung · Heute"
    app.radio(key=PERIOD_RADIO_KEY).set_value(period).run()
    assert area(app, "Watchlist").subheader[0].value == f"Watchlist · {period}"


def test_since_purchase_falls_back_to_today_without_losing_the_depot_choice(offline_demo):
    app = new_app().run()
    app.radio(key=PERIOD_RADIO_KEY).set_value("Seit Kauf").run()
    assert app.subheader[0].value == "Meine Entwicklung · Seit Kauf"

    app = area(app, "Watchlist")
    assert not app.exception
    assert app.radio(key=PERIOD_RADIO_KEY).value == "Heute"
    assert app.subheader[0].value == "Watchlist · Heute"
    assert any(c.value == WATCHLIST_SINCE_PURCHASE_TEXT for c in app.caption)
    assert app.session_state[COURSE_PERIOD_KEY] == "Seit Kauf"
    app.run()  # a hidden rerun must not drop the choice either
    assert app.session_state[COURSE_PERIOD_KEY] == "Seit Kauf"

    app = area(app, "Depot")
    assert app.radio(key=PERIOD_RADIO_KEY).value == "Seit Kauf"
    assert app.subheader[0].value == "Meine Entwicklung · Seit Kauf"

    # An explicit choice in the Watchlist replaces it and stays for the Depot.
    app = area(app, "Watchlist")
    app.radio(key=PERIOD_RADIO_KEY).set_value("Monat").run()
    assert not any(c.value == WATCHLIST_SINCE_PURCHASE_TEXT for c in app.caption)
    app = area(app, "Depot")
    assert app.radio(key=PERIOD_RADIO_KEY).value == "Monat"
    assert app.subheader[0].value == "Kursentwicklung · Monat"


def test_each_area_keeps_its_own_filters_while_the_other_is_shown(offline_demo):
    app = new_app().run()
    app.selectbox(key=FILTER_TYPE_KEY).set_value("ETF").run()
    app = area(app, "Watchlist")
    app.text_input(key=WATCHLIST_FILTER_QUERY_KEY).set_value("US").run()
    assert column(app, "Name") == ["Demo-Aktie US"]
    app = area(app, "Depot")
    assert app.selectbox(key=FILTER_TYPE_KEY).value == "ETF"
    assert column(app, "Instrument") == ["Demo-Fonds B"]
    app = area(app, "Watchlist")
    assert app.text_input(key=WATCHLIST_FILTER_QUERY_KEY).value == "US"
    assert column(app, "Name") == ["Demo-Aktie US"]


# --- filters and sorting --------------------------------------------------------


def test_watchlist_search_type_category_and_sorting(offline_demo):
    app = area(new_app().run(), "Watchlist")
    assert app.text_input(key=WATCHLIST_FILTER_QUERY_KEY).label == (
        "Suche (Name, Ticker, Yahoo-Symbol, Kategorie)"
    )
    # Default: performance, descending, n. v. last.
    assert column(app, "Name") == [
        "Demo-Aktie US", "Demo-Aktie DE", "Demo-ETF DE", "Demo-Aktie NYSE",
        "Demo-Krypto", "Demo-ohne Kurs",
    ]
    app.selectbox(key=WATCHLIST_SORT_ORDER_KEY).set_value("Aufsteigend").run()
    assert column(app, "Name")[0] == "Demo-Krypto"
    assert column(app, "Name")[-1] == "Demo-ohne Kurs"
    app.selectbox(key=WATCHLIST_SORT_FIELD_KEY).set_value("Name").run()
    assert column(app, "Name")[0] == "Demo-Aktie DE"
    app.selectbox(key=WATCHLIST_SORT_FIELD_KEY).set_value("Ticker").run()
    assert column(app, "Name")[-2:] == ["Demo-Krypto", "Demo-ohne Kurs"]  # no ticker: last

    app.selectbox(key=WATCHLIST_FILTER_TYPE_KEY).set_value("ETF").run()
    assert column(app, "Name") == ["Demo-ETF DE"]
    app.selectbox(key=WATCHLIST_FILTER_TYPE_KEY).set_value("Nicht angegeben").run()
    assert sorted(column(app, "Name")) == ["Demo-Krypto", "Demo-ohne Kurs"]
    app.selectbox(key=WATCHLIST_FILTER_TYPE_KEY).set_value("Alle").run()
    assert app.selectbox(key=WATCHLIST_FILTER_CATEGORY_KEY).options == [
        "Alle", "Demo", "Ohne Kategorie"
    ]
    app.selectbox(key=WATCHLIST_FILTER_CATEGORY_KEY).set_value("Ohne Kategorie").run()
    assert sorted(column(app, "Name")) == ["Demo-Krypto", "Demo-ohne Kurs"]
    app.text_input(key=WATCHLIST_FILTER_QUERY_KEY).set_value("democ").run()  # Yahoo symbol
    assert not app.dataframe  # only uncategorized rows remain, none matches
    assert any("Keine Watchlist-Einträge" in info.value for info in app.info)
    app.selectbox(key=WATCHLIST_FILTER_CATEGORY_KEY).set_value("Alle").run()
    assert column(app, "Name") == ["Demo-Aktie US"]
    # KPIs describe the whole Watchlist, not the filter.
    assert app.metric[0].value == "6"
    assert not app.exception


# --- local mode: configuration, loading, state -----------------------------------


def test_local_mode_without_a_range_shows_a_hint_instead_of_an_error(local):
    app = new_app().run()
    assert not app.exception and app.dataframe
    app = area(app, "Watchlist")
    assert not app.exception and not app.error
    assert [info.value for info in app.info] == [WATCHLIST_NOT_CONFIGURED_TEXT]
    assert "PORTFOLIO_WATCHLIST_RANGE" in app.info[0].value
    assert not app.dataframe and not app.metric
    app = area(app, "Depot")  # the Depot is unaffected
    assert app.dataframe and app.subheader[0].value == "Kursentwicklung · Heute"


def test_local_mode_never_builds_or_loads_the_watchlist_before_it_is_opened(local):
    loader = FakeLoader(WatchlistBundle(True, snapshot_of([]), (), STAMP))
    state = local["use"](loader)
    app = new_app().run()
    app.run()
    assert not app.exception and state["built"] == [] and loader.calls == []
    area(app, "Watchlist")
    assert state["built"] == ["local"] and loader.calls == [False]


def test_watchlist_is_loaded_once_per_session_and_refreshed_on_request(local):
    snapshot = snapshot_of(sheet_rows(3, wide=True))
    bundle = WatchlistBundle(True, snapshot, entries(), STAMP)
    loader = FakeLoader(bundle)
    state = local["use"](loader)
    app = area(new_app().run(), "Watchlist")
    app.radio(key=PERIOD_RADIO_KEY).set_value("Monat").run()
    app.text_input(key=WATCHLIST_FILTER_QUERY_KEY).set_value("alp").run()
    area(area(app, "Depot"), "Watchlist")
    assert loader.calls == [False] and state["built"] == ["local"]
    assert app.session_state[WATCHLIST_BUNDLE_KEY] is bundle

    app.button(key=ui.REFRESH_BUTTON_KEY).click().run()
    assert loader.calls == [False, True]
    assert not app.exception
    # The refresh button acts on the shown area only: no second Depot refresh happened.
    assert state["built"] == ["local"]


def test_a_new_calendar_day_rebuilds_the_loader_and_loads_again(local, monkeypatch):
    day = [date(2026, 10, 28)]
    monkeypatch.setattr(ui, "_today", lambda: day[0])
    loader = FakeLoader(WatchlistBundle(True, snapshot_of([]), (), STAMP))
    state = local["use"](loader)
    app = area(new_app().run(), "Watchlist")
    app.run()
    assert state["built"] == ["local"] and loader.calls == [False]
    day[0] = date(2026, 10, 29)
    app.run()
    assert state["built"] == ["local", "local"] and loader.calls == [False, False]
    app.run()
    assert state["built"] == ["local", "local"] and loader.calls == [False, False]


def test_two_hundred_fifty_rows_without_optional_columns(local):
    reads = []
    coordinator = real_coordinator(sheet_rows(250), BASIC_HEADERS, reads)
    state = local["use"](coordinator)
    app = area(new_app().run(), "Watchlist")
    assert not app.exception and not app.error
    assert len(table(app)) == 250 and len(reads) == 1
    assert app.metric[0].value == "250"
    # Only the Sheet substitute for Heute; no Yahoo mapping, so no other period.
    assert (app.metric[1].value, app.metric[1].delta) == ("250 von 250", "mit Kennzahl")
    assert any("250 Sheet-Ersatzwert(e) (Sheet-Wert, Kursstand unbekannt)" in c.value for c in app.caption)
    assert set(column(app, "Quelle")) == {SHEET_VALUE_LABEL}
    assert set(column(app, "Status")) == {SHEET_VALUE_LABEL}
    assert set(column(app, "Typ")) == {"Nicht angegeben"}
    assert set(column(app, "Ticker")) == {"—"}
    assert expander_labels(app)[0] == "Watchlist-Details ohne Hover (250)"
    assert details_text(app).count("Zeile im Blatt") == 250

    app.radio(key=PERIOD_RADIO_KEY).set_value("Jahr").run()
    assert set(column(app, "Kursentwicklung (%)")) == {"n. v."}
    assert app.metric[1].value == "0 von 250"
    assert app.metric[2].value == app.metric[3].value == "n. v."
    assert len(reads) == 1  # the period switch reuses the loaded bundle
    app.text_input(key=WATCHLIST_FILTER_QUERY_KEY).set_value("Wert 24").run()
    assert len(table(app)) == 10
    assert state["built"] == ["local"] and len(reads) == 1


def test_two_hundred_fifty_rows_with_optional_columns_and_missing_cells(local):
    rows = sheet_rows(250, wide=True)
    rows[3] = ["Ohne Werte", None, None, None]  # short row, blank cells
    local["use"](real_coordinator(rows, WIDE))
    app = area(new_app().run(), "Watchlist")
    assert not app.exception and len(table(app)) == 250
    assert table(app).set_index("Name").loc["Ohne Werte", "Kursentwicklung (%)"] == "n. v."
    app.selectbox(key=WATCHLIST_FILTER_TYPE_KEY).set_value("ETF").run()
    assert len(table(app)) == 124  # 125 odd rows, one of them blanked
    app.selectbox(key=WATCHLIST_FILTER_CATEGORY_KEY).set_value("Welt").run()
    assert 0 < len(table(app)) < 124
    app.selectbox(key=WATCHLIST_SORT_FIELD_KEY).set_value("Ticker").run()
    assert not app.exception


def test_unavailable_watchlist_is_an_error_and_the_depot_stays_usable(local):
    failed = WatchlistSnapshot(
        STAMP, SnapshotStatus.FAILED, (), (DataWarning("read_failed", "Blatt nicht lesbar."),)
    )
    message = "Daten nicht verfügbar: Watchlist konnte nicht geladen werden."
    local["use"](FakeLoader(empty_bundle(failed, STAMP, message)))
    app = area(new_app().run(), "Watchlist")
    assert not app.exception
    assert [error.value for error in app.error] == [message]
    assert not app.dataframe and not app.metric
    assert area(app, "Depot").dataframe


def test_cached_stand_warns_and_marks_cached_and_sheet_rows(local):
    snapshot = snapshot_of(sheet_rows(5, wide=True))
    warning = "Aktualisierung fehlgeschlagen – letzter gültiger Stand vom 28.10.2026 (Alter: vor 3 Std.)"
    bundle = WatchlistBundle(True, snapshot, entries(), STAMP, True, warning)
    local["use"](FakeLoader(bundle))
    app = area(new_app().run(), "Watchlist")
    assert [w.value for w in app.warning] == [warning]
    rows = table(app).set_index("Name")
    assert rows.loc["Beta ETF", "Quelle"] == "Anbieter, zwischengespeichert (vor 2 Std.)"
    assert rows.loc["Beta ETF", "Status"].startswith("Zwischengespeichert")
    assert "vor 2 Std." in rows.loc["Beta ETF", "Kursstand"]
    assert rows.loc["Gamma Krypto", "Status"] == SHEET_VALUE_LABEL
    notes = app.markdown[-1].value
    assert "Beta ETF" in notes and "Gamma Krypto" in notes and "Zwischengespeichert" in notes
    assert "Alpha AG" in notes  # Heute only from the Sheet


def test_mixed_origins_are_visible_per_period_in_the_table_and_details(local):
    local["use"](FakeLoader(WatchlistBundle(True, snapshot_of([]), entries(), STAMP)))
    app = area(new_app().run(), "Watchlist")
    rows = table(app).set_index("Name")
    assert rows.loc["Alpha AG", "Quelle"].startswith(SHEET_VALUE_LABEL)
    assert rows.loc["Alpha AG", "Status"].endswith(f"Heute: {SHEET_VALUE_LABEL}")
    app.radio(key=PERIOD_RADIO_KEY).set_value("Woche").run()
    rows = table(app).set_index("Name")
    assert rows.loc["Alpha AG", "Quelle"] == "Anbieter (Yahoo)"
    assert rows.loc["Alpha AG", "Referenzdatum"] == "27.10.2026"
    assert rows.loc["Beta ETF", "Kursentwicklung (%)"] == "n. v."
    assert "Historie unvollständig" in rows.loc["Beta ETF", "Quelle"]
    assert rows.loc["Delta Optionsschein", "Kursentwicklung (%)"] == "n. v."


def test_error_text_of_a_failing_credential_build_is_the_safe_message(local, monkeypatch):
    from portfolio_dashboard.sheets_session import SheetsCredentialError

    monkeypatch.setenv("PORTFOLIO_WATCHLIST_RANGE", RANGE["PORTFOLIO_WATCHLIST_RANGE"])

    def failing(config):
        raise SheetsCredentialError("Service-Account-Schlüssel fehlt oder ist ungültig.")

    monkeypatch.setattr(watchlist_refresh, "build_local_watchlist_coordinator", failing)
    app = area(new_app().run(), "Watchlist")
    assert not app.exception
    assert [e.value for e in app.error] == ["Service-Account-Schlüssel fehlt oder ist ungültig."]


# --- helpers ----------------------------------------------------------------------


def test_markdown_text_escapes_sheet_provided_text():
    raw = "<b>*A*</b> _x_ [l](u) $5 `c` #h | ~s~ \\"
    escaped = _markdown_text(raw)
    assert "<b>" not in escaped
    for char in "*_[]$`#|~":
        assert char not in escaped.replace("\\" + char, "")
    assert escaped.endswith("\\\\")
