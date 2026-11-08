"""Pure parsing of the ADR-0012 tables Instrumente, Transaktionen and Kurse_manuell.

The ISIN is the instrument ID. A Yahoo listing becomes a verified listing only
when the owner set ``geprueft``; names, tickers and rows never establish
identity. Positions and their cost basis come from the transaction journal.
Warnings name table, field and row, never cell values.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.ledger import (
    Ledger,
    Transaction,
    TransactionKind,
    build_ledger,
)
from portfolio_dashboard.models import (
    CostBasis,
    CostBasisStatus,
    DataWarning,
    Instrument,
    InstrumentType,
    PortfolioSnapshot,
    Position,
    Provenance,
    ProviderMapping,
    QuoteUnit,
    SnapshotStatus,
    Valuation,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.sheet_parser import NumberLocale, _missing, _number, _text

INSTRUMENTS_RANGE = "'Instrumente'!A1:L2000"
TRANSACTIONS_RANGE = "'Transaktionen'!A1:L10000"
PRICES_RANGE = "'Kurse_manuell'!A1:C2000"
TABLE_RANGES = (INSTRUMENTS_RANGE, TRANSACTIONS_RANGE, PRICES_RANGE)

INSTRUMENT_COLUMNS = (
    "isin", "name", "typ", "aktienform", "anteilsklasse", "basiswert_isin",
    "kategorie", "google_symbol", "yahoo_symbol", "yahoo_handelsplatz",
    "waehrung", "geprueft",
)
TRANSACTION_COLUMNS = (
    "txn_id", "datum", "isin", "art", "stueck", "kurs_eur", "gebuehr_eur",
    "steuern_eur", "betrag_eur", "bezug_txn_id", "broker_ref", "notiz",
)
PRICE_COLUMNS = ("isin", "datum", "kurs_eur")

_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")
_TYPES = {
    "aktie": InstrumentType.STOCK,
    "etf": InstrumentType.ETF,
    "derivat": InstrumentType.DERIVATIVE,
}
_KINDS = {kind.value.casefold(): kind for kind in TransactionKind}
_EUR = QuoteUnit("EUR", Decimal("1"))
_BERLIN = ZoneInfo("Europe/Berlin")
_SERIAL_EPOCH = date(1899, 12, 30)
_LOCALE = NumberLocale.GERMAN


class TableSchemaError(ValueError):
    """A required header is missing or duplicated; message names table and column."""


@dataclass(frozen=True, slots=True)
class InstrumentRecord:
    isin: str
    name: str
    instrument_type: InstrumentType
    source_row: int
    equity_form: EquityForm | None = None
    share_class: str | None = None
    underlying_isin: str | None = None
    category: str | None = None
    google_symbol: str | None = None
    yahoo_symbol: str | None = None
    yahoo_venue: str | None = None
    currency: str | None = None
    verified_on: date | None = None

    @property
    def display_ticker(self) -> str | None:
        """Short label from the Google symbol (``NASDAQ:MSFT`` → ``MSFT``)."""
        if self.google_symbol is None:
            return None
        return self.google_symbol.rsplit(":", 1)[-1] or None


@dataclass(frozen=True, slots=True)
class ManualPrice:
    isin: str
    price_eur: Decimal
    source_row: int
    day: date | None = None


@dataclass(frozen=True, slots=True)
class Tables:
    instruments: tuple[InstrumentRecord, ...]
    transactions: tuple[Transaction, ...]
    prices: tuple[ManualPrice, ...]
    warnings: tuple[DataWarning, ...] = ()
    # ISINs whose journal lost a row while parsing; their holdings are unknown.
    incomplete: frozenset[str] = frozenset()
    rejected_rows: int = 0


def _warning(code: str, message: str, table: str, row: int | None) -> DataWarning:
    return DataWarning(code, message, table, row)


def _columns(headers: Sequence[object], names: Sequence[str], table: str) -> dict[str, int]:
    normalized = [cell.strip().casefold() if isinstance(cell, str) else "" for cell in headers]
    mapping = {}
    for name in names:
        matches = [i for i, header in enumerate(normalized) if header == name]
        if len(matches) != 1:
            raise TableSchemaError(f"{table}: Spalte '{name}' fehlt oder ist mehrfach vorhanden.")
        mapping[name] = matches[0]
    return mapping


def _rows(values: Sequence[Sequence[object]], names, table, header_row):
    if not values:
        raise TableSchemaError(f"{table}: Überschriftenzeile fehlt.")
    columns = _columns(values[0], names, table)
    for offset, row in enumerate(values[1:], 1):
        cells = {name: row[i] if i < len(row) else None for name, i in columns.items()}
        if all(_missing(cell) for cell in cells.values()):
            continue
        yield header_row + offset, cells


def parse_day(cell: object) -> date | None:
    """dd.mm.yyyy, ISO text or a Sheets serial number (UNFORMATTED_VALUE)."""
    if isinstance(cell, bool):
        return None
    if isinstance(cell, (int, float, Decimal)):
        if isinstance(cell, Decimal) and not cell.is_finite():
            return None
        if 1 <= cell < 100000 and cell == int(cell):
            return _SERIAL_EPOCH + timedelta(days=int(cell))
        return None
    if isinstance(cell, str):
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(cell.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _isin(cell: object) -> str | None:
    text = _text(cell)
    return text.upper() if text is not None and _ISIN.fullmatch(text.upper()) else None


def parse_tables(
    instruments: Sequence[Sequence[object]],
    transactions: Sequence[Sequence[object]],
    prices: Sequence[Sequence[object]],
) -> Tables:
    """Parse the three tables; schema errors raise ``TableSchemaError``."""
    warnings: list[DataWarning] = []
    incomplete: set[str] = set()
    rejected = 0

    records: list[InstrumentRecord] = []
    seen: set[str] = set()
    for row, cells in _rows(instruments, INSTRUMENT_COLUMNS, "instrumente", 1):
        isin = _isin(cells["isin"])
        name = _text(cells["name"])
        kind = _TYPES.get((_text(cells["typ"]) or "").casefold())
        if isin is None or name is None or kind is None:
            warnings.append(_warning(
                "invalid_instrument", "ISIN, Name oder Typ fehlt oder ist ungültig; Zeile ignoriert.",
                "instrumente", row))
            continue
        if isin in seen:
            warnings.append(_warning(
                "duplicate_isin", "ISIN mehrfach vorhanden; spätere Zeile ignoriert.",
                "instrumente", row))
            continue
        seen.add(isin)
        form_text = (_text(cells["aktienform"]) or "").casefold()
        form = None
        if kind is InstrumentType.STOCK and form_text:
            try:
                form = EquityForm(form_text)
            except ValueError:
                warnings.append(_warning(
                    "invalid_equity_form", "aktienform muss share oder adr sein.",
                    "instrumente", row))
        verified_cell = cells["geprueft"]
        verified = None if _missing(verified_cell) else parse_day(verified_cell)
        if verified is None and not _missing(verified_cell):
            warnings.append(_warning(
                "invalid_verified_date", "geprueft ist kein gültiges Datum; Listing ungeprüft.",
                "instrumente", row))
        currency = _text(cells["waehrung"])
        records.append(InstrumentRecord(
            isin, name, kind, row,
            equity_form=form,
            share_class=_text(cells["anteilsklasse"]) if kind is InstrumentType.ETF else None,
            underlying_isin=_isin(cells["basiswert_isin"]),
            category=_text(cells["kategorie"]),
            google_symbol=_text(cells["google_symbol"]),
            yahoo_symbol=_text(cells["yahoo_symbol"]),
            yahoo_venue=(_text(cells["yahoo_handelsplatz"]) or "").upper() or None,
            currency=currency.upper() if currency else None,
            verified_on=verified,
        ))

    journal: list[Transaction] = []
    for row, cells in _rows(transactions, TRANSACTION_COLUMNS, "transaktionen", 1):
        isin = _isin(cells["isin"])
        txn_id = _text(cells["txn_id"])
        day = parse_day(cells["datum"])
        kind = _KINDS.get((_text(cells["art"]) or "").casefold())
        quantity, _ = _number(cells["stueck"], _LOCALE)
        amount, amount_status = _number(cells["betrag_eur"], _LOCALE, euro=True)
        if (
            isin is None or txn_id is None or day is None or kind is None
            or quantity is None or quantity <= 0
            or amount_status is ValueStatus.INVALID or (amount is not None and amount < 0)
        ):
            rejected += 1
            if isin is not None:
                incomplete.add(isin)
            warnings.append(_warning(
                "invalid_transaction",
                "Transaktion unvollständig oder ungültig; Bestand dieser ISIN nicht verfügbar.",
                "transaktionen", row))
            continue
        journal.append(Transaction(
            txn_id, day, isin, kind, quantity, row,
            amount_eur=amount,
            reference=_text(cells["bezug_txn_id"]),
            broker_ref=_text(cells["broker_ref"]),
        ))

    manual: list[ManualPrice] = []
    for row, cells in _rows(prices, PRICE_COLUMNS, "kurse_manuell", 1):
        isin = _isin(cells["isin"])
        price, status = _number(cells["kurs_eur"], _LOCALE, euro=True)
        day_cell = cells["datum"]
        day = None if _missing(day_cell) else parse_day(day_cell)
        if status is ValueStatus.MISSING:
            continue  # e.g. a GOOGLEFINANCE formula without a current result
        if isin is None or price is None or price < 0 or (day is None and not _missing(day_cell)):
            warnings.append(_warning(
                "invalid_manual_price", "Sheet-Kurs ungültig; Zeile ignoriert.",
                "kurse_manuell", row))
            continue
        manual.append(ManualPrice(isin, price, row, day))

    return Tables(tuple(records), tuple(journal), tuple(manual), tuple(warnings),
                  frozenset(incomplete), rejected)


def verified_listings(
    records: Sequence[InstrumentRecord],
    venues: Mapping[str, str],
) -> tuple[tuple[VerifiedListing, ...], tuple[DataWarning, ...]]:
    """Owner-verified listings on supported venues; duplicates are dropped.

    ``venues`` maps each supported Yahoo venue code to its required currency.
    """
    candidates: list[tuple[InstrumentRecord, VerifiedListing]] = []
    warnings: list[DataWarning] = []
    for record in records:
        if (
            record.verified_on is None
            or record.yahoo_symbol is None
            or record.yahoo_venue not in venues
            or record.instrument_type is InstrumentType.DERIVATIVE
        ):
            continue
        if record.currency != venues[record.yahoo_venue] or (
            record.instrument_type is InstrumentType.STOCK and record.equity_form is None
        ) or (record.instrument_type is InstrumentType.ETF and record.share_class is None):
            warnings.append(_warning(
                "incomplete_listing",
                "Geprüftes Listing unvollständig oder widersprüchlich; keine Kurszuordnung.",
                "instrumente", record.source_row))
            continue
        candidates.append((record, VerifiedListing(
            Instrument(
                record.instrument_type,
                instrument_id=record.isin,
                trading_venue=record.yahoo_venue,
                quote_unit=QuoteUnit(record.currency, Decimal("1")),
                provider_mapping=ProviderMapping("yfinance", record.yahoo_symbol),
            ),
            equity_form=record.equity_form,
            isin=record.isin,
            share_class=record.share_class,
        )))
    symbols: dict[str, int] = {}
    for record, _ in candidates:
        symbols[record.yahoo_symbol] = symbols.get(record.yahoo_symbol, 0) + 1
    listings = []
    for record, listing in candidates:
        if symbols[record.yahoo_symbol] > 1:
            warnings.append(_warning(
                "duplicate_yahoo_symbol",
                "Yahoo-Symbol mehreren ISINs zugeordnet; keine Kurszuordnung.",
                "instrumente", record.source_row))
            continue
        listings.append(listing)
    return tuple(listings), tuple(warnings)


def _latest_prices(prices: Sequence[ManualPrice]) -> dict[str, ManualPrice]:
    """Latest dated entry per ISIN; an undated entry only when none is dated."""
    best: dict[str, ManualPrice] = {}
    for price in prices:
        current = best.get(price.isin)
        if current is None:
            best[price.isin] = price
        elif price.day is not None and (current.day is None or price.day >= current.day):
            best[price.isin] = price
        elif price.day is None and current.day is None:
            best[price.isin] = price
    return best


def _valuation(price: ManualPrice | None, imported_at: datetime, row: int) -> Valuation:
    if price is None:
        warning = _warning("missing_price",
                           "EUR-Sheet-Kurs fehlt; kein bewertbarer Ersatzkurs.", "kurse_manuell", row)
        return Valuation(None, ValueStatus.MISSING,
                         Provenance(ValuationSource.SHEET, imported_at), _EUR, (warning,))
    observed = None
    if price.day is not None:
        observed = datetime(price.day.year, price.day.month, price.day.day, tzinfo=_BERLIN)
        if observed > imported_at:
            observed = None
    warnings = () if observed is not None else (_warning(
        "unknown_price_time", "Sheet-Ersatzkurs mit unbekanntem tatsächlichem Kurszeitpunkt.",
        "kurse_manuell", price.source_row),)
    return Valuation(price.price_eur, ValueStatus.AVAILABLE,
                     Provenance(ValuationSource.SHEET, imported_at, observed), _EUR, warnings)


@dataclass(frozen=True, slots=True)
class JournalResult:
    snapshot: PortfolioSnapshot
    listings: tuple[VerifiedListing, ...]
    ledger: Ledger


def build_portfolio(
    tables: Tables,
    *,
    imported_at: datetime,
    venues: Mapping[str, str],
) -> JournalResult:
    """Positions with nonzero journal quantity, valued by Sheet price as fallback."""
    ledger = build_ledger(tables.transactions)
    listings, listing_warnings = verified_listings(tables.instruments, venues)
    warnings = [*tables.warnings, *ledger.warnings, *listing_warnings]
    by_isin = {record.isin: record for record in tables.instruments}
    registry = {listing.isin: listing for listing in listings}
    prices = _latest_prices(tables.prices)
    partial = bool(tables.rejected_rows or ledger.invalid)

    unavailable = (ledger.invalid | tables.incomplete) & set(by_isin)
    for isin in sorted(unavailable):
        warnings.append(_warning(
            "journal_unavailable",
            "Journal dieser ISIN fehlerhaft; Position nicht übernommen.",
            "instrumente", by_isin[isin].source_row))

    positions = []
    held = {isin: h for isin, h in ledger.holdings.items()
            if h.quantity != 0 and isin not in tables.incomplete}
    for isin, holding in held.items():
        if isin not in by_isin:
            partial = True
            warnings.append(_warning(
                "unknown_isin", "Transaktion mit ISIN ohne Zeile in Instrumente; Position fehlt.",
                "transaktionen", None))
    for record in tables.instruments:
        holding = held.get(record.isin)
        if holding is None:
            continue
        listing = registry.get(record.isin)
        valuation = _valuation(prices.get(record.isin), imported_at, record.source_row)
        positions.append(Position(
            record.name,
            holding.quantity,
            Instrument(
                record.instrument_type,
                instrument_id=record.isin,
                display_ticker=record.display_ticker,
                source_symbol=record.google_symbol,
                trading_venue=listing.instrument.trading_venue if listing else None,
                quote_unit=listing.instrument.quote_unit if listing else None,
                source_isin=record.isin,
            ),
            record.category,
            record.source_row,
            imported_at,
            CostBasis(holding.remaining_cost_eur, CostBasisStatus.VERIFIED_REMAINING),
            valuation,
            valuation.warnings,
        ))
    status = SnapshotStatus.PARTIAL if partial and warnings else SnapshotStatus.COMPLETE
    snapshot = PortfolioSnapshot(imported_at, status, tuple(positions), tuple(warnings))
    return JournalResult(snapshot, listings, ledger)
