"""Watchlist view model: filters, sorting, KPIs, table rows and details (ADR 0011, #85).

Pure and offline; no Streamlit, provider or Sheet access. Everything is derived from
the entries of one ``WatchlistBundle``, so KPIs, table and detail blocks always agree.
Percentages are plain price changes in trading currency (Sheet substitute: EUR) and
are never weighted; a missing period value is always "n. v." and never ``0,00 %``.
Each period carries its own origin, because one row may mix provider values with a
Sheet substitute for ``Heute``.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from portfolio_dashboard.models import InstrumentType
from portfolio_dashboard.treemap import (
    ALL_FILTER,
    NO_CATEGORY_LABEL,
    NOT_AVAILABLE,
    FilterCriteria,
    _german,
    format_percent,
    performance_direction_label,
)
from portfolio_dashboard.watchlist_quotes import (
    PERIODS,
    UNVERIFIED_MAPPING,
    WatchlistEntry,
    WatchlistEntryStatus,
    WatchlistOrigin,
    WatchlistPeriodValue,
)
from portfolio_dashboard.watchlist_sheet import SHEET_VALUE_LABEL

NOT_SPECIFIED = "Nicht angegeben"
NO_VALUE = "—"
TYPE_FILTER_OPTIONS = (ALL_FILTER, "Aktie", "ETF", "Derivat", NOT_SPECIFIED)

SORT_BY_PERFORMANCE = "Kursentwicklung (%)"
SORT_BY_NAME = "Name"
SORT_BY_TICKER = "Ticker"
SORT_FIELDS = (SORT_BY_PERFORMANCE, SORT_BY_NAME, SORT_BY_TICKER)

_TYPE_LABELS = {
    InstrumentType.STOCK: "Aktie",
    InstrumentType.ETF: "ETF",
    InstrumentType.DERIVATIVE: "Derivat",
}

# Only these fixed labels may describe why a value is missing. Codes that are not
# listed are never shown; the entry then says only that the value is unavailable.
REASON_LABELS = {
    UNVERIFIED_MAPPING: "keine Yahoo-Zuordnung im Blatt",
    "sheet_value_missing": "kein Sheet-Wert",
    "stale_quote": "Kurs oder Historie veraltet",
    "insufficient_history": "Historie unvollständig",
    "missing_reference_close": "Historie unvollständig",
    "unexpected_gap": "Historie unvollständig",
    "zero_reference": "Referenzkurs ist 0",
    "missing_current_close": "aktueller Schlusskurs fehlt",
    "calendar_incomplete": "Handelskalender unvollständig",
    "history_unavailable": "Historie fehlt",
    "provider_failure": "Kursanbieter nicht erreichbar",
    "provider_backoff": "Abruf vorübergehend pausiert",
    "missing_data": "keine Kursdaten",
    "missing_metadata": "Prüfdaten fehlen",
    "no_provider_coverage": "keine Anbieterabdeckung",
    "unsupported_provider": "Kursquelle nicht unterstützt",
    "invalid_price": "Kurs ungültig",
    "invalid_history": "Tageskurse ungültig",
    "invalid_session_date": "Handelstag ungültig",
    "missing_quote_time": "Kurszeitpunkt fehlt",
    "invalid_quote_time": "Kurszeitpunkt ungültig",
    "instrument_metadata_mismatch": "Instrument oder Handelsplatz weicht ab",
    "unknown_or_conflicting_unit": "Währung oder Kurseinheit unklar",
    "unknown_exchange_timezone": "Börsenzeitzone unbekannt",
    "exchange_timezone_mismatch": "Börsenzeitzone passt nicht zum Handelsplatz",
}
UNKNOWN_REASON = "Grund nicht verfügbar"

_STATUS_LABELS = {
    WatchlistEntryStatus.LIVE: "Aktuell (Anbieterdaten)",
    WatchlistEntryStatus.CACHED: "Zwischengespeichert (letzter gültiger Anbieterstand)",
    WatchlistEntryStatus.SHEET_VALUE: SHEET_VALUE_LABEL,
    WatchlistEntryStatus.METADATA_MISMATCH: (
        "Yahoo-Metadaten widersprechen dem Blatt, kein Anbieterkurs übernommen"
    ),
    WatchlistEntryStatus.UNAVAILABLE: "Daten nicht verfügbar",
    WatchlistEntryStatus.DEMO: "Demo-Wert, synthetisch",
}
_ORIGIN_LABELS = {
    WatchlistOrigin.PROVIDER: "Anbieter (Yahoo)",
    WatchlistOrigin.CACHED_PROVIDER: "Anbieter, zwischengespeichert",
    WatchlistOrigin.SHEET: SHEET_VALUE_LABEL,
    WatchlistOrigin.DEMO: "Demo-Wert, synthetisch",
}


def reason_label(code: str | None) -> str:
    """Fixed German text for a reason code; unknown codes are never echoed."""
    return REASON_LABELS.get(code, UNKNOWN_REASON) if code else UNKNOWN_REASON


def format_price(entry: WatchlistEntry) -> str:
    """Price in its own unit (trading currency, EUR for the Sheet substitute)."""
    if entry.price is None or entry.price_unit is None:
        return NOT_AVAILABLE
    places = 4 if abs(entry.price) < 1 else 2
    text = f"{_german(entry.price, places)} {entry.price_unit.currency}"
    if entry.price_origin is WatchlistOrigin.SHEET:
        text += " (Sheet)"
    return text


def period_source(entry: WatchlistEntry, value: WatchlistPeriodValue) -> str:
    """Where one period value comes from; the reason for a missing value."""
    if value.percent is None:
        return f"{NOT_AVAILABLE} ({reason_label(value.reason)})"
    text = _ORIGIN_LABELS[value.origin]
    if value.origin is WatchlistOrigin.CACHED_PROVIDER and entry.age_text:
        text += f" ({entry.age_text})"
    if (
        value.origin is WatchlistOrigin.SHEET
        and value.reason
        and value.reason != UNVERIFIED_MAPPING
    ):
        text += f"; Anbieterwert fehlt: {reason_label(value.reason)}"
    return text


def status_label(entry: WatchlistEntry) -> str:
    """Short German status of the row, naming a Sheet substitute and the reason."""
    text = _STATUS_LABELS[entry.status]
    if entry.status is WatchlistEntryStatus.CACHED and entry.age_text:
        text += f", Alter: {entry.age_text}"
    if entry.status is WatchlistEntryStatus.SHEET_VALUE and entry.status_reason:
        text += f"; Anbieterdaten nicht verfügbar ({reason_label(entry.status_reason)})"
    if entry.status in (
        WatchlistEntryStatus.UNAVAILABLE,
        WatchlistEntryStatus.METADATA_MISMATCH,
    ) and entry.status_reason:
        text += f" ({reason_label(entry.status_reason)})"
    if entry.status in (WatchlistEntryStatus.LIVE, WatchlistEntryStatus.CACHED) and (
        entry.periods[0].origin is WatchlistOrigin.SHEET
    ):
        text += f"; Heute: {SHEET_VALUE_LABEL}"
    return text


def _has_sheet_value(entry: WatchlistEntry) -> bool:
    return entry.price_origin is WatchlistOrigin.SHEET or any(
        value.origin is WatchlistOrigin.SHEET for value in entry.periods
    )


def quote_time_label(entry: WatchlistEntry) -> str:
    """Quote time of the displayed provider data, or the Sheet value's unknown time."""
    if entry.current_date is None:
        return "Kursstand unbekannt" if _has_sheet_value(entry) else NO_VALUE
    text = f"{entry.current_date:%d.%m.%Y}"
    if entry.status is WatchlistEntryStatus.DEMO:
        return f"{text} (Demo)"
    if entry.status is WatchlistEntryStatus.CACHED:
        return f"{text} (zwischengespeichert, {entry.age_text})" if entry.age_text else text
    if entry.market_open:
        text += " (Börse geöffnet)"
    return text


