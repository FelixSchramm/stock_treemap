"""Area «Innerer Wert» (#79): AppTests with invented data, offline.

The demo entry point runs through ``app.py`` with the invented loader and scenario
reader; local mode gets a fake depot coordinator and, where stated, a fake
fundamentals loader that records every call. IP sockets are blocked.
"""

import os
import socket
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import portfolio_dashboard.fundamentals_loader as fundamentals_loader
import portfolio_dashboard.local_source as local_source
import portfolio_dashboard.valuation_view as view
from portfolio_dashboard.demo import demo_snapshot
from portfolio_dashboard.fundamentals_demo import DEMO_VALUED_ID
from portfolio_dashboard.fundamentals_loader import FundamentalsBundle
from portfolio_dashboard.private_store import PrivateStorageError
from portfolio_dashboard.quote_cache import QuoteCache
from portfolio_dashboard.refresh import PortfolioRefreshCoordinator
from portfolio_dashboard.ui import AREA_RADIO_KEY, COURSE_PERIOD_KEY
from portfolio_dashboard.valuation_demo import demo_valuation_source
from portfolio_dashboard.valuation_overview import (
    SCOPE_PORTFOLIO,
    SCOPE_WATCHLIST,
    SHOW_UNVALUED,
    SORT_BY_DISCOUNT,
)

APP_PATH = Path(__file__).parents[1] / "app.py"
LOCAL = {
    "PORTFOLIO_MODE": "local",
    "PORTFOLIO_SHEET_ID": "synthetic-source-id",
    "PORTFOLIO_SHEET_RANGE": "'Synthetic tab'!A1:M10",
    "PORTFOLIO_SHEETS_CREDENTIALS_FILE": "/nonexistent/synthetic-credential.json",
}
COLUMNS = [
    "Name",
    "Ticker",
    "Bestand",
    "Kurs",
    "Vorsichtig",
    "Basis",
    "Optimistisch",
    "Kurs-Abschlag zum Basiswert (% des Basiswerts)",
    "Basiswert über Kurs (% des Kurses)",
    "Zielpreis (Basis)",
    "Status",
    "Datenstand",
]


def clean_environment(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("PORTFOLIO_"):
            monkeypatch.delenv(name)

    def no_network(*args, **kwargs):
        raise AssertionError("Valuation overview attempted network access")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)


@pytest.fixture
def demo(monkeypatch):
    clean_environment(monkeypatch)
    monkeypatch.setenv("PORTFOLIO_WATCHLIST_RANGE", "Synthetic!A1:J400")


class ForbiddenProvider:
    def __getattr__(self, name):
        raise AssertionError("Depot stand-in requested market data")


