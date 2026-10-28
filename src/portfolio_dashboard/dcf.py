"""Pure, deterministic FCFF-DCF with three independent scenarios and sensitivity.

Implements ADR 0010 / docs/INTRINSIC_VALUE.md on the contracts of
``fundamentals`` and ``intrinsic_models``. Nothing here reads the clock, a
provider, the network, Streamlit, holdings or cost basis: the valuation date is
``ScenarioAssumptions.valuation_at`` and every number comes from the explicit
report set and confirmed assumptions. No financial default is supplied.

Conventions (see docs/DCF_CALCULATION.md): nominal flows in reporting-currency
major units, cash flow t at the end of year t (t = 1..5, no mid-year shift),
terminal value at the end of year 5 from the ROIC-based sustainable FCFF_6.
Arithmetic uses Decimal with 50 significant digits and no intermediate
rounding; overflow, underflow or invalid operations block the result instead
of producing zero or infinity.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    Underflow,
    localcontext,
)

from portfolio_dashboard.fundamentals import (
    BRIDGE_FIELDS,
    FlowPeriod,
    FundamentalField,
    FundamentalObservation,
    FundamentalReportSet,
    InstantPeriod,
    PeriodKind,
    QualityIssue,
    QualityStatus,
)
from portfolio_dashboard.intrinsic_models import (
    CalculationStep,
    InputReference,
    IntrinsicValueResult,
    Scenario,
    ScenarioAssumptions,
)
from portfolio_dashboard.models import ValueStatus, _decimal

MODEL_VERSION = "fcff-dcf-v1"
# Technical hint threshold from ADR 0010 ("ab 75 %"); it never changes a result.
TERMINAL_DOMINANCE_THRESHOLD = Decimal("0.75")
# Relative tolerance (to |NOPAT_6|) when reconciling the owner's year-6 component
# plan with the ROIC-based sustainable reinvestment. 0.01 % absorbs input rounding
# to a few significant digits without hiding a materially inconsistent plan.
PLAN_TOLERANCE = Decimal("0.0001")

# Limitation codes shown with every scenario result (the UI maps them to text).
LIMIT_NOT_CONFIDENCE_INTERVAL = "estimate_not_confidence_interval"
LIMIT_CONSTANT_RATES = "constant_wacc_and_sustainable_roic"
LIMIT_YEAR_END = "year_end_cash_flows_no_mid_year_shift"
LIMIT_TERMINAL_DOMINANT = "terminal_value_dominant"
LIMIT_NEGATIVE_FORECAST_FCFF = "negative_forecast_fcff"
LIMIT_RANGE_NOT_CONFIDENCE_INTERVAL = (
    "scenario_range_is_model_estimate_not_confidence_interval"
)
LIMIT_VALUES_NOT_ORDERED = "scenario_values_not_ordered"

_ZERO = Decimal(0)
_ONE = Decimal(1)
_YEARS = 5


def _context() -> Context:
    return Context(
        prec=50,
        rounding=ROUND_HALF_EVEN,
        traps=[InvalidOperation, DivisionByZero, Overflow, Underflow],
    )


def _invalid(code: str) -> QualityIssue:
    return QualityIssue(QualityStatus.INVALID, code)


def _blocking(issues) -> bool:
    return any(issue.blocking for issue in issues)


@dataclass(frozen=True, slots=True)
class _Bridge:
    excess_cash: Decimal
    nonoperating_assets: Decimal
    financial_debt: Decimal
    preferred_claims: Decimal
    minority_claims: Decimal


@dataclass(frozen=True, slots=True)
class _Outcome:
    issues: tuple[QualityIssue, ...]
    steps: tuple[CalculationStep, ...]
    value_per_share: Decimal | None
    terminal_value_share: Decimal | None


def _discount(
    currency: str,
    fcff: tuple[Decimal, ...],
    nopat_5: Decimal,
    wacc: Decimal,
    growth: Decimal,
    roic: Decimal,
    bridge: _Bridge,
    shares: Decimal,
) -> _Outcome:
    """Value kernel on normalized major units; validates every rate and count."""
    issues = []
    if wacc <= 0:
        issues.append(_invalid("wacc_not_positive"))
    if growth < 0:
        issues.append(_invalid("growth_negative"))
    if wacc > 0 and growth >= wacc:
        issues.append(_invalid("growth_not_below_wacc"))
    if roic <= 0:
        issues.append(_invalid("roic_not_positive"))
    elif growth >= roic:
        issues.append(_invalid("growth_not_below_roic"))
    if shares <= 0:
        issues.append(_invalid("shares_not_positive"))
    if issues:
        return _Outcome(tuple(issues), (), None, None)
    try:
        with localcontext(_context()):
            return _compute(currency, fcff, nopat_5, wacc, growth, roic, bridge, shares)
    except DecimalException:
        return _Outcome((_invalid("calculation_overflow"),), (), None, None)


def _compute(currency, fcff, nopat_5, wacc, growth, roic, bridge, shares) -> _Outcome:
    factor = _ONE + wacc
    steps = []
    present = []
    for t, flow in enumerate(fcff, 1):
        value = flow / factor**t
        present.append(value)
        steps.append(
            CalculationStep(
                f"fcff_year_{t}", "EBIT*(1-T)+D&A-Capex-dNWC", flow, currency
            )
        )
        steps.append(
            CalculationStep(f"pv_year_{t}", "FCFF_t/(1+WACC)^t", value, currency)
        )
    pv_explicit = sum(present, _ZERO)
    steps.append(
        CalculationStep("pv_explicit_sum", "sum(PV_1..PV_5)", pv_explicit, currency)
    )
    nopat_6 = nopat_5 * (_ONE + growth)
    reinvestment_6 = nopat_6 * growth / roic
    fcff_6 = nopat_6 - reinvestment_6
    steps.append(CalculationStep("nopat_6", "NOPAT_5*(1+g)", nopat_6, currency))
    steps.append(
        CalculationStep("reinvestment_6", "NOPAT_6*g/ROIC", reinvestment_6, currency)
    )
    steps.append(
        CalculationStep(
            "sustainable_fcff_6", "NOPAT_6-Reinvestment_6", fcff_6, currency
        )
    )
    if fcff_6 <= 0:
        return _Outcome(
            (_invalid("nonpositive_sustainable_fcff"),), tuple(steps), None, None
        )
    terminal = fcff_6 / (wacc - growth)
    pv_terminal = terminal / factor**_YEARS
    operating = pv_explicit + pv_terminal
    steps.append(
        CalculationStep("terminal_value_5", "FCFF_6/(WACC-g)", terminal, currency)
    )
    steps.append(
        CalculationStep("pv_terminal_value", "TV_5/(1+WACC)^5", pv_terminal, currency)
    )
    steps.append(
        CalculationStep(
            "operating_value", "sum(PV_1..PV_5)+PV(TV_5)", operating, currency
        )
    )
    if operating <= 0:
        return _Outcome(
            (_invalid("no_positive_operating_value"),), tuple(steps), None, None
        )
    terminal_share = pv_terminal / operating
    steps.append(
        CalculationStep(
            "terminal_value_share",
            "PV(TV_5)/operating_value",
            terminal_share,
            "fraction",
        )
    )
    for name, value, sign in (
        ("bridge_excess_cash", bridge.excess_cash, 1),
        ("bridge_nonoperating_assets", bridge.nonoperating_assets, 1),
        ("bridge_financial_debt", bridge.financial_debt, -1),
        ("bridge_preferred_claims", bridge.preferred_claims, -1),
        ("bridge_minority_claims", bridge.minority_claims, -1),
    ):
        steps.append(
            CalculationStep(
                name,
                "added to equity" if sign > 0 else "deducted from equity",
                value,
                currency,
            )
        )
    equity = (
        operating
        + bridge.excess_cash
        + bridge.nonoperating_assets
        - bridge.financial_debt
        - bridge.preferred_claims
        - bridge.minority_claims
    )
    steps.append(
        CalculationStep(
            "common_equity_value",
            "operating_value+cash+nonoperating-debt-preferred-minority",
            equity,
            currency,
        )
    )
    steps.append(
        CalculationStep(
            "diluted_shares", "outstanding+incremental dilution", shares, "shares"
        )
    )
    if equity <= 0:
        return _Outcome(
            (_invalid("no_positive_common_equity_value"),),
            tuple(steps),
            None,
            terminal_share,
        )
    per_share = equity / shares
    if per_share <= 0:
        return _Outcome(
            (_invalid("no_positive_common_equity_value"),),
            tuple(steps),
            None,
            terminal_share,
        )
    steps.append(
        CalculationStep(
            "value_per_share",
            "common_equity_value/diluted_shares",
            per_share,
            f"{currency} per share",
        )
    )
    return _Outcome((), tuple(steps), per_share, terminal_share)


@dataclass(frozen=True, slots=True)
class _Prepared:
    issues: tuple[QualityIssue, ...]
    steps: tuple[CalculationStep, ...]
    fcff: tuple[Decimal, ...] | None
    nopat_5: Decimal | None
    bridge: _Bridge | None
    shares: Decimal | None

    @property
    def ready(self) -> bool:
        return (
            not _blocking(self.issues)
            and self.fcff is not None
            and self.nopat_5 is not None
            and self.bridge is not None
            and self.shares is not None
        )


def _amount(observations: dict, report_set, field, period) -> Decimal | None:
    """Usable observation scaled to major units/pieces; None if not usable."""
    observation: FundamentalObservation | None = observations.get((field, period))
    if (
        observation is None
        or observation.status is not ValueStatus.AVAILABLE
        or observation.value is None
        or observation.unit is None
        or not observation.unit.known
        or observation.unit.currency not in (None, report_set.currency)
    ):
        return None
    return observation.value * observation.unit.scale


def _prepare(
    report_set: FundamentalReportSet, assumptions: ScenarioAssumptions
) -> _Prepared:
    issues = list(assumptions.issues_for(report_set))
    steps = []
    try:
        with localcontext(_context()):
            fcff, nopat_5, bridge, shares = _numbers(
                report_set, assumptions, issues, steps
            )
    except DecimalException:
        issues.append(_invalid("calculation_overflow"))
        fcff = nopat_5 = bridge = shares = None
    return _Prepared(
        tuple(dict.fromkeys(issues)), tuple(steps), fcff, nopat_5, bridge, shares
    )


def _numbers(report_set, assumptions, issues, steps):
    currency = report_set.currency
    observations = {(o.field, o.period): o for o in report_set.effective_observations}

    def amount(field, period):
        return _amount(observations, report_set, field, period)

    annual = sorted(
        {
            period
            for field, period in observations
            if field is FundamentalField.EBIT
            and isinstance(period, FlowPeriod)
            and period.kind is PeriodKind.FISCAL_YEAR
            and period.complete
        },
        key=lambda p: p.start,
    )
    for period in annual:
        ebit = amount(FundamentalField.EBIT, period)
        rate = amount(FundamentalField.OPERATING_TAX_RATE, period)
        da = amount(FundamentalField.DEPRECIATION_AMORTIZATION, period)
        capex = amount(FundamentalField.CAPEX, period)
        opening = amount(
            FundamentalField.OPERATING_NWC,
            InstantPeriod(period.start - timedelta(days=1)),
        )
        closing = amount(FundamentalField.OPERATING_NWC, InstantPeriod(period.end))
        if any(v is None for v in (ebit, rate, da, capex, opening, closing)):
            continue  # The structural gate already reports the missing component.
        change = closing - opening
        stated = amount(FundamentalField.CHANGE_OPERATING_NWC, period)
        if stated is not None and abs(stated - change) > PLAN_TOLERANCE * max(
            abs(stated), abs(change)
        ):
            issues.append(_invalid("nwc_change_inconsistent"))
        nopat = ebit * (_ONE - rate)
        flow = nopat + da - capex - change
        steps.append(
            CalculationStep(
                f"historical_fcff_{period.fiscal_label}",
                "EBIT*(1-T)+D&A-Capex-dNWC",
                flow,
                currency,
            )
        )
        if ebit <= 0:
            issues.append(
                QualityIssue(
                    QualityStatus.UNSUPPORTED_MODEL, "historical_ebit_not_positive"
                )
            )
        if flow <= 0:
            issues.append(
                QualityIssue(
                    QualityStatus.UNSUPPORTED_MODEL, "historical_fcff_not_positive"
                )
            )

    fcff = nopat_5 = None
    wacc, growth, roic = (
        assumptions.wacc,
        assumptions.terminal_growth,
        assumptions.sustainable_roic,
    )
    terminal = assumptions.terminal
    if (
        len(assumptions.years) == _YEARS
        and all(y.complete for y in assumptions.years)
        and terminal is not None
        and terminal.complete
        and None not in (wacc, growth, roic)
    ):
        flows = []
        for y in assumptions.years:
            nopat = y.ebit * (_ONE - y.operating_tax_rate)
            flows.append(
                nopat + y.depreciation_amortization - y.capex - y.change_operating_nwc
            )
        fcff = tuple(flows)
        nopat_5 = assumptions.years[-1].ebit * (
            _ONE - assumptions.years[-1].operating_tax_rate
        )
        nopat_6 = nopat_5 * (_ONE + growth)
        if roic > 0:
            reinvestment_6 = nopat_6 * growth / roic
            limit = PLAN_TOLERANCE * abs(nopat_6)
            if abs(terminal.nopat - nopat_6) > limit:
                issues.append(_invalid("terminal_nopat_mismatch"))
            net = (
                terminal.capex
                + terminal.change_operating_nwc
                - terminal.depreciation_amortization
            )
            if abs(net - reinvestment_6) > limit:
                issues.append(_invalid("terminal_reinvestment_mismatch"))

    at_bridge = InstantPeriod(report_set.bridge_date)
    bridge_values = {f: amount(f, at_bridge) for f in BRIDGE_FIELDS}
    shares_a = amount(FundamentalField.OUTSTANDING_SHARES, report_set.shares_period)
    shares_b = amount(FundamentalField.INCREMENTAL_DILUTION, report_set.shares_period)
    bridge = shares = None
    if None in bridge_values.values() or shares_a is None or shares_b is None:
        issues.append(
            QualityIssue(QualityStatus.INCOMPLETE, "calculation_input_unusable")
        )
    else:
        bridge = _Bridge(
            bridge_values[FundamentalField.EXCESS_CASH],
            bridge_values[FundamentalField.NONOPERATING_ASSETS],
            bridge_values[FundamentalField.FINANCIAL_DEBT],
            bridge_values[FundamentalField.PREFERRED_CLAIMS],
            bridge_values[FundamentalField.MINORITY_CLAIMS],
        )
        shares = shares_a + shares_b
    return fcff, nopat_5, bridge, shares


@dataclass(frozen=True, slots=True)
class ScenarioCalculation:
    """One scenario's model result plus model-bound limitation codes."""

    result: IntrinsicValueResult
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.result, IntrinsicValueResult):
            raise TypeError("result must be IntrinsicValueResult")
        if not isinstance(self.limitations, tuple) or not all(
            isinstance(v, str) and v for v in self.limitations
        ):
            raise TypeError("limitations must be a tuple of codes")

    @property
    def terminal_value_dominant(self) -> bool:
        return LIMIT_TERMINAL_DOMINANT in self.limitations


