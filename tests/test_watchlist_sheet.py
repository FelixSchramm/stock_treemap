"""Entirely synthetic watchlist-tab cases (ADR 0011); no Sheet or market access."""

import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from portfolio_dashboard.config import (
    ConfigurationError,
    Mode,
    load_config,
    parse_watchlist_range,
)
from portfolio_dashboard.models import (
    DataWarning,
    InstrumentType,
    SnapshotStatus,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.sheet_parser import NumberLocale
from portfolio_dashboard.watchlist_sheet import (
    BASIC_HEADERS,
    EXCHANGES,
    OPTIONAL_HEADERS,
    SHEET_VALUE_LABEL,
    WatchlistItem,
    WatchlistMapping,
    WatchlistRowStatus,
    WatchlistSnapshot,
    parse_watchlist_rows,
)

IMPORTED = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)
FULL_HEADERS = (*BASIC_HEADERS, *OPTIONAL_HEADERS)


def parse(rows, headers=FULL_HEADERS, locale=NumberLocale.GERMAN, **kwargs):
    return parse_watchlist_rows(
        headers, rows, imported_at=IMPORTED, number_locale=locale, **kwargs
    )


def row(
    name="Synthetik AG",
    price="12,50",
    change="1,5",
    net="0,18",
    ticker="SYN",
    kind="Aktie",
    symbol="SYN.DE",
    exchange="GER",
    currency="EUR",
    category="Demo",
):
    return [name, price, change, net, ticker, kind, symbol, exchange, currency, category]


def codes(item_or_snapshot):
    return [w.code for w in item_or_snapshot.warnings]


def test_basic_four_column_rows_are_valid_sheet_fallbacks():
    result = parse(
        [["Synthetik AG", "12,50 €", "-1,5 %", "-0,19"], ["Beispiel plc", "1.234,5", "0", "0"]],
        headers=BASIC_HEADERS,
    )
    snapshot = result.snapshot
    assert snapshot.status is SnapshotStatus.COMPLETE and snapshot.warnings == ()
    first, second = snapshot.items
    assert first.status is WatchlistRowStatus.SHEET_ONLY and first.mapping is None
    assert first.sheet_price.price == Decimal("12.50")
    assert first.sheet_price.status is ValueStatus.AVAILABLE
    assert first.sheet_price.unit.currency == "EUR"
    assert first.sheet_price.provenance.source is ValuationSource.SHEET
    assert first.sheet_price.provenance.observed_at is None  # Kursstand unbekannt
    assert first.sheet_change_percent_points == Decimal("-1.5")  # nicht mit 100 skaliert
    assert first.sheet_net_change_eur == Decimal("-0.19")
    assert second.sheet_price.price == Decimal("1234.5")
    assert (first.source_row, second.source_row) == (2, 3)
    assert SHEET_VALUE_LABEL == "Sheet-Wert, Kursstand unbekannt"


def test_blank_basic_values_are_missing_without_warnings_and_blank_rows_ignored():
    result = parse(
        [["Synthetik AG", None, "k. A.", ""], [None, "", None, None], []],
        headers=BASIC_HEADERS,
    )
    (item,) = result.snapshot.items
    assert item.sheet_price.status is ValueStatus.MISSING and item.sheet_price.price is None
    assert item.sheet_change_percent_points is None and item.sheet_net_change_eur is None
    assert item.warnings == () and result.snapshot.status is SnapshotStatus.COMPLETE
    assert [(e.source_row, e.reason) for e in result.excluded_rows] == [
        (3, "empty"),
        (4, "empty"),
    ]


