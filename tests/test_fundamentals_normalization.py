"""Offline tests with entirely invented statements; no provider response is used."""

import subprocess
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

import portfolio_dashboard.fundamentals_coverage as coverage
import portfolio_dashboard.fundamentals_normalization as norm
from portfolio_dashboard.fundamentals import (
    CapitalStructure,
    CurrentSharesPeriod,
    EvidenceKind,
    FieldProvenance,
    FlowPeriod,
    FundamentalField,
    FundamentalObservation,
    FundamentalSource,
    InstantPeriod,
    IssuerKind,
    ManualAdjustment,
    PeriodKind,
    QualityStatus,
    VerifiedFundamentalIdentity,
)
from portfolio_dashboard.fundamentals_normalization import (
    FIELD_CATALOG,
    MANUAL_FIELDS,
    FetchFailure,
    OwnerConfirmations,
    ProviderExpectation,
    ProviderStatements,
    StatementTable,
    build_report_set,
    normalize_statements,
    trailing_twelve_months,
)
from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    QuoteUnit,
    ValueStatus,
)

D = Decimal
RETRIEVED = datetime(2025, 3, 15, 10, 0, tzinfo=timezone.utc)
VALUATION = datetime(2025, 3, 20, tzinfo=timezone.utc)
FY_ENDS = [date(y, 12, 31) for y in (2024, 2023, 2022, 2021)]
BALANCE_ENDS = FY_ENDS + [date(2020, 12, 31)]
SHARES_DATE = date(2025, 3, 1)
INFO = {
    "quoteType": "EQUITY",
    "currency": "EUR",
    "financialCurrency": "EUR",
    "sharesOutstanding": 10_000_000,
    "impliedSharesOutstanding": None,
}
EXPECTATION = ProviderExpectation("EUR", 12, 31)


def table(rows: dict[str, list], ends=None) -> StatementTable:
    """rows: label -> values aligned with ``ends`` (newest first); None = missing."""
    ends = ends or FY_ENDS
    return StatementTable(
        {
            label: {
                end: None if value is None else D(str(value))
                for end, value in zip(ends, values)
            }
            for label, values in rows.items()
        }
    )


def income(**overrides) -> StatementTable:
    rows = {
        "Operating Income": [150e6, 140e6, 130e6, 120e6],
        "EBIT": [151e6, 141e6, 131e6, 121e6],
        "Pretax Income": [140e6, 130e6, 120e6, 110e6],
        "Tax Provision": [28e6, 26e6, 24e6, 22e6],
        "Net Income Common Stockholders": [100e6, 95e6, 90e6, 85e6],
        "Diluted Average Shares": [10e6] * 4,
        "Diluted EPS": [10.0, 9.5, 9.0, 8.5],
    }
    rows.update(overrides)
    return table(rows)


def cashflow(**overrides) -> StatementTable:
    rows = {
        "Depreciation And Amortization": [20e6] * 4,
        "Capital Expenditure": [-44e6] * 4,
        "Operating Cash Flow": [100e6] * 4,
        "Free Cash Flow": [56e6] * 4,
    }
    rows.update(overrides)
    return table(rows)


def balance(**overrides) -> StatementTable:
    rows = {
        "Current Assets": [300e6, 290e6, 280e6, 270e6, 260e6],
        "Current Liabilities": [200e6, 195e6, 190e6, 185e6, 180e6],
        "Cash Cash Equivalents And Short Term Investments": [100e6] * 5,
        "Current Debt And Capital Lease Obligation": [50e6] * 5,
        "Total Debt": [120e6, 118e6, 116e6, 114e6, 112e6],
        "Capital Lease Obligations": [10e6] * 5,
        "Minority Interest": [5e6, None, None, None, None],
        "Ordinary Shares Number": [10e6] * 5,
    }
    rows.update(overrides)
    return table(rows, BALANCE_ENDS)


def statements(**overrides) -> ProviderStatements:
    values = {
        "retrieved_at": RETRIEVED,
        "annual_income": income(),
        "annual_balance": balance(),
        "annual_cashflow": cashflow(),
        "info": dict(INFO),
        "dated_shares": ((SHARES_DATE, D(10_000_000)),),
    }
    values.update(overrides)
    return ProviderStatements(**values)