def calculate_scenario(
    report_set: FundamentalReportSet, assumptions: ScenarioAssumptions
) -> ScenarioCalculation:
    """Value one confirmed scenario; unavailable results carry no price at all."""
    if not isinstance(assumptions, ScenarioAssumptions):
        raise TypeError("assumptions must be ScenarioAssumptions")
    prepared = _prepare(report_set, assumptions)
    issues = list(prepared.issues)
    steps = list(prepared.steps)
    limitations = [LIMIT_NOT_CONFIDENCE_INTERVAL, LIMIT_CONSTANT_RATES, LIMIT_YEAR_END]
    value = target = share = None
    if prepared.ready:
        outcome = _discount(
            report_set.currency,
            prepared.fcff,
            prepared.nopat_5,
            assumptions.wacc,
            assumptions.terminal_growth,
            assumptions.sustainable_roic,
            prepared.bridge,
            prepared.shares,
        )
        issues.extend(outcome.issues)
        steps.extend(outcome.steps)
        value, share = outcome.value_per_share, outcome.terminal_value_share
        if any(f < 0 for f in prepared.fcff):
            limitations.append(LIMIT_NEGATIVE_FORECAST_FCFF)
        if share is not None and share >= TERMINAL_DOMINANCE_THRESHOLD:
            limitations.append(LIMIT_TERMINAL_DOMINANT)
        if value is not None and assumptions.safety_margin is not None:
            with localcontext(_context()):
                target = value * (_ONE - assumptions.safety_margin)
            steps.append(
                CalculationStep(
                    "target_price",
                    "value_per_share*(1-safety_margin)",
                    target,
                    f"{report_set.currency} per share",
                )
            )
    report = report_set.report
    references = (
        f"report:{report.report_set_id}@{report.revision}",
        f"input:{report_set.input_version}",
        f"assumptions:{assumptions.version}",
        f"provider:{report_set.identity.source.provider}",
    )
    result = IntrinsicValueResult(
        assumptions.inputs,
        assumptions.version,
        assumptions.scenario,
        MODEL_VERSION,
        assumptions.valuation_at,
        assumptions.currency,
        tuple(dict.fromkeys(issues)),
        references,
        tuple(steps),
        value,
        target,
        share,
    )
    return ScenarioCalculation(result, tuple(limitations))


