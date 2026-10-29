"""Valuation overview (#79): rows, filters, coverage; invented data, offline.

The demo fixtures of ``fundamentals_demo`` / ``valuation_demo`` serve as inputs:
one verified security held in the depot (Frankfurt listing) and observed on the
Watchlist (Xetra listing), further Watchlist rows in other states, one ETF each.
Expected figures are derived by hand from the synthetic example in
docs/INTRINSIC_VALUE.md (model values 106,98 / 135,15 / 192,86 per share).
"""

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from portfolio_dashboard.demo import (
    DEMO_TIMESTAMP,
    demo_watchlist_bundle,
    demo_watchlist_entries,
)
from portfolio_dashboard.fundamentals_demo import (
    DEMO_VALUED_ID,
    SyntheticFundamentalsProvider,
    demo_fundamentals_loader,
    demo_registry,
    demo_subjects,
)
from portfolio_dashboard.fundamentals_loader import FetchGate, FundamentalsLoader
from portfolio_dashboard.fundamentals_cache import MemoryFundamentalsStore
from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.intrinsic_models import Scenario
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    ProviderMapping,
    QuoteUnit,
)
from portfolio_dashboard.scenarios import SavedScenario
from portfolio_dashboard.sheet_parser import NumberLocale
from portfolio_dashboard.valuation_demo import (
    demo_overview_valuation,
    demo_valuation_source,
)
from portfolio_dashboard.valuation_overview import (
    MEMBERSHIP_BOTH,
    MEMBERSHIP_PORTFOLIO,
    MEMBERSHIP_WATCHLIST,
    SCOPE_ALL,
    SCOPE_PORTFOLIO,
    SCOPE_WATCHLIST,
    SHOW_UNVALUED,
    SHOW_VALUED,
    SORT_BY_DISCOUNT,
    SORT_BY_DISTANCE,
    SORT_BY_NAME,
    SORT_BY_TICKER,
    EvaluationContext,
    FundamentalsState,
    Status,
    build_rows,
    collect_subjects,
    coverage,
    filter_rows,
    sort_rows,
    verified_instruments,
)
from portfolio_dashboard.valuation_view import load_fundamentals
from portfolio_dashboard.watchlist_quotes import demo_entry, sheet_entry
from portfolio_dashboard.watchlist_sheet import (
    BASIC_HEADERS,
    OPTIONAL_HEADERS,
    parse_watchlist_rows,
)

D = Decimal
AS_OF = DEMO_TIMESTAMP
WIDE = (*BASIC_HEADERS, *OPTIONAL_HEADERS)
MODEL = {
    Scenario.CAUTIOUS: D("106.97996962"),
    Scenario.BASE: D("135.15009531"),
    Scenario.OPTIMISTIC: D("192.86377604"),
}


def evaluate(
    portfolio="demo",
    entries="demo",
    *,
    listings=None,
    loader=None,
    mutate=None,
    state=None,
    extra_docs=None,
):
    """Rows for the given inputs; ``mutate`` may change the stored documents."""
    source = demo_valuation_source()
    portfolio = source.portfolio if portfolio == "demo" else portfolio
    entries = demo_watchlist_entries() if entries == "demo" else entries
    registry = source.listings if listings is None else listings
    subjects = collect_subjects(portfolio, entries, registry)
    loader = loader or demo_fundamentals_loader()
    loaded = load_fundamentals(source, loader, subjects, refresh=False)
    documents = dict(loaded.documents)
    if mutate is not None:
        documents = {
            key: None if doc is None else mutate(doc) for key, doc in documents.items()
        }
    documents.update(extra_docs or {})
    context = EvaluationContext(
        registry,
        AS_OF,
        state or FundamentalsState(loaded.bundle, True, loaded.error),
        documents,
        demo=True,
    )
    return build_rows(subjects, context)


def by_name(rows):
    return {row.name: row for row in rows}


def values(row):
    return tuple(view.value for view in row.scenarios)


def watchlist_items(rows):
    return parse_watchlist_rows(
        WIDE, rows, imported_at=DEMO_TIMESTAMP, number_locale=NumberLocale.GERMAN
    ).snapshot.items


# --- membership: portfolio only, Watchlist only, overlap, empty -----------------------


def test_overlap_is_one_row_flagged_in_depot_and_on_watchlist():
    rows = by_name(evaluate())
    row = rows["Demo-Aktie A"]
    assert row.in_portfolio and row.on_watchlist
    assert row.membership == MEMBERSHIP_BOTH == "im Depot und auf Watchlist"
    assert [r.name for r in rows.values()].count("Demo-Aktie A") == 1
    assert "Demo-Aktie DE" not in rows  # the Watchlist row is the same security


