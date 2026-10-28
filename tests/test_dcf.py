"""FCFF-DCF checks with invented numbers and independently derived expectations.

Expected values come from (a) the hand-derived synthetic example in
docs/INTRINSIC_VALUE.md, (b) closed-form perpetuity identities that never use the
5-year-plus-terminal split (EV = FCFF_1 / (WACC - g) when flows grow at g from
year 1), and (c) exact ``Fraction`` arithmetic. None of them repeats the
implementation's formula chain.
"""

import inspect
import re
import subprocess
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from test_fundamentals import (
    AT,
    CURRENT,
    MONEY,
    observation,
    period,
    report_set,
)

import portfolio_dashboard.dcf as dcf
from portfolio_dashboard.dcf import (
    LIMIT_TERMINAL_DOMINANT,
    LIMIT_VALUES_NOT_ORDERED,
    SensitivityCell,
    calculate_scenario,
    calculate_scenarios,
    calculate_sensitivity,
    centered_axis,
)
from portfolio_dashboard.fundamentals import (
    EvidenceKind,
    FundamentalField,
    FundamentalRegistry,
    FundamentalUnit,
    InstantPeriod,
    IssuerKind,
    ManualAdjustment,
    QualityStatus,
    UnitKind,
)
from portfolio_dashboard.intrinsic_models import (
    ForecastYear,
    InputReference,
    Scenario,
    ScenarioAssumptions,
    TerminalPlan,
)

D = Decimal
M = D(1_000_000)


# ---------------------------------------------------------------- fixtures


def base_report(
    *,
    ebit=(130, 140, 150),
    capex=44,
    nwc=(100, 116, 132, 148),
    bridge=(100, 20, 200, 10, 5),
    shares=(D("9.5"), D("0.5")),
):
    """Report set in millions EUR / millions of shares (all scale 1,000,000)."""
    observations = []
    for year, value in zip((2022, 2023, 2024), ebit):
        for field, amount in (
            (FundamentalField.EBIT, D(value)),
            (FundamentalField.OPERATING_TAX_RATE, D("0.2")),
            (FundamentalField.DEPRECIATION_AMORTIZATION, D(20)),
            (FundamentalField.CAPEX, D(capex)),
        ):
            observations.append(observation(field, amount, period(year)))
    for year, value in zip((2021, 2022, 2023, 2024), nwc):
        observations.append(
            observation(
                FundamentalField.OPERATING_NWC,
                D(value),
                InstantPeriod(date(year, 12, 31)),
            )
        )
    for field, value in zip(
        (
            FundamentalField.EXCESS_CASH,
            FundamentalField.NONOPERATING_ASSETS,
            FundamentalField.FINANCIAL_DEBT,
            FundamentalField.PREFERRED_CLAIMS,
            FundamentalField.MINORITY_CLAIMS,
        ),
        bridge,
    ):
        observations.append(observation(field, D(value), InstantPeriod(CURRENT.as_of)))
    observations.append(
        observation(FundamentalField.OUTSTANDING_SHARES, shares[0], CURRENT)
    )
    observations.append(
        observation(FundamentalField.INCREMENTAL_DILUTION, shares[1], CURRENT)
    )
    return replace(report_set(), observations=tuple(observations))


def forecast_years(
    ebit, tax=D("0.2"), da=20 * M, capex=48 * M, dnwc=20 * M, first=2026
):
    return tuple(
        ForecastYear(
            t,
            period(first - 1 + t),
            ebit[t - 1],
            tax,
            da[t - 1] if isinstance(da, tuple) else da,
            capex[t - 1] if isinstance(capex, tuple) else capex,
            dnwc[t - 1] if isinstance(dnwc, tuple) else dnwc,
        )
        for t in range(1, 6)
    )


def plan(
    report,
    label,
    wacc,
    growth,
    terminal_capex,
    terminal_nopat,
    *,
    roic=D("0.1"),
    years=None,
    terminal_da=20 * M,
    terminal_dnwc=D(0),
    margin=None,
    confirmed=True,
    version="synthetic-assumptions-v1",
    at=AT,
):
    return ScenarioAssumptions(
        InputReference.from_report_set(report),
        version,
        label,
        at,
        "EUR",
        years or forecast_years(tuple(D(x) * M for x in (160, 170, 180, 190, 200))),
        TerminalPlan(
            terminal_nopat,
            terminal_da,
            terminal_capex,
            terminal_dnwc,
            "synthetic sustainable year",
        ),
        wacc,
        growth,
        roic,
        margin,
        "synthetic explicit plan",
        AT if confirmed else None,
        "synthetic-owner-confirmation" if confirmed else None,
    )