def test_english_locale_and_numeric_cells():
    result = parse(
        [["Synthetik AG", "1,234.50", 2, Decimal("-0.5")]],
        headers=BASIC_HEADERS,
        locale=NumberLocale.ENGLISH,
    )
    (item,) = result.snapshot.items
    assert item.sheet_price.price == Decimal("1234.50")
    assert item.sheet_change_percent_points == Decimal("2")
    assert item.sheet_net_change_eur == Decimal("-0.5")
    german = parse([["Synthetik AG", "1,234.50", "1", "1"]], headers=BASIC_HEADERS)
    assert codes(german.snapshot.items[0]) == ["invalid_price"]


def test_invalid_numbers_become_row_warnings_and_keep_the_row():
    result = parse(
        [["Synthetik AG", "abc", "x%", "1,2,3"], ["Zwei AG", "-3", "-100,5", "0"]],
        headers=BASIC_HEADERS,
    )
    first, second = result.snapshot.items
    assert first.sheet_price.status is ValueStatus.INVALID and first.sheet_price.price is None
    assert first.sheet_change_percent_points is None and first.sheet_net_change_eur is None
    assert codes(first) == ["invalid_price", "invalid_change", "invalid_net_change"]
    assert codes(second) == ["invalid_price", "invalid_change"]
    assert all(w.source_row in (2, 3) and w.field for w in result.snapshot.warnings)
    assert result.snapshot.status is SnapshotStatus.COMPLETE
    for bad in (True, float("nan"), Decimal("Infinity")):
        (item,) = parse([["A", bad, 0, 0]], headers=BASIC_HEADERS).snapshot.items
        assert item.sheet_price.status is ValueStatus.INVALID


def test_missing_name_rejects_row_as_partial():
    result = parse(
        [[None, "1", "1", "1"], ["Synthetik AG", "1", "1", "1"]], headers=BASIC_HEADERS
    )
    assert result.snapshot.status is SnapshotStatus.PARTIAL
    assert [i.name for i in result.snapshot.items] == ["Synthetik AG"]
    assert [(w.code, w.source_row) for w in result.snapshot.warnings] == [("invalid_name", 2)]
    assert [(e.source_row, e.reason) for e in result.excluded_rows] == [(2, "invalid_row")]


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        (BASIC_HEADERS[:3], "missing_column"),
        ((*BASIC_HEADERS[:3], "Ticker"), "missing_column"),
        ((BASIC_HEADERS[0], BASIC_HEADERS[0], *BASIC_HEADERS[2:]), "duplicate_column"),
        # A basic header right of D does not count.
        ((*BASIC_HEADERS[:3], "Ticker", BASIC_HEADERS[3]), "missing_column"),
    ],
)
def test_schema_requires_each_basic_header_once_in_a_to_d(headers, code):
    result = parse([["Synthetik AG", "1", "1", "1"]], headers=headers)
    assert result.snapshot.status is SnapshotStatus.FAILED and result.snapshot.items == ()
    assert result.snapshot.warnings[0].code == code
    assert result.snapshot.warnings[0].source_row == 1


def test_basic_headers_may_be_reordered_and_are_case_and_space_insensitive():
    headers = ("  nettoveränd.  (eur)", "POSITION", "Veränd. (%)", "Letzter Kurs (EUR)")
    (item,) = parse([["0,1", "Synthetik AG", "2", "3"]], headers=headers).snapshot.items
    assert (item.name, item.sheet_price.price) == ("Synthetik AG", Decimal("3"))
    assert item.sheet_net_change_eur == Decimal("0.1")


def test_header_row_offsets_source_rows_and_bad_arguments_fail_closed():
    result = parse([["Synthetik AG", "1", "1", "1"]], headers=BASIC_HEADERS, header_row=7)
    assert result.snapshot.items[0].source_row == 8
    with pytest.raises(ValueError):
        parse([], headers=BASIC_HEADERS, header_row=0)
    with pytest.raises(TypeError):
        parse([], headers=BASIC_HEADERS, locale="de_DE")
    with pytest.raises(ValueError):
        parse_watchlist_rows(
            BASIC_HEADERS,
            [],
            imported_at=datetime(2026, 1, 1),
            number_locale=NumberLocale.GERMAN,
        )