def test_same_security_has_identical_model_values_in_every_scope():
    everything = evaluate()
    portfolio_only = by_name(evaluate(entries=()))["Demo-Aktie A"]
    watchlist_only = by_name(evaluate(portfolio=None))["Demo-Aktie DE"]
    overlap = by_name(everything)["Demo-Aktie A"]
    assert portfolio_only.membership == MEMBERSHIP_PORTFOLIO
    assert watchlist_only.membership == MEMBERSHIP_WATCHLIST
    for scenario, expected in MODEL.items():
        found = {
            row.scenarios[list(MODEL).index(scenario)].value
            for row in (portfolio_only, watchlist_only, overlap)
        }
        assert len(found) == 1
        assert abs(found.pop() - expected) < D("1E-7")
    # Filtering by scope keeps the one valuation of the overlap row.
    assert filter_rows(everything, scope=SCOPE_PORTFOLIO)[0] == overlap
    assert overlap in filter_rows(everything, scope=SCOPE_WATCHLIST)


def test_watchlist_row_needs_neither_cost_basis_nor_quantity():
    row = by_name(evaluate(portfolio=None))["Demo-Aktie DE"]
    assert row.status is Status.VALUED
    assert row.price_label == "45,20 EUR" and "Demo-Wert" in row.price_source
    text = " ".join(row.detail_lines())
    assert "Stück" not in text and "Einstand" not in text and "Anteil" not in text


def test_empty_lists_give_no_rows_and_a_defined_coverage():
    rows = evaluate(portfolio=None, entries=())
    assert rows == ()
    figures = coverage(rows)
    assert figures.considered == 0 and figures.coverage_label == "n. v."
    assert filter_rows(rows, scope=SCOPE_ALL) == ()
    assert sort_rows(rows, SORT_BY_DISCOUNT) == ()


def test_every_entry_stays_visible_nothing_is_dropped():
    rows = evaluate()
    assert [r.name for r in rows] == [
        "Demo-Aktie A",
        "Demo-Fonds B",
        "Demo-ETF DE",
        "Demo-Aktie US",
        "Demo-Aktie NYSE",
        "Demo-Krypto",
        "Demo-ohne Kurs",
    ]


# --- statuses: unvaluable stocks keep a reason ---------------------------------------------


def test_unvaluable_entries_state_why_without_inventing_a_value():
    rows = by_name(evaluate())
    states = {name: row.status for name, row in rows.items()}
    assert states["Demo-Aktie A"] is Status.VALUED
    assert states["Demo-Aktie US"] is Status.ASSUMPTIONS_MISSING
    assert states["Demo-Aktie NYSE"] is Status.UNSUPPORTED
    assert states["Demo-Krypto"] is Status.IDENTITY_MISSING
    assert states["Demo-ohne Kurs"] is Status.IDENTITY_MISSING
    assert states["Demo-Fonds B"] is Status.EXCLUDED
    assert states["Demo-ETF DE"] is Status.EXCLUDED
    for name in ("Demo-Aktie US", "Demo-Aktie NYSE", "Demo-Krypto", "Demo-Fonds B"):
        row = rows[name]
        assert values(row) == (None, None, None) and not row.has_model_value
        assert row.reasons and not row.covered
        assert row.target_label == "n. v."
    assert "Annahmen fehlen" in rows["Demo-Aktie US"].status_text
    assert rows["Demo-Aktie US"].detail_key == "DEMO-FUNDAMENTALS-C"
    assert rows["Demo-Krypto"].detail_key is None
    assert rows["Demo-Fonds B"].detail_key is None


def test_missing_values_are_never_displayed_as_zero():
    for row in evaluate():
        if row.has_model_value:
            continue
        cells = row.to_display_dict()
        for column in ("Vorsichtig", "Basis", "Optimistisch", "Zielpreis (Basis)"):
            assert cells[column] == "n. v."
        assert cells["Kurs-Abschlag zum Basiswert (% des Basiswerts)"] == "n. v."
        assert cells["Basiswert über Kurs (% des Kurses)"] == "n. v."