@dataclass(frozen=True, slots=True)
class WatchlistRow:
    """One Watchlist entry as displayed for the selected period."""

    name: str
    ticker: str | None
    instrument_type: str
    category: str
    symbol: str | None
    venue: str
    price_label: str
    percent: Decimal | None
    percent_label: str
    source: str
    reference_date: str
    quote_time: str
    status: str
    source_row: int
    period: str
    period_lines: tuple[str, ...]
    notes: tuple[str, ...] = ()

    def to_display_dict(self) -> dict[str, str]:
        """Columns of the sortable table; every cell is plain text."""
        return {
            "Name": self.name,
            "Ticker": self.ticker or NO_VALUE,
            "Typ": self.instrument_type,
            "Kategorie": self.category,
            "Handelsplatz": self.venue,
            "Kurs": self.price_label,
            SORT_BY_PERFORMANCE: self.percent_label,
            "Quelle": self.source,
            "Referenzdatum": self.reference_date,
            "Kursstand": self.quote_time,
            "Status": self.status,
        }

    def detail_lines(self) -> tuple[str, ...]:
        """All table fields, every period with its origin and the row notes.

        The first line is the heading; nothing here needs hover.
        """
        header = self.name + (f" ({self.ticker})" if self.ticker else "")
        lines = [
            header,
            f"Typ: {self.instrument_type}",
            f"Kategorie: {self.category}",
            f"Handelsplatz: {self.venue}",
        ]
        if self.symbol:
            lines.append(f"Yahoo-Symbol (Angabe im Blatt): {self.symbol}")
        lines += [
            f"Kurs: {self.price_label}",
            f"Kursentwicklung {self.period}: {self.percent_label} "
            f"({performance_direction_label(self.percent)})",
            f"Quelle: {self.source}",
            f"Referenzdatum: {self.reference_date}",
            f"Kursstand: {self.quote_time}",
            *self.period_lines,
            f"Status: {self.status}",
            *(f"Hinweis: {note}" for note in self.notes),
            f"Zeile im Blatt: {self.source_row}",
        ]
        return tuple(lines)


