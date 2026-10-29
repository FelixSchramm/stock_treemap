"""Offline tests with entirely invented frames; no provider response is used."""

import ast
import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from portfolio_dashboard.fundamentals_coverage import (
    ALL_CHECKS,
    REQUIRED_FIELDS,
    CoverageTarget,
    ProviderSnapshot,
    Status,
    aggregate,
    annual_chain,
    cell,
    evaluate_instrument,
    finite,
    key_values,
    load_universe,
    market_class,
    period_ends,
    quarterly_chain,
    render_aggregate_markdown,
    render_instrument_markdown,
)

NAN = float("nan")
AS_OF = date(2026, 10, 29)
YEAR_ENDS = ["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"]
QUARTER_ENDS = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"]


def frame(rows: dict[str, list[float]], ends: list[str]) -> pd.DataFrame:
    return pd.DataFrame(rows, index=ends).T


def target(**changes) -> CoverageTarget:
    values = {
        "case_id": "F-01",
        "trading_currency": "EUR",
        "expected_reporting_currency": "EUR",
        "fiscal_year_end": "12-31",
        "market_class": "Synthetic EUR",
        "symbol": "SYN1.XX",
        "label": "Synthetic Co",
    }
    return CoverageTarget(**{**values, **changes})


def income(**overrides) -> pd.DataFrame:
    rows = {
        "Total Revenue": [1000.0, 900.0, 800.0, 700.0],
        "Operating Income": [100.0, 90.0, 80.0, 70.0],
        "Pretax Income": [95.0, 85.0, 75.0, 65.0],
        "Tax Provision": [28.0, 25.0, 22.0, 19.0],
        "Net Income Common Stockholders": [70.0, 60.0, 53.0, 46.0],
        "Diluted Average Shares": [10.0, 10.0, 10.0, 10.0],
        "Diluted EPS": [7.0, 6.0, 5.3, 4.6],
    }
    rows.update(overrides)
    return frame(rows, YEAR_ENDS)


def cashflow(**overrides) -> pd.DataFrame:
    rows = {
        "Depreciation And Amortization": [20.0, 19.0, 18.0, 17.0],
        "Capital Expenditure": [-40.0, -38.0, -36.0, -34.0],
        "Operating Cash Flow": [120.0, 110.0, 100.0, 90.0],
        "Free Cash Flow": [80.0, 72.0, 64.0, 56.0],
    }
    rows.update(overrides)
    return frame(rows, YEAR_ENDS)


def balance(**overrides) -> pd.DataFrame:
    rows = {
        "Current Assets": [500.0, 450.0, 400.0, 350.0],
        "Current Liabilities": [300.0, 280.0, 260.0, 240.0],
        "Cash Cash Equivalents And Short Term Investments": [150.0, 140.0, 130.0, 120.0],
        "Current Debt And Capital Lease Obligation": [20.0, 20.0, 20.0, 20.0],
        "Long Term Debt And Capital Lease Obligation": [180.0, 170.0, 160.0, 150.0],
        "Total Debt": [200.0, 190.0, 180.0, 170.0],
        "Capital Lease Obligations": [30.0, 30.0, 30.0, 30.0],
        "Minority Interest": [5.0, 5.0, 5.0, 5.0],
        "Preferred Stock Equity": [0.0, 0.0, 0.0, 0.0],
        "Ordinary Shares Number": [10.0, 10.0, 10.0, 10.0],
    }
    rows.update(overrides)
    return frame(rows, YEAR_ENDS)


def quarterly(label: str = "Total Revenue") -> pd.DataFrame:
    return frame({label: [1.0] * 5}, QUARTER_ENDS)


def snapshot(**overrides) -> ProviderSnapshot:
    values = {
        "annual_income": income(),
        "annual_balance": balance(),
        "annual_cashflow": cashflow(),
        "quarterly_income": quarterly(),
        "quarterly_balance": quarterly("Current Assets"),
        "quarterly_cashflow": quarterly("Operating Cash Flow"),
        "info": {
            "quoteType": "EQUITY",
            "currency": "EUR",
            "financialCurrency": "EUR",
            "sharesOutstanding": 10.0,
        },
        "shares_full": pd.Series([10.0, 10.0], index=pd.to_datetime(["2026-08-01", "2026-10-01"])),
        "earnings_dates": [date(2026, 2, 20), date(2025, 2, 21), date(2024, 2, 22)],
    }
    return ProviderSnapshot(**{**values, **overrides})


