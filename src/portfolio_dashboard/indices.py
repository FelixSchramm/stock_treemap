"""Index constituents from the configured Sheet; session-local, read-only loading.

The Sheet declares symbols, Yahoo supplies checked identity/currency metadata.
Returns use the same exchange calendars and price engine as the Watchlist. Company
market caps are not free-float weights or official index weights. Different
currencies are never added or compared as if they were the same unit.
"""

import re
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from portfolio_dashboard.config import SourceKind
from portfolio_dashboard.fundamentals_provider import _quiet_provider_logs
from portfolio_dashboard.local_source import venue_calendars
from portfolio_dashboard.market_data import YFinanceProvider
from portfolio_dashboard.sheet_parser import NumberLocale
from portfolio_dashboard.sheets_adapter import (
    SheetsConfigurationError,
    _private_transport_diagnostics,
    _reject_constant,
)
from portfolio_dashboard.sheets_session import (
    build_readonly_session,
    build_readonly_session_from_info,
)
from portfolio_dashboard.watchlist_quotes import WatchlistEntry, WatchlistEntryStatus
from portfolio_dashboard.watchlist_refresh import (
    _provider_entry,
    session_running,
    watchlist_listing,
)
from portfolio_dashboard.watchlist_sheet import (
    BASIC_HEADERS,
    EXCHANGES,
    parse_watchlist_rows,
)

INDEX_NAMES = (
    "DAX", "MDAX", "Euro Stoxx 50", "STOXX Europe 600", "S&P 500", "Nasdaq 100", "Dow Jones",
    "Hang Seng", "Nikkei 225",
)
MAX_ROWS = 999  # Header + constituents fit the standard 1,000-row Sheet grid.
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,19}")
INFO_KEYS = ("symbol", "quoteType", "currency", "exchange", "exchangeTimezoneName", "marketCap")


class IndexSourceError(ValueError):
    """Only neutral, displayable messages; never transport details."""


@dataclass(frozen=True, repr=False)
class Constituent:
    name: str
    symbol: str | None
    note: str | None = None


@dataclass(frozen=True, repr=False)
class IndexRow:
    constituent: Constituent
    quote: WatchlistEntry | None = None
    market_cap: Decimal | None = None
    currency: str | None = None
    note: str | None = None

    def percent(self, period: str) -> Decimal | None:
        return self.quote.period(period).percent if self.quote is not None else None


@dataclass(frozen=True, repr=False)
class IndexBundle:
    name: str
    rows: tuple[IndexRow, ...]
    fetched_at: datetime
    available: bool = True
    warning: str | None = None

    @property
    def cap_currency(self) -> str | None:
        currencies = {r.currency for r in self.rows if r.market_cap is not None}
        return next(iter(currencies)) if len(currencies) == 1 else None

    @property
    def total_cap(self) -> Decimal | None:
        if self.cap_currency is None:
            return None
        return sum((r.market_cap for r in self.rows if r.market_cap is not None), Decimal(0))


def parse_constituents(values: object) -> tuple[Constituent, ...]:
    """Keep incomplete rows visible; never infer a Yahoo ticker from a name."""
    if not isinstance(values, list) or len(values) > MAX_ROWS + 1 or any(
        not isinstance(row, list) or len(row) > 2
        or any(type(cell) not in (str, int, float, Decimal, bool, type(None)) for cell in row)
        for row in values
    ):
        raise IndexSourceError("Index-Blatt konnte nicht gelesen werden.")
    if not values:
        return ()
    header = [str(cell).strip().casefold() for cell in values[0]]
    if set(header) != {"unternehmen", "yahoo-finance-ticker"} or len(header) != 2:
        raise IndexSourceError("Erwartete Spalten: Unternehmen und Yahoo-Finance-Ticker (A/B).")
    name_col, symbol_col = header.index("unternehmen"), header.index("yahoo-finance-ticker")
    result, seen = [], set()
    for number, row in enumerate(values[1:], 2):
        cells = row + [None] * (2 - len(row))
        if all(cell is None or isinstance(cell, str) and not cell.strip() for cell in cells):
            continue
        name = cells[name_col].strip() if isinstance(cells[name_col], str) else ""
        raw = cells[symbol_col]
        symbol = raw.strip().upper() if isinstance(raw, str) else ""
        note = None
        if not SYMBOL.fullmatch(symbol):
            symbol, note = None, "NA · Yahoo-Ticker fehlt oder ist ungültig"
        elif symbol in seen:
            note = "NA · doppelter Yahoo-Ticker im Blatt; nicht erneut gewichtet"
        else:
            seen.add(symbol)
        result.append(Constituent(name or symbol or f"Zeile {number}", symbol, note))
    return tuple(result)


