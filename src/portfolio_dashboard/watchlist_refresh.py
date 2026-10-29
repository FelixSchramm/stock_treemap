"""Watchlist refresh: Sheet tab, yfinance quotes and the four price periods (#84).

The Watchlist tab (``PORTFOLIO_WATCHLIST_RANGE``) is read through the same
read-only service-account session and the same ``UNFORMATTED_VALUE`` rendering as
the depot, so number semantics match. Rows with a complete, consistent Yahoo
mapping become technical ``VerifiedListing`` keys; the provider then checks
symbol, exchangeName and currency of every response against the Sheet entry, and
the exchange timezone is checked against the Handelsplatz. Quotes and histories
go through ``QuoteCache`` (TTL, backoff, last valid state with age) and
``price_period_returns``. Rows without a usable mapping, and rows whose provider
data are missing or contradict the Sheet, show the labelled Sheet value for
``Heute`` and "n. v." for the other periods. Watchlist periods are pure price
returns in trading currency: no FX, no EUR valuation, no link to the depot.
"""

import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from requests.exceptions import Timeout

from portfolio_dashboard.config import (
    AppConfig,
    CacheTTLs,
    ConfigurationError,
    SourceConfig,
    SourceKind,
    parse_watchlist_range,
)
from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.local_source import LOCAL_SHEET_METADATA, venue_calendars
from portfolio_dashboard.market_data import (
    FxObservation,
    MarketDataProvider,
    PriceHistory,
    PriceObservation,
    VerifiedFxPair,
    YFinanceProvider,
)
from portfolio_dashboard.models import (
    DataWarning,
    Instrument,
    InstrumentType,
    ProviderMapping,
    QuoteUnit,
    SnapshotStatus,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.price_periods import SessionCalendar, price_period_returns
from portfolio_dashboard.quote_cache import QuoteCache
from portfolio_dashboard.refresh import (
    PERIOD_KEY_TO_ENUM,
    _format_timestamp,
    format_snapshot_age,
)
from portfolio_dashboard.sheet_parser import NumberLocale
from portfolio_dashboard.sheets_adapter import (
    SheetsConfigurationError,
    SheetsSession,
    _failed,
    _http_failure,
    _private_transport_diagnostics,
    _reject_constant,
    _timeout,
)
from portfolio_dashboard.sheets_session import (
    build_readonly_session,
    build_readonly_session_from_info,
)
from portfolio_dashboard.watchlist_quotes import (
    DATA_UNAVAILABLE_TEXT,
    PERIODS,
    WatchlistBundle,
    WatchlistEntry,
    WatchlistEntryStatus,
    WatchlistOrigin,
    WatchlistPeriodValue,
    sheet_entry,
)
from portfolio_dashboard.watchlist_sheet import (
    SHEET_VALUE_LABEL,
    WatchlistItem,
    WatchlistRowStatus,
    WatchlistSnapshot,
    parse_watchlist_rows,
)

# Safety bound on rows requested per refresh. PORTFOLIO_WATCHLIST_RANGE enforces
# no row limit (ADR 0011); a larger range is clipped here and reported as PARTIAL.
MAX_WATCHLIST_ROWS = 2_000
# Same query as SheetsAdapter.read, so cell values (percentages!) read identically.
SHEET_REQUEST_PARAMS = {
    "majorDimension": "ROWS",
    "valueRenderOption": "UNFORMATTED_VALUE",
    "dateTimeRenderOption": "SERIAL_NUMBER",
    "fields": "majorDimension,values",
}
# Provider/response codes meaning "Yahoo describes another instrument than the Sheet".
METADATA_MISMATCH_CODES = frozenset(
    {
        "instrument_metadata_mismatch",  # symbol or exchangeName differs
        "unknown_or_conflicting_unit",  # currency differs
        "exchange_timezone_mismatch",  # timezone does not belong to the Handelsplatz
    }
)
_INFO_CODES = frozenset({"cached_provider_value"})
_CACHED_SNAPSHOT_CODE = "cached_holdings_snapshot"


def session_running(calendar: SessionCalendar, timezone: str, now: datetime) -> bool:
    """Regular session open at ``now`` (aware), judged in the exchange's own time."""
    local = now.astimezone(ZoneInfo(timezone))
    return (
        local.date() in calendar.sessions
        and calendar.open_time <= local.time() < calendar.close_time
    )


def _failed_snapshot(attempted_at: datetime, result) -> WatchlistSnapshot:
    return WatchlistSnapshot(
        attempted_at, SnapshotStatus.FAILED, warnings=result.snapshot.warnings
    )


@dataclass(frozen=True, slots=True, repr=False)
class WatchlistSheetsReader:
    """One bounded, read-only Sheets request for the Watchlist tab.

    Mirrors ``SheetsAdapter`` (single GET, no redirects, TLS verification,
    neutral failures, private transport diagnostics) but for the Watchlist range
    and parser. The caller supplies the authorized read-only session.
    """

    source: SourceConfig = field(repr=False)
    session: SheetsSession = field(repr=False)
    number_locale: NumberLocale = NumberLocale.GERMAN
    timeout_seconds: float = 10
    max_rows: int = MAX_WATCHLIST_ROWS
    _url: str = field(init=False, repr=False)
    _header_row: int = field(init=False, repr=False)
    _row_count: int = field(init=False, repr=False)
    _column_count: int = field(init=False, repr=False)
    _clipped: bool = field(init=False, repr=False)

    def __post_init__(self) -> None:
        source_id = self.source.spreadsheet_id if isinstance(self.source, SourceConfig) else None
        if (
            not isinstance(self.source, SourceConfig)
            or self.source.kind is not SourceKind.GOOGLE_SHEETS
            or not isinstance(source_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", source_id)
        ):
            raise SheetsConfigurationError("Explicit Google Sheets source required.")
        try:
            wanted = parse_watchlist_range(self.source.watchlist_range)
        except ConfigurationError:
            raise SheetsConfigurationError("Watchlist range missing or invalid.") from None
        if type(self.max_rows) is not int or self.max_rows < 2:
            raise SheetsConfigurationError("Row limit must be at least two.")
        if not isinstance(self.number_locale, NumberLocale):
            raise SheetsConfigurationError("Explicit number locale required.")
        rows = min(wanted.row_count, self.max_rows)
        sheet = wanted.cell_range.rsplit("!", 1)[0]
        column = chr(ord("A") + wanted.column_count - 1)
        cell_range = f"{sheet}!A{wanted.first_row}:{column}{wanted.first_row + rows - 1}"
        object.__setattr__(self, "timeout_seconds", _timeout(self.timeout_seconds))
        object.__setattr__(self, "_header_row", wanted.first_row)
        object.__setattr__(self, "_row_count", rows)
        object.__setattr__(self, "_column_count", wanted.column_count)
        object.__setattr__(self, "_clipped", rows < wanted.row_count)
        object.__setattr__(
            self,
            "_url",
            "https://sheets.googleapis.com/v4/spreadsheets/"
            f"{quote(source_id, safe='')}/values/{quote(cell_range, safe='')}",
        )

    def read(self, attempted_at: datetime) -> WatchlistSnapshot:
        """Fetch once and parse; failures yield a FAILED snapshot without items."""
        WatchlistSnapshot(attempted_at, SnapshotStatus.COMPLETE)  # validates the clock
        try:
            with _private_transport_diagnostics():
                response = self.session.get(
                    self._url,
                    params=dict(SHEET_REQUEST_PARAMS),
                    timeout=self.timeout_seconds,
                    allow_redirects=False,
                    verify=True,
                )
                try:
                    status = response.status_code
                    if type(status) is not int:
                        return self._invalid(attempted_at)
                    if status != 200:
                        return _failed_snapshot(
                            attempted_at, _http_failure(attempted_at, status)
                        )
                    payload = response.json(
                        parse_float=Decimal, parse_constant=_reject_constant
                    )
                finally:
                    response.close()
        except (Timeout, TimeoutError):
            return _failed_snapshot(
                attempted_at,
                _failed(attempted_at, "sheet_timeout", "Sheet read timed out."),
            )
        except ValueError:
            return self._invalid(attempted_at)
        except Exception:
            # Transport/auth exceptions can contain URLs, response bodies or tokens.
            return _failed_snapshot(
                attempted_at,
                _failed(
                    attempted_at,
                    "sheet_transport_error",
                    "Sheet read could not be completed.",
                ),
            )
        if not isinstance(payload, dict) or payload.get("majorDimension") != "ROWS":
            return self._invalid(attempted_at)
        rows = payload.get("values", [])
        if (
            not isinstance(rows, list)
            or len(rows) > self._row_count
            or any(
                not isinstance(row, list)
                or len(row) > self._column_count
                or any(
                    type(cell) not in (str, int, float, Decimal, bool, type(None))
                    for cell in row
                )
                for row in rows
            )
        ):
            return self._invalid(attempted_at)
        if not rows:
            snapshot = WatchlistSnapshot(attempted_at, SnapshotStatus.COMPLETE)
        else:
            snapshot = parse_watchlist_rows(
                rows[0],
                rows[1:],
                imported_at=attempted_at,
                number_locale=self.number_locale,
                header_row=self._header_row,
            ).snapshot
        if self._clipped and snapshot.status is not SnapshotStatus.FAILED:
            note = DataWarning(
                "watchlist_row_limit",
                f"Watchlist-Bereich überschreitet das Zeilenlimit ({self.max_rows}); "
                "nur die ersten Zeilen wurden gelesen.",
            )
            snapshot = replace(
                snapshot,
                status=SnapshotStatus.PARTIAL,
                warnings=snapshot.warnings + (note,),
            )
        return snapshot

    @staticmethod
    def _invalid(attempted_at: datetime) -> WatchlistSnapshot:
        return _failed_snapshot(
            attempted_at,
            _failed(
                attempted_at,
                "sheet_invalid_response",
                "Sheet response could not be interpreted.",
            ),
        )


class _SwitchableProvider:
    """``MarketDataProvider`` whose listing registry follows the current Watchlist.

    ``QuoteCache`` keeps its provider for its whole life, but the Watchlist (and
    with it the verified registry) changes with every Sheet read.
    """

    def __init__(
        self, factory: Callable[[tuple[VerifiedListing, ...]], MarketDataProvider]
    ) -> None:
        self._factory = factory
        self._inner = factory(())

    def use(self, listings: tuple[VerifiedListing, ...]) -> None:
        self._inner = self._factory(listings)

    def latest_quote(self, instrument: Instrument) -> PriceObservation:
        return self._inner.latest_quote(instrument)

    def history(
        self, instrument: Instrument, start: date, end: date, *, minimum_points: int = 2
    ) -> PriceHistory:
        return self._inner.history(
            instrument, start, end, minimum_points=minimum_points
        )

    def fx(self, pair: VerifiedFxPair) -> FxObservation:
        return self._inner.fx(pair)


def watchlist_listing(item: WatchlistItem) -> VerifiedListing:
    """Technical registry key for a MAPPED row; carries no ISIN or product identity.

    The id combines Handelsplatz and Yahoo symbol only. The Sheet entry is merely
    a candidate: ``YFinanceProvider`` rejects every response whose symbol,
    exchangeName or currency differs, so nothing unchecked is displayed.
    """
    mapping = item.mapping
    if item.status is not WatchlistRowStatus.MAPPED or mapping is None:
        raise ValueError("only MAPPED rows have a provider listing")
    return VerifiedListing(
        Instrument(
            InstrumentType.STOCK,
            instrument_id=f"watchlist:{mapping.exchange}:{mapping.symbol}",
            display_ticker=item.ticker,
            trading_venue=mapping.exchange,
            quote_unit=QuoteUnit(mapping.currency, Decimal(1)),
            provider_mapping=ProviderMapping("yfinance", mapping.symbol),
        ),
        equity_form=EquityForm.SHARE,
    )


def _code(warnings: tuple[DataWarning, ...]) -> str | None:
    return next((w.code for w in warnings if w.code not in _INFO_CODES), None)


def _failure_entry(item: WatchlistItem, code: str) -> WatchlistEntry:
    mismatch = code in METADATA_MISMATCH_CODES
    return sheet_entry(
        item,
        code,
        status=WatchlistEntryStatus.METADATA_MISMATCH if mismatch else None,
    )


def _provider_entry(
    item: WatchlistItem,
    history: PriceHistory,
    quote_obs: PriceObservation | None,
    calendar: SessionCalendar,
    now: datetime,
) -> WatchlistEntry:
    """Combine checked provider data with the Sheet fallback for one MAPPED row."""
    if history.status is not ValueStatus.AVAILABLE:
        return _failure_entry(item, _code(history.warnings) or "history_unavailable")
    if history.exchange_timezone != item.mapping.timezone:
        return _failure_entry(item, "exchange_timezone_mismatch")
    if quote_obs is not None and quote_obs.valuation.status is not ValueStatus.AVAILABLE:
        code = _code(quote_obs.valuation.warnings)
        if code in METADATA_MISMATCH_CODES:
            return _failure_entry(item, code)

    results = price_period_returns(history, quote_obs, calendar, now)
    parts = [history.provenance]
    if quote_obs is not None and quote_obs.valuation.status is ValueStatus.AVAILABLE:
        parts.append(quote_obs.valuation.provenance)
    cached = any(part.source is ValuationSource.CACHED_PROVIDER for part in parts)
    origin = WatchlistOrigin.CACHED_PROVIDER if cached else WatchlistOrigin.PROVIDER
    values = []
    for label in PERIODS:
        result = results[PERIOD_KEY_TO_ENUM[label]]
        if result.percent is not None:
            values.append(
                WatchlistPeriodValue(
                    label, result.percent, origin, None, result.reference_date
                )
            )
        else:
            values.append(
                WatchlistPeriodValue(
                    label,
                    None,
                    WatchlistOrigin.NONE,
                    result.reason or "history_unavailable",
                )
            )
    if not any(value.available for value in values):
        reasons = [value.reason for value in values]
        return sheet_entry(item, reasons[0], period_reasons=reasons)

    sheet_today = False
    change = item.sheet_change_percent_points
    if not values[0].available and change is not None:
        values[0] = WatchlistPeriodValue(
            "Heute", change, WatchlistOrigin.SHEET, values[0].reason
        )
        sheet_today = True
    today = results[PERIOD_KEY_TO_ENUM["Heute"]]
    shown = next(
        (r for r in results.values() if r.current_price is not None), None
    )
    retrieved_at = min(part.retrieved_at for part in parts)
    if cached:
        age = format_snapshot_age(retrieved_at, now)
        text = (
            "Aktualisierung fehlgeschlagen – letzter gültiger Stand vom "
            f"{_format_timestamp(retrieved_at)} (Alter: {age})"
        )
    else:
        age = None
        text = "Anbieterdaten"
        if today.current_date is not None:
            text += f", Kursstand {today.current_date:%d.%m.%Y}"
        if today.market_open:
            text += " (Börse geöffnet)"
    if sheet_today:
        text += f"; Heute: {SHEET_VALUE_LABEL}"
    return WatchlistEntry(
        item,
        WatchlistEntryStatus.CACHED if cached else WatchlistEntryStatus.LIVE,
        text,
        tuple(values),
        shown.current_price if shown else None,
        shown.unit if shown else None,
        origin if shown else WatchlistOrigin.NONE,
        None,
        today.current_date,
        today.market_open,
        retrieved_at,
        age,
        item.warnings,
    )


@dataclass(frozen=True, slots=True)
class _Planned:
    item: WatchlistItem
    listing: VerifiedListing | None = None
    calendar: SessionCalendar | None = None
    running: bool = False


class WatchlistRefreshCoordinator:
    """Atomic Watchlist refresh: Sheet tab, provider data, period returns.

    Owns a ``QuoteCache`` (TTL, backoff, last valid provider state) whose
    holdings slot also keeps the last non-FAILED Sheet read. If a refresh fails
    after a success, the complete previous bundle is returned with its age;
    without any earlier state the bundle says "Daten nicht verfügbar".
    """

    def __init__(
        self,
        read_watchlist: Callable[[datetime], WatchlistSnapshot],
        *,
        calendars: Mapping[str, SessionCalendar],
        ttls: CacheTTLs | None = None,
        provider_factory: Callable[
            [tuple[VerifiedListing, ...]], MarketDataProvider
        ] = YFinanceProvider,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """
        :param read_watchlist: Read-only loader taking ``attempted_at``.
        :param calendars: Session calendar per Yahoo exchangeName (``venue_calendars``).
        :param ttls: Cache lifetimes; defaults to ``CacheTTLs()``.
        :param provider_factory: Builds the provider for a verified registry.
        :param clock: Injectable aware clock.
        """
        self._read = read_watchlist
        self._calendars = dict(calendars)
        self._clock = clock
        self._provider = _SwitchableProvider(provider_factory)
        self._cache = QuoteCache(
            self._provider, ttls if ttls is not None else CacheTTLs(), clock=clock
        )
        self._last_bundle: WatchlistBundle | None = None
        self._lock = threading.RLock()

    @property
    def last_bundle(self) -> WatchlistBundle | None:
        return self._last_bundle

    def _fetch(self, attempted_at: datetime) -> WatchlistSnapshot:
        try:
            return self._read(attempted_at)
        except Exception:
            return WatchlistSnapshot(
                attempted_at,
                SnapshotStatus.FAILED,
                warnings=(
                    DataWarning(
                        "sheet_transport_error", "Sheet read could not be completed."
                    ),
                ),
            )

    def _fallback(
        self, now: datetime, failed: WatchlistSnapshot | None = None
    ) -> WatchlistBundle:
        last = self._last_bundle
        if last is not None:
            age = format_snapshot_age(last.snapshot.attempted_at, now)
            stamp = _format_timestamp(last.snapshot.attempted_at)
            return replace(
                last,
                last_attempt_at=now,
                using_cached_stand=True,
                status_message=(
                    f"Aktualisierung fehlgeschlagen – vorhandener Stand vom {stamp} "
                    f"(Alter: {age}) bleibt unverändert erhalten."
                ),
            )
        snapshot = (
            failed
            if failed is not None and failed.status is SnapshotStatus.FAILED
            else WatchlistSnapshot(
                now,
                SnapshotStatus.FAILED,
                warnings=(
                    DataWarning("data_unavailable", "Kein gültiger Datenstand verfügbar."),
                ),
            )
        )
        return WatchlistBundle(
            False,
            snapshot,
            (),
            now,
            False,
            f"{DATA_UNAVAILABLE_TEXT}: Watchlist konnte nicht geladen werden "
            "und es liegt kein vorheriger Stand vor.",
        )

    def load(self, *, refresh: bool = False) -> WatchlistBundle:
        """Refresh (or reuse within the TTLs) and swap the bundle atomically.

        :param refresh: Skip cache TTLs once; backoff still applies.
        """
        with self._lock:
            now = self._clock()
            try:
                raw = self._cache.holdings(lambda: self._fetch(now), refresh=refresh)
            except Exception:
                return self._fallback(now)
            if raw is None or raw.status is SnapshotStatus.FAILED:
                return self._fallback(now, raw)
            if (
                any(w.code == _CACHED_SNAPSHOT_CODE for w in raw.warnings)
                and self._last_bundle is not None
            ):
                return self._fallback(now, raw)
            try:
                entries = self._entries(raw, now, refresh)
            except Exception:
                return self._fallback(now)
            message = (
                f"Daten erfolgreich aktualisiert ({_format_timestamp(now)})."
                if refresh
                else None
            )
            bundle = WatchlistBundle(True, raw, entries, now, False, message)
            self._last_bundle = bundle
            return bundle

    def _failure_ranks(self) -> dict[Instrument, int]:
        """Consecutive failed provider fetches per instrument, quote and history summed.

        Taken from the cache's own counters, exactly what ``QuoteCache.quotes`` sorts by.
        They rise only when a fetch was attempted and failed (a success resets them), so
        results served from the cache merely because of a backoff do not count. The
        coordinator keeps no failure state of its own.
        """
        ranks: dict[Instrument, int] = {}
        for key, entry in self._cache._entries.items():
            if entry.failures and key[0] in ("quote", "history"):
                ranks[key[1]] = ranks.get(key[1], 0) + entry.failures
        return ranks

    def _entries(
        self, snapshot: WatchlistSnapshot, now: datetime, refresh: bool
    ) -> tuple[WatchlistEntry, ...]:
        plan: list[_Planned] = []
        listings: list[VerifiedListing] = []
        claimed: set[tuple[str, str]] = set()
        for item in snapshot.items:
            if item.status is not WatchlistRowStatus.MAPPED:
                plan.append(_Planned(item))
                continue
            key = (item.mapping.exchange, item.mapping.symbol)
            if key in claimed:  # Parser prevents this; a hand-built snapshot must not.
                plan.append(_Planned(item))
                continue
            claimed.add(key)
            listing = watchlist_listing(item)
            listings.append(listing)
            calendar = self._calendars.get(item.mapping.exchange)
            # Only a running session needs a quote; otherwise the last close counts.
            running = calendar is not None and session_running(
                calendar, item.mapping.timezone, now
            )
            plan.append(_Planned(item, listing, calendar, running))

        self._provider.use(tuple(listings))
        # Provider-wide backoff opens after three failures in a row, and a recovery probe
        # takes the first due key. Candidates are therefore fetched in one common order for
        # both kinds: rows without failed fetches first (histories before quotes, then
        # sheet order), known-defective rows last, so rejected symbols cannot lock out the
        # valid rows behind them, whatever the market state.
        ranks = self._failure_ranks()
        tasks = [
            ("history", step)
            for step in plan
            if step.listing is not None and step.calendar is not None
        ] + [("quote", step) for step in plan if step.running]
        tasks.sort(key=lambda task: ranks.get(task[1].listing.instrument, 0))
        quotes: dict[Instrument, PriceObservation] = {}
        histories: dict[Instrument, PriceHistory] = {}
        for kind, step in tasks:
            instrument = step.listing.instrument
            if kind == "quote":
                quotes[instrument] = self._cache.quote(instrument, refresh=refresh)
            else:
                histories[instrument] = self._cache.history(
                    instrument,
                    step.calendar.first_day,
                    step.calendar.last_day,
                    refresh=refresh,
                )
        entries = []
        for step in plan:
            item, listing, calendar = step.item, step.listing, step.calendar
            if listing is None:
                entry = sheet_entry(item)
                if item.status is WatchlistRowStatus.MAPPED:
                    entry = replace(
                        entry,
                        warnings=entry.warnings
                        + (
                            DataWarning(
                                "duplicate_yahoo_symbol",
                                "Yahoo-Symbol wird bereits von einer anderen Zeile abgerufen.",
                                "symbol",
                                item.source_row,
                            ),
                        ),
                    )
            elif calendar is None:
                entry = sheet_entry(item, "calendar_incomplete")
            else:
                entry = _provider_entry(
                    item,
                    histories[listing.instrument],
                    quotes.get(listing.instrument) if step.running else None,
                    calendar,
                    now,
                )
            entries.append(entry)
        return tuple(entries)


def _coordinator(config: AppConfig, session) -> WatchlistRefreshCoordinator | None:
    if config.source.watchlist_range is None:
        return None
    reader = WatchlistSheetsReader(
        config.source, session, LOCAL_SHEET_METADATA.number_locale
    )
    return WatchlistRefreshCoordinator(
        reader.read,
        calendars=venue_calendars(date.today()),
        ttls=config.ttls,
    )


def build_local_watchlist_coordinator(
    config: AppConfig, session=None
) -> WatchlistRefreshCoordinator | None:
    """Local mode: None without ``PORTFOLIO_WATCHLIST_RANGE``.

    Pass the depot's session to share one read-only service-account session.
    """
    if config.source.watchlist_range is None:
        return None
    return _coordinator(
        config,
        session
        if session is not None
        else build_readonly_session(config.source.credentials_file),
    )


def build_production_watchlist_coordinator(
    config: AppConfig, service_account: object, session=None
) -> WatchlistRefreshCoordinator | None:
    """Production: same ``[sheets_service_account]`` secret, after owner login."""
    if config.source.watchlist_range is None:
        return None
    return _coordinator(
        config,
        session
        if session is not None
        else build_readonly_session_from_info(service_account),
    )