def check(result, name):
    return result.result(name)


def run(tgt=None, **overrides):
    return evaluate_instrument(tgt or target(), snapshot(**overrides), AS_OF)


# --- primitives: missing is not zero, NaN is not zero -----------------------


def test_finite_distinguishes_missing_nan_and_zero():
    assert finite(None) is None
    assert finite(NAN) is None
    assert finite(float("inf")) is None
    assert finite("n/a") is None
    assert finite(True) is None
    assert finite(0) == 0.0
    assert finite(-3.5) == -3.5


def test_cell_absent_row_nan_and_zero():
    data = frame({"Row": [NAN, 0.0, 5.0]}, ["2025-12-31", "2024-12-31", "2023-12-31"])
    assert cell(data, ("Absent",), date(2024, 12, 31)) is None
    assert cell(data, ("Row",), date(2025, 12, 31)) is None
    assert cell(data, ("Row",), date(2024, 12, 31)) == 0.0
    assert cell(data, ("Row",), date(2020, 12, 31)) is None
    assert cell(None, ("Row",), date(2024, 12, 31)) is None


def test_period_ends_skip_all_nan_columns_and_sort_newest_first():
    data = frame({"A": [NAN, 1.0, 2.0], "B": [NAN, NAN, 3.0]}, ["2026-12-31", "2025-12-31", "2024-12-31"])
    assert period_ends(data) == [date(2025, 12, 31), date(2024, 12, 31)]
    assert period_ends(None) == []
    assert period_ends(pd.DataFrame()) == []


def test_chains_detect_gaps():
    annual = [date(2025, 12, 31), date(2024, 12, 31), date(2022, 12, 31)]
    assert annual_chain(annual) == 2
    assert annual_chain([]) == 0
    quarters = [date(2026, 6, 30), date(2026, 3, 31), date(2025, 9, 30)]
    assert quarterly_chain(quarters) == 2
    assert quarterly_chain([date(2026, 6, 30)]) == 1


# --- a complete synthetic snapshot ----------------------------------------


def test_complete_snapshot_is_ok_for_every_required_field():
    coverage = run()
    assert tuple(r.check for r in coverage.results) == ALL_CHECKS
    for name in REQUIRED_FIELDS:
        assert check(coverage, name).status is Status.OK, name
    assert check(coverage, "annual_history").status is Status.OK
    assert check(coverage, "scale_consistency").status is Status.OK
    assert check(coverage, "fiscal_year_end").status is Status.OK
    assert "capex_outflow_negative" in check(coverage, "capex_sign").codes
    assert "fcf_identity_holds" in check(coverage, "capex_sign").codes
    assert "lease_row_visible" in check(coverage, "financial_debt").codes


def test_publication_date_is_never_better_than_partial():
    result = check(run(), "publication_date")
    assert result.status is Status.PARTIAL
    assert result.codes == ("proxy_event_dates_only",)
    assert check(run(earnings_dates=None), "publication_date").status is Status.MISSING


# --- missing is never zero ------------------------------------------------


def test_absent_minority_and_preferred_rows_are_missing_not_zero():
    data = balance().drop(["Minority Interest", "Preferred Stock Equity"])
    coverage = run(annual_balance=data)
    for name in ("minority", "preferred"):
        result = check(coverage, name)
        assert result.status is Status.MISSING
        assert result.codes == ("row_absent_not_zero",)


def test_nan_at_latest_balance_date_is_missing_but_reported_zero_is_ok():
    data = balance(**{"Minority Interest": [NAN, 5.0, 5.0, 5.0]})
    assert check(run(annual_balance=data), "minority").codes == ("nan_at_latest",)
    assert check(run(annual_balance=balance()), "preferred").status is Status.OK


def test_ebit_falls_back_to_other_definition_as_partial_only():
    data = income().drop(["Operating Income"]).copy()
    data.loc["EBIT"] = [110.0, 100.0, 90.0, 80.0]
    result = check(run(annual_income=data), "ebit")
    assert result.status is Status.PARTIAL
    assert result.codes == ("definition_differs",)