def make_identity(currency="EUR", scale=D(1)) -> VerifiedFundamentalIdentity:
    return VerifiedFundamentalIdentity(
        VerifiedListing(
            Instrument(
                InstrumentType.STOCK,
                instrument_id="synthetic-share",
                trading_venue="synthetic-venue",
                quote_unit=QuoteUnit(currency, scale),
            ),
            equity_form=EquityForm.SHARE,
        ),
        "synthetic-class",
        "synthetic-issuer",
        FundamentalSource("yfinance", "SYN.XX"),
        "synthetic-group",
        IssuerKind.NONFINANCIAL,
        "synthetic-identity-proof",
        RETRIEVED,
    )


IDENTITY = make_identity()


def run(expectation=EXPECTATION, **overrides):
    return normalize_statements(IDENTITY, statements(**overrides), expectation)


def by_field(result, field):
    return [o for o in result.observations if o.field is field]


def value_at(result, field, period):
    (obs,) = [o for o in result.observations if o.field is field and o.period == period]
    return obs


def codes(result):
    return {i.code for i in result.issues}


def test_complete_synthetic_year_normalizes_to_the_contract_fields():
    result = run()
    assert not result.numbers_withheld and result.currency == "EUR"
    assert not result.issues
    # Every balance date is complete, so all four fiscal years remain.
    ebit = by_field(result, FundamentalField.EBIT)
    assert [o.period.end for o in ebit] == FY_ENDS
    assert all(o.status is ValueStatus.AVAILABLE for o in ebit)
    assert ebit[0].period == FlowPeriod(
        date(2024, 1, 1),
        date(2024, 12, 31),
        PeriodKind.FISCAL_YEAR,
        "FY ending 2024-12-31",
        True,
    )
    assert ebit[0].value == D(150_000_000) and ebit[0].unit.currency == "EUR"
    assert ebit[0].provenance.field_reference == "Operating Income"
    assert ebit[0].provenance.retrieved_at == RETRIEVED
    assert ebit[0].provenance.published_at is None  # Never derived from the fetch.
    assert result.report.revision == norm.REVISION_UNKNOWN
    assert result.bridge_date == date(2024, 12, 31)


def test_capex_sign_is_reversed_and_provider_fcf_stays_a_control():
    result = run()
    capex = by_field(result, FundamentalField.CAPEX)[0]
    assert capex.value == D(44_000_000)  # Provider outflow -44 -> deduction +44.
    assert "sign reversed" in capex.provenance.normalization
    fcf = by_field(result, FundamentalField.PROVIDER_FCF)
    assert fcf and all("not FCFF" in o.provenance.normalization for o in fcf)
    # No FCFF anywhere: the contract has no such field.
    assert all(o.field.value != "fcff" for o in result.observations)


def test_working_capital_per_balance_date_excludes_cash_and_current_debt():
    result = run()
    nwc = {
        o.period.as_of: o.value
        for o in by_field(result, FundamentalField.OPERATING_NWC)
    }
    # (300-100) - (200-50) = 50 at the newest date; five instants for four years.
    assert nwc[date(2024, 12, 31)] == D(50_000_000)
    assert nwc[date(2023, 12, 31)] == D(290_000_000 - 100_000_000 - 145_000_000)
    assert set(nwc) == set(BALANCE_ENDS)


def test_missing_is_never_zero_and_gaps_stay_visible():
    result = run(
        annual_cashflow=cashflow(**{"Capital Expenditure": [None] * 4}),
        annual_balance=balance(**{"Minority Interest": [None] * 5}),
    )
    capex = by_field(result, FundamentalField.CAPEX)
    assert all(o.status is ValueStatus.MISSING and o.value is None for o in capex)
    assert "provider_fcf_equals_operating_cash_flow" not in result.notes  # 100 != 56
    minority = by_field(result, FundamentalField.MINORITY_CLAIMS)
    assert len(minority) == 1 and minority[0].value is None
    assert not any(
        s.field is FundamentalField.MINORITY_CLAIMS for s in result.suggestions
    )
    cash = cashflow(
        **{"Capital Expenditure": [None] * 4, "Free Cash Flow": [100e6] * 4}
    )
    assert "provider_fcf_equals_operating_cash_flow" in run(annual_cashflow=cash).notes


