"""Synthetic Ticker contracts only; pytest-socket blocks IP access throughout."""

import inspect
import sys
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.market_data import (
    AdjustmentBasis,
    VerifiedFxPair,
    YFinanceProvider,
)
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    ProviderMapping,
    QuoteUnit,
    ValuationSource,
    ValueStatus,
)

RETRIEVED = datetime(2026, 10, 22, 20, 5, tzinfo=UTC)
QUOTED = datetime(2026, 10, 22, 20, tzinfo=UTC)
USD = QuoteUnit("USD", Decimal(1))
EUR = QuoteUnit("EUR", Decimal(1))
GBP_PENCE = QuoteUnit("GBP", Decimal("0.01"))
START, END = date(2026, 10, 20), date(2026, 10, 23)


def listing(unit=USD):
    return VerifiedListing(
        Instrument(
            InstrumentType.STOCK,
            instrument_id="synthetic-security",
            display_ticker="SYNTHETIC",
            trading_venue="SYNTHETIC-EXCHANGE",
            quote_unit=unit,
            provider_mapping=ProviderMapping("yfinance", "SYNTHETIC.LISTING"),
        ),
        equity_form=EquityForm.SHARE,
    )


def frame():
    # Yahoo Close is ALREADY split-comparable. Dividend-adjusted Adj Close is
    # deliberately different and must not become price-only history.
    return pd.DataFrame(
        {
            "Close": [50.0, 51.0, 49.0],
            "Adj Close": [48.0, 48.96, 49.0],
            "Stock Splits": [0.0, 2.0, 0.0],
            "Dividends": [0.0, 0.0, 2.0],
        },
        index=pd.date_range("2026-10-20", periods=3, tz="America/New_York"),
    )


def metadata():
    return {
        "symbol": "SYNTHETIC.LISTING",
        "exchangeName": "SYNTHETIC-EXCHANGE",
        "currency": "USD",
        "exchangeTimezoneName": "America/New_York",
        # Public get_history_metadata() returns this formatted aware Timestamp.
        "regularMarketPrice": 49.0,
        "regularMarketTime": pd.Timestamp(QUOTED),
        "postMarketPrice": 99.0,
        "preMarketPrice": 101.0,
    }


class SyntheticTicker:
    def __init__(self):
        self.frame = frame()
        self.metadata = metadata()
        self.calls = []
        self.symbols = []

    def factory(self, symbol):
        self.symbols.append(symbol)
        return self

    def history(self, **kwargs):
        self.calls.append(kwargs)
        return self.frame

    def get_history_metadata(self):
        return self.metadata


@pytest.fixture
def setup_provider():
    ticker = SyntheticTicker()
    verified = listing()
    provider = YFinanceProvider(
        (verified,), ticker_factory=ticker.factory, clock=lambda: RETRIEVED
    )
    return provider, verified.instrument, ticker


def code(result):
    return result.warnings[0].code


def test_latest_regular_quote_keeps_time_source_unit_and_adjustment(setup_provider):
    provider, instrument, ticker = setup_provider
    result = provider.latest_quote(instrument)
    value = result.valuation
    assert value.status is ValueStatus.AVAILABLE
    assert value.price == Decimal("49.0")  # No pre/post-market price.
    assert value.unit == USD
    assert value.provenance.source is ValuationSource.PROVIDER
    assert value.provenance.provider == "yfinance"
    assert value.provenance.observed_at == QUOTED
    assert value.provenance.observed_at.tzinfo.key == "America/New_York"
    assert value.provenance.retrieved_at == RETRIEVED
    assert result.adjustment_basis is AdjustmentBasis.YAHOO_SPLIT_ADJUSTED_PRICE
    assert ticker.symbols == ["SYNTHETIC.LISTING"]
    assert ticker.calls == [
        {
            "period": "5d",
            "interval": "1d",
            "prepost": False,
            "actions": True,
            "auto_adjust": False,
            "back_adjust": False,
            "repair": False,
            "keepna": True,
            "rounding": False,
            "timeout": 10,
            "raise_errors": True,
        }
    ]