@pytest.mark.parametrize(
    ("kwargs", "exchange", "timezone_name", "currency"),
    [
        ({"symbol": "SYN.DE", "exchange": "GER"}, "GER", "Europe/Berlin", "EUR"),
        ({"symbol": "syn.f", "exchange": "fra"}, "FRA", "Europe/Berlin", "EUR"),
        ({"symbol": "SYN.SG", "exchange": "STU"}, "STU", "Europe/Berlin", "EUR"),
        ({"symbol": "SYN.MU", "exchange": "MUN"}, "MUN", "Europe/Berlin", "EUR"),
        ({"symbol": "SYN.DU", "exchange": "DUS"}, "DUS", "Europe/Berlin", "EUR"),
        ({"symbol": "SYN", "exchange": "NMS", "currency": "USD"}, "NMS", "America/New_York", "USD"),
        ({"symbol": "SYN-B", "exchange": "NYQ", "currency": "USD"}, "NYQ", "America/New_York", "USD"),
        ({"symbol": "SYN", "exchange": "NGM", "currency": "USD"}, "NGM", "America/New_York", "USD"),
        ({"symbol": "SYN", "exchange": "NCM", "currency": "USD"}, "NCM", "America/New_York", "USD"),
        ({"symbol": "SYN", "exchange": "ASE", "currency": "USD"}, "ASE", "America/New_York", "USD"),
        (
            {"symbol": "SYN", "exchange": "PCX", "currency": "USD", "kind": "ETF"},
            "PCX",
            "America/New_York",
            "USD",
        ),
    ],
)
def test_complete_de_and_us_mappings(kwargs, exchange, timezone_name, currency):
    result = parse([row(**kwargs)])
    (item,) = result.snapshot.items
    assert item.status is WatchlistRowStatus.MAPPED and item.warnings == ()
    assert item.mapping.exchange == exchange and item.mapping.currency == currency
    assert item.mapping.timezone == timezone_name
    assert item.mapping.symbol == kwargs["symbol"].upper()
    assert item.ticker == "SYN" and item.category == "Demo"
    assert item.instrument_type in (InstrumentType.STOCK, InstrumentType.ETF)
    assert item.sheet_price.price == Decimal("12.50")  # Fallback bleibt erhalten
    assert exchange in EXCHANGES


def test_supported_exchange_set_is_exactly_the_adr_list():
    assert set(EXCHANGES) == {
        "GER", "FRA", "STU", "MUN", "DUS", "NMS", "NGM", "NCM", "NYQ", "ASE", "PCX",
    }


def test_mapping_without_type_or_optional_text_is_allowed():
    (item,) = parse([row(kind=None, ticker=None, category=None)]).snapshot.items
    assert item.status is WatchlistRowStatus.MAPPED and item.mapping.instrument_type is None


def test_optional_columns_found_by_header_within_e_to_t_only():
    headers = (*BASIC_HEADERS, "Notiz", "Handelsplatz", "Währung", "Yahoo-Symbol")
    (item,) = parse(
        [["Synthetik AG", "1", "1", "1", "x", "GER", "EUR", "SYN.DE"]], headers=headers
    ).snapshot.items
    assert item.mapping == WatchlistMapping("SYN.DE", "GER", "EUR")
    far = (*BASIC_HEADERS, *("-",) * 16, "Yahoo-Symbol")  # column U
    (item,) = parse(
        [["Synthetik AG", "1", "1", "1"] + [None] * 16 + ["SYN.DE"]], headers=far
    ).snapshot.items
    assert item.status is WatchlistRowStatus.SHEET_ONLY