def test_explicit_zero_is_a_delivered_value():
    result = run(
        annual_balance=balance(
            **{
                "Total Debt": [0.0, 0, 0, 0, 0],
                "Current Debt And Capital Lease Obligation": [0.0] * 5,
            }
        ),
        annual_cashflow=cashflow(**{"Capital Expenditure": [0.0] * 4}),
    )
    debt = by_field(result, FundamentalField.FINANCIAL_DEBT)[0]
    assert debt.status is ValueStatus.AVAILABLE and debt.value == 0
    capex = by_field(result, FundamentalField.CAPEX)
    assert all(o.status is ValueStatus.AVAILABLE and o.value == 0 for o in capex)
    assert "capex_zero_reported" in result.notes


def test_manual_fields_are_missing_observations_with_labelled_suggestions():
    result = run()
    manual = result.manual_required
    assert {o.field for o in manual} == MANUAL_FIELDS
    assert all(o.value is None for o in manual)
    assert all(o.provenance.field_reference == norm.NOT_SUPPLIED for o in manual)
    tax = [o for o in manual if o.field is FundamentalField.OPERATING_TAX_RATE]
    assert len(tax) == 4
    rates = {
        s.period.end: s.value
        for s in result.suggestions
        if s.field is FundamentalField.OPERATING_TAX_RATE
    }
    assert rates[date(2024, 12, 31)] == D(28) / D(140)  # Effective, not operating.
    assert all(
        s.basis.startswith("effective_rate")
        for s in result.suggestions
        if s.field is FundamentalField.OPERATING_TAX_RATE
    )
    gross = [s for s in result.suggestions if s.field is FundamentalField.EXCESS_CASH]
    assert gross[0].value == D(100_000_000) and "upper_bound" in gross[0].basis
    minority = [
        s for s in result.suggestions if s.field is FundamentalField.MINORITY_CLAIMS
    ]
    assert minority[0].basis == "provider_book_value"


def test_fixed_row_preference_uses_one_row_for_the_whole_series():
    # The preferred row lacks one year; the fallback is complete: use it for all.
    result = run(
        annual_income=income(
            **{
                "Operating Income": [150e6, None, 130e6, 120e6],
                "Total Operating Income As Reported": [149e6, 139e6, 129e6, 119e6],
            }
        )
    )
    ebit = by_field(result, FundamentalField.EBIT)
    assert [o.value for o in ebit] == [D(v) for v in (149e6, 139e6, 129e6, 119e6)]
    assert all(o.provenance.field_reference.startswith("Total") for o in ebit)
    assert all("fallback row" in o.provenance.normalization for o in ebit)
    assert "fallback_row_operating_income" in result.notes
    # Preferred row complete -> preferred row, no fallback note.
    assert "fallback_row_operating_income" not in run().notes


def test_alternative_ebit_row_is_only_a_control_with_a_definition_note():
    assert "ebit_control_definition_differs" not in run().notes  # 0.7% apart.
    same = run(annual_income=income(EBIT=[150e6, 140e6, 130e6, 120e6]))
    assert "ebit_control_definition_differs" not in same.notes
    far = run(annual_income=income(EBIT=[160e6, 140e6, 130e6, 120e6]))
    assert "ebit_control_definition_differs" in far.notes
    only_ebit_row = income()
    rows = dict(only_ebit_row.rows)
    del rows["Operating Income"]
    result = run(annual_income=StatementTable(rows))
    assert all(
        o.status is ValueStatus.MISSING for o in by_field(result, FundamentalField.EBIT)
    )


def test_invalid_signs_become_invalid_observations_not_exceptions():
    positive_capex = run(
        annual_cashflow=cashflow(**{"Capital Expenditure": [44e6] * 4})
    )
    capex = by_field(positive_capex, FundamentalField.CAPEX)
    assert all(o.status is ValueStatus.INVALID and o.value is None for o in capex)
    assert "capex_sign_unexpected" in positive_capex.notes
    negative_da = run(
        annual_cashflow=cashflow(**{"Depreciation And Amortization": [-20e6] * 4})
    )
    assert all(
        o.status is ValueStatus.INVALID
        for o in by_field(negative_da, FundamentalField.DEPRECIATION_AMORTIZATION)
    )
    low_debt = run(annual_balance=balance(**{"Total Debt": [10e6] * 5}))
    debt = by_field(low_debt, FundamentalField.FINANCIAL_DEBT)[0]
    assert debt.status is ValueStatus.INVALID
    assert "financial_debt_below_current_debt" in low_debt.notes


