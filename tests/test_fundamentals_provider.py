"""Adapter tests with a fake ticker and invented frames; no network, no yfinance."""

import logging
import subprocess
import sys
import types
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from test_fundamentals_normalization import IDENTITY, INFO

import portfolio_dashboard.fundamentals_provider as provider_module
from portfolio_dashboard.fundamentals import (
    FundamentalField,
    FundamentalSource,
    IssuerKind,
)
from portfolio_dashboard.fundamentals_normalization import (
    ProviderExpectation,
    normalize_statements,
)
from portfolio_dashboard.fundamentals_provider import (
    FundamentalsProvider,
    YFinanceFundamentalsProvider,
    classify_error,
    convert_frame,
    convert_shares,
)
from portfolio_dashboard.models import ValueStatus

NOW = datetime(2025, 3, 15, 10, 0, tzinfo=UTC)
ENDS = ["2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31"]
BALANCE_ENDS = ENDS + ["2020-12-31"]
SECRET = "SECRET-SYMBOL-AND-URL"


def frame(rows, ends):
    return pd.DataFrame(rows, index=pd.to_datetime(ends)).T


def income_frame():
    return frame(
        {
            "Operating Income": [150e6, 140e6, 130e6, 120e6],
            "Pretax Income": [140e6, 130e6, 120e6, 110e6],
            "Tax Provision": [28e6, 26e6, 24e6, 22e6],
            "Net Income Common Stockholders": [100e6, 95e6, 90e6, 85e6],
            "Diluted Average Shares": [10e6] * 4,
            "Diluted EPS": [10.0, 9.5, 9.0, 8.5],
        },
        ENDS,
    )


def balance_frame():
    return frame(
        {
            "Current Assets": [300e6, 290e6, 280e6, 270e6, 260e6],
            "Current Liabilities": [200e6, 195e6, 190e6, 185e6, 180e6],
            "Cash Cash Equivalents And Short Term Investments": [100e6] * 5,
            "Current Debt And Capital Lease Obligation": [50e6] * 5,
            "Total Debt": [120e6] * 5,
            "Ordinary Shares Number": [10e6] * 5,
        },
        BALANCE_ENDS,
    )


def cashflow_frame():
    return frame(
        {
            "Depreciation And Amortization": [20e6] * 4,
            "Capital Expenditure": [-44e6] * 4,
            "Operating Cash Flow": [100e6] * 4,
            "Free Cash Flow": [56e6] * 4,
        },
        ENDS,
    )


def shares_series():
    index = pd.DatetimeIndex(["2025-02-27", "2025-03-01"], tz="America/New_York")
    return pd.Series([9_900_000.0, 10_000_000.0], index=index)


class FakeTicker:
    def __init__(self, **overrides):
        self.answers = {
            "get_income_stmt": income_frame(),
            "get_balance_sheet": balance_frame(),
            "get_cashflow": cashflow_frame(),
            "get_shares_full": shares_series(),
            "get_info": {**INFO, "symbol": "SYN.XX", "longName": "Name Not Passed On"},
        }
        self.answers.update(overrides)
        self.calls = []

    def _answer(self, name, kwargs):
        self.calls.append((name, kwargs))
        answer = self.answers[name]
        if isinstance(answer, list):  # Sequence of answers, consumed per call.
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def get_income_stmt(self, **kw):
        return self._answer("get_income_stmt", kw)

    def get_balance_sheet(self, **kw):
        return self._answer("get_balance_sheet", kw)

    def get_cashflow(self, **kw):
        return self._answer("get_cashflow", kw)

    def get_shares_full(self, **kw):
        return self._answer("get_shares_full", kw)

    def get_info(self):
        return self._answer("get_info", {})


def adapter(ticker=None, sleeps=None, **options):
    ticker = ticker or FakeTicker()
    sleeps = sleeps if sleeps is not None else []
    created = []

    def factory(symbol):
        created.append(symbol)
        return ticker

    result = YFinanceFundamentalsProvider(
        ticker_factory=factory,
        clock=lambda: NOW,
        sleep=sleeps.append,
        pause_seconds=options.pop("pause_seconds", 1.5),
        **options,
    )
    result.created = created
    return result