def test_unverified_watchlist_rows_explain_which_assignment_is_missing():
    rows = by_name(evaluate())
    no_candidate = rows["Demo-Krypto"].reasons[0]
    assert "Yahoo-Symbol" in no_candidate and "Handelsplatz" in no_candidate
    unmatched = watchlist_items(
        [["Fremd AG", "10,00", "0,5", "0,05", "FREMD", "Aktie", "ZZZ.DE", "GER", "EUR", None]]
    )
    entries = tuple(sheet_entry(i) for i in unmatched)
    row = evaluate(portfolio=None, entries=entries)[0]
    assert row.status is Status.IDENTITY_MISSING
    assert "keinem geprüften Listing" in row.reasons[0]
    assert row.detail_key is None and row.price_label == "10,00 EUR"


def test_a_listing_without_its_own_fundamentals_entry_is_not_guessed_from_the_isin():
    # Same security (same ISIN) on a venue without a [[fundamental]] entry: no
    # ISIN-only match, the row says the per-listing mapping is missing.
    stuttgart = VerifiedListing(
        Instrument(
            InstrumentType.STOCK,
            instrument_id=DEMO_VALUED_ID,
            trading_venue="STU",
            quote_unit=QuoteUnit("EUR", D(1)),
            provider_mapping=ProviderMapping("yfinance", "DEMOA.SG"),
        ),
        equity_form=EquityForm.SHARE,
        isin=DEMO_VALUED_ID,
    )
    registry = (*demo_registry(), stuttgart)
    snapshot = demo_overview_valuation()
    position = replace(
        snapshot.snapshot.positions[0],
        instrument=replace(stuttgart.instrument, display_ticker="DEMO-A"),
    )
    held = replace(
        snapshot.snapshot, positions=(position, *snapshot.snapshot.positions[1:])
    )
    from portfolio_dashboard.valuations import value_portfolio

    portfolio = value_portfolio(held, registry)
    row = evaluate(portfolio, (), listings=registry)[0]
    assert row.status is Status.UNMAPPED
    assert "eigener Eintrag" in row.reasons[0] and row.detail_key is None
    assert values(row) == (None, None, None)


def test_two_watchlist_rows_of_one_security_become_one_row():
    items = watchlist_items(
        [
            ["Demo-Aktie DE", "45,20", "1,1", "0,5", "A", "Aktie", "DEMOA.DE", "GER", "EUR", None],
            ["Demo-Aktie FRA", "45,30", "1,0", "0,4", "A2", "Aktie", "DEMOA.F", "FRA", "EUR", None],
        ]
    )
    entries = tuple(sheet_entry(i) for i in items)
    rows = evaluate(portfolio=None, entries=entries)
    assert len(rows) == 1 and rows[0].status is Status.VALUED
    assert any("2 Watchlist-Zeilen" in note for note in rows[0].notes)


# --- price relation: two named reference quantities -----------------------------------------


def test_discount_and_distance_are_two_labelled_quantities_from_the_comparison():
    row = by_name(evaluate())["Demo-Aktie A"]
    base = row.base
    assert base.value_label == "135,15 EUR"
    # Price 120 against 135,15: 1 - 120/135,15 and 135,15/120 - 1.
    assert base.discount_label == "+11,21 %"
    assert base.distance_label == "+12,63 %"
    cells = row.to_display_dict()
    assert cells["Kurs-Abschlag zum Basiswert (% des Basiswerts)"] == "+11,21 %"
    assert cells["Basiswert über Kurs (% des Kurses)"] == "+12,63 %"
    assert row.position_text == "Kurs liegt unter dem Basiswert"
    assert "bezogen auf den Modellwert" in row.comparison_text
    assert "bezogen auf den Kurs" in row.comparison_text
    # The cautious scenario lies below the price: both quantities are negative.
    assert row.scenarios[0].discount_label == "-12,17 %"
    assert row.scenarios[0].distance_label == "-10,85 %"
    assert row.target_label == "108,12 EUR" and row.margin_label == "20,00 %"


def test_without_a_price_the_model_values_stay_and_the_comparison_says_why():
    item = watchlist_items(
        [["Demo-Aktie DE", None, None, None, "A", "Aktie", "DEMOA.DE", "GER", "EUR", None]]
    )[0]
    row = evaluate(portfolio=None, entries=(sheet_entry(item),))[0]
    assert row.status is Status.VALUED and row.price_label == "n. v."
    assert row.base.value_label == "135,15 EUR"
    assert row.base.discount_label == "n. v." and row.discount is None
    assert row.comparison_text.startswith("Kein Kursvergleich")
    assert "Kurs fehlt" in row.comparison_text


