from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from portfolio_dashboard.fundamentals import (
    FlowPeriod,
    PeriodKind,
    QualityIssue,
    QualityStatus,
    ReportIdentity,
)
from portfolio_dashboard.intrinsic_models import (
    CalculationStep,
    ForecastYear,
    InputReference,
    IntrinsicValueResult,
    Scenario,
    ScenarioAssumptions,
    TerminalPlan,
)

D = Decimal
AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
INPUTS = InputReference(
    "synthetic-share",
    "synthetic-class",
    ReportIdentity("synthetic-issuer", "synthetic-report", "v1", "synthetic-group"),
    "synthetic-input-v1",
)


def assumptions():
    years = tuple(
        ForecastYear(
            t,
            FlowPeriod(
                date(2025 + t, 1, 1),
                date(2025 + t, 12, 31),
                PeriodKind.FISCAL_YEAR,
                str(2025 + t),
                True,
            ),
            D(100),
            D("0.2"),
            D(20),
            D(30),
            D(10),
        )
        for t in range(1, 6)
    )
    return ScenarioAssumptions(
        INPUTS,
        "synthetic-assumptions-v1",
        Scenario.BASE,
        AT,
        "EUR",
        years,
        TerminalPlan(D(80), D(20), D(20), D(0), "synthetic terminal rationale"),
        D("0.08"),
        D(0),
        D("0.1"),
        None,
        "synthetic explicit forecast plan",
        AT,
        "synthetic-owner-confirmation",
    )


def result():
    return IntrinsicValueResult(
        INPUTS,
        "synthetic-assumptions-v1",
        Scenario.BASE,
        "synthetic-model-v1",
        AT,
        "EUR",
        (),
        ("synthetic-evidence",),
        (CalculationStep("synthetic-step", "synthetic formula", D("100.123"), "EUR"),),
        D("100.123"),
        None,
        D("0.8"),
    )


def test_complete_confirmed_assumptions_do_not_invent_a_safety_margin():
    plan = assumptions()
    assert plan.issues == ()
    assert plan.terminal_growth == 0
    assert plan.safety_margin is None
    assert result().target_price is None
    assert result().value_per_share == D("100.123")


def test_missing_and_unconfirmed_assumptions_are_independent_and_stay_missing():
    plan = replace(
        assumptions(),
        years=(),
        terminal=None,
        wacc=None,
        terminal_growth=None,
        sustainable_roic=None,
        confirmed_at=None,
        confirmation_reference=None,
    )
    assert {i.status for i in plan.issues} == {
        QualityStatus.INCOMPLETE,
        QualityStatus.UNCONFIRMED_ASSUMPTIONS,
    }
    assert plan.wacc is None and plan.terminal_growth is None
    plan = assumptions()
    partial = replace(plan.years[0], capex=None)
    assert not partial.complete
    assert replace(plan, years=(partial,) + plan.years[1:]).issues


@pytest.mark.parametrize(
    "changes",
    [
        {"wacc": D(0)},
        {"wacc": D("NaN")},
        {"wacc": 0.08},
        {"terminal_growth": D("0.08")},
        {"terminal_growth": D("-0.01")},
        {"sustainable_roic": D(0)},
        {"terminal_growth": D("0.05"), "sustainable_roic": D("0.04")},
        {"safety_margin": D(1)},
        {"confirmation_reference": None},
        {"valuation_at": datetime(2026, 1, 1)},
    ],
)
def test_invalid_rates_nonfinite_values_and_incomplete_confirmation_rejected(changes):
    with pytest.raises((TypeError, ValueError)):
        replace(assumptions(), **changes)


def test_forecast_periods_cannot_be_history_duplicates_quarters_or_gaps():
    plan = assumptions()
    with pytest.raises(ValueError, match="ordered"):
        replace(plan, years=(plan.years[1],))
    historical = replace(
        plan.years[0],
        period=FlowPeriod(
            date(2025, 1, 1), date(2025, 12, 31), PeriodKind.FISCAL_YEAR, "2025", True
        ),
    )
    with pytest.raises(ValueError, match="history"):
        replace(plan, years=(historical,))
    with pytest.raises(ValueError, match="annual"):
        replace(
            plan.years[0],
            period=FlowPeriod(
                date(2026, 1, 1), date(2026, 3, 31), PeriodKind.QUARTER, "Q1", True
            ),
        )
    with pytest.raises(ValueError, match="consecutive"):
        replace(plan, years=(plan.years[0], replace(plan.years[2], year=2)))
    with pytest.raises(ValueError, match="tax rate"):
        replace(plan.years[0], operating_tax_rate=D(1))
    with pytest.raises(ValueError, match="nonnegative"):
        replace(plan.terminal, capex=D(-1))


def test_result_keeps_versioned_inputs_steps_sources_and_all_quality_flags():
    valid = result()
    assert valid.inputs == INPUTS
    assert valid.steps[0].value == D("100.123")
    assert valid.scenario is Scenario.BASE
    issues = (
        QualityIssue(QualityStatus.MISSING, "synthetic-missing-field"),
        QualityIssue(QualityStatus.STALE, "synthetic-old-report"),
        QualityIssue(QualityStatus.MANUALLY_SUPPLEMENTED, "synthetic-amendment"),
    )
    unavailable = replace(valid, issues=issues, value_per_share=None)
    assert unavailable.issues == issues
    assert unavailable.source_references == valid.source_references
    assert unavailable.steps == valid.steps
    with pytest.raises(ValueError, match="blocking result"):
        replace(valid, issues=issues)
    with pytest.raises(ValueError, match="blocking reason"):
        replace(valid, value_per_share=None)


@pytest.mark.parametrize(
    "changes",
    [
        {"steps": ()},
        {"source_references": ()},
        {"value_per_share": D("Infinity")},
        {"value_per_share": D(0)},
        {"target_price": D(110)},
        {"terminal_value_share": None},
    ],
)
def test_available_results_require_positive_finite_value_and_audit_trail(changes):
    with pytest.raises(ValueError):
        replace(result(), **changes)