class IndexSheetsReader:
    """Discover supported index tabs; read only the selected tab's two columns."""

    def __init__(self, source, session):
        if source.kind is not SourceKind.GOOGLE_SHEETS or not re.fullmatch(
            r"[A-Za-z0-9_-]+", source.spreadsheet_id or ""
        ):
            raise SheetsConfigurationError("Google-Sheets-Konfiguration fehlt für Indizes.")
        self._base = "https://sheets.googleapis.com/v4/spreadsheets/" + source.spreadsheet_id
        self._session = session

    def _get(self, suffix, params):
        try:
            with _private_transport_diagnostics():
                response = self._session.get(
                    self._base + suffix, params=params, timeout=10,
                    allow_redirects=False, verify=True,
                )
                try:
                    if response.status_code != 200:
                        raise IndexSourceError("Index-Blätter sind zurzeit nicht erreichbar.")
                    payload = response.json(parse_float=Decimal, parse_constant=_reject_constant)
                finally:
                    response.close()
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except Exception:
            raise IndexSourceError("Index-Blätter sind zurzeit nicht erreichbar.") from None

    def list_indices(self) -> tuple[str, ...]:
        payload = self._get("", {"fields": "sheets.properties.title"})
        try:
            tabs = {sheet["properties"]["title"] for sheet in payload["sheets"]}
        except (KeyError, TypeError):
            raise IndexSourceError("Index-Auswahl konnte nicht gelesen werden.") from None
        return tuple(name for name in INDEX_NAMES if name in tabs)

    def read(self, name: str) -> tuple[Constituent, ...]:
        if name not in INDEX_NAMES:
            raise IndexSourceError("Index-Blatt wird nicht unterstützt.")
        cell_range = f"'{name}'!A1:B{MAX_ROWS + 1}"
        payload = self._get("/values/" + quote(cell_range, safe=""), {
            "majorDimension": "ROWS", "valueRenderOption": "UNFORMATTED_VALUE",
            "fields": "majorDimension,values",
        })
        if payload.get("majorDimension") != "ROWS":
            raise IndexSourceError("Index-Blatt konnte nicht gelesen werden.")
        return parse_constituents(payload.get("values", []))


def _positive(value) -> Decimal | None:
    if isinstance(value, (str, bool)) or value is None:
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number > 0 else None
    except (ValueError, InvalidOperation):
        return None


def _ticker(symbol):
    import yfinance

    return yfinance.Ticker(symbol)