def test_duplicate_optional_header_is_noted_and_column_unread():
    headers = (*BASIC_HEADERS, "Yahoo-Symbol", "Yahoo-Symbol", "Handelsplatz", "Währung")
    result = parse(
        [["Synthetik AG", "1", "1", "1", "A.DE", "B.DE", "GER", "EUR"]], headers=headers
    )
    first = result.snapshot.warnings[0]
    assert (first.code, first.source_row) == ("duplicate_column", 1)
    (item,) = result.snapshot.items
    assert item.status is WatchlistRowStatus.FALLBACK and item.mapping is None
    assert "incomplete_mapping" in codes(item)


NO_HINT = {"symbol": None, "exchange": None, "currency": None}


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"symbol": None}, ["incomplete_mapping"]),
        ({"exchange": None}, ["incomplete_mapping"]),
        ({"currency": None}, ["incomplete_mapping"]),
        ({"symbol": None, "exchange": None}, ["incomplete_mapping", "incomplete_mapping"]),
        ({"symbol": "syn de"}, ["invalid_yahoo_symbol"]),
        ({"symbol": "^SYN"}, ["invalid_yahoo_symbol"]),
        ({"symbol": 123}, ["invalid_text"]),
        ({"exchange": "XYZ"}, ["unknown_exchange"]),
        ({"exchange": "Xetra"}, ["unknown_exchange"]),
        ({"exchange": "LSE", "symbol": "SYN.L", "currency": "GBP"}, ["unknown_exchange"]),
        ({"currency": "Euro"}, ["invalid_currency"]),
        ({"currency": "GBp"}, ["invalid_currency"]),
        ({"symbol": "SYN.F"}, ["symbol_exchange_conflict"]),  # GER braucht .DE
        ({"symbol": "SYN"}, ["symbol_exchange_conflict"]),
        ({"exchange": "NMS", "currency": "USD"}, ["symbol_exchange_conflict"]),  # .DE am US-Platz
        ({"exchange": "NMS", "symbol": "SYN"}, ["currency_exchange_conflict"]),  # EUR am US-Platz
        ({"kind": "Derivat"}, ["unsupported_derivative"]),
        ({"kind": "Derivat", **NO_HINT}, ["unsupported_derivative"]),
        ({"kind": "Fonds"}, ["invalid_instrument_type"]),
        ({"kind": "Fonds", **NO_HINT}, ["invalid_instrument_type"]),
    ],
)
def test_bad_yahoo_hints_warn_and_fall_back_to_the_sheet_value(kwargs, expected):
    result = parse([row(**kwargs)])
    (item,) = result.snapshot.items
    assert item.status is WatchlistRowStatus.FALLBACK and item.mapping is None
    assert codes(item) == expected
    assert all(w.source_row == 2 and w.field for w in item.warnings)
    assert item.sheet_price.price == Decimal("12.50")
    assert item.sheet_change_percent_points == Decimal("1.5")
    assert result.snapshot.status is SnapshotStatus.COMPLETE
    assert result.snapshot.warnings == item.warnings


def test_derivative_never_gets_a_mapping_but_keeps_its_type():
    (item,) = parse([row(kind="derivat")]).snapshot.items
    assert item.instrument_type is InstrumentType.DERIVATIVE and item.mapping is None


def test_descriptive_columns_alone_are_sheet_only_without_warning():
    (item,) = parse([row(**NO_HINT)]).snapshot.items
    assert item.status is WatchlistRowStatus.SHEET_ONLY and item.warnings == ()
    assert item.ticker == "SYN" and item.instrument_type is InstrumentType.STOCK


def test_duplicate_yahoo_symbol_first_row_keeps_mapping_later_rows_fall_back():
    result = parse(
        [
            row(name="Eins"),
            row(name="Zwei", symbol="syn.de"),
            row(name="Drei", symbol="OTHER.DE"),
            row(name="Vier", symbol="SYN.DE"),
        ]
    )
    assert [i.status for i in result.snapshot.items] == [
        WatchlistRowStatus.MAPPED,
        WatchlistRowStatus.FALLBACK,
        WatchlistRowStatus.MAPPED,
        WatchlistRowStatus.FALLBACK,
    ]
    second = result.snapshot.items[1]
    assert codes(second) == ["duplicate_yahoo_symbol"]
    assert "Zeile 2" in second.warnings[0].message and second.warnings[0].source_row == 3
    assert second.sheet_price.price == Decimal("12.50")


