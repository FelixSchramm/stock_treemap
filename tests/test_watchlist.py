"""Synthetic Sheet-watchlist membership cases (#95); no Sheet, provider or storage."""

import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

import portfolio_dashboard.watchlist as watchlist
from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    Position,
    ProviderMapping,
    QuoteUnit,
)
from portfolio_dashboard.sheet_parser import NumberLocale
from portfolio_dashboard.watchlist import (
    IdentityStatus,
    WatchlistMembership,
    link_portfolio,
)
from portfolio_dashboard.watchlist_sheet import (
    BASIC_HEADERS,
    OPTIONAL_HEADERS,
    WatchlistRowStatus,
    parse_watchlist_rows,
)

IMPORTED = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
HEADERS = (*BASIC_HEADERS, *OPTIONAL_HEADERS)
EUR = QuoteUnit("EUR", Decimal(1))


def listing(
    instrument_id="synthetic-share-a",
    symbol="SYNA.DE",
    venue="GER",
    unit=EUR,
    ticker="SYNA",
):
    return VerifiedListing(
        Instrument(
            InstrumentType.STOCK,
            instrument_id=instrument_id,
            display_ticker=ticker,
            trading_venue=venue,
            quote_unit=unit,
            provider_mapping=ProviderMapping("yfinance", symbol),
        ),
        equity_form=EquityForm.SHARE,
        isin="synthetic-isin-" + instrument_id,
    )


def etf_listing(symbol="SYNE.DE", venue="GER"):
    return VerifiedListing(
        Instrument(
            InstrumentType.ETF,
            instrument_id="synthetic-etf",
            trading_venue=venue,
            quote_unit=EUR,
            provider_mapping=ProviderMapping("yfinance", symbol),
        ),
        isin="synthetic-isin-etf",
        share_class="synthetic-class",
    )


def row(
    name="Synthetik AG",
    kind="Aktie",
    symbol="SYNA.DE",
    exchange="GER",
    currency="EUR",
    ticker="SYNA",
):
    return [name, "12,50", "1,5", "0,18", ticker, kind, symbol, exchange, currency, ""]


def items(*rows):
    result = parse_watchlist_rows(
        HEADERS, rows, imported_at=IMPORTED, number_locale=NumberLocale.GERMAN
    )
    assert len(result.snapshot.items) == len(rows)
    return result.snapshot.items


def position(source, name="Synthetische Position"):
    return Position(
        source_row=2,
        name=name,
        instrument=source.instrument,
        quantity=Decimal(7),
        category=None,
        imported_at=IMPORTED,
    )


def test_portfolio_only_creates_no_watchlist_rows_and_changes_nothing():
    a = listing()
    positions = (position(a),)
    original = positions[0]
    assert link_portfolio((), positions, (a,)) == ()
    assert positions[0] is original and positions[0].quantity == Decimal(7)


def test_watchlist_only_verified_stock_gets_the_registry_key_without_portfolio():
    a, b = listing(), listing("synthetic-share-b", "SYNB.DE", ticker="SYNB")
    [link] = link_portfolio(items(row()), (), (a, b))
    assert link.status is IdentityStatus.VERIFIED
    assert link.valuation_key == "synthetic-share-a"
    assert link.in_portfolio is False
    assert link.listing is a and link.instrument is a.instrument
    assert "bestätigt" in link.explanation


def test_overlap_shares_one_key_across_listings_without_touching_positions():
    xetra = listing()
    frankfurt = listing(symbol="SYNA.F", venue="FRA")
    other_class = listing("synthetic-share-b", "SYNB.DE", ticker="SYNB")
    positions = (position(xetra),)
    original = positions[0]
    rows = items(
        row(),  # the portfolio's own listing
        row(symbol="SYNA.F", exchange="FRA"),  # same verified security, other venue
        row("Klasse B", symbol="SYNB.DE"),
        row("Nur Blatt", kind="", symbol="", exchange="", currency=""),
    )
    links = link_portfolio(rows, positions, (xetra, frankfurt, other_class))
    assert [link.in_portfolio for link in links] == [True, True, False, False]
    assert [link.valuation_key for link in links] == [
        "synthetic-share-a",
        "synthetic-share-a",
        "synthetic-share-b",
        None,
    ]
    portfolio_key = frankfurt.instrument.instrument_id
    assert links[0].valuation_key == links[1].valuation_key == portfolio_key
    assert positions[0] is original and positions[0].quantity == Decimal(7)