# (label, WACC, g, NOPAT_6, capex_6) of docs/INTRINSIC_VALUE.md, millions EUR.
DOC_SCENARIOS = {
    Scenario.CAUTIOUS: (D("0.12"), D("0.01"), D("161.60"), D("36.16")),
    Scenario.BASE: (D("0.10"), D("0.02"), D("163.20"), D("52.64")),
    Scenario.OPTIMISTIC: (D("0.08"), D("0.03"), D("164.80"), D("69.44")),
}


def doc_plan(report, label, **kwargs):
    wacc, growth, nopat6, capex6 = DOC_SCENARIOS[label]
    return plan(report, label, wacc, growth, capex6 * M, nopat6 * M, **kwargs)


def doc_plans(report, **kwargs):
    return tuple(doc_plan(report, label, **kwargs) for label in Scenario)


def step(calculation, name):
    return next(s for s in calculation.result.steps if s.name == name)


def codes(calculation):
    return {i.code for i in calculation.result.issues if i.blocking}


# ----------------------------------------------- reference values (hand-derived)

# Per-share values: reference script in docs/INTRINSIC_VALUE.md (12 decimals).
DOC_PER_SHARE = {
    Scenario.CAUTIOUS: D("99.479969621694"),
    Scenario.BASE: D("127.650095311423"),
    Scenario.OPTIMISTIC: D("185.363776035345"),
}
# Table in docs/INTRINSIC_VALUE.md, millions EUR: (FCFF_6, TV_5, PV(TV), EV, TV share).
DOC_TABLE = {
    Scenario.CAUTIOUS: (
        D("145.44"),
        D("1322.181818"),
        D("750.241472"),
        D("1089.799696"),
        D("0.68842144"),
    ),
    Scenario.BASE: (
        D("130.56"),
        D("1632"),
        D("1013.343599"),
        D("1371.500953"),
        D("0.73885738"),
    ),
    Scenario.OPTIMISTIC: (
        D("115.36"),
        D("2307.2"),
        D("1570.241552"),
        D("1948.637760"),
        D("0.80581501"),
    ),
}


def test_reference_scenarios_match_independent_hand_derivation():
    report = base_report()
    result_set = calculate_scenarios(report, doc_plans(report))
    assert result_set.missing == ()
    for label in Scenario:
        calc = result_set.get(label)
        result = calc.result
        assert result.scenario is label
        assert not [i for i in result.issues if i.blocking]
        assert abs(result.value_per_share - DOC_PER_SHARE[label]) < D("1e-12")
        fcff6, tv, pv_tv, ev, share = DOC_TABLE[label]
        assert abs(step(calc, "sustainable_fcff_6").value / M - fcff6) < D("5e-7")
        assert abs(step(calc, "terminal_value_5").value / M - tv) < D("5e-7")
        assert abs(step(calc, "pv_terminal_value").value / M - pv_tv) < D("5e-7")
        assert abs(step(calc, "operating_value").value / M - ev) < D("5e-7")
        assert abs(result.terminal_value_share - share) < D("5e-9")
        assert step(calc, "diluted_shares").value == 10 * M
    # Documented PV(1..5) of the base case: 358,157353881690 million EUR.
    base = result_set.get(Scenario.BASE)
    assert abs(step(base, "pv_explicit_sum").value / M - D("358.157353881690")) < D(
        "1e-11"
    )
    # Documented per-year PVs of the base case (millions EUR, 6 decimals).
    for t, expected in enumerate(
        ("72.727273", "72.727273", "72.126221", "71.033399", "69.543188"), 1
    ):
        assert abs(step(base, f"pv_year_{t}").value / M - D(expected)) < D("5e-7")


def test_reference_bridge_and_target_price():
    report = base_report()
    calc = calculate_scenario(report, doc_plan(report, Scenario.BASE, margin=D("0.2")))
    # Bridge: +100 cash +20 non-operating -200 debt -10 preferred -5 minorities.
    equity = step(calc, "common_equity_value").value
    assert abs(equity / M - D("1276.500953114231")) < D("1e-9")
    assert abs(calc.result.target_price - D("102.120076249138")) < D("1e-12")
    assert abs(calc.result.value_per_share - D("127.650095311423")) < D("1e-12")
    # The margin only produces the separate target; the DCF value is unchanged.
    plain = calculate_scenario(report, doc_plan(report, Scenario.BASE))
    assert plain.result.value_per_share == calc.result.value_per_share
    assert plain.result.target_price is None
    zero = calculate_scenario(report, doc_plan(report, Scenario.BASE, margin=D(0)))
    assert zero.result.target_price == zero.result.value_per_share


def flat_case(*, bridge=(50, 10, 200, 20, 40), shares=(D(8), D(0)), wacc=D("0.1")):
    """Flat FCFF of 100 m forever: EBIT 125, T 20 % -> NOPAT 100; D&A = Capex."""
    report = base_report(bridge=bridge, shares=shares)
    years = forecast_years((125 * M,) * 5, da=20 * M, capex=20 * M, dnwc=D(0))
    assumptions = plan(
        report,
        Scenario.BASE,
        wacc,
        D(0),
        20 * M,
        100 * M,
        years=years,
        terminal_da=20 * M,
    )
    return report, assumptions