def _type_label(entry: WatchlistEntry) -> str:
    kind = entry.item.instrument_type
    return _TYPE_LABELS[kind] if kind is not None else NOT_SPECIFIED


def _category_label(entry: WatchlistEntry) -> str:
    return entry.item.category or NO_CATEGORY_LABEL


def _symbol(entry: WatchlistEntry) -> str | None:
    mapping = entry.item.mapping
    return mapping.symbol if mapping is not None else None


def available_categories(entries: Sequence[WatchlistEntry]) -> tuple[str, ...]:
    """``Alle``, the sorted categories and ``Ohne Kategorie`` when rows lack one."""
    categories = {entry.item.category for entry in entries if entry.item.category}
    options = [ALL_FILTER, *sorted(categories)]
    if categories and any(not entry.item.category for entry in entries):
        options.append(NO_CATEGORY_LABEL)
    return tuple(options)


def matches(entry: WatchlistEntry, criteria: FilterCriteria) -> bool:
    """Search (name, ticker, Yahoo symbol, category), type and category jointly."""
    if criteria.instrument_type not in ("", ALL_FILTER):
        if criteria.instrument_type != _type_label(entry):
            return False
    if criteria.category not in ("", ALL_FILTER):
        if criteria.category != _category_label(entry):
            return False
    query = criteria.query.strip().lower()
    if query:
        item = entry.item
        tokens = (item.name, item.ticker, _symbol(entry), item.category)
        if not any(query in token.lower() for token in tokens if token):
            return False
    return True