def test_negative_ebit_is_kept_for_the_calculator_to_judge():
    result = run(
        annual_income=income(**{"Operating Income": [-5e6, 140e6, 130e6, 120e6]})
    )
    first = by_field(result, FundamentalField.EBIT)[0]
    assert first.status is ValueStatus.AVAILABLE and first.value == D(-5_000_000)


@pytest.mark.parametrize(
    ("info", "code"),
    [
        ({**INFO, "financialCurrency": "USD"}, "reporting_currency_conflict"),
        ({**INFO, "financialCurrency": None}, "reporting_currency_unverified"),
        ({**INFO, "currency": "USD"}, "trading_currency_conflict"),
        ({**INFO, "currency": "GBp"}, "trading_currency_conflict"),
        ({**INFO, "quoteType": "ETF"}, "quote_type_conflict"),
        ({"trailingPegRatio": None}, "info_metadata_placeholder"),
        (None, "reporting_currency_unverified"),
    ],
)
def test_currency_and_identity_conflicts_withhold_every_number(info, code):
    result = run(info=info)
    assert result.numbers_withheld and result.observations == ()
    assert result.currency is None  # No EUR or other default.
    assert result.suggestions == ()
    assert code in codes(result)
    capital = CapitalStructure(
        "synthetic-issuer",
        IDENTITY.source,
        SHARES_DATE,
        1,
        True,
        "synthetic-proof",
        RETRIEVED,
    )
    assert build_report_set(result, capital, "v1") is None


def test_reporting_currency_is_separate_from_trading_currency():
    identity = make_identity("HKD")
    info = {**INFO, "currency": "HKD", "financialCurrency": "CNY"}
    result = normalize_statements(
        identity, statements(info=info), ProviderExpectation("CNY", 12, 31)
    )
    assert not result.numbers_withheld and result.currency == "CNY"
    assert "reporting_currency_differs_from_trading_currency" in result.notes
    assert by_field(result, FundamentalField.EBIT)[0].unit.currency == "CNY"
    # The same answer against the trading currency as expectation is a conflict.
    wrong = normalize_statements(
        identity, statements(info=info), ProviderExpectation("HKD", 12, 31)
    )
    assert "reporting_currency_conflict" in codes(wrong)


def test_minor_unit_trading_token_matches_only_the_declared_scale():
    identity = make_identity("GBP", D("0.01"))
    info = {**INFO, "currency": "GBp", "financialCurrency": "GBP"}
    ok = normalize_statements(
        identity, statements(info=info), ProviderExpectation("GBP", 12, 31)
    )
    assert not ok.numbers_withheld
    bad = normalize_statements(
        identity,
        statements(info={**info, "currency": "GBP"}),
        ProviderExpectation("GBP", 12, 31),
    )
    assert "trading_currency_conflict" in codes(bad)


def test_scale_conflict_is_detected_through_eps_and_withholds_numbers():
    result = run(annual_income=income(**{"Diluted EPS": [10_000.0, 9.5, 9.0, 8.5]}))
    assert result.numbers_withheld and "scale_inconsistent" in codes(result)
    unverifiable = run(
        annual_income=income(**{"Diluted EPS": [None] * 4}),
    )
    assert not unverifiable.numbers_withheld
    assert "scale_unverified" in unverifiable.notes


def test_fetch_failures_become_sanitized_codes_only():
    failures = (
        FetchFailure("annual_balance", "timeout"),
        FetchFailure("annual_cashflow", "access_restricted"),
        FetchFailure("shares_full", "invalid_values"),
    )
    result = run(annual_balance=None, annual_cashflow=None, failures=failures)
    assert {
        "annual_balance_timeout",
        "annual_cashflow_access_restricted",
        "shares_full_invalid_values",
    } <= codes(result)
    status = {i.code: i.status for i in result.issues}
    assert status["annual_balance_timeout"] is QualityStatus.MISSING
    assert status["shares_full_invalid_values"] is QualityStatus.INVALID
    # Without the balance sheet nothing is invented.
    assert all(
        o.status is ValueStatus.MISSING
        for o in by_field(result, FundamentalField.OPERATING_NWC)
    ) or not by_field(result, FundamentalField.OPERATING_NWC)
    with pytest.raises(ValueError):
        FetchFailure("annual_income", "Traceback: secret message")


