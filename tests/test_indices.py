"""Synthetic Sheet and Yahoo boundaries; all IP sockets remain blocked."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from urllib.parse import unquote

import pytest
from test_watchlist_refresh import CLOSED, RUNNING, FakeYahoo, close_of

from portfolio_dashboard.config import SourceConfig, SourceKind
from portfolio_dashboard.indices import (
    Constituent,
    IndexBundle,
    IndexMarketData,
    IndexRow,
    IndexSheetsReader,
    IndexSourceError,
    IndicesLoader,
    parse_constituents,
)
from portfolio_dashboard.indices_view import build_figure, table_rows
from portfolio_dashboard.local_source import venue_calendars

HEADERS = ["Unternehmen", "Yahoo-Finance-Ticker"]
MEMBER = Constituent("Synthetische Beispiel AG", "SYN.DE")
SECRET = "synthetic-private-detail"


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload, self.closed = payload, False

    def json(self, **kwargs):
        return self.payload

    def close(self):
        self.closed = True


class Session:
    def __init__(self, payload):
        self.response, self.calls = Response(payload), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def reader(payload):
    session = Session(payload)
    source = SourceConfig(SourceKind.GOOGLE_SHEETS, spreadsheet_id="synthetic-source-id")
    return IndexSheetsReader(source, session), session


def test_reader_discovers_only_supported_index_tabs_then_reads_bounded_columns():
    source, session = reader({"sheets": [
        {"properties": {"title": title}} for title in ("Private tab", "S&P 500", "DAX")
    ]})
    assert source.list_indices() == ("DAX", "S&P 500")
    assert session.calls[0][1]["params"] == {"fields": "sheets.properties.title"}
    session.response = Response({"majorDimension": "ROWS", "values": [HEADERS, [MEMBER.name, MEMBER.symbol]]})
    assert source.read("DAX") == (MEMBER,)
    url, options = session.calls[1]
    assert unquote(url).endswith("/values/'DAX'!A1:B1000")
    assert options["params"]["valueRenderOption"] == "UNFORMATTED_VALUE"
    assert options["verify"] and not options["allow_redirects"]
    assert options["timeout"] == 10 and session.response.closed
    with pytest.raises(IndexSourceError):
        source.read("Private tab")
    assert len(session.calls) == 2


def test_parser_keeps_missing_and_duplicate_mappings_visible_without_double_weight():
    rows = parse_constituents([HEADERS, [MEMBER.name, " syn.de "], [],
                               ["Ohne Ticker"], ["Doppelte Zeile", "SYN.DE"], ["Ungültig", "=FORMULA()"]])
    assert len(rows) == 4 and rows[0] == MEMBER
    assert rows[1].symbol is None and "fehlt" in rows[1].note
    assert "doppelter" in rows[2].note
    assert rows[3].symbol is None
    assert parse_constituents([HEADERS[::-1], ["SYN.DE", MEMBER.name]]) == (MEMBER,)


@pytest.mark.parametrize("payload", [None, {}, [["Wrong", "Headers"]], [HEADERS, ["a", "b", "c"]], [HEADERS, [{}]]])
def test_invalid_sheet_payload_never_becomes_a_partial_index(payload):
    with pytest.raises(IndexSourceError):
        parse_constituents(payload)


def test_source_failures_are_neutral_and_close_response():
    source, session = reader({"private": SECRET})
    session.response.status_code = 403
    with pytest.raises(IndexSourceError) as exc:
        source.list_indices()
    assert SECRET not in str(exc.value) and "synthetic-source-id" not in str(exc.value)
    assert session.response.closed


def market(now=CLOSED, info_overrides=None, history_overrides=None):
    calendars = venue_calendars(now.date())
    yahoo = FakeYahoo(calendars, lambda: now)
    yahoo.add("SYN.DE", "GER", "EUR", **(history_overrides or {}))
    class Ticker:
        def __init__(self, symbol):
            self.inner = yahoo.factory(symbol)

        def get_info(self):
            return {
                "symbol": "SYN.DE", "exchange": "GER", "currency": "EUR",
                "quoteType": "EQUITY", "exchangeTimezoneName": "Europe/Berlin",
                "marketCap": 12_000_000_000, **(info_overrides or {}),
            }

        def history(self, **kwargs):
            return self.inner.history(**kwargs)

        def get_history_metadata(self):
            return self.inner.get_history_metadata()
    return IndexMarketData(Ticker), yahoo, calendars


@pytest.mark.parametrize("now", [CLOSED, RUNNING])
def test_real_price_engine_and_cap_with_synthetic_yahoo(now):
    provider, yahoo, calendars = market(now)
    row = provider.fetch(MEMBER, now, calendars)
    assert row.market_cap == Decimal(12_000_000_000) and row.currency == "EUR"
    assert row.quote is not None
    assert all(value.percent is not None for value in row.quote.periods)
    expected_price = Decimal("111.11") if now == RUNNING else Decimal(str(close_of(now.date())))
    assert row.quote.price == expected_price
    previous = Decimal(str(close_of(now.date() - timedelta(days=1))))
    assert row.percent("Heute") == (expected_price - previous) / previous * 100
    assert len(yahoo.quote_calls("SYN.DE")) == (1 if now == RUNNING else 0)
    assert len(yahoo.history_calls("SYN.DE")) == 1


@pytest.mark.parametrize("overrides", [
    {"symbol": "OTHER.DE"}, {"quoteType": "ETF"}, {"currency": "GBp"},
    {"exchangeTimezoneName": "America/New_York"}, {"exchange": None},
])
def test_unconfirmed_identity_never_gets_a_cap_or_price(overrides):
    provider, yahoo, calendars = market(info_overrides=overrides)
    row = provider.fetch(MEMBER, CLOSED, calendars)
    assert row.market_cap is None and row.quote is None and "NA" in row.note
    assert not yahoo.calls


@pytest.mark.parametrize("value", [None, 0, -5, float("nan"), float("inf"), True, "12000000"])
def test_missing_or_invalid_cap_does_not_hide_valid_quotes(value):
    provider, _, calendars = market(info_overrides={"marketCap": value})
    row = provider.fetch(MEMBER, CLOSED, calendars)
    assert row.market_cap is None and row.percent("Heute") is not None


def test_conflicting_history_metadata_rejects_the_entire_company_value():
    provider, _, calendars = market(history_overrides={"currency": "USD"})
    row = provider.fetch(MEMBER, CLOSED, calendars)
    assert row.market_cap is None and row.percent("Heute") is None
    assert "widersprechen" in row.note


def test_unsupported_exchange_shows_cap_but_does_not_invent_calendar():
    provider, yahoo, calendars = market(info_overrides={"exchange": "OTHER"})
    row = provider.fetch(MEMBER, CLOSED, calendars)
    assert row.market_cap is not None and row.quote is None
    assert "wird noch angebunden" in row.note and not yahoo.calls


class Source:
    def __init__(self):
        self.reads, self.fail = [], False

    def list_indices(self):
        return ("DAX", "MDAX")

    def read(self, name):
        self.reads.append(name)
        if self.fail:
            raise IndexSourceError("Index-Daten nicht verfügbar")
        return (MEMBER,)


class Market:
    def __init__(self):
        self.calls = []

    def fetch(self, member, now, calendars):
        self.calls.append(member)
        return IndexRow(member, market_cap=Decimal(500), currency="EUR")


def test_loading_is_lazy_per_index_and_refresh_failure_keeps_atomic_previous_stand():
    source, provider = Source(), Market()
    now = [CLOSED]
    loader = IndicesLoader(source, provider, clock=lambda: now[0])
    assert loader.list_indices() == ("DAX", "MDAX") and not provider.calls
    dax = loader.load("DAX")
    assert loader.load("DAX") is dax and source.reads == ["DAX"]
    loader.load("MDAX")
    source.fail = True
    failed = loader.load("DAX", refresh=True)
    assert failed.rows is dax.rows and failed.fetched_at == CLOSED
    assert failed.warning and failed.available
    now[0] += timedelta(days=1)
    assert loader.load("DAX").warning
    fresh_loader = IndicesLoader(source, provider, clock=lambda: now[0])
    assert not fresh_loader.load("DAX").available
    source.fail = False
    assert loader.load("DAX", refresh=True).fetched_at == now[0]


def test_missing_rows_skip_provider_and_do_not_expose_transport_errors():
    calls = []
    def broken(symbol):
        calls.append(symbol)
        raise RuntimeError(SECRET)
    provider = IndexMarketData(broken)
    calendars = venue_calendars(CLOSED.date())
    assert SECRET not in provider.fetch(MEMBER, CLOSED, calendars).note
    missing = Constituent("Ohne Zuordnung", None, "NA · Ticker fehlt")
    duplicate = replace(MEMBER, note="NA · doppelt")
    assert provider.fetch(missing, CLOSED, calendars).market_cap is None
    assert provider.fetch(duplicate, CLOSED, calendars).market_cap is None
    assert calls == [MEMBER.symbol]


def test_caps_are_never_added_across_currencies_and_missing_rows_stay_in_table():
    rows = (IndexRow(MEMBER, market_cap=Decimal(100), currency="EUR"),
            IndexRow(Constituent("Zweites Beispiel", "SECOND"), market_cap=Decimal(50), currency="USD"),
            IndexRow(Constituent("Noch offen", None)))
    mixed = IndexBundle("DAX", rows, CLOSED)
    assert mixed.total_cap is None and mixed.cap_currency is None
    assert tuple(build_figure(mixed, "Heute").data[0].values) == (1, 1, 1)
    homogeneous = replace(mixed, rows=(rows[0], replace(rows[1], currency="EUR"), rows[2]))
    assert homogeneous.total_cap == 150
    assert tuple(build_figure(homogeneous, "Heute").data[0].values) == (100, 50)
    assert len(table_rows(homogeneous, "Heute")) == 3
    assert table_rows(homogeneous, "Heute")[2]["Marktkapitalisierung"] is None
    assert list(table_rows(homogeneous, "Heute")[0])[1] == "Kursentwicklung (%)"


def test_hover_labels_escape_sheet_html_and_zero_return_remains_a_number():
    provider, _, calendars = market()
    row = provider.fetch(replace(MEMBER, name="<b>Synthetic</b>"), CLOSED, calendars)
    periods = tuple(replace(value, percent=Decimal(0)) for value in row.quote.periods)
    row = replace(row, quote=replace(row.quote, periods=periods))
    bundle = IndexBundle("DAX", (row,), CLOSED)
    chart = build_figure(bundle, "Heute").data[0]
    assert chart.labels == ("&lt;b&gt;Synthetic&lt;/b&gt;",)
    assert chart.customdata[0][1] == "0,00 %"
    assert table_rows(bundle, "Heute")[0]["Kursentwicklung (%)"] == 0