def test_a_price_in_another_currency_without_fx_blocks_only_the_comparison():
    # Model value in EUR, listing quoted in USD, no FX rate: values stay, no ratio.
    usd = VerifiedListing(
        Instrument(
            InstrumentType.STOCK,
            instrument_id=DEMO_VALUED_ID,
            trading_venue="NMS",
            quote_unit=QuoteUnit("USD", D(1)),
            provider_mapping=ProviderMapping("yfinance", "DEMOA"),
        ),
        equity_form=EquityForm.SHARE,
        isin=DEMO_VALUED_ID,
    )
    subjects = demo_subjects().subjects
    from portfolio_dashboard.fundamentals_demo import _stock

    extra = _stock(
        DEMO_VALUED_ID, "NMS", "DEMOA", "USD", suffix="e", confirmed=True,
        source_symbol="DEMO-E.SYN",
    )
    from portfolio_dashboard.fundamentals_config import FundamentalSubjects

    loader = FundamentalsLoader(
        FundamentalSubjects((*subjects, extra)),
        SyntheticFundamentalsProvider(),
        MemoryFundamentalsStore(),
        clock=lambda: DEMO_TIMESTAMP,
        sleep=lambda seconds: None,
        package_version="demo",
        gate=FetchGate(),
    )
    registry = (*demo_registry(), usd)
    item = watchlist_items(
        [["US-Listing", None, None, None, "A", "Aktie", "DEMOA", "NMS", "USD", None]]
    )[0]
    entry = demo_entry(
        item,
        (D(1), D(1), D(1), D(1)),
        D("150.00"),
        QuoteUnit("USD", D(1)),
        date(2026, 10, 22),
        DEMO_TIMESTAMP,
    )
    standard = demo_valuation_source()
    subjects_ = collect_subjects(standard.portfolio, (), standard.listings)
    loaded = load_fundamentals(
        standard, demo_fundamentals_loader(), subjects_, refresh=False
    )
    row = evaluate(
        portfolio=None,
        entries=(entry,),
        listings=registry,
        loader=loader,
        extra_docs={DEMO_VALUED_ID: loaded.documents[DEMO_VALUED_ID]},
    )[0]
    assert row.status is Status.VALUED and row.price_label == "150,00 USD"
    assert row.base.value_label == "135,15 EUR" and row.discount is None
    assert "Wechselkurs fehlt" in row.comparison_text


# --- filters, sorting, coverage -------------------------------------------------------------


def test_filters_never_change_the_underlying_valuation():
    rows = evaluate()
    for scope in (SCOPE_ALL, SCOPE_PORTFOLIO, SCOPE_WATCHLIST):
        for show in (SHOW_VALUED, SHOW_UNVALUED):
            for row in filter_rows(rows, scope=scope, query="demo", show=show):
                assert row in rows  # the very same, unchanged row objects
    assert [r.name for r in filter_rows(rows, show=SHOW_VALUED)] == ["Demo-Aktie A"]
    assert "Demo-Aktie A" not in [r.name for r in filter_rows(rows, show=SHOW_UNVALUED)]
    assert [r.name for r in filter_rows(rows, query=" krypto ")] == ["Demo-Krypto"]
    assert [r.name for r in filter_rows(rows, query="DEMO-NY")] == ["Demo-Aktie NYSE"]
    assert filter_rows(rows, query="gibt es nicht") == ()


def test_sorting_puts_missing_values_last_and_never_treats_them_as_zero():
    rows = evaluate()
    for field in (SORT_BY_DISCOUNT, SORT_BY_DISTANCE):
        for descending in (False, True):
            ordered = sort_rows(rows, field, descending=descending)
            assert ordered[0].name == "Demo-Aktie A"  # the only row with a figure
            assert all(r.discount is None for r in ordered[1:])
            assert sorted(r.index for r in ordered) == sorted(r.index for r in rows)
    ticker = sort_rows(rows, SORT_BY_TICKER)
    assert [r.ticker for r in ticker][:5] == sorted(
        r.ticker for r in rows if r.ticker
    )
    assert [r.name for r in ticker][-2:] == ["Demo-Krypto", "Demo-ohne Kurs"]
    names = [r.name for r in sort_rows(rows, SORT_BY_NAME, descending=True)]
    assert names == sorted((r.name for r in rows), key=str.casefold, reverse=True)
    with pytest.raises(ValueError):
        sort_rows(rows, "Kurs")