def test_empty_columns_are_discarded_and_dates_use_tolerance():
    # An all-missing column does not count as a period.
    sparse = dict(income().rows)
    sparse = {k: {**v, date(2020, 12, 31): None} for k, v in sparse.items()}
    result = run(annual_income=StatementTable(sparse))
    assert [o.period.end for o in by_field(result, FundamentalField.EBIT)] == FY_ENDS
    # Cash flow columns a few days off still match the fiscal year end.
    shifted = StatementTable(
        {
            label: {e + timedelta(days=3): v for e, v in cells.items()}
            for label, cells in cashflow().rows.items()
        }
    )
    aligned = run(annual_cashflow=shifted)
    assert all(
        o.status is ValueStatus.AVAILABLE
        for o in by_field(aligned, FundamentalField.CAPEX)
    )
    assert "period_end_aligned" in aligned.notes
    far = StatementTable(
        {
            label: {e + timedelta(days=30): v for e, v in cells.items()}
            for label, cells in cashflow().rows.items()
        }
    )
    assert all(
        o.status is ValueStatus.MISSING
        for o in by_field(run(annual_cashflow=far), FundamentalField.CAPEX)
    )


def test_fiscal_year_end_must_match_the_confirmed_one():
    result = run(expectation=ProviderExpectation("EUR", 6, 30))
    assert "fiscal_year_end_mismatch" in codes(result)
    assert not by_field(result, FundamentalField.EBIT)
    near = ProviderExpectation("EUR", 1, 5)  # 5 days from 31 December: tolerated.
    assert by_field(run(expectation=near), FundamentalField.EBIT)


def test_non_annual_columns_do_not_become_fiscal_years():
    halves = [
        date(2024, 12, 31),
        date(2024, 6, 30),
        date(2023, 12, 31),
        date(2023, 6, 30),
    ]
    rows = {label: list(cells.values()) for label, cells in income().rows.items()}
    result = run(annual_income=table(rows, halves))
    ebit = by_field(result, FundamentalField.EBIT)
    # Only the newest year is a clean annual chain; half-year ends break it.
    assert [o.period.end for o in ebit] == [date(2024, 12, 31)]
    assert "fiscal_year_end_mismatch" in codes(result)
    # A skipped year also ends the chain, without a blocking dataset issue.
    skipped = [
        date(2024, 12, 31),
        date(2022, 12, 31),
        date(2021, 12, 31),
        date(2020, 12, 31),
    ]
    gap = run(annual_income=table(rows, skipped))
    assert [o.period.end for o in by_field(gap, FundamentalField.EBIT)] == [
        date(2024, 12, 31)
    ]
    assert "annual_history_gap" in gap.notes


def test_oldest_year_needs_an_evidenced_start():
    # Four income columns but no older balance date: the oldest year has no start.
    short = StatementTable(
        {
            k: {e: v for e, v in c.items() if e != date(2020, 12, 31)}
            for k, c in balance().rows.items()
        }
    )
    result = run(annual_balance=short)
    assert [o.period.end for o in by_field(result, FundamentalField.EBIT)] == FY_ENDS[
        :3
    ]
    assert "oldest_period_start_unknown" in result.notes


def test_oldest_year_without_opening_nwc_is_dropped_not_half_filled():
    rows = {
        label: [
            None if i == 4 and label != "Ordinary Shares Number" else v
            for i, v in enumerate(cells.values())
        ]
        for label, cells in balance().rows.items()
    }
    result = run(annual_balance=table(rows, BALANCE_ENDS))
    assert [o.period.end for o in by_field(result, FundamentalField.EBIT)] == FY_ENDS[
        :3
    ]
    assert "oldest_period_dropped_nwc_gap" in result.notes
    nwc = by_field(result, FundamentalField.OPERATING_NWC)
    assert len(nwc) == 4 and all(o.status is ValueStatus.AVAILABLE for o in nwc)


def test_missing_nwc_component_voids_only_that_balance_date():
    rows = dict(balance().rows)
    rows["Current Liabilities"] = {
        e: (None if e == date(2022, 12, 31) else v)
        for e, v in rows["Current Liabilities"].items()
    }
    result = run(annual_balance=StatementTable(rows))
    gap = value_at(
        result, FundamentalField.OPERATING_NWC, InstantPeriod(date(2022, 12, 31))
    )
    assert gap.status is ValueStatus.MISSING and gap.value is None
    others = [
        o for o in by_field(result, FundamentalField.OPERATING_NWC) if o is not gap
    ]
    assert all(o.status is ValueStatus.AVAILABLE for o in others)