def test_flat_perpetuity_equals_fcff_over_wacc_regardless_of_the_split():
    # Closed form: 100 m / 10 % = 1000 m. Equity 1000+50+10-200-20-40 = 800; 8 m shares.
    report, assumptions = flat_case()
    calc = calculate_scenario(report, assumptions)
    assert abs(step(calc, "operating_value").value - 1000 * M) < D("1e-30")
    assert abs(calc.result.value_per_share - D(100)) < D("1e-30")
    exact_share = 1 / Fraction(11, 10) ** 5  # PV(TV)/EV with TV = EV at t=5
    tiny = Fraction(1, 10**35)
    assert abs(Fraction(calc.result.terminal_value_share) - exact_share) < tiny
    # Explicit part: 100 * annuity(5, 10 %) = 379.0786769408...
    annuity = 100 * (1 - Fraction(10, 11) ** 5) / Fraction(1, 10)
    explicit = Fraction(step(calc, "pv_explicit_sum").value) / 10**6
    assert abs(explicit - annuity) < tiny


def test_growing_perpetuity_closed_form_with_roic_reinvestment():
    # NOPAT grows 5 % from 100 m, ROIC 25 %, so reinvestment is 20 % of NOPAT and
    # FCFF_t = 80 * 1.05^(t-1) m: EV = FCFF_1 / (WACC - g) = 80 / 0.05 = 1600 m.
    report = base_report(bridge=(0, 0, 0, 0, 0), shares=(D(16), D(0)))
    nopat = [D("100") * D("1.05") ** k for k in range(5)]
    years = forecast_years(
        tuple(n / D("0.8") * M for n in nopat),
        da=D(0),
        capex=tuple(n * D("0.2") * M for n in nopat),
        dnwc=D(0),
    )
    nopat6 = nopat[-1] * D("1.05")
    assumptions = plan(
        report,
        Scenario.BASE,
        D("0.10"),
        D("0.05"),
        nopat6 * D("0.2") * M,
        nopat6 * M,
        roic=D("0.25"),
        years=years,
        terminal_da=D(0),
    )
    calc = calculate_scenario(report, assumptions)
    assert abs(step(calc, "operating_value").value - 1600 * M) < D("1e-30")
    assert abs(calc.result.value_per_share - D(100)) < D("1e-30")  # 1600 m / 16 m


# ------------------------------------------------- monotonicity and the bridge


def swap(report, field, value, scale=None):
    """Replace one bridge observation, optionally with another unit scale."""
    unit = MONEY if scale is None else FundamentalUnit(UnitKind.MONEY, scale, "EUR")
    observations = tuple(
        observation(field, value, InstantPeriod(CURRENT.as_of), unit)
        if o.field is field
        else o
        for o in report.observations
    )
    return replace(report, observations=observations)


def test_extra_debt_and_cash_move_equity_by_exactly_their_amount():
    report, assumptions = flat_case()
    base = calculate_scenario(report, assumptions).result
    # 80 m extra debt: -80 m equity, -10 per share on 8 m shares.
    more_debt = swap(report, FundamentalField.FINANCIAL_DEBT, D(280))
    debt = calculate_scenario(more_debt, assumptions).result
    assert abs((base.value_per_share - debt.value_per_share) - D(10)) < D("1e-30")
    # 40 m extra excess cash: +5 per share. Stated in thousands (scale 1,000).
    more_cash = swap(report, FundamentalField.EXCESS_CASH, D(90_000), scale=D(1000))
    cash = calculate_scenario(more_cash, assumptions).result
    assert abs((cash.value_per_share - base.value_per_share) - D(5)) < D("1e-30")
    # Operating value and terminal share do not depend on the bridge.
    assert (
        cash.terminal_value_share
        == base.terminal_value_share
        == debt.terminal_value_share
    )


def test_higher_wacc_lowers_value_for_equal_positive_cash_flows():
    values = []
    for wacc in (D("0.06"), D("0.08"), D("0.10"), D("0.12"), D("0.15")):
        report, assumptions = flat_case(wacc=wacc)
        values.append(calculate_scenario(report, assumptions).result.value_per_share)
    assert values == sorted(values, reverse=True)
    assert len(set(values)) == len(values)


def test_share_count_uses_diluted_shares_and_scales():
    report, assumptions = flat_case(shares=(D("3.5"), D("0.5")))  # 4 m shares
    result = calculate_scenario(report, assumptions).result
    assert abs(result.value_per_share - D(200)) < D("1e-30")  # 800 m / 4 m


