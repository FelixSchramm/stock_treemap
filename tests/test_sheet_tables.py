"""Journal tables and batch reader (ADR 0012) with synthetic rows only."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from portfolio_dashboard.config import SourceConfig, SourceKind
from portfolio_dashboard.instruments import EquityForm, resolve_listing
from portfolio_dashboard.models import (
    CostBasisStatus,
    InstrumentType,
    SnapshotStatus,
    ValueStatus,
)
from portfolio_dashboard.sheet_tables import (
    INSTRUMENT_COLUMNS,
    PRICE_COLUMNS,
    TABLE_RANGES,
    TRANSACTION_COLUMNS,
    TableSchemaError,
    build_portfolio,
    parse_day,
    parse_tables,
)
from portfolio_dashboard.sheets_adapter import JournalSheetsReader

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)
VENUES = {"GER": "EUR", "FRA": "EUR"}
STOCK, ETF, DERIV, LOOSE = "XX0000000001", "XX0000000002", "XX0000000003", "XX0000000004"


def instruments(*rows):
    return [list(INSTRUMENT_COLUMNS), *rows]


def stock(isin=STOCK, symbol="SYN.DE", venue="GER", currency="EUR", verified=46334, form="share"):
    return [isin, "Synthetik AG", "Aktie", form, "", "", "Tech", "ETR:SYN", symbol, venue,
            currency, verified]


def journal(*rows):
    return [list(TRANSACTION_COLUMNS), *rows]


def buy(txn_id, isin, qty, amount, day=46000, kind="Kauf", ref=""):
    return [txn_id, day, isin, kind, qty, "", "", "", amount, ref, "", ""]


def prices(*rows):
    return [list(PRICE_COLUMNS), *rows]


def portfolio(instrument_rows, transaction_rows, price_rows=()):
    tables = parse_tables(instruments(*instrument_rows), journal(*transaction_rows),
                          prices(*price_rows))
    return build_portfolio(tables, imported_at=NOW, venues=VENUES)


def test_held_stock_gets_listing_cost_basis_and_sheet_fallback():
    result = portfolio(
        [stock()],
        [buy("T1", STOCK, 10, 1000), buy("T2", STOCK, 4, "", kind="Verkauf")[:8] + [500, "", "", ""]],
        [[STOCK, 46330, "123,5"]],
    )
    (position,) = result.snapshot.positions
    assert result.snapshot.status is SnapshotStatus.COMPLETE
    assert position.quantity == 6 and position.name == "Synthetik AG"
    assert position.instrument.instrument_id == STOCK
    assert position.instrument.trading_venue == "GER"
    assert position.instrument.display_ticker == "SYN"
    assert position.cost_basis.status is CostBasisStatus.VERIFIED_REMAINING
    assert position.cost_basis.amount_eur == 600
    assert position.valuation.price == Decimal("123.5")
    assert position.valuation.provenance.observed_at.date() == date(2026, 11, 4)
    (listing,) = result.listings
    assert listing.equity_form is EquityForm.SHARE
    assert listing.instrument.provider_mapping.symbol == "SYN.DE"
    assert resolve_listing(position.instrument, result.listings) == listing


def test_unverified_or_unsupported_listings_give_no_registry_entry():
    for row in (stock(verified=""), stock(venue="NMS", currency="USD", symbol="SYN")):
        result = portfolio([row], [buy("T1", STOCK, 1, 10)])
        assert result.listings == ()
        assert result.snapshot.positions[0].instrument.trading_venue is None


def test_incomplete_or_duplicate_listings_are_dropped_with_row_warnings():
    result = portfolio([stock(form="")], [])
    assert result.listings == () and result.snapshot.warnings[0].code == "incomplete_listing"
    result = portfolio([stock(), stock(isin=ETF)[:2] + ["ETF", "", "Acc"] + stock()[5:]], [])
    assert result.listings == ()
    assert {w.code for w in result.snapshot.warnings} == {"duplicate_yahoo_symbol"}


def test_etf_needs_share_class_and_derivatives_never_get_listings():
    etf = [ETF, "Synthetik ETF (Acc)", "ETF", "", "Acc", "", "ETF", "", "SYNE.DE", "GER", "EUR", 46334]
    deriv = [DERIV, "Synthetik Long (Open-End)", "Derivat", "", "", STOCK, "", "", "SYNL.DE", "GER",
             "EUR", 46334]
    result = portfolio([etf, deriv], [])
    assert [listing.instrument.instrument_type for listing in result.listings] == [InstrumentType.ETF]


def test_zero_holdings_are_skipped_and_missing_price_stays_missing():
    result = portfolio(
        [stock(), [ETF, "Ohne Kurs", "Aktie", "share", "", "", "", "", "", "", "", ""]],
        [buy("T1", STOCK, 1, 10), [*buy("T2", STOCK, 1, "")[:3], "Verkauf", 1, "", "", "", 12, "", "", ""],
         buy("T3", ETF, 2, 20)],
    )
    (position,) = result.snapshot.positions
    assert position.name == "Ohne Kurs"
    assert position.valuation.status is ValueStatus.MISSING


def test_undated_formula_price_has_unknown_time_and_dated_entry_wins():
    result = portfolio([stock()], [buy("T1", STOCK, 1, 10)], [[STOCK, "", 99], [STOCK, 46320, 50]])
    assert result.snapshot.positions[0].valuation.price == 50
    result = portfolio([stock()], [buy("T1", STOCK, 1, 10)], [[STOCK, "", 99], [STOCK, "", ""]])
    valuation = result.snapshot.positions[0].valuation
    assert valuation.price == 99 and valuation.provenance.observed_at is None
    assert valuation.warnings[0].code == "unknown_price_time"


def test_bad_transaction_row_makes_that_isin_unavailable_and_snapshot_partial():
    result = portfolio(
        [stock(), [ETF, "Andere", "Aktie", "share", "", "", "", "", "", "", "", ""]],
        [buy("T1", STOCK, 1, 10), buy("T2", STOCK, "x", 10), buy("T3", ETF, 1, 10)],
    )
    assert result.snapshot.status is SnapshotStatus.PARTIAL
    assert [p.name for p in result.snapshot.positions] == ["Andere"]
    codes = {w.code for w in result.snapshot.warnings}
    assert {"invalid_transaction", "journal_unavailable"} <= codes


def test_journal_isin_without_instrument_row_is_reported():
    result = portfolio([], [buy("T1", STOCK, 1, 10)])
    assert result.snapshot.status is SnapshotStatus.PARTIAL
    assert result.snapshot.warnings[0].code == "unknown_isin"


def test_invalid_instrument_rows_and_duplicates_are_ignored_without_values_in_warnings():
    result = portfolio([["kein-isin", "Name", "Aktie"] + [""] * 9, stock(), stock()], [])
    codes = [w.code for w in result.snapshot.warnings]
    assert codes == ["invalid_instrument", "duplicate_isin"]
    assert all("kein-isin" not in w.message for w in result.snapshot.warnings)


def test_schema_errors_name_table_and_column():
    with pytest.raises(TableSchemaError, match="transaktionen: Spalte 'art'"):
        parse_tables(instruments(), [["txn_id", "datum", "isin"]], prices())
    with pytest.raises(TableSchemaError, match="Überschriftenzeile"):
        parse_tables([], journal(), prices())


def test_dates_accept_serials_german_and_iso_text():
    assert parse_day(46334) == parse_day("08.11.2026") == parse_day("2026-11-08") == date(2026, 11, 8)
    assert parse_day(True) is None and parse_day("8/11/2026") is None


class Response:
    def __init__(self, payload, status=200):
        self.status_code, self.payload, self.closed = status, payload, False

    def json(self, **kwargs):
        return self.payload

    def close(self):
        self.closed = True


class Session:
    def __init__(self, response):
        self.response, self.calls = response, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


SOURCE = SourceConfig(SourceKind.GOOGLE_SHEETS, "synthetic-source")


def payload(*tables):
    return {"valueRanges": [{"majorDimension": "ROWS", "values": t} for t in tables]}


def test_reader_issues_one_bounded_batch_get_and_builds_the_depot():
    session = Session(Response(payload(instruments(stock()), journal(buy("T1", STOCK, 2, 20)),
                                       prices())))
    result = JournalSheetsReader(SOURCE, session, VENUES).read(attempted_at=NOW)
    (url, kwargs), = session.calls
    assert "/values:batchGet?" in url and url.count("ranges=") == len(TABLE_RANGES)
    assert kwargs["allow_redirects"] is False and kwargs["verify"] is True
    assert kwargs["params"]["valueRenderOption"] == "UNFORMATTED_VALUE"
    assert result.snapshot.positions[0].quantity == 2 and len(result.listings) == 1
    assert session.response.closed


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (Response({}, status=403), "sheet_permission_denied"),
        (Response({"valueRanges": []}), "sheet_invalid_response"),
        (Response({"valueRanges": [{"values": [[{"x": 1}]]}] * 3}), "sheet_invalid_response"),
        (Response(payload([["isin"]], journal(), prices())), "sheet_schema_error"),
        (ConnectionError("synthetic-private-detail"), "sheet_transport_error"),
    ],
)
def test_reader_failures_are_neutral(response, code):
    result = JournalSheetsReader(SOURCE, Session(response), VENUES).read(attempted_at=NOW)
    assert result.snapshot.status is SnapshotStatus.FAILED and result.listings == ()
    assert result.snapshot.warnings[0].code == code
    assert "synthetic-private-detail" not in repr(result) + result.snapshot.warnings[0].message