def test_future_period_is_rejected():
    future = date(2025, 12, 31)
    rows = {k: {**v, future: D(1)} for k, v in income().rows.items()}
    result = run(annual_income=StatementTable(rows))
    assert "future_period" in codes(result)
    assert future not in {o.period.end for o in by_field(result, FundamentalField.EBIT)}


def test_share_count_needs_a_date_and_agreeing_sources():
    undated = run(dated_shares=None)
    assert "shares_undated" in codes(undated)
    assert undated.shares_period is None
    assert not by_field(undated, FundamentalField.OUTSTANDING_SHARES)
    suggestion = [
        s for s in undated.suggestions if s.field is FundamentalField.OUTSTANDING_SHARES
    ]
    assert (
        suggestion[0].period is None and suggestion[0].basis == "provider_undated_count"
    )
    ok = run()
    shares = by_field(ok, FundamentalField.OUTSTANDING_SHARES)[0]
    assert shares.value == D(10_000_000)
    assert shares.period == CurrentSharesPeriod(
        SHARES_DATE, norm.SPLIT_BASIS_UNVERIFIED
    )
    assert ok.shares_period == shares.period
    conflict = run(info={**INFO, "sharesOutstanding": 12_000_000})  # 20% apart.
    assert "share_sources_conflict" in codes(conflict)
    assert (
        by_field(conflict, FundamentalField.OUTSTANDING_SHARES)[0].status
        is ValueStatus.INVALID
    )
    close = run(info={**INFO, "sharesOutstanding": 10_500_000})
    assert "share_sources_conflict" not in codes(close)
    # The latest dated entry wins; non-positive entries are ignored.
    series = (
        (date(2025, 1, 1), D(9_000_000)),
        (SHARES_DATE, D(10_000_000)),
        (date(2025, 3, 10), D(0)),
    )
    assert run(dated_shares=series).shares_period.as_of == SHARES_DATE


def test_lease_visibility_is_reported():
    assert "financial_debt_includes_leases" in run().notes
    rows = dict(balance().rows)
    del rows["Capital Lease Obligations"]
    assert "lease_split_not_visible" in run(annual_balance=StatementTable(rows)).notes


# --- report set -------------------------------------------------------------


def capital(result):
    return CapitalStructure(
        "synthetic-issuer",
        IDENTITY.source,
        result.shares_period.as_of,
        1,
        True,
        "synthetic-complete-capital-proof",
        RETRIEVED,
    )


def manual_value(field, original, value):
    unit = original.unit
    return FundamentalObservation(
        field,
        value,
        ValueStatus.AVAILABLE,
        unit,
        original.period,
        original.report,
        FieldProvenance(
            IDENTITY.source,
            EvidenceKind.MANUAL,
            "synthetic-owner-evidence",
            "owner supplied",
            "confirmed",
            None,
        ),
    )


def adjustments(result):
    out = []
    for original in result.manual_required:
        value = (
            D("0.2") if original.field is FundamentalField.OPERATING_TAX_RATE else D(0)
        )
        out.append(
            ManualAdjustment(
                original,
                manual_value(original.field, original, value),
                "synthetic confirmed value",
                RETRIEVED,
                RETRIEVED,
                "synthetic-confirmation",
            )
        )
    return tuple(out)


def blocking(report, at=VALUATION):
    return {i.code for i in report.issues_at(at) if i.blocking}


def test_report_set_is_blocked_until_manual_fields_and_owner_statements_exist():
    result = run()
    report = build_report_set(result, capital(result), "v1")
    assert report is not None and report.currency == "EUR"
    assert {
        "ebit_definition",
        "share_split_basis",
        "lease_treatment",
    } <= blocking(report)
    # Manual fields are missing observations, so they block as missing.
    assert {"operating_tax_rate", "excess_cash", "incremental_dilution"} <= blocking(
        report
    )
    confirmed = build_report_set(
        result, capital(result), "v1", OwnerConfirmations(True, True, True)
    )
    assert not {"ebit_definition", "share_split_basis", "lease_treatment"} & blocking(
        confirmed
    )