def _period_line(entry: WatchlistEntry, value: WatchlistPeriodValue) -> str:
    text = f"{value.period}: {format_percent(value.percent)} · {period_source(entry, value)}"
    if value.reference_date is not None:
        text += f" · Referenzdatum {value.reference_date:%d.%m.%Y}"
    return text


def _row(entry: WatchlistEntry, period: str) -> WatchlistRow:
    item = entry.item
    value = entry.period(period)
    return WatchlistRow(
        name=item.name,
        ticker=item.ticker,
        instrument_type=_type_label(entry),
        category=_category_label(entry),
        symbol=_symbol(entry),
        venue=item.mapping.exchange if item.mapping is not None else NO_VALUE,
        price_label=format_price(entry),
        percent=value.percent,
        percent_label=format_percent(value.percent),
        source=period_source(entry, value),
        reference_date=(
            f"{value.reference_date:%d.%m.%Y}" if value.reference_date else NO_VALUE
        ),
        quote_time=quote_time_label(entry),
        status=status_label(entry),
        source_row=item.source_row,
        period=period,
        period_lines=tuple(_period_line(entry, each) for each in entry.periods),
        notes=tuple(warning.message for warning in entry.warnings),
    )


def build_rows(
    entries: Sequence[WatchlistEntry],
    period: str,
    criteria: FilterCriteria | None = None,
) -> tuple[WatchlistRow, ...]:
    """Rows of the filtered entries in sheet order.

    :raises ValueError: If ``period`` is not one of the four course periods.
    """
    if period not in PERIODS:
        raise ValueError("period must be Heute, Woche, Monat or Jahr")
    active = criteria if criteria is not None else FilterCriteria()
    return tuple(_row(entry, period) for entry in entries if matches(entry, active))


def sort_rows(
    rows: Sequence[WatchlistRow],
    sort_by: str = SORT_BY_PERFORMANCE,
    *,
    descending: bool = True,
) -> tuple[WatchlistRow, ...]:
    """Sort rows; missing values (n. v., no ticker) always come last.

    Ties are broken deterministically by lowercase name, then sheet row.

    :raises ValueError: If ``sort_by`` is not in ``SORT_FIELDS``.
    """
    if sort_by not in SORT_FIELDS:
        raise ValueError(f"unsupported sort_by field: {sort_by}")
    tie = lambda row: (row.name.lower(), row.source_row)  # noqa: E731
    if sort_by == SORT_BY_PERFORMANCE:
        present = [row for row in rows if row.percent is not None]
        missing = [row for row in rows if row.percent is None]
        present.sort(
            key=lambda row: (
                -row.percent if descending else row.percent,
                *tie(row),
            )
        )
        missing.sort(key=tie)
        return tuple(present + missing)
    if sort_by == SORT_BY_TICKER:
        present = sorted((row for row in rows if row.ticker), key=tie)
        present.sort(key=lambda row: row.ticker.lower(), reverse=descending)
        missing = sorted((row for row in rows if not row.ticker), key=tie)
        return tuple(present + missing)
    ordered = sorted(rows, key=tie)
    ordered.sort(key=lambda row: row.name.lower(), reverse=descending)
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class WatchlistKpis:
    """Header KPIs of the unfiltered Watchlist for the selected period."""

    count_label: str
    coverage_label: str  # Metric value: "5 von 6".
    coverage_note: str  # Small line below the value.
    coverage_detail: str  # Full breakdown by origin for a caption (no truncation).
    sheet_value_count: int
    winner_label: str  # Metric value: the percentage, or "n. v.".
    loser_label: str
    winner_note: str | None  # Ticker or name, or why "n. v.".
    loser_note: str | None


def _short_name(entry: WatchlistEntry, ticker_counts: Counter) -> str:
    ticker = entry.item.ticker
    return ticker if ticker and ticker_counts[ticker] == 1 else entry.item.name