def test_default_factory_is_lazy_and_replaceable_without_provider_requests(monkeypatch):
    ticker = SyntheticTicker()
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=ticker.factory))
    verified = listing()
    provider = YFinanceProvider((verified,), clock=lambda: RETRIEVED)
    assert ticker.symbols == []
    assert provider.latest_quote(verified.instrument).valuation.price == Decimal(49)
    assert ticker.symbols == [verified.instrument.provider_mapping.symbol]


def test_price_only_history_uses_close_without_double_split_or_dividend_adjustment(
    setup_provider,
):
    provider, instrument, ticker = setup_provider
    result = provider.history(instrument, START, END)
    assert result.status is ValueStatus.AVAILABLE
    assert [point.close for point in result.points] == [
        Decimal(50),
        Decimal(51),
        Decimal(49),
    ]
    assert [point.split_ratio for point in result.points] == [0, 2, 0]
    assert [point.session_date for point in result.points] == [
        date(2026, 10, day) for day in (20, 21, 22)
    ]
    assert result.provenance.observed_at is None  # Midnight bar label isn't quote time.
    assert result.provenance.retrieved_at == RETRIEVED
    assert result.exchange_timezone == "America/New_York"
    assert result.unit == USD
    assert result.adjustment_basis is AdjustmentBasis.YAHOO_SPLIT_ADJUSTED_PRICE
    assert ticker.calls[0]["start"] == START.isoformat()
    assert ticker.calls[0]["end"] == END.isoformat()
    assert "period" not in ticker.calls[0]
    assert ticker.calls[0]["auto_adjust"] is ticker.calls[0]["repair"] is False


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"regularMarketPrice": None}, "invalid_price"),
        ({"regularMarketPrice": float("nan")}, "invalid_price"),
        ({"regularMarketPrice": float("inf")}, "invalid_price"),
        ({"regularMarketPrice": -1}, "invalid_price"),
        ({"regularMarketPrice": True}, "invalid_price"),
        ({"regularMarketTime": None}, "missing_quote_time"),
        ({"regularMarketTime": "2026-10-22"}, "missing_quote_time"),
        ({"regularMarketTime": QUOTED.replace(tzinfo=None)}, "missing_quote_time"),
        ({"regularMarketTime": pd.NaT}, "missing_quote_time"),
        ({"regularMarketTime": int(QUOTED.timestamp() * 1000)}, "invalid_quote_time"),
        ({"regularMarketTime": int(RETRIEVED.timestamp()) + 1}, "invalid_quote_time"),
        ({"exchangeTimezoneName": None}, "unknown_exchange_timezone"),
        ({"exchangeTimezoneName": "SYNTHETIC/Unknown"}, "unknown_exchange_timezone"),
        ({"currency": None}, "unknown_or_conflicting_unit"),
        ({"currency": "EUR"}, "unknown_or_conflicting_unit"),
        ({"symbol": "SYNTHETIC.UNDERLYING"}, "instrument_metadata_mismatch"),
        ({"exchangeName": "SYNTHETIC-OTHER"}, "instrument_metadata_mismatch"),
    ],
)
def test_missing_invalid_or_conflicting_metadata_is_unavailable(
    setup_provider, changes, expected
):
    provider, instrument, ticker = setup_provider
    ticker.metadata.update(changes)
    result = provider.latest_quote(instrument)
    assert result.valuation.price is None
    assert result.valuation.status is ValueStatus.MISSING
    assert code(result.valuation) == expected
    assert result.adjustment_basis is None
    assert result.valuation.provenance.observed_at is None


def test_explicit_zero_quote_is_available(setup_provider):
    provider, instrument, ticker = setup_provider
    ticker.metadata["regularMarketPrice"] = 0
    assert provider.latest_quote(instrument).valuation.price == Decimal(0)


def test_raw_yahoo_quote_time_is_explicitly_unix_seconds(setup_provider):
    provider, instrument, ticker = setup_provider
    ticker.metadata["regularMarketTime"] = int(QUOTED.timestamp())
    assert provider.latest_quote(instrument).valuation.provenance.observed_at == QUOTED