def test_report_set_with_confirmed_manual_values_has_no_blocking_issue():
    result = run()
    report = build_report_set(
        result, capital(result), "v1", OwnerConfirmations(True, True, True)
    )
    report = replace(report, adjustments=adjustments(result))
    assert blocking(report) == set()
    advisory = {i.status for i in report.issues_at(VALUATION) if not i.blocking}
    assert QualityStatus.UNKNOWN_PUBLICATION in advisory
    assert QualityStatus.MANUALLY_SUPPLEMENTED in advisory


def test_stale_share_count_and_provider_gaps_block_through_the_report_set():
    result = run(dated_shares=((date(2024, 9, 1), D(10_000_000)),))
    report = build_report_set(
        result, capital(result), "v1", OwnerConfirmations(True, True, True)
    )
    report = replace(report, adjustments=adjustments(result))
    assert "share_count_age" in blocking(report)
    gap = run(annual_cashflow=cashflow(**{"Capital Expenditure": [None] * 4}))
    report = build_report_set(
        gap, capital(gap), "v1", OwnerConfirmations(True, True, True)
    )
    report = replace(report, adjustments=adjustments(gap))
    assert "capex" in blocking(report)


def test_report_set_requires_a_dated_share_count_and_the_owner_class_proof():
    undated = run(dated_shares=None)
    proof = CapitalStructure(
        "synthetic-issuer", IDENTITY.source, SHARES_DATE, 1, True, "p", RETRIEVED
    )
    assert build_report_set(undated, proof, "v1") is None
    result = run()
    wrong_date = replace(capital(result), as_of=date(2025, 3, 2))
    with pytest.raises(ValueError, match="class proof"):
        build_report_set(result, wrong_date, "v1")
    unknown_classes = CapitalStructure(
        "synthetic-issuer", IDENTITY.source, SHARES_DATE, None, False, None, None
    )
    report = build_report_set(result, unknown_classes, "v1")
    assert "ordinary_class_count_unknown" in {
        i.code for i in report.issues_at(VALUATION)
    }


def test_reruns_are_deterministic_and_do_not_mix_retrievals():
    first, second = run(), run()
    assert first == second
    later = normalize_statements(
        IDENTITY,
        statements(retrieved_at=RETRIEVED + timedelta(hours=1)),
        EXPECTATION,
    )
    assert later.report.report_set_id != first.report.report_set_id
    assert {o.report for o in first.observations} == {first.report}


# --- trailing twelve months -------------------------------------------------


def quarter(start, end, kind=PeriodKind.QUARTER, complete=True):
    return FlowPeriod(start, end, kind, f"{kind.value} {end.isoformat()}", complete)


QUARTERS = [
    (quarter(date(2024, 1, 1), date(2024, 3, 31)), D(10)),
    (quarter(date(2024, 4, 1), date(2024, 6, 30)), D(20)),
    (quarter(date(2024, 7, 1), date(2024, 9, 30)), D(30)),
    (quarter(date(2024, 10, 1), date(2024, 12, 31)), D(40)),
]


def test_ttm_sums_four_contiguous_quarters_and_ignores_cumulative_periods():
    ytd = [
        (quarter(date(2024, 1, 1), date(2024, 6, 30), PeriodKind.YTD), D(30)),
        (quarter(date(2024, 1, 1), date(2024, 9, 30), PeriodKind.YTD), D(60)),
        (quarter(date(2024, 1, 1), date(2024, 12, 31), PeriodKind.FISCAL_YEAR), D(100)),
    ]
    result = trailing_twelve_months(QUARTERS + ytd)
    assert result.value == D(100) and not result.issues  # Not 100 + 30 + 60 + 100.
    assert result.period.kind is PeriodKind.TTM
    assert (result.period.start, result.period.end) == (
        date(2024, 1, 1),
        date(2024, 12, 31),
    )
    assert trailing_twelve_months(QUARTERS[::-1]) == result  # Order-independent.


def test_ttm_uses_the_newest_four_of_five_quarters():
    older = (quarter(date(2023, 10, 1), date(2023, 12, 31)), D(5))
    assert trailing_twelve_months([older] + QUARTERS).value == D(100)


def test_missing_incomplete_or_noncontiguous_quarters_give_no_ttm():
    for broken in (
        QUARTERS[:3],
        QUARTERS[:2] + QUARTERS[3:],
        [*QUARTERS[:3], (QUARTERS[3][0], None)],
        [*QUARTERS[:3], (replace(QUARTERS[3][0], complete=False), D(40))],
        [*QUARTERS[:3], (quarter(date(2024, 10, 1), date(2025, 2, 28)), D(40))],
    ):
        result = trailing_twelve_months(broken)
        assert result.value is None and result.period is None
        assert result.issues[0].code == "ttm_incomplete"
        assert result.issues[0].blocking


