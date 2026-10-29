"""Read-only yfinance adapter for annual fundamentals (M8-04, #73).

The adapter only retrieves and converts: frames and metadata become the plain
``ProviderStatements`` of ``fundamentals_normalization``, which does all the
financial interpretation. It is separate from the price provider in
``market_data`` and does not change it. Importing this module performs no
provider access; yfinance is imported lazily when the default ticker factory
runs, and tests inject a fake factory, clock and sleep, so nothing here needs
the network.

Every problem is translated into a sanitized failure class (``timeout``,
``access_restricted``, ``empty``, ...). Exception messages, symbols, URLs and raw
responses are never stored or logged. A request that cannot be tied to the
verified identity returns no data at all.
"""

import logging
import math
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

import pandas as pd

from portfolio_dashboard.fundamentals import VerifiedFundamentalIdentity
from portfolio_dashboard.fundamentals_normalization import (
    FAILURE_ACCESS_RESTRICTED,
    FAILURE_EMPTY,
    FAILURE_IDENTITY_MISMATCH,
    FAILURE_IDENTITY_NOT_SUPPORTED,
    FAILURE_INVALID_VALUES,
    FAILURE_METADATA_PLACEHOLDER,
    FAILURE_PROVIDER_ERROR,
    FAILURE_TIMEOUT,
    FAILURE_UNEXPECTED_SCHEMA,
    FAILURE_UNSUPPORTED_PROVIDER,
    INFO_KEYS,
    PROVIDER,
    FetchFailure,
    ProviderStatements,
    StatementTable,
)

DEFAULT_PAUSE_SECONDS = 1.5  # Between reads; the provider throttles silently.
INFO_ATTEMPTS = 3  # A throttled quote summary returns a stub without an error.

_ACCESS_ERRORS = frozenset({"YFRateLimitError", "TooManyRequests", "HTTPError"})
_TIMEOUT_ERRORS = frozenset(
    {"Timeout", "ReadTimeout", "ConnectTimeout", "TimeoutError", "timeout"}
)
_ACCESS_STATUS = frozenset({401, 403, 429})


class FundamentalsProvider(Protocol):
    """Replaceable read-only contract; one verified identity, one retrieval."""

    def fetch(self, identity: VerifiedFundamentalIdentity) -> ProviderStatements: ...


class Ticker(Protocol):
    def get_income_stmt(self, **kwargs) -> pd.DataFrame: ...

    def get_balance_sheet(self, **kwargs) -> pd.DataFrame: ...

    def get_cashflow(self, **kwargs) -> pd.DataFrame: ...

    def get_shares_full(self, **kwargs) -> pd.Series | None: ...

    def get_info(self) -> Mapping: ...


def _ticker(symbol: str) -> Ticker:
    import yfinance

    return yfinance.Ticker(symbol)


def classify_error(error: BaseException) -> str:
    """Map an exception to a sanitized class by type name and status only."""
    names = {cls.__name__ for cls in type(error).__mro__}
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if names & _ACCESS_ERRORS and not names & _TIMEOUT_ERRORS:
        if "HTTPError" not in names or status in _ACCESS_STATUS:
            return FAILURE_ACCESS_RESTRICTED
    if status in _ACCESS_STATUS:
        return FAILURE_ACCESS_RESTRICTED
    if names & _TIMEOUT_ERRORS:
        return FAILURE_TIMEOUT
    return FAILURE_PROVIDER_ERROR


@contextmanager
def _quiet_provider_logs():
    # The library can log symbols and URLs; keep that out of any log sink.
    logger = logging.getLogger("yfinance")
    previous = logger.disabled
    logger.disabled = True
    try:
        yield
    finally:
        logger.disabled = previous


def _to_decimal(value: object) -> tuple[Decimal | None, bool]:
    """(finite Decimal or None, invalid). NaN/None are missing; junk is invalid."""
    if value is None:
        return None, False
    if isinstance(value, bool) or isinstance(value, str):
        return None, True
    try:
        if pd.isna(value):
            return None, False
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None, True
    if not math.isfinite(number):
        return None, True
    return Decimal(str(number)), False


