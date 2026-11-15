"""Public start page with synthetic market data; offline AppTest, no secrets."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_dashboard import index_members, start_view
from portfolio_dashboard.indices import DemoIndices
from portfolio_dashboard.market_overview import ASSETS, GROUPS, MarketQuote
from portfolio_dashboard.start_view import ASSETS_GROUP, build_market_figure

STAMP = datetime(2026, 10, 28, 22, 0, tzinfo=UTC)


def quotes_for(symbols, percent=Decimal("1.5")):
    closes = ((date(2026, 10, 27), Decimal(100)), (date(2026, 10, 28), Decimal(101)))
    return tuple(
        MarketQuote(item, {"Heute": percent if i else -percent, "Woche": None, "Monat": None,
                           "Jahr": Decimal(10)}, Decimal(101), date(2026, 10, 28), False, closes)
        for i, item in enumerate(symbols)
    )


def landing():
    import streamlit as st

    from portfolio_dashboard import start_view as view

    view.render(lambda: st.button("Login", key="auth.login", type="primary"))


@pytest.fixture
def requests(monkeypatch):
    seen = []

    def load(group):
        seen.append(group)
        return STAMP, quotes_for(ASSETS if group == ASSETS_GROUP else GROUPS[group])

    monkeypatch.setattr(start_view, "cached_quotes", load)
    return seen


def test_start_page_shows_login_clock_breadth_treemap_and_tiles(requests):
    app = AppTest.from_function(landing, default_timeout=30).run()
    assert not app.exception and not app.error
    assert app.header[0].value == "Märkte im Überblick"
    assert app.button(key="auth.login").label == "Login"
    labels = [metric.label for metric in app.metric]
    assert labels[:3] == ["Im Plus", "Stärkster Wert", "Schwächster Wert"]
    assert app.metric[0].value == "14 von 15"
    assert "DAX · 1 Jahr" in labels and "S&P 500 · 1 Jahr" in labels
    assert [metric.label for metric in app.metric[-4:]] == [item.name for item in ASSETS]
    assert len(app.get("plotly_chart")) == 1
    assert requests == ["Weltindizes", ASSETS_GROUP]


def test_sector_view_and_period_switch_reuse_the_shared_loader(requests):
    app = AppTest.from_function(landing, default_timeout=30).run()
    app.segmented_control(key=start_view.GROUP_KEY).set_value("Branchen Europa").run()
    assert not app.exception and app.metric[0].value == "18 von 19"
    app.segmented_control(key=start_view.PERIOD_KEY).set_value("Woche").run()
    assert not app.exception and app.metric[0].value == "0 von 19"
    assert app.metric[1].value == "NA"
    assert "Branchen Europa" in requests


def test_unavailable_market_data_keeps_login_and_shows_a_neutral_notice(monkeypatch):
    def broken(group):
        raise RuntimeError("synthetic-private-provider-error")

    monkeypatch.setattr(start_view, "cached_quotes", broken)
    app = AppTest.from_function(landing, default_timeout=30).run()
    assert not app.exception and app.button(key="auth.login")
    assert [info.value for info in app.info] == [start_view.NOT_AVAILABLE_TEXT]
    assert "synthetic-private-provider-error" not in str(app)


def index_levels():
    from portfolio_dashboard.indices_view import render_index_level

    render_index_level("DAX", "Heute")
    render_index_level("DAX", "Woche")
    render_index_level("Demo-Index", "Heute")  # no checked symbol: nothing shown


def test_index_page_level_tile_uses_the_shared_market_data(requests):
    app = AppTest.from_function(index_levels, default_timeout=30).run()
    assert not app.exception
    assert [metric.label for metric in app.metric] == ["DAX · Indexstand", "DAX · Indexstand"]
    assert app.metric[0].value == "101,00 Pkt." and app.metric[0].delta == "-1,50 %"
    assert not app.metric[1].delta  # no weekly value: no invented change
    assert requests == ["Weltindizes", "Weltindizes", "Weltindizes"]


def test_world_treemap_groups_by_region_with_equal_tiles():
    quotes = quotes_for(GROUPS["Weltindizes"])
    trace = build_market_figure(quotes, "Heute").data[0]
    assert list(trace.ids[:3]) == ["Europa", "Amerika", "Asien"]
    assert list(trace.parents[:3]) == ["", "", ""] and set(trace.parents[3:]) == {"Europa", "Amerika", "Asien"}
    assert set(trace.values[3:]) == {1}
    assert trace.customdata[3][0] == "-1,50 %" and trace.customdata[4][0] == "+1,50 %"


def test_sector_treemap_is_flat():
    trace = build_market_figure(quotes_for(GROUPS["Branchen USA"]), "Jahr").data[0]
    assert set(trace.parents) == {""} and len(trace.ids) == 11


def test_grouped_treemap_shows_the_path_bar_for_zooming_back_out():
    grouped = build_market_figure(quotes_for(GROUPS["Weltindizes"]), "Heute").data[0]
    flat = build_market_figure(quotes_for(GROUPS["Branchen USA"]), "Heute").data[0]
    assert grouped.pathbar.visible is True and flat.pathbar.visible is False


def test_fixed_members_cover_only_world_indices_with_unique_tickers():
    members = index_members.members()
    names = {item.name for item in GROUPS["Weltindizes"]}
    assert set(members) <= names and "DAX" in members
    assert len(members["DAX"]) == 40 and len(members["Dow Jones"]) == 30
    # Only the largest members, so one click stays within Yahoo's rate limit.
    assert len(members["S&P 500"]) == len(members["Nikkei 225"]) == index_members.TOP_N
    assert max(len(rows) for rows in members.values()) == index_members.TOP_N
    for rows in members.values():
        tickers = [row.symbol for row in rows]
        assert len(tickers) == len(set(tickers)) and all(tickers)
        assert all(row.name for row in rows)


def click(symbol):
    from types import SimpleNamespace

    import streamlit as st

    from portfolio_dashboard import start_view as view

    selection = SimpleNamespace(points=[{"id": symbol}] if symbol else [])
    st.session_state[view.TREEMAP_KEY] = SimpleNamespace(selection=selection)
    view._open_index()


@pytest.mark.parametrize(("symbol", "expected"), [("^GDAXI", "DAX"), ("^FTSE", None), ("Europa", None), (None, None)])
def test_tile_click_opens_only_indices_with_fixed_members(symbol, expected):
    app = AppTest.from_function(click, args=(symbol,), default_timeout=30).run()
    assert not app.exception
    assert app.query_params.get(start_view.INDEX_PARAM) == expected
    assert bool(app.toast) is (symbol == "^FTSE")


def test_index_view_shows_companies_and_returns_to_the_overview(requests, monkeypatch):
    loaded = []

    def load_index(name, now):
        loaded.append(name)
        return DemoIndices().load("Demo-Index")

    monkeypatch.setattr(start_view, "cached_index", load_index)
    app = AppTest.from_function(landing, default_timeout=30)
    app.query_params[start_view.INDEX_PARAM] = "DAX"
    app.run()
    assert not app.exception and loaded == ["DAX"]
    assert app.button(key="auth.login") and app.button(key="start.back")
    assert any("Weltindizes › DAX" in caption.value for caption in app.caption)
    assert app.subheader[0].value == "DAX · Heute" and len(app.dataframe) == 1
    assert [metric.label for metric in app.metric][:2] == ["Marktkapitalisierung", "Größter Gewinner"]

    app.button(key="start.back").click().run()
    assert not app.exception and start_view.INDEX_PARAM not in app.query_params
    assert app.metric[0].label == "Im Plus" and not app.dataframe


def test_unknown_index_parameter_shows_the_overview(requests):
    app = AppTest.from_function(landing, default_timeout=30)
    app.query_params[start_view.INDEX_PARAM] = "FTSE 100"
    app.run()
    assert not app.exception and app.metric[0].label == "Im Plus"


def _berlin(day, clock):
    from zoneinfo import ZoneInfo

    return datetime.combine(day, clock, ZoneInfo("Europe/Berlin"))


def test_index_slot_is_short_while_trading_and_long_while_closed():
    from datetime import timedelta

    from portfolio_dashboard.market_overview import market_calendars

    friday = date(2026, 11, 13)
    calendars = market_calendars(friday)
    ger = calendars["GER"]
    assert friday in ger.sessions
    opened = _berlin(friday, ger.open_time) + timedelta(minutes=1)
    later = opened + timedelta(minutes=20)
    slot = start_view.index_slot("DAX", opened, calendars)
    assert not slot.startswith("ruht") and slot != start_view.index_slot("DAX", later, calendars)
    # Shortly after the bell prices may still settle; afterwards one slot until Monday.
    bell = _berlin(friday, ger.close_time)
    assert not start_view.index_slot("DAX", bell + timedelta(minutes=5), calendars).startswith("ruht")
    evening = start_view.index_slot("DAX", bell + timedelta(hours=1), calendars)
    assert evening == start_view.index_slot("DAX", bell + timedelta(hours=3), calendars)
    assert evening == "ruht nach 2026-11-13"
    # The same slot over the weekend until Monday's open.
    saturday = _berlin(date(2026, 11, 14), ger.open_time)
    monday_early = _berlin(date(2026, 11, 16), ger.open_time) - timedelta(minutes=5)
    assert start_view.index_slot("DAX", saturday, calendars) == evening
    assert start_view.index_slot("DAX", monday_early, calendars) == evening
    # Without a calendar the short slot stays.
    assert not start_view.index_slot("DAX", saturday, {}).startswith("ruht")


def test_all_rows_without_quote_say_not_available(requests, monkeypatch):
    from dataclasses import replace

    bundle = DemoIndices().load("Demo-Index")
    empty = replace(bundle, rows=tuple(replace(row, quote=None) for row in bundle.rows))
    monkeypatch.setattr(start_view, "cached_index", lambda name, now: empty)
    app = AppTest.from_function(landing, default_timeout=30)
    app.query_params[start_view.INDEX_PARAM] = "DAX"
    app.run()
    assert not app.exception and not app.dataframe
    assert any(start_view.NOT_AVAILABLE_TEXT in info.value for info in app.info)


def test_every_fixed_member_can_be_priced():
    assert all(
        index_members.processable(member.symbol)
        for rows in index_members.members().values()
        for member in rows
    )
    assert all(len(rows) <= index_members.TOP_N for rows in index_members.members().values())


@pytest.mark.parametrize(("ticker", "expected"), [
    ("SAP.DE", True), ("BRK-B", True), ("0700.HK", True), ("RELIANCE.NS", True),
    ("M&M.NS", False), ("ABI.BR", False), ("NDA-FI.HE", False), ("XYZ.L", False),
])
def test_processable_follows_the_price_engine_rules(ticker, expected):
    assert index_members.processable(ticker) is expected


def test_midday_break_is_one_slot():
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from portfolio_dashboard.market_overview import market_calendars

    friday = date(2026, 11, 13)
    calendars = market_calendars(friday)
    item = start_view._INDEX_BY_NAME["Nikkei 225"]
    calendar = calendars[item.calendar]
    assert calendar.break_start is not None
    start = datetime.combine(friday, calendar.break_start, ZoneInfo(item.timezone))
    slots = {start_view.index_slot("Nikkei 225", start + timedelta(minutes=m), calendars) for m in (1, 30, 50)}
    assert slots == {"pause 2026-11-13"}


def test_outage_with_cached_identity_counts_as_degraded():
    from test_indices import MEMBER, market
    from test_watchlist_refresh import CLOSED

    from portfolio_dashboard.indices import IndexBundle

    provider, _, calendars = market(CLOSED, cache_info=True)
    healthy = provider.fetch(MEMBER, CLOSED, calendars)
    assert start_view.has_prices(healthy)

    def outage(self, **kwargs):
        raise ConnectionError("synthetic outage")

    provider._ticker.history = outage
    provider._ticker.get_history_metadata = outage
    row = provider.fetch(MEMBER, CLOSED, calendars)
    assert row.quote is not None and row.market_cap is not None  # identity came from the cache
    assert not start_view.has_prices(row)
    assert start_view.degraded(IndexBundle("DAX", (row,), CLOSED))
    assert start_view.degraded(IndexBundle("DAX", (healthy, row), CLOSED))
    assert not start_view.degraded(IndexBundle("DAX", (healthy,), CLOSED))


def test_degraded_load_is_retried_soon_but_a_complete_one_is_kept(monkeypatch):
    from dataclasses import replace
    from datetime import timedelta

    from portfolio_dashboard.market_overview import market_calendars

    demo = DemoIndices().load("Demo-Index")
    good = replace(demo, rows=tuple(row for row in demo.rows if start_view.has_prices(row)))
    bad = replace(good, rows=tuple(replace(row, quote=None) for row in good.rows))
    assert good.rows and not start_view.degraded(good) and start_view.degraded(bad)
    answers, loads = [bad, good], []

    class Loader:
        def __init__(self, *args, **kwargs):
            pass

        def load(self, name):
            loads.append(name)
            return answers[min(len(loads), len(answers)) - 1]

    for function in (start_view._recent_index, start_view._settled_index):
        function.clear()
    monkeypatch.setattr(start_view, "IndicesLoader", Loader)
    monkeypatch.setattr(start_view, "index_market", lambda: None)
    friday = date(2026, 11, 13)
    calendars = market_calendars(friday)
    monkeypatch.setattr(start_view, "cached_calendars", lambda day: calendars)
    evening = _berlin(friday, calendars["GER"].close_time) + timedelta(hours=2)

    assert start_view.cached_index("DAX", evening) is not None and loads == ["DAX"]
    # Same 15 minutes: the failed load is served from the short cache, no new request.
    start_view.cached_index("DAX", evening + timedelta(minutes=1))
    assert loads == ["DAX"]
    # Next slot: retried; the complete answer is then kept for the closed period.
    start_view.cached_index("DAX", evening + timedelta(minutes=16))
    assert loads == ["DAX", "DAX"]
    kept = start_view.cached_index("DAX", evening + timedelta(hours=5))
    assert loads == ["DAX", "DAX"] and not start_view.degraded(kept)
    for function in (start_view._recent_index, start_view._settled_index):
        function.clear()


def test_one_permanently_missing_member_does_not_block_the_resting_cache():
    from dataclasses import replace

    demo = DemoIndices().load("Demo-Index")
    good = tuple(row for row in demo.rows if start_view.has_prices(row))
    stale = next(row for row in demo.rows if row.quote is not None and not start_view.has_prices(row))
    many = tuple(good * 12)
    assert not start_view.degraded(replace(demo, rows=(*many, stale)))  # 1 of 49
    assert start_view.degraded(replace(demo, rows=(*good, stale)))  # 1 of 5


def test_load_from_before_the_settle_time_is_not_kept(monkeypatch):
    from datetime import timedelta

    from portfolio_dashboard.market_overview import market_calendars

    loads = []

    class Loader:
        def __init__(self, *args, **kwargs):
            pass

        def load(self, name):
            loads.append(name)
            demo = DemoIndices().load("Demo-Index")
            return type(demo)(demo.name, tuple(r for r in demo.rows if start_view.has_prices(r)), demo.fetched_at)

    for function in (start_view._recent_index, start_view._settled_index):
        function.clear()
    monkeypatch.setattr(start_view, "IndicesLoader", Loader)
    monkeypatch.setattr(start_view, "index_market", lambda: None)
    friday = date(2026, 11, 13)
    calendars = market_calendars(friday)
    monkeypatch.setattr(start_view, "cached_calendars", lambda day: calendars)
    bell = _berlin(friday, calendars["GER"].close_time)
    start_view.cached_index("DAX", bell + timedelta(minutes=16))  # settling, short slot
    start_view.cached_index("DAX", bell + timedelta(minutes=21))  # resting: fresh load
    start_view.cached_index("DAX", bell + timedelta(hours=3))
    assert loads == ["DAX", "DAX"]
    for function in (start_view._recent_index, start_view._settled_index):
        function.clear()