def test_sorting_orders_two_figures_by_value():
    first = evaluate()[0]
    cheaper = replace(first, index=99, name="Zweite Aktie")
    cheaper_base = replace(
        first.base,
        comparison=replace(
            first.base.comparison, discount_to_model_percent=D("30"),
            distance_to_model_percent=D("40"),
        ),
    )
    cheaper = replace(cheaper, scenarios=(first.scenarios[0], cheaper_base, first.scenarios[2]))
    ordered = sort_rows((first, cheaper), SORT_BY_DISCOUNT, descending=True)
    assert [r.name for r in ordered] == ["Zweite Aktie", "Demo-Aktie A"]
    ordered = sort_rows((first, cheaper), SORT_BY_DISTANCE, descending=False)
    assert [r.name for r in ordered] == ["Demo-Aktie A", "Zweite Aktie"]


def test_coverage_counts_computable_stocks_against_the_considered_stocks_of_the_filter():
    rows = evaluate()
    everything = coverage(rows)
    assert (everything.considered, everything.valued, everything.complete) == (5, 1, 1)
    assert everything.coverage_label == "1 von 5 (20 %)"
    assert everything.excluded == 2 and everything.excluded_by_kind == (("ETF", 2),)
    portfolio = coverage(filter_rows(rows, scope=SCOPE_PORTFOLIO))
    assert portfolio.coverage_label == "1 von 1 (100 %)" and portfolio.excluded == 1
    watchlist = coverage(filter_rows(rows, scope=SCOPE_WATCHLIST))
    assert watchlist.coverage_label == "1 von 5 (20 %)" and watchlist.excluded == 1
    searched = coverage(filter_rows(rows, query="krypto"))
    assert searched.coverage_label == "0 von 1 (0 %)" and searched.without == 1
    assert coverage(filter_rows(rows, query="ETF")).considered == 0


# --- source, age, manual supplements, incomplete assumptions, unsupported model -----


def test_data_stand_keeps_retrieval_age_and_business_age_apart():
    row = by_name(evaluate())["Demo-Aktie A"]
    text = "\n".join(row.stand_lines)
    assert "Abrufalter: Abruf 23.10.2026, 12:00 UTC" in text
    assert "Fachliches Alter: Jahresabschluss bis 31.12.2025 (fachliches Alter 296 Tage)" in text
    assert "Annahmenstand: Version demo-assumptions-v1" in text and "synthetisch" in text
    assert row.stand_short == "Abruf vor 0 Sek.; Abschluss bis 31.12.2025"


def test_a_fresh_retrieval_of_old_reports_does_not_make_them_recent():
    later = datetime(2027, 6, 1, 12, 0, tzinfo=timezone.utc)
    source = demo_valuation_source()
    subjects = collect_subjects(source.portfolio, (), source.listings)
    loaded = load_fundamentals(source, demo_fundamentals_loader(), subjects, refresh=False)
    context = EvaluationContext(
        source.listings, later, FundamentalsState(loaded.bundle), loaded.documents, demo=True
    )
    row = build_rows(subjects, context)[0]
    text = "\n".join(row.stand_lines)
    assert "vor 221 Tag(en)" in text  # retrieval age of the 23.10.2026 retrieval
    assert "fachliches Alter 517 Tage" in text  # 31.12.2025 is the business date
    assert any("älter als die Altersgrenze" in n for n in row.notes)


def test_source_manual_supplement_and_dominance_are_text_not_color():
    row = by_name(evaluate())["Demo-Aktie A"]
    notes = " | ".join(row.notes)
    assert "manuell ergänzt:" in notes and "operating_tax_rate" in notes
    assert "Kurs: Sheet-Wert, kein Anbieterkurs" in notes
    assert "Terminalwertanteil ab 75 %" in notes
    assert "Demo:" in notes and "erfunden" in notes
    assert row.price_source == "Sheet-Wert, Kursstand unbekannt"
    watch = by_name(evaluate(portfolio=None))["Demo-Aktie DE"]
    assert watch.price_source == "Demo-Wert, synthetisch"


def test_incomplete_and_unconfirmed_assumptions_block_the_value_with_a_reason():
    def unconfirmed(document):
        saved = tuple(
            SavedScenario(
                replace(s.assumptions, confirmed_at=None, confirmation_reference=None),
                s.model_version,
            )
            for s in document.scenarios
        )
        return replace(document, scenarios=saved)

    def incomplete(document):
        saved = tuple(
            SavedScenario(replace(s.assumptions, terminal=None), s.model_version)
            for s in document.scenarios
        )
        return replace(document, scenarios=saved)

    row = by_name(evaluate(mutate=unconfirmed))["Demo-Aktie A"]
    assert row.status is Status.UNCONFIRMED and values(row) == (None, None, None)
    assert "scenario_confirmation" in " ".join(row.reasons)
    row = by_name(evaluate(mutate=incomplete))["Demo-Aktie A"]
    assert row.status is Status.INCOMPLETE and not row.covered
    assert "scenario_components" in " ".join(row.reasons)
    assert row.comparison_text == "kein Basiswert, kein Vergleich"
    assert row.discount is None and row.target_label == "n. v."