def test_nonpositive_ebit_stays_covered_but_is_flagged():
    result = check(run(annual_income=income(**{"Operating Income": [-5.0, 90.0, 80.0, 70.0]})), "ebit")
    assert result.status is Status.OK
    assert result.codes == ("ebit_not_positive",)
    assert check(run(annual_income=income(**{"Operating Income": [0.0, 90.0, 80.0, 70.0]})), "ebit").status is Status.OK


def test_flow_field_gaps_and_nan_are_graded():
    data = cashflow(**{"Depreciation And Amortization": [20.0, NAN, 18.0, 17.0]})
    result = check(run(annual_cashflow=data), "da")
    assert result.status is Status.PARTIAL
    assert result.detail.startswith("2/3")
    assert check(run(annual_cashflow=cashflow().drop(["Capital Expenditure", "Purchase Of PPE"], errors="ignore")), "capex").codes == ("row_absent",)


def test_tax_needs_expense_and_pretax_and_is_flagged_effective_only():
    full = check(run(), "tax")
    assert full.status is Status.OK
    assert full.codes == ("tax_is_effective_not_operating",)
    data = income(**{"Tax Provision": [28.0, NAN, 22.0, 19.0]})
    assert check(run(annual_income=data), "tax").status is Status.PARTIAL


def test_nwc_needs_all_four_components_on_four_dates():
    assert check(run(), "nwc").status is Status.OK
    data = balance(**{"Current Debt And Capital Lease Obligation": [20.0, 20.0, NAN, 20.0]})
    result = check(run(annual_balance=data), "nwc")
    assert result.status is Status.PARTIAL
    assert result.codes == ("component_missing",)
    assert "Current Debt And Capital Lease Obligation" in result.detail
    short = balance().iloc[:, :3]
    assert check(run(annual_balance=short), "nwc").status is Status.PARTIAL


def test_fallback_and_mixed_rows_are_marked_not_plain_ok():
    fallback = income().drop(["Operating Income"]).copy()
    fallback.loc["Total Operating Income As Reported"] = [110.0, 100.0, 90.0, 80.0]
    result = check(run(annual_income=fallback), "ebit")
    assert result.status is Status.OK
    assert result.codes == ("fallback_row_used",)
    assert "Total Operating Income As Reported" in result.detail
    mixed = income().copy()
    mixed.loc["Total Operating Income As Reported"] = [110.0, 100.0, 90.0, 80.0]
    mixed.loc["Operating Income", "2023-12-31"] = NAN
    result = check(run(annual_income=mixed), "ebit")
    assert result.status is Status.OK
    assert result.codes == ("fallback_row_used", "mixed_row_labels")
    clean = check(run(), "ebit")
    assert clean.codes == ()
    da = cashflow().rename(index={"Depreciation And Amortization": "Depreciation Amortization Depletion"})
    assert check(run(annual_cashflow=da), "da").codes == ("fallback_row_used",)
    capex = cashflow().rename(index={"Capital Expenditure": "Purchase Of PPE"})
    assert check(run(annual_cashflow=capex), "capex").codes == ("fallback_row_used",)


def test_fallback_rows_are_marked_for_balance_fields_and_counted_in_the_aggregate():
    narrow = balance().rename(
        index={"Cash Cash Equivalents And Short Term Investments": "Cash And Cash Equivalents"}
    )
    coverage = run(annual_balance=narrow)
    assert check(coverage, "cash").codes == ("fallback_row_used",)
    assert "fallback_row_used" in check(coverage, "nwc").codes
    assert check(coverage, "nwc").status is Status.OK
    other_preferred = balance().rename(index={"Preferred Stock Equity": "Preferred Stock"})
    assert check(run(annual_balance=other_preferred), "preferred").codes == ("fallback_row_used",)
    summary = aggregate([coverage, run()])
    assert summary["codes"][("cash", "fallback_row_used")] == 1
    assert summary["per_check"]["cash"] == {"ok": 2}


def test_financial_debt_sum_identity_is_checked_or_reported_unchecked():
    assert "debt_identity_holds" in check(run(), "financial_debt").codes
    broken = balance(**{"Total Debt": [260.0, 190.0, 180.0, 170.0]})
    result = check(run(annual_balance=broken), "financial_debt")
    assert result.status is Status.PROBLEM
    assert "debt_identity_failed" in result.codes
    missing = balance().drop(["Long Term Debt And Capital Lease Obligation"])
    result = check(run(annual_balance=missing), "financial_debt")
    assert result.status is Status.OK
    assert "debt_identity_not_checked" in result.codes
    assert "debt_identity_holds" not in result.codes