@dataclass(frozen=True, slots=True)
class ScenarioValuationSet:
    """Independent scenario results in the caller's order, never re-sorted."""

    calculations: tuple[ScenarioCalculation, ...]
    missing: tuple[Scenario, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.calculations, tuple) or not all(
            isinstance(c, ScenarioCalculation) for c in self.calculations
        ):
            raise TypeError("calculations must be a tuple of ScenarioCalculation")
        labels = [c.result.scenario for c in self.calculations]
        if not labels or len(set(labels)) != len(labels):
            raise ValueError("scenarios must be unique and nonempty")

    def get(self, scenario: Scenario) -> ScenarioCalculation | None:
        return next(
            (c for c in self.calculations if c.result.scenario is scenario), None
        )

    @property
    def value_range(self) -> tuple[Decimal, Decimal] | None:
        """(lowest, highest) per-share model value, only if all three are available.

        A model-scenario span, explicitly not a statistical confidence interval.
        """
        values = [c.result.value_per_share for c in self.calculations]
        if self.missing or any(v is None for v in values):
            return None
        return min(values), max(values)


def calculate_scenarios(
    report_set: FundamentalReportSet, assumptions: tuple[ScenarioAssumptions, ...]
) -> ScenarioValuationSet:
    """Calculate each supplied scenario on its own plan (1..3, unique labels).

    A failing scenario never blocks another. Labels come from the assumptions
    and are never changed because the resulting values look unexpected; such an
    order is only reported as a limitation.
    """
    if not isinstance(assumptions, tuple) or not all(
        isinstance(a, ScenarioAssumptions) for a in assumptions
    ):
        raise TypeError("assumptions must be a tuple of ScenarioAssumptions")
    labels = [a.scenario for a in assumptions]
    if not 1 <= len(labels) <= len(Scenario) or len(set(labels)) != len(labels):
        raise ValueError("supply one to three assumptions with unique scenario labels")
    calculations = tuple(calculate_scenario(report_set, a) for a in assumptions)
    missing = tuple(s for s in Scenario if s not in labels)
    limitations = [LIMIT_RANGE_NOT_CONFIDENCE_INTERVAL]
    values = {c.result.scenario: c.result.value_per_share for c in calculations}
    if all(values.get(s) is not None for s in Scenario) and not (
        values[Scenario.CAUTIOUS]
        <= values[Scenario.BASE]
        <= values[Scenario.OPTIMISTIC]
    ):
        limitations.append(LIMIT_VALUES_NOT_ORDERED)
    return ScenarioValuationSet(calculations, missing, tuple(limitations))