def test_a_partly_stored_plan_is_a_partial_valuation():
    def only_two(document):
        return replace(document, scenarios=document.scenarios[:2])

    row = by_name(evaluate(mutate=only_two))["Demo-Aktie A"]
    assert row.status is Status.PARTIAL and row.covered
    assert row.status_text == "Teilweise bewertet (2 von 3 Szenarien)"
    assert row.scenarios[2].value is None
    assert row.scenarios[2].reason == "Annahmen fehlen"
    assert row.scenarios[0].value is not None


def test_new_fundamentals_do_not_reuse_assumptions_bound_to_the_old_report_set():
    def other_version(document):
        return replace(
            document, originals=replace(document.originals, input_version="neuer-stand")
        )

    row = by_name(evaluate(mutate=other_version))["Demo-Aktie A"]
    assert row.status is Status.DATA_CHANGED
    assert values(row) == (None, None, None) and "geändert" in row.reasons[0]
    assert row.detail_key == DEMO_VALUED_ID


def test_no_document_means_assumptions_missing_even_with_current_fundamentals():
    row = by_name(evaluate(mutate=lambda document: None))["Demo-Aktie A"]
    assert row.status is Status.ASSUMPTIONS_MISSING
    assert "Fundamentaldaten" not in row.status_text
    assert "keine Szenarioannahmen" in row.reasons[0]
    assert "Annahmenstand: keine gespeicherten Annahmen" in row.stand_lines


def test_loader_states_are_distinguished_from_missing_assumptions():
    not_configured = by_name(evaluate(state=FundamentalsState(None, False)))
    assert not_configured["Demo-Aktie A"].status is Status.NOT_CONFIGURED
    not_loaded = by_name(evaluate(state=FundamentalsState(None, True)))
    assert not_loaded["Demo-Aktie A"].status is Status.NOT_LOADED
    failed = by_name(evaluate(state=FundamentalsState(None, True, "Feste Meldung.")))
    assert failed["Demo-Aktie A"].status is Status.LOAD_FAILED
    assert failed["Demo-Aktie A"].reasons == ("Feste Meldung.",)
    # Everything else keeps its own state: only verified stocks wait for the loader.
    assert failed["Demo-Krypto"].status is Status.IDENTITY_MISSING
    assert failed["Demo-Fonds B"].status is Status.EXCLUDED


def test_a_failed_retrieval_leaves_no_value_and_names_the_failure():
    class Failing:
        def fetch(self, identity):
            raise RuntimeError("synthetic outage")

    loader = FundamentalsLoader(
        demo_subjects(),
        Failing(),
        MemoryFundamentalsStore(),
        clock=lambda: DEMO_TIMESTAMP,
        sleep=lambda seconds: None,
        package_version="demo",
        gate=FetchGate(),
    )
    row = by_name(evaluate(loader=loader))["Demo-Aktie A"]
    assert row.status is Status.NO_DATA and values(row) == (None, None, None)
    assert any("request_provider_error" in r for r in row.reasons)
    assert row.stand_short == "nicht abgerufen"


def test_no_row_contains_a_buy_or_sell_signal():
    banned = ("kaufen", "verkaufen", "kaufsignal", "verkaufssignal", "unterbewertet", "überbewertet", "empfehl")
    for row in evaluate():
        text = " ".join((*row.detail_lines(), *row.to_display_dict().values())).lower()
        assert not any(word in text for word in banned)


def test_instruments_for_the_loader_are_the_verified_listings_only():
    source = demo_valuation_source()
    subjects = collect_subjects(source.portfolio, demo_watchlist_bundle().entries, source.listings)
    instruments = verified_instruments(subjects)
    assert {(i.instrument_id, i.trading_venue) for i in instruments} == {
        (DEMO_VALUED_ID, "FRA"),
        (DEMO_VALUED_ID, "GER"),
        ("DEMO-FUNDAMENTALS-C", "NMS"),
        ("DEMO-FUNDAMENTALS-D", "NYQ"),
    }
