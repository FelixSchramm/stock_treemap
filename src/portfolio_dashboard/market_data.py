"""Small, replaceable market-data boundary; no cache, returns or valuation math.

Only explicitly verified mappings are requested. Importing this module performs
no provider access; the default Ticker is created lazily when a method is called.
Daily bars have session dates, not invented close-of-session timestamps.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from portfolio_dashboard.instruments import VerifiedListing, resolve_listing
from portfolio_dashboard.models import (
    DataWarning,
    Instrument,
    Provenance,
    ProviderMapping,
    QuoteUnit,
    Valuation,
    ValuationSource,
    ValueStatus,
)


class AdjustmentBasis(Enum):
    # Yahoo Close already incorporates splits; Adj Close also adjusts dividends.
    YAHOO_SPLIT_ADJUSTED_PRICE = "yahoo_close_split_adjusted_no_dividends"


@dataclass(frozen=True, slots=True)
class PriceObservation:
    valuation: Valuation
    adjustment_basis: AdjustmentBasis | None = None


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    session_date: date
    close: Decimal
    split_ratio: Decimal  # Provider event: new shares / old shares; 0 = no event.


@dataclass(frozen=True, slots=True)
class PriceHistory:
    status: ValueStatus
    provenance: Provenance
    unit: QuoteUnit | None = None
    exchange_timezone: str | None = None
    adjustment_basis: AdjustmentBasis | None = None
    points: tuple[HistoryPoint, ...] = ()
    warnings: tuple[DataWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class VerifiedFxPair:
    """Caller-verified rate in quote units per ONE base unit, never ticker inference.

    Like VerifiedListing, construction asserts prior external verification.
    trading_venue is the provider's exact exchangeName; no inverse pair fallback.
    """

    base_unit: QuoteUnit
    quote_unit: QuoteUnit
    mapping: ProviderMapping
    trading_venue: str

    def __post_init__(self) -> None:
        for unit in (self.base_unit, self.quote_unit):
            if not isinstance(unit, QuoteUnit) or unit.scale_to_currency is None:
                raise ValueError("FX requires explicit base and quote currency units")
        if not isinstance(self.mapping, ProviderMapping):
            raise TypeError("mapping must be ProviderMapping")
        if not isinstance(self.trading_venue, str) or not self.trading_venue.strip():
            raise ValueError("trading_venue must be nonblank text")


@dataclass(frozen=True, slots=True)
class FxObservation:
    pair: VerifiedFxPair
    rate: Decimal | None
    status: ValueStatus
    provenance: Provenance
    warnings: tuple[DataWarning, ...] = ()


class MarketDataProvider(Protocol):
    def latest_quote(self, instrument: Instrument) -> PriceObservation: ...

    def history(
        self, instrument: Instrument, start: date, end: date, *, minimum_points: int = 2
    ) -> PriceHistory: ...

    def fx(self, pair: VerifiedFxPair) -> FxObservation: ...


class Ticker(Protocol):
    def history(self, **kwargs) -> pd.DataFrame: ...

    def get_history_metadata(self) -> Mapping: ...


def _ticker(symbol: str) -> Ticker:
    import yfinance

    return yfinance.Ticker(symbol)


def _warning(code: str) -> tuple[DataWarning, ...]:
    # Do not attach exception messages, symbols, raw responses or request URLs.
    return (DataWarning(code, "Market data unavailable: " + code.replace("_", " ")),)


class _Unavailable(Exception):
    def __init__(self, code: str):
        self.code = code


def _number(value: object, *, positive: bool = False) -> Decimal:
    if isinstance(value, (bool, str)) or value is None:
        raise _Unavailable("invalid_price")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise _Unavailable("invalid_price") from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise _Unavailable("invalid_price")
    return result


def _check_unit(token: object, unit: QuoteUnit) -> None:
    minor_units = {
        "GBp": QuoteUnit("GBP", Decimal("0.01")),
        "ZAc": QuoteUnit("ZAR", Decimal("0.01")),
        "ILA": QuoteUnit("ILS", Decimal("0.01")),
    }
    if token == unit.currency and unit.scale_to_currency == Decimal(1):
        return
    if isinstance(token, str) and minor_units.get(token) == unit:
        return
    raise _Unavailable("unknown_or_conflicting_unit")


class YFinanceProvider:
    """yfinance 1.7.0, regularMarket quote fields and regular-session daily Close.

    Registry trading_venue uses the explicitly verified Yahoo exchangeName code.
    The factory/clock seams permit deterministic synthetic tests, without login,
    environment inspection, real symbols or live requests. No default registry.
    """

    def __init__(
        self,
        listings: tuple[VerifiedListing, ...],
        *,
        ticker_factory: Callable[[str], Ticker] = _ticker,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        listing_resolver: Callable[[Instrument], VerifiedListing | None] | None = None,
    ) -> None:
        self._listings = listings
        self._ticker_factory = ticker_factory
        self._clock = clock
        self._listing_resolver = listing_resolver

    def _provenance(self, observed_at: datetime | None = None) -> Provenance:
        return Provenance(
            ValuationSource.PROVIDER, self._clock(), observed_at, "yfinance"
        )

    def _listing(self, instrument: Instrument) -> Instrument:
        listing = (
            self._listing_resolver(instrument)
            if self._listing_resolver is not None
            else resolve_listing(instrument, self._listings)
        )
        if listing is None:
            raise _Unavailable("unverified_mapping")
        verified = listing.instrument
        if verified.provider_mapping is None:
            raise _Unavailable("no_provider_coverage")
        return verified

    def _read(
        self, mapping: ProviderMapping, venue: str, unit: QuoteUnit, **window
    ) -> tuple[pd.DataFrame, Mapping, ZoneInfo]:
        if mapping.provider != "yfinance":
            raise _Unavailable("unsupported_provider")
        try:
            ticker = self._ticker_factory(mapping.symbol)
            frame = ticker.history(
                interval="1d",
                prepost=False,
                actions=True,
                auto_adjust=False,
                back_adjust=False,
                repair=False,
                keepna=True,
                rounding=False,
                timeout=10,
                raise_errors=True,
                **window,
            )
            metadata = ticker.get_history_metadata()
        except Exception:
            raise _Unavailable("provider_failure") from None
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise _Unavailable("missing_data")
        if not isinstance(metadata, Mapping):
            raise _Unavailable("missing_metadata")
        if (
            metadata.get("symbol") != mapping.symbol
            or metadata.get("exchangeName") != venue
        ):
            raise _Unavailable("instrument_metadata_mismatch")
        _check_unit(metadata.get("currency"), unit)
        try:
            timezone = ZoneInfo(metadata["exchangeTimezoneName"])
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
            raise _Unavailable("unknown_exchange_timezone") from None
        return frame, metadata, timezone

    def _quote(
        self, mapping: ProviderMapping, venue: str, unit: QuoteUnit
    ) -> PriceObservation:
        _, metadata, timezone = self._read(mapping, venue, unit, period="5d")
        price = _number(metadata.get("regularMarketPrice"))
        timestamp = metadata.get("regularMarketTime")
        # yfinance format_history_metadata converts Yahoo's Unix seconds into an
        # aware pandas Timestamp. Also accept the documented raw seconds form.
        try:
            if isinstance(timestamp, datetime) and not pd.isna(timestamp):
                if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    raise _Unavailable("missing_quote_time")
                observed_at = timestamp.astimezone(timezone)
            elif type(timestamp) is int and timestamp > 0:
                observed_at = datetime.fromtimestamp(timestamp, UTC).astimezone(
                    timezone
                )
            else:
                raise _Unavailable("missing_quote_time")
        except (ValueError, OverflowError, OSError):
            raise _Unavailable("invalid_quote_time") from None
        provenance = self._provenance(observed_at)
        if observed_at > provenance.retrieved_at:
            raise _Unavailable("invalid_quote_time")
        return PriceObservation(
            Valuation(price, ValueStatus.AVAILABLE, provenance, unit),
            AdjustmentBasis.YAHOO_SPLIT_ADJUSTED_PRICE,
        )

    def latest_quote(self, instrument: Instrument) -> PriceObservation:
        try:
            listing = self._listing(instrument)
            return self._quote(
                listing.provider_mapping, listing.trading_venue, listing.quote_unit
            )
        except _Unavailable as error:
            return PriceObservation(
                Valuation(
                    None,
                    ValueStatus.MISSING,
                    self._provenance(),
                    warnings=_warning(error.code),
                )
            )

    def history(
        self, instrument: Instrument, start: date, end: date, *, minimum_points: int = 2
    ) -> PriceHistory:
        """Inclusive start, exclusive end; no calendar/reference/return selection.

        minimum_points checks only sample count. Calendar gaps, target-date
        coverage and removal of the incomplete current day belong to #22.
        """
        if type(start) is not date or type(end) is not date or start >= end:
            raise ValueError("history requires start date before exclusive end date")
        if type(minimum_points) is not int or minimum_points < 1:
            raise ValueError("minimum_points must be a positive integer")
        try:
            listing = self._listing(instrument)
            frame, _, timezone = self._read(
                listing.provider_mapping,
                listing.trading_venue,
                listing.quote_unit,
                start=start.isoformat(),
                end=end.isoformat(),
            )
            if (
                not isinstance(frame.index, pd.DatetimeIndex)
                or str(frame.index.tz) != timezone.key
                or not frame.index.is_unique
                or not frame.index.is_monotonic_increasing
                or "Close" not in frame
                or "Stock Splits" not in frame
            ):
                raise _Unavailable("invalid_history")
            points = []
            for timestamp, row in frame.iterrows():
                if pd.isna(timestamp) or timestamp.time() != datetime.min.time():
                    raise _Unavailable("invalid_session_date")
                session_date = timestamp.date()
                if not start <= session_date < end:
                    raise _Unavailable("invalid_session_date")
                points.append(
                    HistoryPoint(
                        session_date,
                        _number(row["Close"]),
                        _number(row["Stock Splits"]),
                    )
                )
            if len(points) < minimum_points:
                raise _Unavailable("insufficient_history")
            return PriceHistory(
                ValueStatus.AVAILABLE,
                self._provenance(),
                listing.quote_unit,
                timezone.key,
                AdjustmentBasis.YAHOO_SPLIT_ADJUSTED_PRICE,
                tuple(points),
            )
        except _Unavailable as error:
            return PriceHistory(
                ValueStatus.MISSING, self._provenance(), warnings=_warning(error.code)
            )

    def fx(self, pair: VerifiedFxPair) -> FxObservation:
        try:
            quote = self._quote(pair.mapping, pair.trading_venue, pair.quote_unit)
            if quote.valuation.price == 0:
                raise _Unavailable("invalid_fx_rate")
            return FxObservation(
                pair,
                quote.valuation.price,
                ValueStatus.AVAILABLE,
                quote.valuation.provenance,
            )
        except _Unavailable as error:
            return FxObservation(
                pair,
                None,
                ValueStatus.MISSING,
                self._provenance(),
                _warning(error.code),
            )