def failure_codes(statements):
    return {(f.dataset, f.code) for f in statements.failures}


class Timeout(Exception):
    pass


class ReadTimeout(Exception):
    pass


class YFRateLimitError(Exception):
    pass


def test_complete_fetch_converts_frames_and_feeds_the_normalizer():
    ticker = FakeTicker()
    fetched = adapter(ticker).fetch(IDENTITY)
    assert not fetched.failures and fetched.retrieved_at == NOW
    assert fetched.annual_income.value(
        "Operating Income", date(2024, 12, 31)
    ) == Decimal("150000000.0")
    assert fetched.dated_shares == (
        (date(2025, 2, 27), Decimal("9900000.0")),
        (date(2025, 3, 1), Decimal("10000000.0")),
    )
    # Only the minimal metadata subset leaves the adapter, never names or the symbol.
    assert set(fetched.info) == {
        "quoteType",
        "currency",
        "financialCurrency",
        "sharesOutstanding",
        "impliedSharesOutstanding",
    }
    assert [name for name, _ in ticker.calls] == [
        "get_info",
        "get_income_stmt",
        "get_balance_sheet",
        "get_cashflow",
        "get_shares_full",
    ]
    assert all(
        kw == {"freq": "yearly"}
        for name, kw in ticker.calls
        if name.startswith("get_") and name not in {"get_info", "get_shares_full"}
    )
    result = normalize_statements(IDENTITY, fetched, ProviderExpectation("EUR", 12, 31))
    assert not result.numbers_withheld
    ebit = [o for o in result.observations if o.field is FundamentalField.EBIT]
    assert ebit and all(o.status is ValueStatus.AVAILABLE for o in ebit)


def test_it_satisfies_the_replaceable_provider_contract_and_requests_pauses():
    sleeps = []
    instance = adapter(sleeps=sleeps)
    assert isinstance(instance, YFinanceFundamentalsProvider)
    provider: FundamentalsProvider = instance
    provider.fetch(IDENTITY)
    assert sleeps == [1.5] * 4  # Before each statement and the share series.


def test_only_the_verified_source_is_requested_without_substitution():
    instance = adapter()
    instance.fetch(IDENTITY)
    assert instance.created == ["SYN.XX"]
    other = replace(IDENTITY, source=FundamentalSource("other", "SYN.XX"))
    blocked = adapter()
    result = blocked.fetch(other)
    assert failure_codes(result) == {("request", "unsupported_provider")}
    assert blocked.created == []


def test_unsupported_issuers_make_no_request():
    bank = replace(IDENTITY, issuer_kind=IssuerKind.BANK)
    instance = adapter()
    result = instance.fetch(bank)
    assert failure_codes(result) == {("request", "identity_not_supported")}
    assert instance.created == []
    assert result.annual_income is None and result.info is None


def test_wrong_entity_in_the_answer_discards_everything():
    ticker = FakeTicker(get_info={**INFO, "symbol": "OTHER.XX"})
    result = adapter(ticker).fetch(IDENTITY)
    assert failure_codes(result) == {("info", "identity_mismatch")}
    assert result.annual_income is None and result.info is None
    assert [name for name, _ in ticker.calls] == ["get_info"]


def test_throttled_metadata_stub_is_retried_then_reported_without_statements():
    stub = {"trailingPegRatio": None}
    sleeps = []
    ticker = FakeTicker(get_info=[stub, stub, stub])
    result = adapter(ticker, sleeps).fetch(IDENTITY)
    assert failure_codes(result) == {("info", "metadata_placeholder")}
    assert [n for n, _ in ticker.calls].count("get_info") == 3
    assert sleeps == [6.0, 12.0]  # Growing pauses, bounded attempts.
    assert result.annual_income is None
    outcome = normalize_statements(IDENTITY, result, ProviderExpectation("EUR", 12, 31))
    assert outcome.numbers_withheld
    # A stub that recovers on a retry is accepted.
    good = {**INFO, "symbol": "SYN.XX"}
    ticker = FakeTicker(get_info=[stub, good])
    recovered = adapter(ticker, []).fetch(IDENTITY)
    assert not recovered.failures and recovered.info["currency"] == "EUR"