def test_invalid_row_does_not_reserve_its_symbol():
    result = parse([row(name="Eins", exchange="XYZ"), row(name="Zwei")])
    assert [i.status for i in result.snapshot.items] == [
        WatchlistRowStatus.FALLBACK,
        WatchlistRowStatus.MAPPED,
    ]


def test_warnings_never_contain_raw_cell_values():
    secret = "ZZ-SECRET-CELL"
    result = parse([[secret] * 10])
    text = " ".join(f"{w.message}{w.field}" for w in result.snapshot.warnings)
    assert result.snapshot.warnings and secret not in text


def test_two_hundred_fifty_plus_rows_parse_deterministically():
    rows = []
    for i in range(1, 281):
        base = [f"Synthetik {i}", f"{i},5", "1,0", "0,1"]
        if i % 3 == 0:
            rows.append([*base, None, None, f"S{i}.DE", "GER", "EUR"])
        elif i % 3 == 1:
            rows.append(base)  # reines 4-Spalten-Format
        else:
            rows.append([*base, None, "ETF", f"U{i}", "NMS", "USD"])
    rows.insert(100, [])
    result = parse(rows)
    assert len(result.snapshot.items) == 280 and len(result.excluded_rows) == 1
    assert result.snapshot.status is SnapshotStatus.COMPLETE and result.snapshot.warnings == ()
    statuses = [i.status for i in result.snapshot.items]
    assert statuses.count(WatchlistRowStatus.MAPPED) == 186
    assert statuses.count(WatchlistRowStatus.SHEET_ONLY) == 94
    assert result.snapshot.items[-1].source_row == 282
    assert result == parse(rows)


def test_model_contracts_reject_inconsistent_values():
    (item,) = parse([row()]).snapshot.items
    with pytest.raises(ValueError):
        WatchlistMapping("SYN.DE", "LSE", "EUR")
    with pytest.raises(ValueError):
        WatchlistMapping("SYN.DE", "GER", "eur")
    with pytest.raises(ValueError):
        WatchlistMapping("SYN.DE", "GER", "EUR", InstrumentType.DERIVATIVE)
    with pytest.raises(ValueError):  # a mapping exists exactly for MAPPED rows
        WatchlistItem("A", 2, item.sheet_price, status=WatchlistRowStatus.MAPPED)
    with pytest.raises(ValueError):
        WatchlistItem("A", 2, item.sheet_price, mapping=item.mapping)
    with pytest.raises(ValueError):
        WatchlistItem(" ", 2, item.sheet_price)
    with pytest.raises(ValueError):
        WatchlistItem("A", 0, item.sheet_price)
    with pytest.raises(ValueError):
        WatchlistSnapshot(IMPORTED, SnapshotStatus.PARTIAL)
    with pytest.raises(ValueError):
        WatchlistSnapshot(IMPORTED, SnapshotStatus.FAILED, (item,), (DataWarning("x", "y"),))
    with pytest.raises(TypeError):
        WatchlistSnapshot(IMPORTED, SnapshotStatus.COMPLETE, [item])


def test_parser_import_is_offline_and_needs_no_ui_or_provider_packages():
    code = (
        "import sys\n"
        "for name in ('streamlit', 'yfinance', 'requests', 'plotly'):\n"
        "    sys.modules[name] = None\n"
        "from datetime import datetime, timezone\n"
        "from portfolio_dashboard.sheet_parser import NumberLocale\n"
        "from portfolio_dashboard.watchlist_sheet import parse_watchlist_rows, BASIC_HEADERS\n"
        "r = parse_watchlist_rows(BASIC_HEADERS, [['A', '1', '1', '1']],\n"
        "    imported_at=datetime(2026, 1, 1, tzinfo=timezone.utc),\n"
        "    number_locale=NumberLocale.GERMAN)\n"
        "assert len(r.snapshot.items) == 1\n"
    )
    subprocess.run([sys.executable, "-I", "-c", code], check=True)