def build_kpis(entries: Sequence[WatchlistEntry], period: str) -> WatchlistKpis:
    """Count, data coverage for ``period`` and the biggest winner and loser.

    Sheet substitutes count as values but are named as such, in the coverage note
    and in the winner/loser line.

    :raises ValueError: If ``period`` is not one of the four course periods.
    """
    if period not in PERIODS:
        raise ValueError("period must be Heute, Woche, Monat or Jahr")
    measured = [(e, e.period(period)) for e in entries if e.period(period).percent is not None]
    ticker_counts = Counter(entry.item.ticker for entry in entries if entry.item.ticker)
    by_origin = Counter(value.origin for _, value in measured)
    parts = []
    if by_origin[WatchlistOrigin.SHEET]:
        parts.append(
            f"{by_origin[WatchlistOrigin.SHEET]} Sheet-Ersatzwert(e) ({SHEET_VALUE_LABEL})"
        )
    if by_origin[WatchlistOrigin.CACHED_PROVIDER]:
        parts.append(f"{by_origin[WatchlistOrigin.CACHED_PROVIDER]} zwischengespeichert")
    if by_origin[WatchlistOrigin.DEMO]:
        parts.append(f"{by_origin[WatchlistOrigin.DEMO]} synthetische Demo-Werte")

    def tie(pair):
        return pair[0].item.name.lower(), pair[0].item.source_row

    gains = sorted((p for p in measured if p[1].percent > 0), key=lambda p: (-p[1].percent, *tie(p)))
    losses = sorted((p for p in measured if p[1].percent < 0), key=lambda p: (p[1].percent, *tie(p)))

    def summary(pair, empty_note):
        if pair is None:
            return NOT_AVAILABLE, empty_note if measured else None
        entry, value = pair
        note = _short_name(entry, ticker_counts)
        if value.origin is WatchlistOrigin.SHEET:
            note += " · Sheet-Wert"
        return format_percent(value.percent), note

    winner_label, winner_note = summary(gains[0] if gains else None, "keine > 0 %")
    loser_label, loser_note = summary(losses[0] if losses else None, "keine < 0 %")
    return WatchlistKpis(
        count_label=str(len(entries)),
        coverage_label=f"{len(measured)} von {len(entries)}",
        coverage_note="mit Kennzahl",
        coverage_detail=(
            f"Datenabdeckung „{period}“: {len(measured)} von {len(entries)} Werten mit "
            "Kennzahl" + (", davon " + "; ".join(parts) if parts else "")
            + ". Fehlende Werte sind „n. v.“."
        ),
        sheet_value_count=by_origin[WatchlistOrigin.SHEET],
        winner_label=winner_label,
        loser_label=loser_label,
        winner_note=winner_note,
        loser_note=loser_note,
    )


def build_status_notes(
    entries: Sequence[WatchlistEntry], snapshot_warnings: Sequence = ()
) -> tuple[str, ...]:
    """Row-referenced German notes for everything that is not fresh provider data.

    :param entries: All entries of the bundle (unfiltered).
    :param snapshot_warnings: Sheet-level ``DataWarning`` items of the bundle.
    """
    notes: list[str] = []
    for warning in snapshot_warnings:
        row = f"Zeile {warning.source_row}: " if warning.source_row is not None else ""
        note = f"Import-Hinweis ({warning.code}): {row}{warning.message}"
        if note not in notes:
            notes.append(note)
    for entry in entries:
        item = entry.item
        who = item.name + (f" ({item.ticker})" if item.ticker else "")
        if entry.status not in (WatchlistEntryStatus.LIVE, WatchlistEntryStatus.DEMO):
            notes.append(f"{who}: {status_label(entry)}")
        elif entry.periods[0].origin is WatchlistOrigin.SHEET:
            notes.append(f"{who}: Heute: {SHEET_VALUE_LABEL}")
        for warning in entry.warnings:
            notes.append(f"{who}: {warning.message}")
    return tuple(notes)