def test_symbol_less_metadata_counts_as_a_stub():
    ticker = FakeTicker(get_info=dict(INFO))
    result = adapter(ticker, info_attempts=1).fetch(IDENTITY)
    assert failure_codes(result) == {("info", "metadata_placeholder")}


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (YFRateLimitError(SECRET), "access_restricted"),
        (Timeout(SECRET), "timeout"),
        (ReadTimeout(SECRET), "timeout"),
        (TimeoutError(SECRET), "timeout"),
        (RuntimeError(SECRET), "provider_error"),
    ],
)
def test_exceptions_become_sanitized_classes_without_message_text(error, code):
    assert classify_error(error) == code
    ticker = FakeTicker(
        get_income_stmt=error,
        get_balance_sheet=error,
        get_cashflow=error,
        get_shares_full=error,
    )
    result = adapter(ticker).fetch(IDENTITY)
    assert failure_codes(result) == {
        (dataset, code)
        for dataset in (
            "annual_income",
            "annual_balance",
            "annual_cashflow",
            "shares_full",
        )
    }
    assert SECRET not in repr(result)
    outcome = normalize_statements(IDENTITY, result, ProviderExpectation("EUR", 12, 31))
    assert SECRET not in repr(outcome)


def test_http_status_marks_access_restriction():
    class StatusError(Exception):
        def __init__(self, status):
            self.response = types.SimpleNamespace(status_code=status)

    assert classify_error(StatusError(429)) == "access_restricted"
    assert classify_error(StatusError(403)) == "access_restricted"
    assert classify_error(StatusError(500)) == "provider_error"


def test_restricted_metadata_is_not_retried():
    ticker = FakeTicker(get_info=YFRateLimitError(SECRET))
    sleeps = []
    result = adapter(ticker, sleeps).fetch(IDENTITY)
    assert failure_codes(result) == {("info", "access_restricted")}
    assert [n for n, _ in ticker.calls] == ["get_info"] and sleeps == []


def test_factory_failure_is_a_sanitized_request_failure():
    def factory(symbol):
        raise RuntimeError(SECRET)

    instance = YFinanceFundamentalsProvider(ticker_factory=factory, clock=lambda: NOW)
    result = instance.fetch(IDENTITY)
    assert failure_codes(result) == {("request", "provider_error")}
    assert SECRET not in repr(result)


def test_empty_unexpected_and_changed_schemas_are_classified():
    transposed = income_frame().T  # Periods in rows: provider changed its layout.
    text_columns = pd.DataFrame({"a": [1.0], "b": [2.0]}, index=["row"])
    int_labels = pd.DataFrame([[1.0, 2.0]], columns=pd.to_datetime(ENDS[:2]), index=[7])
    duplicate = pd.DataFrame(
        [[1.0, 2.0]],
        columns=pd.to_datetime(["2024-12-31", "2024-12-31"]),
        index=["row"],
    )
    for bad, code in (
        (pd.DataFrame(), "empty"),
        (None, "unexpected_schema"),
        ({"not": "a frame"}, "unexpected_schema"),
        (transposed, "unexpected_schema"),
        (text_columns, "unexpected_schema"),
        (int_labels, "unexpected_schema"),
        (duplicate, "unexpected_schema"),
        (frame({"Operating Income": [float("nan")] * 2}, ENDS[:2]), "empty"),
    ):
        assert convert_frame(bad) == (None, (code,)), code
    ticker = FakeTicker(get_income_stmt=pd.DataFrame(), get_balance_sheet=transposed)
    result = adapter(ticker).fetch(IDENTITY)
    assert {("annual_income", "empty"), ("annual_balance", "unexpected_schema")} <= (
        failure_codes(result)
    )


