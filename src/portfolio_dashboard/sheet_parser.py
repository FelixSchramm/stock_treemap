"""Pure A–M Sheet parsing with optional header-bound metadata through X.

The caller supplies the number locale and import timestamp. Only the documented
EUR/percentage-point columns establish units; source symbols establish no ID.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from zoneinfo import ZoneInfo

from portfolio_dashboard.models import (
    CostBasis,
    CostBasisStatus,
    DataWarning,
    Instrument,
    InstrumentType,
    PortfolioSnapshot,
    Position,
    PositionActivity,
    Provenance,
    QuoteUnit,
    SnapshotStatus,
    Valuation,
    ValuationSource,
    ValueStatus,
)

SHEET_HEADERS = (
    "Position",
    "Ticker",
    "Typ",
    "Anzahl",
    "Symbol (Google Finance)",
    "Währung",
    "Kurs (EUR)",
    "Aktueller Wert (EUR)",
    "Investiert (EUR)",
    "G/V (%)",
    "G/V (EUR)",
    "Kategorie",
    "Gewicht (%)",
)
_FIELDS = (
    "name",
    "display_ticker",
    "instrument_type",
    "quantity",
    "source_symbol",
    "quote_currency",
    "sheet_price_eur",
    "current_value_eur",
    "cost_basis_eur",
    "gain_loss_percent_points",
    "gain_loss_eur",
    "category",
    "weight_percent_points",
)
_TYPES = {
    "aktie": InstrumentType.STOCK,
    "etf": InstrumentType.ETF,
    "derivat": InstrumentType.DERIVATIVE,
}
_SUMMARY_LABELS = {
    "summe",
    "gesamt",
    "gesamtwert",
    "total",
    "grand total",
    "vergleich",
    "benchmark",
}
_EUR = QuoteUnit("EUR", Decimal("1"))
_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")
_BERLIN = ZoneInfo("Europe/Berlin")
_SERIAL_EPOCH = date(1899, 12, 30)
# Optional columns right of M: (field, header test), searched only in N–X.
_OPTIONAL = (
    ("source_isin", lambda header: header == "isin"),
    ("source_venue", lambda header: header == "handelsplatz"),
    ("price_date", lambda header: header.startswith("kursdatum")),
    ("yahoo_symbol", lambda header: header == "yahoo-symbol"),
    ("yahoo_venue", lambda header: header == "yahoo-handelsplatz"),
    ("yahoo_equity_form", lambda header: header == "yahoo-aktienform"),
    ("yahoo_share_class", lambda header: header == "yahoo-anteilsklasse"),
)


class NumberLocale(Enum):
    GERMAN = "de_DE"
    ENGLISH = "en_US"


@dataclass(frozen=True, slots=True)
class SheetMetadata:
    """Explicit source facts, independent of runtime credentials/configuration.

    Verification means the source's I column was checked to represent the cost
    of *remaining* units after sales. That fact is unknown for the actual source.
    An import time never establishes the actual market price timestamp.
    """

    number_locale: NumberLocale
    cost_basis_verified_remaining: bool = False
    sheet_price_observed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.number_locale, NumberLocale):
            raise TypeError("number_locale must be NumberLocale")
        if type(self.cost_basis_verified_remaining) is not bool:
            raise TypeError("cost_basis_verified_remaining must be bool")
        if self.sheet_price_observed_at is not None:
            # Reuse the domain's aware-timestamp contract without consulting a clock.
            Provenance(ValuationSource.SHEET, self.sheet_price_observed_at)


@dataclass(frozen=True, slots=True)
class SheetReferenceValues:
    """Source results for plausibility only; never inputs to portfolio totals."""

    source_row: int
    current_value_eur: Decimal | None
    gain_loss_percent_points: Decimal | None
    gain_loss_eur: Decimal | None
    weight_percent_points: Decimal | None


@dataclass(frozen=True, slots=True)
class ExcludedRow:
    source_row: int
    reason: str  # empty, summary, zero_quantity, invalid_position


@dataclass(frozen=True, slots=True)
class SheetListingInput:
    """Explicit owner-maintained alternative; validation belongs to the loader."""

    source_row: int
    symbol: str
    venue: str
    equity_form: str | None = None
    share_class: str | None = None


@dataclass(frozen=True, slots=True)
class SheetParseResult:
    """Snapshot preserves signed nonzero rows; active_positions is the long set.

    Negative rows are retained for separate review, never converted to longs.
    Rejected holdings yield PARTIAL; schema failure yields FAILED. Missing price,
    cost or optional metadata preserves the holding with explicit warnings.
    """

    snapshot: PortfolioSnapshot
    references: tuple[SheetReferenceValues, ...] = ()
    excluded_rows: tuple[ExcludedRow, ...] = ()
    yahoo_listings: tuple[SheetListingInput, ...] = ()

    @property
    def active_positions(self) -> tuple[Position, ...]:
        return tuple(
            p for p in self.snapshot.positions if p.activity is PositionActivity.ACTIVE
        )

    @property
    def negative_positions(self) -> tuple[Position, ...]:
        return tuple(
            p
            for p in self.snapshot.positions
            if p.activity is PositionActivity.NEGATIVE
        )


def _missing(cell: object) -> bool:
    return cell is None or (
        isinstance(cell, str) and re.sub(r"\s+", "", cell).casefold() in {"", "k.a."}
    )


def _text(cell: object) -> str | None:
    return cell.strip() if isinstance(cell, str) and not _missing(cell) else None


def _normal_header(cell: object) -> str:
    return " ".join(cell.split()).casefold() if isinstance(cell, str) else ""


def _warning(code: str, message: str, field: str, row: int) -> DataWarning:
    return DataWarning(code, message, field, row)


def _number(
    cell: object,
    locale: NumberLocale,
    *,
    euro: bool = False,
    percent: bool = False,
) -> tuple[Decimal | None, ValueStatus]:
    if _missing(cell):
        return None, ValueStatus.MISSING
    if type(cell) in (int, float) or isinstance(cell, Decimal):
        value = Decimal(str(cell))
    elif isinstance(cell, str):
        text = (
            cell.strip()
            .replace("\N{NO-BREAK SPACE}", " ")
            .replace("\N{NARROW NO-BREAK SPACE}", " ")
        )
        if euro:
            # A single EUR marker is allowed only in the schema's EUR columns.
            text = re.sub(r"^(?:€|EUR)\s*|\s*(?:€|EUR)$", "", text, count=1).strip()
        if percent and text.endswith("%"):
            text = text[:-1].strip()  # Schema already supplies percentage points.
        group, decimal = (".", ",") if locale is NumberLocale.GERMAN else (",", ".")
        integer = rf"(?:[0-9]+|[0-9]{{1,3}}(?:{re.escape(group)}[0-9]{{3}})+|[0-9]{{1,3}}(?: [0-9]{{3}})+)"
        fraction = rf"{re.escape(decimal)}[0-9]+"
        if not re.fullmatch(rf"[+-]?(?:{integer}(?:{fraction})?|{fraction})", text):
            return None, ValueStatus.INVALID
        value = Decimal(text.replace(group, "").replace(" ", "").replace(decimal, "."))
    else:
        return None, ValueStatus.INVALID
    return (
        (value, ValueStatus.AVAILABLE)
        if value.is_finite()
        else (None, ValueStatus.INVALID)
    )


def _schema(
    headers: Sequence[object], header_row: int
) -> tuple[dict[str, int], list[DataWarning]]:
    mapping: dict[str, int] = {}
    warnings: list[DataWarning] = []
    normalized = [_normal_header(cell) for cell in headers[:13]]
    for title, field in zip(SHEET_HEADERS, _FIELDS):
        matches = [
            i for i, header in enumerate(normalized) if header == _normal_header(title)
        ]
        if len(matches) != 1:
            code = "missing_column" if not matches else "duplicate_column"
            warnings.append(
                _warning(
                    code,
                    f"Schema A–M: Überschrift '{title}' fehlt oder ist mehrfach vorhanden.",
                    field,
                    header_row,
                )
            )
        else:
            mapping[field] = matches[0]
    return mapping, warnings


def _optional_columns(headers: Sequence[object]) -> dict[str, int]:
    """Locate optional columns in N–X; absent or duplicated ones are unread."""
    normalized = [_normal_header(cell) for cell in headers[13:24]]
    found: dict[str, int] = {}
    for field, test in _OPTIONAL:
        matches = [i for i, header in enumerate(normalized) if test(header)]
        if len(matches) == 1:
            found[field] = 13 + matches[0]
    return found


def _price_date(cell: object, imported_at: datetime) -> datetime | None:
    """Read a date-only cell; day precision, so use the start of the Berlin day.

    Accepts dd.mm.yyyy, ISO text or a Sheets serial number (UNFORMATTED_VALUE)
    after the 1899-12-30 epoch and not after imported_at.
    """
    day: date | None = None
    if isinstance(cell, bool):
        return None
    if isinstance(cell, (int, float, Decimal)):
        if isinstance(cell, Decimal) and not cell.is_finite():
            return None
        # Bound the value before converting; NaN/infinity and huge serials are invalid.
        if 1 <= cell < 100000 and cell == int(cell):
            day = _SERIAL_EPOCH + timedelta(days=int(cell))
    elif isinstance(cell, str):
        text = cell.strip()
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                day = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
    if day is None or day <= _SERIAL_EPOCH:
        return None
    observed = datetime(day.year, day.month, day.day, tzinfo=_BERLIN)
    return observed if observed <= imported_at else None


def _is_summary(cells: dict[str, object]) -> bool:
    name = _normal_header(cells["name"])
    label = name in _SUMMARY_LABELS or name.startswith(
        (
        "summe ",
        "gesamt ",
        "vergleich ",
        "benchmark ",
        "brokerage",
        "differenz ",
    )
    )
    # An instrument named e.g. 'Total' with a valid type remains a holding.
    return label and _text(cells["instrument_type"]) is None


def parse_sheet_rows(
    headers: Sequence[object],
    rows: Iterable[Sequence[object]],
    *,
    imported_at: datetime,
    metadata: SheetMetadata,
    header_row: int = 1,
) -> SheetParseResult:
    """Parse header-relative rows using only supplied, deterministic inputs.

    All thirteen headers are required within A–M, in any order; additional cells
    are read only through explicit optional headers. Short rows have missing trailing
    cells. All diagnostic messages use fixed schema text, never raw cell values.
    Number strings must match the explicit locale; numeric cells are already
    unformatted. J and M retain percentage-point magnitude, even with a '%' sign.
    """
    if type(header_row) is not int or header_row < 1:
        raise ValueError("header_row must be a positive integer")
    if not isinstance(metadata, SheetMetadata):
        raise TypeError("metadata must be SheetMetadata")
    provenance = Provenance(
        ValuationSource.SHEET, imported_at, metadata.sheet_price_observed_at
    )
    mapping, warnings = _schema(headers, header_row)
    optional = _optional_columns(headers)
    if warnings:
        return SheetParseResult(
            PortfolioSnapshot(
                imported_at, SnapshotStatus.FAILED, warnings=tuple(warnings)
            )
        )
    for field, test in _OPTIONAL:
        if field.startswith("yahoo_") and sum(test(_normal_header(cell)) for cell in headers[13:24]) > 1:
            warnings.append(_warning(
                "duplicate_yahoo_column",
                "Yahoo-Ersatzspalte mehrfach vorhanden; diese Angabe wird ignoriert.",
                field, header_row,
            ))

    positions: list[Position] = []
    references: list[SheetReferenceValues] = []
    excluded: list[ExcludedRow] = []
    yahoo_listings: list[SheetListingInput] = []
    rejected = False
    for source_row, row in enumerate(rows, start=header_row + 1):
        cells = {
            field: row[index] if index < len(row) else None
            for field, index in mapping.items()
        }
        extra = {
            field: row[index] if index < len(row) else None
            for field, index in optional.items()
        }
        if all(_missing(cell) for cell in cells.values()):
            excluded.append(ExcludedRow(source_row, "empty"))
            continue
        if _is_summary(cells):
            excluded.append(ExcludedRow(source_row, "summary"))
            continue

        row_warnings: list[DataWarning] = []
        name = _text(cells["name"])
        instrument_type = _TYPES.get((_text(cells["instrument_type"]) or "").casefold())
        quantity, quantity_status = _number(cells["quantity"], metadata.number_locale)
        if name is None:
            row_warnings.append(
                _warning(
                    "invalid_name",
                    "Positionsname fehlt oder ist kein Text.",
                    "name",
                    source_row,
                )
            )
        if instrument_type is None:
            row_warnings.append(
                _warning(
                    "invalid_instrument_type",
                    "Instrumenttyp muss Aktie, ETF oder Derivat sein.",
                    "instrument_type",
                    source_row,
                )
            )
        if quantity_status is not ValueStatus.AVAILABLE:
            row_warnings.append(
                _warning(
                    "invalid_quantity",
                    "Bestand fehlt oder ist keine gültige endliche Zahl für das Zahlenformat.",
                    "quantity",
                    source_row,
                )
            )
        if row_warnings:
            warnings.extend(row_warnings)
            excluded.append(ExcludedRow(source_row, "invalid_position"))
            rejected = True
            continue
        if quantity == 0:
            excluded.append(ExcludedRow(source_row, "zero_quantity"))
            continue
        if quantity < 0:
            row_warnings.append(
                _warning(
                    "negative_quantity",
                    "Negativer Bestand: gesondert prüfen; keine aktive Long-Position.",
                    "quantity",
                    source_row,
                )
            )

        texts = {}
        for field in ("display_ticker", "source_symbol", "category", "quote_currency"):
            texts[field] = _text(cells[field])
            if texts[field] is None and not _missing(cells[field]):
                row_warnings.append(
                    _warning(
                        "invalid_text",
                        "Feld muss Text enthalten; Metadatum bleibt unbekannt.",
                        field,
                        source_row,
                    )
                )
        isin = _text(extra.get("source_isin"))
        if isin is not None and not _ISIN.fullmatch(isin):
            row_warnings.append(
                _warning(
                    "invalid_isin",
                    "ISIN-Hinweis hat kein gültiges Format und wird verworfen.",
                    "source_isin",
                    source_row,
                )
            )
            isin = None
        venue = _text(extra.get("source_venue"))
        yahoo_fields = ("yahoo_symbol", "yahoo_venue", "yahoo_equity_form", "yahoo_share_class")
        if any(not _missing(extra.get(field)) for field in yahoo_fields):
            symbol, yahoo_venue, equity_form, share_class = (
                _text(extra.get(field)) for field in yahoo_fields
            )
            valid = (
                isin is not None and symbol is not None and yahoo_venue is not None
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.^=_-]{0,63}", symbol)
                and (
                    instrument_type is InstrumentType.STOCK
                    and equity_form in {"share", "adr"} and share_class is None
                    or instrument_type is InstrumentType.ETF
                    and share_class is not None and equity_form is None
                )
                and all(_missing(extra.get(field)) or isinstance(extra.get(field), str)
                        for field in yahoo_fields)
            )
            if valid:
                yahoo_listings.append(SheetListingInput(
                    source_row, symbol, yahoo_venue, equity_form, share_class
                ))
            else:
                row_warnings.append(_warning(
                    "invalid_yahoo_listing",
                    "Yahoo-Ersatzzuordnung unvollständig oder ungültig; ignoriert.",
                    "yahoo_listing", source_row,
                ))
        currency = texts["quote_currency"]
        unit = None
        if currency is not None:
            if re.fullmatch(r"[A-Z]{3}", currency):
                unit = QuoteUnit(
                    currency, None
                )  # Currency alone proves no major/minor unit.
            else:
                row_warnings.append(
                    _warning(
                        "invalid_currency",
                        "Währung muss ein dreibuchstabiger Großbuchstaben-Code sein; Einheit bleibt unbekannt.",
                        "quote_currency",
                        source_row,
                    )
                )

        price, price_status = _number(
            cells["sheet_price_eur"], metadata.number_locale, euro=True
        )
        if price is not None and price < 0:
            price, price_status = None, ValueStatus.INVALID
        price_warnings = []
        if price_status is not ValueStatus.AVAILABLE:
            price_warnings.append(
                _warning(
                    "missing_price"
                    if price_status is ValueStatus.MISSING
                    else "invalid_price",
                    "EUR-Sheet-Kurs fehlt oder ist ungültig; kein bewertbarer Ersatzkurs.",
                    "sheet_price_eur",
                    source_row,
                )
            )
        row_provenance = provenance
        if "price_date" in extra and not _missing(extra["price_date"]):
            observed = _price_date(extra["price_date"], imported_at)
            row_provenance = Provenance(ValuationSource.SHEET, imported_at, observed)
            if observed is None:
                row_warnings.append(
                    _warning(
                        "invalid_price_date",
                        "Kursdatum ist kein gültiges Datum; Kursstand bleibt unbekannt.",
                        "price_date",
                        source_row,
                    )
                )
        if row_provenance.observed_at is None and price_status is ValueStatus.AVAILABLE:
            price_warnings.append(
                _warning(
                    "unknown_price_time",
                    "Sheet-Ersatzkurs mit unbekanntem tatsächlichem Kurszeitpunkt.",
                    "sheet_price_eur",
                    source_row,
                )
            )
        row_warnings.extend(price_warnings)
        valuation = Valuation(
            price, price_status, row_provenance, _EUR, tuple(price_warnings)
        )

        cost, cost_status = _number(
            cells["cost_basis_eur"], metadata.number_locale, euro=True
        )
        if cost is not None and cost < 0:
            cost, cost_status = None, ValueStatus.INVALID
        if cost_status is not ValueStatus.AVAILABLE:
            basis = CostBasis(
                status=CostBasisStatus.MISSING
                if cost_status is ValueStatus.MISSING
                else CostBasisStatus.INVALID
            )
            row_warnings.append(
                _warning(
                    "missing_cost_basis"
                    if cost_status is ValueStatus.MISSING
                    else "invalid_cost_basis",
                    "EUR-Einstand fehlt oder ist ungültig; Seit-Kauf-Rendite nicht verfügbar.",
                    "cost_basis_eur",
                    source_row,
                )
            )
        else:
            basis = CostBasis(
                cost,
                CostBasisStatus.VERIFIED_REMAINING
                if metadata.cost_basis_verified_remaining
                else CostBasisStatus.UNVERIFIED,
            )
            if not basis.usable_for_since_purchase:
                row_warnings.append(
                    _warning(
                        "unusable_cost_basis",
                        "Einstand ist nicht als positiver verbleibender Einstand bestätigt; Seit-Kauf-Rendite nicht verfügbar.",
                        "cost_basis_eur",
                        source_row,
                    )
                )

        result_cells = {}
        for field in (
            "current_value_eur",
            "gain_loss_percent_points",
            "gain_loss_eur",
            "weight_percent_points",
        ):
            percent = field.endswith("percent_points")
            value, status = _number(
                cells[field], metadata.number_locale, euro=not percent, percent=percent
            )
            if value is not None and (
                (field == "current_value_eur" and value < 0 and quantity > 0)
                or (field == "weight_percent_points" and not 0 <= value <= 100)
            ):
                value, status = None, ValueStatus.INVALID
            if status is ValueStatus.INVALID:
                row_warnings.append(
                    _warning(
                        "invalid_reference",
                        "Ergebniszelle ist nicht plausibel; sie wird nicht zur Bewertung verwendet.",
                        field,
                        source_row,
                    )
                )
            result_cells[field] = value
        references.append(SheetReferenceValues(source_row=source_row, **result_cells))
        positions.append(
            Position(
                name,
                quantity,
                Instrument(
                    instrument_type,
                    display_ticker=texts["display_ticker"],
                    source_symbol=texts["source_symbol"],
                    quote_unit=unit,
                    source_isin=isin,
                    source_venue=venue,
                ),
                texts["category"],
                source_row,
                imported_at,
                basis,
                valuation,
                tuple(row_warnings),
            )
        )
        warnings.extend(row_warnings)

    snapshot = PortfolioSnapshot(
        imported_at,
        SnapshotStatus.PARTIAL if rejected else SnapshotStatus.COMPLETE,
        tuple(positions),
        tuple(warnings),
    )
    return SheetParseResult(snapshot, tuple(references), tuple(excluded), tuple(yahoo_listings))