def test_holdings_and_cost_basis_cannot_influence_the_value():
    for function in (calculate_scenario, calculate_scenarios, calculate_sensitivity):
        names = set(inspect.signature(function).parameters)
        assert not names & {"quantity", "shares_held", "cost_basis", "position"}
    report = base_report()
    first = report.identity
    second = replace(
        first,
        listing=replace(
            first.listing,
            instrument=replace(first.listing.instrument, trading_venue="other-venue"),
        ),
    )
    registry = FundamentalRegistry((first, second), (report,))
    plans = doc_plans(report)
    via_portfolio = calculate_scenarios(
        registry.resolve(first.listing.instrument), plans
    )
    via_watchlist = calculate_scenarios(
        registry.resolve(second.listing.instrument), plans
    )
    assert via_portfolio == via_watchlist


# ------------------------------------------------------ scenarios and ordering


def test_scenarios_are_independent_and_never_reordered():
    report = base_report()
    # Deliberately unexpected: "cautious" gets the optimistic rates and vice versa.
    swapped = (
        replace(doc_plan(report, Scenario.OPTIMISTIC), scenario=Scenario.CAUTIOUS),
        doc_plan(report, Scenario.BASE),
        replace(doc_plan(report, Scenario.CAUTIOUS), scenario=Scenario.OPTIMISTIC),
    )
    result_set = calculate_scenarios(report, swapped)
    assert [c.result.scenario for c in result_set.calculations] == [
        Scenario.CAUTIOUS,
        Scenario.BASE,
        Scenario.OPTIMISTIC,
    ]
    cautious = result_set.get(Scenario.CAUTIOUS).result.value_per_share
    optimistic = result_set.get(Scenario.OPTIMISTIC).result.value_per_share
    assert cautious > optimistic  # reported as computed, labels untouched
    assert LIMIT_VALUES_NOT_ORDERED in result_set.limitations
    assert result_set.value_range == (optimistic, cautious)
    # Input order is also preserved when the caller lists base first.
    reordered = calculate_scenarios(
        report, (doc_plan(report, Scenario.BASE), doc_plan(report, Scenario.CAUTIOUS))
    )
    assert [c.result.scenario for c in reordered.calculations] == [
        Scenario.BASE,
        Scenario.CAUTIOUS,
    ]
    assert reordered.missing == (Scenario.OPTIMISTIC,)
    assert reordered.value_range is None  # a span needs all three scenarios


def test_ordered_results_report_range_and_no_confidence_claim():
    report = base_report()
    result_set = calculate_scenarios(report, doc_plans(report))
    assert LIMIT_VALUES_NOT_ORDERED not in result_set.limitations
    low, high = result_set.value_range
    assert abs(low - DOC_PER_SHARE[Scenario.CAUTIOUS]) < D("1e-12")
    assert abs(high - DOC_PER_SHARE[Scenario.OPTIMISTIC]) < D("1e-12")
    texts = set(result_set.limitations)
    for calc in result_set.calculations:
        texts |= set(calc.limitations)
    assert "scenario_range_is_model_estimate_not_confidence_interval" in texts
    assert "estimate_not_confidence_interval" in texts
    assert not any("confidence_interval" in t and "not" not in t for t in texts)


def test_one_blocked_scenario_does_not_block_the_others():
    report = base_report()
    plans = (
        doc_plan(report, Scenario.CAUTIOUS, confirmed=False),
        doc_plan(report, Scenario.BASE),
        doc_plan(report, Scenario.OPTIMISTIC),
    )
    result_set = calculate_scenarios(report, plans)
    cautious = result_set.get(Scenario.CAUTIOUS).result
    assert cautious.value_per_share is None and cautious.target_price is None
    assert QualityStatus.UNCONFIRMED_ASSUMPTIONS in {i.status for i in cautious.issues}
    assert result_set.get(Scenario.BASE).result.value_per_share is not None
    assert result_set.value_range is None


def test_scenario_set_rejects_duplicates_empty_and_too_many():
    report = base_report()
    one = doc_plan(report, Scenario.BASE)
    with pytest.raises(ValueError):
        calculate_scenarios(report, (one, one))
    with pytest.raises(ValueError):
        calculate_scenarios(report, ())
    with pytest.raises(TypeError):
        calculate_scenarios(report, [one])


def test_terminal_value_dominance_is_flagged_from_75_percent():
    report = base_report()
    result_set = calculate_scenarios(report, doc_plans(report))
    assert LIMIT_TERMINAL_DOMINANT not in result_set.get(Scenario.BASE).limitations
    assert not result_set.get(Scenario.CAUTIOUS).terminal_value_dominant
    assert result_set.get(Scenario.OPTIMISTIC).terminal_value_dominant  # 80.58 %