# --- units, signs, currencies and periods -----------------------------------


def test_capex_sign_conventions_and_identity():
    positive = cashflow(**{"Capital Expenditure": [40.0, 38.0, 36.0, 34.0]})
    result = check(run(annual_cashflow=positive), "capex_sign")
    assert "capex_outflow_positive" in result.codes
    mixed = cashflow(**{"Capital Expenditure": [-40.0, 38.0, -36.0, -34.0]})
    assert check(run(annual_cashflow=mixed), "capex_sign").codes == ("capex_sign_mixed",)
    broken = cashflow(**{"Free Cash Flow": [10.0, 72.0, 64.0, 56.0]})
    bad = check(run(annual_cashflow=broken), "capex_sign")
    assert bad.status is Status.PROBLEM
    assert "fcf_identity_failed" in bad.codes
    zero = cashflow(**{"Capital Expenditure": [0.0, 0.0, 0.0, 0.0]})
    assert check(run(annual_cashflow=zero), "capex_sign").status is Status.PARTIAL


def test_capex_sign_is_not_ok_without_a_passed_fcf_control():
    result = check(run(), "capex_sign")
    assert result.status is Status.OK
    assert "3/3 years" in result.detail
    no_fcf = cashflow().drop(["Free Cash Flow"])
    unchecked = check(run(annual_cashflow=no_fcf), "capex_sign")
    assert unchecked.status is Status.PARTIAL
    assert unchecked.codes == ("capex_outflow_negative", "fcf_identity_not_checked")
    other_label = cashflow().rename(index={"Capital Expenditure": "Purchase Of PPE"})
    assert check(run(annual_cashflow=other_label), "capex_sign").status is Status.PARTIAL


def test_fcf_identity_is_checked_in_every_year_not_only_the_first():
    late_break = cashflow(**{"Free Cash Flow": [80.0, 72.0, 10.0, 56.0]})
    result = check(run(annual_cashflow=late_break), "capex_sign")
    assert result.status is Status.PROBLEM
    assert "fcf_identity_failed" in result.codes
    one_year = cashflow(**{"Free Cash Flow": [80.0, NAN, NAN, NAN]})
    partly = check(run(annual_cashflow=one_year), "capex_sign")
    assert partly.status is Status.OK
    assert "1/3 years" in partly.detail


def test_missing_capex_with_fcf_equal_to_operating_cash_flow_is_exposed():
    dropped = cashflow(**{"Capital Expenditure": [NAN] * 4, "Free Cash Flow": [120.0, 110.0, 100.0, 90.0]})
    coverage = run(annual_cashflow=dropped)
    assert check(coverage, "capex").status is Status.MISSING
    assert check(coverage, "capex_sign").codes == ("no_capex", "fcf_equals_operating_cash_flow")
    genuine = cashflow(**{"Capital Expenditure": [NAN] * 4})
    assert check(run(annual_cashflow=genuine), "capex_sign").codes == ("no_capex",)


def test_reporting_currency_checks():
    other = {"quoteType": "EQUITY", "currency": "AAA", "financialCurrency": "BBB"}
    cross = target(trading_currency="AAA", expected_reporting_currency="BBB")
    result = check(run(cross, info=other), "reporting_currency")
    assert result.status is Status.OK
    assert result.codes == ("cross_currency",)
    wrong = check(run(target(), info={**other, "currency": "EUR"}), "reporting_currency")
    assert wrong.status is Status.PROBLEM
    assert check(run(info={"quoteType": "EQUITY", "currency": "EUR"}), "reporting_currency").status is Status.MISSING


def test_identity_flags_non_equity_currency_and_unit_tokens():
    base = {"quoteType": "EQUITY", "currency": "EUR"}
    assert check(run(info=base), "identity").status is Status.OK
    bad = check(run(info={"quoteType": "ETF", "currency": "GBp"}), "identity")
    assert bad.status is Status.PROBLEM
    assert set(bad.codes) == {
        "quote_type_not_equity",
        "trading_currency_mismatch",
        "sub_unit_quote_token",
    }
    assert check(run(info=None), "identity").status is Status.MISSING
    stub = check(run(info={"trailingPegRatio": None}), "identity")
    assert stub.status is Status.MISSING
    assert stub.codes == ("info_stub",)