@pytest.mark.parametrize(
    "currency,available", [("GBp", True), ("GBP", False), ("GBX", False)]
)
def test_verified_pence_unit_is_preserved_without_hidden_scaling(currency, available):
    ticker = SyntheticTicker()
    ticker.metadata["currency"] = currency
    verified = listing(GBP_PENCE)
    provider = YFinanceProvider(
        (verified,), ticker_factory=ticker.factory, clock=lambda: RETRIEVED
    )
    result = provider.latest_quote(verified.instrument).valuation
    assert (result.status is ValueStatus.AVAILABLE) is available
    if available:
        assert result.price == Decimal(49)
        assert result.unit == GBP_PENCE


@pytest.mark.parametrize(
    "change",
    [
        {"instrument_id": None, "provider_mapping": None},
        {"instrument_id": "synthetic-unverified"},
        {"trading_venue": "SYNTHETIC-OTHER"},
        {"provider_mapping": ProviderMapping("yfinance", "SYNTHETIC.UNDERLYING")},
        {"quote_unit": EUR},
    ],
)
def test_unverified_mappings_never_request_a_symbol(setup_provider, change):
    provider, instrument, ticker = setup_provider
    unresolved = replace(instrument, **change)
    assert code(provider.latest_quote(unresolved).valuation) == "unverified_mapping"
    assert code(provider.history(unresolved, START, END)) == "unverified_mapping"
    assert ticker.symbols == []


def test_derivative_without_own_coverage_never_uses_underlying(setup_provider):
    _, underlying, ticker = setup_provider
    derivative = VerifiedListing(
        Instrument(
            InstrumentType.DERIVATIVE,
            instrument_id="synthetic-product",
            source_symbol=underlying.provider_mapping.symbol,
            display_ticker=underlying.display_ticker,
            trading_venue=underlying.trading_venue,
        ),
        product_id="synthetic-product-id",
    )
    provider = YFinanceProvider((listing(), derivative), ticker_factory=ticker.factory)
    assert (
        code(provider.latest_quote(derivative.instrument).valuation)
        == "no_provider_coverage"
    )
    assert ticker.symbols == []


def test_unsupported_provider_never_calls_factory():
    verified = listing()
    verified = replace(
        verified,
        instrument=replace(
            verified.instrument,
            provider_mapping=ProviderMapping("synthetic-other", "SYNTHETIC"),
        ),
    )
    ticker = SyntheticTicker()
    provider = YFinanceProvider((verified,), ticker_factory=ticker.factory)
    assert (
        code(provider.latest_quote(verified.instrument).valuation)
        == "unsupported_provider"
    )
    assert ticker.symbols == []


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("empty", "missing_data"),
        ("short", "insufficient_history"),
        ("nan", "invalid_price"),
        ("missing_close", "invalid_history"),
        ("missing_splits", "invalid_history"),
        ("naive", "invalid_history"),
        ("wrong_tz", "invalid_history"),
        ("duplicate", "invalid_history"),
        ("unsorted", "invalid_history"),
        ("out_of_range", "invalid_session_date"),
        ("intraday", "invalid_session_date"),
        ("negative_split", "invalid_price"),
    ],
)
def test_history_failures_keep_no_usable_series(setup_provider, kind, expected):
    provider, instrument, ticker = setup_provider
    if kind == "empty":
        ticker.frame = ticker.frame.iloc[:0]
    elif kind == "short":
        ticker.frame = ticker.frame.iloc[:1]
    elif kind == "nan":
        ticker.frame.loc[ticker.frame.index[1], "Close"] = float("nan")
    elif kind.startswith("missing_"):
        ticker.frame = ticker.frame.drop(
            columns={"missing_close": "Close", "missing_splits": "Stock Splits"}[kind]
        )
    elif kind == "naive":
        ticker.frame.index = ticker.frame.index.tz_localize(None)
    elif kind == "wrong_tz":
        ticker.frame.index = ticker.frame.index.tz_convert("UTC")
    elif kind == "duplicate":
        ticker.frame = pd.concat([ticker.frame, ticker.frame.iloc[:1]])
    elif kind == "unsorted":
        ticker.frame = ticker.frame.iloc[::-1]
    elif kind == "out_of_range":
        ticker.frame.index += pd.Timedelta(days=1)
    elif kind == "intraday":
        ticker.frame.index += pd.Timedelta(hours=1)
    elif kind == "negative_split":
        ticker.frame.loc[ticker.frame.index[1], "Stock Splits"] = -2
    result = provider.history(instrument, START, END)
    assert result.status is ValueStatus.MISSING
    assert result.points == ()
    assert result.adjustment_basis is None
    assert code(result) == expected