def test_result_carries_versions_evidence_steps_and_model_limitations():
    report = base_report()
    assumptions = doc_plan(report, Scenario.BASE)
    result = calculate_scenario(report, assumptions).result
    assert result.inputs == assumptions.inputs
    assert result.assumptions_version == "synthetic-assumptions-v1"
    assert result.model_version == dcf.MODEL_VERSION
    assert result.valuation_at == AT and result.currency == "EUR"
    assert any(
        r.startswith("input:synthetic-input-v1") for r in result.source_references
    )
    names = [s.name for s in result.steps]
    for expected in (
        "historical_fcff_2024",
        "pv_year_5",
        "terminal_value_5",
        "common_equity_value",
        "value_per_share",
    ):
        assert expected in names
    # Historical FCFF 2024 from the documented table: 80 m.
    assert (
        step(calculate_scenario(report, assumptions), "historical_fcff_2024").value
        == 80 * M
    )
    # Unknown publication stays a visible, non-blocking note.
    assert QualityStatus.UNKNOWN_PUBLICATION in {i.status for i in result.issues}


# ------------------------------------------------------- blocked and edge cases


def blocked(report, assumptions):
    calc = calculate_scenario(report, assumptions)
    assert calc.result.value_per_share is None and calc.result.target_price is None
    return calc


def test_unconfirmed_assumptions_give_no_value():
    report = base_report()
    calc = blocked(report, doc_plan(report, Scenario.BASE, confirmed=False))
    assert QualityStatus.UNCONFIRMED_ASSUMPTIONS in {
        i.status for i in calc.result.issues
    }


def test_missing_data_and_new_report_version_give_no_value():
    report = base_report()
    assumptions = doc_plan(report, Scenario.BASE, margin=D("0.1"))
    without_debt = replace(
        report,
        observations=tuple(
            o
            for o in report.observations
            if o.field is not FundamentalField.FINANCIAL_DEBT
        ),
    )
    calc = blocked(without_debt, assumptions)
    assert "capital_bridge" in codes(calc)
    # Missing is never zero: no pro-share number appears anywhere.
    assert "value_per_share" not in {s.name for s in calc.result.steps}
    newer = replace(report, input_version="synthetic-input-v2")
    assert "assumptions_input_mismatch" in codes(blocked(newer, assumptions))


def test_stale_annual_report_and_share_count_block():
    report = base_report()
    later = datetime(2027, 1, 1, tzinfo=timezone.utc)  # 731 days after FY2024
    years = forecast_years(
        tuple(D(x) * M for x in (160, 170, 180, 190, 200)), first=2027
    )
    stale = plan(
        report,
        Scenario.BASE,
        D("0.1"),
        D("0.02"),
        D("52.64") * M,
        D("163.2") * M,
        years=years,
        at=later,
    )
    calc = blocked(report, stale)
    assert {"annual_report_age", "share_count_age"} <= codes(calc)
    assert QualityStatus.STALE in {i.status for i in calc.result.issues}


def test_unsupported_companies_get_status_instead_of_a_value():
    report = base_report()
    assumptions = doc_plan(report, Scenario.BASE)
    bank = replace(
        report, identity=replace(report.identity, issuer_kind=IssuerKind.BANK)
    )
    calc = blocked(
        bank, replace(assumptions, inputs=InputReference.from_report_set(bank))
    )
    assert QualityStatus.UNSUPPORTED_MODEL in {i.status for i in calc.result.issues}
    two_classes = replace(
        report,
        capital_structure=replace(report.capital_structure, ordinary_class_count=2),
    )
    calc = blocked(two_classes, assumptions)
    assert "multiple_classes" in codes(calc)
    unknown = replace(
        report,
        capital_structure=replace(
            report.capital_structure,
            ordinary_class_count=None,
            includes_unlisted=False,
            evidence_reference=None,
            verified_at=None,
        ),
    )
    assert "ordinary_class_count_unknown" in codes(blocked(unknown, assumptions))


def test_non_profitable_history_is_unsupported_not_valued():
    def assumptions_for(r):
        return doc_plan(r, Scenario.BASE)

    negative_ebit = base_report(ebit=(130, -10, 150))
    calc = blocked(negative_ebit, assumptions_for(negative_ebit))
    assert "historical_ebit_not_positive" in codes(calc)
    # Positive EBIT but capex 100 m: FCFF = 120 + 20 - 100 - 16 = 24 > 0 in 2024? use 140.
    negative_fcff = base_report(capex=140)  # FCFF 2022 = 104 + 20 - 140 - 16 < 0
    calc = blocked(negative_fcff, assumptions_for(negative_fcff))
    assert "historical_fcff_not_positive" in codes(calc)
    assert QualityStatus.UNSUPPORTED_MODEL in {i.status for i in calc.result.issues}