def test_scale_consistency_catches_share_scale_errors():
    thousands = income(**{"Diluted Average Shares": [10000.0, 10000.0, 10000.0, 10000.0]})
    result = check(run(annual_income=thousands), "scale_consistency")
    assert result.status is Status.PROBLEM
    assert result.codes == ("eps_identity_failed",)
    no_eps = income().drop(["Diluted EPS"])
    assert check(run(annual_income=no_eps), "scale_consistency").status is Status.MISSING


def test_fiscal_year_end_tolerates_52_53_week_calendars_but_flags_other_months():
    shifted = income().copy()
    shifted.columns = ["2025-12-28", "2024-12-29", "2023-12-31", "2022-12-31"]
    assert check(run(annual_income=shifted), "fiscal_year_end").status is Status.OK
    result = check(run(target(fiscal_year_end="06-30")), "fiscal_year_end")
    assert result.status is Status.PROBLEM
    assert result.codes == ("fiscal_year_end_mismatch",)


def test_freshness_uses_explicit_reference_date():
    assert check(run(), "freshness").status is Status.OK
    late = evaluate_instrument(target(), snapshot(), date(2027, 12, 31))
    assert check(late, "freshness").codes[0] == "stale_annual_report"
    early = evaluate_instrument(target(), snapshot(), date(2025, 1, 1))
    assert check(early, "freshness").codes == ("future_period",)
    assert "newer_interim_available" in check(run(), "freshness").codes


def test_history_counts_and_gaps():
    result = check(run(), "annual_history")
    assert result.status is Status.OK
    gappy = income().drop(columns=["2024-12-31"])
    partial = check(run(annual_income=gappy), "annual_history")
    assert partial.status is Status.PARTIAL
    assert "gap_income" in partial.codes
    assert check(run(quarterly_income=None), "quarterly_history").status is Status.PARTIAL
    nothing = run(
        annual_income=None,
        annual_balance=None,
        annual_cashflow=None,
        quarterly_income=None,
        quarterly_balance=None,
        quarterly_cashflow=None,
    )
    assert check(nothing, "annual_history").status is Status.MISSING
    assert check(nothing, "quarterly_history").status is Status.MISSING


# --- shares ---------------------------------------------------------------


def test_share_count_dating_conflict_and_fallbacks():
    assert check(run(), "shares").status is Status.OK
    undated = run(shares_full=None)
    assert check(undated, "shares").codes == ("shares_date_unknown",)
    stale = pd.Series([10.0], index=pd.to_datetime(["2026-01-01"]))
    assert check(run(shares_full=stale), "shares").codes == ("shares_date_stale",)
    unsorted = pd.Series([10.0, 10.0], index=pd.to_datetime(["2026-10-01", "2026-08-01"]))
    assert check(run(shares_full=unsorted), "shares").detail == "dated count, 28 days old"
    conflict = check(run(info={**snapshot().info, "sharesOutstanding": 12.0}), "shares")
    assert conflict.status is Status.PROBLEM
    assert conflict.codes == ("share_sources_conflict",)
    only_balance = run(info={"quoteType": "EQUITY", "currency": "EUR"}, shares_full=None)
    assert check(only_balance, "shares").codes == ("only_balance_date_shares",)
    zero = run(
        info={"quoteType": "EQUITY", "currency": "EUR", "sharesOutstanding": 0},
        shares_full=None,
        annual_balance=balance().drop(["Ordinary Shares Number"]),
    )
    assert check(zero, "shares").status is Status.MISSING


def test_failed_datasets_are_reported_not_zero():
    coverage = run(annual_cashflow=None, failed_datasets=("annual_cashflow",))
    for name in ("da", "capex"):
        assert check(coverage, name).codes == ("fetch_failed",)
    assert check(coverage, "annual_history").codes == ("fetch_failed",)


# --- aggregation, rendering and privacy -------------------------------------