class FakeFundamentals:
    """Records every load; raises the given storage error when set."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def load(self, instruments, *, refresh=False):
        self.calls.append((tuple(instruments), refresh))
        if self.error is not None:
            raise self.error
        return FundamentalsBundle({}, demo_valuation_source().clock(), 0)


@pytest.fixture
def local(monkeypatch):
    clean_environment(monkeypatch)
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

    def use(loader):
        monkeypatch.setattr(
            fundamentals_loader,
            "build_local_fundamentals_loader",
            lambda config: loader,
        )

    return use


def new_app():
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def opened(app=None):
    app = app or new_app().run()
    app.radio(key=AREA_RADIO_KEY).set_value(view.VALUATION_AREA)
    return app.run()


def names(app):
    return app.dataframe[0].value["Name"].tolist()


def metrics(app):
    return {m.label: m.value for m in app.metric}


def expander_labels(app):
    return [e.label for e in app.expander]


# --- demo ------------------------------------------------------------------------------


def test_area_is_offered_and_shows_the_synthetic_overview(demo):
    app = new_app().run()
    assert app.radio(key=AREA_RADIO_KEY).options == [
        "Depot",
        "Watchlist",
        "Innerer Wert",
    ]
    app = opened(app)
    assert not app.exception and not app.error
    assert [s.value for s in app.subheader][:2] == [
        "Innerer Wert",
        "Bewertungsübersicht",
    ]
    assert list(app.dataframe[0].value.columns) == COLUMNS
    assert names(app) == [
        "Demo-Aktie A",
        "Demo-Aktie NYSE",
        "Demo-Aktie US",
        "Demo-Krypto",
        "Demo-ohne Kurs",
    ]
    assert any("Nur synthetische Beispieldaten" in i.value for i in app.info)
    captions = " ".join(c.value for c in app.caption)
    assert "kein Kauf- oder Verkaufssignal" in captions
    assert "(synthetisch)" in captions  # data stand of the invented sources
    assert "Fundamentaldaten: letzter Abruf 23.10.2026" in captions
    assert "bezieht sich auf den Basiswert" in captions
    assert "bezieht sich auf den Kurs" in captions


def test_overlap_and_model_values_are_visible_with_text_status(demo):
    table = opened().dataframe[0].value
    first = table.iloc[0]
    assert first["Bestand"] == "im Depot und auf Watchlist"
    assert (first["Vorsichtig"], first["Basis"], first["Optimistisch"]) == (
        "106,98 EUR",
        "135,15 EUR",
        "192,86 EUR",
    )
    assert first["Kurs-Abschlag zum Basiswert (% des Basiswerts)"] == "+11,21 %"
    assert first["Basiswert über Kurs (% des Kurses)"] == "+12,63 %"
    assert first["Zielpreis (Basis)"] == "108,12 EUR"
    assert first["Status"] == "Bewertet"
    rest = table.iloc[1:]
    assert set(rest["Status"]) == {
        "Modell nicht unterstützt",
        "Annahmen fehlen",
        "Zuordnung fehlt",
    }
    for column in ("Vorsichtig", "Basis", "Optimistisch", "Zielpreis (Basis)"):
        assert set(rest[column]) == {"n. v."}  # missing, never 0


def test_coverage_follows_the_filter(demo):
    app = opened()
    assert metrics(app) == {
        "Betrachtete Aktien": "5",
        "Mit Modellwert": "1 von 5 (20 %)",
        "Alle drei Szenarien": "1",
        "Ohne Modellwert": "4",
    }
    assert any(
        "Ausgeschlossene Produktarten (nicht gezählt): ETF: 2." in c.value
        for c in app.caption
    )
    app.radio(key=view.SCOPE_KEY).set_value(SCOPE_PORTFOLIO).run()
    assert names(app) == ["Demo-Aktie A"]
    assert metrics(app)["Mit Modellwert"] == "1 von 1 (100 %)"
    app.radio(key=view.SCOPE_KEY).set_value(SCOPE_WATCHLIST).run()
    assert "Demo-Aktie A" in names(app) and len(names(app)) == 5
    assert metrics(app)["Mit Modellwert"] == "1 von 5 (20 %)"
    app.text_input(key=view.QUERY_KEY).set_value("krypto").run()
    assert names(app) == ["Demo-Krypto"]
    assert metrics(app)["Mit Modellwert"] == "0 von 1 (0 %)"
    app.text_input(key=view.QUERY_KEY).set_value("gibt es nicht").run()
    assert not app.dataframe and any("Keine Aktien" in i.value for i in app.info)
    assert metrics(app)["Mit Modellwert"] == "n. v."


def test_filter_by_model_value_and_sorting_keep_missing_values_last(demo):
    app = opened()
    app.selectbox(key=view.SHOW_KEY).set_value(SHOW_UNVALUED).run()
    assert "Demo-Aktie A" not in names(app) and len(names(app)) == 4
    app.selectbox(key=view.SHOW_KEY).set_value("Alle Einträge").run()
    app.selectbox(key=view.SORT_FIELD_KEY).set_value(SORT_BY_DISCOUNT).run()
    for order in ("Absteigend", "Aufsteigend"):
        app.selectbox(key=view.SORT_ORDER_KEY).set_value(order).run()
        assert names(app)[0] == "Demo-Aktie A"  # only row with a figure; gaps last
        assert app.dataframe[0].value["Basis"].tolist()[1:] == ["n. v."] * 4


def test_excluded_products_are_explained_apart_from_the_stocks(demo):
    app = opened()
    assert "Ausgeschlossene Produktarten (2)" in expander_labels(app)
    excluded = next(e for e in app.expander if e.label.startswith("Ausgeschlossene"))
    text = " ".join(m.value for m in excluded.markdown) + " ".join(
        c.value for c in excluded.caption
    )
    assert "Demo-Fonds B" in text and "Demo-ETF DE" in text
    assert "keinen solchen Wert" in text
    assert "Demo-Fonds B" not in names(app)


def test_compact_cards_show_every_field_without_hover_or_table(demo):
    app = opened()
    app.radio(key=view.LAYOUT_KEY).set_value(view.LAYOUT_COMPACT).run()
    assert not app.dataframe and not app.exception
    text = " ".join(m.value for m in app.markdown)
    for expected in (
        "Modellwert Basis: 135,15 EUR",
        "Kursvergleich (Basis)",
        "Abrufalter:",
        "Fachliches Alter: Jahresabschluss bis 31.12.2025",
        "Annahmenstand: Version demo-assumptions-v1",
        "manuell ergänzt:",
        "Status: Annahmen fehlen",
        "Grund: Im Watchlist-Blatt fehlt eine vollständige Yahoo-Zuordnung",
    ):
        assert expected in text
    app.radio(key=view.LAYOUT_KEY).set_value(view.LAYOUT_TABLE).run()
    assert app.dataframe and "Bewertungsdetails ohne Hover (5)" in expander_labels(app)


def test_notes_list_every_stock_without_full_valuation(demo):
    app = opened()
    assert "Datenstatus & Hinweise (4)" in expander_labels(app)
    notes = next(e for e in app.expander if e.label.startswith("Datenstatus"))
    text = " ".join(m.value for m in notes.markdown)
    assert "Demo-Aktie US" in text and "Annahmen fehlen" in text
    assert "Demo-Aktie A" not in text  # fully valued


def test_detail_entry_opens_the_detail_view_and_consumes_the_requested_key(demo):
    app = opened()
    assert view.DETAIL_KEY not in app.session_state
    select = app.selectbox(key=view.DETAIL_SELECT_KEY)
    assert (
        len(select.options) == 3
    )  # verified stocks only: valued, unsupported, no assumptions
    app.button(key=view.DETAIL_BUTTON_KEY).click().run()
    assert view.DETAIL_KEY not in app.session_state  # read and cleared (#80)
    assert app.session_state[view.DETAIL_OPEN_KEY] == DEMO_VALUED_ID
    assert app.subheader[1].value == "Details und Annahmen: Demo-Aktie A"
    assert not app.exception


def test_choices_survive_switching_areas_and_the_period_is_untouched(demo):
    app = new_app().run()
    before = app.session_state[COURSE_PERIOD_KEY]
    app = opened(app)
    app.text_input(key=view.QUERY_KEY).set_value("us").run()
    app.radio(key=AREA_RADIO_KEY).set_value("Watchlist").run()
    app.radio(key=AREA_RADIO_KEY).set_value(view.VALUATION_AREA).run()
    assert app.text_input(key=view.QUERY_KEY).value == "us"
    assert names(app) == ["Demo-Aktie US"]
    app.radio(key=AREA_RADIO_KEY).set_value("Depot").run()
    assert app.session_state[COURSE_PERIOD_KEY] == before
    assert app.subheader[0].value == "Kursentwicklung · Heute"


def test_valuation_area_uses_only_native_keyboard_operable_widgets(demo):
    app = opened()
    assert {w.label for w in app.radio} >= {"Ansicht", "Bestand", "Darstellung"}
    assert app.text_input(key=view.QUERY_KEY).label.startswith("Suche")
    assert not app.get("plotly_chart") and not app.get("html")


# --- local mode: explicit loading, fixed storage messages --------------------------------


def test_without_a_fundamentals_mapping_the_area_explains_it_and_loads_nothing(local):
    local(None)
    app = opened()
    assert not app.exception and not app.error
    assert not [b for b in app.button if b.key == view.LOAD_BUTTON_KEY]
    table = app.dataframe[0].value
    assert table["Status"].tolist() == ["Zuordnung fehlt"]  # unverified depot stock
    notes = next(e for e in app.expander if e.label.startswith("Datenstatus"))
    assert any(
        "keine private Fundamentaldaten-Zuordnung" in m.value for m in notes.markdown
    )


def test_fundamentals_load_only_on_the_explicit_action_and_stay_in_the_session(local):
    loader = FakeFundamentals()
    local(loader)
    app = opened()
    assert loader.calls == []  # opening the area and every rerun retrieve nothing
    button = app.button(key=view.LOAD_BUTTON_KEY)
    assert button.label == view.LOAD_LABEL_FIRST
    assert app.dataframe[0].value["Status"].tolist() == ["Zuordnung fehlt"]
    button.click().run()
    assert loader.calls == [((), False)]
    app.run()
    app.text_input(key=view.QUERY_KEY).set_value("a").run()
    assert len(loader.calls) == 1
    assert app.button(key=view.LOAD_BUTTON_KEY).label == view.LOAD_LABEL_AGAIN
    app.button(key=view.LOAD_BUTTON_KEY).click().run()
    assert loader.calls[-1] == ((), True) and len(loader.calls) == 2


def test_the_global_refresh_button_is_an_explicit_action_in_this_area(local):
    loader = FakeFundamentals()
    local(loader)
    app = opened()
    app.button(key="dashboard.refresh").click().run()
    assert len(loader.calls) == 1


def test_a_storage_error_is_shown_as_its_fixed_message(local):
    error = PrivateStorageError("path")
    loader = FakeFundamentals(error)
    local(loader)
    app = opened()
    app.button(key=view.LOAD_BUTTON_KEY).click().run()
    assert not app.exception
    assert [e.value for e in app.error] == [str(error)]
    assert app.dataframe[0].value["Status"].tolist() == ["Zuordnung fehlt"]
    assert len(loader.calls) == 1
    app.run()
    assert len(loader.calls) == 1  # a rerun does not retry


def test_touch_targets_and_wrapping_styles_apply_to_the_area(demo):
    app = opened()
    styles = " ".join(m.value for m in app.markdown)
    assert "min-height: 2.75rem" in styles and "flex-wrap: wrap" in styles