def test_inconsistent_stated_nwc_change_blocks():
    report = base_report()
    stated = observation(FundamentalField.CHANGE_OPERATING_NWC, D(99), period(2024))
    tampered = replace(report, observations=report.observations + (stated,))
    assert "nwc_change_inconsistent" in codes(
        blocked(tampered, doc_plan(tampered, Scenario.BASE))
    )
    consistent = observation(FundamentalField.CHANGE_OPERATING_NWC, D(16), period(2024))
    fine = replace(report, observations=report.observations + (consistent,))
    assert calculate_scenario(
        fine, doc_plan(fine, Scenario.BASE)
    ).result.value_per_share


def test_provider_fcf_is_a_control_and_never_discounted():
    report = base_report()
    control = observation(FundamentalField.PROVIDER_FCF, D(9999), period(2024))
    with_control = replace(report, observations=report.observations + (control,))
    plain = calculate_scenario(report, doc_plan(report, Scenario.BASE)).result
    other = calculate_scenario(
        with_control, doc_plan(with_control, Scenario.BASE)
    ).result
    assert plain.value_per_share == other.value_per_share


def test_non_positive_equity_is_explained_by_the_bridge_not_priced():
    # Operating value 1371.5 m, debt 2000 m: equity -> negative.
    report = base_report(bridge=(100, 20, 2000, 10, 5))
    calc = blocked(report, doc_plan(report, Scenario.BASE, margin=D("0.2")))
    assert codes(calc) == {"no_positive_common_equity_value"}
    equity = step(calc, "common_equity_value").value
    assert abs(equity / M - D("-523.499046885769")) < D("1e-9")
    assert step(calc, "bridge_financial_debt").value == 2000 * M
    assert "value_per_share" not in {s.name for s in calc.result.steps}
    assert calc.result.target_price is None


def test_zero_equity_is_not_positive_either():
    report, assumptions = flat_case(bridge=(0, 0, 1000, 0, 0))  # EV 1000 m - 1000 m
    calc = blocked(report, assumptions)
    assert "no_positive_common_equity_value" in codes(calc)


def test_share_count_must_be_positive_at_contract_and_kernel_level():
    with pytest.raises(ValueError):
        observation(FundamentalField.OUTSTANDING_SHARES, D(0), CURRENT)
    with pytest.raises(ValueError):
        observation(FundamentalField.OUTSTANDING_SHARES, D(-5), CURRENT)
    bridge = dcf._Bridge(*(D(0),) * 5)
    for shares in (D(0), D(-1)):
        outcome = dcf._discount(
            "EUR", (D(100),) * 5, D(100), D("0.1"), D(0), D("0.1"), bridge, shares
        )
        assert [i.code for i in outcome.issues] == ["shares_not_positive"]
        assert outcome.value_per_share is None and outcome.steps == ()


def test_wacc_not_above_growth_is_rejected_by_contract_and_by_kernel():
    report = base_report()
    good = doc_plan(report, Scenario.BASE)
    for wacc in (D("0.02"), D("0.01")):  # equal to and below g = 2 %
        with pytest.raises(ValueError):
            replace(good, wacc=wacc)
    bridge = dcf._Bridge(*(D(0),) * 5)
    for wacc, growth in ((D("0.02"), D("0.02")), (D("0.01"), D("0.02"))):
        outcome = dcf._discount(
            "EUR", (D(100),) * 5, D(100), wacc, growth, D("0.1"), bridge, D(1)
        )
        assert "growth_not_below_wacc" in {i.code for i in outcome.issues}
        assert outcome.value_per_share is None


def test_terminal_plan_must_match_growth_and_roic_reinvestment():
    report = base_report()
    zero_reinvestment = doc_plan(report, Scenario.BASE)
    zero_reinvestment = replace(
        zero_reinvestment,
        terminal=replace(
            zero_reinvestment.terminal, capex=20 * M
        ),  # growth, no reinvestment
    )
    assert "terminal_reinvestment_mismatch" in codes(blocked(report, zero_reinvestment))
    wrong_nopat = doc_plan(report, Scenario.BASE)
    wrong_nopat = replace(
        wrong_nopat, terminal=replace(wrong_nopat.terminal, nopat=170 * M)
    )
    assert "terminal_nopat_mismatch" in codes(blocked(report, wrong_nopat))
    # Rounding of the plan inputs within 0.01 % of NOPAT_6 is accepted.
    rounded = doc_plan(report, Scenario.BASE)
    rounded = replace(
        rounded, terminal=replace(rounded.terminal, capex=(D("52.6401") * M))
    )
    assert calculate_scenario(report, rounded).result.value_per_share is not None


def test_non_positive_sustainable_cash_flow_gives_no_value():
    report = base_report()
    years = forecast_years(
        tuple(D(x) * M for x in (160, 170, 180, 190, -100)),
        capex=20 * M,
        dnwc=D(0),
    )
    # NOPAT_5 = -80 m, NOPAT_6 = -81.6 m, reinvestment = -16.32 m: all consistent.
    calc = blocked(
        report,
        plan(
            report,
            Scenario.BASE,
            D("0.10"),
            D("0.02"),
            (20 * M) + D("-16.32") * M,
            D("-81.6") * M,
            years=years,
        ),
    )
    assert codes(calc) == {"nonpositive_sustainable_fcff"}