def test_overlap_resolves_the_same_fundamental_report_as_the_portfolio():
    from test_fundamentals import identity, report_set

    from portfolio_dashboard.fundamentals import FundamentalRegistry

    checked, report = identity(), report_set()
    base = checked.listing.instrument
    xetra = replace(
        checked.listing,
        instrument=replace(
            base,
            trading_venue="GER",
            quote_unit=EUR,
            provider_mapping=ProviderMapping("yfinance", "SYNA.DE"),
        ),
    )
    frankfurt = replace(
        xetra,
        instrument=replace(
            xetra.instrument,
            trading_venue="FRA",
            provider_mapping=ProviderMapping("yfinance", "SYNA.F"),
        ),
    )
    registry = FundamentalRegistry(
        (
            checked,
            replace(checked, listing=xetra),
            replace(checked, listing=frankfurt),
        ),
        (report,),
    )
    [link] = link_portfolio(
        items(row(symbol="SYNA.F", exchange="FRA")), (position(xetra),), (xetra, frankfurt)
    )
    assert link.in_portfolio and link.valuation_key == base.instrument_id
    assert registry.resolve(link.instrument) is report
    assert registry.resolve(position(xetra).instrument) is report


@pytest.mark.parametrize(
    "kwargs",
    [
        {"symbol": "NONE.DE"},  # symbol not in the registry
        {"symbol": "SYNA.F", "exchange": "FRA"},  # venue not verified for it
        {"currency": "USD"},  # registry unit is EUR
        {"kind": "ETF"},  # Typ contradicts the verified stock
    ],
    ids=["symbol", "venue", "currency", "type"],
)
def test_unmatched_candidates_stay_visible_without_key(kwargs):
    # Candidates the parser accepts (MAPPED) but the registry does not confirm.
    [parsed] = items(row(**kwargs))
    assert parsed.status is WatchlistRowStatus.MAPPED
    [link] = link_portfolio((parsed,), (), (listing(),))
    assert link.item is parsed
    assert link.valuation_key is None and link.in_portfolio is False
    assert link.instrument is None
    assert link.status is IdentityStatus.UNMATCHED
    assert "ungeprüft" in link.explanation


def test_rows_without_a_complete_candidate_never_receive_a_key():
    a = listing()
    sheet = items(
        # Same name/ticker as the verified stock, but no Yahoo columns at all.
        row("Synthetik AG", kind="", symbol="", exchange="", currency=""),
        # Contradictory hint: parser falls back to the Sheet value.
        row(symbol="SYNA.DE", exchange="FRA"),
        # Derivatives never carry a mapping.
        row(kind="Derivat"),
        # Incomplete hint.
        row(exchange=""),
    )
    assert [i.status for i in sheet] == [
        WatchlistRowStatus.SHEET_ONLY,
        WatchlistRowStatus.FALLBACK,
        WatchlistRowStatus.FALLBACK,
        WatchlistRowStatus.FALLBACK,
    ]
    links = link_portfolio(sheet, (position(a),), (a,))
    assert len(links) == 4
    assert {link.status for link in links} == {IdentityStatus.NO_CANDIDATE}
    assert all(link.valuation_key is None and not link.in_portfolio for link in links)
    assert [link.item for link in links] == list(sheet)  # all visible, row order


def test_name_ticker_and_row_number_never_establish_identity():
    a = listing(ticker="SYNA")
    [link] = link_portfolio(
        items(row(symbol="OTHER.DE", ticker="SYNA", name=a.instrument.instrument_id)),
        (position(a),),
        (a,),
    )
    assert link.valuation_key is None and link.status is IdentityStatus.UNMATCHED


def test_blank_type_is_accepted_but_a_matching_type_is_too():
    a = listing()
    for kind in ("", "Aktie", "aktie"):
        [link] = link_portfolio(items(row(kind=kind)), (), (a,))
        assert link.valuation_key == "synthetic-share-a"