def test_aggregate_and_public_markdown_contain_counts_only():
    good = run()
    bad = run(
        target(case_id="F-02", symbol="SYN2.XX", label="Other Synthetic"),
        annual_balance=balance().drop(["Minority Interest"]),
    )
    summary = aggregate([good, bad])
    assert summary["cases"] == 2
    assert summary["per_check"]["minority"] == {"ok": 1, "missing": 1}
    assert summary["codes"][("minority", "row_absent_not_zero")] == 1
    text = render_aggregate_markdown(summary)
    assert "row_absent_not_zero" in text
    for private in ("SYN1.XX", "SYN2.XX", "Synthetic Co", "Other Synthetic", "F-01"):
        assert private not in text


def test_instrument_markdown_is_the_private_view_and_repr_hides_identifiers():
    coverage = run()
    assert "Synthetic Co" in render_instrument_markdown(coverage)
    assert "SYN1.XX" not in repr(coverage.target)
    assert "Synthetic Co" not in repr(coverage.target)


def test_key_values_keep_missing_as_none():
    values = key_values(snapshot(annual_cashflow=cashflow().drop(["Capital Expenditure"])))
    assert values["revenue"] == 1000.0
    assert values["capex"] is None
    assert key_values(ProviderSnapshot()) == {}


# --- universe loading ----------------------------------------------------------


def entry(**changes) -> dict:
    values = {
        "name": "Synthetic Co",
        "venue": "XETRA",
        "yahoo_symbol": "SYN1.XX",
        "trading_currency": "EUR",
        "quote_unit_scale": 1,
        "expected_reporting_currency": "EUR",
        "fiscal_year_end": "12-31",
    }
    return {**values, **changes}


def test_load_universe_requires_approval_and_valid_entries():
    approved = {"live_test_approved": True, "instruments": [entry(), entry(venue="XYZX", trading_currency="AAA", expected_reporting_currency="BBB")]}
    targets = load_universe(approved)
    assert [t.case_id for t in targets] == ["F-01", "F-02"]
    assert targets[0].market_class == "Xetra EUR"
    assert targets[1].market_class == "sonstige Börse AAA (Berichtswährung BBB)"
    assert "SYN1.XX" not in repr(targets)
    with pytest.raises(ValueError, match="not approved"):
        load_universe({**approved, "live_test_approved": False})
    with pytest.raises(ValueError, match="no instruments"):
        load_universe({"live_test_approved": True, "instruments": []})
    with pytest.raises(ValueError, match="yahoo_symbol"):
        load_universe({"live_test_approved": True, "instruments": [entry(yahoo_symbol=" ")]})
    with pytest.raises(ValueError, match="quote_unit_scale"):
        load_universe({"live_test_approved": True, "instruments": [entry(quote_unit_scale=100)]})


def test_market_class_for_unknown_venue():
    assert market_class(entry(venue="LSE", trading_currency="GBP", expected_reporting_currency="GBP")) == "sonstige Börse GBP"


# --- the manual runner stays out of the app and out of CI -------------------

ROOT = Path(__file__).resolve().parents[1]


def load_runner():
    spec = importlib.util.spec_from_file_location("coverage_probe", ROOT / "scripts" / "fundamentals_coverage_probe.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["coverage_probe"] = module
    spec.loader.exec_module(module)
    return module


def test_runner_import_has_no_provider_access_and_refuses_unsafe_calls(tmp_path, monkeypatch):
    module = load_runner()  # Importing must not import yfinance or touch the network.
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"live_test_approved": True, "instruments": [entry()]}))

    def run_with(*args):
        monkeypatch.setattr(sys, "argv", ["probe", *args])
        return module.main()

    with pytest.raises(SystemExit) as missing_flag:
        run_with(str(universe), "--output", str(tmp_path / "private" / "r.md"))
    assert missing_flag.value.code == 2
    with pytest.raises(SystemExit) as public_output:
        run_with(str(universe), "--confirm-live", "--output", str(tmp_path / "public.md"))
    assert public_output.value.code == 2
    assert not (tmp_path / "public.md").exists()


def test_app_and_package_never_import_the_probe_or_coverage_module():
    importers = [
        path.name
        for path in [ROOT / "app.py", *(ROOT / "src" / "portfolio_dashboard").glob("*.py")]
        if "fundamentals_coverage" in path.read_text(encoding="utf-8")
        and path.name != "fundamentals_coverage.py"
    ]
    assert importers == []
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "fundamentals_coverage_probe" not in workflow