def test_cumulative_periods_alone_and_overlaps_never_form_a_ttm():
    ytd_only = [
        (quarter(date(2024, 1, 1), date(2024, 6, 30), PeriodKind.YTD), D(30)),
        (quarter(date(2024, 1, 1), date(2024, 12, 31), PeriodKind.YTD), D(100)),
    ]
    assert trailing_twelve_months(ytd_only).issues[0].code == "ttm_no_quarters"
    overlapping = QUARTERS + [(quarter(date(2024, 4, 1), date(2024, 9, 30)), D(50))]
    assert (
        trailing_twelve_months(overlapping).issues[0].code == "ttm_overlapping_periods"
    )
    assert trailing_twelve_months([]).issues[0].code == "ttm_no_quarters"


def test_ttm_rejects_balance_sheet_instants():
    with pytest.raises(TypeError):
        trailing_twelve_months([(InstantPeriod(date(2024, 12, 31)), D(1))])


# --- catalog and boundaries -----------------------------------------------


def test_field_catalog_covers_every_contract_field_exactly_once():
    fields = [e.field for e in FIELD_CATALOG]
    assert sorted(f.value for f in fields) == sorted(f.value for f in FundamentalField)
    assert MANUAL_FIELDS == {
        FundamentalField.OPERATING_TAX_RATE,
        FundamentalField.EXCESS_CASH,
        FundamentalField.NONOPERATING_ASSETS,
        FundamentalField.PREFERRED_CLAIMS,
        FundamentalField.MINORITY_CLAIMS,
        FundamentalField.INCREMENTAL_DILUTION,
    }
    assert {e.field for e in FIELD_CATALOG if e.origin is norm.FieldOrigin.CONTROL} == {
        FundamentalField.PROVIDER_FCF,
        FundamentalField.WEIGHTED_EPS_SHARES,
    }


def test_row_preferences_match_the_reviewed_coverage_lists():
    for name in (
        "EBIT_ROWS",
        "TAX_ROWS",
        "PRETAX_ROWS",
        "DA_ROWS",
        "CAPEX_ROWS",
        "CASH_ROWS",
        "CURRENT_DEBT_ROWS",
        "CURRENT_ASSET_ROWS",
        "CURRENT_LIABILITY_ROWS",
        "DEBT_ROWS",
        "MINORITY_ROWS",
        "PREFERRED_ROWS",
        "NET_INCOME_ROWS",
        "EPS_ROWS",
        "EPS_SHARE_ROWS",
        "BALANCE_SHARE_ROWS",
        "LEASE_ROWS",
        "OPERATING_CASH_ROWS",
        "FREE_CASH_ROWS",
    ):
        assert getattr(norm, name) == getattr(coverage, name), name
    assert norm.EBIT_CONTROL_ROWS == coverage.EBIT_ALTERNATIVE_ROWS


def test_normalization_imports_no_ui_provider_pandas_or_network_package():
    code = """
import importlib.abc
import socket
import sys
class Boundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {
            'streamlit', 'pandas', 'plotly', 'yfinance', 'requests', 'google', 'gspread'
        }:
            raise AssertionError(f'Unexpected dependency: {fullname}')
sys.meta_path.insert(0, Boundary())
def no_network(*args, **kwargs):
    raise AssertionError('Unexpected network connection')
socket.socket = no_network
socket.create_connection = no_network
import portfolio_dashboard.fundamentals_normalization
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_normalizer_validates_its_arguments():
    with pytest.raises(TypeError):
        normalize_statements("x", statements(), EXPECTATION)
    with pytest.raises(TypeError):
        normalize_statements(IDENTITY, "x", EXPECTATION)
    with pytest.raises(ValueError):
        ProviderExpectation("eur", 12, 31)
    with pytest.raises(ValueError):
        ProviderExpectation("EUR", 13, 31)
    with pytest.raises(TypeError):
        StatementTable({"row": {date(2024, 12, 31): 1.5}})
    with pytest.raises(ValueError):
        StatementTable({"row": {date(2024, 12, 31): D("NaN")}})