def test_provider_failure_is_sanitized_and_does_not_retry_or_substitute(
    setup_provider, caplog, capsys
):
    _, instrument, ticker = setup_provider

    def failure(symbol):
        ticker.symbols.append(symbol)
        raise RuntimeError("synthetic-private-url-and-credentials")

    provider = YFinanceProvider((listing(),), ticker_factory=failure)
    result = provider.latest_quote(instrument)
    assert code(result.valuation) == "provider_failure"
    assert ticker.symbols == [instrument.provider_mapping.symbol]
    assert "synthetic-private" not in repr(result) + caplog.text + str(
        capsys.readouterr()
    )


def test_fx_direction_units_and_mapping_are_explicit_and_preserved():
    # Intentionally unrelated symbol text: direction comes solely from the pair.
    pair = VerifiedFxPair(
        USD, EUR, ProviderMapping("yfinance", "SYNTHETIC.FX"), "SYNTHETIC-FX"
    )
    ticker = SyntheticTicker()
    ticker.metadata.update(
        symbol=pair.mapping.symbol,
        exchangeName=pair.trading_venue,
        currency="EUR",
        regularMarketPrice=0.8,
    )
    provider = YFinanceProvider(
        (), ticker_factory=ticker.factory, clock=lambda: RETRIEVED
    )
    result = provider.fx(pair)
    assert result.pair is pair
    assert result.pair.base_unit == USD and result.pair.quote_unit == EUR
    assert result.rate == Decimal("0.8")  # 0.8 EUR per 1 USD; never inverted.
    assert result.status is ValueStatus.AVAILABLE
    assert result.provenance.observed_at == QUOTED
    assert result.provenance.retrieved_at == RETRIEVED
    assert ticker.symbols == [pair.mapping.symbol]
    inverse = replace(
        pair,
        base_unit=EUR,
        quote_unit=USD,
        mapping=ProviderMapping("yfinance", "SYNTHETIC.INVERSE"),
    )
    unavailable = provider.fx(inverse)
    assert unavailable.pair is inverse
    assert unavailable.rate is None
    assert code(unavailable) == "instrument_metadata_mismatch"
    assert ticker.symbols == [pair.mapping.symbol, inverse.mapping.symbol]


@pytest.mark.parametrize("price", [0, -1, None, float("nan")])
def test_fx_unavailable_rates_do_not_acquire_an_inverse_or_identity_fallback(
    setup_provider, price
):
    provider, _, ticker = setup_provider
    ticker.metadata["regularMarketPrice"] = price
    pair = VerifiedFxPair(
        EUR, USD, ProviderMapping("yfinance", "SYNTHETIC.LISTING"), "SYNTHETIC-EXCHANGE"
    )
    result = provider.fx(pair)
    assert result.pair is pair and result.rate is None
    assert result.status is ValueStatus.MISSING
    assert len(ticker.symbols) == 1


def test_pinned_official_library_defaults_and_dividend_adjustment_contract():
    import yfinance
    from yfinance.scrapers.history import PriceHistory
    from yfinance.utils import auto_adjust, format_history_metadata

    assert yfinance.__version__ == "1.7.0"
    defaults = inspect.signature(PriceHistory.history).parameters
    assert defaults["auto_adjust"].default is True
    assert defaults["back_adjust"].default is defaults["repair"].default is False
    assert defaults["prepost"].default is defaults["keepna"].default is False
    data = pd.DataFrame(
        {
            "Open": [50.0],
            "High": [51.0],
            "Low": [49.0],
            "Close": [50.0],
            "Adj Close": [48.0],
        }
    )
    assert auto_adjust(data)["Close"].iloc[0] == 48.0
    assert data["Close"].iloc[0] == 50.0
    raw = metadata()
    raw["regularMarketTime"] = int(QUOTED.timestamp())
    formatted = format_history_metadata(raw)
    assert isinstance(formatted["regularMarketTime"], pd.Timestamp)
    assert formatted["regularMarketTime"] == QUOTED