def test_runner_reuses_private_snapshots_instead_of_refetching(tmp_path, monkeypatch):
    module = load_runner()
    calls = []

    def fake_fetch(symbol, pause):
        calls.append(symbol)
        return snapshot()

    monkeypatch.setattr(module, "fetch_snapshot", fake_fetch)
    directory = tmp_path / "private" / "snapshots"
    first = module.load_or_fetch(target(), directory, 0)
    second = module.load_or_fetch(target(), directory, 0)
    assert calls == ["SYN1.XX"]
    assert second.info == first.info
    assert module.load_or_fetch(target(), None, 0) is not None
    assert calls == ["SYN1.XX", "SYN1.XX"]


def test_runner_refuses_public_snapshot_directory(tmp_path, monkeypatch):
    module = load_runner()
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"live_test_approved": True, "instruments": [entry()]}))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "probe",
            str(universe),
            "--confirm-live",
            "--output",
            str(tmp_path / "private" / "r.md"),
            "--snapshot-dir",
            str(tmp_path / "public"),
        ],
    )
    with pytest.raises(SystemExit) as refused:
        module.main()
    assert refused.value.code == 2


def test_runner_refetches_only_a_stubbed_info_and_keeps_statements(tmp_path, monkeypatch):
    module = load_runner()
    directory = tmp_path / "private" / "snapshots"
    stubbed = snapshot(info={"trailingPegRatio": None})
    directory.mkdir(parents=True)
    with (directory / "F-01.pickle").open("wb") as handle:
        module.pickle.dump(stubbed, handle)

    class FakeTicker:
        info = {"quoteType": "EQUITY", "currency": "EUR"}

    import types

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=lambda symbol: FakeTicker()))
    monkeypatch.setattr(module, "fetch_snapshot", lambda *a: pytest.fail("statements refetched"))
    repaired = module.load_or_fetch(target(), directory, 0)
    assert repaired.info["quoteType"] == "EQUITY"
    assert repaired.annual_income is stubbed.annual_income or repaired.annual_income.equals(stubbed.annual_income)


def test_private_location_must_be_a_directory_named_private(tmp_path):
    module = load_runner()
    assert module.inside_private_dir(tmp_path / "private" / "r.md")
    assert module.inside_private_dir(tmp_path / "docs" / "private" / "snapshots")
    assert module.inside_private_dir(tmp_path / "private")
    assert not module.inside_private_dir(tmp_path / "public" / "r.md")
    (tmp_path / "private").write_text("a file, not a directory")
    assert not module.inside_private_dir(tmp_path / "private")
    assert not module.inside_private_dir(tmp_path / "private" / "r.md")


def test_runner_refuses_output_named_private_when_it_is_a_file(tmp_path, monkeypatch):
    module = load_runner()
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"live_test_approved": True, "instruments": [entry()]}))
    (tmp_path / "private").write_text("a file, not a directory")
    monkeypatch.setattr(
        sys, "argv", ["probe", str(universe), "--confirm-live", "--output", str(tmp_path / "private")]
    )
    with pytest.raises(SystemExit) as refused:
        module.main()
    assert refused.value.code == 2


def test_importing_coverage_module_and_runner_does_not_import_yfinance_or_network():
    probe = str(ROOT / "scripts" / "fundamentals_coverage_probe.py")
    code = f"""
import importlib.abc
import importlib.util
import socket
import sys
class Boundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {{'streamlit', 'yfinance', 'requests', 'gspread'}}:
            raise AssertionError(f'Unexpected dependency: {{fullname}}')
sys.meta_path.insert(0, Boundary())
def no_network(*args, **kwargs):
    raise AssertionError('Import attempted a network connection')
socket.socket = no_network
socket.create_connection = no_network
import portfolio_dashboard.fundamentals_coverage
spec = importlib.util.spec_from_file_location('probe', {probe!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert 'yfinance' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_no_app_or_package_module_imports_the_probe_script():
    sources = [ROOT / "app.py", *(ROOT / "src" / "portfolio_dashboard").glob("*.py")]
    imported: set[str] = set()
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    assert not [name for name in imported if "fundamentals_coverage_probe" in name]