# --- PORTFOLIO_WATCHLIST_RANGE -------------------------------------------------


@pytest.mark.parametrize(
    ("text", "last_row", "columns"),
    [
        ("Watchlist!A1:D300", 300, 4),
        ("Watchlist!A1:J300", 300, 10),
        ("'Meine Watchlist'!A1:T400", 400, 20),
        ("'It''s'!A2:D251", 251, 4),
        ("Beobachtung!A1:E1000", 1000, 5),
    ],
)
def test_watchlist_range_accepts_d_to_t_with_large_lists(text, last_row, columns):
    parsed = parse_watchlist_range(text)
    assert parsed.last_row == last_row and parsed.column_count == columns
    assert parsed.row_count == last_row - parsed.first_row + 1 > 200
    assert text not in repr(parsed)


@pytest.mark.parametrize(
    "text",
    [
        "A1:D300",
        "Watchlist!A1:C300",
        "Watchlist!A1:U300",
        "Watchlist!B1:D300",
        "Watchlist!A0:D300",
        "Watchlist!A5:D4",
        "Watchlist!A1:D",
        "Watchlist!A:D",
        "Watchlist!a1:d300",
        "Watchlist!A1:D300\n",
        "!A1:D300",
        "'Watchlist!A1:D300",
        None,
        5,
    ],
)
def test_watchlist_range_rejects_other_shapes_without_leaking_the_value(text):
    with pytest.raises(ConfigurationError) as error:
        parse_watchlist_range(text)
    assert "PORTFOLIO_WATCHLIST_RANGE" in str(error.value)
    assert not isinstance(text, str) or text.strip() not in str(error.value)


def local_values(**extra):
    return {
        "PORTFOLIO_MODE": "local",
        "PORTFOLIO_SHEET_ID": "synthetic-source-id",
        "PORTFOLIO_SHEET_RANGE": "synthetic-source-range",
        "PORTFOLIO_SHEETS_CREDENTIALS_FILE": "/nonexistent/synthetic-local-file.json",
        **extra,
    }


def test_load_config_watchlist_range_is_optional_and_trimmed():
    assert load_config(local_values()).source.watchlist_range is None
    blank = load_config(local_values(PORTFOLIO_WATCHLIST_RANGE="  "))
    assert blank.source.watchlist_range is None
    config = load_config(local_values(PORTFOLIO_WATCHLIST_RANGE=" Watchlist!A1:J300 "))
    assert config.mode is Mode.LOCAL
    assert config.source.watchlist_range == "Watchlist!A1:J300"
    assert "Watchlist!A1" not in repr(config)


@pytest.mark.parametrize(
    "value", ["Watchlist!A1:C300", "A1:D300", "<PRIVATE_WATCHLIST_RANGE>", "CHANGE_ME"]
)
def test_load_config_rejects_invalid_watchlist_range(value):
    with pytest.raises(ConfigurationError, match="PORTFOLIO_WATCHLIST_RANGE") as error:
        load_config(local_values(PORTFOLIO_WATCHLIST_RANGE=value))
    assert value not in str(error.value)


def test_demo_discards_watchlist_range_even_if_inherited():
    inherited = {"PORTFOLIO_MODE": "demo", "PORTFOLIO_WATCHLIST_RANGE": "Watchlist!A1:D300"}
    assert load_config(inherited).source.watchlist_range is None
    inherited["PORTFOLIO_WATCHLIST_RANGE"] = "invalid"
    assert load_config(inherited).mode is Mode.DEMO