class IndexMarketData:
    """Bounded per-symbol requests; no country, exchange or currency inference."""

    def __init__(self, ticker_factory=_ticker, *, cache_info=False):
        self._ticker = ticker_factory
        # Identity and market cap change at most daily; prices are fetched on every
        # load. With ``cache_info`` the info fields are kept per UTC day, but only
        # after they passed the identity, venue and market-cap checks.
        self._infos = {} if cache_info else None

    def _info(self, ticker, symbol: str, day) -> tuple[object, bool]:
        """:return: (info, True if it came from the daily cache)."""
        cached = self._infos.get(symbol) if self._infos is not None else None
        if cached is not None and cached[0] == day:
            return cached[1], True
        return ticker.get_info(), False

    def _remember(self, symbol: str, day, info: Mapping) -> None:
        """Keep only checked, complete answers; throttled partial ones are retried."""
        if self._infos is not None:
            self._infos[symbol] = (day, {key: info.get(key) for key in INFO_KEYS})

    def fetch(self, constituent: Constituent, now: datetime, calendars) -> IndexRow:
        if constituent.note or not constituent.symbol:
            return IndexRow(constituent, note=constituent.note)
        try:
            ticker = self._ticker(constituent.symbol)
            info, cached = self._info(ticker, constituent.symbol, now.date())
            if not isinstance(info, Mapping) or (
                info.get("symbol") != constituent.symbol or info.get("quoteType") != "EQUITY"
                or not isinstance(info.get("currency"), str)
                or not re.fullmatch(r"[A-Z]{3}", info["currency"])
                or not isinstance(info.get("exchange"), str) or not info["exchange"]
            ):
                return IndexRow(constituent, note="NA · Yahoo-Zuordnung nicht bestätigt")
            currency, exchange = info["currency"], info["exchange"]
            cap = _positive(info.get("marketCap"))
            row = IndexRow(constituent, market_cap=cap, currency=currency)
            if exchange not in EXCHANGES or exchange not in calendars:
                return replace(row, note="NA · Kursverlauf für diesen Handelsplatz wird noch angebunden")
            if info.get("exchangeTimezoneName") != EXCHANGES[exchange][0]:
                return IndexRow(constituent, note="NA · Yahoo-Handelsplatz nicht bestätigt")
            if not cached and cap is not None:
                self._remember(constituent.symbol, now.date(), info)
            snapshot = parse_watchlist_rows(
                (*BASIC_HEADERS, "Yahoo-Symbol", "Handelsplatz", "Währung"),
                [[constituent.name, None, None, None, constituent.symbol, exchange, currency]],
                imported_at=now, number_locale=NumberLocale.GERMAN,
            ).snapshot
            item = snapshot.items[0]
            if item.mapping is None:
                return IndexRow(constituent, note="NA · Yahoo-Zuordnung nicht bestätigt")
            listing = watchlist_listing(item)
            provider = YFinanceProvider(
                (listing,), ticker_factory=lambda _: ticker, clock=lambda: now,
            )
            calendar = calendars[exchange]
            history = provider.history(listing.instrument, calendar.first_day, now.date() + timedelta(days=1))
            quote_obs = (
                provider.latest_quote(listing.instrument)
                if session_running(calendar, item.mapping.timezone, now) else None
            )
            entry = _provider_entry(item, history, quote_obs, calendar, now)
            if entry.status is WatchlistEntryStatus.METADATA_MISMATCH:
                return IndexRow(constituent, note="NA · Yahoo-Metadaten widersprechen sich")
            return replace(row, quote=entry, note=(
                "NA · Marktkapitalisierung nicht verfügbar" if cap is None else None
            ))
        except Exception:
            return IndexRow(constituent, note="NA · Yahoo-Daten zurzeit nicht verfügbar")


class IndicesLoader:
    """One owner's session: cache each selected index, never all index data globally."""

    def __init__(self, reader, market=None, *, clock=lambda: datetime.now(UTC), workers=4):
        self._reader = reader
        self._market = market if market is not None else IndexMarketData()
        self._clock = clock
        self._workers = workers
        self._names = None
        self._bundles = {}

    def list_indices(self, *, refresh=False):
        if self._names is None or refresh:
            self._names = self._reader.list_indices()
        return self._names

    def load(self, name: str, *, refresh=False) -> IndexBundle:
        now = self._clock()
        previous = self._bundles.get(name)
        if previous and previous.fetched_at.date() == now.date() and not refresh:
            return previous
        try:
            members = self._reader.read(name)
        except IndexSourceError as exc:
            if previous is not None:
                return replace(previous, warning="Aktualisierung fehlgeschlagen · letzter geladener Stand bleibt sichtbar.")
            return IndexBundle(name, (), now, available=False, warning=str(exc))
        calendars = venue_calendars(now.date())
        # Yahoo may log private symbols/URLs. Keep its logging disabled throughout
        # this bounded batch; workers do not mutate Streamlit state or log flags.
        with _quiet_provider_logs(), ThreadPoolExecutor(max_workers=self._workers) as pool:
            rows = tuple(pool.map(lambda member: self._market.fetch(member, now, calendars), members))
        bundle = IndexBundle(name, rows, now)
        self._bundles[name] = bundle
        return bundle


def build_local_indices(config, session=None):
    return IndicesLoader(IndexSheetsReader(
        config.source,
        session if session is not None else build_readonly_session(config.source.credentials_file),
    ))


def build_production_indices(config, service_account, session=None):
    return IndicesLoader(IndexSheetsReader(
        config.source,
        session if session is not None else build_readonly_session_from_info(service_account),
    ))


class DemoIndices:
    """Invented companies only; no Sheet access or provider requests."""

    def list_indices(self, *, refresh=False):
        return ("Demo-Index", "Demo-Technologie")

    def load(self, name, *, refresh=False):
        from portfolio_dashboard.demo import demo_watchlist_entries

        entries = demo_watchlist_entries()
        if name == "Demo-Technologie":
            entries = entries[:2]
        return IndexBundle(name, tuple(
            IndexRow(Constituent(entry.item.name, entry.item.ticker), entry,
                     Decimal(10 - i) * 1_000_000_000, "EUR", "Synthetische Beispieldaten")
            for i, entry in enumerate(entries)
        ), datetime.now(UTC))
