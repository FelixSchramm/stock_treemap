"""Watchlist view model (#85): filters, sorting, KPIs, labels; synthetic and offline."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from portfolio_dashboard.models import DataWarning, QuoteUnit
from portfolio_dashboard.sheet_parser import NumberLocale
from portfolio_dashboard.treemap import (
    ALL_FILTER,
    MISSING_COLOR,
    NO_CATEGORY_LABEL,
    FilterCriteria,
    color_for,
)
from portfolio_dashboard.watchlist_quotes import (
    PERIODS,
    WatchlistEntry,
    WatchlistEntryStatus,
    WatchlistOrigin,
    WatchlistPeriodValue,
    sheet_entry,
)
from portfolio_dashboard.watchlist_session import LOADER_STATE_KEY, session_loader
from portfolio_dashboard.watchlist_sheet import (
    BASIC_HEADERS,
    OPTIONAL_HEADERS,
    SHEET_VALUE_LABEL,
    parse_watchlist_rows,
)
from portfolio_dashboard.watchlist_view import (
    NOT_SPECIFIED,
    SORT_BY_NAME,
    SORT_BY_PERFORMANCE,
    SORT_BY_TICKER,
    UNKNOWN_REASON,
    available_categories,
    build_kpis,
    build_rows,
    build_status_notes,
    build_watchlist_treemap,
    sort_rows,
)

STAMP = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
FULL = (*BASIC_HEADERS, *OPTIONAL_HEADERS)


def parse(rows, headers=FULL):
    return parse_watchlist_rows(
        headers, rows, imported_at=STAMP, number_locale=NumberLocale.GERMAN
    ).snapshot.items


def provider_entry(
    item,
    percents=("1.5", "2.5", "-3.5", "10"),
    *,
    cached=False,
    sheet_today=False,
    reasons=None,
    price="100.00",
    currency="USD",
):
    """A hand-built mapped row as the coordinator would deliver it."""
    origin = WatchlistOrigin.CACHED_PROVIDER if cached else WatchlistOrigin.PROVIDER
    values = []
    for label, percent in zip(PERIODS, percents):
        if percent is None:
            code = (reasons or {}).get(label, "insufficient_history")
            values.append(WatchlistPeriodValue(label, None, WatchlistOrigin.NONE, code))
        else:
            values.append(
                WatchlistPeriodValue(
                    label, Decimal(percent), origin, None, date(2026, 10, 27)
                )
            )
    if sheet_today:
        values[0] = WatchlistPeriodValue(
            "Heute", Decimal("0.5"), WatchlistOrigin.SHEET, "missing_current_close"
        )
    return WatchlistEntry(
        item,
        WatchlistEntryStatus.CACHED if cached else WatchlistEntryStatus.LIVE,
        "Anbieterdaten",
        tuple(values),
        Decimal(price),
        QuoteUnit(currency, Decimal("1")),
        origin,
        current_date=date(2026, 10, 28),
        market_open=False,
        retrieved_at=STAMP,
        age_text="vor 2 Std." if cached else None,
        warnings=item.warnings,
    )


def entries():
    items = parse(
        [
            ["Alpha AG", "10,00", "1,0", "0,1", "ALP", "Aktie", "ALP.DE", "GER", "EUR", "Tech"],
            ["Beta ETF", "20,00", "-2,0", "-0,4", "BET", "ETF", "BET", "NMS", "USD", "Welt"],
            ["Gamma Krypto", "30,00", "3,0", "0,9", None, None, None, None, None, None],
            ["Delta Optionsschein", "1,00", "0", "0", "DEL", "Derivat", None, None, None, "Tech"],
            ["Epsilon ohne Wert", None, None, None, "EPS", None, None, None, None, None],
        ]
    )
    alpha, beta, gamma, delta, eps = items
    return (
        provider_entry(alpha, currency="EUR", sheet_today=True),
        provider_entry(beta, ("-1", None, "5", "20"), cached=True),
        sheet_entry(gamma),
        sheet_entry(delta),
        sheet_entry(eps),
    )


def names(rows):
    return [row.name for row in rows]


def test_missing_values_are_n_v_and_never_zero_percent():
    rows = {row.name: row for row in build_rows(entries(), "Woche")}
    gamma, eps = rows["Gamma Krypto"], rows["Epsilon ohne Wert"]
    assert gamma.percent is None and gamma.percent_label == "n. v."
    assert "keine Yahoo-Zuordnung im Blatt" in gamma.source
    assert eps.percent_label == "n. v." and "keine Yahoo-Zuordnung" in eps.source
    eps_today = {row.name: row for row in build_rows(entries(), "Heute")}["Epsilon ohne Wert"]
    assert eps_today.percent_label == "n. v." and "kein Sheet-Wert" in eps_today.source
    assert rows["Beta ETF"].percent_label == "n. v."  # provider gap in Woche
    assert "Historie unvollständig" in rows["Beta ETF"].source
    assert all("0,00 %" not in row.percent_label for row in rows.values() if row.percent is None)
    # A real zero change stays 0,00 %, distinguishable from n. v.
    delta_today = {row.name: row for row in build_rows(entries(), "Heute")}["Delta Optionsschein"]
    assert delta_today.percent_label == "0,00 %"
    assert SHEET_VALUE_LABEL in delta_today.source


def test_origin_is_shown_per_period_not_only_per_row():
    today = {row.name: row for row in build_rows(entries(), "Heute")}["Alpha AG"]
    week = {row.name: row for row in build_rows(entries(), "Woche")}["Alpha AG"]
    assert today.source.startswith(SHEET_VALUE_LABEL)
    assert "Anbieterwert fehlt: aktueller Schlusskurs fehlt" in today.source
    assert week.source == "Anbieter (Yahoo)"
    assert "Heute: +0,50 % · Sheet-Wert, Kursstand unbekannt" in "\n".join(week.period_lines)
    assert any(
        line.startswith("Woche: +2,50 % · Anbieter (Yahoo)")
        and "Referenzdatum 27.10.2026" in line
        for line in week.period_lines
    )
    assert f"Heute: {SHEET_VALUE_LABEL}" in week.status
    assert today.price_label == "100,00 EUR"  # trading currency, not converted


def test_cached_rows_carry_their_age_and_sheet_rows_the_unknown_quote_time():
    rows = {row.name: row for row in build_rows(entries(), "Heute")}
    beta, gamma = rows["Beta ETF"], rows["Gamma Krypto"]
    assert beta.source == "Anbieter, zwischengespeichert (vor 2 Std.)"
    assert "Alter: vor 2 Std." in beta.status and beta.status.startswith("Zwischengespeichert")
    assert "zwischengespeichert, vor 2 Std." in beta.quote_time
    assert gamma.status == SHEET_VALUE_LABEL
    assert gamma.quote_time == "Kursstand unbekannt"
    assert gamma.price_label == "30,00 EUR (Sheet)"
    assert gamma.venue == "—" and beta.venue == "NMS"
    assert rows["Epsilon ohne Wert"].price_label == "n. v."
    assert rows["Epsilon ohne Wert"].status == "Daten nicht verfügbar"


def test_provider_failure_reason_is_translated_and_unknown_codes_are_not_echoed():
    item = parse([["Zeta", "5,00", "1", "0", "ZET", "Aktie", "ZET.DE", "GER", "EUR", "X"]])[0]
    failed = sheet_entry(item, "provider_failure")
    odd = sheet_entry(item, "secret-ish_unknown_code")
    row, odd_row = build_rows([failed], "Heute")[0], build_rows([odd], "Heute")[0]
    assert "Kursanbieter nicht erreichbar" in row.status
    assert row.status.startswith(SHEET_VALUE_LABEL)
    assert UNKNOWN_REASON in odd_row.status and "unknown_code" not in odd_row.status
    assert "provider_failure" not in row.status


def test_rows_without_optional_columns_still_render_every_field():
    items = parse([["Nur Basis", "5,00", "-1", "-0,05"]], headers=BASIC_HEADERS)
    row = build_rows([sheet_entry(items[0])], "Jahr")[0]
    assert row.ticker is None and row.instrument_type == NOT_SPECIFIED
    assert row.category == NO_CATEGORY_LABEL and row.venue == "—"
    display = row.to_display_dict()
    assert display["Ticker"] == "—" and display["Kursentwicklung (%)"] == "n. v."
    assert len(row.detail_lines()) > 10


@pytest.mark.parametrize(
    ("criteria", "expected"),
    [
        (FilterCriteria(query="alp"), ["Alpha AG"]),
        (FilterCriteria(query="BET"), ["Beta ETF"]),  # ticker and symbol
        (FilterCriteria(query="alp.de"), ["Alpha AG"]),  # Yahoo symbol
        (FilterCriteria(query=" welt "), ["Beta ETF"]),  # category
        (FilterCriteria(query="gibt es nicht"), []),
        (FilterCriteria(instrument_type="Aktie"), ["Alpha AG"]),
        (FilterCriteria(instrument_type="ETF"), ["Beta ETF"]),
        (FilterCriteria(instrument_type="Derivat"), ["Delta Optionsschein"]),
        (FilterCriteria(instrument_type=NOT_SPECIFIED), ["Gamma Krypto", "Epsilon ohne Wert"]),
        (FilterCriteria(category="Tech"), ["Alpha AG", "Delta Optionsschein"]),
        (FilterCriteria(category=NO_CATEGORY_LABEL), ["Gamma Krypto", "Epsilon ohne Wert"]),
        (FilterCriteria(query="a", instrument_type="ETF", category="Welt"), ["Beta ETF"]),
        (FilterCriteria(query="alp", category="Welt"), []),
    ],
)
def test_filters_combine_search_type_and_category(criteria, expected):
    assert names(build_rows(entries(), "Heute", criteria)) == expected


def test_category_options_and_all_filter():
    assert available_categories(entries()) == (ALL_FILTER, "Tech", "Welt", NO_CATEGORY_LABEL)
    assert available_categories(entries()[2:3]) == (ALL_FILTER,)  # nothing but uncategorized
    assert len(build_rows(entries(), "Heute", FilterCriteria())) == 5


def test_sorting_puts_missing_values_last_in_both_directions():
    rows = build_rows(entries(), "Heute")
    desc = sort_rows(rows, SORT_BY_PERFORMANCE, descending=True)
    asc = sort_rows(rows, SORT_BY_PERFORMANCE, descending=False)
    # Gamma +3,0 and Delta 0,00 come from the Sheet; Epsilon has no value.
    assert names(desc) == [
        "Gamma Krypto", "Alpha AG", "Delta Optionsschein", "Beta ETF", "Epsilon ohne Wert"
    ]
    assert names(asc) == [
        "Beta ETF", "Delta Optionsschein", "Alpha AG", "Gamma Krypto", "Epsilon ohne Wert"
    ]
    week = sort_rows(build_rows(entries(), "Woche"), SORT_BY_PERFORMANCE, descending=True)
    assert names(week)[:1] == ["Alpha AG"]
    assert set(names(week)[1:]) == {
        "Beta ETF", "Gamma Krypto", "Delta Optionsschein", "Epsilon ohne Wert"
    }


def test_name_and_ticker_sorting_are_deterministic_and_tickerless_rows_stay_last():
    rows = build_rows(entries(), "Heute")
    assert names(sort_rows(rows, SORT_BY_NAME, descending=False)) == [
        "Alpha AG", "Beta ETF", "Delta Optionsschein", "Epsilon ohne Wert", "Gamma Krypto"
    ]
    assert names(sort_rows(rows, SORT_BY_NAME, descending=True))[0] == "Gamma Krypto"
    asc = sort_rows(rows, SORT_BY_TICKER, descending=False)
    desc = sort_rows(rows, SORT_BY_TICKER, descending=True)
    assert names(asc) == ["Alpha AG", "Beta ETF", "Delta Optionsschein", "Epsilon ohne Wert", "Gamma Krypto"]
    assert names(desc) == ["Epsilon ohne Wert", "Delta Optionsschein", "Beta ETF", "Alpha AG", "Gamma Krypto"]
    with pytest.raises(ValueError):
        sort_rows(rows, "Wert (EUR)")


def test_equal_values_sort_by_name_then_sheet_row():
    items = parse([["Zwilling", "1", "2", "0"], ["Zwilling", "1", "2", "0"], ["Adam", "1", "2", "0"]], BASIC_HEADERS)
    ordered = sort_rows(build_rows([sheet_entry(i) for i in items], "Heute"), SORT_BY_PERFORMANCE)
    assert [(r.name, r.source_row) for r in ordered] == [("Adam", 4), ("Zwilling", 2), ("Zwilling", 3)]


def test_kpis_count_coverage_winner_and_loser_with_substitute_notes():
    today = build_kpis(entries(), "Heute")
    assert today.count_label == "5"
    assert (today.coverage_label, today.coverage_note) == ("4 von 5", "mit Kennzahl")
    assert "3 Sheet-Ersatzwert(e) (Sheet-Wert, Kursstand unbekannt)" in today.coverage_detail
    assert "1 zwischengespeichert" in today.coverage_detail
    assert today.coverage_detail.startswith("Datenabdeckung „Heute“: 4 von 5 Werten mit Kennzahl")
    assert today.sheet_value_count == 3  # Alpha (Heute), Gamma and Delta
    assert (today.winner_label, today.winner_note) == ("+3,00 %", "Gamma Krypto · Sheet-Wert")
    assert (today.loser_label, today.loser_note) == ("-1,00 %", "BET")
    week = build_kpis(entries(), "Woche")
    assert week.coverage_label == "1 von 5"
    assert week.sheet_value_count == 0 and "davon" not in week.coverage_detail
    assert week.winner_note == "ALP" and week.loser_label == "n. v."
    assert week.loser_note == "keine < 0 %"


def test_kpis_of_empty_and_valueless_lists():
    empty = build_kpis((), "Heute")
    assert empty.count_label == "0" and empty.coverage_label == "0 von 0"
    assert empty.winner_label == empty.loser_label == "n. v."
    assert empty.winner_note is None and empty.loser_note is None
    with pytest.raises(ValueError):
        build_kpis(entries(), "Seit Kauf")
    with pytest.raises(ValueError):
        build_rows(entries(), "Seit Kauf")


def test_status_notes_name_substitutes_cached_state_and_sheet_warnings():
    items = parse([["Eta", "5,00", "1", "0", "ETA", "Aktie", "ETA.DE", "GER", "EUR", "X"]])
    warned = sheet_entry(items[0])
    warned = WatchlistEntry(
        warned.item, warned.status, warned.status_text, warned.periods, warned.price,
        warned.price_unit, warned.price_origin,
        warnings=(DataWarning("synthetic_code", "Synthetischer Hinweis.", "symbol", 7),),
    )
    notes = build_status_notes(
        (*entries()[:3], warned), (DataWarning("row_note", "Zeilenhinweis.", None, 9),)
    )
    assert notes[0] == "Import-Hinweis (row_note): Zeile 9: Zeilenhinweis."
    assert any(note.startswith("Alpha AG (ALP): Heute: Sheet-Wert, Kursstand unbekannt") for note in notes)
    assert any(note.startswith("Beta ETF (BET): Zwischengespeichert") for note in notes)
    assert "Gamma Krypto: Sheet-Wert, Kursstand unbekannt" in notes
    assert "Eta (ETA): Synthetischer Hinweis." in notes


def test_session_loader_builds_once_per_day_and_rebuilds_when_the_date_changes():
    state, built = {}, []

    def factory():
        built.append(object())
        return built[-1]

    day1, day2 = date(2026, 10, 28), date(2026, 10, 29)
    first, rebuilt = session_loader(state, factory, day1)
    again, rebuilt_again = session_loader(state, factory, day1)
    assert (rebuilt, rebuilt_again) == (True, False) and again is first
    nextday, rebuilt_next = session_loader(state, factory, day2)
    assert rebuilt_next and nextday is not first and len(built) == 2


def test_session_loader_remembers_no_loader_and_retries_after_a_failed_build():
    state = {}
    assert session_loader(state, lambda: None, date(2026, 10, 28)) == (None, True)
    assert session_loader(state, lambda: "never built", date(2026, 10, 28)) == (None, False)

    def broken():
        raise ValueError("synthetic")

    fresh = {}
    with pytest.raises(ValueError):
        session_loader(fresh, broken, date(2026, 10, 28))
    assert LOADER_STATE_KEY not in fresh
    assert session_loader(fresh, lambda: "ok", date(2026, 10, 28)) == ("ok", True)


# --- heatmap (#86) ----------------------------------------------------------------


def heatmap(period, criteria=None, items=None):
    rows = sort_rows(build_rows(items or entries(), period, criteria))
    return rows, build_watchlist_treemap(rows)


@pytest.mark.parametrize("period", ["Heute", "Woche", "Monat", "Jahr"])
def test_heatmap_tiles_have_exactly_equal_weight_also_for_missing_values(period):
    rows, model = heatmap(period)
    assert len(model.tiles) == len(rows) == 5
    assert {tile.amount_eur for tile in model.tiles} == {Decimal(1)}
    assert model.omitted_count == 0
    # Missing values keep their tile: gray, "n. v.", no percent.
    missing = [tile for tile in model.tiles if tile.percent is None]
    assert missing and all(tile.color == MISSING_COLOR for tile in missing)
    assert all("n. v." in tile.label and "0,00 %" not in tile.label for tile in missing)


def test_heatmap_follows_the_table_order_and_uses_the_depot_color_scale():
    rows, model = heatmap("Heute")
    assert [tile.name for tile in model.tiles] == names(rows)
    assert names(rows)[0] == "Gamma Krypto"  # +3,00 %, the largest value first
    values = [tile.percent for tile in model.tiles if tile.percent is not None]
    assert model.scale_percent == max(abs(v) for v in values) and not model.capped
    for tile in model.tiles:
        assert tile.color == color_for(tile.percent, model.scale_percent)
    by_name = {tile.name: tile for tile in model.tiles}
    assert by_name["Gamma Krypto"].color != by_name["Beta ETF"].color  # gain vs loss
    assert by_name["Delta Optionsschein"].percent == Decimal(0)  # real 0 %, not n. v.
    assert by_name["Delta Optionsschein"].color != MISSING_COLOR


def test_heatmap_caps_only_the_color_never_the_number():
    items = parse(
        [
            ["Ausreißer", "10,00", "80,0", "8,0", "OUT", "Aktie", None, None, None, None],
            ["Verlierer", "10,00", "-2,0", "-0,2", "LOS", "Aktie", None, None, None, None],
        ]
    )
    rows, model = heatmap("Heute", items=[sheet_entry(item) for item in items])
    assert model.capped and model.scale_percent == Decimal(50)
    outlier = model.tiles[0]
    assert outlier.label[-2:] == ("+80,00 %", "Sheet-Wert")
    assert outlier.color == color_for(Decimal(50), Decimal(50))
    assert "Kursentwicklung Heute: +80,00 %" in outlier.tooltip


def test_heatmap_scale_is_derived_from_the_filtered_rows_only():
    _, everything = heatmap("Jahr")
    _, only_alpha = heatmap("Jahr", FilterCriteria(query="Alpha"))
    assert everything.scale_percent == Decimal(20)  # Beta, cached provider
    assert only_alpha.scale_percent == Decimal(10)
    assert [tile.name for tile in only_alpha.tiles] == ["Alpha AG"]


def test_heatmap_labels_name_ticker_percent_and_non_fresh_origin_as_text():
    _, model = heatmap("Heute")
    tiles = {tile.name: tile for tile in model.tiles}
    assert tiles["Alpha AG"].label == ("Alpha AG", "ALP", "+0,50 %", "Sheet-Wert")
    assert tiles["Beta ETF"].label == ("Beta ETF", "BET", "-1,00 %", "zwischengespeichert")
    assert tiles["Gamma Krypto"].label == ("Gamma Krypto", "+3,00 %", "Sheet-Wert")
    assert tiles["Epsilon ohne Wert"].label == ("Epsilon ohne Wert", "EPS", "n. v.")
    _, week = heatmap("Woche")
    alpha = {tile.name: tile for tile in week.tiles}["Alpha AG"]
    assert alpha.label == ("Alpha AG", "ALP", "+2,50 %")  # fresh provider value: no tag


def test_heatmap_tooltip_has_every_field_of_the_issue():
    rows, model = heatmap("Monat")
    tooltip = {tile.name: tile.tooltip for tile in model.tiles}["Beta ETF"]
    assert tooltip == (
        "Beta ETF (BET)",
        "Typ: ETF",
        "Handelsplatz: NMS",
        "Kurs: 100,00 USD",
        "Kursentwicklung Monat: +5,00 %",
        "Quelle: Anbieter, zwischengespeichert (vor 2 Std.)",
        "Referenzdatum: 27.10.2026",
        "Kursstand: 28.10.2026 (zwischengespeichert, vor 2 Std.)",
    )
    sheet = {tile.name: tile.tooltip for tile in model.tiles}["Gamma Krypto"]
    assert "Handelsplatz: —" in sheet and "Kursstand: Kursstand unbekannt" in sheet
    assert any("keine Yahoo-Zuordnung im Blatt" in line for line in sheet)


@pytest.mark.parametrize(
    "criteria, expected",
    [
        (FilterCriteria(query="alp"), ["Alpha AG"]),
        (FilterCriteria(instrument_type="ETF"), ["Beta ETF"]),
        (FilterCriteria(category="Tech"), ["Alpha AG", "Delta Optionsschein"]),
        (FilterCriteria(category=NO_CATEGORY_LABEL), ["Gamma Krypto", "Epsilon ohne Wert"]),
        (FilterCriteria(query="zzz"), []),
    ],
)
def test_heatmap_tiles_are_exactly_the_filtered_table_rows(criteria, expected):
    rows, model = heatmap("Heute", criteria)
    assert sorted(names(rows)) == sorted(expected)
    assert sorted(tile.name for tile in model.tiles) == sorted(expected)
    assert {tile.amount_eur for tile in model.tiles} <= {Decimal(1)}


def test_heatmap_of_no_rows_is_empty_with_the_minimum_scale():
    model = build_watchlist_treemap(())
    assert model.tiles == () and model.scale_percent == Decimal(1) and not model.capped