def centered_axis(center: Decimal, step: Decimal, count: int) -> tuple[Decimal, ...]:
    """center - count*step .. center + count*step; step and count are explicit."""
    _decimal(center, "center")
    _decimal(step, "step")
    if step <= 0:
        raise ValueError("step must be positive")
    if type(count) is not int or count < 0:
        raise ValueError("count must be a nonnegative integer")
    with localcontext(_context()):
        return tuple(center + step * k for k in range(-count, count + 1))


@dataclass(frozen=True, slots=True)
class SensitivityCell:
    wacc: Decimal
    growth: Decimal
    value_per_share: Decimal | None
    terminal_value_share: Decimal | None
    issues: tuple[QualityIssue, ...]

    def __post_init__(self) -> None:
        for name in ("wacc", "growth"):
            _decimal(getattr(self, name), name)
        if self.value_per_share is not None:
            if self.value_per_share <= 0 or self.terminal_value_share is None:
                raise ValueError("available cell needs a positive value and share")
            if _blocking(self.issues):
                raise ValueError("blocked cell cannot expose a model value")
        elif not _blocking(self.issues):
            raise ValueError("unavailable cell must state a blocking reason")

    @property
    def available(self) -> bool:
        return self.value_per_share is not None


@dataclass(frozen=True, slots=True)
class SensitivityGrid:
    """WACC x growth grid in the caller's axis order; rows follow the WACC axis."""

    inputs: InputReference
    assumptions_version: str
    scenario: Scenario
    model_version: str
    wacc_values: tuple[Decimal, ...]
    growth_values: tuple[Decimal, ...]
    rows: tuple[tuple[SensitivityCell, ...], ...]
    issues: tuple[QualityIssue, ...]
    limitations: tuple[str, ...]

    def cell(self, wacc: Decimal, growth: Decimal) -> SensitivityCell:
        for row in self.rows:
            for candidate in row:
                if candidate.wacc == wacc and candidate.growth == growth:
                    return candidate
        raise KeyError("combination is not part of this grid")