def test_missing_and_invalid_cells_are_separated():
    mixed = frame(
        {
            "Operating Income": [1.5, float("nan"), float("inf"), "text"],
            "Other": [True, None, 2, 3],
        },
        ENDS,
    )
    table, codes = convert_frame(mixed)
    assert codes == ("invalid_values",)
    cells = table.rows["Operating Income"]
    assert cells[date(2024, 12, 31)] == Decimal("1.5")
    assert cells[date(2023, 12, 31)] is None  # NaN is missing, not zero.
    assert cells[date(2022, 12, 31)] is None and cells[date(2021, 12, 31)] is None
    assert table.rows["Other"][date(2024, 12, 31)] is None  # A bool is not a number.
    assert table.rows["Other"][date(2022, 12, 31)] == Decimal("2")
    # Zero stays an explicit zero.
    zero, _ = convert_frame(frame({"Capital Expenditure": [0.0, -1.0]}, ENDS[:2]))
    assert zero.rows["Capital Expenditure"][date(2024, 12, 31)] == Decimal("0.0")


def test_share_series_conversion():
    assert convert_shares(None) == (None, ("empty",))
    assert convert_shares(pd.Series(dtype=float)) == (None, ("empty",))
    assert convert_shares([1, 2]) == (None, ("unexpected_schema",))
    index = pd.to_datetime(["2025-01-01", "2025-02-01", "2025-03-01"])
    series = pd.Series([10.0, -5.0, float("nan")], index=index)
    counts, codes = convert_shares(series)
    assert counts == ((date(2025, 1, 1), Decimal("10.0")),)
    assert codes == ("invalid_values",)
    assert convert_shares(pd.Series([float("nan")], index=index[:1])) == (
        None,
        ("empty",),
    )
    assert convert_shares(pd.Series([-1.0], index=index[:1])) == (
        None,
        ("invalid_values",),
    )


def test_invalid_values_reach_the_quality_issues_not_the_numbers():
    income = income_frame().astype(object)
    income.loc["Operating Income", pd.Timestamp("2023-12-31")] = "junk"
    result = adapter(FakeTicker(get_income_stmt=income)).fetch(IDENTITY)
    assert ("annual_income", "invalid_values") in failure_codes(result)
    outcome = normalize_statements(IDENTITY, result, ProviderExpectation("EUR", 12, 31))
    assert "annual_income_invalid_values" in {i.code for i in outcome.issues}
    ebit = {
        o.period.end: o
        for o in outcome.observations
        if o.field is FundamentalField.EBIT
    }
    assert ebit[date(2023, 12, 31)].status is ValueStatus.MISSING


def test_provider_logging_is_silenced_during_a_fetch_and_restored():
    logger = logging.getLogger("yfinance")
    seen = []

    class Noisy(FakeTicker):
        def get_info(self):
            seen.append(logger.disabled)
            return super().get_info()

    logger.disabled = False
    adapter(Noisy()).fetch(IDENTITY)
    assert seen == [True] and logger.disabled is False


def test_default_factory_imports_yfinance_lazily(monkeypatch):
    created = []
    fake = types.SimpleNamespace(
        Ticker=lambda symbol: created.append(symbol) or FakeTicker()
    )
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    instance = YFinanceFundamentalsProvider(clock=lambda: NOW, sleep=lambda s: None)
    result = instance.fetch(IDENTITY)
    assert created == ["SYN.XX"] and not result.failures


def test_importing_the_adapter_does_not_import_yfinance_or_touch_the_price_provider():
    code = (
        "import sys\n"
        "import portfolio_dashboard.fundamentals_provider\n"
        "assert 'yfinance' not in sys.modules\n"
        "assert 'streamlit' not in sys.modules\n"
        "assert 'portfolio_dashboard.market_data' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
    market_data = Path(provider_module.__file__).with_name("market_data.py")
    assert "fundamentals" not in market_data.read_text(encoding="utf-8")


def test_constructor_and_argument_validation():
    with pytest.raises(ValueError):
        YFinanceFundamentalsProvider(pause_seconds=-1)
    with pytest.raises(ValueError):
        YFinanceFundamentalsProvider(info_attempts=0)
    with pytest.raises(TypeError):
        adapter().fetch("SYN.XX")