def test_negative_explicit_forecast_flow_is_discounted_and_flagged():
    report = base_report()
    years = forecast_years(
        tuple(
            D(x) * M for x in (40, 170, 180, 190, 200)
        ),  # year 1 FCFF = 32+20-48-20 = -16
    )
    calc = calculate_scenario(
        report,
        plan(
            report,
            Scenario.BASE,
            D("0.10"),
            D("0.02"),
            D("52.64") * M,
            D("163.2") * M,
            years=years,
        ),
    )
    assert calc.result.value_per_share is not None
    assert dcf.LIMIT_NEGATIVE_FORECAST_FCFF in calc.limitations
    assert step(calc, "fcff_year_1").value == D("-16") * M


def test_overflow_blocks_instead_of_returning_zero_or_infinity():
    report = base_report()
    huge = D("9E+999999")  # the largest decimal magnitude in the default context
    years = forecast_years((huge,) * 5, da=D(0), capex=D(0), dnwc=D(0))
    calc = blocked(
        report,
        plan(
            report,
            Scenario.BASE,
            D("0.10"),
            D("0.02"),
            huge * D("0.8") * D("1.02") * D("0.2"),  # consistent reinvestment g/ROIC
            huge * D("0.8") * D("1.02"),
            years=years,
            terminal_da=D(0),
        ),
    )
    assert calc.result.value_per_share is None
    assert codes(calc) == {"calculation_overflow"}


def test_non_finite_numbers_are_rejected_before_any_calculation():
    for bad in (D("NaN"), D("Infinity"), D("-Infinity")):
        with pytest.raises(ValueError):
            ForecastYear(1, period(2026), bad, D("0.2"), D(1), D(1), D(1))
        with pytest.raises(ValueError):
            observation(FundamentalField.EBIT, bad, period(2024))
    with pytest.raises(TypeError):
        observation(FundamentalField.EBIT, 100.0, period(2024))


def test_confirmed_manual_bridge_value_is_used_and_unconfirmed_blocks():
    report = base_report()
    original = next(
        o for o in report.observations if o.field is FundamentalField.FINANCIAL_DEBT
    )
    replacement = replace(
        original,
        value=D(300),
        provenance=replace(
            original.provenance,
            kind=EvidenceKind.MANUAL,
            retrieved_at=None,
            field_reference="synthetic-manual-evidence",
        ),
    )

    def with_adjustment(confirmed):
        adjustment = ManualAdjustment(
            original,
            replacement,
            "synthetic reason",
            AT,
            AT if confirmed else None,
            "synthetic-confirmation" if confirmed else None,
        )
        return replace(report, adjustments=(adjustment,))

    confirmed = with_adjustment(True)
    calc = calculate_scenario(confirmed, doc_plan(confirmed, Scenario.BASE))
    # 100 m more debt than the 200 m original: 10 per share lower than 127.65...
    assert abs(calc.result.value_per_share - (D("127.650095311423") - 10)) < D("1e-12")
    assert QualityStatus.MANUALLY_SUPPLEMENTED in {i.status for i in calc.result.issues}
    unconfirmed = with_adjustment(False)
    blocked(unconfirmed, doc_plan(unconfirmed, Scenario.BASE))


# ------------------------------------------------------------------ sensitivity


def test_sensitivity_cells_match_independent_reference_values():
    report = base_report()
    assumptions = doc_plan(report, Scenario.BASE)
    wacc_axis = (D("0.12"), D("0.10"), D("0.08"))
    growth_axis = (D("0.03"), D("0.02"), D("0.01"))
    grid = calculate_sensitivity(report, assumptions, wacc_axis, growth_axis)
    assert grid.wacc_values == wacc_axis and grid.growth_values == growth_axis
    # All three documented scenarios share this plan and ROIC; only WACC/g differ.
    assert abs(
        grid.cell(D("0.12"), D("0.01")).value_per_share
        - DOC_PER_SHARE[Scenario.CAUTIOUS]
    ) < D("1e-12")
    assert abs(
        grid.cell(D("0.10"), D("0.02")).value_per_share - DOC_PER_SHARE[Scenario.BASE]
    ) < D("1e-12")
    assert abs(
        grid.cell(D("0.08"), D("0.03")).value_per_share
        - DOC_PER_SHARE[Scenario.OPTIMISTIC]
    ) < D("1e-12")
    base_value = calculate_scenario(report, assumptions).result.value_per_share
    assert grid.cell(D("0.10"), D("0.02")).value_per_share == base_value
    # Rows follow the WACC axis order, columns the growth axis order (no sorting).
    assert [row[0].wacc for row in grid.rows] == list(wacc_axis)
    assert [c.growth for c in grid.rows[0]] == list(growth_axis)


