"""Pure Watchlist-tab parsing (ADR 0011); no source access, provider calls or IDs.

Columns A-D are the basic schema of the existing tab; optional columns E-T are
found by header. Sheet values are labelled fallbacks, never provider prices.
A Yahoo mapping is only a sheet-maintained candidate that the runtime must still
check against the provider metadata; it establishes no verified instrument ID.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from portfolio_dashboard.models import (
    DataWarning,
    InstrumentType,
    Provenance,
    QuoteUnit,
    SnapshotStatus,
    Valuation,
    ValuationSource,
    ValueStatus,
    _aware,
    _decimal,
    _row,
    _text,
    _warnings,
)
from portfolio_dashboard.sheet_parser import (
    NumberLocale,
    _missing,
    _normal_header,
    _number,
)
from portfolio_dashboard.sheet_parser import (
    _text as _cell_text,
)

SHEET_VALUE_LABEL = "Sheet-Wert, Kursstand unbekannt"
BASIC_HEADERS = (
    "Position",
    "Letzter Kurs (EUR)",
    "Veränd. (%)",
    "Nettoveränd. (EUR)",
)
_BASIC_FIELDS = ("name", "sheet_price_eur", "sheet_change_percent", "sheet_net_change_eur")
OPTIONAL_HEADERS = ("Ticker", "Typ", "Yahoo-Symbol", "Handelsplatz", "Währung", "Kategorie")
_OPTIONAL_FIELDS = ("ticker", "instrument_type", "symbol", "exchange", "currency", "category")
_LAST_COLUMN = 20  # T; cells further right are ignored.

# Yahoo exchangeName -> (exchange timezone, required Yahoo symbol suffix or None).
EXCHANGES: dict[str, tuple[str, str | None]] = {
    "GER": ("Europe/Berlin", ".DE"),
    "FRA": ("Europe/Berlin", ".F"),
    "STU": ("Europe/Berlin", ".SG"),
    "MUN": ("Europe/Berlin", ".MU"),
    "DUS": ("Europe/Berlin", ".DU"),
    "NMS": ("America/New_York", None),
    "NGM": ("America/New_York", None),
    "NCM": ("America/New_York", None),
    "NYQ": ("America/New_York", None),
    "ASE": ("America/New_York", None),
    "PCX": ("America/New_York", None),
}
_TYPES = {
    "aktie": InstrumentType.STOCK,
    "etf": InstrumentType.ETF,
    "derivat": InstrumentType.DERIVATIVE,
}
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,14}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_EUR = QuoteUnit("EUR", Decimal("1"))


class WatchlistRowStatus(Enum):
    SHEET_ONLY = "sheet_only"  # no Yahoo hint: sheet value, other periods n. v.
    MAPPED = "mapped"  # complete, consistent candidate for the provider
    FALLBACK = "fallback"  # incomplete/contradictory hint: sheet value only


@dataclass(frozen=True, slots=True)
class WatchlistMapping:
    """Sheet-maintained Yahoo candidate (symbol, exchangeName, currency)."""

    symbol: str
    exchange: str
    currency: str
    instrument_type: InstrumentType | None = None

    def __post_init__(self) -> None:
        _text(self.symbol, "symbol")
        if self.exchange not in EXCHANGES:
            raise ValueError("exchange must be a supported Yahoo exchangeName")
        QuoteUnit(self.currency, None)  # three-letter uppercase code
        if self.instrument_type is InstrumentType.DERIVATIVE:
            raise ValueError("derivatives have no provider mapping")

    @property
    def timezone(self) -> str:
        return EXCHANGES[self.exchange][0]


@dataclass(frozen=True, slots=True)
class WatchlistItem:
    """One sheet row: price development only, no quantity, cost or target price.

    ``sheet_price`` is an EUR Sheet value with unknown price time (see
    SHEET_VALUE_LABEL); a blank cell is MISSING without a warning. The sheet
    change is percentage points as read, never rescaled.
    """

    name: str
    source_row: int
    sheet_price: Valuation
    sheet_change_percent_points: Decimal | None = None
    sheet_net_change_eur: Decimal | None = None
    status: WatchlistRowStatus = WatchlistRowStatus.SHEET_ONLY
    mapping: WatchlistMapping | None = None
    ticker: str | None = None
    instrument_type: InstrumentType | None = None
    category: str | None = None
    warnings: tuple[DataWarning, ...] = ()

    def __post_init__(self) -> None:
        _text(self.name, "name")
        _row(self.source_row)
        if not isinstance(self.sheet_price, Valuation):
            raise TypeError("sheet_price must be Valuation")
        for value, name in (
            (self.sheet_change_percent_points, "sheet_change_percent_points"),
            (self.sheet_net_change_eur, "sheet_net_change_eur"),
        ):
            if value is not None:
                _decimal(value, name)
        if not isinstance(self.status, WatchlistRowStatus):
            raise TypeError("status must be WatchlistRowStatus")
        if (self.status is WatchlistRowStatus.MAPPED) != (self.mapping is not None):
            raise ValueError("a mapping exists exactly for MAPPED rows")
        if self.mapping is not None and not isinstance(self.mapping, WatchlistMapping):
            raise TypeError("mapping must be WatchlistMapping or None")
        for value, name in ((self.ticker, "ticker"), (self.category, "category")):
            if value is not None:
                _text(value, name)
        _warnings(self.warnings)


@dataclass(frozen=True, slots=True)
class WatchlistSnapshot:
    """One import attempt; same status contract as PortfolioSnapshot.

    COMPLETE may carry row warnings (hints, fallbacks); PARTIAL means rejected
    rows; FAILED means a schema/envelope problem and no items.
    """

    attempted_at: datetime
    status: SnapshotStatus
    items: tuple[WatchlistItem, ...] = ()
    warnings: tuple[DataWarning, ...] = ()

    def __post_init__(self) -> None:
        _aware(self.attempted_at, "attempted_at")
        if not isinstance(self.status, SnapshotStatus):
            raise TypeError("status must be SnapshotStatus")
        if not isinstance(self.items, tuple) or not all(
            isinstance(item, WatchlistItem) for item in self.items
        ):
            raise TypeError("items must be a tuple of WatchlistItem")
        _warnings(self.warnings)
        if self.status is SnapshotStatus.FAILED and self.items:
            raise ValueError("failed snapshot cannot contain items")
        if self.status is not SnapshotStatus.COMPLETE and not self.warnings:
            raise ValueError("partial or failed snapshot requires an explanation")


@dataclass(frozen=True, slots=True)
class WatchlistExcludedRow:
    source_row: int
    reason: str  # empty, invalid_row


@dataclass(frozen=True, slots=True)
class WatchlistParseResult:
    snapshot: WatchlistSnapshot
    excluded_rows: tuple[WatchlistExcludedRow, ...] = ()


def _warning(code: str, message: str, field: str, row: int | None = None) -> DataWarning:
    return DataWarning(code, message, field, row)


def _schema(
    headers: Sequence[object], header_row: int
) -> tuple[dict[str, int], list[DataWarning], list[DataWarning]]:
    """Locate A-D by header (each exactly once) and optional E-T columns."""
    normalized = [_normal_header(cell) for cell in headers[:_LAST_COLUMN]]
    mapping: dict[str, int] = {}
    errors: list[DataWarning] = []
    notes: list[DataWarning] = []
    for title, field in zip(BASIC_HEADERS, _BASIC_FIELDS):
        matches = [i for i, h in enumerate(normalized[:4]) if h == _normal_header(title)]
        if len(matches) != 1:
            errors.append(
                _warning(
                    "missing_column" if not matches else "duplicate_column",
                    f"Schema A–D: Überschrift '{title}' fehlt oder ist mehrfach vorhanden.",
                    field,
                    header_row,
                )
            )
        else:
            mapping[field] = matches[0]
    for title, field in zip(OPTIONAL_HEADERS, _OPTIONAL_FIELDS):
        matches = [
            i for i, h in enumerate(normalized) if i >= 4 and h == _normal_header(title)
        ]
        if len(matches) == 1:
            mapping[field] = matches[0]
        elif len(matches) > 1:
            notes.append(
                _warning(
                    "duplicate_column",
                    f"Optionale Überschrift '{title}' ist mehrfach vorhanden und wird nicht gelesen.",
                    field,
                    header_row,
                )
            )
    return mapping, errors, notes


def _mapping(
    cells: dict[str, object],
    instrument_type: InstrumentType | None,
    type_unreadable: bool,
    source_row: int,
    symbol_rows: dict[str, int],
) -> tuple[WatchlistMapping | None, WatchlistRowStatus, list[DataWarning]]:
    """Decide the Yahoo candidate; any problem falls back to the sheet value."""
    symbol = _cell_text(cells.get("symbol"))
    exchange = _cell_text(cells.get("exchange"))
    currency = _cell_text(cells.get("currency"))
    warnings: list[DataWarning] = []

    def problem(code: str, message: str, field: str) -> None:
        warnings.append(_warning(code, message, field, source_row))

    hinted = any(not _missing(cells.get(name)) for name in ("symbol", "exchange", "currency"))
    if instrument_type is InstrumentType.DERIVATIVE:
        problem(
            "unsupported_derivative",
            "Derivate erhalten keine Yahoo-Zuordnung; nur der Sheet-Wert wird angezeigt.",
            "instrument_type",
        )
    if not hinted and not (warnings or type_unreadable):
        return None, WatchlistRowStatus.SHEET_ONLY, warnings
    if not hinted or instrument_type is InstrumentType.DERIVATIVE or type_unreadable:
        # The unreadable type was already reported on the row; sheet value only.
        return None, WatchlistRowStatus.FALLBACK, warnings

    for name, value, field in (
        ("Yahoo-Symbol", symbol, "symbol"),
        ("Handelsplatz", exchange, "exchange"),
        ("Währung", currency, "currency"),
    ):
        if value is None:
            if _missing(cells.get(field)):
                problem(
                    "incomplete_mapping",
                    f"Yahoo-Zuordnung unvollständig ({name} fehlt); Sheet-Wert wird verwendet.",
                    field,
                )
            else:
                problem("invalid_text", f"{name} muss Text enthalten.", field)
    if warnings:
        return None, WatchlistRowStatus.FALLBACK, warnings

    symbol = symbol.upper()
    exchange = exchange.upper()
    if not _SYMBOL.fullmatch(symbol):
        problem("invalid_yahoo_symbol", "Yahoo-Symbol hat kein gültiges Format.", "symbol")
    if exchange not in EXCHANGES:
        problem(
            "unknown_exchange",
            "Handelsplatz ist kein unterstützter Yahoo-exchangeName (GER, FRA, STU, MUN, DUS, NMS, NGM, NCM, NYQ, ASE, PCX).",
            "exchange",
        )
    if not _CURRENCY.fullmatch(currency):
        problem(
            "invalid_currency",
            "Währung muss ein dreibuchstabiger Großbuchstaben-Code sein.",
            "currency",
        )
    if warnings:
        return None, WatchlistRowStatus.FALLBACK, warnings

    suffix = EXCHANGES[exchange][1]
    german = suffix is not None
    if (german and not symbol.endswith(suffix)) or (not german and "." in symbol):
        problem(
            "symbol_exchange_conflict",
            "Yahoo-Symbol und Handelsplatz widersprechen sich (Symbolendung bzw. US-Schreibweise).",
            "symbol",
        )
    if not german and currency != "USD":
        problem(
            "currency_exchange_conflict",
            "US-Handelsplätze notieren in USD; Währung widerspricht dem Handelsplatz.",
            "currency",
        )
    if warnings:
        return None, WatchlistRowStatus.FALLBACK, warnings
    if symbol in symbol_rows:
        problem(
            "duplicate_yahoo_symbol",
            f"Yahoo-Symbol kommt bereits in Zeile {symbol_rows[symbol]} vor; "
            "diese Zeile verwendet nur den Sheet-Wert.",
            "symbol",
        )
        return None, WatchlistRowStatus.FALLBACK, warnings
    symbol_rows[symbol] = source_row
    return (
        WatchlistMapping(symbol, exchange, currency, instrument_type),
        WatchlistRowStatus.MAPPED,
        warnings,
    )


def parse_watchlist_rows(
    headers: Sequence[object],
    rows: Iterable[Sequence[object]],
    *,
    imported_at: datetime,
    number_locale: NumberLocale,
    header_row: int = 1,
) -> WatchlistParseResult:
    """Parse header-relative watchlist rows using only supplied inputs.

    The four basic headers must appear once each in A-D; optional headers are read
    from E-T when they occur exactly once. Blank rows are ignored. A row without
    a name is rejected (PARTIAL); every other problem keeps the row with a
    warning. Messages use fixed text, never raw cell values. Duplicate Yahoo
    symbols: the first valid row keeps the mapping, later ones fall back.
    """
    if type(header_row) is not int or header_row < 1:
        raise ValueError("header_row must be a positive integer")
    if not isinstance(number_locale, NumberLocale):
        raise TypeError("number_locale must be NumberLocale")
    provenance = Provenance(ValuationSource.SHEET, imported_at)
    mapping, errors, notes = _schema(headers, header_row)
    if errors:
        return WatchlistParseResult(
            WatchlistSnapshot(imported_at, SnapshotStatus.FAILED, warnings=tuple(errors))
        )

    items: list[WatchlistItem] = []
    warnings: list[DataWarning] = list(notes)
    excluded: list[WatchlistExcludedRow] = []
    symbol_rows: dict[str, int] = {}
    rejected = False
    for source_row, row in enumerate(rows, start=header_row + 1):
        cells = {
            field: row[index] if index < len(row) else None
            for field, index in mapping.items()
        }
        if all(_missing(cell) for cell in cells.values()):
            excluded.append(WatchlistExcludedRow(source_row, "empty"))
            continue
        name = _cell_text(cells["name"])
        if name is None:
            warnings.append(
                _warning(
                    "invalid_name",
                    "Positionsname fehlt oder ist kein Text.",
                    "name",
                    source_row,
                )
            )
            excluded.append(WatchlistExcludedRow(source_row, "invalid_row"))
            rejected = True
            continue

        row_warnings: list[DataWarning] = []
        price, price_status = _number(cells["sheet_price_eur"], number_locale, euro=True)
        if price is not None and price < 0:
            price, price_status = None, ValueStatus.INVALID
        price_warnings = ()
        if price_status is ValueStatus.INVALID:
            price_warnings = (
                _warning(
                    "invalid_price",
                    "Letzter Kurs (EUR) ist ungültig; kein Sheet-Ersatzwert.",
                    "sheet_price_eur",
                    source_row,
                ),
            )
        row_warnings.extend(price_warnings)
        valuation = Valuation(price, price_status, provenance, _EUR, price_warnings)

        change, change_status = _number(
            cells["sheet_change_percent"], number_locale, percent=True
        )
        if change is not None and change < -100:
            change, change_status = None, ValueStatus.INVALID
        if change_status is ValueStatus.INVALID:
            row_warnings.append(
                _warning(
                    "invalid_change",
                    "Veränd. (%) ist ungültig oder unter −100 Prozentpunkten; kein Sheet-Wert.",
                    "sheet_change_percent",
                    source_row,
                )
            )
        net, net_status = _number(cells["sheet_net_change_eur"], number_locale, euro=True)
        if net_status is ValueStatus.INVALID:
            row_warnings.append(
                _warning(
                    "invalid_net_change",
                    "Nettoveränd. (EUR) ist ungültig; kein Sheet-Wert.",
                    "sheet_net_change_eur",
                    source_row,
                )
            )

        texts = {}
        for field in ("ticker", "category"):
            texts[field] = _cell_text(cells.get(field))
            if texts[field] is None and not _missing(cells.get(field)):
                row_warnings.append(
                    _warning(
                        "invalid_text",
                        "Feld muss Text enthalten; Metadatum bleibt unbekannt.",
                        field,
                        source_row,
                    )
                )
        instrument_type = None
        type_cell = cells.get("instrument_type")
        type_unreadable = False
        if not _missing(type_cell):
            instrument_type = _TYPES.get((_cell_text(type_cell) or "").casefold())
            type_unreadable = instrument_type is None
            if type_unreadable:
                row_warnings.append(
                    _warning(
                        "invalid_instrument_type",
                        "Typ muss Aktie, ETF oder Derivat sein; Typ bleibt unbekannt.",
                        "instrument_type",
                        source_row,
                    )
                )
        candidate, status, mapping_warnings = _mapping(
            cells, instrument_type, type_unreadable, source_row, symbol_rows
        )
        row_warnings.extend(mapping_warnings)
        items.append(
            WatchlistItem(
                name,
                source_row,
                valuation,
                change,
                net if net_status is ValueStatus.AVAILABLE else None,
                status,
                candidate,
                texts["ticker"],
                instrument_type,
                texts["category"],
                tuple(row_warnings),
            )
        )
        warnings.extend(row_warnings)

    snapshot = WatchlistSnapshot(
        imported_at,
        SnapshotStatus.PARTIAL if rejected else SnapshotStatus.COMPLETE,
        tuple(items),
        tuple(warnings),
    )
    return WatchlistParseResult(snapshot, tuple(excluded))