def _axis(values: tuple[Decimal, ...], name: str) -> None:
    if not isinstance(values, tuple) or not values:
        raise ValueError(f"{name} must be a nonempty tuple")
    for value in values:
        _decimal(value, name)
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must not contain duplicates")


def calculate_sensitivity(
    report_set: FundamentalReportSet,
    assumptions: ScenarioAssumptions,
    wacc_values: tuple[Decimal, ...],
    growth_values: tuple[Decimal, ...],
) -> SensitivityGrid:
    """Re-value one scenario for explicit WACC and growth values.

    Each cell recomputes FCFF_6 including its reinvestment g/ROIC; the explicit
    year-6 component plan only reconciles the chosen base inputs. Invalid
    combinations (WACC <= 0, g < 0, g >= WACC, g >= ROIC) or a non-positive
    equity value are unavailable cells. If the base inputs are blocked, every
    cell is unavailable and carries the blocking reasons.
    """
    if not isinstance(assumptions, ScenarioAssumptions):
        raise TypeError("assumptions must be ScenarioAssumptions")
    _axis(wacc_values, "wacc_values")
    _axis(growth_values, "growth_values")
    prepared = _prepare(report_set, assumptions)
    base_blocking = tuple(i for i in prepared.issues if i.blocking)
    rows = []
    for wacc in wacc_values:
        row = []
        for growth in growth_values:
            if not prepared.ready:
                row.append(SensitivityCell(wacc, growth, None, None, base_blocking))
                continue
            outcome = _discount(
                report_set.currency,
                prepared.fcff,
                prepared.nopat_5,
                wacc,
                growth,
                assumptions.sustainable_roic,
                prepared.bridge,
                prepared.shares,
            )
            row.append(
                SensitivityCell(
                    wacc,
                    growth,
                    outcome.value_per_share,
                    outcome.terminal_value_share,
                    outcome.issues,
                )
            )
        rows.append(tuple(row))
    return SensitivityGrid(
        assumptions.inputs,
        assumptions.version,
        assumptions.scenario,
        MODEL_VERSION,
        wacc_values,
        growth_values,
        tuple(rows),
        prepared.issues,
        (LIMIT_NOT_CONFIDENCE_INTERVAL, LIMIT_CONSTANT_RATES, LIMIT_YEAR_END),
    )