def test_sensitivity_marks_invalid_combinations_not_computable():
    report = base_report()
    grid = calculate_sensitivity(
        report,
        doc_plan(report, Scenario.BASE),
        (D("0.10"), D("0.03"), D("0.02"), D(0)),
        (D("0.02"), D("0.03"), D("0.10"), D("-0.01")),
    )
    expect = {
        (D("0.10"), D("0.02")): None,
        (D("0.10"), D("0.03")): None,
        (D("0.10"), D("0.10")): {"growth_not_below_wacc", "growth_not_below_roic"},
        (D("0.10"), D("-0.01")): {"growth_negative"},
        (D("0.03"), D("0.03")): {"growth_not_below_wacc"},
        (D("0.02"), D("0.03")): {"growth_not_below_wacc"},
        (D(0), D("0.02")): {"wacc_not_positive"},
    }
    for (wacc, growth), reasons in expect.items():
        cell = grid.cell(wacc, growth)
        if reasons is None:
            assert cell.available and cell.value_per_share > 0
        else:
            assert not cell.available and cell.value_per_share is None
            assert reasons <= {i.code for i in cell.issues}


def test_sensitivity_never_shows_a_price_for_non_positive_equity_cells():
    report = base_report(bridge=(0, 0, 1200, 0, 0))
    grid = calculate_sensitivity(
        report,
        doc_plan(report, Scenario.BASE),
        (D("0.08"), D("0.10"), D("0.12")),
        (D("0.02"),),
    )
    states = [row[0].available for row in grid.rows]
    # EV is about 1,949 m / 1,372 m / below 1,100 m, against 1,200 m of debt.
    assert states == [True, True, False]
    assert grid.rows[2][0].value_per_share is None


def test_sensitivity_of_a_blocked_base_is_blocked_everywhere():
    report = base_report()
    grid = calculate_sensitivity(
        report,
        doc_plan(report, Scenario.BASE, confirmed=False),
        (D("0.10"),),
        (D("0.02"),),
    )
    cell = grid.rows[0][0]
    assert not cell.available
    assert QualityStatus.UNCONFIRMED_ASSUMPTIONS in {i.status for i in cell.issues}


def test_centered_axis_and_axis_validation():
    assert centered_axis(D("0.10"), D("0.01"), 2) == (
        D("0.08"),
        D("0.09"),
        D("0.10"),
        D("0.11"),
        D("0.12"),
    )
    assert centered_axis(D("0.02"), D("0.005"), 0) == (D("0.02"),)
    for bad in ((D(0), 1), (D("-0.01"), 1), (D("0.01"), -1)):
        with pytest.raises(ValueError):
            centered_axis(D("0.1"), *bad)
    report = base_report()
    assumptions = doc_plan(report, Scenario.BASE)
    with pytest.raises(ValueError):
        calculate_sensitivity(report, assumptions, (D("0.1"), D("0.1")), (D("0.02"),))
    with pytest.raises(ValueError):
        calculate_sensitivity(report, assumptions, (), (D("0.02"),))
    with pytest.raises(TypeError):
        calculate_sensitivity(report, assumptions, (0.1,), (D("0.02"),))


def test_cell_contract_forbids_values_with_blocking_reasons():
    with pytest.raises(ValueError):
        SensitivityCell(D("0.1"), D("0.02"), None, None, ())


# ------------------------------------------------------------- determinism etc.


def test_repeated_calculation_is_identical_and_inputs_are_not_mutated():
    report = base_report()
    plans = doc_plans(report, margin=D("0.15"))
    first = calculate_scenarios(report, plans)
    second = calculate_scenarios(report, plans)
    assert first == second
    assert calculate_sensitivity(report, plans[1], (D("0.1"),), (D("0.02"),)) == (
        calculate_sensitivity(report, plans[1], (D("0.1"),), (D("0.02"),))
    )


def test_module_has_no_clock_network_provider_or_ui_dependency():
    source = Path(dcf.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "now(",
        "today(",
        "time.time",
        "datetime.now",
        "streamlit",
        "yfinance",
        "requests",
        "socket",
    ):
        assert forbidden not in source
    assert not re.search(r"^\s*(import time|from time )", source, re.MULTILINE)
    code = """
import importlib.abc
import sys
class Boundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {
            'streamlit', 'pandas', 'plotly', 'yfinance', 'requests', 'google', 'gspread'
        }:
            raise AssertionError(f'Unexpected dependency: {fullname}')
sys.meta_path.insert(0, Boundary())
import portfolio_dashboard.dcf
"""
    subprocess.run([sys.executable, "-c", code], check=True)