def _to_date(value: object) -> date | None:
    try:
        stamp = pd.Timestamp(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if pd.isna(stamp) else stamp.date()


def convert_frame(frame: object) -> tuple[StatementTable | None, tuple[str, ...]]:
    """DataFrame (rows = labels, columns = period ends) -> table and failure codes."""
    if not isinstance(frame, pd.DataFrame):
        return None, (FAILURE_UNEXPECTED_SCHEMA,)
    if frame.empty:
        return None, (FAILURE_EMPTY,)
    if not frame.index.is_unique or not frame.columns.is_unique:
        return None, (FAILURE_UNEXPECTED_SCHEMA,)
    if not all(isinstance(label, str) and label.strip() for label in frame.index):
        return None, (FAILURE_UNEXPECTED_SCHEMA,)
    ends = [_to_date(column) for column in frame.columns]
    # Periods in columns, labels in rows. A transposed or relabelled answer fails.
    if any(end is None for end in ends) or len(set(ends)) != len(ends):
        return None, (FAILURE_UNEXPECTED_SCHEMA,)
    invalid = False
    rows: dict[str, dict[date, Decimal | None]] = {}
    for label, values in zip(frame.index, frame.to_numpy(dtype=object)):
        cells = {}
        for end, raw in zip(ends, values):
            number, bad = _to_decimal(raw)
            invalid = invalid or bad
            cells[end] = number
        rows[label] = cells
    table = StatementTable(rows)
    if not table.period_ends():
        return None, (FAILURE_EMPTY,)
    return table, (FAILURE_INVALID_VALUES,) if invalid else ()


def convert_shares(series: object) -> tuple[tuple[tuple[date, Decimal], ...] | None, tuple[str, ...]]:
    """Dated share counts (one per date, last wins) from a time-indexed series."""
    if series is None:
        return None, (FAILURE_EMPTY,)
    if not isinstance(series, pd.Series):
        return None, (FAILURE_UNEXPECTED_SCHEMA,)
    if series.empty:
        return None, (FAILURE_EMPTY,)
    counts: dict[date, Decimal] = {}
    invalid = False
    for stamp, raw in series.items():
        end = _to_date(stamp)
        if end is None:
            return None, (FAILURE_UNEXPECTED_SCHEMA,)
        number, bad = _to_decimal(raw)
        if bad or (number is not None and number <= 0):
            invalid = True
        elif number is not None:
            counts[end] = number
    if not counts:
        return None, (FAILURE_INVALID_VALUES,) if invalid else (FAILURE_EMPTY,)
    ordered = tuple(sorted(counts.items()))
    return ordered, (FAILURE_INVALID_VALUES,) if invalid else ()


class YFinanceFundamentalsProvider:
    """yfinance 1.7.0 annual statements, share series and a minimal metadata subset.

    Only the explicitly verified fundamentals source is read: ``source.provider``
    must be ``yfinance`` and ``source.entity_id`` is the Yahoo symbol. There is no
    search, renaming or substitution. Quarterly statements are not read: the
    model needs annual flows, and every extra request raises the throttling risk.
    yfinance applies its own network timeout; its errors are classified below.
    """

    def __init__(
        self,
        *,
        ticker_factory: Callable[[str], Ticker] = _ticker,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        pause_seconds: float = DEFAULT_PAUSE_SECONDS,
        info_attempts: int = INFO_ATTEMPTS,
    ) -> None:
        if pause_seconds < 0 or info_attempts < 1:
            raise ValueError("pause must be nonnegative and attempts positive")
        self._ticker_factory = ticker_factory
        self._clock = clock
        self._sleep = sleep
        self._pause = pause_seconds
        self._info_attempts = info_attempts

    def _failed(self, dataset: str, code: str) -> ProviderStatements:
        return ProviderStatements(self._clock(), failures=(FetchFailure(dataset, code),))

    def fetch(self, identity: VerifiedFundamentalIdentity) -> ProviderStatements:
        if not isinstance(identity, VerifiedFundamentalIdentity):
            raise TypeError("identity must be VerifiedFundamentalIdentity")
        if any(issue.blocking for issue in identity.issues):
            # Banks, ADRs, unknown issuer kinds: no request, an explained status.
            return self._failed("request", FAILURE_IDENTITY_NOT_SUPPORTED)
        if identity.source.provider != PROVIDER:
            return self._failed("request", FAILURE_UNSUPPORTED_PROVIDER)
        symbol = identity.source.entity_id
        with _quiet_provider_logs():
            try:
                ticker = self._ticker_factory(symbol)
            except Exception as error:
                return self._failed("request", classify_error(error))
            info, failures = self._read_info(ticker, symbol)
            if info is None:
                return ProviderStatements(self._clock(), failures=tuple(failures))
            tables = {}
            for name, method in (
                ("annual_income", "get_income_stmt"),
                ("annual_balance", "get_balance_sheet"),
                ("annual_cashflow", "get_cashflow"),
            ):
                self._sleep(self._pause)
                table, codes = self._read(
                    lambda m=method: getattr(ticker, m)(freq="yearly"), convert_frame
                )
                tables[name] = table
                failures += [FetchFailure(name, code) for code in codes]
            self._sleep(self._pause)
            shares, codes = self._read(lambda: ticker.get_shares_full(), convert_shares)
            failures += [FetchFailure("shares_full", code) for code in codes]
        return ProviderStatements(
            self._clock(),
            info=info,
            dated_shares=shares,
            failures=tuple(failures),
            **tables,
        )

    def _read(self, call, convert):
        try:
            raw = call()
        except Exception as error:
            return None, (classify_error(error),)
        return convert(raw)

    def _read_info(self, ticker: Ticker, symbol: str):
        """Minimal metadata subset; a stub is retried a few times, then reported."""
        failures: list[FetchFailure] = []
        for attempt in range(self._info_attempts):
            try:
                raw = ticker.get_info()
            except Exception as error:
                code = classify_error(error)
                failures.append(FetchFailure("info", code))
                if code == FAILURE_ACCESS_RESTRICTED:
                    return None, failures  # Do not hammer a restricting provider.
                raw = None
            else:
                if not isinstance(raw, Mapping):
                    failures.append(FetchFailure("info", FAILURE_UNEXPECTED_SCHEMA))
                    raw = None
            if raw is not None:
                reported = raw.get("symbol")
                if reported is not None and reported != symbol:
                    # The answer belongs to another entity: use none of it.
                    return None, [FetchFailure("info", FAILURE_IDENTITY_MISMATCH)]
                if reported == symbol and raw.get("quoteType") and raw.get("currency"):
                    return {k: raw.get(k) for k in INFO_KEYS}, failures
            if attempt + 1 < self._info_attempts:
                self._sleep(self._pause * 4 * (attempt + 1))
        failures.append(FetchFailure("info", FAILURE_METADATA_PLACEHOLDER))
        return None, failures
