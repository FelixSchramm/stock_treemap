"""Sheet-to-company selection with fake transports; never reads private Sheets."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from portfolio_dashboard.exploration import view
from portfolio_dashboard.exploration.charts import AnalysisUnavailable, build
from portfolio_dashboard.exploration.data import fetch, prices
from portfolio_dashboard.exploration.demo import demo_bundle
from portfolio_dashboard.exploration.selection import (
    DEFAULT_COMPANIES,
    Catalog,
    Company,
    combine_companies,
    load_catalog,
    watchlist_companies,
)
from portfolio_dashboard.instruments import EquityForm
from portfolio_dashboard.sheet_parser import NumberLocale
from portfolio_dashboard.watchlist_refresh import WatchlistRefreshCoordinator
from portfolio_dashboard.watchlist_sheet import (
    BASIC_HEADERS,
    OPTIONAL_HEADERS,
    parse_watchlist_rows,
)

NOW = datetime(2026, 11, 2, tzinfo=UTC)
FIRST = Company(
    "Synthetisch A",
    "SYNTH-A.DE",
    "GER",
    "EUR",
    ("Depot",),
    "depot:2",
    Decimal(1),
    EquityForm.SHARE,
)
SECOND = Company(
    "Synthetisch B",
    "SYNTH-B",
    "NMS",
    "USD",
    ("Watchlist",),
    "watchlist:2",
    Decimal(1),
    EquityForm.SHARE,
)


def watch_snapshot():
    return parse_watchlist_rows(
        (*BASIC_HEADERS, *OPTIONAL_HEADERS),
        [
            [
                "Synthetisch A",
                None,
                None,
                None,
                "DO-NOT-USE",
                "Aktie",
                FIRST.symbol,
                "GER",
                "EUR",
                "Demo",
            ],
            [
                "Ohne Zuordnung",
                None,
                None,
                None,
                "DO-NOT-FETCH",
                "Aktie",
                None,
                None,
                None,
                None,
            ],
            [
                "Fonds",
                None,
                None,
                None,
                "ETF",
                "ETF",
                "SYNTH-ETF.DE",
                "GER",
                "EUR",
                "Demo",
            ],
        ],
        imported_at=NOW,
        number_locale=NumberLocale.GERMAN,
    ).snapshot


def test_watchlist_only_uses_explicit_mapping_and_marks_unavailable():
    rows = watchlist_companies(watch_snapshot())
    assert rows[0].symbol == FIRST.symbol
    assert rows[1].symbol is None and "Zuordnung" in rows[1].reason
    assert rows[2].symbol is None and "Aktie" in rows[2].reason


def test_duplicate_listing_merges_membership_but_not_venues():
    watch = replace(
        FIRST, origins=("Watchlist",), row_key="watchlist:2", equity_form=None
    )
    other = replace(watch, exchange="FRA", symbol="SYNTH-A.F")
    companies = Catalog(depot=(FIRST,), watchlist=(watch, other)).companies
    assert len(companies) == 2
    assert companies[0].origins == ("Depot", "Watchlist")
    assert companies[0].equity_form is EquityForm.SHARE


def test_catalog_keeps_failed_source_but_accepts_successful_empty_source():
    previous = Catalog(
        depot=(FIRST,), watchlist=(SECOND,), depot_at=NOW, watchlist_at=NOW
    )

    def failed():
        raise RuntimeError("private detail must not escape")

    good_empty = replace(watch_snapshot(), items=())
    actual = load_catalog(
        SimpleNamespace(read_selection=failed),
        SimpleNamespace(read_selection=lambda: good_empty),
        previous,
    )
    assert actual.depot == (FIRST,)
    assert actual.watchlist == ()
    assert actual.depot_at == NOW
    assert "private detail" not in str(actual.notes)
    assert any("Depot-Sheet" in n for n in actual.notes)


def test_watchlist_selection_never_requests_market_data():
    calls = []

    def forbidden(*args):
        raise AssertionError("Catalog attempted market retrieval")

    coordinator = WatchlistRefreshCoordinator(
        lambda at: calls.append(at) or watch_snapshot(),
        calendars={},
        provider_factory=lambda listings: SimpleNamespace(
            latest_quote=forbidden, history=forbidden, fx=forbidden
        ),
        clock=lambda: NOW,
    )
    assert coordinator.read_selection().items
    assert calls == [NOW]
    assert coordinator.last_bundle is None


@pytest.mark.parametrize("currency", ["EUR", "JPY", "CHF"])
def test_financial_charts_use_report_currency_without_changing_amounts(currency):
    bundle = demo_bundle(FIRST)
    bundle["info"]["financialCurrency"] = currency
    result = build(9, bundle)
    panel = result.panels[0]
    assert panel.unit == f"Mrd. {currency}"
    assert panel.figure.layout.yaxis.title.text == f"Mrd. {currency}"
    assert currency in panel.figure.data[0].hovertemplate
    assert panel.data.iloc[-1, 0] == pytest.approx(
        bundle["cash"].loc["Operating Cash Flow"].iloc[-1] / 1e9
    )
    assert "Juni" not in result.note


def test_mixed_currencies_and_adr_block_price_earnings_comparison():
    bundle = demo_bundle(SECOND)
    bundle["info"]["financialCurrency"] = "EUR"
    for topic in (12, 27):
        with pytest.raises(AnalysisUnavailable, match="währung"):
            build(topic, bundle)
    bundle["info"]["financialCurrency"] = "USD"
    bundle["equity_form"] = "adr"
    with pytest.raises(AnalysisUnavailable, match="ADR"):
        build(27, bundle)


def test_generic_fx_and_missing_currency_never_assume_usd():
    dates = pd.to_datetime(["2026-10-01", "2026-10-02"])
    raw = {
        "SYNTH-JP": pd.DataFrame({"Close": [11000, 22000]}, index=dates),
        "USDJPY=X": pd.DataFrame({"Close": [110, 110]}, index=dates),
        "EURUSD=X": pd.DataFrame({"Close": [2, 2]}, index=dates),
    }
    bundle = {"prices": raw, "currencies": {"SYNTH-JP": "JPY"}}
    assert prices(bundle, ("SYNTH-JP",), "EUR", False).iloc[:, 0].tolist() == [50, 100]
    del bundle["currencies"]["SYNTH-JP"]
    with pytest.raises(KeyError):
        prices(bundle, ("SYNTH-JP",), "EUR", False)


def fake_yahoo(monkeypatch, *, bad=None):
    import yfinance

    calls = []
    fixture = demo_bundle(FIRST)

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol
            calls.append(symbol)

        @property
        def info(self):
            value = fixture["info"] | {
                "symbol": self.symbol,
                "exchange": "GER",
                "quoteType": "EQUITY",
                "currency": "EUR",
            }
            return value | (bad or {})

        def history(self, **kwargs):
            return fixture["prices"][FIRST.symbol].copy()

        def __getattr__(self, name):
            resources = {
                "income_stmt": "income",
                "cashflow": "cash",
                "balance_sheet": "balance",
                "quarterly_income_stmt": "quarter_income",
                "quarterly_cashflow": "quarter_cash",
            }
            if name in resources:
                return fixture[resources[name]]
            raise ValueError("Synthetic unavailable resource")

    monkeypatch.setattr(yfinance, "Ticker", Ticker)
    return calls


def test_fetch_selected_listing_only_optional_resource_failure_is_local(monkeypatch):
    calls = fake_yahoo(monkeypatch)
    bundle = fetch(FIRST)
    assert bundle["symbol"] == FIRST.symbol and FIRST.symbol in bundle["prices"]
    assert "options" in bundle["errors"]
    assert build(9, bundle).panels
    assert not {"MSFT", "ORCL", "SAP.DE", "9988.HK"}.intersection(calls)
    assert set(calls) == {FIRST.symbol, "EURUSD=X", "SPY", "QQQ", "^GSPC", "^NDX"}


@pytest.mark.parametrize(
    "bad",
    [
        {"currency": "USD"},
        {"exchange": "NMS"},
        {"symbol": "OTHER"},
        {"quoteType": "ETF"},
    ],
)
def test_fetch_rejects_provider_mapping_mismatch_before_prices(monkeypatch, bad):
    calls = fake_yahoo(monkeypatch, bad=bad)
    bundle = fetch(FIRST)
    assert not bundle["prices"]
    assert calls == [FIRST.symbol]
    assert "Metadaten" in bundle["errors"][FIRST.symbol]


def live_page():
    from decimal import Decimal

    import streamlit as st

    from portfolio_dashboard.exploration import view
    from portfolio_dashboard.exploration.selection import Catalog, Company
    from portfolio_dashboard.instruments import EquityForm

    first = Company(
        "Synthetisch A",
        "SYNTH-A.DE",
        "GER",
        "EUR",
        ("Depot",),
        "depot:2",
        Decimal(1),
        EquityForm.SHARE,
    )
    second = Company(
        "Synthetisch B",
        "SYNTH-B",
        "NMS",
        "USD",
        ("Watchlist",),
        "watchlist:2",
        Decimal(1),
        EquityForm.SHARE,
    )

    def load(previous):
        st.session_state["catalog_reads"] = st.session_state.get("catalog_reads", 0) + 1
        return Catalog(depot=(first,), watchlist=(second,))

    view.render(demo=False, load_companies=load)


def test_automatic_defaults_selection_changes_and_cache_do_not_reload_sheet(
    monkeypatch,
):
    calls = []

    def provider(company, comparisons):
        calls.append((company.symbol, tuple(c.symbol for c in comparisons)))
        bundle = demo_bundle(company, comparisons)
        bundle["synthetic"] = False
        bundle["retrieved_at"] = pd.Timestamp.now(tz="UTC").isoformat()
        return bundle

    monkeypatch.setattr(view, "fetch", provider)
    app = AppTest.from_function(live_page).run()
    assert not app.exception and app.get("plotly_chart")
    assert calls == [("MSFT", ("ORCL", "9988.HK", "SAP.DE"))]
    assert app.selectbox(key=view.COMPANY_KEY).value == repr(DEFAULT_COMPANIES[0].key)
    assert app.multiselect(key=view.COMPARE_KEY).value == [
        repr(c.key) for c in DEFAULT_COMPANIES[1:]
    ]
    assert not app.button
    app.run()
    app.radio(key=view.GROUP_KEY).set_value("Geschäftsmodell").run()
    app.selectbox(key=f"{view.TOPIC_KEY}.Geschäftsmodell").set_value(9).run()
    assert not app.exception and app.get("plotly_chart")
    assert len(calls) == 1
    app.selectbox(key=view.COMPANY_KEY).set_value(repr(SECOND.key)).run()
    assert not app.exception and app.get("plotly_chart")
    assert calls[-1] == (SECOND.symbol, ("ORCL", "9988.HK", "SAP.DE"))
    app.selectbox(key=view.COMPANY_KEY).set_value(repr(DEFAULT_COMPANIES[0].key)).run()
    assert len(calls) == 2  # Existing Microsoft snapshot is reused.
    app.multiselect(key=view.COMPARE_KEY).set_value([repr(FIRST.key)]).run()
    assert not app.exception
    assert calls[-1] == ("MSFT", (FIRST.symbol,))
    assert app.session_state["catalog_reads"] == 1
    snapshots = app.session_state[view.STATE + ".live"]
    assert {b["symbol"] for b in snapshots.values()} == {"MSFT", SECOND.symbol}
    assert len(snapshots) == 3


def test_glossary_available_when_automatic_market_loading_fails(monkeypatch):
    monkeypatch.setattr(view, "fetch", lambda *args: {"prices": {}})
    app = AppTest.from_function(live_page).run()
    assert not app.exception
    assert [e.label for e in app.expander] == [
        "Glossar – Kennzahlen",
        "Glossar – Diagramme",
        "Glossar – Datenquellen",
    ]
    metrics, charts, sources = app.expander
    assert "ROIC" in metrics.markdown[0].value
    assert "Was wurde aus 100?" in charts.markdown[0].value
    assert "Noch nicht angebunden" in sources.markdown[0].value
    assert "t.history" in sources.markdown[0].value
    assert "t.history" not in metrics.markdown[0].value + charts.markdown[0].value


def test_presets_merge_with_sheet_membership_without_promoting_free_tickers():
    sheet = replace(DEFAULT_COMPANIES[0], origins=("Depot",), row_key="depot:2")
    companies = combine_companies(DEFAULT_COMPANIES, (sheet, FIRST))
    assert len(companies) == 5
    assert companies[0].origins == ("Standardauswahl", "Depot")
    assert companies[-1] == FIRST


def test_failed_automatic_fetch_does_not_retry_on_rerun_and_refresh_recovers(
    monkeypatch,
):
    calls = []

    def provider(company, comparisons):
        calls.append(company.symbol)
        if len(calls) == 1:
            raise RuntimeError("Synthetic provider failure")
        return demo_bundle(company, comparisons)

    def page():
        import streamlit as st

        from portfolio_dashboard.exploration import view
        from portfolio_dashboard.exploration.selection import Catalog

        def sheet(previous):
            st.session_state["sheet_reads"] = st.session_state.get("sheet_reads", 0) + 1
            return Catalog()

        view.render(
            demo=False, refresh_clicked=st.button("Aktualisieren"), load_companies=sheet
        )

    monkeypatch.setattr(view, "fetch", provider)
    app = AppTest.from_function(page).run()
    assert not app.exception and not app.get("plotly_chart")
    assert any("Abruf fehlgeschlagen" in w.value for w in app.warning)
    app.run()
    assert calls == ["MSFT"] and app.session_state["sheet_reads"] == 1
    app.button[0].click().run()
    assert not app.exception and app.get("plotly_chart")
    assert calls == ["MSFT", "MSFT"] and app.session_state["sheet_reads"] == 2
    assert not app.session_state[view.STATE + ".failures.live"]


def test_sheet_failure_keeps_defaults_available_without_retry_loop(monkeypatch):
    def page():
        import streamlit as st

        from portfolio_dashboard.exploration import view

        def sheet(previous):
            st.session_state["sheet_reads"] = st.session_state.get("sheet_reads", 0) + 1
            raise RuntimeError("Synthetic private error must not be displayed")

        view.render(demo=False, load_companies=sheet)

    monkeypatch.setattr(view, "fetch", demo_bundle)
    app = AppTest.from_function(page).run()
    assert not app.exception and app.get("plotly_chart")
    assert len(app.selectbox(key=view.COMPANY_KEY).options) == 4
    assert any("Sheet-Liste konnte nicht geladen" in w.value for w in app.warning)
    assert not any("Synthetic private error" in w.value for w in app.warning)
    app.run()
    assert app.session_state["sheet_reads"] == 1