def test_registry_unit_must_agree_with_the_sheet_currency():
    for unit in (QuoteUnit("USD", Decimal(1)), QuoteUnit("EUR", Decimal("0.01"))):
        [link] = link_portfolio(items(row()), (), (listing(unit=unit),))
        assert link.valuation_key is None and link.status is IdentityStatus.UNMATCHED


def test_verified_etf_is_marked_but_has_no_valuation_key():
    etf = etf_listing()
    [link] = link_portfolio(
        items(row("Synthetischer ETF", kind="ETF", symbol="SYNE.DE", ticker="SYNE")),
        (position(etf),),
        (etf,),
    )
    assert link.status is IdentityStatus.VERIFIED_NOT_STOCK
    assert link.in_portfolio is True and link.valuation_key is None
    assert link.instrument is etf.instrument
    assert "keine Aktie" in link.explanation


def test_us_candidate_matches_only_with_exact_venue_symbol_and_currency():
    us = listing("synthetic-us", "SYNU", "NMS", QuoteUnit("USD", Decimal(1)), "SYNU")
    usd = row("US Synthetik", symbol="SYNU", exchange="NMS", currency="USD", ticker="SYNU")
    [match] = link_portfolio(items(usd), (), (us,))
    assert match.valuation_key == "synthetic-us"
    [other_exchange] = link_portfolio(
        items(row("US Synthetik", symbol="SYNU", exchange="NYQ", currency="USD")),
        (),
        (us,),
    )
    assert other_exchange.valuation_key is None


def test_an_adr_or_other_class_keeps_its_own_identity():
    share, adr = listing(), listing("synthetic-adr", "SYNA", "NMS", QuoteUnit("USD", Decimal(1)))
    adr = replace(adr, equity_form=EquityForm.ADR)
    links = link_portfolio(
        items(row(), row(symbol="SYNA", exchange="NMS", currency="USD")),
        (position(share),),
        (share, adr),
    )
    assert [link.valuation_key for link in links] == ["synthetic-share-a", "synthetic-adr"]
    assert [link.in_portfolio for link in links] == [True, False]


def test_two_hundred_fifty_rows_keep_order_and_all_rows():
    a = listing()
    rows = [row(f"Synthetik {n}", kind="", symbol="", exchange="", currency="") for n in range(249)]
    rows.insert(100, row())
    links = link_portfolio(items(*rows), (position(a),), (a,))
    assert len(links) == 250
    assert [bool(link.valuation_key) for link in links].count(True) == 1
    assert links[100].valuation_key == "synthetic-share-a"
    assert [link.item.source_row for link in links] == sorted(
        link.item.source_row for link in links
    )


def test_inputs_are_validated_and_the_registry_must_be_consistent():
    a = listing()
    with pytest.raises(TypeError):
        link_portfolio([], (), ())
    with pytest.raises(TypeError):
        link_portfolio((object(),), (), ())
    with pytest.raises(TypeError):
        link_portfolio((), [position(a)], (a,))
    with pytest.raises(TypeError):
        link_portfolio((), (), [a])
    clashing = replace(
        a, instrument=replace(a.instrument, trading_venue="FRA")
    )  # same provider symbol on a second listing
    with pytest.raises(ValueError):
        link_portfolio(items(row()), (), (a, clashing))


def test_empty_registry_leaves_every_row_visible_and_unkeyed():
    links = link_portfolio(items(row(), row(symbol="SYNB.DE")), (), ())
    assert [link.status for link in links] == [IdentityStatus.UNMATCHED] * 2
    assert [link.valuation_key for link in links] == [None, None]


def test_representations_do_not_expose_watchlist_values():
    a = listing()
    [link] = link_portfolio(items(row("Geheime Bezeichnung")), (), (a,))
    text = repr(link)
    for value in ("Geheime Bezeichnung", "synthetic-share-a", "SYNA"):
        assert value not in text
    assert isinstance(link, WatchlistMembership)


def test_there_is_no_application_maintained_watchlist_store_any_more():
    for name in ("WatchlistRepository", "WatchlistEntry", "WatchlistConflict", "MappingStatus"):
        assert not hasattr(watchlist, name)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, portfolio_dashboard.watchlist\n"
            "bad = {'sqlite3', 'streamlit', 'portfolio_dashboard.private_store'} & set(sys.modules)\n"
            "assert not bad, bad\n"
            "print('pure-ok')",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "pure-ok"
